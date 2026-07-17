from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.config import Settings
from app.errors import TaskCancelled, TaskRepositoryReadError, TaskStaleLeaseError, TaskTransitionConflict
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
        self.calls.append("publish_terminal" if event.status in {"COMPLETED", "FAILED"} else "publish_progress")
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


@pytest.mark.parametrize("binding", ["missing", "terminal", "mismatch"])
def test_claim_requires_processing_task_bound_to_job(tmp_path: Path, binding: str) -> None:
    coordinator, _job_repository, task_repository, _publisher, _calls = build_terminal_coordinator(tmp_path)
    job = enqueue_job(coordinator, f"TCLAIM_BINDING_{binding.upper()}")
    if binding == "terminal":
        task_repository.save_compare_task(
            CompareTask(task_id=job.task_id, status="COMPLETED", active_job_id=job.job_id)
        )
    elif binding == "mismatch":
        task_repository.save_compare_task(CompareTask(task_id=job.task_id, active_job_id=f"compare:{job.task_id}:2"))

    assert coordinator.claim_next(worker_id="worker-1", lease_seconds=30) is None
    assert coordinator.load(job.job_id).status == "QUEUED"


def test_claim_skips_orphan_old_job_and_claims_task_active_job(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    job_repository = LocalJsonTaskJobRepository(app_settings)
    task_repository = LocalJsonTaskRepository(app_settings)
    task_id = "TCLAIM_ACTIVE_ONLY"
    old_job = job_repository._persist(
        TaskJob(job_id=f"compare:{task_id}:1", task_id=task_id, task_type="compare", execution_no=1)
    )
    active_job = job_repository._persist(
        TaskJob(job_id=f"compare:{task_id}:2", task_id=task_id, task_type="compare", execution_no=2)
    )
    task_repository.save_compare_task(CompareTask(task_id=task_id, active_job_id=active_job.job_id))
    coordinator = ExecutionStateCoordinator(
        job_repository,
        task_repository=task_repository,
        progress_publisher=RecordingPublisher([]),
    )

    claimed = coordinator.claim_next(worker_id="worker-1", lease_seconds=30)

    assert claimed is not None
    assert claimed.job_id == active_job.job_id
    assert coordinator.load(old_job.job_id).status == "QUEUED"


def test_expired_active_lease_at_attempt_limit_commits_task_then_job_then_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, job_repository, task_repository, publisher, calls = build_terminal_coordinator(tmp_path)
    job = enqueue_job(coordinator, "TLEASE_LIMIT_AUTHORITY")
    task_repository.save_compare_task(CompareTask(task_id=job.task_id, active_job_id=job.job_id))
    claimed = coordinator.claim_next(worker_id="expired-worker", lease_seconds=-1)
    assert claimed is not None
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

    assert coordinator.claim_next(worker_id="replacement-worker", lease_seconds=30) is None

    task = task_repository.load_compare_task(job.task_id)
    stored_job = coordinator.load(job.job_id)
    assert calls == ["persist_task", "persist_job", "publish_terminal"]
    assert (task.status, task.terminal_reason) == ("FAILED", "EXECUTION_FAILED")
    assert (task.terminal_job_id, task.terminal_attempt) == (job.job_id, claimed.attempt)
    assert stored_job.status == "FAILED"
    assert stored_job.error_code == "LEASE_EXPIRED_MAX_ATTEMPTS"
    assert publisher.events[-1].revision == task.revision


@pytest.mark.parametrize("failure_point", ["task", "job"])
def test_expired_lease_terminal_partial_failure_preserves_repairable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    coordinator, job_repository, task_repository, publisher, _calls = build_terminal_coordinator(tmp_path)
    job = enqueue_job(coordinator, f"TLEASE_PARTIAL_{failure_point.upper()}")
    task_repository.save_compare_task(CompareTask(task_id=job.task_id, active_job_id=job.job_id))
    claimed = coordinator.claim_next(worker_id="expired-worker", lease_seconds=-1)
    assert claimed is not None
    if failure_point == "task":
        monkeypatch.setattr(
            task_repository,
            "update_compare_task",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("task disk unavailable")),
        )
    else:
        persist_job = job_repository._persist

        def fail_terminal_job(candidate: TaskJob) -> TaskJob:
            if candidate.status == "FAILED":
                raise OSError("job disk unavailable")
            return persist_job(candidate)

        monkeypatch.setattr(job_repository, "_persist", fail_terminal_job)

    with pytest.raises(OSError, match="disk unavailable"):
        coordinator.claim_next(worker_id="replacement-worker", lease_seconds=30)

    task = task_repository.load_compare_task(job.task_id)
    stored_job = coordinator.load(job.job_id)
    if failure_point == "task":
        assert (task.status, stored_job.status) == ("PROCESSING", "RUNNING")
    else:
        assert (task.status, stored_job.status) == ("FAILED", "RUNNING")
        assert coordinator.claim_next(worker_id="another-worker", lease_seconds=30) is None
    assert publisher.events == []


