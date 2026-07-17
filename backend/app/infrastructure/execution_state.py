from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Protocol

from app.errors import (
    NotFoundError,
    TaskCancelled,
    TaskRepositoryReadError,
    TaskStaleLeaseError,
    TaskTransitionConflict,
)
from app.models import CompareTask, DiffItem, TaskStatus, TaskTerminalReason

if TYPE_CHECKING:
    from app.infrastructure.task_runner import TaskJob, TaskJobStatus, TaskJobType


logger = logging.getLogger(__name__)


class TaskJobPersistence(Protocol):
    @property
    def job_dir(self) -> Path: ...

    def load(self, job_id: str) -> TaskJob: ...

    def list_jobs(self) -> list[TaskJob]: ...

    def _persist(self, job: TaskJob) -> TaskJob: ...


class CompareTaskPersistence(Protocol):
    def load_compare_task(self, task_id: str) -> CompareTask: ...

    def list_compare_tasks(self) -> list[CompareTask]: ...

    def update_compare_task(self, task_id: str, mutate: Callable[[CompareTask], None]) -> CompareTask: ...


class ProgressPublisher(Protocol):
    def publish(self, event: object) -> None: ...


TaskEnqueueMutation = Callable[[CompareTask, "TaskJob"], None]


class TaskClaimPersistenceError(OSError):
    def __init__(self, job: TaskJob, cause: OSError) -> None:
        super().__init__(str(cause))
        self.job_id = job.job_id
        self.task_id = job.task_id


@dataclass(frozen=True)
class TaskExecutionContext:
    job_id: str
    task_id: str
    worker_id: str
    cancellation_token: CancellationToken


@dataclass(frozen=True)
class CancellationToken:
    job_id: str
    worker_id: str
    coordinator: ExecutionStateCoordinator

    def raise_if_cancelled(self) -> None:
        self.coordinator.raise_if_cancelled(self.job_id, self.worker_id)


