from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.infrastructure.execution_state import ExecutionStateCoordinator
from app.infrastructure import reconciliation
from app.infrastructure.reconciliation import reconcile_terminal_jobs
from app.infrastructure.recovery_store import RecoveryStore
from app.infrastructure.task_repository import LocalJsonTaskRepository
from app.infrastructure.task_runner import LocalJsonTaskJobRepository, TaskJob
from app.models import CompareTask


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[object] = []

    def publish(self, event: object) -> None:
        self.events.append(event)


def test_startup_reconciliation_repairs_task_terminal_job_gap_without_task_change_or_republish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    task_repository = LocalJsonTaskRepository(app_settings)
    job_repository = LocalJsonTaskJobRepository(app_settings)
    publisher = RecordingPublisher()
    coordinator = ExecutionStateCoordinator(
        job_repository,
        task_repository=task_repository,
        progress_publisher=publisher,
    )
    job = coordinator.enqueue(
        TaskJob(
            job_id="compare:TRECOVERY:1",
            task_id="TRECOVERY",
            task_type="compare",
            execution_no=1,
            max_attempts=1,
        )
    )
    task_repository.save_compare_task(CompareTask(task_id=job.task_id, active_job_id=job.job_id))
    claimed = coordinator.claim_next(worker_id="worker-1", lease_seconds=30)
    assert claimed is not None
    persist_job = job_repository._persist

    def fail_terminal_job(candidate: TaskJob) -> TaskJob:
        if candidate.status == "SUCCEEDED":
            raise OSError("job disk unavailable")
        return persist_job(candidate)

    monkeypatch.setattr(job_repository, "_persist", fail_terminal_job)
    with pytest.raises(OSError, match="job disk unavailable"):
        coordinator.commit_success(
            job.job_id,
            worker_id="worker-1",
            result=CompareTask(task_id=job.task_id),
        )

    terminal_before = task_repository.load_compare_task(job.task_id)
    assert terminal_before.status == "COMPLETED"
    assert job_repository.load(job.job_id).status == "RUNNING"
    assert publisher.events == []

    monkeypatch.setattr(job_repository, "_persist", persist_job)
    restarted = ExecutionStateCoordinator(
        job_repository,
        task_repository=task_repository,
        progress_publisher=publisher,
    )
    repaired = reconcile_terminal_jobs(task_repository, restarted)
    terminal_after = task_repository.load_compare_task(job.task_id)

    assert repaired == 1
    assert restarted.load(job.job_id).status == "SUCCEEDED"
    assert terminal_after == terminal_before
    assert publisher.events == []
    assert reconcile_terminal_jobs(task_repository, restarted) == 0


@pytest.mark.parametrize(
    ("terminal_reason", "expected_job_status", "expected_error"),
    [
        ("EXECUTION_FAILED", "FAILED", "handler failed"),
        ("CANCELLED", "CANCELLED", ""),
    ],
)
def test_startup_reconciliation_maps_failed_task_reason_to_job_terminal(
    tmp_path: Path,
    terminal_reason: str,
    expected_job_status: str,
    expected_error: str,
) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    task_repository = LocalJsonTaskRepository(app_settings)
    job_repository = LocalJsonTaskJobRepository(app_settings)
    publisher = RecordingPublisher()
    coordinator = ExecutionStateCoordinator(
        job_repository,
        task_repository=task_repository,
        progress_publisher=publisher,
    )
    task_id = f"TRECONCILE_{terminal_reason}"
    job = coordinator.enqueue(
        TaskJob(
            job_id=f"compare:{task_id}:1",
            task_id=task_id,
            task_type="compare",
            execution_no=1,
            max_attempts=1,
        )
    )
    task_repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            status="FAILED",
            terminal_reason=terminal_reason,
            active_job_id=job.job_id,
            terminal_job_id=job.job_id,
            terminal_attempt=job.attempt,
            errors=["handler failed" if terminal_reason == "EXECUTION_FAILED" else "任务已取消。"],
        )
    )

    assert reconcile_terminal_jobs(task_repository, coordinator) == 1

    repaired = coordinator.load(job.job_id)
    assert repaired.status == expected_job_status
    assert repaired.last_error == expected_error
    assert publisher.events == []


