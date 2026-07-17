from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.config import Settings
from app.errors import TaskCancelled, TaskRepositoryReadError, TaskStaleLeaseError
from app.infrastructure.execution_state import (
    CancellationToken,
    ExecutionStateCoordinator,
    TaskExecutionContext,
)
from app.infrastructure.task_repository import LocalJsonTaskRepository
from app.infrastructure.task_runner import (
    LazyDefaultTaskJobRepository,
    LocalJsonTaskJobRepository,
    TaskJob,
    TaskJobRepository,
)
from app.models import CompareTask
from app.services.progress_bus import ProgressEvent


def build_coordinator(tmp_path: Path) -> tuple[ExecutionStateCoordinator, LocalJsonTaskJobRepository]:
    repository = LocalJsonTaskJobRepository(Settings(storage_dir=tmp_path / "storage"))
    return ExecutionStateCoordinator(repository), repository


def enqueue_job(coordinator: ExecutionStateCoordinator, task_id: str = "TCOORD") -> TaskJob:
    return coordinator.enqueue(
        TaskJob(
            job_id=f"compare:{task_id}:1",
            task_id=task_id,
            task_type="compare",
            execution_no=1,
        )
    )


class RecordingPublisher:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.events: list[ProgressEvent] = []

    def publish(self, event: ProgressEvent) -> None:
        self.calls.append("publish_terminal")
        self.events.append(event)


def build_terminal_coordinator(
    tmp_path: Path,
) -> tuple[
    ExecutionStateCoordinator,
    LocalJsonTaskJobRepository,
    LocalJsonTaskRepository,
    RecordingPublisher,
    list[str],
]:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    job_repository = LocalJsonTaskJobRepository(app_settings)
    task_repository = LocalJsonTaskRepository(app_settings)
    calls: list[str] = []
    publisher = RecordingPublisher(calls)
    coordinator = ExecutionStateCoordinator(
        job_repository,
        task_repository=task_repository,
        progress_publisher=publisher,
    )
    return coordinator, job_repository, task_repository, publisher, calls


def seed_running_terminal_job(
    coordinator: ExecutionStateCoordinator,
    task_repository: LocalJsonTaskRepository,
    task_id: str,
) -> TaskJob:
    job = enqueue_job(coordinator, task_id)
    task_repository.save_compare_task(CompareTask(task_id=task_id, active_job_id=job.job_id))
    claimed = coordinator.claim_next(worker_id="worker-1", lease_seconds=30)
    assert claimed is not None
    return claimed


@pytest.mark.parametrize("terminal", ["success", "failure", "cancel"])
def test_terminal_commit_persists_task_then_job_then_publishes_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: str,
) -> None:
    coordinator, job_repository, task_repository, publisher, calls = build_terminal_coordinator(tmp_path)
    job = seed_running_terminal_job(coordinator, task_repository, f"TORDER_{terminal.upper()}")
    persist_task = task_repository.update_compare_task
    persist_job = job_repository._persist

    def record_task(*args: object, **kwargs: object) -> CompareTask:
        calls.append("persist_task")
        return persist_task(*args, **kwargs)

    def record_job(candidate: TaskJob) -> TaskJob:
        calls.append("persist_job")
        return persist_job(candidate)

    monkeypatch.setattr(task_repository, "update_compare_task", record_task)
    monkeypatch.setattr(job_repository, "_persist", record_job)

    if terminal == "success":
        task, stored_job = coordinator.commit_success(
            job.job_id,
            worker_id="worker-1",
            result=CompareTask(task_id=job.task_id, metrics={"pipeline": "complete"}),
        )
        expected = ("COMPLETED", "NONE", "SUCCEEDED")
        assert task.report_revision == 1
    elif terminal == "failure":
        task, stored_job = coordinator.commit_failure(
            job.job_id,
            worker_id="worker-1",
            error="stage exploded",
            retry_delay_seconds=0,
        )
        expected = ("FAILED", "EXECUTION_FAILED", "FAILED")
    else:
        coordinator.request_cancel(job.task_id, task_type="compare")
        calls.clear()
        task, stored_job = coordinator.commit_cancelled(job.job_id, worker_id="worker-1")
        expected = ("FAILED", "CANCELLED", "CANCELLED")

    assert calls == ["persist_task", "persist_job", "publish_terminal"]
    assert (task.status, task.terminal_reason, stored_job.status) == expected
    assert task.terminal_job_id == job.job_id
    assert task.terminal_attempt == job.attempt
    assert publisher.events[-1].revision == task.revision


