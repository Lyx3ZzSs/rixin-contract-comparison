from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Mapping

import pytest

from app.application.compare_tasks import CompareTaskApplication
from app.config import Settings
from app.errors import TaskCancelled, TaskExecutionError, TaskStaleLeaseError, TaskTransitionConflict
from app.infrastructure.execution_state import CancellationToken, ExecutionStateCoordinator, TaskExecutionContext
from app.infrastructure.reconciliation import reconcile_terminal_jobs
from app.infrastructure.task_repository import LocalJsonTaskRepository
from app.infrastructure.task_runner import LocalJsonTaskJobRepository, QueuedTaskRunner, TaskJob
from app.models import CompareTask
from app.services.compare_service import CompareService
from app.services.pipeline import ComparePipeline, PipelineContext
from app.services.progress_bus import ProgressBus


def build_runner(tmp_path: Path, *, max_workers: int = 1, autostart: bool = True) -> QueuedTaskRunner:
    app_settings = Settings(
        storage_dir=tmp_path / "storage",
        task_runner_max_workers=max_workers,
        task_runner_max_attempts=1,
        task_runner_lease_seconds=30,
        task_runner_retry_delay_seconds=0.01,
        task_runner_poll_interval_seconds=0.01,
    )
    return QueuedTaskRunner(
        job_repository=LocalJsonTaskJobRepository(app_settings),
        app_settings=app_settings,
        autostart=autostart,
    )


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")


def test_local_json_task_job_repository_skips_legacy_extraction_job(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    repository = LocalJsonTaskJobRepository(app_settings)
    legacy_job = {
        "job_id": "extraction:TEXTRACT",
        "task_id": "TEXTRACT",
        "task_type": "extraction",
        "status": "QUEUED",
        "payload": {"task_id": "TEXTRACT"},
        "attempt": 0,
        "max_attempts": 1,
        "queued_at": "2026-05-20T08:00:00+00:00",
        "started_at": "",
        "finished_at": "",
        "updated_at": "2026-05-20T08:00:00+00:00",
        "next_run_at": "",
        "lease_owner": "",
        "lease_expires_at": "",
        "last_error": "",
    }
    compare_job = {
        **legacy_job,
        "job_id": "compare:TCOMPARE",
        "task_id": "TCOMPARE",
        "task_type": "compare",
        "payload": {"task_id": "TCOMPARE"},
        "queued_at": "2026-05-21T08:00:00+00:00",
        "updated_at": "2026-05-21T08:00:00+00:00",
    }
    legacy_path = app_settings.tasks_dir / "TEXTRACT" / "job.json"
    compare_path = app_settings.tasks_dir / "TCOMPARE" / "job.json"
    legacy_path.parent.mkdir(parents=True)
    compare_path.parent.mkdir(parents=True)
    legacy_json = json.dumps(legacy_job, ensure_ascii=False, indent=2)
    legacy_path.write_text(legacy_json, encoding="utf-8")
    compare_path.write_text(json.dumps(compare_job, ensure_ascii=False, indent=2), encoding="utf-8")
    coordinator = ExecutionStateCoordinator(repository)

    jobs = repository.list_jobs()
    claimed = coordinator.claim_next(worker_id="compare-worker", lease_seconds=30)

    assert [(job.job_id, job.task_type) for job in jobs] == [("compare:TCOMPARE", "compare")]
    assert claimed is not None
    assert (claimed.job_id, claimed.task_type) == ("compare:TCOMPARE", "compare")
    assert coordinator.claim_next(worker_id="compare-worker", lease_seconds=30) is None
    assert legacy_path.read_text(encoding="utf-8") == legacy_json


def test_queued_task_runner_persists_and_runs_job(tmp_path: Path) -> None:
    runner = build_runner(tmp_path)
    finished = threading.Event()
    seen_payload: list[Mapping[str, Any]] = []

    def handler(context: TaskExecutionContext, payload: Mapping[str, Any]) -> None:
        assert context.task_id == "T001"
        assert context.job_id == job.job_id
        seen_payload.append(payload)
        finished.set()

    runner.register_handler("compare", handler)
    job = runner.submit(task_type="compare", task_id="T001", payload={"task_id": "T001", "file": "a.pdf"})

    assert finished.wait(2)
    wait_until(lambda: runner.job_repository.load(job.job_id).status == "SUCCEEDED")
    stored = runner.job_repository.load(job.job_id)
    runner.stop()

    assert stored.status == "SUCCEEDED"
    assert stored.attempt == 1
    assert seen_payload == [{"task_id": "T001", "file": "a.pdf"}]


def test_new_submission_creates_first_execution_in_jobs_directory(tmp_path: Path) -> None:
    runner = build_runner(tmp_path, autostart=False)
    runner.register_handler("compare", lambda _context, _payload: None)

    job = runner.submit(task_type="compare", task_id="TFIRST", payload={"task_id": "TFIRST"})

    assert job.job_id == "compare:TFIRST:1"
    assert job.execution_no == 1
    assert (runner.settings.tasks_dir / "TFIRST" / "jobs" / "1.json").is_file()
    assert not (runner.settings.tasks_dir / "TFIRST" / "job.json").exists()


def test_submit_establishes_active_job_before_immediate_handler_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = build_runner(tmp_path)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    task_repository.save_compare_task(CompareTask(task_id="TIMMEDIATE_SUBMIT"))
    active_job_ids: list[str] = []

    def handler(context: TaskExecutionContext, _payload: Mapping[str, Any]) -> CompareTask:
        active_job_ids.append(task_repository.load_compare_task(context.task_id).active_job_id)
        return CompareTask(task_id=context.task_id)

    def run_immediately() -> None:
        claimed = runner.coordinator.claim_next(worker_id="immediate-worker", lease_seconds=30)
        assert claimed is not None
        runner._run_job(claimed, "immediate-worker")

    runner.register_handler("compare", handler)
    monkeypatch.setattr(runner, "start", run_immediately)

    job = application.submit_compare(
        original_path=tmp_path / "original.pdf",
        compare_path=tmp_path / "compare.pdf",
        task_id="TIMMEDIATE_SUBMIT",
        original_filename="original.pdf",
        compare_filename="compare.pdf",
    )

    assert active_job_ids == [job.job_id]
    assert runner.coordinator.load(job.job_id).status == "SUCCEEDED"
    assert task_repository.load_compare_task(job.task_id).status == "COMPLETED"


def test_retry_establishes_active_job_and_processing_state_before_immediate_handler_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = build_runner(tmp_path)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    original_path = tmp_path / "original.pdf"
    compare_path = tmp_path / "compare.pdf"
    original_path.touch()
    compare_path.touch()
    task_id = "TIMMEDIATE_RETRY"
    first_job_id = f"compare:{task_id}:1"
    task_repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            active_job_id=first_job_id,
            original_pdf_path=str(original_path),
            compare_pdf_path=str(compare_path),
        )
    )
    first = runner.coordinator.enqueue(
        TaskJob(
            job_id=first_job_id,
            task_id=task_id,
            task_type="compare",
            execution_no=1,
            max_attempts=1,
        )
    )
    claimed = runner.coordinator.claim_next(worker_id="seed-worker", lease_seconds=30)
    assert claimed is not None
    runner.coordinator.commit_failure(
        first.job_id,
        worker_id="seed-worker",
        error="first failure",
        retry_delay_seconds=0,
    )
    observed: list[tuple[str, str, str]] = []

    def handler(context: TaskExecutionContext, _payload: Mapping[str, Any]) -> CompareTask:
        task = task_repository.load_compare_task(context.task_id)
        observed.append((task.active_job_id, task.status, task.stage))
        return CompareTask(task_id=context.task_id)

    def run_immediately() -> None:
        retried = runner.coordinator.claim_next(worker_id="immediate-worker", lease_seconds=30)
        assert retried is not None
        runner._run_job(retried, "immediate-worker")

    runner.register_handler("compare", handler)
    monkeypatch.setattr(runner, "start", run_immediately)

    retried = application.retry_compare(task_id)

    assert observed == [(retried.job_id, "PROCESSING", "排队中")]
    assert runner.coordinator.load(first.job_id).status == "FAILED"
    assert runner.coordinator.load(retried.job_id).status == "SUCCEEDED"
    assert task_repository.load_compare_task(task_id).status == "COMPLETED"


