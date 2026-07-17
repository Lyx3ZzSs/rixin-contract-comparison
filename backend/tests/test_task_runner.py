from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Mapping

import pytest

from app.application.compare_tasks import CompareTaskApplication
from app.config import Settings
from app.errors import (
    TaskCancelled,
    TaskExecutionError,
    TaskRepositoryReadError,
    TaskStaleLeaseError,
    TaskTransitionConflict,
)
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


def _build_failed_retry_task(
    tmp_path: Path,
    task_id: str,
) -> tuple[QueuedTaskRunner, LocalJsonTaskRepository, CompareTaskApplication, str, TaskJob]:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    original_path = tmp_path / f"{task_id}-original.pdf"
    compare_path = tmp_path / f"{task_id}-compare.pdf"
    original_path.touch()
    compare_path.touch()
    first_job = TaskJob(
        job_id=f"compare:{task_id}:1",
        task_id=task_id,
        task_type="compare",
        execution_no=1,
        payload={"source": "first-failure"},
    )
    task_repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            active_job_id=first_job.job_id,
            original_pdf_path=str(original_path),
            compare_pdf_path=str(compare_path),
        )
    )
    runner.coordinator.enqueue(first_job)
    claimed = runner.coordinator.claim_next(worker_id="seed-worker", lease_seconds=30)
    assert claimed is not None
    runner.coordinator.commit_failure(
        first_job.job_id,
        worker_id="seed-worker",
        error="first failure",
        retry_delay_seconds=0,
    )
    return runner, task_repository, application, task_id, first_job


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


@pytest.mark.parametrize("failure", ["write", "replace"])
def test_job_persist_failure_cleans_authoritative_temp_without_replacing_primary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    repository = LocalJsonTaskJobRepository(app_settings)
    job = TaskJob(job_id="compare:TJOB_TEMP:1", task_id="TJOB_TEMP", task_type="compare")
    write_text = Path.write_text
    replace = Path.replace

    def fail_write(path: Path, *args, **kwargs):
        result = write_text(path, *args, **kwargs)
        if path.name.endswith(".json.tmp"):
            raise OSError("job-write-primary")
        return result

    def fail_replace(path: Path, target: Path):
        if path.name.endswith(".json.tmp"):
            raise OSError("job-replace-primary")
        return replace(path, target)

    monkeypatch.setattr(Path, "write_text", fail_write if failure == "write" else write_text)
    monkeypatch.setattr(Path, "replace", fail_replace if failure == "replace" else replace)

    with pytest.raises(OSError, match=f"job-{failure}-primary"):
        repository._persist(job)

    assert list(app_settings.tasks_dir.rglob("*.json.tmp")) == []
    assert not (app_settings.tasks_dir / "TJOB_TEMP" / "jobs" / "1.json").exists()


def test_job_temp_cleanup_failure_does_not_override_replace_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    repository = LocalJsonTaskJobRepository(app_settings)
    job = TaskJob(job_id="compare:TJOB_CLEANUP:1", task_id="TJOB_CLEANUP", task_type="compare")
    unlink = Path.unlink
    monkeypatch.setattr(
        Path,
        "replace",
        lambda path, _target: (
            (_ for _ in ()).throw(OSError("job-replace-primary")) if path.name.endswith(".json.tmp") else None
        ),
    )

    def fail_cleanup(path: Path, *args, **kwargs):
        if path.name.endswith(".json.tmp"):
            raise OSError("job-cleanup-secondary")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_cleanup)

    with pytest.raises(OSError, match="job-replace-primary"):
        repository._persist(job)


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


