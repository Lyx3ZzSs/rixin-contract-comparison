from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.config import Settings, settings
from app.errors import ConflictError, NotFoundError

logger = logging.getLogger(__name__)

TaskJobType = Literal["compare"]
TaskJobStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCEL_REQUESTED", "CANCELLED"]
TERMINAL_JOB_STATUSES: set[TaskJobStatus] = {"SUCCEEDED", "FAILED", "CANCELLED"}


class TaskJob(BaseModel):
    job_id: str
    task_id: str
    task_type: TaskJobType
    status: TaskJobStatus = "QUEUED"
    payload: dict[str, Any] = Field(default_factory=dict)
    attempt: int = 0
    max_attempts: int = 1
    queued_at: str = Field(default_factory=lambda: _utc_now())
    started_at: str = ""
    finished_at: str = ""
    updated_at: str = Field(default_factory=lambda: _utc_now())
    next_run_at: str = ""
    lease_owner: str = ""
    lease_expires_at: str = ""
    last_error: str = ""


class TaskRunnerStats(BaseModel):
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancel_requested: int = 0
    cancelled: int = 0


TaskHandler = Callable[[Mapping[str, Any]], None]


class TaskJobRepository(Protocol):
    def enqueue(self, job: TaskJob) -> TaskJob:
        raise NotImplementedError

    def load(self, job_id: str) -> TaskJob:
        raise NotImplementedError

    def list_jobs(self) -> list[TaskJob]:
        raise NotImplementedError

    def claim_next(self, *, worker_id: str, lease_seconds: int) -> TaskJob | None:
        raise NotImplementedError

    def extend_lease(self, job_id: str, *, worker_id: str, lease_seconds: int) -> TaskJob | None:
        raise NotImplementedError

    def mark_succeeded(self, job_id: str, *, worker_id: str) -> TaskJob:
        raise NotImplementedError

    def mark_failed(self, job_id: str, *, worker_id: str, error: str, retry_delay_seconds: float) -> TaskJob:
        raise NotImplementedError

    def request_cancel(self, task_id: str, *, task_type: TaskJobType | None = None) -> list[TaskJob]:
        raise NotImplementedError


