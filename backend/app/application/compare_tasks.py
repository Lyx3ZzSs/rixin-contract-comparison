from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

from app.errors import ConflictError, NotFoundError
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
    ) -> CompareTask:
        task = CompareTask(
            task_id=task_id,
            stage="排队中",
            progress_percent=3,
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
    ) -> None:
        options = compare_options or CompareOptions()
        self.runner.submit(
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
        )

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
        job = jobs[0]
        if job.status == "CANCELLED":
            self._mark_cancelled(task_id)
        elif job.status == "CANCEL_REQUESTED":
            self._mark_cancel_requested(task_id)
        return job

    def retry_compare(self, task_id: str) -> TaskJob:
        task = self.load_compare_task(task_id)
        if task.status != "FAILED":
            raise ConflictError("只有失败的对比任务可以重试。")
        job = self.runner.retry(task_id, task_type="compare")
        self._mark_retry_queued(task)
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

    def _run_compare_job(self, payload: Mapping[str, Any]) -> None:
        self._run_compare_task(
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
        original_path: Path,
        compare_path: Path,
        task_id: str,
        original_filename: str | None,
        compare_filename: str | None,
        compare_options: CompareOptions | None = None,
    ) -> None:
        try:
            CompareService(repository=self.repository).compare(
                original_path,
                compare_path,
                task_id=task_id,
                original_filename=original_filename,
                compare_filename=compare_filename,
                compare_options=compare_options,
            )
        except Exception:
            logger.exception("Background compare task failed: %s", task_id)
            raise

    def _mark_cancelled(self, task_id: str) -> None:
        def mutate(task: CompareTask) -> None:
            task.status = "FAILED"
            task.stage = "已取消"
            task.progress_percent = 100
            if "任务已取消。" not in task.errors:
                task.errors.append("任务已取消。")

        self.repository.update_compare_task(task_id, mutate)

    def _mark_cancel_requested(self, task_id: str) -> None:
        def mutate(task: CompareTask) -> None:
            if task.status == "PROCESSING":
                task.stage = "取消请求已提交"

        self.repository.update_compare_task(task_id, mutate)

    def _mark_retry_queued(self, task: CompareTask) -> None:
        def mutate(persisted: CompareTask) -> None:
            persisted.status = "PROCESSING"
            persisted.stage = "排队中"
            persisted.progress_percent = 3
            persisted.errors = []

        self.repository.update_compare_task(task.task_id, mutate)


default_compare_task_application = CompareTaskApplication()