@pytest.mark.parametrize("operation", ["submit", "retry"])
@pytest.mark.parametrize("failed_manifest", ["task", "job"])
def test_manifest_failure_after_primary_replace_preserves_task_job_association(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    failed_manifest: str,
) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    original_path = tmp_path / "original.pdf"
    compare_path = tmp_path / "compare.pdf"
    original_path.touch()
    compare_path.touch()
    task_id = f"TMANIFEST_{operation.upper()}_{failed_manifest.upper()}"
    task_repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            original_pdf_path=str(original_path),
            compare_pdf_path=str(compare_path),
        )
    )
    expected_execution_no = 1
    expected_job_ids: list[str] = []
    if operation == "retry":
        first_job_id = f"compare:{task_id}:1"
        task_repository.update_compare_task(
            task_id,
            lambda task: setattr(task, "active_job_id", first_job_id),
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
        expected_execution_no = 2
        expected_job_ids.append(first.job_id)

    expected_job_id = f"compare:{task_id}:{expected_execution_no}"
    primary_records_seen: list[str] = []
    job_repository = runner.coordinator._repository

    if failed_manifest == "task":

        def fail_task_manifest(manifest_task_id: str, _data: dict[str, Any]) -> None:
            primary = task_repository.load_compare_task(manifest_task_id)
            primary_records_seen.append(primary.active_job_id)
            raise OSError("task manifest unavailable")

        monkeypatch.setattr(task_repository, "_write_manifest", fail_task_manifest)
    else:

        def fail_job_manifest(candidate: TaskJob, job_path: Path) -> None:
            primary = TaskJob.model_validate_json(job_path.read_text(encoding="utf-8"))
            primary_records_seen.append(primary.job_id)
            raise OSError("job manifest unavailable")

        monkeypatch.setattr(job_repository, "_write_manifest", fail_job_manifest)

    if operation == "submit":
        job = application.submit_compare(
            original_path=original_path,
            compare_path=compare_path,
            task_id=task_id,
            original_filename=original_path.name,
            compare_filename=compare_path.name,
        )
    else:
        job = application.retry_compare(task_id)

    expected_job_ids.append(expected_job_id)
    stored_task = task_repository.load_compare_task(task_id)
    stored_job = job_repository.load(expected_job_id)

    assert primary_records_seen == [expected_job_id]
    assert job.job_id == expected_job_id
    assert (stored_task.active_job_id, stored_task.status) == (expected_job_id, "PROCESSING")
    assert (stored_job.job_id, stored_job.status) == (expected_job_id, "QUEUED")
    assert [item.job_id for item in runner.coordinator.list_jobs()] == expected_job_ids


@pytest.mark.parametrize("operation", ["submit", "retry"])
def test_post_commit_task_reload_failure_cannot_split_task_job_association(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    original_path = tmp_path / "original.pdf"
    compare_path = tmp_path / "compare.pdf"
    original_path.touch()
    compare_path.touch()
    task_id = f"TPOST_COMMIT_READ_{operation.upper()}"
    task_repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            original_pdf_path=str(original_path),
            compare_pdf_path=str(compare_path),
        )
    )
    expected_execution_no = 1
    expected_job_ids: list[str] = []
    if operation == "retry":
        first_job_id = f"compare:{task_id}:1"
        task_repository.update_compare_task(
            task_id,
            lambda task: setattr(task, "active_job_id", first_job_id),
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
        expected_execution_no = 2
        expected_job_ids.append(first.job_id)

    expected_job_id = f"compare:{task_id}:{expected_execution_no}"
    load_committed_attempts: list[str] = []
    load_compare_task = task_repository.load_compare_task

    def fail_if_primary_is_already_committed(load_task_id: str) -> CompareTask:
        stored = load_compare_task(load_task_id)
        if stored.active_job_id == expected_job_id:
            load_committed_attempts.append(stored.active_job_id)
            raise OSError("post-commit task read unavailable")
        return stored

    monkeypatch.setattr(task_repository, "load_compare_task", fail_if_primary_is_already_committed)

    if operation == "submit":
        job = application.submit_compare(
            original_path=original_path,
            compare_path=compare_path,
            task_id=task_id,
            original_filename=original_path.name,
            compare_filename=compare_path.name,
        )
    else:
        job = application.retry_compare(task_id)

    expected_job_ids.append(expected_job_id)
    stored_task = load_compare_task(task_id)
    stored_job = runner.coordinator._repository.load(expected_job_id)

    assert load_committed_attempts == []
    assert job.job_id == expected_job_id
    assert (stored_task.active_job_id, stored_task.status) == (expected_job_id, "PROCESSING")
    assert (stored_job.job_id, stored_job.status) == (expected_job_id, "QUEUED")
    assert [item.job_id for item in runner.coordinator.list_jobs()] == expected_job_ids


@pytest.mark.parametrize("operation", ["submit", "retry"])
def test_task_primary_write_failure_reports_error_without_changing_task_or_jobs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    original_path = tmp_path / "original.pdf"
    compare_path = tmp_path / "compare.pdf"
    original_path.touch()
    compare_path.touch()
    task_id = f"TPRE_COMMIT_WRITE_{operation.upper()}"
    task_repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            stage="before write",
            original_pdf_path=str(original_path),
            compare_pdf_path=str(compare_path),
        )
    )
    expected_execution_no = 1
    expected_job_ids: list[str] = []
    if operation == "retry":
        first_job_id = f"compare:{task_id}:1"
        task_repository.update_compare_task(
            task_id,
            lambda task: setattr(task, "active_job_id", first_job_id),
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
        expected_execution_no = 2
        expected_job_ids.append(first.job_id)

    before = task_repository.load_compare_task(task_id)
    expected_job_id = f"compare:{task_id}:{expected_execution_no}"
    write_task = task_repository._write_task

    def fail_before_primary_replace(write_task_id: str, data: dict[str, Any]) -> Path:
        if data.get("active_job_id") == expected_job_id:
            raise OSError("task primary unavailable before replace")
        return write_task(write_task_id, data)

    monkeypatch.setattr(task_repository, "_write_task", fail_before_primary_replace)

    with pytest.raises(OSError, match="task primary unavailable before replace"):
        if operation == "submit":
            application.submit_compare(
                original_path=original_path,
                compare_path=compare_path,
                task_id=task_id,
                original_filename=original_path.name,
                compare_filename=compare_path.name,
            )
        else:
            application.retry_compare(task_id)

    stored = task_repository.load_compare_task(task_id)
    assert stored.model_dump() == before.model_dump()
    assert [item.job_id for item in runner.coordinator.list_jobs()] == expected_job_ids


def test_worker_survives_task_read_error_and_completes_after_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    task_id = "TWORKER_READ_RECOVERY"
    task_repository.save_compare_task(CompareTask(task_id=task_id))
    job = application.submit_compare(
        original_path=tmp_path / "original.pdf",
        compare_path=tmp_path / "compare.pdf",
        task_id=task_id,
        original_filename="original.pdf",
        compare_filename="compare.pdf",
    )
    load_task = task_repository.load_compare_task
    failed_once = threading.Event()

    def fail_once(load_task_id: str) -> CompareTask:
        if not failed_once.is_set():
            failed_once.set()
            raise TaskRepositoryReadError("transient task read failure")
        return load_task(load_task_id)

    monkeypatch.setattr(task_repository, "load_compare_task", fail_once)
    runner.register_handler("compare", lambda context, _payload: load_task(context.task_id))
    caplog.set_level("WARNING", logger="app.infrastructure.execution_state")

    runner.start()
    try:
        assert failed_once.wait(1)
        wait_until(lambda: runner.coordinator.load(job.job_id).status == "SUCCEEDED")
        worker_survived = any(thread.is_alive() for thread in runner._threads)
    finally:
        runner.stop()

    assert worker_survived
    assert runner.coordinator.load(job.job_id).status == "SUCCEEDED"
    assert task_id in caplog.text
    assert job.job_id in caplog.text


def test_application_execution_and_cancel_target_task_active_job_not_latest(tmp_path: Path) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    task_id = "TCONTROL_ACTIVE"
    job_repository = runner.coordinator._repository
    active_job = job_repository._persist(
        TaskJob(job_id=f"compare:{task_id}:1", task_id=task_id, task_type="compare", execution_no=1)
    )
    stray_latest = job_repository._persist(
        TaskJob(job_id=f"compare:{task_id}:2", task_id=task_id, task_type="compare", execution_no=2)
    )
    task_repository.save_compare_task(CompareTask(task_id=task_id, active_job_id=active_job.job_id))
    runner.coordinator = ExecutionStateCoordinator(job_repository)
    runner.job_repository = runner.coordinator
    application = CompareTaskApplication(repository=task_repository, runner=runner)

    assert application.load_execution(task_id).job_id == active_job.job_id
    cancelled = application.cancel_compare(task_id)

    assert cancelled.job_id == active_job.job_id
    assert cancelled.status == "CANCELLED"
    assert runner.coordinator.load(stray_latest.job_id).status == "QUEUED"


def test_application_execution_without_active_job_is_not_found(tmp_path: Path) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    task_repository.save_compare_task(CompareTask(task_id="TNO_ACTIVE_CONTROL"))
    application = CompareTaskApplication(repository=task_repository, runner=runner)

    with pytest.raises(Exception, match="活动执行记录不存在"):
        application.load_execution("TNO_ACTIVE_CONTROL")

    with pytest.raises(Exception, match="活动执行记录不存在"):
        application.cancel_compare("TNO_ACTIVE_CONTROL")


def test_retry_uses_task_bound_terminal_job_when_higher_orphan_exists(tmp_path: Path) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    task_id = "TRETRY_BOUND_SOURCE"
    original_path = tmp_path / "original.pdf"
    compare_path = tmp_path / "compare.pdf"
    original_path.touch()
    compare_path.touch()
    bound_job = TaskJob(
        job_id=f"compare:{task_id}:1",
        task_id=task_id,
        task_type="compare",
        execution_no=1,
        status="FAILED",
        attempt=1,
        max_attempts=3,
        payload={"source": "task-terminal-binding"},
    )
    orphan_job = TaskJob(
        job_id=f"compare:{task_id}:3",
        task_id=task_id,
        task_type="compare",
        execution_no=3,
        status="SUCCEEDED",
        attempt=1,
        max_attempts=9,
        payload={"source": "higher-orphan"},
    )
    job_repository = runner.coordinator._repository
    job_repository._persist(bound_job)
    job_repository._persist(orphan_job)
    task_repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            status="FAILED",
            terminal_reason="EXECUTION_FAILED",
            active_job_id=bound_job.job_id,
            terminal_job_id=bound_job.job_id,
            terminal_attempt=bound_job.attempt,
            original_pdf_path=str(original_path),
            compare_pdf_path=str(compare_path),
        )
    )
    runner.coordinator = ExecutionStateCoordinator(job_repository)
    runner.job_repository = runner.coordinator
    application = CompareTaskApplication(repository=task_repository, runner=runner)

    retried = application.retry_compare(task_id)

    assert (retried.job_id, retried.execution_no) == (f"compare:{task_id}:4", 4)
    assert retried.payload == bound_job.payload
    assert retried.max_attempts == bound_job.max_attempts


