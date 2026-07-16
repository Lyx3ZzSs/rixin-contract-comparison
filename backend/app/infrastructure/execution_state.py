from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, ClassVar, Protocol

from app.errors import TaskCancelled, TaskStaleLeaseError, TaskTransitionConflict

if TYPE_CHECKING:
    from app.infrastructure.task_runner import TaskJob, TaskJobStatus, TaskJobType


class TaskJobPersistence(Protocol):
    @property
    def job_dir(self) -> Path: ...

    def enqueue(self, job: TaskJob) -> TaskJob: ...

    def list_jobs(self) -> list[TaskJob]: ...

    def persist(self, job: TaskJob) -> TaskJob: ...


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

    def __init__(self, repository: TaskJobPersistence) -> None:
        self._repository = repository
        with self._process_lock:
            self._repository_namespace = self._storage_namespace()
            self._snapshots = self._hydrate_snapshots()

    def enqueue(self, job: TaskJob) -> TaskJob:
        with self._process_lock:
            self._ensure_repository_namespace()
            persisted = self._repository.enqueue(job.model_copy(deep=True))
            self._replace_snapshot(persisted)
            return persisted.model_copy(deep=True)

    def load(self, job_id: str) -> TaskJob:
        with self._process_lock:
            return self._get(job_id).model_copy(deep=True)

    def list_jobs(self) -> list[TaskJob]:
        with self._process_lock:
            self._ensure_repository_namespace()
            jobs = [job.model_copy(deep=True) for job in self._snapshots.values()]
        return sorted(jobs, key=lambda job: (job.next_run_at or job.queued_at, job.queued_at))

    def claim_next(self, *, worker_id: str, lease_seconds: int) -> TaskJob | None:
        with self._process_lock:
            now = _utc_now()
            for job in self.list_jobs():
                if self._is_expired_at_attempt_limit(job, now):
                    candidate = job.model_copy(deep=True)
                    candidate.status = "FAILED"
                    candidate.error_code = "LEASE_EXPIRED_MAX_ATTEMPTS"
                    candidate.last_error = "任务租约过期且已达到最大尝试次数。"
                    candidate.finished_at = now
                    candidate.updated_at = now
                    candidate.lease_owner = ""
                    candidate.lease_expires_at = ""
                    self._persist_then_replace(candidate)
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
                return self._persist_then_replace(candidate)
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
            if job.status in {"SUCCEEDED", "FAILED", "CANCELLED", "CANCEL_REQUESTED"}:
                return [job.model_copy(deep=True)]
            candidate = job.model_copy(deep=True)
            now = _utc_now()
            if candidate.status == "QUEUED":
                candidate.status = "CANCELLED"
                candidate.finished_at = now
                candidate.lease_owner = ""
                candidate.lease_expires_at = ""
            elif candidate.status == "RUNNING":
                candidate.status = "CANCEL_REQUESTED"
            else:
                raise TaskTransitionConflict(f"执行记录 {candidate.job_id} 不能从 {candidate.status} 请求取消。")
            candidate.updated_at = now
            return [self._persist_then_replace(candidate)]

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

    def _ensure_current_lease(self, job: TaskJob, worker_id: str) -> None:
        if job.lease_owner != worker_id:
            raise TaskStaleLeaseError(f"执行记录 {job.job_id} 不属于 worker {worker_id}。")
        if not job.lease_expires_at or job.lease_expires_at <= _utc_now():
            raise TaskStaleLeaseError(f"执行记录 {job.job_id} 的 worker lease 已过期。")

    def _persist_then_replace(self, candidate: TaskJob) -> TaskJob:
        persisted = self._repository.persist(candidate.model_copy(deep=True))
        self._replace_snapshot(persisted)
        return persisted.model_copy(deep=True)

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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _plus_seconds(seconds: float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
