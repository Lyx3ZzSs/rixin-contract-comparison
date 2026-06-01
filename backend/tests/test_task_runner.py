from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Mapping

from app.config import Settings
from app.infrastructure.task_runner import LocalJsonTaskJobRepository, QueuedTaskRunner


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
    assert stored.attempt == 2
    assert stored.last_error == ""


def test_queued_task_runner_cancels_queued_job(tmp_path: Path) -> None:
    runner = build_runner(tmp_path, autostart=False)
    executed = False

    def handler(payload: Mapping[str, Any]) -> None:
        nonlocal executed
        executed = True

    runner.register_handler("extraction", handler)
    job = runner.submit(task_type="extraction", task_id="TCANCEL", payload={"task_id": "TCANCEL"})
    cancelled = runner.cancel("TCANCEL", task_type="extraction")
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


def test_queued_task_runner_retries_failed_job_from_repository(tmp_path: Path) -> None:
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
    runner.stop()

    assert retried.status == "QUEUED"
    assert stored.status == "SUCCEEDED"
    assert stored.attempt == 1
    assert seen_payload == [{"task_id": "TRETRY_API"}]
