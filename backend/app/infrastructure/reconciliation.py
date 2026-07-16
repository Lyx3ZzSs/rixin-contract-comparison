from __future__ import annotations

import logging

from app.infrastructure.execution_state import ExecutionStateCoordinator
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