def test_success_terminal_replay_is_idempotent_without_revision_or_report_increment(tmp_path: Path) -> None:
    coordinator, _job_repository, task_repository, publisher, calls = build_terminal_coordinator(tmp_path)
    job = seed_running_terminal_job(coordinator, task_repository, "TREPLAY_SUCCESS")
    first_task, first_job = coordinator.commit_success(
        job.job_id,
        worker_id="worker-1",
        result=CompareTask(task_id=job.task_id),
    )
    calls.clear()

    second_task, second_job = coordinator.commit_success(
        job.job_id,
        worker_id="worker-1",
        result=CompareTask(task_id=job.task_id),
    )

    assert second_task == first_task
    assert second_job == first_job
    assert second_task.report_revision == 1
    assert second_task.revision == first_task.revision
    assert calls == []
    assert len(publisher.events) == 1


@pytest.mark.parametrize("terminal", ["success", "failure"])
def test_cancel_wins_authoritative_terminal_barrier_without_task_or_event_write(
    tmp_path: Path,
    terminal: str,
) -> None:
    coordinator, _job_repository, task_repository, publisher, calls = build_terminal_coordinator(tmp_path)
    job = seed_running_terminal_job(coordinator, task_repository, "TCANCEL_WINS_COMMIT")
    barrier = threading.Barrier(2)
    cancel_done = threading.Event()
    terminal_errors: list[BaseException] = []

    def cancel() -> None:
        barrier.wait()
        coordinator.request_cancel(job.task_id, task_type="compare")
        cancel_done.set()

    def succeed() -> None:
        barrier.wait()
        assert cancel_done.wait(1)
        try:
            if terminal == "success":
                coordinator.commit_success(
                    job.job_id,
                    worker_id="worker-1",
                    result=CompareTask(task_id=job.task_id),
                )
            else:
                coordinator.commit_failure(
                    job.job_id,
                    worker_id="worker-1",
                    error="stage failed",
                    retry_delay_seconds=0,
                )
        except BaseException as exc:
            terminal_errors.append(exc)

    threads = [threading.Thread(target=cancel), threading.Thread(target=succeed)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    stored_task = task_repository.load_compare_task(job.task_id)
    assert len(terminal_errors) == 1
    assert isinstance(terminal_errors[0], TaskCancelled)
    assert stored_task.status == "PROCESSING"
    assert publisher.events == []
    assert "publish_terminal" not in calls


def test_queued_cancellation_uses_authoritative_terminal_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, job_repository, task_repository, publisher, calls = build_terminal_coordinator(tmp_path)
    job = enqueue_job(coordinator, "TQUEUED_CANCEL_ORDER")
    task_repository.save_compare_task(CompareTask(task_id=job.task_id, active_job_id=job.job_id))
    persist_task = task_repository.update_compare_task
    persist_job = job_repository._persist

    def record_task(*args: object, **kwargs: object) -> CompareTask:
        calls.append("persist_task")
        return persist_task(*args, **kwargs)

    def record_job(candidate: TaskJob) -> TaskJob:
        calls.append("persist_job")
        return persist_job(candidate)

    monkeypatch.setattr(task_repository, "update_compare_task", record_task)
    monkeypatch.setattr(job_repository, "_persist", record_job)

    [cancelled] = coordinator.request_cancel(job.task_id, task_type="compare")

    task = task_repository.load_compare_task(job.task_id)
    assert calls == ["persist_task", "persist_job", "publish_terminal"]
    assert (task.status, task.terminal_reason, cancelled.status) == ("FAILED", "CANCELLED", "CANCELLED")
    assert publisher.events[-1].revision == task.revision


@pytest.mark.parametrize(
    "repository_type",
    [TaskJobRepository, LocalJsonTaskJobRepository, LazyDefaultTaskJobRepository],
)
def test_raw_job_storage_does_not_expose_state_transition_methods(
    tmp_path: Path,
    repository_type: type[object],
) -> None:
    repository = (
        repository_type(Settings(storage_dir=tmp_path / "storage"))
        if repository_type is not TaskJobRepository
        else repository_type
    )

    transition_methods = {
        "enqueue",
        "claim_next",
        "extend_lease",
        "request_cancel",
        "mark_succeeded",
        "mark_failed",
    }

    assert transition_methods.isdisjoint(dir(repository))


def test_raw_storage_cannot_cancel_on_disk_behind_coordinator_token(tmp_path: Path) -> None:
    coordinator, repository = build_coordinator(tmp_path)
    job = enqueue_job(coordinator, "TRAW_CANCEL")
    coordinator.claim_next(worker_id="worker-1", lease_seconds=30)
    token = CancellationToken(job_id=job.job_id, worker_id="worker-1", coordinator=coordinator)

    with pytest.raises(AttributeError):
        repository.request_cancel(job.task_id, task_type="compare")  # type: ignore[attr-defined]

    token.raise_if_cancelled()
    assert repository.load(job.job_id).status == "RUNNING"


def test_mutation_persists_before_replacing_memory_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    coordinator, repository = build_coordinator(tmp_path)
    job = enqueue_job(coordinator)
    persisted_while_memory_was: list[str] = []
    persist = repository._persist

    def observe_persist(candidate: TaskJob) -> TaskJob:
        persisted_while_memory_was.append(coordinator.load(job.job_id).status)
        return persist(candidate)

    monkeypatch.setattr(repository, "_persist", observe_persist)

    claimed = coordinator.claim_next(worker_id="worker-1", lease_seconds=30)

    assert claimed is not None
    assert claimed.status == "RUNNING"
    assert persisted_while_memory_was == ["QUEUED"]
    assert coordinator.load(job.job_id).status == "RUNNING"


def test_persistence_failure_leaves_memory_snapshot_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    coordinator, repository = build_coordinator(tmp_path)
    job = enqueue_job(coordinator)

    def fail_persist(_candidate: TaskJob) -> TaskJob:
        raise OSError("disk unavailable")

    monkeypatch.setattr(repository, "_persist", fail_persist)

    with pytest.raises(OSError, match="disk unavailable"):
        coordinator.claim_next(worker_id="worker-1", lease_seconds=30)

    assert coordinator.load(job.job_id).status == "QUEUED"
    assert repository.load(job.job_id).status == "QUEUED"


def test_claim_skips_round_when_task_primary_read_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    coordinator, _job_repository, task_repository, _publisher, _calls = build_terminal_coordinator(tmp_path)
    job = enqueue_job(coordinator, "TREAD_SKIP")
    task_repository.save_compare_task(CompareTask(task_id=job.task_id, active_job_id=job.job_id))

    def fail_read(_task_id: str) -> CompareTask:
        raise TaskRepositoryReadError("task primary unavailable")

    monkeypatch.setattr(task_repository, "load_compare_task", fail_read)
    caplog.set_level("WARNING", logger="app.infrastructure.execution_state")

    assert coordinator.claim_next(worker_id="worker-1", lease_seconds=30) is None
    assert coordinator.load(job.job_id).status == "QUEUED"
    assert job.task_id in caplog.text
    assert job.job_id in caplog.text


def test_claim_recovers_after_task_primary_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, _job_repository, task_repository, _publisher, _calls = build_terminal_coordinator(tmp_path)
    job = enqueue_job(coordinator, "TREAD_RECOVERY")
    task_repository.save_compare_task(CompareTask(task_id=job.task_id, active_job_id=job.job_id))
    load_task = task_repository.load_compare_task
    attempts = 0

    def fail_once(task_id: str) -> CompareTask:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TaskRepositoryReadError("transient task primary read failure")
        return load_task(task_id)

    monkeypatch.setattr(task_repository, "load_compare_task", fail_once)

    assert coordinator.claim_next(worker_id="worker-1", lease_seconds=30) is None
    claimed = coordinator.claim_next(worker_id="worker-1", lease_seconds=30)

    assert claimed is not None
    assert (claimed.job_id, claimed.status) == (job.job_id, "RUNNING")


def test_claim_does_not_swallow_programming_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, _job_repository, task_repository, _publisher, _calls = build_terminal_coordinator(tmp_path)
    job = enqueue_job(coordinator, "TPROGRAMMING_ERROR")
    task_repository.save_compare_task(CompareTask(task_id=job.task_id, active_job_id=job.job_id))

    def fail_programming(_task_id: str) -> CompareTask:
        raise RuntimeError("programming defect")

    monkeypatch.setattr(task_repository, "load_compare_task", fail_programming)

    with pytest.raises(RuntimeError, match="programming defect"):
        coordinator.claim_next(worker_id="worker-1", lease_seconds=30)

    assert coordinator.load(job.job_id).status == "QUEUED"


def test_repeated_cancellation_token_checks_only_read_memory_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, repository = build_coordinator(tmp_path)
    job = enqueue_job(coordinator)
    coordinator.claim_next(worker_id="worker-1", lease_seconds=30)
    token = CancellationToken(job_id=job.job_id, worker_id="worker-1", coordinator=coordinator)

    monkeypatch.setattr(repository, "load", lambda _job_id: pytest.fail("token read job JSON"))
    monkeypatch.setattr(repository, "list_jobs", lambda: pytest.fail("token scanned job JSON"))

    for _ in range(10):
        token.raise_if_cancelled()


def test_persisted_cancel_request_immediately_updates_token_memory(tmp_path: Path) -> None:
    coordinator, repository = build_coordinator(tmp_path)
    job = enqueue_job(coordinator)
    coordinator.claim_next(worker_id="worker-1", lease_seconds=30)
    token = CancellationToken(job_id=job.job_id, worker_id="worker-1", coordinator=coordinator)

    [cancelled] = coordinator.request_cancel(job.task_id, task_type="compare")

    assert cancelled.status == "CANCEL_REQUESTED"
    assert repository.load(job.job_id).status == "CANCEL_REQUESTED"
    with pytest.raises(TaskCancelled):
        token.raise_if_cancelled()


def test_mark_cancelled_accepts_ownerless_queued_job(tmp_path: Path) -> None:
    coordinator, _repository = build_coordinator(tmp_path)
    job = enqueue_job(coordinator)

    cancelled = coordinator.mark_cancelled(job.job_id, worker_id="")

    assert cancelled.status == "CANCELLED"
    assert cancelled.attempt == 0


def test_mark_cancelled_accepts_current_owner_cancel_request(tmp_path: Path) -> None:
    coordinator, _repository = build_coordinator(tmp_path)
    job = enqueue_job(coordinator)
    coordinator.claim_next(worker_id="worker-1", lease_seconds=30)
    coordinator.request_cancel(job.task_id, task_type="compare")

    cancelled = coordinator.mark_cancelled(job.job_id, worker_id="worker-1")

    assert cancelled.status == "CANCELLED"
    assert cancelled.lease_owner == ""


def test_mark_cancelled_is_idempotent_without_another_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, repository = build_coordinator(tmp_path)
    job = enqueue_job(coordinator)
    first = coordinator.mark_cancelled(job.job_id, worker_id="")
    writes = 0
    persist = repository._persist

    def count_persist(candidate: TaskJob) -> TaskJob:
        nonlocal writes
        writes += 1
        return persist(candidate)

    monkeypatch.setattr(repository, "_persist", count_persist)

    second = coordinator.mark_cancelled(job.job_id, worker_id="stale-worker")

    assert second == first
    assert writes == 0


@pytest.mark.parametrize("terminal_method", ["mark_succeeded", "mark_failed"])
def test_stale_owner_terminal_attempt_has_no_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_method: str,
) -> None:
    coordinator, repository = build_coordinator(tmp_path)
    job = enqueue_job(coordinator)
    coordinator.claim_next(worker_id="worker-1", lease_seconds=30)
    writes = 0
    persist = repository._persist

    def count_persist(candidate: TaskJob) -> TaskJob:
        nonlocal writes
        writes += 1
        return persist(candidate)

    monkeypatch.setattr(repository, "_persist", count_persist)

    with pytest.raises(TaskStaleLeaseError):
        if terminal_method == "mark_succeeded":
            coordinator.mark_succeeded(job.job_id, worker_id="worker-2")
        else:
            coordinator.mark_failed(
                job.job_id,
                worker_id="worker-2",
                error="failure",
                retry_delay_seconds=0,
            )

    assert writes == 0
    assert coordinator.load(job.job_id).status == "RUNNING"