def _reconciliation_dependencies(tmp_path: Path):
    app_settings = Settings(storage_dir=tmp_path / "storage")
    task_repository = LocalJsonTaskRepository(app_settings)
    job_repository = LocalJsonTaskJobRepository(app_settings)
    coordinator = ExecutionStateCoordinator(job_repository, task_repository=task_repository)
    return task_repository, coordinator, RecoveryStore(app_settings)


def test_startup_reconciliation_completes_matching_terminal_job(tmp_path: Path) -> None:
    task_repository, coordinator, recovery_store = _reconciliation_dependencies(tmp_path)
    task_id = "TTERMINAL"
    job = coordinator.enqueue(TaskJob(job_id=f"compare:{task_id}:1", task_id=task_id, task_type="compare"))
    task_repository.save_compare_task(
        CompareTask(
            task_id=task_id,
            status="COMPLETED",
            active_job_id=job.job_id,
            terminal_job_id=job.job_id,
            terminal_attempt=job.attempt,
        )
    )

    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store) == 1
    assert coordinator.load(job.job_id).status == "SUCCEEDED"
    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store) == 0


def test_startup_reconciliation_fails_unreferenced_nonterminal_job(tmp_path: Path) -> None:
    task_repository, coordinator, recovery_store = _reconciliation_dependencies(tmp_path)
    task_id = "TORPHAN"
    task_repository.save_compare_task(
        CompareTask(task_id=task_id, status="FAILED", terminal_reason="SUBMISSION_FAILED")
    )
    job = coordinator.enqueue(TaskJob(job_id=f"compare:{task_id}:1", task_id=task_id, task_type="compare"))

    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store) == 1
    repaired = coordinator.load(job.job_id)
    assert (repaired.status, repaired.error_code) == ("FAILED", "ORPHANED_JOB")
    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store) == 0


def test_startup_reconciliation_fails_processing_task_with_missing_active_job(tmp_path: Path) -> None:
    task_repository, coordinator, recovery_store = _reconciliation_dependencies(tmp_path)
    task_id = "TMISSING"
    task_repository.save_compare_task(CompareTask(task_id=task_id, active_job_id=f"compare:{task_id}:1"))

    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store) == 1
    repaired = task_repository.load_compare_task(task_id)
    assert (repaired.status, repaired.terminal_reason) == ("FAILED", "SUBMISSION_FAILED")
    assert recovery_store.load_marker(task_id).primary_error == "活动执行记录缺失或身份不匹配。"
    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store) == 0


def test_startup_reconciliation_requeues_prestart_running_job_with_attempts_remaining(tmp_path: Path) -> None:
    task_repository, coordinator, recovery_store = _reconciliation_dependencies(tmp_path)
    task_id = "TSTALE_RETRY"
    task_repository.save_compare_task(CompareTask(task_id=task_id, active_job_id=f"compare:{task_id}:1"))
    job = coordinator.enqueue(
        TaskJob(job_id=f"compare:{task_id}:1", task_id=task_id, task_type="compare", max_attempts=2)
    )
    running = coordinator.claim_next(worker_id="old-worker", lease_seconds=3600)
    assert running is not None

    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store) == 1
    repaired = coordinator.load(job.job_id)
    assert (repaired.status, repaired.attempt, repaired.lease_owner, repaired.lease_expires_at) == (
        "QUEUED",
        1,
        "",
        "",
    )
    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store) == 0


