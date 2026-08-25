from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from fastapi import UploadFile

from app.auth.models import CurrentUser
from app.errors import TaskTransitionConflict
from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.infrastructure.report_store import ReportStore
from app.infrastructure.task_repository import SQLiteTaskRepository, default_task_repository
from app.infrastructure.task_runner import (
    QueuedTaskRunner,
    TaskExecutionContext,
    TaskJob,
    default_task_runner,
)
from app.models import CompareOptions, CompareTask, DiffItem, ReviewStatus
from app.services.audit_summary import AuditItem
from app.services.compare_service import CompareService
from app.services.review_service import CompareReviewService
from app.utils.file_utils import safe_filename, stream_upload_to_path, validate_pdf_path

logger = logging.getLogger(__name__)


def requires_diagnostic_retention(task: CompareTask) -> bool:
    quality = task.ocr_quality_summary
    return task.status == "FAILED" or bool(quality and (quality.requires_review or quality.status != "OK"))


class CompareTaskApplication:
    def __init__(
        self,
        repository: SQLiteTaskRepository = default_task_repository,
        runner: QueuedTaskRunner = default_task_runner,
        artifact_store: ArtifactStore = default_artifact_store,
        report_store: ReportStore | None = None,
        **_: object,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self.runner.repository = repository
        self.artifact_store = artifact_store
        self.report_store = report_store or ReportStore(artifact_store=artifact_store)
        self.runner.register_handler("compare", self._run_compare_job)

    async def submit_uploads(
        self,
        *,
        task_id: str,
        original_file: UploadFile,
        compare_file: UploadFile,
        compare_options: CompareOptions | None,
        owner: CurrentUser,
    ) -> CompareTask:
        original_filename = safe_filename(original_file.filename or "")
        compare_filename = safe_filename(compare_file.filename or "")
        attempt_id = uuid.uuid4().hex
        self.create_queued_task(
            task_id=task_id,
            original_path=self.artifact_store.upload_path(task_id, "original", original_filename),
            compare_path=self.artifact_store.upload_path(task_id, "compare", compare_filename),
            original_filename=original_filename,
            compare_filename=compare_filename,
            compare_options=compare_options,
            owner=owner,
        )
        staged_original = self.artifact_store.staging_path(task_id, attempt_id, "original", original_filename)
        staged_compare = self.artifact_store.staging_path(task_id, attempt_id, "compare", compare_filename)
        try:
            await stream_upload_to_path(original_file, staged_original)
            await stream_upload_to_path(compare_file, staged_compare)
            validate_pdf_path(staged_original, original_filename)
            validate_pdf_path(staged_compare, compare_filename)

            original_path = self.artifact_store.upload_path(task_id, "original", original_filename)
            compare_path = self.artifact_store.upload_path(task_id, "compare", compare_filename)
            if original_path.exists() or compare_path.exists():
                raise FileExistsError("任务输入文件已存在。")
            self.artifact_store.publish_staged(staged_original, original_path)
            self.artifact_store.publish_staged(staged_compare, compare_path)
            self.artifact_store.remove_staging_attempt(task_id, attempt_id)
            self.submit_compare(
                original_path=original_path,
                compare_path=compare_path,
                task_id=task_id,
                original_filename=original_filename,
                compare_filename=compare_filename,
                compare_options=compare_options,
            )
            return self.repository.load_compare_task(task_id)
        except Exception as exc:
            self._record_submission_failure(task_id, exc)
            raise

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
            stage="接收文件",
            progress_percent=1,
            owner_sub=owner.sub,
            owner_username=owner.preferred_username,
            owner_display_name=owner.display_name,
            owner_department_code=owner.department_code,
            owner_department_name=owner.department_name,
            original_filename=original_filename,
            compare_filename=compare_filename,
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
        return self.runner.submit(
            task_type="compare",
            task_id=task_id,
            payload=self._build_compare_payload(
                task_id=task_id,
                original_path=original_path,
                compare_path=compare_path,
                original_filename=original_filename or original_path.name,
                compare_filename=compare_filename or compare_path.name,
                compare_options=compare_options or CompareOptions(),
            ),
        )

    def load_compare_task(self, task_id: str) -> CompareTask:
        return self.repository.load_compare_task(task_id)

    def list_compare_tasks(self) -> list[CompareTask]:
        return self.repository.list_compare_tasks()

    def list_compare_record_summaries(self) -> list[dict[str, Any]]:
        return self.repository.list_compare_record_summaries()

    def load_execution(self, task_id: str) -> TaskJob:
        return self.runner.load_active_job(task_id)

    def cancel_compare(self, task_id: str) -> TaskJob:
        return self.runner.cancel_active_task(task_id)

    def retry_compare(self, task_id: str) -> TaskJob:
        task = self.load_compare_task(task_id)
        if not self.is_retry_eligible(task):
            raise TaskTransitionConflict(f"任务 {task.task_id} 不允许从 {task.status}/{task.terminal_reason} 重试。")
        return self.runner.submit(
            task_type="compare",
            task_id=task.task_id,
            payload=self._build_compare_payload(
                task_id=task.task_id,
                original_path=Path(task.original_pdf_path),
                compare_path=Path(task.compare_pdf_path),
                original_filename=task.original_filename,
                compare_filename=task.compare_filename,
                compare_options=task.compare_options,
            ),
        )

    @staticmethod
    def is_retry_eligible(task: CompareTask) -> bool:
        return bool(
            task.status == "FAILED"
            and task.terminal_reason in {"EXECUTION_FAILED", "SUBMISSION_FAILED"}
            and Path(task.original_pdf_path).is_file()
            and Path(task.compare_pdf_path).is_file()
        )

    def ensure_report(self, task: CompareTask) -> CompareTask:
        return CompareService(
            repository=self.repository,
            artifact_store=self.artifact_store,
            report_store=self.report_store,
        ).ensure_report(task)

    def update_diff_review(
        self,
        task: CompareTask,
        diff_id: str,
        review_status: ReviewStatus,
        review_comment: str | None = None,
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
        review_comment: str | None = None,
        reviewed_by: str = "",
    ) -> tuple[CompareTask, AuditItem]:
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
        task = CompareService(
            repository=self.repository,
            artifact_store=self.artifact_store,
            report_store=self.report_store,
        ).compare(
            Path(str(payload["original_path"])),
            Path(str(payload["compare_path"])),
            task_id=str(payload["task_id"]),
            original_filename=str(payload.get("original_filename") or ""),
            compare_filename=str(payload.get("compare_filename") or ""),
            compare_options=CompareOptions.model_validate(payload.get("compare_options") or {}),
            execution_context=execution_context,
        )
        if not requires_diagnostic_retention(task):
            self.artifact_store.remove_diagnostics(task.task_id)
        return task

    def _record_submission_failure(self, task_id: str, exc: Exception) -> None:
        message = str(exc) or type(exc).__name__

        def failed(task: CompareTask) -> None:
            now = datetime.now(UTC).isoformat()
            task.status = "FAILED"
            task.terminal_reason = "SUBMISSION_FAILED"
            task.stage = "提交失败"
            task.progress_percent = 100
            if task.execution_no == 0:
                task.execution_no = 1
                task.execution_id = f"compare:{task.task_id}:1"
                task.execution_queued_at = task.created_at
            task.execution_status = "FAILED"
            task.execution_finished_at = now
            task.execution_error_code = type(exc).__name__
            task.execution_last_error = message
            if message not in task.errors:
                task.errors.append(message)

        try:
            self.repository.update_compare_task(task_id, failed)
        except Exception:
            logger.exception("Submission failure state persistence failed: task_id=%s", task_id)

    @staticmethod
    def _build_compare_payload(
        *,
        task_id: str,
        original_path: Path,
        compare_path: Path,
        original_filename: str,
        compare_filename: str,
        compare_options: CompareOptions,
    ) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "original_path": str(original_path),
            "compare_path": str(compare_path),
            "original_filename": original_filename,
            "compare_filename": compare_filename,
            "compare_options": compare_options.model_dump(mode="json"),
        }


default_compare_task_application = CompareTaskApplication()
