from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Literal, Mapping

from app.auth.models import CurrentUser
from app.errors import TaskStaleLeaseError, TaskTransitionConflict
from fastapi import UploadFile

from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.infrastructure.execution_state import TaskExecutionContext
from app.infrastructure.recovery_store import (
    RecoveryAction,
    RecoveryMarker,
    RecoveryStore,
    default_recovery_store,
)
from app.infrastructure.task_repository import TaskRepository, default_task_repository
from app.infrastructure.task_runner import QueuedTaskRunner, TaskJob, default_task_runner
from app.models import CompareOptions, CompareTask, DiffItem, ReviewStatus
from app.services.audit_summary import AuditItem
from app.services.compare_service import CompareService
from app.services.review_service import CompareReviewService
from app.utils.file_utils import safe_filename, stream_upload_to_path, validate_pdf_path

logger = logging.getLogger(__name__)


class CompareTaskApplication:
    def __init__(
        self,
        repository: TaskRepository = default_task_repository,
        runner: QueuedTaskRunner = default_task_runner,
        artifact_store: ArtifactStore = default_artifact_store,
        recovery_store: RecoveryStore = default_recovery_store,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self.artifact_store = artifact_store
        self.recovery_store = recovery_store
        from app.services.progress_bus import ProgressBus

        self.runner.coordinator.configure_terminal_commits(repository, ProgressBus.get_instance())
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
        attempt_id = uuid.uuid4().hex
        original_filename = safe_filename(original_file.filename or "original.pdf")
        compare_filename = safe_filename(compare_file.filename or "compare.pdf")
        staged_original = self.artifact_store.staging_path(
            task_id,
            attempt_id,
            "original",
            original_filename,
        )
        staged_compare = self.artifact_store.staging_path(
            task_id,
            attempt_id,
            "compare",
            compare_filename,
        )
        attempt_dir = staged_original.parent
        staged_actions = [
            RecoveryAction(action="unlink", path=str(staged_original)),
            RecoveryAction(action="unlink", path=str(staged_compare)),
            RecoveryAction(action="rmdir", path=str(attempt_dir)),
        ]
        final_actions: list[RecoveryAction] = []
        task_persisted = False

        try:
            await stream_upload_to_path(original_file, staged_original)
            await stream_upload_to_path(compare_file, staged_compare)
            validate_pdf_path(staged_original, original_file.filename or "")
            validate_pdf_path(staged_compare, compare_file.filename or "")

            original_path = self.artifact_store.upload_path(
                task_id,
                "original",
                original_filename,
            )
            compare_path = self.artifact_store.upload_path(
                task_id,
                "compare",
                compare_filename,
            )
            self._ensure_publish_destinations_absent(original_path, compare_path)

            self.artifact_store.publish_staged(staged_original, original_path)
            final_actions.append(RecoveryAction(action="unlink", path=str(original_path), scope="final_input"))
            self.artifact_store.publish_staged(staged_compare, compare_path)
            final_actions.append(RecoveryAction(action="unlink", path=str(compare_path), scope="final_input"))

            task = self.create_queued_task(
                task_id=task_id,
                original_path=original_path,
                compare_path=compare_path,
                original_filename=original_file.filename or original_filename,
                compare_filename=compare_file.filename or compare_filename,
                compare_options=compare_options,
                owner=owner,
            )
            task_persisted = True
            try:
                self.submit_compare(
                    original_path=original_path,
                    compare_path=compare_path,
                    task_id=task_id,
                    original_filename=original_file.filename,
                    compare_filename=compare_file.filename,
                    compare_options=compare_options,
                )
            except BaseException as launch_error:
                if not self._has_durable_active_job(task_id):
                    raise
                logger.critical(
                    "Worker launch failed after durable submission; queued Job retained: task_id=%s launch_error=%s",
                    task_id,
                    self._error_text(launch_error),
                    exc_info=True,
                )
        except BaseException as primary:
            if task_persisted:
                self._record_submission_failure(task_id, primary)
                actions = staged_actions
            else:
                actions = [*reversed(final_actions), *staged_actions]
            self._compensate_submission(
                task_id=task_id,
                attempt_id=attempt_id,
                primary=primary,
                actions=actions,
            )
            raise

        self._cleanup_successful_submission(
            task_id=task_id,
            attempt_id=attempt_id,
            actions=staged_actions,
        )
        return task

    @staticmethod
    def _ensure_publish_destinations_absent(*paths: Path) -> None:
        existing = next((path for path in paths if path.exists()), None)
        if existing is not None:
            raise FileExistsError(f"任务输入文件已存在: {existing.name}")

    def _record_submission_failure(self, task_id: str, primary: BaseException) -> None:
        primary_error = self._error_text(primary)

        def mutate(task: CompareTask) -> None:
            if task.status != "PROCESSING":
                return
            task.ensure_transition_allowed(
                "FAILED",
                terminal_reason="SUBMISSION_FAILED",
                job_id=task.active_job_id,
            )
            task.status = "FAILED"
            task.terminal_reason = "SUBMISSION_FAILED"
            task.active_job_id = ""
            task.terminal_job_id = ""
            task.terminal_attempt = 0
            task.stage = "提交失败"
            task.progress_percent = 100
            if primary_error not in task.errors:
                task.errors.append(primary_error)

        try:
            self.repository.update_compare_task(task_id, mutate)
        except BaseException as secondary:
            logger.error(
                "Submission failure state persistence failed: task_id=%s primary_error=%s secondary_error=%s",
                task_id,
                primary_error,
                self._error_text(secondary),
                exc_info=True,
            )

    def _has_durable_active_job(self, task_id: str) -> bool:
        try:
            task = self.repository.load_compare_task(task_id)
            if task.status != "PROCESSING" or not task.active_job_id:
                return False
            job = self.runner.load_job(task.active_job_id)
        except Exception:
            return False
        return (
            job.task_id == task_id
            and job.task_type == "compare"
            and job.status
            not in {
                "SUCCEEDED",
                "FAILED",
                "CANCELLED",
            }
        )

    def _compensate_submission(
        self,
        *,
        task_id: str,
        attempt_id: str,
        primary: BaseException,
        actions: list[RecoveryAction],
    ) -> None:
        primary_error = self._error_text(primary)
        try:
            marker = self.recovery_store.create_marker(
                task_id=task_id,
                attempt_id=attempt_id,
                primary_error=primary_error,
                actions=actions,
            )
        except BaseException as marker_error:
            logger.critical(
                "Submission recovery marker creation failed: task_id=%s attempt_id=%s "
                "primary_error=%s actions=%s marker_error=%s",
                task_id,
                attempt_id,
                primary_error,
                [action.model_dump(mode="json") for action in actions],
                self._error_text(marker_error),
                exc_info=True,
            )
            marker = RecoveryMarker(
                task_id=task_id,
                attempt_id=attempt_id,
                primary_error=primary_error,
                actions=actions,
            )
            self.recovery_store.cleanup_attempt(marker)
            return
        self.recovery_store.recover_marker(marker)

    def _cleanup_successful_submission(
        self,
        *,
        task_id: str,
        attempt_id: str,
        actions: list[RecoveryAction],
    ) -> None:
        marker = RecoveryMarker(
            task_id=task_id,
            attempt_id=attempt_id,
            primary_error="post-submit staging cleanup",
            actions=actions,
        )
        self.recovery_store.cleanup_attempt(marker)

    @staticmethod
    def _error_text(exc: BaseException) -> str:
        return str(exc) or type(exc).__name__

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
        job = self.runner.submit(
            task_type="compare",
            task_id=task_id,
            payload=self._build_compare_payload(
                task_id=task_id,
                original_path=original_path,
                compare_path=compare_path,
                original_filename=original_filename or "",
                compare_filename=compare_filename or "",
                compare_options=compare_options or CompareOptions(),
            ),
            task_mutation=self._mark_active_job,
        )
        return job

    def load_compare_task(self, task_id: str) -> CompareTask:
        return self.repository.load_compare_task(task_id)

    def list_compare_tasks(self) -> list[CompareTask]:
        return self.repository.list_compare_tasks()

    def load_execution(self, task_id: str) -> TaskJob:
        return self.runner.load_active_job(task_id, task_type="compare")

    def cancel_compare(self, task_id: str) -> TaskJob:
        return self.runner.cancel_active_task(task_id, task_type="compare")

    def retry_compare(self, task_id: str) -> TaskJob:
        task = self.load_compare_task(task_id)
        retry_mode = self._ensure_retry_eligible(task)
        source_job = self._retry_source_job(task) if retry_mode == "RETRY_FAILED_JOB" else None
        if retry_mode == "RETRY_FAILED_JOB" and source_job is None:
            raise TaskTransitionConflict(f"任务 {task.task_id} 的失败执行记录与终态绑定不一致。")

        def mark_retry_queued(persisted: CompareTask, job: TaskJob) -> None:
            self._ensure_retry_eligible(persisted)
            if source_job is not None:
                persisted_source = self._retry_source_job(persisted)
                if persisted_source is None or persisted_source.job_id != source_job.job_id:
                    raise TaskTransitionConflict(f"任务 {persisted.task_id} 的失败执行记录已发生变化。")
            persisted.status = "PROCESSING"
            persisted.terminal_reason = "NONE"
            persisted.active_job_id = job.job_id
            persisted.stage = "排队中"
            persisted.progress_percent = 3
            persisted.errors = []

        if retry_mode == "REBUILD_SUBMISSION":
            job = self.runner.submit(
                task_type="compare",
                task_id=task_id,
                payload=self._build_compare_payload(
                    task_id=task.task_id,
                    original_path=Path(task.original_pdf_path),
                    compare_path=Path(task.compare_pdf_path),
                    original_filename=task.original_filename,
                    compare_filename=task.compare_filename,
                    compare_options=task.compare_options,
                ),
                task_mutation=mark_retry_queued,
                reject_existing=True,
            )
        else:
            job = self.runner.retry(
                task_id,
                task_type="compare",
                source_job_id=source_job.job_id if source_job is not None else None,
                task_mutation=mark_retry_queued,
            )
        return job

    def is_retry_eligible(self, task: CompareTask) -> bool:
        return self._retry_mode(task) is not None

    def _ensure_retry_eligible(self, task: CompareTask) -> Literal["RETRY_FAILED_JOB", "REBUILD_SUBMISSION"]:
        retry_mode = self._retry_mode(task)
        if retry_mode is None:
            raise TaskTransitionConflict(f"任务 {task.task_id} 不允许从 {task.status}/{task.terminal_reason} 重试。")
        return retry_mode

    def _retry_mode(self, task: CompareTask) -> Literal["RETRY_FAILED_JOB", "REBUILD_SUBMISSION"] | None:
        if not (Path(task.original_pdf_path).is_file() and Path(task.compare_pdf_path).is_file()):
            return None
        if task.status != "FAILED" or task.terminal_reason not in {"EXECUTION_FAILED", "SUBMISSION_FAILED"}:
            return None
        jobs = self.runner.jobs_for_task(task.task_id, task_type="compare")
        if any(job.status not in {"SUCCEEDED", "FAILED", "CANCELLED"} for job in jobs):
            return None
        if not jobs:
            return "REBUILD_SUBMISSION" if task.terminal_reason == "SUBMISSION_FAILED" else None
        return "RETRY_FAILED_JOB" if self._retry_source_job(task, jobs=jobs) is not None else None

    def _retry_source_job(self, task: CompareTask, *, jobs: list[TaskJob] | None = None) -> TaskJob | None:
        task_jobs = jobs if jobs is not None else self.runner.jobs_for_task(task.task_id, task_type="compare")
        if not task_jobs:
            return None
        if task.terminal_job_id:
            source = next((job for job in task_jobs if job.job_id == task.terminal_job_id), None)
            if source is None:
                return None
            if (
                source.task_id != task.task_id
                or source.task_type != "compare"
                or source.attempt != task.terminal_attempt
                or source.status != "FAILED"
            ):
                return None
            return source
        if task.terminal_attempt:
            return None
        latest = max(task_jobs, key=lambda job: job.execution_no)
        return latest if latest.status == "FAILED" else None

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
            "compare_options": compare_options.model_dump(),
        }

    def ensure_report(self, task: CompareTask) -> CompareTask:
        return CompareService(repository=self.repository).ensure_report(task)

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
