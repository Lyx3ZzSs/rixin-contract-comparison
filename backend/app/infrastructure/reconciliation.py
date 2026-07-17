from __future__ import annotations

import logging

from app.infrastructure.execution_state import ExecutionStateCoordinator
from app.infrastructure.recovery_store import RecoveryStore
from app.infrastructure.task_runner import TERMINAL_JOB_STATUSES
from app.infrastructure.task_repository import TaskRepository

logger = logging.getLogger(__name__)


def reconcile_terminal_jobs(
    task_repository: TaskRepository,
    coordinator: ExecutionStateCoordinator,
) -> int:
    """Repair only the Task-terminal/Job-nonterminal crash window."""

    repaired = 0
    for task in task_repository.list_compare_tasks():
        if task.status not in {"COMPLETED", "FAILED"} or not task.terminal_job_id:
            continue
        if coordinator.repair_job_from_terminal_task(task):
            repaired += 1
            logger.warning(
                "Reconciled terminal task job: task_id=%s job_id=%s attempt=%s",
                task.task_id,
                task.terminal_job_id,
                task.terminal_attempt,
            )
    return repaired


def reconcile_startup(
    task_repository: TaskRepository,
    coordinator: ExecutionStateCoordinator,
    *,
    recovery_store: RecoveryStore | None = None,
    pre_start: bool = True,
) -> int:
    """Repair durable Task/Job crash windows before worker threads are started."""
    coordinator.reload_from_storage()
    repaired = reconcile_terminal_jobs(task_repository, coordinator)
    tasks = {task.task_id: task for task in task_repository.list_compare_tasks()}

    for task in tasks.values():
        if task.status != "PROCESSING":
            continue
        try:
            active = coordinator.load(task.active_job_id) if task.active_job_id else None
        except FileNotFoundError:
            active = None
        if (
            active is not None
            and active.task_id == task.task_id
            and active.task_type == "compare"
            and active.status not in TERMINAL_JOB_STATUSES
            and not active.duplicate_execution
            and active.error_code != "DUPLICATE_JOB_EXECUTION"
        ):
            continue
        error = "活动执行记录缺失或身份不匹配。"
        if coordinator.reconcile_submission_failure(task.task_id, error=error):
            repaired += 1
            if recovery_store is not None:
                recovery_store.create_marker(
                    task_id=task.task_id,
                    attempt_id=f"reconcile-{task.active_job_id or 'missing'}",
                    primary_error=error,
                    actions=[],
                )

    tasks = {task.task_id: task for task in task_repository.list_compare_tasks()}
    for job in coordinator.list_persisted_jobs():
        if job.duplicate_execution or job.error_code == "DUPLICATE_JOB_EXECUTION":
            if coordinator.reconcile_duplicate_execution(job):
                repaired += 1
            continue
        task = tasks.get(job.task_id)
        if job.status not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            references_job = task is not None and job.job_id in {task.active_job_id, task.terminal_job_id}
            if not references_job:
                if coordinator.reconcile_orphan_job(job):
                    repaired += 1
                continue
        if pre_start and task is not None and task.status == "PROCESSING" and task.active_job_id == job.job_id:
            if coordinator.reconcile_stale_running_job(job):
                repaired += 1
    return repaired