def test_cancel_expired_running_job_commits_cancel_terminal_immediately(tmp_path: Path) -> None:
    coordinator, _job_repository, task_repository, publisher, _calls = build_terminal_coordinator(tmp_path)
    job = enqueue_job(coordinator, "TCANCEL_EXPIRED_RUNNING")
    task_repository.save_compare_task(CompareTask(task_id=job.task_id, active_job_id=job.job_id))
    claimed = coordinator.claim_next(worker_id="crashed-worker", lease_seconds=-1)
    assert claimed is not None

    [cancelled] = coordinator.request_cancel(job.task_id, task_type="compare")

    task = task_repository.load_compare_task(job.task_id)
    assert cancelled.status == "CANCELLED"
    assert (task.status, task.terminal_reason) == ("FAILED", "CANCELLED")
    assert publisher.events[-1].revision == task.revision


def test_cancel_requested_owner_can_commit_after_lease_expires(tmp_path: Path) -> None:
    coordinator, _job_repository, task_repository, _publisher, _calls = build_terminal_coordinator(tmp_path)
    job = enqueue_job(coordinator, "TCANCEL_OWNER_EXPIRED")
    task_repository.save_compare_task(CompareTask(task_id=job.task_id, active_job_id=job.job_id))
    claimed = coordinator.claim_next(worker_id="worker-1", lease_seconds=0.01)
    assert claimed is not None
    [requested] = coordinator.request_cancel(job.task_id, task_type="compare")
    assert requested.status == "CANCEL_REQUESTED"
    time.sleep(0.02)

    task, cancelled = coordinator.commit_cancelled(job.job_id, worker_id="worker-1")

    assert cancelled.status == "CANCELLED"
    assert (task.status, task.terminal_reason) == ("FAILED", "CANCELLED")


def test_expired_cancel_request_is_finalized_by_claim_loop_after_worker_crash(tmp_path: Path) -> None:
    coordinator, _job_repository, task_repository, publisher, _calls = build_terminal_coordinator(tmp_path)
    job = enqueue_job(coordinator, "TCANCEL_CRASH_RECOVERY")
    task_repository.save_compare_task(CompareTask(task_id=job.task_id, active_job_id=job.job_id))
    claimed = coordinator.claim_next(worker_id="crashed-worker", lease_seconds=0.01)
    assert claimed is not None
    [requested] = coordinator.request_cancel(job.task_id, task_type="compare")
    assert requested.status == "CANCEL_REQUESTED"
    time.sleep(0.02)

    assert coordinator.claim_next(worker_id="replacement-worker", lease_seconds=30) is None

    task = task_repository.load_compare_task(job.task_id)
    assert coordinator.load(job.job_id).status == "CANCELLED"
    assert (task.status, task.terminal_reason) == ("FAILED", "CANCELLED")
    assert len(publisher.events) == 1