class LocalJsonTaskJobRepository:
    """Durable local task queue metadata stored beside each task."""

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings
        self._lock = threading.RLock()

    def enqueue(self, job: TaskJob) -> TaskJob:
        with self._lock:
            try:
                existing = self.load(job.job_id)
            except FileNotFoundError:
                existing = None
            if existing and existing.status not in TERMINAL_JOB_STATUSES:
                return existing
            job.status = "QUEUED"
            job.queued_at = _utc_now()
            job.updated_at = job.queued_at
            self._write_job(job)
            return job

    def load(self, job_id: str) -> TaskJob:
        path = self.job_path(job_id)
        if not path.exists():
            raise FileNotFoundError(f"任务执行记录不存在: {job_id}")
        with self._lock:
            return TaskJob(**json.loads(path.read_text(encoding="utf-8")))

    def list_jobs(self) -> list[TaskJob]:
        tasks_dir = self.settings.tasks_dir
        if not tasks_dir.exists():
            return []
        jobs: list[TaskJob] = []
        with self._lock:
            for path in tasks_dir.glob("*/job.json"):
                try:
                    jobs.append(TaskJob(**json.loads(path.read_text(encoding="utf-8"))))
                except (OSError, ValueError, TypeError):
                    continue
        return sorted(jobs, key=lambda job: (job.next_run_at or job.queued_at, job.queued_at))

    def claim_next(self, *, worker_id: str, lease_seconds: int) -> TaskJob | None:
        with self._lock:
            now = _utc_now()
            for job in self.list_jobs():
                if not self._is_claimable(job, now):
                    continue
                job.status = "RUNNING"
                job.attempt += 1
                job.started_at = now
                job.updated_at = now
                job.lease_owner = worker_id
                job.lease_expires_at = _plus_seconds(lease_seconds)
                job.last_error = ""
                self._write_job(job)
                return job
        return None

    def extend_lease(self, job_id: str, *, worker_id: str, lease_seconds: int) -> TaskJob | None:
        with self._lock:
            try:
                job = self.load(job_id)
            except FileNotFoundError:
                return None
            if job.status != "RUNNING" or job.lease_owner != worker_id:
                return None
            job.lease_expires_at = _plus_seconds(lease_seconds)
            job.updated_at = _utc_now()
            self._write_job(job)
            return job

    def mark_succeeded(self, job_id: str, *, worker_id: str) -> TaskJob:
        def mutate(job: TaskJob) -> None:
            if job.lease_owner != worker_id and job.status == "RUNNING":
                raise RuntimeError(f"执行记录 {job_id} 不属于当前 worker。")
            job.status = "SUCCEEDED"
            job.finished_at = _utc_now()
            job.updated_at = job.finished_at
            job.lease_owner = ""
            job.lease_expires_at = ""

        return self._update_job(job_id, mutate)

    def mark_failed(self, job_id: str, *, worker_id: str, error: str, retry_delay_seconds: float) -> TaskJob:
        def mutate(job: TaskJob) -> None:
            if job.lease_owner != worker_id and job.status == "RUNNING":
                raise RuntimeError(f"执行记录 {job_id} 不属于当前 worker。")
            job.last_error = error
            job.updated_at = _utc_now()
            job.lease_owner = ""
            job.lease_expires_at = ""
            if job.attempt < job.max_attempts:
                job.status = "QUEUED"
                job.next_run_at = _plus_seconds(retry_delay_seconds)
                return
            job.status = "FAILED"
            job.finished_at = job.updated_at

        return self._update_job(job_id, mutate)

    def request_cancel(self, task_id: str, *, task_type: TaskJobType | None = None) -> list[TaskJob]:
        updated: list[TaskJob] = []
        with self._lock:
            for job in self.list_jobs():
                if job.task_id != task_id or (task_type and job.task_type != task_type):
                    continue
                if job.status in TERMINAL_JOB_STATUSES:
                    updated.append(job)
                    continue
                if job.status == "QUEUED":
                    job.status = "CANCELLED"
                    job.finished_at = _utc_now()
                    job.lease_owner = ""
                    job.lease_expires_at = ""
                else:
                    job.status = "CANCEL_REQUESTED"
                job.updated_at = _utc_now()
                self._write_job(job)
                updated.append(job)
        return updated

    @property
    def job_dir(self) -> Path:
        return self.settings.tasks_dir

    def job_path(self, job_id: str) -> Path:
        task_id = job_id.split(":", 1)[1] if ":" in job_id else job_id
        return self._task_dir(task_id) / "job.json"

    def _is_claimable(self, job: TaskJob, now: str) -> bool:
        if job.status == "QUEUED":
            return not job.next_run_at or job.next_run_at <= now
        if job.status == "RUNNING":
            return bool(job.lease_expires_at and job.lease_expires_at <= now)
        return False

    def _update_job(self, job_id: str, mutate: Callable[[TaskJob], None]) -> TaskJob:
        with self._lock:
            job = self.load(job_id)
            mutate(job)
            self._write_job(job)
            return job

    def _write_job(self, job: TaskJob) -> None:
        path = self.job_path(job.job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(job.model_dump_json(indent=2), encoding="utf-8")
        temp_path.replace(path)
        self._write_manifest(job)

    def _task_dir(self, task_id: str) -> Path:
        safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in task_id)
        return self.settings.tasks_dir / (safe_name or "task")

    def _write_manifest(self, job: TaskJob) -> None:
        task_dir = self._task_dir(job.task_id)
        manifest_path = task_dir / "manifest.json"
        now = _utc_now()
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                manifest = {}
        else:
            manifest = {}
        artifacts = {
            str(item.get("path")): item
            for item in manifest.get("artifacts", [])
            if isinstance(item, dict) and item.get("path")
        }
        artifacts["job.json"] = {
            "path": "job.json",
            "area": "metadata",
            "kind": "json",
            "updated_at": now,
        }
        manifest.update(
            {
                "task_id": task_dir.name,
                "job_status": job.status,
                "updated_at": now,
                "artifacts": sorted(artifacts.values(), key=lambda item: str(item["path"])),
            }
        )
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