def test_startup_reconciliation_fails_prestart_running_job_at_attempt_limit(tmp_path: Path) -> None:
    task_repository, coordinator, recovery_store = _reconciliation_dependencies(tmp_path)
    task_id = "TSTALE_MAX"
    task_repository.save_compare_task(CompareTask(task_id=task_id, active_job_id=f"compare:{task_id}:1"))
    job = coordinator.enqueue(
        TaskJob(job_id=f"compare:{task_id}:1", task_id=task_id, task_type="compare", max_attempts=1)
    )
    assert coordinator.claim_next(worker_id="old-worker", lease_seconds=3600) is not None

    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store) == 1
    assert (coordinator.load(job.job_id).status, coordinator.load(job.job_id).error_code) == (
        "FAILED",
        "PROCESS_RESTART_MAX_ATTEMPTS",
    )
    assert (task_repository.load_compare_task(task_id).status, task_repository.load_compare_task(task_id).terminal_reason) == (
        "FAILED",
        "EXECUTION_FAILED",
    )
    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store) == 0


def test_runtime_reconciliation_keeps_valid_running_lease_unchanged(tmp_path: Path) -> None:
    task_repository, coordinator, recovery_store = _reconciliation_dependencies(tmp_path)
    task_id = "TRUNTIME"
    task_repository.save_compare_task(CompareTask(task_id=task_id, active_job_id=f"compare:{task_id}:1"))
    job = coordinator.enqueue(TaskJob(job_id=f"compare:{task_id}:1", task_id=task_id, task_type="compare", max_attempts=2))
    running = coordinator.claim_next(worker_id="live-worker", lease_seconds=3600)
    assert running is not None

    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store, pre_start=False) == 0
    assert coordinator.load(job.job_id) == running
    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store, pre_start=False) == 0


def test_startup_reconciliation_deduplicates_agreeing_legacy_and_new_execution(tmp_path: Path) -> None:
    task_repository, coordinator, recovery_store = _reconciliation_dependencies(tmp_path)
    task_id = "TLEGACY_AGREE"
    task_repository.save_compare_task(CompareTask(task_id=task_id, active_job_id=f"compare:{task_id}:1"))
    legacy = TaskJob(job_id=f"compare:{task_id}:1", task_id=task_id, task_type="compare")
    legacy_path = tmp_path / "storage" / "tasks" / task_id / "job.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(legacy.model_dump_json(indent=2), encoding="utf-8")
    new_path = legacy_path.parent / "jobs" / "1.json"
    new_path.parent.mkdir()
    new_path.write_text(legacy.model_dump_json(indent=2), encoding="utf-8")

    coordinator.reload_from_storage()
    assert len(coordinator.list_jobs()) == 1
    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store) == 0
    persisted = coordinator.load(legacy.job_id)
    assert persisted.source_path == str(legacy_path)
    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store) == 0


def test_startup_reconciliation_marks_conflicting_duplicate_execution_unclaimable(tmp_path: Path) -> None:
    task_repository, coordinator, recovery_store = _reconciliation_dependencies(tmp_path)
    task_id = "TLEGACY_CONFLICT"
    task_repository.save_compare_task(CompareTask(task_id=task_id, active_job_id=f"compare:{task_id}:1"))
    legacy = TaskJob(job_id=f"compare:{task_id}:1", task_id=task_id, task_type="compare")
    legacy_path = tmp_path / "storage" / "tasks" / task_id / "job.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(legacy.model_dump_json(indent=2), encoding="utf-8")
    conflicting = legacy.model_copy(update={"status": "RUNNING", "attempt": 1, "lease_owner": "old"})
    new_path = legacy_path.parent / "jobs" / "1.json"
    new_path.parent.mkdir()
    new_path.write_text(conflicting.model_dump_json(indent=2), encoding="utf-8")

    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store) >= 1
    assert coordinator.claim_next(worker_id="worker", lease_seconds=30) is None
    assert all(job.error_code == "DUPLICATE_JOB_EXECUTION" for job in coordinator.list_jobs())
    assert reconciliation.reconcile_startup(task_repository, coordinator, recovery_store=recovery_store) == 0
