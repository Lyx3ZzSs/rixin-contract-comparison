from __future__ import annotations

import logging
import queue
import threading
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import Settings, settings
from app.errors import ConflictError, NotFoundError, TaskCancelled
from app.infrastructure.task_repository import SQLiteTaskRepository, default_task_repository
from app.models import CompareTask
from app.services.progress_bus import ProgressBus, ProgressEvent

logger = logging.getLogger(__name__)

TaskJobType = Literal["compare"]
TaskJobStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCEL_REQUESTED", "CANCELLED"]
TERMINAL_JOB_STATUSES: set[TaskJobStatus] = {"SUCCEEDED", "FAILED", "CANCELLED"}


class TaskJob(BaseModel):
    job_id: str
    task_id: str
    task_type: TaskJobType = "compare"
    status: TaskJobStatus = "QUEUED"
    execution_no: int = 1
    payload: dict[str, Any] = Field(default_factory=dict, exclude=True, repr=False)
    attempt: int = 0
    max_attempts: int = 1
    queued_at: str = Field(default_factory=lambda: _utc_now())
    started_at: str = ""
    finished_at: str = ""
    updated_at: str = Field(default_factory=lambda: _utc_now())
    error_code: str = ""
    last_error: str = ""


class CancellationToken:
    def __init__(self, event: threading.Event) -> None:
        self._event = event

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise TaskCancelled("任务已取消。")


class TaskExecutionContext:
    def __init__(self, *, job_id: str, task_id: str, worker_id: str, cancellation_token: CancellationToken) -> None:
        self.job_id = job_id
        self.task_id = task_id
        self.worker_id = worker_id
        self.cancellation_token = cancellation_token


TaskHandler = Callable[[TaskExecutionContext, Mapping[str, Any]], CompareTask | None]
TaskEnqueueMutation = Callable[[CompareTask, TaskJob], None]