class ExecutionStateCoordinator:
    """Serializes Job transitions and keeps process-local immutable snapshots."""

    _process_lock: ClassVar[threading.RLock] = threading.RLock()
    _process_snapshots: ClassVar[dict[str, Mapping[str, TaskJob]]] = {}

    def __init__(
        self,
        repository: TaskJobPersistence,
        *,
        task_repository: CompareTaskPersistence | None = None,
        progress_publisher: ProgressPublisher | None = None,
    ) -> None:
        self._repository = repository
        self._task_repository = task_repository
        self._progress_publisher = progress_publisher
        with self._process_lock:
            self._repository_namespace = self._storage_namespace()
            self._snapshots = self._hydrate_snapshots()

    def configure_terminal_commits(
        self,
        task_repository: CompareTaskPersistence,
        progress_publisher: ProgressPublisher,
    ) -> None:
        with self._process_lock:
            self._task_repository = task_repository
            self._progress_publisher = progress_publisher

    @property
    def has_terminal_dependencies(self) -> bool:
        return self._task_repository is not None and self._progress_publisher is not None

    def enqueue(
        self,
        job: TaskJob,
        *,
        task_mutation: TaskEnqueueMutation | None = None,
        reject_existing: bool = False,
    ) -> TaskJob:
        with self._process_lock:
            self._ensure_repository_namespace()
            existing_jobs = list(self._snapshots.values())
            job_id_matches = [existing for existing in existing_jobs if existing.job_id == job.job_id]
            if job_id_matches:
                if len(job_id_matches) != 1 or not self._has_same_identity(job_id_matches[0], job):
                    raise TaskTransitionConflict(f"执行记录 ID {job.job_id} 与已有执行身份冲突。")
                existing = job_id_matches[0]
                if reject_existing:
                    raise TaskTransitionConflict(f"执行记录 {existing.job_id} 已存在，不能重复创建。")
                if existing.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    raise TaskTransitionConflict(
                        f"执行记录 {existing.job_id} 已为终态 {existing.status}，不能重新入队。"
                    )
                return existing.model_copy(deep=True)
            if any(self._has_same_execution_identity(existing, job) for existing in existing_jobs):
                raise TaskTransitionConflict(
                    f"任务 {job.task_id} 的第 {job.execution_no} 次 {job.task_type} 执行记录已存在，不能重复创建。"
                )
            active_jobs = [
                existing
                for existing in existing_jobs
                if existing.task_id == job.task_id
                and existing.task_type == job.task_type
                and existing.status not in {"SUCCEEDED", "FAILED", "CANCELLED"}
            ]
            if active_jobs:
                raise TaskTransitionConflict(f"任务 {job.task_id} 已有活动执行记录 {active_jobs[0].job_id}。")
            candidate = job.model_copy(deep=True)
            candidate.status = "QUEUED"
            candidate.queued_at = _utc_now()
            candidate.updated_at = candidate.queued_at
            candidate.source_path = ""
            if task_mutation is not None:
                return self._persist_enqueue_with_task(candidate, task_mutation)
            return self._persist_then_replace(candidate)

    def load(self, job_id: str) -> TaskJob:
        with self._process_lock:
            return self._get(job_id).model_copy(deep=True)

    def list_jobs(self) -> list[TaskJob]:
        with self._process_lock:
            self._ensure_repository_namespace()
            jobs = [job.model_copy(deep=True) for job in self._snapshots.values()]
        return sorted(jobs, key=lambda job: (job.next_run_at or job.queued_at, job.queued_at))

    def reload_from_storage(self) -> None:
        """Refresh the coordinator view before startup reconciliation."""
        with self._process_lock:
            self._repository_namespace = self._storage_namespace()
            self._snapshots = self._hydrate_snapshots()

    def list_persisted_jobs(self) -> list[TaskJob]:
        """Return the repository view without collapsing duplicate job IDs."""
        with self._process_lock:
            return [job.model_copy(deep=True) for job in self._repository.list_jobs()]

    def claim_next(self, *, worker_id: str, lease_seconds: int) -> TaskJob | None:
        with self._process_lock:
            now = _utc_now()
            for job in self.list_jobs():
                try:
                    if not self._task_binding_allows_claim(job):
                        continue
                except TaskRepositoryReadError:
                    logger.warning(
                        "Skipping task job claim because authoritative Task read failed: task_id=%s job_id=%s",
                        job.task_id,
                        job.job_id,
                        exc_info=True,
                    )
                    continue
                if self._is_expired_cancel_request(job, now):
                    try:
                        self._commit_cancel_terminal(job)
                    except OSError as exc:
                        raise TaskClaimPersistenceError(job, exc) from exc
                    continue
                if self._is_expired_at_attempt_limit(job, now):
                    try:
                        self._commit_expired_lease_failure(job)
                    except OSError as exc:
                        raise TaskClaimPersistenceError(job, exc) from exc
                    continue
                if not self._is_claimable(job, now):
                    continue
                candidate = job.model_copy(deep=True)
                candidate.status = "RUNNING"
                candidate.attempt += 1
                candidate.started_at = now
                candidate.updated_at = now
                candidate.lease_owner = worker_id
                candidate.lease_expires_at = _plus_seconds(lease_seconds)
                candidate.last_error = ""
                try:
                    return self._persist_then_replace(candidate)
                except OSError as exc:
                    raise TaskClaimPersistenceError(job, exc) from exc
        return None

    def extend_lease(self, job_id: str, *, worker_id: str, lease_seconds: int) -> TaskJob | None:
        with self._process_lock:
            job = self._get(job_id)
            if job.status != "RUNNING":
                return None
            self._ensure_current_lease(job, worker_id)
            candidate = job.model_copy(deep=True)
            candidate.lease_expires_at = _plus_seconds(lease_seconds)
            candidate.updated_at = _utc_now()
            return self._persist_then_replace(candidate)

    def request_cancel(self, task_id: str, *, task_type: TaskJobType | None = None) -> list[TaskJob]:
        with self._process_lock:
            self._ensure_repository_namespace()
            jobs = [
                job
                for job in self._snapshots.values()
                if job.task_id == task_id and (task_type is None or job.task_type == task_type)
            ]
            if not jobs:
                return []
            active = [job for job in jobs if job.status not in {"SUCCEEDED", "FAILED", "CANCELLED"}]
            job = max(active or jobs, key=lambda item: item.execution_no)
            return [self._request_cancel_job_locked(job)]

    def request_cancel_job(self, job_id: str) -> TaskJob:
        with self._process_lock:
            return self._request_cancel_job_locked(self._get(job_id))

    def load_active_job(self, task_id: str, *, task_type: TaskJobType) -> TaskJob:
        with self._process_lock:
            _task, job = self._load_active_task_job_locked(task_id, task_type=task_type)
            return job.model_copy(deep=True)

    def request_cancel_active_task(self, task_id: str, *, task_type: TaskJobType) -> TaskJob:
        with self._process_lock:
            task = self._load_task_locked(task_id)
            if task.status == "PROCESSING":
                job = self._load_bound_job_locked(task, task.active_job_id, task_type=task_type, binding="活动")
                return self._request_cancel_job_locked(job)
            if task.status == "COMPLETED" and task.terminal_reason == "NONE":
                return self._load_terminal_job_locked(task, task_type=task_type, expected_status="SUCCEEDED")
            if task.status == "FAILED" and task.terminal_reason == "CANCELLED":
                return self._load_terminal_job_locked(task, task_type=task_type, expected_status="CANCELLED")
            raise TaskTransitionConflict(
                f"任务 {task.task_id} 已为 {task.status}/{task.terminal_reason}，不能请求取消。"
            )

    def _request_cancel_job_locked(self, job: TaskJob) -> TaskJob:
        if job.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return job.model_copy(deep=True)
        now = _utc_now()
        if job.status == "CANCEL_REQUESTED":
            if self._lease_expired(job, now):
                return self._commit_cancel_terminal(job)[1]
            return job.model_copy(deep=True)
        if job.status == "RUNNING" and self._lease_expired(job, now):
            return self._commit_cancel_terminal(job)[1]
        candidate = job.model_copy(deep=True)
        if candidate.status == "QUEUED":
            if self.has_terminal_dependencies:
                return self._commit_cancel_terminal(job)[1]
            candidate.status = "CANCELLED"
            candidate.finished_at = now
            candidate.lease_owner = ""
            candidate.lease_expires_at = ""
        elif candidate.status == "RUNNING":
            candidate.status = "CANCEL_REQUESTED"
        else:
            raise TaskTransitionConflict(f"执行记录 {candidate.job_id} 不能从 {candidate.status} 请求取消。")
        candidate.updated_at = now
        return self._persist_then_replace(candidate)

    def _load_active_task_job_locked(
        self,
        task_id: str,
        *,
        task_type: TaskJobType,
    ) -> tuple[CompareTask, TaskJob]:
        task = self._load_task_locked(task_id)
        job = self._load_bound_job_locked(task, task.active_job_id, task_type=task_type, binding="活动")
        return task, job

    def _load_task_locked(self, task_id: str) -> CompareTask:
        if self._task_repository is None:
            raise RuntimeError("ExecutionStateCoordinator 未配置 Task persistence。")
        return self._task_repository.load_compare_task(task_id)

    def _load_bound_job_locked(
        self,
        task: CompareTask,
        job_id: str,
        *,
        task_type: TaskJobType,
        binding: str,
    ) -> TaskJob:
        if not job_id:
            raise NotFoundError(f"任务 {task.task_id} 的{binding}执行记录不存在。")
        job = self._get(job_id)
        if job.task_id != task.task_id or job.task_type != task_type:
            raise TaskTransitionConflict(f"任务 {task.task_id} 的{binding}执行记录身份不匹配。")
        return job

    def _load_terminal_job_locked(
        self,
        task: CompareTask,
        *,
        task_type: TaskJobType,
        expected_status: TaskJobStatus,
    ) -> TaskJob:
        try:
            job = self._load_bound_job_locked(
                task,
                task.terminal_job_id,
                task_type=task_type,
                binding="终态",
            )
        except (FileNotFoundError, NotFoundError) as exc:
            raise TaskTransitionConflict(f"任务 {task.task_id} 的终态执行记录不存在。") from exc
        if job.attempt != task.terminal_attempt or job.status != expected_status:
            raise TaskTransitionConflict(f"任务 {task.task_id} 的终态执行记录与任务终态不一致。")
        return job.model_copy(deep=True)

    def mark_succeeded(self, job_id: str, *, worker_id: str) -> TaskJob:
        with self._process_lock:
            job = self._validate_running_terminal(job_id, worker_id, "SUCCEEDED")
            candidate = job.model_copy(deep=True)
            candidate.status = "SUCCEEDED"
            candidate.finished_at = _utc_now()
            candidate.updated_at = candidate.finished_at
            candidate.lease_owner = ""
            candidate.lease_expires_at = ""
            return self._persist_then_replace(candidate)

    def commit_success(
        self,
        job_id: str,
        *,
        worker_id: str,
        result: CompareTask,
    ) -> tuple[CompareTask, TaskJob]:
        with self._process_lock:
            replay = self._matching_terminal_replay(job_id, "COMPLETED", "NONE", "SUCCEEDED")
            if replay is not None:
                return replay
            job = self._validate_running_terminal(job_id, worker_id, "SUCCEEDED")
            task = self._persist_terminal_task(
                job,
                status="COMPLETED",
                terminal_reason="NONE",
                result=result,
            )
            candidate = self._terminal_job_candidate(job, "SUCCEEDED")
            persisted_job = self._persist_then_replace(candidate)
            self._publish_terminal(task)
            return task, persisted_job

    def commit_progress(
        self,
        job_id: str,
        *,
        worker_id: str,
        stage: str,
        progress_percent: int,
        detail: dict | None = None,
    ) -> CompareTask:
        with self._process_lock:
            job = self._validate_running_terminal(job_id, worker_id, "RUNNING")
            if self._task_repository is None:
                raise RuntimeError("ExecutionStateCoordinator 未配置 Task persistence。")
            progress = min(max(progress_percent, 0), 99)

            def mutate(task: CompareTask) -> None:
                if task.status != "PROCESSING" or task.active_job_id != job.job_id:
                    raise TaskTransitionConflict(f"任务 {task.task_id} 的活动执行记录不是 {job.job_id}，不能写入进度。")
                task.stage = stage
                task.progress_percent = max(task.progress_percent, progress)

            task = self._task_repository.update_compare_task(job.task_id, mutate)
            self._publish_progress(task, detail=detail)
            return task

    def commit_failure(
        self,
        job_id: str,
        *,
        worker_id: str,
        error: str,
        retry_delay_seconds: float,
    ) -> tuple[CompareTask | None, TaskJob]:
        with self._process_lock:
            replay = self._matching_terminal_replay(
                job_id,
                "FAILED",
                "EXECUTION_FAILED",
                "FAILED",
            )
            if replay is not None:
                return replay
            job = self._validate_running_terminal(job_id, worker_id, "FAILED")
            if job.attempt < job.max_attempts:
                candidate = job.model_copy(deep=True)
                candidate.status = "QUEUED"
                candidate.last_error = error
                candidate.updated_at = _utc_now()
                candidate.next_run_at = _plus_seconds(retry_delay_seconds)
                candidate.lease_owner = ""
                candidate.lease_expires_at = ""
                return None, self._persist_then_replace(candidate)
            task = self._persist_terminal_task(
                job,
                status="FAILED",
                terminal_reason="EXECUTION_FAILED",
                error=error,
            )
            candidate = self._terminal_job_candidate(job, "FAILED", error=error)
            persisted_job = self._persist_then_replace(candidate)
            self._publish_terminal(task, detail={"error": error})
            return task, persisted_job

    def commit_cancelled(self, job_id: str, *, worker_id: str) -> tuple[CompareTask, TaskJob]:
        with self._process_lock:
            replay = self._matching_terminal_replay(job_id, "FAILED", "CANCELLED", "CANCELLED")
            if replay is not None:
                return replay
            job = self._get(job_id)
            if job.status != "CANCEL_REQUESTED":
                raise TaskTransitionConflict(f"执行记录 {job_id} 不能从 {job.status} 改写为 CANCELLED。")
            if job.lease_owner != worker_id:
                raise TaskStaleLeaseError(f"执行记录 {job.job_id} 不属于 worker {worker_id}。")
            return self._commit_cancel_terminal(job)

    def repair_job_from_terminal_task(self, task: CompareTask) -> bool:
        with self._process_lock:
            if task.status not in {"COMPLETED", "FAILED"} or not task.terminal_job_id:
                return False
            try:
                job = self._get(task.terminal_job_id)
            except FileNotFoundError:
                return False
            if job.task_id != task.task_id or job.attempt != task.terminal_attempt:
                return False
            target = self._job_status_for_task(task)
            if job.status == target:
                return False
            if job.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                return False
            error = task.errors[-1] if target == "FAILED" and task.errors else ""
            self._persist_then_replace(self._terminal_job_candidate(job, target, error=error))
            return True

    def reconcile_duplicate_execution(self, job: TaskJob) -> bool:
        with self._process_lock:
            if not job.duplicate_execution:
                return False
            if (
                job.error_code == "DUPLICATE_JOB_EXECUTION"
                and job.last_error == "同一执行编号存在冲突的 Job 记录。"
                and job.status in {"SUCCEEDED", "FAILED", "CANCELLED"}
            ):
                return False
            candidate = job.model_copy(deep=True)
            candidate.error_code = "DUPLICATE_JOB_EXECUTION"
            candidate.last_error = "同一执行编号存在冲突的 Job 记录。"
            if candidate.status not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                candidate.status = "FAILED"
                candidate.finished_at = _utc_now()
                candidate.lease_owner = ""
                candidate.lease_expires_at = ""
            candidate.updated_at = _utc_now()
            candidate.duplicate_execution = False
            self._persist_then_replace(candidate)
            return True

    def reconcile_orphan_job(self, job: TaskJob) -> bool:
        with self._process_lock:
            if job.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                return False
            candidate = self._terminal_job_candidate(job, "FAILED", error="未关联到 Task 的活动或终态执行记录。")
            candidate.error_code = "ORPHANED_JOB"
            self._persist_then_replace(candidate)
            return True

    def reconcile_submission_failure(self, task_id: str, *, error: str) -> bool:
        with self._process_lock:
            if self._task_repository is None:
                raise RuntimeError("ExecutionStateCoordinator 未配置 Task persistence。")
            task = self._task_repository.load_compare_task(task_id)
            if task.status != "PROCESSING":
                return False

            def mutate(candidate: CompareTask) -> None:
                candidate.ensure_transition_allowed(
                    "FAILED",
                    terminal_reason="SUBMISSION_FAILED",
                    job_id=candidate.active_job_id,
                )
                candidate.status = "FAILED"
                candidate.terminal_reason = "SUBMISSION_FAILED"
                candidate.active_job_id = ""
                candidate.terminal_job_id = ""
                candidate.terminal_attempt = 0
                candidate.stage = "提交失败"
                candidate.progress_percent = 100
                if error not in candidate.errors:
                    candidate.errors.append(error)

            self._task_repository.update_compare_task(task_id, mutate)
            return True

    def reconcile_stale_running_job(self, job: TaskJob) -> bool:
        with self._process_lock:
            if job.status != "RUNNING":
                return False
            if job.attempt < job.max_attempts:
                candidate = job.model_copy(deep=True)
                candidate.status = "QUEUED"
                candidate.next_run_at = ""
                candidate.updated_at = _utc_now()
                candidate.lease_owner = ""
                candidate.lease_expires_at = ""
                self._persist_then_replace(candidate)
                return True

            error = "进程重启时执行已达到最大尝试次数。"
            if self._task_repository is not None:
                self._persist_terminal_task(
                    job,
                    status="FAILED",
                    terminal_reason="EXECUTION_FAILED",
                    error=error,
                )
            candidate = self._terminal_job_candidate(job, "FAILED", error=error)
            candidate.error_code = "PROCESS_RESTART_MAX_ATTEMPTS"
            self._persist_then_replace(candidate)
            return True

    def mark_failed(
        self,
        job_id: str,
        *,
        worker_id: str,
        error: str,
        retry_delay_seconds: float,
    ) -> TaskJob:
        with self._process_lock:
            job = self._validate_running_terminal(job_id, worker_id, "FAILED")
            candidate = job.model_copy(deep=True)
            candidate.last_error = error
            candidate.updated_at = _utc_now()
            candidate.lease_owner = ""
            candidate.lease_expires_at = ""
            if candidate.attempt < candidate.max_attempts:
                candidate.status = "QUEUED"
                candidate.next_run_at = _plus_seconds(retry_delay_seconds)
            else:
                candidate.status = "FAILED"
                candidate.finished_at = candidate.updated_at
            return self._persist_then_replace(candidate)

    def mark_cancelled(self, job_id: str, *, worker_id: str) -> TaskJob:
        with self._process_lock:
            job = self._get(job_id)
            if job.status == "CANCELLED":
                return job.model_copy(deep=True)
            if job.status == "QUEUED":
                if job.lease_owner:
                    raise TaskStaleLeaseError(f"执行记录 {job_id} 的排队取消不是无 owner 转换。")
            elif job.status == "CANCEL_REQUESTED":
                if job.lease_owner != worker_id:
                    raise TaskStaleLeaseError(f"执行记录 {job_id} 不属于 worker {worker_id}。")
            else:
                raise TaskTransitionConflict(f"执行记录 {job_id} 不能从 {job.status} 改写为 CANCELLED。")
            candidate = job.model_copy(deep=True)
            candidate.status = "CANCELLED"
            candidate.finished_at = _utc_now()
            candidate.updated_at = candidate.finished_at
            candidate.lease_owner = ""
            candidate.lease_expires_at = ""
            return self._persist_then_replace(candidate)

    def raise_if_cancelled(self, job_id: str, worker_id: str) -> None:
        with self._process_lock:
            job = self._get(job_id)
            if job.status in {"CANCEL_REQUESTED", "CANCELLED"}:
                raise TaskCancelled(f"执行记录 {job_id} 已取消。")
            if job.status != "RUNNING":
                raise TaskStaleLeaseError(f"执行记录 {job_id} 已不处于 RUNNING。")
            self._ensure_current_lease(job, worker_id)

    def _validate_running_terminal(
        self,
        job_id: str,
        worker_id: str,
        target_status: TaskJobStatus,
    ) -> TaskJob:
        job = self._get(job_id)
        if job.status in {"CANCEL_REQUESTED", "CANCELLED"}:
            raise TaskCancelled(f"执行记录 {job_id} 的取消优先于 {target_status}。")
        if job.status != "RUNNING":
            raise TaskTransitionConflict(f"执行记录 {job_id} 不能从 {job.status} 改写为 {target_status}。")
        self._ensure_current_lease(job, worker_id)
        return job

    def _matching_terminal_replay(
        self,
        job_id: str,
        task_status: str,
        terminal_reason: str,
        job_status: str,
    ) -> tuple[CompareTask, TaskJob] | None:
        job = self._get(job_id)
        if job.status != job_status or self._task_repository is None:
            return None
        task = self._task_repository.load_compare_task(job.task_id)
        if (
            task.status == task_status
            and task.terminal_reason == terminal_reason
            and task.terminal_job_id == job.job_id
            and task.terminal_attempt == job.attempt
        ):
            return task, job.model_copy(deep=True)
        raise TaskTransitionConflict(f"执行记录 {job_id} 的终态与任务终态不一致。")

    def _persist_terminal_task(
        self,
        job: TaskJob,
        *,
        status: TaskStatus,
        terminal_reason: TaskTerminalReason,
        result: CompareTask | None = None,
        error: str = "",
    ) -> CompareTask:
        if self._task_repository is None:
            raise RuntimeError("ExecutionStateCoordinator 未配置 Task persistence。")

        def mutate(task: CompareTask) -> None:
            task.ensure_transition_allowed(
                status,
                terminal_reason=terminal_reason,
                job_id=job.job_id,
            )
            if result is not None:
                _copy_processing_result(task, result)
            task.status = status
            task.terminal_reason = terminal_reason
            task.terminal_job_id = job.job_id
            task.terminal_attempt = job.attempt
            task.progress_percent = 100
            if status == "COMPLETED":
                task.stage = "已完成"
                task.errors = []
                if task.report_revision == 0:
                    task.report_revision = 1
            elif terminal_reason == "CANCELLED":
                task.stage = "已取消"
                if error and error not in task.errors:
                    task.errors.append(error)
            else:
                task.stage = "失败"
                if error and error not in task.errors:
                    task.errors.append(error)

        return self._task_repository.update_compare_task(job.task_id, mutate)

    def _publish_terminal(self, task: CompareTask, *, detail: dict | None = None) -> None:
        if self._progress_publisher is None:
            return
        from app.services.progress_bus import ProgressEvent

        self._progress_publisher.publish(
            ProgressEvent(
                task_id=task.task_id,
                stage=task.stage,
                progress_percent=task.progress_percent,
                status=task.status,
                detail=detail,
                revision=task.revision,
            )
        )

    def _publish_progress(self, task: CompareTask, *, detail: dict | None = None) -> None:
        if self._progress_publisher is None:
            return
        from app.services.progress_bus import ProgressEvent

        self._progress_publisher.publish(
            ProgressEvent(
                task_id=task.task_id,
                stage=task.stage,
                progress_percent=task.progress_percent,
                status="PROCESSING",
                detail=detail,
                revision=task.revision,
            )
        )

    def _commit_expired_lease_failure(self, job: TaskJob) -> tuple[CompareTask | None, TaskJob]:
        error = "任务租约过期且已达到最大尝试次数。"
        if self._task_repository is None:
            candidate = self._terminal_job_candidate(job, "FAILED", error=error)
            candidate.error_code = "LEASE_EXPIRED_MAX_ATTEMPTS"
            return None, self._persist_then_replace(candidate)
        task = self._persist_terminal_task(
            job,
            status="FAILED",
            terminal_reason="EXECUTION_FAILED",
            error=error,
        )
        candidate = self._terminal_job_candidate(job, "FAILED", error=error)
        candidate.error_code = "LEASE_EXPIRED_MAX_ATTEMPTS"
        persisted_job = self._persist_then_replace(candidate)
        self._publish_terminal(task, detail={"error": error, "error_code": candidate.error_code})
        return task, persisted_job

    def _commit_cancel_terminal(self, job: TaskJob) -> tuple[CompareTask, TaskJob]:
        task = self._persist_terminal_task(
            job,
            status="FAILED",
            terminal_reason="CANCELLED",
            error="任务已取消。",
        )
        persisted_job = self._persist_then_replace(self._terminal_job_candidate(job, "CANCELLED"))
        self._publish_terminal(task)
        return task, persisted_job

    @staticmethod
    def _terminal_job_candidate(job: TaskJob, status: TaskJobStatus, *, error: str = "") -> TaskJob:
        candidate = job.model_copy(deep=True)
        candidate.status = status
        candidate.finished_at = _utc_now()
        candidate.updated_at = candidate.finished_at
        candidate.lease_owner = ""
        candidate.lease_expires_at = ""
        if error:
            candidate.last_error = error
        return candidate

    @staticmethod
    def _job_status_for_task(task: CompareTask) -> TaskJobStatus:
        if task.status == "COMPLETED":
            return "SUCCEEDED"
        if task.terminal_reason == "CANCELLED":
            return "CANCELLED"
        return "FAILED"

    def _ensure_current_lease(self, job: TaskJob, worker_id: str) -> None:
        if job.lease_owner != worker_id:
            raise TaskStaleLeaseError(f"执行记录 {job.job_id} 不属于 worker {worker_id}。")
        if not job.lease_expires_at or job.lease_expires_at <= _utc_now():
            raise TaskStaleLeaseError(f"执行记录 {job.job_id} 的 worker lease 已过期。")

    def _task_binding_allows_claim(self, job: TaskJob) -> bool:
        if job.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return False
        if job.duplicate_execution or job.error_code == "DUPLICATE_JOB_EXECUTION":
            return False
        if self._task_repository is None:
            return True
        try:
            task = self._task_repository.load_compare_task(job.task_id)
        except FileNotFoundError:
            return False
        return bool(task.status == "PROCESSING" and task.active_job_id == job.job_id)

    def _persist_then_replace(self, candidate: TaskJob) -> TaskJob:
        persisted = self._repository._persist(candidate.model_copy(deep=True))
        self._replace_snapshot(persisted)
        return persisted.model_copy(deep=True)

    def _persist_enqueue_with_task(
        self,
        candidate: TaskJob,
        task_mutation: TaskEnqueueMutation,
    ) -> TaskJob:
        if self._task_repository is None:
            raise RuntimeError("ExecutionStateCoordinator 未配置 Task persistence。")
        previous = self._task_repository.load_compare_task(candidate.task_id)
        self._task_repository.update_compare_task(
            candidate.task_id,
            lambda task: task_mutation(task, candidate),
        )
        try:
            return self._persist_then_replace(candidate)
        except BaseException:
            self._task_repository.update_compare_task(
                candidate.task_id,
                lambda task: self._restore_task_snapshot(task, previous),
            )
            raise

    @staticmethod
    def _restore_task_snapshot(task: CompareTask, previous: CompareTask) -> None:
        task.__dict__.clear()
        task.__dict__.update(previous.model_copy(deep=True).__dict__)

    def _replace_snapshot(self, job: TaskJob) -> None:
        replacement = dict(self._snapshots)
        replacement[job.job_id] = job.model_copy(deep=True)
        self._snapshots = MappingProxyType(replacement)

    def _get(self, job_id: str) -> TaskJob:
        self._ensure_repository_namespace()
        try:
            return self._snapshots[job_id]
        except KeyError as exc:
            raise FileNotFoundError(f"任务执行记录不存在: {job_id}") from exc

    def _ensure_repository_namespace(self) -> None:
        namespace = self._storage_namespace()
        if namespace == self._repository_namespace:
            return
        snapshots = self._hydrate_snapshots()
        self._repository_namespace = namespace
        self._snapshots = snapshots

    def _hydrate_snapshots(self) -> Mapping[str, TaskJob]:
        return MappingProxyType({job.job_id: job.model_copy(deep=True) for job in self._repository.list_jobs()})

    def _storage_namespace(self) -> str:
        job_dir = getattr(self._repository, "job_dir", None)
        if job_dir is None:
            repository_settings = getattr(self._repository, "settings", None)
            job_dir = getattr(repository_settings, "tasks_dir", None)
        if job_dir is None:
            return f"repository:{id(self._repository)}"
        return str(Path(job_dir).resolve())

    @property
    def _snapshots(self) -> Mapping[str, TaskJob]:
        return self._process_snapshots[self._repository_namespace]

    @_snapshots.setter
    def _snapshots(self, snapshots: Mapping[str, TaskJob]) -> None:
        self._process_snapshots[self._repository_namespace] = snapshots

    @staticmethod
    def _is_claimable(job: TaskJob, now: str) -> bool:
        if job.status == "QUEUED":
            return not job.next_run_at or job.next_run_at <= now
        if job.status == "RUNNING":
            return bool(job.lease_expires_at and job.lease_expires_at <= now)
        return False

    @staticmethod
    def _is_expired_at_attempt_limit(job: TaskJob, now: str) -> bool:
        return bool(
            job.status == "RUNNING"
            and job.lease_expires_at
            and job.lease_expires_at <= now
            and job.attempt >= job.max_attempts
        )

    @classmethod
    def _is_expired_cancel_request(cls, job: TaskJob, now: str) -> bool:
        return bool(job.status == "CANCEL_REQUESTED" and cls._lease_expired(job, now))

    @staticmethod
    def _lease_expired(job: TaskJob, now: str) -> bool:
        return bool(job.lease_expires_at and job.lease_expires_at <= now)

    @classmethod
    def _has_same_identity(cls, existing: TaskJob, candidate: TaskJob) -> bool:
        return bool(existing.job_id == candidate.job_id and cls._has_same_execution_identity(existing, candidate))

    @staticmethod
    def _has_same_execution_identity(existing: TaskJob, candidate: TaskJob) -> bool:
        return bool(
            existing.task_id == candidate.task_id
            and existing.task_type == candidate.task_type
            and existing.execution_no == candidate.execution_no
        )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _plus_seconds(seconds: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _copy_processing_result(target: CompareTask, source: CompareTask) -> None:
    target.original_filename = source.original_filename
    target.compare_filename = source.compare_filename
    target.original_pdf_path = source.original_pdf_path
    target.compare_pdf_path = source.compare_pdf_path
    target.original_highlight_pdf_path = source.original_highlight_pdf_path
    target.compare_highlight_pdf_path = source.compare_highlight_pdf_path
    target.extractor_used = source.extractor_used
    target.ocr_raw_result_path = source.ocr_raw_result_path
    target.ocr_raw_result_paths = source.ocr_raw_result_paths
    target.parse_warnings = source.parse_warnings
    target.parse_warning_details = source.parse_warning_details
    target.document_profiles = source.document_profiles
    target.ocr_quality_summary = source.ocr_quality_summary
    target.ocr_remediation_summary = source.ocr_remediation_summary
    target.debug_artifact_paths = source.debug_artifact_paths
    target.diff_count = source.diff_count
    target.audit_item_reviews = source.audit_item_reviews
    target.diffs = _merge_review_state(target.diffs, source.diffs)
    target.metrics = source.metrics


def _merge_review_state(existing: list[DiffItem], incoming: list[DiffItem]) -> list[DiffItem]:
    existing_by_id = {diff.diff_id: diff for diff in existing}
    merged: list[DiffItem] = []
    for diff in incoming:
        previous = existing_by_id.get(diff.diff_id)
        if previous is None or not _has_review_state(previous):
            merged.append(diff)
            continue
        merged.append(
            diff.model_copy(
                update={
                    "review_status": previous.review_status,
                    "review_comment": previous.review_comment,
                    "reviewed_by": previous.reviewed_by,
                    "reviewed_at": previous.reviewed_at,
                }
            )
        )
    return merged


def _has_review_state(diff: DiffItem) -> bool:
    return (
        diff.review_status != "UNREVIEWED"
        or bool(diff.review_comment)
        or bool(diff.reviewed_by)
        or bool(diff.reviewed_at)
    )