def test_retry_legacy_task_without_terminal_binding_falls_back_to_latest_failed_job(tmp_path: Path) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    task_id = "TRETRY_LEGACY_SOURCE"
    original_path = tmp_path / "original.pdf"
    compare_path = tmp_path / "compare.pdf"
    original_path.touch()
    compare_path.touch()
    job_repository = runner.coordinator._repository
    job_repository._persist(
        TaskJob(
            job_id=f"compare:{task_id}:1",
            task_id=task_id,
            task_type="compare",
            execution_no=1,
            status="FAILED",
            attempt=1,
            payload={"source": "older"},
        )
    )
    latest = job_repository._persist(
        TaskJob(
            job_id=f"compare:{task_id}:2",
            task_id=task_id,
            task_type="compare",
            execution_no=2,
            status="FAILED",
            attempt=1,
            max_attempts=4,
            payload={"source": "legacy-latest"},
        )
    )
    task_repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            status="FAILED",
            terminal_reason="EXECUTION_FAILED",
            active_job_id=latest.job_id,
            original_pdf_path=str(original_path),
            compare_pdf_path=str(compare_path),
        )
    )
    runner.coordinator = ExecutionStateCoordinator(job_repository)
    runner.job_repository = runner.coordinator
    application = CompareTaskApplication(repository=task_repository, runner=runner)

    retried = application.retry_compare(task_id)

    assert (retried.job_id, retried.execution_no) == (f"compare:{task_id}:3", 3)
    assert retried.payload == latest.payload
    assert retried.max_attempts == latest.max_attempts