class QueuedTaskRunner:
    """A process-local queue; SQLite stores only the current execution projection."""

    def __init__(
        self,
        *,
        repository: SQLiteTaskRepository = default_task_repository,
        app_settings: Settings = settings,
        max_workers: int | None = None,
        autostart: bool = True,
        **_: Any,
    ) -> None:
        self.settings = app_settings
        self.repository = repository
        self.max_workers = max_workers or app_settings.task_runner_max_workers
        self.autostart = autostart
        self._handlers: dict[TaskJobType, TaskHandler] = {}
        self._queue: queue.Queue[TaskJob] = queue.Queue()
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

    def register_handler(self, task_type: TaskJobType, handler: TaskHandler) -> None:
        self._handlers[task_type] = handler

    def submit(
        self,
        *,
        task_type: TaskJobType,
        task_id: str,
        payload: Mapping[str, Any],
        task_mutation: TaskEnqueueMutation | None = None,
        reject_existing: bool = False,
        **_: Any,
    ) -> TaskJob:
        if task_type not in self._handlers:
            raise RuntimeError(f"未注册任务执行器: {task_type}")
        task = self.repository.load_compare_task(task_id)
        if reject_existing and task.status == "PROCESSING" and task.execution_no > 0:
            raise ConflictError("任务已有活动执行，不能重复提交。")
        execution_no = task.execution_no + 1
        now = _utc_now()
        job = TaskJob(
            job_id=f"{task_type}:{task_id}:{execution_no}",
            task_id=task_id,
            task_type=task_type,
            execution_no=execution_no,
            payload=dict(payload),
            queued_at=now,
            updated_at=now,
        )

        def queued(current: CompareTask) -> None:
            if task_mutation is not None:
                task_mutation(current, job)
            current.status = "PROCESSING"
            current.terminal_reason = "NONE"
            current.stage = "排队中"
            current.progress_percent = 3
            current.execution_id = job.job_id
            current.execution_no = execution_no
            current.execution_status = "QUEUED"
            current.execution_queued_at = now
            current.execution_started_at = ""
            current.execution_finished_at = ""
            current.execution_error_code = ""
            current.execution_last_error = ""
            current.errors = []

        self.repository.update_compare_task(task_id, queued)
        with self._lock:
            self._cancel_events[job.job_id] = threading.Event()
            self._queue.put(job)
        if self.autostart:
            self.start()
        return job

    def load_active_job(self, task_id: str, *, task_type: TaskJobType = "compare") -> TaskJob:
        del task_type
        return self._job_from_task(self.repository.load_compare_task(task_id))

    def load_job(self, job_id: str) -> TaskJob:
        for task in self.repository.list_compare_tasks():
            if task.execution_id == job_id:
                return self._job_from_task(task)
        raise NotFoundError(f"任务执行记录不存在: {job_id}")

    def cancel_active_task(self, task_id: str, *, task_type: TaskJobType = "compare") -> TaskJob:
        del task_type
        task = self.repository.load_compare_task(task_id)
        if task.status != "PROCESSING":
            return self._job_from_task(task)
        with self._lock:
            event = self._cancel_events.get(task.execution_id)
            if event is not None:
                event.set()

        def cancelling(current: CompareTask) -> None:
            current.execution_status = "CANCEL_REQUESTED"
            current.stage = "取消中"

        updated = self.repository.update_compare_task(task_id, cancelling)
        return self._job_from_task(updated)

    def jobs_for_task(self, task_id: str, *, task_type: TaskJobType | None = None) -> list[TaskJob]:
        del task_type
        try:
            return [self._job_from_task(self.repository.load_compare_task(task_id))]
        except FileNotFoundError:
            return []

    def latest_job(self, task_id: str, *, task_type: TaskJobType | None = None) -> TaskJob:
        jobs = self.jobs_for_task(task_id, task_type=task_type)
        if not jobs:
            raise NotFoundError(f"任务执行记录不存在: {task_id}")
        return jobs[0]

    def stats(self) -> dict[str, int]:
        counts = {
            status.lower(): 0
            for status in ("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCEL_REQUESTED", "CANCELLED")
        }
        for task in self.repository.list_compare_tasks():
            counts[task.execution_status.lower()] += 1
        return counts

    def start(self) -> None:
        with self._lock:
            self._threads = [thread for thread in self._threads if thread.is_alive()]
            if self._threads:
                return
            self._stop_event.clear()
            for index in range(self.max_workers):
                thread = threading.Thread(
                    target=self._worker_loop,
                    args=(f"worker-{index}-{uuid.uuid4().hex[:8]}",),
                    name=f"task-runner-{index}",
                    daemon=True,
                )
                thread.start()
                self._threads.append(thread)

    def stop(self, *, wait: bool = True) -> None:
        self._stop_event.set()
        if wait:
            for thread in list(self._threads):
                thread.join(timeout=5)
        with self._lock:
            self._threads = [thread for thread in self._threads if thread.is_alive()]

    def _worker_loop(self, worker_id: str) -> None:
        while not self._stop_event.is_set():
            try:
                job = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._run_job(job, worker_id)
            finally:
                self._queue.task_done()

    def _run_job(self, job: TaskJob, worker_id: str) -> None:
        event = self._cancel_events.setdefault(job.job_id, threading.Event())
        if event.is_set():
            self._finish_cancelled(job)
            return
        started_at = _utc_now()

        def running(task: CompareTask) -> None:
            if task.execution_id != job.job_id or task.execution_status == "CANCEL_REQUESTED":
                raise TaskCancelled("任务已取消。")
            task.execution_status = "RUNNING"
            task.execution_started_at = started_at
            task.stage = "文档解析中"
            task.progress_percent = max(task.progress_percent, 5)

        try:
            self.repository.update_compare_task(job.task_id, running)
            context = TaskExecutionContext(
                job_id=job.job_id,
                task_id=job.task_id,
                worker_id=worker_id,
                cancellation_token=CancellationToken(event),
            )
            result = self._handlers[job.task_type](context, job.payload)
            context.cancellation_token.raise_if_cancelled()
            self._finish_succeeded(job, result)
        except TaskCancelled:
            self._finish_cancelled(job)
        except Exception as exc:
            logger.exception("Background compare task failed: task_id=%s", job.task_id)
            self._finish_failed(job, exc)
        finally:
            with self._lock:
                self._cancel_events.pop(job.job_id, None)

    def _finish_succeeded(self, job: TaskJob, result: CompareTask | None) -> None:
        finished_at = _utc_now()

        def succeeded(task: CompareTask) -> None:
            if result is not None:
                excluded = {
                    "task_id",
                    "revision",
                    "created_at",
                    "updated_at",
                    "owner_sub",
                    "execution_id",
                    "execution_no",
                    "execution_status",
                    "execution_queued_at",
                    "execution_started_at",
                    "execution_finished_at",
                    "execution_error_code",
                    "execution_last_error",
                    "status",
                    "terminal_reason",
                }
                for field_name in result.__class__.model_fields:
                    if field_name not in excluded:
                        setattr(task, field_name, getattr(result, field_name))
            task.status = "COMPLETED"
            task.terminal_reason = "NONE"
            task.stage = "已完成"
            task.progress_percent = 100
            task.report_revision = max(1, task.report_revision)
            task.execution_status = "SUCCEEDED"
            task.execution_finished_at = finished_at

        task = self.repository.update_compare_task(job.task_id, succeeded)
        ProgressBus.get_instance().publish(
            ProgressEvent(
                task_id=task.task_id,
                stage=task.stage,
                progress_percent=100,
                status="COMPLETED",
                revision=task.revision,
            )
        )

    def _finish_failed(self, job: TaskJob, exc: Exception) -> None:
        message = str(exc) or type(exc).__name__
        finished_at = _utc_now()

        def failed(task: CompareTask) -> None:
            task.status = "FAILED"
            task.terminal_reason = "EXECUTION_FAILED"
            task.stage = "处理失败"
            task.progress_percent = 100
            task.execution_status = "FAILED"
            task.execution_finished_at = finished_at
            task.execution_error_code = type(exc).__name__
            task.execution_last_error = message
            if message not in task.errors:
                task.errors.append(message)

        task = self.repository.update_compare_task(job.task_id, failed)
        ProgressBus.get_instance().publish(
            ProgressEvent(
                task_id=task.task_id,
                stage=task.stage,
                progress_percent=100,
                status="FAILED",
                revision=task.revision,
            )
        )

    def _finish_cancelled(self, job: TaskJob) -> None:
        finished_at = _utc_now()

        def cancelled(task: CompareTask) -> None:
            task.status = "FAILED"
            task.terminal_reason = "CANCELLED"
            task.stage = "已取消"
            task.progress_percent = 100
            task.execution_status = "CANCELLED"
            task.execution_finished_at = finished_at
            task.execution_error_code = "CANCELLED"
            task.execution_last_error = "任务已取消。"

        task = self.repository.update_compare_task(job.task_id, cancelled)
        ProgressBus.get_instance().publish(
            ProgressEvent(
                task_id=task.task_id,
                stage=task.stage,
                progress_percent=100,
                status="FAILED",
                revision=task.revision,
            )
        )

    @staticmethod
    def _job_from_task(task: CompareTask) -> TaskJob:
        return TaskJob(
            job_id=task.execution_id or f"compare:{task.task_id}:{max(1, task.execution_no)}",
            task_id=task.task_id,
            status=task.execution_status,
            execution_no=max(1, task.execution_no),
            attempt=1 if task.execution_started_at else 0,
            max_attempts=1,
            queued_at=task.execution_queued_at or task.created_at,
            started_at=task.execution_started_at,
            finished_at=task.execution_finished_at,
            updated_at=task.updated_at,
            error_code=task.execution_error_code,
            last_error=task.execution_last_error,
        )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


default_task_runner = QueuedTaskRunner()