def test_submit_job_persistence_failure_rolls_back_active_task_association(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    task_repository.save_compare_task(CompareTask(task_id="TSUBMIT_ROLLBACK", stage="ready"))
    before = task_repository.load_compare_task("TSUBMIT_ROLLBACK")
    job_repository = runner.coordinator._repository

    def fail_persist(_job: TaskJob) -> TaskJob:
        raise OSError("job disk unavailable")

    monkeypatch.setattr(job_repository, "_persist", fail_persist)

    with pytest.raises(OSError, match="job disk unavailable"):
        application.submit_compare(
            original_path=tmp_path / "original.pdf",
            compare_path=tmp_path / "compare.pdf",
            task_id="TSUBMIT_ROLLBACK",
            original_filename="original.pdf",
            compare_filename="compare.pdf",
        )

    stored = task_repository.load_compare_task("TSUBMIT_ROLLBACK")
    assert (stored.active_job_id, stored.status, stored.stage) == ("", "PROCESSING", "ready")
    assert stored.model_dump(exclude={"revision", "updated_at"}) == before.model_dump(
        exclude={"revision", "updated_at"}
    )
    assert runner.coordinator.list_jobs() == []


def test_retry_job_persistence_failure_rolls_back_terminal_task_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    original_path = tmp_path / "original.pdf"
    compare_path = tmp_path / "compare.pdf"
    original_path.touch()
    compare_path.touch()
    task_id = "TRETRY_ROLLBACK"
    first_job_id = f"compare:{task_id}:1"
    task_repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            active_job_id=first_job_id,
            original_pdf_path=str(original_path),
            compare_pdf_path=str(compare_path),
        )
    )
    first = runner.coordinator.enqueue(
        TaskJob(
            job_id=first_job_id,
            task_id=task_id,
            task_type="compare",
            execution_no=1,
            max_attempts=1,
        )
    )
    claimed = runner.coordinator.claim_next(worker_id="seed-worker", lease_seconds=30)
    assert claimed is not None
    failed_task, _failed_job = runner.coordinator.commit_failure(
        first.job_id,
        worker_id="seed-worker",
        error="first failure",
        retry_delay_seconds=0,
    )
    assert failed_task is not None
    job_repository = runner.coordinator._repository
    persist_job = job_repository._persist

    def fail_retry(candidate: TaskJob) -> TaskJob:
        if candidate.execution_no == 2:
            raise OSError("retry job disk unavailable")
        return persist_job(candidate)

    monkeypatch.setattr(job_repository, "_persist", fail_retry)

    with pytest.raises(OSError, match="retry job disk unavailable"):
        application.retry_compare(task_id)

    stored = task_repository.load_compare_task(task_id)
    assert stored.active_job_id == failed_task.active_job_id
    assert (stored.status, stored.terminal_reason, stored.stage) == (
        failed_task.status,
        failed_task.terminal_reason,
        failed_task.stage,
    )
    assert stored.model_dump(exclude={"revision", "updated_at"}) == failed_task.model_dump(
        exclude={"revision", "updated_at"}
    )
    assert [job.job_id for job in runner.coordinator.list_jobs()] == [first.job_id]