def test_retry_first_then_cancel_targets_new_active_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner, task_repository, application, task_id, first_job = _build_failed_retry_task(
        tmp_path, "TRETRY_CANCEL_RETRY_FIRST"
    )
    persist_job = runner.coordinator._repository._persist
    retry_holds_lock = threading.Event()
    release_retry = threading.Event()

    def pause_retry_persist(candidate: TaskJob) -> TaskJob:
        if candidate.execution_no == 2 and candidate.status == "QUEUED":
            retry_holds_lock.set()
            assert release_retry.wait(5)
        return persist_job(candidate)

    monkeypatch.setattr(runner.coordinator._repository, "_persist", pause_retry_persist)
    retried: list[TaskJob] = []
    cancelled: list[TaskJob] = []
    errors: list[BaseException] = []

    def retry() -> None:
        try:
            retried.append(application.retry_compare(task_id))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def cancel() -> None:
        try:
            cancelled.append(application.cancel_compare(task_id))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    retry_thread = threading.Thread(target=retry)
    retry_thread.start()
    assert retry_holds_lock.wait(5)
    cancel_thread = threading.Thread(target=cancel)
    cancel_thread.start()
    release_retry.set()
    retry_thread.join(5)
    cancel_thread.join(5)

    assert not retry_thread.is_alive() and not cancel_thread.is_alive()
    assert errors == []
    assert [job.job_id for job in retried] == [f"compare:{task_id}:2"]
    assert [(job.job_id, job.status) for job in cancelled] == [(f"compare:{task_id}:2", "CANCELLED")]
    assert runner.coordinator.load(first_job.job_id).status == "FAILED"
    assert (
        task_repository.load_compare_task(task_id).status,
        task_repository.load_compare_task(task_id).terminal_reason,
    ) == (
        "FAILED",
        "CANCELLED",
    )