@pytest.mark.parametrize("terminal_method", ["mark_succeeded", "mark_failed"])
def test_expired_lease_terminal_attempt_has_no_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_method: str,
) -> None:
    coordinator, repository = build_coordinator(tmp_path)
    job = enqueue_job(coordinator)
    coordinator.claim_next(worker_id="worker-1", lease_seconds=-1)
    writes = 0
    persist = repository._persist

    def count_persist(candidate: TaskJob) -> TaskJob:
        nonlocal writes
        writes += 1
        return persist(candidate)

    monkeypatch.setattr(repository, "_persist", count_persist)

    with pytest.raises(TaskStaleLeaseError):
        if terminal_method == "mark_succeeded":
            coordinator.mark_succeeded(job.job_id, worker_id="worker-1")
        else:
            coordinator.mark_failed(
                job.job_id,
                worker_id="worker-1",
                error="failure",
                retry_delay_seconds=0,
            )

    assert writes == 0
    assert coordinator.load(job.job_id).status == "RUNNING"


def test_multiple_workers_racing_claim_one_job_only_once(tmp_path: Path) -> None:
    coordinator, _repository = build_coordinator(tmp_path)
    job = enqueue_job(coordinator)
    barrier = threading.Barrier(3)
    claimed: list[TaskJob | None] = []
    claimed_lock = threading.Lock()

    def claim(worker_id: str) -> None:
        barrier.wait()
        result = coordinator.claim_next(worker_id=worker_id, lease_seconds=30)
        with claimed_lock:
            claimed.append(result)

    threads = [threading.Thread(target=claim, args=(f"worker-{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    winners = [result for result in claimed if result is not None]
    assert len(winners) == 1
    assert winners[0].job_id == job.job_id
    assert winners[0].attempt == 1
    assert coordinator.load(job.job_id).attempt == 1


def test_multiple_coordinators_in_one_process_share_claim_snapshot(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    first = ExecutionStateCoordinator(LocalJsonTaskJobRepository(app_settings))
    job = enqueue_job(first, "TSHARED_COORDINATOR")
    second = ExecutionStateCoordinator(LocalJsonTaskJobRepository(app_settings))
    barrier = threading.Barrier(3)
    claimed: list[TaskJob | None] = []
    claimed_lock = threading.Lock()

    def claim(coordinator: ExecutionStateCoordinator, worker_id: str) -> None:
        barrier.wait()
        result = coordinator.claim_next(worker_id=worker_id, lease_seconds=30)
        with claimed_lock:
            claimed.append(result)

    threads = [
        threading.Thread(target=claim, args=(first, "worker-1")),
        threading.Thread(target=claim, args=(second, "worker-2")),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    winners = [result for result in claimed if result is not None]
    assert len(winners) == 1
    assert winners[0].job_id == job.job_id
    assert first.load(job.job_id).lease_owner == second.load(job.job_id).lease_owner


@pytest.mark.parametrize("terminal_method", ["mark_succeeded", "mark_failed"])
def test_cancel_wins_terminal_barrier_without_success_or_failure_write(
    tmp_path: Path,
    terminal_method: str,
) -> None:
    coordinator, _repository = build_coordinator(tmp_path)
    job = enqueue_job(coordinator)
    coordinator.claim_next(worker_id="worker-1", lease_seconds=30)
    barrier = threading.Barrier(2)
    cancel_done = threading.Event()
    terminal_errors: list[BaseException] = []

    def cancel() -> None:
        barrier.wait()
        coordinator.request_cancel(job.task_id, task_type="compare")
        cancel_done.set()

    def commit_terminal() -> None:
        barrier.wait()
        assert cancel_done.wait(1)
        try:
            if terminal_method == "mark_succeeded":
                coordinator.mark_succeeded(job.job_id, worker_id="worker-1")
            else:
                coordinator.mark_failed(
                    job.job_id,
                    worker_id="worker-1",
                    error="stage failed",
                    retry_delay_seconds=0,
                )
        except BaseException as exc:
            terminal_errors.append(exc)

    threads = [threading.Thread(target=cancel), threading.Thread(target=commit_terminal)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(terminal_errors) == 1
    assert isinstance(terminal_errors[0], TaskCancelled)
    assert coordinator.mark_cancelled(job.job_id, worker_id="worker-1").status == "CANCELLED"


def test_execution_handoff_types_are_immutable(tmp_path: Path) -> None:
    coordinator, _repository = build_coordinator(tmp_path)
    job = enqueue_job(coordinator)
    token = CancellationToken(job_id=job.job_id, worker_id="worker-1", coordinator=coordinator)
    context = TaskExecutionContext(
        job_id=job.job_id,
        task_id=job.task_id,
        worker_id="worker-1",
        cancellation_token=token,
    )

    with pytest.raises(AttributeError):
        context.worker_id = "worker-2"
    with pytest.raises(AttributeError):
        token.worker_id = "worker-2"


def test_coordinator_rehydrates_when_repository_storage_namespace_changes(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "first-storage")
    repository = LocalJsonTaskJobRepository(app_settings)
    coordinator = ExecutionStateCoordinator(repository)
    enqueue_job(coordinator, "TFIRST_NAMESPACE")

    app_settings.storage_dir = tmp_path / "second-storage"
    app_settings.tasks_dir = app_settings.storage_dir / "tasks"
    second = enqueue_job(coordinator, "TSECOND_NAMESPACE")
    claimed = coordinator.claim_next(worker_id="worker-2", lease_seconds=30)

    assert claimed is not None
    assert claimed.job_id == second.job_id
    assert [job.job_id for job in coordinator.list_jobs()] == [second.job_id]