def test_submit_rejects_legacy_raw_job_id_collision_with_different_identity(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    legacy_path = app_settings.tasks_dir / "tenant_1" / "job.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        TaskJob(
            job_id="compare:tenant:1",
            task_id="tenant:1",
            task_type="compare",
            execution_no=1,
            payload={"task_id": "tenant:1"},
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    original_legacy_job = legacy_path.read_text(encoding="utf-8")
    runner = build_runner(tmp_path, autostart=False)
    runner.register_handler("compare", lambda _context, _payload: None)

    with pytest.raises(TaskTransitionConflict):
        runner.submit(task_type="compare", task_id="tenant", payload={"task_id": "tenant"})

    assert legacy_path.read_text(encoding="utf-8") == original_legacy_job
    assert not (runner.settings.tasks_dir / "tenant" / "jobs" / "1.json").exists()


def test_queued_task_runner_retries_failed_jobs(tmp_path: Path) -> None:
    runner = build_runner(tmp_path)
    attempts = 0

    def handler(_context: TaskExecutionContext, _payload: Mapping[str, Any]) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")

    runner.register_handler("compare", handler)
    job = runner.submit(
        task_type="compare",
        task_id="TRETRY",
        payload={"task_id": "TRETRY"},
        max_attempts=2,
    )

    wait_until(lambda: runner.job_repository.load(job.job_id).status == "SUCCEEDED")
    stored = runner.job_repository.load(job.job_id)
    runner.stop()

    assert attempts == 2
    assert stored.job_id == job.job_id
    assert stored.execution_no == 1
    assert stored.attempt == 2
    assert stored.last_error == ""


def test_queued_task_runner_cancels_queued_job(tmp_path: Path) -> None:
    runner = build_runner(tmp_path, autostart=False)
    executed = False

    def handler(_context: TaskExecutionContext, _payload: Mapping[str, Any]) -> None:
        nonlocal executed
        executed = True

    runner.register_handler("compare", handler)
    job = runner.submit(task_type="compare", task_id="TCANCEL", payload={"task_id": "TCANCEL"})
    cancelled = runner.cancel("TCANCEL", task_type="compare")
    runner.start()
    time.sleep(0.05)
    stored = runner.job_repository.load(job.job_id)
    runner.stop()

    assert [item.status for item in cancelled] == ["CANCELLED"]
    assert [item.attempt for item in cancelled] == [0]
    assert stored.status == "CANCELLED"
    assert stored.attempt == 0
    assert executed is False


def test_queued_task_runner_limits_concurrency(tmp_path: Path) -> None:
    runner = build_runner(tmp_path, max_workers=2)
    active = 0
    max_active = 0
    lock = threading.Lock()
    finished_count = 0

    def handler(_context: TaskExecutionContext, _payload: Mapping[str, Any]) -> None:
        nonlocal active, max_active, finished_count
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
            finished_count += 1

    runner.register_handler("compare", handler)
    for index in range(5):
        runner.submit(task_type="compare", task_id=f"T{index}", payload={"task_id": f"T{index}"})

    wait_until(lambda: finished_count == 5)
    runner.stop()

    assert max_active == 2
    assert runner.stats().succeeded == 5


def test_user_retry_creates_new_execution_and_preserves_failed_job(tmp_path: Path) -> None:
    runner = build_runner(tmp_path)

    def failing_handler(_context: TaskExecutionContext, _payload: Mapping[str, Any]) -> None:
        raise RuntimeError("first failure")

    runner.register_handler("compare", failing_handler)
    job = runner.submit(task_type="compare", task_id="TRETRY_API", payload={"task_id": "TRETRY_API"})
    wait_until(lambda: runner.job_repository.load(job.job_id).status == "FAILED")

    seen_payload: list[Mapping[str, Any]] = []

    def succeeding_handler(_context: TaskExecutionContext, payload: Mapping[str, Any]) -> None:
        seen_payload.append(payload)

    runner.register_handler("compare", succeeding_handler)
    retried = runner.retry("TRETRY_API", task_type="compare")
    wait_until(lambda: runner.job_repository.load(retried.job_id).status == "SUCCEEDED")
    stored = runner.job_repository.load(retried.job_id)
    original = runner.job_repository.load(job.job_id)
    runner.stop()

    assert retried.status == "QUEUED"
    assert retried.job_id == "compare:TRETRY_API:2"
    assert retried.execution_no == 2
    assert stored.status == "SUCCEEDED"
    assert stored.attempt == 1
    assert original.status == "FAILED"
    assert original.attempt == 1
    assert (runner.settings.tasks_dir / "TRETRY_API" / "jobs" / "1.json").is_file()
    assert (runner.settings.tasks_dir / "TRETRY_API" / "jobs" / "2.json").is_file()
    assert seen_payload == [{"task_id": "TRETRY_API"}]


def test_repository_rejects_second_active_execution(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    repository = LocalJsonTaskJobRepository(app_settings)
    coordinator = ExecutionStateCoordinator(repository)
    coordinator.enqueue(TaskJob(job_id="compare:TACTIVE:1", task_id="TACTIVE", task_type="compare", execution_no=1))

    with pytest.raises(TaskTransitionConflict):
        coordinator.enqueue(TaskJob(job_id="compare:TACTIVE:2", task_id="TACTIVE", task_type="compare", execution_no=2))

    assert [(job.job_id, job.status) for job in repository.list_jobs()] == [("compare:TACTIVE:1", "QUEUED")]


def test_enqueue_cannot_replace_terminal_job(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    repository = LocalJsonTaskJobRepository(app_settings)
    coordinator = ExecutionStateCoordinator(repository)
    job = coordinator.enqueue(
        TaskJob(
            job_id="compare:TENQUEUE_TERMINAL:1",
            task_id="TENQUEUE_TERMINAL",
            task_type="compare",
            execution_no=1,
        )
    )
    coordinator.claim_next(worker_id="worker-1", lease_seconds=30)
    coordinator.mark_failed(job.job_id, worker_id="worker-1", error="original failure", retry_delay_seconds=0)

    with pytest.raises(TaskTransitionConflict):
        coordinator.enqueue(job.model_copy(update={"status": "QUEUED", "attempt": 0, "last_error": ""}))

    stored = repository.load(job.job_id)
    assert stored.status == "FAILED"
    assert stored.attempt == 1
    assert stored.last_error == "original failure"


def test_enqueue_cannot_overwrite_terminal_execution_path(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    repository = LocalJsonTaskJobRepository(app_settings)
    coordinator = ExecutionStateCoordinator(repository)
    job = coordinator.enqueue(
        TaskJob(
            job_id="compare:TPATH_TERMINAL:1",
            task_id="TPATH_TERMINAL",
            task_type="compare",
            execution_no=1,
            attempt=1,
        )
    )
    coordinator.claim_next(worker_id="worker-1", lease_seconds=30)
    coordinator.mark_failed(job.job_id, worker_id="worker-1", error="original failure", retry_delay_seconds=0)

    with pytest.raises(TaskTransitionConflict):
        coordinator.enqueue(
            TaskJob(
                job_id="compare:TPATH_TERMINAL:01",
                task_id="TPATH_TERMINAL",
                task_type="compare",
                execution_no=1,
            )
        )

    assert repository.load(job.job_id).status == "FAILED"


def test_enqueue_rejects_execution_identity_already_stored_in_legacy_path(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    repository = LocalJsonTaskJobRepository(app_settings)
    legacy_path = app_settings.tasks_dir / "TLEGACY_TERMINAL" / "job.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        TaskJob(
            job_id="compare:TLEGACY_TERMINAL",
            task_id="TLEGACY_TERMINAL",
            task_type="compare",
            status="FAILED",
            execution_no=1,
            attempt=1,
            max_attempts=1,
            last_error="legacy failure",
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    coordinator = ExecutionStateCoordinator(repository)

    with pytest.raises(TaskTransitionConflict):
        coordinator.enqueue(
            TaskJob(
                job_id="compare:TLEGACY_TERMINAL:1",
                task_id="TLEGACY_TERMINAL",
                task_type="compare",
                execution_no=1,
            )
        )

    assert not (legacy_path.parent / "jobs" / "1.json").exists()
    assert json.loads(legacy_path.read_text(encoding="utf-8"))["status"] == "FAILED"


def test_lease_takeover_increments_attempt(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    repository = LocalJsonTaskJobRepository(app_settings)
    coordinator = ExecutionStateCoordinator(repository)
    coordinator.enqueue(
        TaskJob(
            job_id="compare:TLEASE:1",
            task_id="TLEASE",
            task_type="compare",
            execution_no=1,
            max_attempts=2,
        )
    )

    first_claim = coordinator.claim_next(worker_id="worker-1", lease_seconds=-1)
    takeover = coordinator.claim_next(worker_id="worker-2", lease_seconds=30)

    assert first_claim is not None
    assert first_claim.attempt == 1
    assert takeover is not None
    assert takeover.job_id == first_claim.job_id
    assert takeover.attempt == 2
    assert takeover.lease_owner == "worker-2"


def test_expired_lease_at_attempt_limit_fails_without_handler(tmp_path: Path) -> None:
    runner = build_runner(tmp_path, autostart=False)
    handler_calls = 0

    def handler(_context: TaskExecutionContext, _payload: Mapping[str, Any]) -> None:
        nonlocal handler_calls
        handler_calls += 1

    runner.register_handler("compare", handler)
    job = runner.submit(
        task_type="compare",
        task_id="TLEASE_LIMIT",
        payload={"task_id": "TLEASE_LIMIT"},
        max_attempts=1,
    )
    claimed = runner.job_repository.claim_next(worker_id="dead-worker", lease_seconds=-1)
    assert claimed is not None
    assert claimed.attempt == 1

    runner.start()
    wait_until(lambda: runner.job_repository.load(job.job_id).status == "FAILED")
    stored = runner.job_repository.load(job.job_id)
    runner.stop()

    assert handler_calls == 0
    assert stored.attempt == 1
    assert stored.error_code == "LEASE_EXPIRED_MAX_ATTEMPTS"


def test_runner_cancellation_token_stops_blocked_handler_and_marks_job_cancelled(tmp_path: Path) -> None:
    runner = build_runner(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def handler(context: TaskExecutionContext, _payload: Mapping[str, Any]) -> None:
        entered.set()
        assert release.wait(1)
        context.cancellation_token.raise_if_cancelled()

    runner.register_handler("compare", handler)
    job = runner.submit(task_type="compare", task_id="TBLOCKED_CANCEL", payload={})
    assert entered.wait(1)

    [requested] = runner.cancel(job.task_id, task_type="compare")
    release.set()
    wait_until(lambda: runner.job_repository.load(job.job_id).status == "CANCELLED")
    stored = runner.job_repository.load(job.job_id)
    runner.stop()

    assert requested.status == "CANCEL_REQUESTED"
    assert stored.status == "CANCELLED"
    assert stored.last_error == ""


def test_runner_checks_cancellation_immediately_before_terminal_commit(tmp_path: Path) -> None:
    runner = build_runner(tmp_path)

    def handler(context: TaskExecutionContext, _payload: Mapping[str, Any]) -> None:
        [requested] = runner.cancel(context.task_id, task_type="compare")
        assert requested.status == "CANCEL_REQUESTED"

    runner.register_handler("compare", handler)
    job = runner.submit(task_type="compare", task_id="TBEFORE_TERMINAL", payload={})
    wait_until(lambda: runner.job_repository.load(job.job_id).status == "CANCELLED")
    stored = runner.job_repository.load(job.job_id)
    runner.stop()

    assert stored.status == "CANCELLED"


def test_runner_stale_lease_stops_without_terminal_write(tmp_path: Path) -> None:
    runner = build_runner(tmp_path, autostart=False)
    handled = False

    def handler(_context: TaskExecutionContext, _payload: Mapping[str, Any]) -> None:
        nonlocal handled
        handled = True

    runner.register_handler("compare", handler)
    job = runner.submit(task_type="compare", task_id="TSTALE_RUNNER", payload={})
    claimed = runner.job_repository.claim_next(worker_id="expired-worker", lease_seconds=-1)
    assert claimed is not None

    runner._run_job(claimed, "expired-worker")

    stored = runner.job_repository.load(job.job_id)
    assert handled is False
    assert stored.status == "RUNNING"


def test_application_passes_execution_context_to_compare_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    task_repository.save_compare_task(CompareTask(task_id="THANDOFF"))
    job = runner.submit(task_type="compare", task_id="THANDOFF", payload={})
    claimed = runner.job_repository.claim_next(worker_id="worker-1", lease_seconds=30)
    assert claimed is not None
    execution_context = TaskExecutionContext(
        job_id=job.job_id,
        task_id=job.task_id,
        worker_id="worker-1",
        cancellation_token=CancellationToken(job.job_id, "worker-1", runner.coordinator),
    )
    seen_contexts: list[TaskExecutionContext] = []

    def compare(_service: CompareService, *_args: object, **kwargs: object) -> CompareTask:
        seen_contexts.append(kwargs["execution_context"])
        return task_repository.load_compare_task(job.task_id)

    monkeypatch.setattr(CompareService, "compare", compare)

    result = application._run_compare_job(
        execution_context,
        {
            "task_id": job.task_id,
            "original_path": str(tmp_path / "original.pdf"),
            "compare_path": str(tmp_path / "compare.pdf"),
        },
    )

    assert result.task_id == job.task_id
    assert seen_contexts == [execution_context]


@pytest.mark.parametrize("error_type", [TaskCancelled, TaskStaleLeaseError])
def test_application_handles_cancellation_separately_from_stale_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    task_repository.save_compare_task(CompareTask(task_id="TCONTROL", active_job_id="compare:TCONTROL:1"))
    job = runner.submit(task_type="compare", task_id="TCONTROL", payload={})
    claimed = runner.job_repository.claim_next(worker_id="worker-1", lease_seconds=30)
    assert claimed is not None
    execution_context = TaskExecutionContext(
        job_id=job.job_id,
        task_id=job.task_id,
        worker_id="worker-1",
        cancellation_token=CancellationToken(job.job_id, "worker-1", runner.coordinator),
    )

    def fail(*_args: object, **_kwargs: object) -> CompareTask:
        raise error_type("control flow")

    monkeypatch.setattr(CompareService, "compare", fail)

    with pytest.raises(error_type):
        application._run_compare_job(
            execution_context,
            {
                "task_id": job.task_id,
                "original_path": str(tmp_path / "original.pdf"),
                "compare_path": str(tmp_path / "compare.pdf"),
            },
        )

    stored_task = task_repository.load_compare_task(job.task_id)
    assert (stored_task.status, stored_task.terminal_reason) == ("PROCESSING", "NONE")


def test_queued_cancellation_before_claim_finishes_task_and_job_as_cancelled(tmp_path: Path) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    task_repository.save_compare_task(CompareTask(task_id="TQUEUED_CANCEL"))
    job = application.submit_compare(
        original_path=tmp_path / "original.pdf",
        compare_path=tmp_path / "compare.pdf",
        task_id="TQUEUED_CANCEL",
        original_filename="original.pdf",
        compare_filename="compare.pdf",
    )

    cancelled = application.cancel_compare(job.task_id)

    stored_task = task_repository.load_compare_task(job.task_id)
    assert cancelled.status == "CANCELLED"
    assert runner.job_repository.load(job.job_id).status == "CANCELLED"
    assert (stored_task.status, stored_task.terminal_reason) == ("FAILED", "CANCELLED")


@pytest.mark.parametrize("timing", ["before_stage", "blocked_stage", "after_stage", "before_terminal"])
def test_running_cancellation_timing_finishes_task_and_job_without_success_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    timing: str,
) -> None:
    runner = build_runner(tmp_path)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    task_id = f"TCANCEL_{timing.upper()}"
    task_repository.save_compare_task(CompareTask(task_id=task_id))
    entered = threading.Event()
    release = threading.Event()
    events: list[object] = []
    monkeypatch.setattr(ProgressBus.get_instance(), "publish", events.append)

    class TimingStage:
        name = "timing-stage"
        start_progress = 20
        progress = 90

        def execute(self, _ctx: PipelineContext) -> None:
            if timing == "blocked_stage":
                entered.set()
                assert release.wait(1)
            elif timing == "after_stage":
                requested = application.cancel_compare(task_id)
                assert requested.status == "CANCEL_REQUESTED"

    def build_pipeline(service: CompareService) -> ComparePipeline:
        return ComparePipeline(stages=[TimingStage()], repository=service.repository)

    monkeypatch.setattr(CompareService, "_build_pipeline", build_pipeline)
    original_compare = CompareService.compare

    if timing == "before_stage":

        def wait_before_stage(service: CompareService, *args: object, **kwargs: object) -> CompareTask:
            entered.set()
            assert release.wait(1)
            return original_compare(service, *args, **kwargs)

        monkeypatch.setattr(CompareService, "compare", wait_before_stage)

    token_checks = 0
    original_token_check = CancellationToken.raise_if_cancelled
    if timing == "before_terminal":

        def wait_before_terminal(token: CancellationToken) -> None:
            nonlocal token_checks
            if token.job_id.endswith(f":{task_id}:1"):
                token_checks += 1
                if token_checks == 7:
                    entered.set()
                    assert release.wait(1)
            original_token_check(token)

        monkeypatch.setattr(CancellationToken, "raise_if_cancelled", wait_before_terminal)

    job = application.submit_compare(
        original_path=tmp_path / "original.pdf",
        compare_path=tmp_path / "compare.pdf",
        task_id=task_id,
        original_filename="original.pdf",
        compare_filename="compare.pdf",
    )
    try:
        if timing in {"before_stage", "blocked_stage", "before_terminal"}:
            assert entered.wait(1)
            requested = application.cancel_compare(task_id)
            assert requested.status == "CANCEL_REQUESTED"
            release.set()

        wait_until(
            lambda: (
                runner.job_repository.load(job.job_id).status == "CANCELLED"
                and task_repository.load_compare_task(task_id).terminal_reason == "CANCELLED"
            )
        )
    finally:
        release.set()
        runner.stop()

    stored_task = task_repository.load_compare_task(task_id)
    assert runner.job_repository.load(job.job_id).status == "CANCELLED"
    assert (stored_task.status, stored_task.terminal_reason) == ("FAILED", "CANCELLED")
    assert not any(getattr(event, "status", None) == "COMPLETED" for event in events)


@pytest.mark.parametrize(
    ("first_outcome", "task_terminal", "job_terminal"),
    [
        ("success", ("COMPLETED", "NONE"), "SUCCEEDED"),
        ("failure", ("FAILED", "EXECUTION_FAILED"), "FAILED"),
        ("cancel", ("FAILED", "CANCELLED"), "CANCELLED"),
    ],
)
def test_terminal_job_write_failure_leaves_reconciliation_gap_and_worker_processes_next_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    first_outcome: str,
    task_terminal: tuple[str, str],
    job_terminal: str,
) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    first_task_id = f"TTERMINAL_GAP_{first_outcome.upper()}"
    second_task_id = f"TTERMINAL_AFTER_{first_outcome.upper()}"
    for task_id in (first_task_id, second_task_id):
        task_repository.save_compare_task(CompareTask(task_id=task_id))
    first_job = application.submit_compare(
        original_path=tmp_path / "original.pdf",
        compare_path=tmp_path / "compare.pdf",
        task_id=first_task_id,
        original_filename="original.pdf",
        compare_filename="compare.pdf",
    )
    second_job = application.submit_compare(
        original_path=tmp_path / "original.pdf",
        compare_path=tmp_path / "compare.pdf",
        task_id=second_task_id,
        original_filename="original.pdf",
        compare_filename="compare.pdf",
    )

    def handler(context: TaskExecutionContext, _payload: Mapping[str, Any]) -> CompareTask:
        if context.task_id == first_task_id:
            if first_outcome == "failure":
                raise RuntimeError("first handler failed")
            if first_outcome == "cancel":
                runner.coordinator.request_cancel(context.task_id, task_type="compare")
                context.cancellation_token.raise_if_cancelled()
        return CompareTask(task_id=context.task_id)

    runner.register_handler("compare", handler)
    job_repository = runner.coordinator._repository
    persist_job = job_repository._persist
    failed_once = False

    def fail_first_terminal_job(candidate: TaskJob) -> TaskJob:
        nonlocal failed_once
        if candidate.job_id == first_job.job_id and candidate.status == job_terminal and not failed_once:
            failed_once = True
            raise OSError("terminal job disk unavailable")
        return persist_job(candidate)

    monkeypatch.setattr(job_repository, "_persist", fail_first_terminal_job)
    caplog.set_level("ERROR", logger="app.infrastructure.task_runner")

    runner.start()
    try:
        wait_until(lambda: runner.coordinator.load(second_job.job_id).status == "SUCCEEDED")
        worker_survived = any(thread.is_alive() for thread in runner._threads)
        first_task = task_repository.load_compare_task(first_task_id)
        first_gap_job = runner.coordinator.load(first_job.job_id)
    finally:
        runner.stop()

    assert failed_once
    assert worker_survived
    assert (first_task.status, first_task.terminal_reason) == task_terminal
    assert first_gap_job.status not in {"SUCCEEDED", "FAILED", "CANCELLED"}
    assert "startup reconciliation required" in caplog.text

    monkeypatch.setattr(job_repository, "_persist", persist_job)
    restarted = ExecutionStateCoordinator(
        job_repository,
        task_repository=task_repository,
        progress_publisher=ProgressBus.get_instance(),
    )
    assert reconcile_terminal_jobs(task_repository, restarted) == 1
    assert restarted.load(first_job.job_id).status == job_terminal


@pytest.mark.parametrize("control_flow", ["cancel", "stale_lease"])
def test_stage_error_arbitrates_cancel_or_stale_lease_before_generic_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control_flow: str,
) -> None:
    runner = build_runner(tmp_path)
    runner.max_attempts = 2
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    task_id = f"TSTAGE_ERROR_{control_flow.upper()}"
    task_repository.save_compare_task(CompareTask(task_id=task_id))
    stage_entered = threading.Event()
    release_stage = threading.Event()
    compare_finished = threading.Event()
    events: list[object] = []
    monkeypatch.setattr(ProgressBus.get_instance(), "publish", events.append)

    class FailingStage:
        name = "failing-stage"
        start_progress = 20
        progress = 80

        def execute(self, ctx: PipelineContext) -> None:
            if control_flow == "stale_lease":
                assert ctx.execution_context is not None
                runner.coordinator.extend_lease(
                    ctx.execution_context.job_id,
                    worker_id=ctx.execution_context.worker_id,
                    lease_seconds=-1,
                )
            stage_entered.set()
            assert release_stage.wait(1)
            raise RuntimeError("stage exploded after control-flow change")

    def build_pipeline(service: CompareService) -> ComparePipeline:
        return ComparePipeline(stages=[FailingStage()], repository=service.repository)

    monkeypatch.setattr(CompareService, "_build_pipeline", build_pipeline)
    original_compare = CompareService.compare

    def track_compare(service: CompareService, *args: object, **kwargs: object) -> CompareTask:
        try:
            return original_compare(service, *args, **kwargs)
        finally:
            compare_finished.set()

    monkeypatch.setattr(CompareService, "compare", track_compare)
    job = application.submit_compare(
        original_path=tmp_path / "original.pdf",
        compare_path=tmp_path / "compare.pdf",
        task_id=task_id,
        original_filename="original.pdf",
        compare_filename="compare.pdf",
    )

    try:
        assert stage_entered.wait(1)
        if control_flow == "cancel":
            assert application.cancel_compare(task_id).status == "CANCEL_REQUESTED"
        else:
            takeover = runner.coordinator.claim_next(worker_id="replacement-worker", lease_seconds=30)
            assert takeover is not None
            assert takeover.job_id == job.job_id
        release_stage.set()
        assert compare_finished.wait(1)

        if control_flow == "cancel":
            wait_until(lambda: runner.coordinator.load(job.job_id).status == "CANCELLED")
            stored_job = runner.coordinator.load(job.job_id)
            stored_task = task_repository.load_compare_task(task_id)
            assert stored_job.status == "CANCELLED"
            assert (stored_task.status, stored_task.terminal_reason) == ("FAILED", "CANCELLED")
        else:
            stored_job = runner.coordinator.load(job.job_id)
            stored_task = task_repository.load_compare_task(task_id)
            assert (stored_job.status, stored_job.lease_owner) == ("RUNNING", "replacement-worker")
            assert (stored_task.status, stored_task.terminal_reason) == ("PROCESSING", "NONE")
        if control_flow == "cancel":
            terminal_events = [event for event in events if getattr(event, "status", None) == "FAILED"]
            assert len(terminal_events) == 1
            assert terminal_events[0].stage == "已取消"
        else:
            assert not any(getattr(event, "status", None) == "FAILED" for event in events)
    finally:
        release_stage.set()
        runner.stop()


def test_legacy_job_updates_preserve_source_path(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    repository = LocalJsonTaskJobRepository(app_settings)
    legacy_path = app_settings.tasks_dir / "TLEGACY_SOURCE" / "job.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(
        TaskJob(
            job_id="compare:TLEGACY_SOURCE",
            task_id="TLEGACY_SOURCE",
            task_type="compare",
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    coordinator = ExecutionStateCoordinator(repository)

    [loaded] = repository.list_jobs()
    claimed = coordinator.claim_next(worker_id="legacy-worker", lease_seconds=30)

    assert loaded.source_path == str(legacy_path)
    assert claimed is not None
    assert claimed.source_path == str(legacy_path)
    assert json.loads(legacy_path.read_text(encoding="utf-8"))["attempt"] == 1
    assert not (legacy_path.parent / "jobs" / "1.json").exists()


def test_task_job_execution_identity_fields_have_legacy_defaults() -> None:
    job = TaskJob(job_id="compare:TLEGACY", task_id="TLEGACY", task_type="compare")

    assert job.execution_no == 1
    assert job.error_code == ""


@pytest.mark.parametrize(("first_terminal", "second_terminal"), [("SUCCEEDED", "FAILED"), ("FAILED", "SUCCEEDED")])
def test_job_terminal_state_is_immutable(
    tmp_path: Path,
    first_terminal: str,
    second_terminal: str,
) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    repository = LocalJsonTaskJobRepository(app_settings)
    coordinator = ExecutionStateCoordinator(repository)
    job = coordinator.enqueue(
        TaskJob(
            job_id="compare:TTERMINAL",
            task_id="TTERMINAL",
            task_type="compare",
            attempt=1,
        )
    )

    coordinator.claim_next(worker_id="worker-1", lease_seconds=30)
    if first_terminal == "SUCCEEDED":
        coordinator.mark_succeeded(job.job_id, worker_id="worker-1")
    else:
        coordinator.mark_failed(job.job_id, worker_id="worker-1", error="first", retry_delay_seconds=0)

    with pytest.raises(TaskTransitionConflict):
        if second_terminal == "SUCCEEDED":
            coordinator.mark_succeeded(job.job_id, worker_id="worker-1")
        else:
            coordinator.mark_failed(job.job_id, worker_id="worker-1", error="second", retry_delay_seconds=0)

    assert repository.load(job.job_id).status == first_terminal


def test_task_control_flow_errors_share_execution_error_base() -> None:
    error = TaskStaleLeaseError("stale worker")

    assert isinstance(error, TaskExecutionError)
    assert isinstance(TaskCancelled("cancelled"), TaskExecutionError)
    assert isinstance(TaskTransitionConflict("conflict"), TaskExecutionError)
    assert error.status_code == 409