class LazyDefaultTaskJobRepository:
    """Defers task job repository initialization until runtime."""

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings
        self._lock = threading.RLock()
        self._repository: TaskJobRepository | None = None

    def resolve(self) -> TaskJobRepository:
        with self._lock:
            if self._repository is None:
                self._repository = build_task_job_repository(self.settings)
            return self._repository

    def enqueue(self, job: TaskJob) -> TaskJob:
        return self.resolve().enqueue(job)

    def load(self, job_id: str) -> TaskJob:
        return self.resolve().load(job_id)

    def list_jobs(self) -> list[TaskJob]:
        return self.resolve().list_jobs()

    def claim_next(self, *, worker_id: str, lease_seconds: int) -> TaskJob | None:
        return self.resolve().claim_next(worker_id=worker_id, lease_seconds=lease_seconds)

    def extend_lease(self, job_id: str, *, worker_id: str, lease_seconds: int) -> TaskJob | None:
        return self.resolve().extend_lease(job_id, worker_id=worker_id, lease_seconds=lease_seconds)

    def mark_succeeded(self, job_id: str, *, worker_id: str) -> TaskJob:
        return self.resolve().mark_succeeded(job_id, worker_id=worker_id)

    def mark_failed(self, job_id: str, *, worker_id: str, error: str, retry_delay_seconds: float) -> TaskJob:
        return self.resolve().mark_failed(
            job_id,
            worker_id=worker_id,
            error=error,
            retry_delay_seconds=retry_delay_seconds,
        )

    def request_cancel(self, task_id: str, *, task_type: TaskJobType | None = None) -> list[TaskJob]:
        return self.resolve().request_cancel(task_id, task_type=task_type)


def build_task_job_repository(app_settings: Settings = settings) -> TaskJobRepository:
    return LocalJsonTaskJobRepository(app_settings)