def test_cancel_first_conflicts_before_retry_can_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner, task_repository, application, task_id, first_job = _build_failed_retry_task(
        tmp_path, "TRETRY_CANCEL_CANCEL_FIRST"
    )
    load_task = task_repository.load_compare_task
    cancel_holds_read = threading.Event()
    release_cancel = threading.Event()
    cancel_thread_id: list[int] = []

    def pause_cancel_task_read(load_task_id: str) -> CompareTask:
        task = load_task(load_task_id)
        if cancel_thread_id and threading.get_ident() == cancel_thread_id[0]:
            cancel_holds_read.set()
            assert release_cancel.wait(5)
        return task

    monkeypatch.setattr(task_repository, "load_compare_task", pause_cancel_task_read)
    retried: list[TaskJob] = []
    cancel_conflicts: list[TaskTransitionConflict] = []
    unexpected: list[BaseException] = []

    def cancel() -> None:
        cancel_thread_id.append(threading.get_ident())
        try:
            application.cancel_compare(task_id)
        except TaskTransitionConflict as exc:
            cancel_conflicts.append(exc)
        except BaseException as exc:  # pragma: no cover - asserted below
            unexpected.append(exc)

    retry_started = threading.Barrier(2)

    def retry() -> None:
        retry_started.wait(timeout=5)
        try:
            retried.append(application.retry_compare(task_id))
        except BaseException as exc:  # pragma: no cover - asserted below
            unexpected.append(exc)

    cancel_thread = threading.Thread(target=cancel)
    cancel_thread.start()
    assert cancel_holds_read.wait(5)
    retry_thread = threading.Thread(target=retry)
    retry_thread.start()
    retry_started.wait(timeout=5)
    time.sleep(0.05)
    release_cancel.set()
    cancel_thread.join(5)
    retry_thread.join(5)

    assert not cancel_thread.is_alive() and not retry_thread.is_alive()
    assert unexpected == []
    assert len(cancel_conflicts) == 1
    assert [job.job_id for job in retried] == [f"compare:{task_id}:2"]
    assert runner.coordinator.load(first_job.job_id).status == "FAILED"
    assert task_repository.load_compare_task(task_id).active_job_id == f"compare:{task_id}:2"


