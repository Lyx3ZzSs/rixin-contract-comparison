from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from app.config import Settings, settings
from app.errors import (
    ConflictError,
    NotFoundError,
    TaskCancelled,
    TaskRepositoryReadError,
    TaskStaleLeaseError,
    TaskTransitionConflict,
)
from app.infrastructure.atomic_files import atomic_write_json, update_task_manifest
from app.infrastructure.execution_state import (
    CancellationToken,
    ExecutionStateCoordinator,
    TaskEnqueueMutation,
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
    duplicate_execution: bool = Field(default=False, exclude=True, repr=False)


class TaskRunnerStats(BaseModel):
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancel_requested: int = 0
    cancelled: int = 0


TaskHandler = Callable[[TaskExecutionContext, Mapping[str, Any]], CompareTask | None]


class TaskJobRepository(Protocol):
    @property
    def job_dir(self) -> Path:
        raise NotImplementedError

    def load(self, job_id: str) -> TaskJob:
        raise NotImplementedError

    def list_jobs(self) -> list[TaskJob]:
        raise NotImplementedError

    def _persist(self, job: TaskJob) -> TaskJob:
        raise NotImplementedError


class LocalJsonTaskJobRepository:
    """Durable local task queue metadata stored beside each task."""

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings
        self._lock = threading.RLock()

    def load(self, job_id: str) -> TaskJob:
        matches = [job for job in self.list_jobs() if job.job_id == job_id]
        if not matches:
            raise FileNotFoundError(f"任务执行记录不存在: {job_id}")
        return matches[0].model_copy(deep=True)

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
        return self._merge_execution_view(jobs)

    def _persist(self, job: TaskJob) -> TaskJob:
        with self._lock:
            persisted = job.model_copy(deep=True)
            if not persisted.source_path and self._new_job_path(persisted).exists():
                raise TaskTransitionConflict(
                    f"任务 {persisted.task_id} 的第 {persisted.execution_no} 次执行记录已存在，不能覆盖。"
                )
            self._write_job(persisted)
            return persisted

    @property
    def job_dir(self) -> Path:
        return self.settings.tasks_dir

    def _write_job(self, job: TaskJob) -> None:
        path = Path(job.source_path) if job.source_path else self._new_job_path(job)
        job.source_path = str(path)
        # A legacy job.json may coexist with jobs/1.json for the same execution.
        # Keep agreeing physical copies in sync before refreshing the derived manifest.
        for copy_path in self._agreeing_execution_paths(job, path):
            copy_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(copy_path, job.model_dump(mode="json"))
        try:
            self._write_manifest(job, path)
        except OSError:
            logger.warning(
                "Task Job manifest refresh failed after authoritative Job commit: job_id=%s",
                job.job_id,
                exc_info=True,
            )

    def _load_job_path(self, path: Path) -> TaskJob:
        job = TaskJob(**json.loads(path.read_text(encoding="utf-8")))
        job.source_path = str(path)
        return job

    def _agreeing_execution_paths(self, job: TaskJob, primary_path: Path) -> list[Path]:
        candidates = [
            self._task_dir(job.task_id) / "job.json",
            self._new_job_path(job),
        ]
        copies: list[tuple[Path, TaskJob]] = []
        for candidate_path in candidates:
            if not candidate_path.exists():
                continue
            try:
                candidate = self._load_job_path(candidate_path)
            except (OSError, ValueError, TypeError):
                continue
            if self._same_execution(candidate, job):
                copies.append((candidate_path, candidate))

        if len(copies) < 2:
            return [primary_path]
        baseline = copies[0][1].model_dump(mode="json")
        if any(copy.model_dump(mode="json") != baseline for _, copy in copies[1:]):
            return [primary_path]
        return [path for path, _ in copies]

    @staticmethod
    def _same_execution(existing: TaskJob, candidate: TaskJob) -> bool:
        return bool(
            existing.job_id == candidate.job_id
            and existing.task_id == candidate.task_id
            and existing.task_type == candidate.task_type
            and existing.execution_no == candidate.execution_no
        )

    def _find_job_path(self, job_id: str) -> Path | None:
        match = next((job for job in self.list_jobs() if job.job_id == job_id), None)
        return Path(match.source_path) if match is not None else None

    @staticmethod
    def _merge_execution_view(jobs: list[TaskJob]) -> list[TaskJob]:
        grouped: dict[tuple[str, str, int], list[TaskJob]] = {}
        for job in jobs:
            grouped.setdefault((job.task_id, job.task_type, job.execution_no), []).append(job)

        merged: list[TaskJob] = []
        for duplicates in grouped.values():
            if len(duplicates) == 1:
                merged.append(duplicates[0])
                continue
            identities = {(job.job_id, job.status, job.attempt) for job in duplicates}
            if len(identities) == 1:
                # Keep the legacy source for execution 1 so subsequent repairs
                # update it in place; retries still use jobs/{execution_no}.json.
                merged.append(min(duplicates, key=lambda job: (Path(job.source_path).name != "job.json", job.source_path)))
                continue
            for job in duplicates:
                job.error_code = "DUPLICATE_JOB_EXECUTION"
                job.duplicate_execution = True
                merged.append(job)
        return sorted(merged, key=lambda job: (job.next_run_at or job.queued_at, job.queued_at, job.source_path))

    def _new_job_path(self, job: TaskJob) -> Path:
        return self._task_dir(job.task_id) / "jobs" / f"{job.execution_no}.json"

    def _task_dir(self, task_id: str) -> Path:
        safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in task_id)
        return self.settings.tasks_dir / (safe_name or "task")

    def _write_manifest(self, job: TaskJob, job_path: Path) -> None:
        task_dir = self._task_dir(job.task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        now = _utc_now()
        artifact_path = job_path.relative_to(task_dir).as_posix()
        update_task_manifest(
            task_dir / "manifest.json",
            task_id=task_dir.name,
            fields={"job_status": job.status},
            artifact={
                "path": artifact_path,
                "area": "metadata",
                "kind": "json",
                "updated_at": now,
            },
        )


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

    def load(self, job_id: str) -> TaskJob:
        return self.resolve().load(job_id)

    def list_jobs(self) -> list[TaskJob]:
        return self.resolve().list_jobs()

    def _persist(self, job: TaskJob) -> TaskJob:
        return self.resolve()._persist(job)


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
            persistence
            if isinstance(persistence, ExecutionStateCoordinator)
            else ExecutionStateCoordinator(persistence)
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
        task_mutation: TaskEnqueueMutation | None = None,
        reject_existing: bool = False,
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
        queued = self.job_repository.enqueue(
            job,
            task_mutation=task_mutation,
            reject_existing=reject_existing,
        )
        if self.autostart:
            self.start()
        self._wake_event.set()
        return queued

    def cancel(self, task_id: str, *, task_type: TaskJobType | None = None) -> list[TaskJob]:
        jobs = self.job_repository.request_cancel(task_id, task_type=task_type)
        self._wake_event.set()
        return jobs

    def load_job(self, job_id: str) -> TaskJob:
        return self.coordinator.load(job_id)

    def load_active_job(self, task_id: str, *, task_type: TaskJobType) -> TaskJob:
        return self.coordinator.load_active_job(task_id, task_type=task_type)

    def cancel_job(self, job_id: str) -> TaskJob:
        job = self.coordinator.request_cancel_job(job_id)
        self._wake_event.set()
        return job

    def cancel_active_task(self, task_id: str, *, task_type: TaskJobType) -> TaskJob:
        job = self.coordinator.request_cancel_active_task(task_id, task_type=task_type)
        self._wake_event.set()
        return job

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

    def retry(
        self,
        task_id: str,
        *,
        task_type: TaskJobType,
        source_job_id: str | None = None,
        task_mutation: TaskEnqueueMutation | None = None,
    ) -> TaskJob:
        jobs = self.jobs_for_task(task_id, task_type=task_type)
        if not jobs:
            raise NotFoundError(f"任务执行记录不存在: {task_id}")
        if any(job.status not in TERMINAL_JOB_STATUSES for job in jobs):
            raise ConflictError("任务已有活动执行，不能重试。")
        if source_job_id is None:
            job = max(jobs, key=lambda item: item.execution_no)
        else:
            job = next((item for item in jobs if item.job_id == source_job_id), None)
            if job is None:
                raise ConflictError(f"任务绑定的失败执行记录不存在: {source_job_id}")
            if job.task_id != task_id or job.task_type != task_type:
                raise ConflictError(f"任务绑定的失败执行记录身份不匹配: {source_job_id}")
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
        queued = self.job_repository.enqueue(
            retried,
            task_mutation=task_mutation,
            reject_existing=True,
        )
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
        claim_failures = 0
        while not self._stop_event.is_set():
            try:
                job = self.job_repository.claim_next(worker_id=worker_id, lease_seconds=self.lease_seconds)
            except (OSError, TaskRepositoryReadError) as exc:
                claim_failures += 1
                logger.warning(
                    "Recoverable task claim persistence error; worker backing off: "
                    "task_id=%s job_id=%s worker_id=%s failures=%s",
                    getattr(exc, "task_id", None),
                    getattr(exc, "job_id", None),
                    worker_id,
                    claim_failures,
                    exc_info=True,
                )
                self._wait_for_claim_retry(claim_failures)
                continue
            except Exception:
                claim_failures += 1
                logger.exception(
                    "Unexpected task claim error; worker continuing after bounded backoff: worker_id=%s failures=%s",
                    worker_id,
                    claim_failures,
                )
                self._wait_for_claim_retry(claim_failures)
                continue
            claim_failures = 0
            if job is None:
                self._wake_event.wait(self.poll_interval_seconds)
                self._wake_event.clear()
                continue
            try:
                self._run_job(job, worker_id)
            except Exception:
                logger.exception("Unexpected task worker error; worker continuing: job_id=%s", job.job_id)

    def _wait_for_claim_retry(self, failures: int) -> None:
        base = max(0.01, self.poll_interval_seconds)
        delay = min(1.0, base * (2 ** min(failures - 1, 6)))
        self._wake_event.wait(delay)
        self._wake_event.clear()

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
            result = handler(context, job.payload)
            token.raise_if_cancelled()
        except TaskCancelled:
            self._mark_job_cancelled(job, worker_id)
        except TaskStaleLeaseError:
            logger.warning("Stale task job lease; worker stopping: job_id=%s worker_id=%s", job.job_id, worker_id)
        except Exception as exc:
            logger.exception("Task job failed: job_id=%s", job.job_id)
            self._mark_job_failed(job, worker_id, exc)
        else:
            self._mark_job_succeeded(job, worker_id, result)
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1)

    def _mark_job_succeeded(self, job: TaskJob, worker_id: str, result: CompareTask | None) -> None:
        try:
            if result is None:
                self.coordinator.mark_succeeded(job.job_id, worker_id=worker_id)
            else:
                self.coordinator.commit_success(job.job_id, worker_id=worker_id, result=result)
        except TaskCancelled:
            self._mark_job_cancelled(job, worker_id)
        except TaskStaleLeaseError:
            logger.warning("Stale task job success; worker stopping: job_id=%s worker_id=%s", job.job_id, worker_id)
        except Exception:
            self._log_terminal_commit_failure(job, "success")
        else:
            logger.info("Task job succeeded: job_id=%s", job.job_id)

    def _mark_job_cancelled(self, job: TaskJob, worker_id: str) -> None:
        try:
            if self.coordinator.has_terminal_dependencies:
                self.coordinator.commit_cancelled(job.job_id, worker_id=worker_id)
            else:
                self.coordinator.mark_cancelled(job.job_id, worker_id=worker_id)
        except TaskStaleLeaseError:
            logger.warning(
                "Stale task job cancellation; worker stopping: job_id=%s worker_id=%s", job.job_id, worker_id
            )
        except Exception:
            self._log_terminal_commit_failure(job, "cancellation")

    def _mark_job_failed(self, job: TaskJob, worker_id: str, exc: Exception) -> None:
        try:
            if self.coordinator.has_terminal_dependencies:
                self.coordinator.commit_failure(
                    job.job_id,
                    worker_id=worker_id,
                    error=str(exc),
                    retry_delay_seconds=self.retry_delay_seconds,
                )
            else:
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
        except Exception:
            self._log_terminal_commit_failure(job, "failure")

    @staticmethod
    def _log_terminal_commit_failure(job: TaskJob, terminal_path: str) -> None:
        logger.exception(
            "Authoritative terminal %s commit failed; startup reconciliation required if Task is terminal: job_id=%s",
            terminal_path,
            job.job_id,
        )

    def _heartbeat_loop(self, job_id: str, worker_id: str, stop_event: threading.Event) -> None:
        interval = max(0.01, min(1.0, self.lease_seconds / 3))
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
            except (OSError, TaskRepositoryReadError):
                logger.warning(
                    "Recoverable task heartbeat persistence error; retrying: job_id=%s worker_id=%s",
                    job_id,
                    worker_id,
                    exc_info=True,
                )
                continue
            except Exception:
                logger.exception(
                    "Unexpected task heartbeat error; heartbeat stopping: job_id=%s worker_id=%s",
                    job_id,
                    worker_id,
                )
                return
            if extended is None:
                return


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


default_task_runner = QueuedTaskRunner()
