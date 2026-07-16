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
from app.infrastructure.execution_state import CancellationToken, TaskExecutionContext
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

    jobs = repository.list_jobs()
    claimed = repository.claim_next(worker_id="compare-worker", lease_seconds=30)

    assert [(job.job_id, job.task_type) for job in jobs] == [("compare:TCOMPARE", "compare")]
    assert claimed is not None
    assert (claimed.job_id, claimed.task_type) == ("compare:TCOMPARE", "compare")
    assert repository.claim_next(worker_id="compare-worker", lease_seconds=30) is None
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


def test_submit_rejects_legacy_raw_job_id_collision_with_different_identity(tmp_path: Path) -> None:
    runner = build_runner(tmp_path, autostart=False)
    runner.register_handler("compare", lambda _context, _payload: None)
    legacy_path = runner.settings.tasks_dir / "tenant_1" / "job.json"
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
    repository.enqueue(TaskJob(job_id="compare:TACTIVE:1", task_id="TACTIVE", task_type="compare", execution_no=1))

    with pytest.raises(TaskTransitionConflict):
        repository.enqueue(TaskJob(job_id="compare:TACTIVE:2", task_id="TACTIVE", task_type="compare", execution_no=2))

    assert [(job.job_id, job.status) for job in repository.list_jobs()] == [("compare:TACTIVE:1", "QUEUED")]


def test_enqueue_cannot_replace_terminal_job(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    repository = LocalJsonTaskJobRepository(app_settings)
    job = repository.enqueue(
        TaskJob(
            job_id="compare:TENQUEUE_TERMINAL:1",
            task_id="TENQUEUE_TERMINAL",
            task_type="compare",
            execution_no=1,
            attempt=1,
        )
    )
    repository.mark_failed(job.job_id, worker_id="", error="original failure", retry_delay_seconds=0)

    with pytest.raises(TaskTransitionConflict):
        repository.enqueue(job.model_copy(update={"status": "QUEUED", "attempt": 0, "last_error": ""}))

    stored = repository.load(job.job_id)
    assert stored.status == "FAILED"
    assert stored.attempt == 1
    assert stored.last_error == "original failure"


def test_enqueue_cannot_overwrite_terminal_execution_path(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    repository = LocalJsonTaskJobRepository(app_settings)
    job = repository.enqueue(
        TaskJob(
            job_id="compare:TPATH_TERMINAL:1",
            task_id="TPATH_TERMINAL",
            task_type="compare",
            execution_no=1,
            attempt=1,
        )
    )
    repository.mark_failed(job.job_id, worker_id="", error="original failure", retry_delay_seconds=0)

    with pytest.raises(TaskTransitionConflict):
        repository.enqueue(
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

    with pytest.raises(TaskTransitionConflict):
        repository.enqueue(
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
    repository.enqueue(
        TaskJob(
            job_id="compare:TLEASE:1",
            task_id="TLEASE",
            task_type="compare",
            execution_no=1,
            max_attempts=2,
        )
    )

    first_claim = repository.claim_next(worker_id="worker-1", lease_seconds=-1)
    takeover = repository.claim_next(worker_id="worker-2", lease_seconds=30)

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
    if error_type is TaskCancelled:
        assert (stored_task.status, stored_task.terminal_reason) == ("FAILED", "CANCELLED")
    else:
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
                if token_checks == 5:
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
            lambda: runner.job_repository.load(job.job_id).status == "CANCELLED"
            and task_repository.load_compare_task(task_id).terminal_reason == "CANCELLED"
        )
    finally:
        release.set()
        runner.stop()

    stored_task = task_repository.load_compare_task(task_id)
    assert runner.job_repository.load(job.job_id).status == "CANCELLED"
    assert (stored_task.status, stored_task.terminal_reason) == ("FAILED", "CANCELLED")
    assert not any(getattr(event, "status", None) == "COMPLETED" for event in events)


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

    [loaded] = repository.list_jobs()
    claimed = repository.claim_next(worker_id="legacy-worker", lease_seconds=30)

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
    job = repository.enqueue(
        TaskJob(
            job_id="compare:TTERMINAL",
            task_id="TTERMINAL",
            task_type="compare",
            attempt=1,
        )
    )

    if first_terminal == "SUCCEEDED":
        repository.mark_succeeded(job.job_id, worker_id="")
    else:
        repository.mark_failed(job.job_id, worker_id="", error="first", retry_delay_seconds=0)

    with pytest.raises(TaskTransitionConflict):
        if second_terminal == "SUCCEEDED":
            repository.mark_succeeded(job.job_id, worker_id="")
        else:
            repository.mark_failed(job.job_id, worker_id="", error="second", retry_delay_seconds=0)

    assert repository.load(job.job_id).status == first_terminal


def test_task_control_flow_errors_share_execution_error_base() -> None:
    error = TaskStaleLeaseError("stale worker")

    assert isinstance(error, TaskExecutionError)
    assert isinstance(TaskCancelled("cancelled"), TaskExecutionError)
    assert isinstance(TaskTransitionConflict("conflict"), TaskExecutionError)
    assert error.status_code == 409