def test_single_worker_recovers_from_claim_persistence_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    task_id = "TCLAIM_PERSIST_RECOVERY"
    task_repository.save_compare_task(CompareTask(task_id=task_id))
    job = application.submit_compare(
        original_path=tmp_path / "original.pdf",
        compare_path=tmp_path / "compare.pdf",
        task_id=task_id,
        original_filename="original.pdf",
        compare_filename="compare.pdf",
    )
    runner.register_handler("compare", lambda context, _payload: task_repository.load_compare_task(context.task_id))
    job_repository = runner.coordinator._repository
    persist = job_repository._persist
    failed_once = False

    def fail_first_claim(candidate: TaskJob) -> TaskJob:
        nonlocal failed_once
        if candidate.job_id == job.job_id and candidate.status == "RUNNING" and not failed_once:
            failed_once = True
            raise OSError("claim disk temporarily unavailable")
        return persist(candidate)

    monkeypatch.setattr(job_repository, "_persist", fail_first_claim)
    caplog.set_level("WARNING", logger="app.infrastructure.task_runner")

    runner.start()
    try:
        wait_until(lambda: runner.coordinator.load(job.job_id).status == "SUCCEEDED")
        worker_alive = any(thread.is_alive() for thread in runner._threads)
    finally:
        runner.stop()

    assert failed_once
    assert worker_alive
    assert "claim" in caplog.text
    assert job.job_id in caplog.text


def test_single_worker_logs_programming_claim_error_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    task_id = "TCLAIM_PROGRAMMING_RECOVERY"
    task_repository.save_compare_task(CompareTask(task_id=task_id))
    job = application.submit_compare(
        original_path=tmp_path / "original.pdf",
        compare_path=tmp_path / "compare.pdf",
        task_id=task_id,
        original_filename="original.pdf",
        compare_filename="compare.pdf",
    )
    runner.register_handler("compare", lambda context, _payload: task_repository.load_compare_task(context.task_id))
    claim_next = runner.coordinator.claim_next
    failed_once = False

    def fail_first_claim(*, worker_id: str, lease_seconds: int) -> TaskJob | None:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("claim programming defect")
        return claim_next(worker_id=worker_id, lease_seconds=lease_seconds)

    monkeypatch.setattr(runner.coordinator, "claim_next", fail_first_claim)
    caplog.set_level("ERROR", logger="app.infrastructure.task_runner")

    runner.start()
    try:
        wait_until(lambda: runner.coordinator.load(job.job_id).status == "SUCCEEDED")
    finally:
        runner.stop()

    assert failed_once
    assert "claim programming defect" in caplog.text


def test_heartbeat_retries_transient_persistence_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = build_runner(tmp_path, autostart=False)
    runner.lease_seconds = 0.03
    stop_event = threading.Event()
    calls = 0
    job = TaskJob(job_id="compare:THEARTBEAT:1", task_id="THEARTBEAT", task_type="compare")

    def extend_lease(_job_id: str, *, worker_id: str, lease_seconds: int) -> TaskJob:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("heartbeat disk temporarily unavailable")
        stop_event.set()
        return job

    monkeypatch.setattr(runner.coordinator, "extend_lease", extend_lease)
    caplog.set_level("WARNING", logger="app.infrastructure.task_runner")
    heartbeat = threading.Thread(
        target=runner._heartbeat_loop,
        args=(job.job_id, "worker-1", stop_event),
    )

    heartbeat.start()
    heartbeat.join(timeout=0.5)
    stop_event.set()
    heartbeat.join(timeout=1)

    assert calls == 2
    assert "heartbeat" in caplog.text.lower()
    assert job.job_id in caplog.text


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
    task_repository.update_compare_task(
        job.task_id,
        lambda task: setattr(task, "active_job_id", job.job_id),
    )
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


