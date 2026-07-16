from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Mapping

import pytest

from app.config import Settings
from app.errors import TaskCancelled, TaskExecutionError, TaskStaleLeaseError, TaskTransitionConflict
from app.infrastructure.task_runner import LocalJsonTaskJobRepository, QueuedTaskRunner, TaskJob


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

    def handler(payload: Mapping[str, Any]) -> None:
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
    runner.register_handler("compare", lambda payload: None)

    job = runner.submit(task_type="compare", task_id="TFIRST", payload={"task_id": "TFIRST"})

    assert job.job_id == "compare:TFIRST:1"
    assert job.execution_no == 1
    assert (runner.settings.tasks_dir / "TFIRST" / "jobs" / "1.json").is_file()
    assert not (runner.settings.tasks_dir / "TFIRST" / "job.json").exists()


def test_queued_task_runner_retries_failed_jobs(tmp_path: Path) -> None:
    runner = build_runner(tmp_path)
    attempts = 0

    def handler(payload: Mapping[str, Any]) -> None:
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

    def handler(payload: Mapping[str, Any]) -> None:
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
    assert stored.status == "CANCELLED"
    assert executed is False


def test_queued_task_runner_limits_concurrency(tmp_path: Path) -> None:
    runner = build_runner(tmp_path, max_workers=2)
    active = 0
    max_active = 0
    lock = threading.Lock()
    finished_count = 0

    def handler(payload: Mapping[str, Any]) -> None:
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

    def failing_handler(payload: Mapping[str, Any]) -> None:
        raise RuntimeError("first failure")

    runner.register_handler("compare", failing_handler)
    job = runner.submit(task_type="compare", task_id="TRETRY_API", payload={"task_id": "TRETRY_API"})
    wait_until(lambda: runner.job_repository.load(job.job_id).status == "FAILED")

    seen_payload: list[Mapping[str, Any]] = []

    def succeeding_handler(payload: Mapping[str, Any]) -> None:
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

    def handler(payload: Mapping[str, Any]) -> None:
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
