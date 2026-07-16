from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.infrastructure.execution_state import ExecutionStateCoordinator
from app.infrastructure.reconciliation import reconcile_terminal_jobs
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
