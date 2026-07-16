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
from app.errors import (
    ConflictError,
    NotFoundError,
    TaskCancelled,
    TaskStaleLeaseError,
    TaskTransitionConflict,
)
from app.infrastructure.execution_state import (
    CancellationToken,
    ExecutionStateCoordinator,
    TaskExecutionContext,
)
from app.models import CompareTask

logger = logging.getLogger(__name__)

TaskJobType = Literal["compare"]
TaskJobStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCEL_REQUESTED", "CANCELLED"]
TERMINAL_JOB_STATUSES: set[TaskJobStatus] = {"SUCCEEDED", "FAILED", "CANCELLED"}


class TaskJob(BaseModel):
    job_id: str
    task_id: str
    task_type: TaskJobType
    status: TaskJobStatus = "QUEUED"
    execution_no: int = 1
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
    error_code: str = ""
    last_error: str = ""
    source_path: str = Field(default="", exclude=True, repr=False)


class TaskRunnerStats(BaseModel):
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancel_requested: int = 0
    cancelled: int = 0


TaskHandler = Callable[[TaskExecutionContext, Mapping[str, Any]], CompareTask | None]


class TaskJobRepository(Protocol):
    def enqueue(self, job: TaskJob) -> TaskJob:
        raise NotImplementedError

    def load(self, job_id: str) -> TaskJob:
        raise NotImplementedError

    def list_jobs(self) -> list[TaskJob]:
        raise NotImplementedError

    def persist(self, job: TaskJob) -> TaskJob:
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
            existing_jobs = self.list_jobs()
            job_id_matches = [existing for existing in existing_jobs if existing.job_id == job.job_id]
            if job_id_matches:
                if len(job_id_matches) != 1 or not self._has_same_identity(job_id_matches[0], job):
                    raise TaskTransitionConflict(f"执行记录 ID {job.job_id} 与已有执行身份冲突。")
                existing = job_id_matches[0]
                if existing.status in TERMINAL_JOB_STATUSES:
                    raise TaskTransitionConflict(
                        f"执行记录 {existing.job_id} 已为终态 {existing.status}，不能重新入队。"
                    )
                return existing
            execution_matches = [
                existing for existing in existing_jobs if self._has_same_execution_identity(existing, job)
            ]
            if execution_matches:
                raise TaskTransitionConflict(
                    f"任务 {job.task_id} 的第 {job.execution_no} 次 {job.task_type} 执行记录已存在，不能重复创建。"
                )
            target_path = self._new_job_path(job)
            if target_path.exists():
                raise TaskTransitionConflict(f"任务 {job.task_id} 的第 {job.execution_no} 次执行记录已存在，不能覆盖。")
            active_jobs = [
                existing_job
                for existing_job in existing_jobs
                if existing_job.task_id == job.task_id
                and existing_job.task_type == job.task_type
                and existing_job.status not in TERMINAL_JOB_STATUSES
            ]
            if active_jobs:
                raise TaskTransitionConflict(f"任务 {job.task_id} 已有活动执行记录 {active_jobs[0].job_id}。")
            job = job.model_copy(deep=True)
            job.status = "QUEUED"
            job.queued_at = _utc_now()
            job.updated_at = job.queued_at
            job.source_path = str(target_path)
            self._write_job(job)
            return job

    def load(self, job_id: str) -> TaskJob:
        path = self._find_job_path(job_id)
        if path is None:
            raise FileNotFoundError(f"任务执行记录不存在: {job_id}")
        with self._lock:
            return self._load_job_path(path)

    def list_jobs(self) -> list[TaskJob]:
        tasks_dir = self.settings.tasks_dir
        if not tasks_dir.exists():
            return []
        jobs: list[TaskJob] = []
        with self._lock:
            paths = [*tasks_dir.glob("*/job.json"), *tasks_dir.glob("*/jobs/*.json")]
            for path in paths:
                try:
                    jobs.append(self._load_job_path(path))
                except (OSError, ValueError, TypeError):
                    continue
        return sorted(jobs, key=lambda job: (job.next_run_at or job.queued_at, job.queued_at))

    def persist(self, job: TaskJob) -> TaskJob:
        with self._lock:
            persisted = job.model_copy(deep=True)
            self._write_job(persisted)
            return persisted

    def claim_next(self, *, worker_id: str, lease_seconds: int) -> TaskJob | None:
        with self._lock:
            now = _utc_now()
            for job in self.list_jobs():
                if self._is_expired_at_attempt_limit(job, now):
                    job.status = "FAILED"
                    job.error_code = "LEASE_EXPIRED_MAX_ATTEMPTS"
                    job.last_error = "任务租约过期且已达到最大尝试次数。"
                    job.finished_at = now
                    job.updated_at = now
                    job.lease_owner = ""
                    job.lease_expires_at = ""
                    self._write_job(job)
                    continue
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
            self._ensure_terminal_write_allowed(job, "SUCCEEDED")
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
            self._ensure_terminal_write_allowed(job, "FAILED")
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
        with self._lock:
            jobs = [
                job
                for job in self.list_jobs()
                if job.task_id == task_id and (task_type is None or job.task_type == task_type)
            ]
            if not jobs:
                return []
            active_jobs = [job for job in jobs if job.status not in TERMINAL_JOB_STATUSES]
            job = max(active_jobs or jobs, key=lambda item: item.execution_no)
            if job.status in TERMINAL_JOB_STATUSES:
                return [job]
            if job.status == "QUEUED":
                job.status = "CANCELLED"
                job.finished_at = _utc_now()
                job.lease_owner = ""
                job.lease_expires_at = ""
            else:
                job.status = "CANCEL_REQUESTED"
            job.updated_at = _utc_now()
            self._write_job(job)
            return [job]

    @property
    def job_dir(self) -> Path:
        return self.settings.tasks_dir

    def job_path(self, job_id: str) -> Path:
        existing = self._find_job_path(job_id)
        if existing is not None:
            return existing
        task_id, execution_no = self._job_identity_parts(job_id)
        return self._task_dir(task_id) / "jobs" / f"{execution_no}.json"

    def _is_claimable(self, job: TaskJob, now: str) -> bool:
        if job.status == "QUEUED":
            return not job.next_run_at or job.next_run_at <= now
        if job.status == "RUNNING":
            return bool(job.lease_expires_at and job.lease_expires_at <= now)
        return False

    def _is_expired_at_attempt_limit(self, job: TaskJob, now: str) -> bool:
        return bool(
            job.status == "RUNNING"
            and job.lease_expires_at
            and job.lease_expires_at <= now
            and job.attempt >= job.max_attempts
        )

    def _update_job(self, job_id: str, mutate: Callable[[TaskJob], None]) -> TaskJob:
        with self._lock:
            job = self.load(job_id)
            mutate(job)
            self._write_job(job)
            return job

    def _ensure_terminal_write_allowed(self, job: TaskJob, target_status: TaskJobStatus) -> None:
        if job.status not in TERMINAL_JOB_STATUSES:
            return
        raise TaskTransitionConflict(f"执行记录 {job.job_id} 已为终态 {job.status}，不能改写为 {target_status}。")

    def _write_job(self, job: TaskJob) -> None:
        path = Path(job.source_path) if job.source_path else self._new_job_path(job)
        job.source_path = str(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(job.model_dump_json(indent=2), encoding="utf-8")
        temp_path.replace(path)
        self._write_manifest(job, path)

    def _load_job_path(self, path: Path) -> TaskJob:
        job = TaskJob(**json.loads(path.read_text(encoding="utf-8")))
        job.source_path = str(path)
        return job

    def _find_job_path(self, job_id: str) -> Path | None:
        if not self.settings.tasks_dir.exists():
            return None
        paths = [
            *self.settings.tasks_dir.glob("*/job.json"),
            *self.settings.tasks_dir.glob("*/jobs/*.json"),
        ]
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if payload.get("job_id") == job_id:
                return path
        return None

    def _new_job_path(self, job: TaskJob) -> Path:
        return self._task_dir(job.task_id) / "jobs" / f"{job.execution_no}.json"

    def _has_same_identity(self, existing: TaskJob, candidate: TaskJob) -> bool:
        return bool(existing.job_id == candidate.job_id and self._has_same_execution_identity(existing, candidate))

    def _has_same_execution_identity(self, existing: TaskJob, candidate: TaskJob) -> bool:
        return bool(
            existing.task_id == candidate.task_id
            and existing.task_type == candidate.task_type
            and existing.execution_no == candidate.execution_no
        )

    def _job_identity_parts(self, job_id: str) -> tuple[str, int]:
        identity = job_id.split(":", 1)[1] if ":" in job_id else job_id
        task_id, separator, execution = identity.rpartition(":")
        if separator:
            try:
                return task_id, int(execution)
            except ValueError:
                pass
        return identity, 1

    def _task_dir(self, task_id: str) -> Path:
        safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in task_id)
        return self.settings.tasks_dir / (safe_name or "task")

    def _write_manifest(self, job: TaskJob, job_path: Path) -> None:
        task_dir = self._task_dir(job.task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
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
        artifact_path = job_path.relative_to(task_dir).as_posix()
        artifacts[artifact_path] = {
            "path": artifact_path,
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

    @property
    def job_dir(self) -> Path:
        return self.settings.tasks_dir

    def enqueue(self, job: TaskJob) -> TaskJob:
        return self.resolve().enqueue(job)

    def load(self, job_id: str) -> TaskJob:
        return self.resolve().load(job_id)

    def list_jobs(self) -> list[TaskJob]:
        return self.resolve().list_jobs()

    def persist(self, job: TaskJob) -> TaskJob:
        return self.resolve().persist(job)

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
        persistence = job_repository or LazyDefaultTaskJobRepository(app_settings)
        self.coordinator = (
            persistence if isinstance(persistence, ExecutionStateCoordinator) else ExecutionStateCoordinator(persistence)
        )
        self.job_repository: ExecutionStateCoordinator = self.coordinator
        self.max_workers = max_workers or app_settings.task_runner_max_workers
        self.max_attempts = max_attempts or app_settings.task_runner_max_attempts
        self.lease_seconds = lease_seconds or app_settings.task_runner_lease_seconds
        self.retry_delay_seconds = (
            retry_delay_seconds if retry_delay_seconds is not None else app_settings.task_runner_retry_delay_seconds
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
            job_id=f"{task_type}:{task_id}:1",
            task_id=task_id,
            task_type=task_type,
            execution_no=1,
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
        return sorted(
            jobs,
            key=lambda job: (job.execution_no, job.updated_at or job.queued_at),
            reverse=True,
        )

    def latest_job(self, task_id: str, *, task_type: TaskJobType | None = None) -> TaskJob:
        jobs = self.jobs_for_task(task_id, task_type=task_type)
        if not jobs:
            raise NotFoundError(f"任务执行记录不存在: {task_id}")
        return jobs[0]

    def retry(self, task_id: str, *, task_type: TaskJobType) -> TaskJob:
        jobs = self.jobs_for_task(task_id, task_type=task_type)
        if not jobs:
            raise NotFoundError(f"任务执行记录不存在: {task_id}")
        if any(job.status not in TERMINAL_JOB_STATUSES for job in jobs):
            raise ConflictError("任务已有活动执行，不能重试。")
        job = max(jobs, key=lambda item: item.execution_no)
        if job.status != "FAILED":
            raise ConflictError("只有执行失败的任务可以重试。")
        execution_no = max(existing.execution_no for existing in jobs) + 1
        retried = TaskJob(
            job_id=f"{task_type}:{task_id}:{execution_no}",
            task_id=task_id,
            task_type=task_type,
            execution_no=execution_no,
            payload=dict(job.payload),
            max_attempts=job.max_attempts,
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
            self._mark_job_failed(job, worker_id, RuntimeError(f"未注册任务执行器: {job.task_type}"))
            return

        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(job.job_id, worker_id, heartbeat_stop),
            name=f"task-runner-heartbeat-{job.task_id}",
            daemon=True,
        )
        heartbeat.start()
        token = CancellationToken(job_id=job.job_id, worker_id=worker_id, coordinator=self.coordinator)
        context = TaskExecutionContext(
            job_id=job.job_id,
            task_id=job.task_id,
            worker_id=worker_id,
            cancellation_token=token,
        )
        try:
            logger.info("Task job started: job_id=%s task_type=%s attempt=%s", job.job_id, job.task_type, job.attempt)
            token.raise_if_cancelled()
            handler(context, job.payload)
            token.raise_if_cancelled()
            self.coordinator.mark_succeeded(job.job_id, worker_id=worker_id)
        except TaskCancelled:
            self._mark_job_cancelled(job, worker_id)
        except TaskStaleLeaseError:
            logger.warning("Stale task job lease; worker stopping: job_id=%s worker_id=%s", job.job_id, worker_id)
        except Exception as exc:
            logger.exception("Task job failed: job_id=%s", job.job_id)
            self._mark_job_failed(job, worker_id, exc)
        else:
            logger.info("Task job succeeded: job_id=%s", job.job_id)
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1)

    def _mark_job_cancelled(self, job: TaskJob, worker_id: str) -> None:
        try:
            self.coordinator.mark_cancelled(job.job_id, worker_id=worker_id)
        except TaskStaleLeaseError:
            logger.warning("Stale task job cancellation; worker stopping: job_id=%s worker_id=%s", job.job_id, worker_id)

    def _mark_job_failed(self, job: TaskJob, worker_id: str, exc: Exception) -> None:
        try:
            self.coordinator.mark_failed(
                job.job_id,
                worker_id=worker_id,
                error=str(exc),
                retry_delay_seconds=self.retry_delay_seconds,
            )
        except TaskCancelled:
            self._mark_job_cancelled(job, worker_id)
        except TaskStaleLeaseError:
            logger.warning("Stale task job failure; worker stopping: job_id=%s worker_id=%s", job.job_id, worker_id)

    def _heartbeat_loop(self, job_id: str, worker_id: str, stop_event: threading.Event) -> None:
        interval = max(1.0, self.lease_seconds / 3)
        while not stop_event.wait(interval):
            try:
                extended = self.coordinator.extend_lease(
                    job_id,
                    worker_id=worker_id,
                    lease_seconds=self.lease_seconds,
                )
            except TaskStaleLeaseError:
                logger.warning("Task heartbeat lost lease: job_id=%s worker_id=%s", job_id, worker_id)
                return
            if extended is None:
                return


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _plus_seconds(seconds: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


default_task_runner = QueuedTaskRunner()
