from __future__ import annotations

from pathlib import Path

from app.api_schemas import (
    AuditItemReviewResponse,
    AuditItemReviewUpdateResponse,
    CompareDiffListResponse,
    CompareDiffResponse,
    CompareRecordListResponse,
    CompareRecordResponse,
    CompareTaskDetailResponse,
    CompareTaskResponse,
    DiffReviewResponse,
    ReviewStatsResponse,
    TaskExecutionResponse,
)
from app.infrastructure.task_runner import TaskJob
from app.models import AuditItemReview, CompareTask
from app.services.report_generator import build_report_filename
from app.utils.json_utils import to_jsonable


def compare_task_response(task: CompareTask) -> CompareTaskResponse:
    data = {
        "task_id": task.task_id,
        "status": task.status,
        "terminal_reason": task.terminal_reason,
        "revision": task.revision,
        "report_revision": task.report_revision,
        "retry_eligible": task.is_retry_eligible(),
        "stage": task.stage,
        "progress_percent": task.progress_percent,
        "diff_count": task.diff_count,
        "reviewed_count": task.reviewed_count,
        "confirmed_count": task.confirmed_count,
        "false_positive_count": task.false_positive_count,
        "manual_review_count": task.manual_review_count,
        "ignored_count": task.ignored_count,
        "audit_item_reviews": {item_id: to_jsonable(review) for item_id, review in task.audit_item_reviews.items()},
        "extractor_used": task.extractor_used,
        "parse_warnings": task.parse_warnings,
        "parse_warning_details": [to_jsonable(item) for item in task.parse_warning_details],
        "document_profiles": {side: to_jsonable(profile) for side, profile in task.document_profiles.items()},
        "ocr_quality_summary": to_jsonable(task.ocr_quality_summary) if task.ocr_quality_summary else None,
        "ocr_remediation_summary": (
            to_jsonable(task.ocr_remediation_summary) if task.ocr_remediation_summary else None
        ),
        "debug_artifact_paths": artifact_filenames(task.debug_artifact_paths),
        "errors": task.errors,
    }
    data.update(compare_task_artifact_urls(task))
    return CompareTaskResponse(**data)


def compare_task_detail_response(task: CompareTask) -> CompareTaskDetailResponse:
    data = compare_task_response(task).model_dump()
    data.update(
        {
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "original_filename": task.original_filename,
            "compare_filename": task.compare_filename,
        }
    )
    return CompareTaskDetailResponse(**data)


def compare_task_artifact_urls(task: CompareTask) -> dict[str, str]:
    return {
        "original_pdf_url": f"/api/compare/{task.task_id}/original" if task.original_pdf_path else "",
        "compare_pdf_url": f"/api/compare/{task.task_id}/compare" if task.compare_pdf_path else "",
        "report_url": f"/api/compare/{task.task_id}/report" if task.status == "COMPLETED" else "",
        "report_filename": build_report_filename(task),
        "original_highlight_pdf_url": "",
        "compare_highlight_pdf_url": "",
    }


def compare_record_summary(task: CompareTask) -> CompareRecordResponse:
    return CompareRecordResponse(
        task_id=task.task_id,
        status=task.status,
        terminal_reason=task.terminal_reason,
        revision=task.revision,
        report_revision=task.report_revision,
        retry_eligible=task.is_retry_eligible(),
        stage=task.stage,
        progress_percent=task.progress_percent,
        created_at=task.created_at,
        updated_at=task.updated_at,
        original_filename=task.original_filename,
        compare_filename=task.compare_filename,
        diff_count=task.diff_count,
        report_url=f"/api/compare/{task.task_id}/report" if task.status == "COMPLETED" else "",
    )


def compare_record_list_response(
    tasks: list[CompareTask],
    *,
    total: int | None = None,
    page: int = 1,
    page_size: int | None = None,
    total_pages: int | None = None,
) -> CompareRecordListResponse:
    resolved_total = len(tasks) if total is None else total
    resolved_page_size = len(tasks) if page_size is None else page_size
    resolved_total_pages = total_pages if total_pages is not None else (1 if resolved_total else 0)
    return CompareRecordListResponse(
        records=[compare_record_summary(task) for task in tasks],
        total=resolved_total,
        page=page,
        page_size=resolved_page_size,
        total_pages=resolved_total_pages,
    )


def diff_response(diff) -> CompareDiffResponse:
    data = to_jsonable(diff)
    return CompareDiffResponse(**data)


def compare_diff_list_response(task: CompareTask) -> CompareDiffListResponse:
    return CompareDiffListResponse(
        task_id=task.task_id,
        diffs=[diff_response(diff) for diff in task.diffs],
    )


def artifact_filenames(paths: dict[str, str]) -> dict[str, str]:
    return {name: Path(path).name for name, path in paths.items()}


def review_stats(task: CompareTask) -> ReviewStatsResponse:
    return ReviewStatsResponse(
        reviewed_count=task.reviewed_count,
        confirmed_count=task.confirmed_count,
        false_positive_count=task.false_positive_count,
        manual_review_count=task.manual_review_count,
        ignored_count=task.ignored_count,
    )


def diff_review_response(task: CompareTask, diff) -> DiffReviewResponse:
    return DiffReviewResponse(
        task_id=task.task_id,
        diff=diff_response(diff),
        review_stats=review_stats(task),
    )


def audit_item_review_response(
    task: CompareTask,
    audit_item_id: str,
    review: AuditItemReview,
) -> AuditItemReviewUpdateResponse:
    return AuditItemReviewUpdateResponse(
        task_id=task.task_id,
        audit_item_id=audit_item_id,
        audit_item_review=AuditItemReviewResponse(
            audit_item_id=audit_item_id,
            review_status=review.review_status,
            review_comment=review.review_comment,
            reviewed_by=review.reviewed_by,
            reviewed_at=review.reviewed_at,
        ),
        review_stats=review_stats(task),
    )


def task_execution_response(job: TaskJob) -> TaskExecutionResponse:
    return TaskExecutionResponse(
        job_id=job.job_id,
        task_id=job.task_id,
        task_type=job.task_type,
        status=job.status,
        execution_no=job.execution_no,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        queued_at=job.queued_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        updated_at=job.updated_at,
        error_code=job.error_code,
        last_error=job.last_error,
    )