class QueuedTaskRunner:
    """Local durable queue runner with bounded worker threads."""

    def __init__(
        self,
        *,
        job_repository: TaskJobRepository | None = None,
        app_settings: Settings = settings,
        max_workers: int | None = None,
        max_attempts: int | None = None,
        lease_seconds: int | None = None,
        retry_delay_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
        autostart: bool = True,
    ) -> None:
        self.settings = app_settings
        self.job_repository = job_repository or LazyDefaultTaskJobRepository(app_settings)
        self.max_workers = max_workers or app_settings.task_runner_max_workers
        self.max_attempts = max_attempts or app_settings.task_runner_max_attempts
        self.lease_seconds = lease_seconds or app_settings.task_runner_lease_seconds
        self.retry_delay_seconds = (
            retry_delay_seconds
            if retry_delay_seconds is not None
            else app_settings.task_runner_retry_delay_seconds
        )
        self.poll_interval_seconds = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else app_settings.task_runner_poll_interval_seconds
        )
        self.autostart = autostart
        self._handlers: dict[TaskJobType, TaskHandler] = {}
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self._state_lock = threading.RLock()
        self._wake_event = threading.Event()

    def register_handler(self, task_type: TaskJobType, handler: TaskHandler) -> None:
        self._handlers[task_type] = handler

    def submit(
        self,
        *,
        task_type: TaskJobType,
        task_id: str,
        payload: Mapping[str, Any],
        max_attempts: int | None = None,
    ) -> TaskJob:
        if task_type not in self._handlers:
            raise RuntimeError(f"未注册任务执行器: {task_type}")
        job = TaskJob(
            job_id=f"{task_type}:{task_id}",
            task_id=task_id,
            task_type=task_type,
            payload=dict(payload),
            max_attempts=max_attempts or self.max_attempts,
        )
        queued = self.job_repository.enqueue(job)
        if self.autostart:
            self.start()
        self._wake_event.set()
        return queued

    def cancel(self, task_id: str, *, task_type: TaskJobType | None = None) -> list[TaskJob]:
        jobs = self.job_repository.request_cancel(task_id, task_type=task_type)
        self._wake_event.set()
        return jobs

    def jobs_for_task(self, task_id: str, *, task_type: TaskJobType | None = None) -> list[TaskJob]:
        jobs = [
            job
            for job in self.job_repository.list_jobs()
            if job.task_id == task_id and (task_type is None or job.task_type == task_type)
        ]
        return sorted(jobs, key=lambda job: job.updated_at or job.queued_at, reverse=True)

    def latest_job(self, task_id: str, *, task_type: TaskJobType | None = None) -> TaskJob:
        jobs = self.jobs_for_task(task_id, task_type=task_type)
        if not jobs:
            raise NotFoundError(f"任务执行记录不存在: {task_id}")
        return jobs[0]

    def retry(self, task_id: str, *, task_type: TaskJobType) -> TaskJob:
        job = self.latest_job(task_id, task_type=task_type)
        if job.status != "FAILED":
            raise ConflictError("只有执行失败的任务可以重试。")
        retried = job.model_copy(
            update={
                "status": "QUEUED",
                "attempt": 0,
                "queued_at": _utc_now(),
                "started_at": "",
                "finished_at": "",
                "updated_at": _utc_now(),
                "next_run_at": "",
                "lease_owner": "",
                "lease_expires_at": "",
                "last_error": "",
            }
        )
        queued = self.job_repository.enqueue(retried)
        if self.autostart:
            self.start()
        self._wake_event.set()
        return queued

    def stats(self) -> TaskRunnerStats:
        stats = TaskRunnerStats()
        for job in self.job_repository.list_jobs():
            if job.status == "QUEUED":
                stats.queued += 1
            elif job.status == "RUNNING":
                stats.running += 1
            elif job.status == "SUCCEEDED":
                stats.succeeded += 1
            elif job.status == "FAILED":
                stats.failed += 1
            elif job.status == "CANCEL_REQUESTED":
                stats.cancel_requested += 1
            elif job.status == "CANCELLED":
                stats.cancelled += 1
        return stats

    def start(self) -> None:
        with self._state_lock:
            resolve = getattr(self.job_repository, "resolve", None)
            if callable(resolve):
                resolve()
            self._threads = [thread for thread in self._threads if thread.is_alive()]
            if self._threads:
                return
            self._stop_event.clear()
            for index in range(self.max_workers):
                worker_id = f"{uuid.uuid4().hex[:12]}-{index}"
                thread = threading.Thread(
                    target=self._worker_loop,
                    args=(worker_id,),
                    name=f"task-runner-{index}",
                    daemon=True,
                )
                thread.start()
                self._threads.append(thread)

    def stop(self, *, wait: bool = True) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if not wait:
            return
        for thread in list(self._threads):
            thread.join()
        with self._state_lock:
            self._threads.clear()

    def _worker_loop(self, worker_id: str) -> None:
        while not self._stop_event.is_set():
            job = self.job_repository.claim_next(worker_id=worker_id, lease_seconds=self.lease_seconds)
            if job is None:
                self._wake_event.wait(self.poll_interval_seconds)
                self._wake_event.clear()
                continue
            self._run_job(job, worker_id)

    def _run_job(self, job: TaskJob, worker_id: str) -> None:
        handler = self._handlers.get(job.task_type)
        if handler is None:
            self.job_repository.mark_failed(
                job.job_id,
                worker_id=worker_id,
                error=f"未注册任务执行器: {job.task_type}",
                retry_delay_seconds=self.retry_delay_seconds,
            )
            return

        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(job.job_id, worker_id, heartbeat_stop),
            name=f"task-runner-heartbeat-{job.task_id}",
            daemon=True,
        )
        heartbeat.start()
        try:
            logger.info("Task job started: job_id=%s task_type=%s attempt=%s", job.job_id, job.task_type, job.attempt)
            handler(job.payload)
        except Exception as exc:
            logger.exception("Task job failed: job_id=%s", job.job_id)
            self.job_repository.mark_failed(
                job.job_id,
                worker_id=worker_id,
                error=str(exc),
                retry_delay_seconds=self.retry_delay_seconds,
            )
        else:
            self.job_repository.mark_succeeded(job.job_id, worker_id=worker_id)
            logger.info("Task job succeeded: job_id=%s", job.job_id)
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1)

    def _heartbeat_loop(self, job_id: str, worker_id: str, stop_event: threading.Event) -> None:
        interval = max(1.0, self.lease_seconds / 3)
        while not stop_event.wait(interval):
            self.job_repository.extend_lease(job_id, worker_id=worker_id, lease_seconds=self.lease_seconds)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _plus_seconds(seconds: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


default_task_runner = QueuedTaskRunner()
