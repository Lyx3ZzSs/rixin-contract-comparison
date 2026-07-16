from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

from app.auth.models import CurrentUser
from app.errors import NotFoundError, TaskStaleLeaseError
from app.infrastructure.execution_state import TaskExecutionContext
from app.infrastructure.task_repository import TaskRepository, default_task_repository
from app.infrastructure.task_runner import QueuedTaskRunner, TaskJob, default_task_runner
from app.models import AuditItemReview, CompareOptions, CompareTask, DiffItem, ReviewStatus
from app.services.compare_service import CompareService
from app.services.review_service import CompareReviewService

logger = logging.getLogger(__name__)


class CompareTaskApplication:
    def __init__(
        self,
        repository: TaskRepository = default_task_repository,
        runner: QueuedTaskRunner = default_task_runner,
    ) -> None:
        self.repository = repository
        self.runner = runner
        from app.services.progress_bus import ProgressBus

        self.runner.coordinator.configure_terminal_commits(repository, ProgressBus.get_instance())
        self.runner.register_handler("compare", self._run_compare_job)

    def create_queued_task(
        self,
        *,
        task_id: str,
        original_path: Path,
        compare_path: Path,
        original_filename: str,
        compare_filename: str,
        compare_options: CompareOptions | None = None,
        owner: CurrentUser,
    ) -> CompareTask:
        task = CompareTask(
            task_id=task_id,
            stage="排队中",
            progress_percent=3,
            owner_sub=owner.sub,
            owner_username=owner.preferred_username,
            owner_display_name=owner.display_name,
            owner_department_code=owner.department_code,
            owner_department_name=owner.department_name,
            original_filename=original_filename or original_path.name,
            compare_filename=compare_filename or compare_path.name,
            original_pdf_path=str(original_path),
            compare_pdf_path=str(compare_path),
            compare_options=compare_options or CompareOptions(),
        )
        self.repository.save_compare_task(task)
        return task

    def submit_compare(
        self,
        *,
        original_path: Path,
        compare_path: Path,
        task_id: str,
        original_filename: str | None,
        compare_filename: str | None,
        compare_options: CompareOptions | None = None,
    ) -> TaskJob:
        options = compare_options or CompareOptions()
        job = self.runner.submit(
            task_type="compare",
            task_id=task_id,
            payload={
                "task_id": task_id,
                "original_path": str(original_path),
                "compare_path": str(compare_path),
                "original_filename": original_filename or "",
                "compare_filename": compare_filename or "",
                "compare_options": options.model_dump(),
            },
            task_mutation=self._mark_active_job,
        )
        return job

    def load_compare_task(self, task_id: str) -> CompareTask:
        return self.repository.load_compare_task(task_id)

    def list_compare_tasks(self) -> list[CompareTask]:
        return self.repository.list_compare_tasks()

    def load_execution(self, task_id: str) -> TaskJob:
        self.load_compare_task(task_id)
        return self.runner.latest_job(task_id, task_type="compare")

    def cancel_compare(self, task_id: str) -> TaskJob:
        self.load_compare_task(task_id)
        jobs = self.runner.cancel(task_id, task_type="compare")
        if not jobs:
            raise NotFoundError(f"任务执行记录不存在: {task_id}")
        return jobs[0]

    def retry_compare(self, task_id: str) -> TaskJob:
        task = self.load_compare_task(task_id)
        validated_inputs_exist = Path(task.original_pdf_path).is_file() and Path(task.compare_pdf_path).is_file()
        task.ensure_transition_allowed(
            "PROCESSING",
            validated_inputs_exist=validated_inputs_exist,
        )

        def mark_retry_queued(persisted: CompareTask, job: TaskJob) -> None:
            persisted.ensure_transition_allowed(
                "PROCESSING",
                validated_inputs_exist=validated_inputs_exist,
            )
            persisted.status = "PROCESSING"
            persisted.terminal_reason = "NONE"
            persisted.active_job_id = job.job_id
            persisted.stage = "排队中"
            persisted.progress_percent = 3
            persisted.errors = []

        job = self.runner.retry(
            task_id,
            task_type="compare",
            task_mutation=mark_retry_queued,
        )
        return job

    def ensure_report(self, task: CompareTask) -> CompareTask:
        return CompareService(repository=self.repository).ensure_report(task)

    def update_diff_review(
        self,
        task: CompareTask,
        diff_id: str,
        review_status: ReviewStatus,
        review_comment: str = "",
        reviewed_by: str = "",
    ) -> tuple[CompareTask, DiffItem]:
        return CompareReviewService(repository=self.repository).update_diff_review(
            task,
            diff_id,
            review_status,
            review_comment,
            reviewed_by,
        )

    def update_audit_item_review(
        self,
        task: CompareTask,
        audit_item_id: str,
        review_status: ReviewStatus,
        review_comment: str = "",
        reviewed_by: str = "",
    ) -> tuple[CompareTask, AuditItemReview]:
        return CompareReviewService(repository=self.repository).update_audit_item_review(
            task,
            audit_item_id,
            review_status,
            review_comment,
            reviewed_by,
        )

    def _run_compare_job(
        self,
        execution_context: TaskExecutionContext,
        payload: Mapping[str, Any],
    ) -> CompareTask:
        return self._run_compare_task(
            execution_context=execution_context,
            original_path=Path(str(payload["original_path"])),
            compare_path=Path(str(payload["compare_path"])),
            task_id=str(payload["task_id"]),
            original_filename=str(payload.get("original_filename") or ""),
            compare_filename=str(payload.get("compare_filename") or ""),
            compare_options=CompareOptions.model_validate(payload.get("compare_options") or {}),
        )

    def _run_compare_task(
        self,
        *,
        execution_context: TaskExecutionContext,
        original_path: Path,
        compare_path: Path,
        task_id: str,
        original_filename: str | None,
        compare_filename: str | None,
        compare_options: CompareOptions | None = None,
    ) -> CompareTask:
        try:
            return CompareService(repository=self.repository).compare(
                original_path,
                compare_path,
                task_id=task_id,
                original_filename=original_filename,
                compare_filename=compare_filename,
                compare_options=compare_options,
                execution_context=execution_context,
            )
        except TaskStaleLeaseError:
            logger.warning(
                "Background compare task lost lease: task_id=%s job_id=%s worker_id=%s",
                task_id,
                execution_context.job_id,
                execution_context.worker_id,
            )
            raise
        except Exception:
            logger.exception("Background compare task failed: %s", task_id)
            raise

    @staticmethod
    def _mark_active_job(task: CompareTask, job: TaskJob) -> None:
        task.active_job_id = job.job_id


default_compare_task_application = CompareTaskApplication()