@pytest.mark.parametrize(
    ("first_outcome", "task_terminal", "job_terminal"),
    [
        ("success", ("COMPLETED", "NONE"), "SUCCEEDED"),
        ("failure", ("FAILED", "EXECUTION_FAILED"), "FAILED"),
    ],
)
def test_expired_terminal_gap_stays_repairable_while_worker_processes_next_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_outcome: str,
    task_terminal: tuple[str, str],
    job_terminal: str,
) -> None:
    runner = build_runner(tmp_path, autostart=False)
    runner.poll_interval_seconds = 10
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    first_task_id = f"TEXPIRED_GAP_{first_outcome.upper()}"
    second_task_id = f"TEXPIRED_GAP_AFTER_{first_outcome.upper()}"
    task_repository.save_compare_task(CompareTask(task_id=first_task_id))
    first_job = application.submit_compare(
        original_path=tmp_path / "original.pdf",
        compare_path=tmp_path / "compare.pdf",
        task_id=first_task_id,
        original_filename="original.pdf",
        compare_filename="compare.pdf",
    )

    def handler(context: TaskExecutionContext, _payload: Mapping[str, Any]) -> CompareTask:
        if context.task_id == first_task_id and first_outcome == "failure":
            raise RuntimeError("first handler failed")
        return CompareTask(task_id=context.task_id)

    runner.register_handler("compare", handler)
    job_repository = runner.coordinator._repository
    persist_job = job_repository._persist
    terminal_job_write_attempted = threading.Event()
    terminal_job_write_failed = False

    def fail_first_terminal_job(candidate: TaskJob) -> TaskJob:
        nonlocal terminal_job_write_failed
        if candidate.job_id == first_job.job_id and candidate.status == job_terminal and not terminal_job_write_failed:
            terminal_job_write_failed = True
            terminal_job_write_attempted.set()
            raise OSError("terminal job disk unavailable")
        return persist_job(candidate)

    monkeypatch.setattr(job_repository, "_persist", fail_first_terminal_job)
    runner._wake_event.clear()
    runner.start()
    try:
        assert terminal_job_write_attempted.wait(2)
        terminal_task = task_repository.load_compare_task(first_task_id)
        first_gap = runner.coordinator.load(first_job.job_id)
        assert (terminal_task.status, terminal_task.terminal_reason) == task_terminal
        assert first_gap.status == "RUNNING"

        with runner.coordinator._process_lock:
            expired = runner.coordinator.extend_lease(
                first_job.job_id,
                worker_id=first_gap.lease_owner,
                lease_seconds=-1,
            )
            assert expired is not None
            task_repository.save_compare_task(CompareTask(task_id=second_task_id))
            second_job = application.submit_compare(
                original_path=tmp_path / "original.pdf",
                compare_path=tmp_path / "compare.pdf",
                task_id=second_task_id,
                original_filename="original.pdf",
                compare_filename="compare.pdf",
            )

        wait_until(lambda: runner.coordinator.load(second_job.job_id).status == "SUCCEEDED")
        expired_gap = runner.coordinator.load(first_job.job_id)
        worker_survived = any(thread.is_alive() for thread in runner._threads)
    finally:
        runner.stop()

    assert worker_survived
    assert expired_gap.status == "RUNNING"
    assert expired_gap.error_code != "LEASE_EXPIRED_MAX_ATTEMPTS"

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


def test_concurrent_submission_failure_recovery_creates_only_one_first_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = build_runner(tmp_path, autostart=False)
    task_repository = LocalJsonTaskRepository(runner.settings)
    application = CompareTaskApplication(repository=task_repository, runner=runner)
    original_path = tmp_path / "original.pdf"
    compare_path = tmp_path / "compare.pdf"
    original_path.write_bytes(b"original")
    compare_path.write_bytes(b"compare")
    task_id = "TCONCURRENT_SUBMISSION_RECOVERY"
    task_repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            status="FAILED",
            terminal_reason="SUBMISSION_FAILED",
            original_pdf_path=str(original_path),
            compare_pdf_path=str(compare_path),
        )
    )
    initial_policy_barrier = threading.Barrier(2)
    call_counts: dict[int, int] = {}
    count_lock = threading.Lock()
    original_ensure = application._ensure_retry_eligible

    def synchronize_initial_policy(task: CompareTask):
        mode = original_ensure(task)
        thread_id = threading.get_ident()
        with count_lock:
            call_counts[thread_id] = call_counts.get(thread_id, 0) + 1
            first_call = call_counts[thread_id] == 1
        if first_call:
            initial_policy_barrier.wait(timeout=5)
        return mode

    monkeypatch.setattr(application, "_ensure_retry_eligible", synchronize_initial_policy)
    successes: list[TaskJob] = []
    conflicts: list[TaskTransitionConflict] = []

    def recover() -> None:
        try:
            successes.append(application.retry_compare(task_id))
        except TaskTransitionConflict as exc:
            conflicts.append(exc)

    threads = [threading.Thread(target=recover) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert [job.job_id for job in successes] == [f"compare:{task_id}:1"]
    assert len(conflicts) == 1
    assert [job.job_id for job in runner.jobs_for_task(task_id, task_type="compare")] == [f"compare:{task_id}:1"]