def test_repeated_cancel_after_expired_direct_cancel_is_idempotent(tmp_path: Path) -> None:
    coordinator, _job_repository, task_repository, publisher, _calls = build_terminal_coordinator(tmp_path)
    job = enqueue_job(coordinator, "TCANCEL_EXPIRED_REPLAY")
    task_repository.save_compare_task(CompareTask(task_id=job.task_id, active_job_id=job.job_id))
    assert coordinator.claim_next(worker_id="crashed-worker", lease_seconds=-1) is not None
    [first] = coordinator.request_cancel(job.task_id, task_type="compare")
    first_task = task_repository.load_compare_task(job.task_id)

    [second] = coordinator.request_cancel(job.task_id, task_type="compare")
    second_task = task_repository.load_compare_task(job.task_id)

    assert second == first
    assert second_task.revision == first_task.revision
    assert len(publisher.events) == 1


def test_progress_commit_persists_before_publish_with_actual_revision(tmp_path: Path) -> None:
    coordinator, _job_repository, task_repository, publisher, calls = build_terminal_coordinator(tmp_path)
    job = enqueue_job(coordinator, "TPROGRESS_AUTHORITY")
    task_repository.save_compare_task(CompareTask(task_id=job.task_id, active_job_id=job.job_id))
    claimed = coordinator.claim_next(worker_id="worker-1", lease_seconds=30)
    assert claimed is not None
    calls.clear()

    task = coordinator.commit_progress(
        job.job_id,
        worker_id="worker-1",
        stage="条款匹配中",
        progress_percent=42,
        detail={"matched": 3},
    )

    assert calls == ["publish_progress"]
    assert (task.status, task.stage, task.progress_percent) == ("PROCESSING", "条款匹配中", 42)
    assert publisher.events[-1].revision == task.revision
    assert publisher.events[-1].detail == {"matched": 3}
    assert coordinator.load(job.job_id).status == "RUNNING"


def test_progress_commit_rejects_cancel_without_late_processing_write(tmp_path: Path) -> None:
    coordinator, _job_repository, task_repository, publisher, _calls = build_terminal_coordinator(tmp_path)
    job = enqueue_job(coordinator, "TPROGRESS_CANCEL_RACE")
    task_repository.save_compare_task(
        CompareTask(task_id=job.task_id, active_job_id=job.job_id, stage="取消前", progress_percent=20)
    )
    assert coordinator.claim_next(worker_id="worker-1", lease_seconds=30) is not None
    coordinator.request_cancel(job.task_id, task_type="compare")

    with pytest.raises(TaskCancelled):
        coordinator.commit_progress(
            job.job_id,
            worker_id="worker-1",
            stage="取消后晚到进度",
            progress_percent=90,
        )

    task = task_repository.load_compare_task(job.task_id)
    assert (task.stage, task.progress_percent) == ("取消前", 20)
    assert publisher.events == []


def test_progress_commit_rejects_active_job_mismatch_without_write(tmp_path: Path) -> None:
    coordinator, _job_repository, task_repository, publisher, _calls = build_terminal_coordinator(tmp_path)
    job = enqueue_job(coordinator, "TPROGRESS_ACTIVE_MISMATCH")
    task_repository.save_compare_task(
        CompareTask(task_id=job.task_id, active_job_id=f"compare:{job.task_id}:2", stage="before")
    )
    coordinator_without_binding = ExecutionStateCoordinator(coordinator._repository)
    claimed = coordinator_without_binding.claim_next(worker_id="worker-1", lease_seconds=30)
    assert claimed is not None

    with pytest.raises(TaskTransitionConflict):
        coordinator.commit_progress(
            job.job_id,
            worker_id="worker-1",
            stage="late",
            progress_percent=50,
        )

    assert task_repository.load_compare_task(job.task_id).stage == "before"
    assert publisher.events == []


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
