from __future__ import annotations

import time
from pathlib import Path

from app.config import Settings
from app.infrastructure.task_repository import SQLiteTaskRepository
from app.infrastructure.task_runner import QueuedTaskRunner
from app.models import CompareTask


def _wait_for(repo: SQLiteTaskRepository, task_id: str, status: str) -> CompareTask:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        task = repo.load_compare_task(task_id)
        if task.execution_status == status:
            return task
        time.sleep(0.01)
    raise AssertionError(f"task did not reach {status}")


def test_process_local_runner_persists_only_current_execution(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", task_runner_max_workers=1)
    repo = SQLiteTaskRepository(settings)
    repo.save_compare_task(CompareTask(task_id="T1"))
    runner = QueuedTaskRunner(repository=repo, app_settings=settings, max_workers=1)
    runner.register_handler("compare", lambda _ctx, _payload: repo.load_compare_task("T1"))

    job = runner.submit(task_type="compare", task_id="T1", payload={})
    task = _wait_for(repo, "T1", "SUCCEEDED")
    runner.stop()

    assert task.status == "COMPLETED"
    assert task.execution_id == job.job_id
    assert not list(settings.tasks_dir.glob("*/jobs/*.json"))


def test_runner_failure_is_traceable_in_task_and_logs_not_job_files(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", task_runner_max_workers=1)
    repo = SQLiteTaskRepository(settings)
    repo.save_compare_task(CompareTask(task_id="T1"))
    runner = QueuedTaskRunner(repository=repo, app_settings=settings, max_workers=1)

    def fail(_ctx, _payload):
        raise RuntimeError("boom")

    runner.register_handler("compare", fail)
    runner.submit(task_type="compare", task_id="T1", payload={})
    task = _wait_for(repo, "T1", "FAILED")
    runner.stop()

    assert task.terminal_reason == "EXECUTION_FAILED"
    assert task.execution_last_error == "boom"


def test_queued_cancellation_is_cooperative(tmp_path: Path) -> None:
    settings = Settings(storage_dir=tmp_path / "storage", task_runner_max_workers=1)
    repo = SQLiteTaskRepository(settings)
    repo.save_compare_task(CompareTask(task_id="T1"))
    runner = QueuedTaskRunner(repository=repo, app_settings=settings, max_workers=1, autostart=False)
    runner.register_handler("compare", lambda _ctx, _payload: repo.load_compare_task("T1"))
    runner.submit(task_type="compare", task_id="T1", payload={})

    assert runner.cancel_active_task("T1").status == "CANCEL_REQUESTED"
    runner.start()
    task = _wait_for(repo, "T1", "CANCELLED")
    runner.stop()

    assert task.status == "FAILED"
    assert task.terminal_reason == "CANCELLED"
