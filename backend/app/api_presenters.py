from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path

from app.api_schemas import (
    AuditItemResponse,
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
from app.models import CompareTask
from app.services.audit_summary import AuditItem, build_task_audit_items, project_diff_reviews
from app.services.report_generator import build_report_filename
from app.utils.json_utils import to_jsonable


def compare_task_response(task: CompareTask, *, retry_eligible: bool = False) -> CompareTaskResponse:
    audit_items = build_task_audit_items(task)
    review_counts = Counter(item.review_status for item in audit_items)
    data = {
        "task_id": task.task_id,
        "status": task.status,
        "terminal_reason": task.terminal_reason,
        "revision": task.revision,
        "report_revision": task.report_revision,
        "retry_eligible": retry_eligible,
        "stage": task.stage,
        "progress_percent": task.progress_percent,
        "diff_count": task.diff_count,
        "reviewed_count": len([item for item in audit_items if item.review_status != "UNREVIEWED"]),
        "confirmed_count": review_counts["CONFIRMED"],
        "false_positive_count": review_counts["FALSE_POSITIVE"],
        "manual_review_count": review_counts["NEEDS_REVIEW"],
        "ignored_count": review_counts["IGNORED"],
        "audit_item_reviews": {item_id: to_jsonable(review) for item_id, review in task.audit_item_reviews.items()},
        "audit_items": [audit_item_response(item) for item in audit_items],
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


def compare_task_detail_response(task: CompareTask, *, retry_eligible: bool = False) -> CompareTaskDetailResponse:
    data = compare_task_response(task, retry_eligible=retry_eligible).model_dump()
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


def compare_record_summary(task: CompareTask, *, retry_eligible: bool = False) -> CompareRecordResponse:
    return CompareRecordResponse(
        task_id=task.task_id,
        status=task.status,
        terminal_reason=task.terminal_reason,
        revision=task.revision,
        report_revision=task.report_revision,
        retry_eligible=retry_eligible,
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
    retry_eligibility: Callable[[CompareTask], bool] | None = None,
) -> CompareRecordListResponse:
    resolved_total = len(tasks) if total is None else total
    resolved_page_size = len(tasks) if page_size is None else page_size
    resolved_total_pages = total_pages if total_pages is not None else (1 if resolved_total else 0)
    return CompareRecordListResponse(
        records=[
            compare_record_summary(
                task,
                retry_eligible=retry_eligibility(task) if retry_eligibility is not None else False,
            )
            for task in tasks
        ],
        total=resolved_total,
        page=page,
        page_size=resolved_page_size,
        total_pages=resolved_total_pages,
    )


def diff_response(diff) -> CompareDiffResponse:
    data = to_jsonable(diff)
    return CompareDiffResponse(**data)


def compare_diff_list_response(task: CompareTask) -> CompareDiffListResponse:
    projected_diffs = project_diff_reviews(task.diffs, build_task_audit_items(task))
    return CompareDiffListResponse(
        task_id=task.task_id,
        diffs=[diff_response(diff) for diff in projected_diffs],
    )


def artifact_filenames(paths: dict[str, str]) -> dict[str, str]:
    return {name: Path(path).name for name, path in paths.items()}


def review_stats(task: CompareTask) -> ReviewStatsResponse:
    items = build_task_audit_items(task)
    return ReviewStatsResponse(
        total_count=len(items),
        reviewed_count=task.reviewed_count,
        confirmed_count=task.confirmed_count,
        false_positive_count=task.false_positive_count,
        manual_review_count=task.manual_review_count,
        ignored_count=task.ignored_count,
        review_unit="audit_item",
    )


def diff_review_response(task: CompareTask, diff) -> DiffReviewResponse:
    return DiffReviewResponse(
        task_id=task.task_id,
        diff=diff_response(diff),
        review_stats=review_stats(task),
    )


def audit_item_review_response(
    task: CompareTask,
    item: AuditItem,
) -> AuditItemReviewUpdateResponse:
    return AuditItemReviewUpdateResponse(
        task_id=task.task_id,
        audit_item=audit_item_response(item),
        review_stats=review_stats(task),
        report_revision=task.report_revision,
    )


def audit_item_response(item: AuditItem) -> AuditItemResponse:
    return AuditItemResponse(
        audit_item_id=item.item_id,
        diff_id=item.diff_id,
        diff_type=item.diff_type,
        source_type=item.source_type,
        section_type=item.section_type,
        section_path=item.section_path,
        title=item.title,
        summary=item.summary,
        original_text=item.original_text,
        compare_text=item.compare_text,
        original_evidence=[to_jsonable(evidence) for evidence in item.original_evidence],
        compare_evidence=[to_jsonable(evidence) for evidence in item.compare_evidence],
        evidence_state=item.evidence_state,
        quality_status=item.quality_status,
        structural_flags=item.structural_flags,
        review_flags=item.review_flags,
        text_confidence=item.text_confidence,
        match_confidence=item.match_confidence,
        ocr_context={
            "affected": item.ocr_context.affected,
            "statuses": list(item.ocr_context.statuses),
            "reasons": list(item.ocr_context.reasons),
            "sides": list(item.ocr_context.sides),
            "page_numbers": list(item.ocr_context.page_numbers),
        },
        remediation_context={
            "action_ids": list(item.remediation_context.action_ids),
            "action_types": list(item.remediation_context.action_types),
            "statuses": list(item.remediation_context.statuses),
            "changed_evidence": item.remediation_context.changed_evidence,
            "changed_diff_text": item.remediation_context.changed_diff_text,
            "requires_manual_review": item.remediation_context.requires_manual_review,
        },
        review_status=item.review_status,
        review_comment=item.review_comment,
        reviewed_by=item.reviewed_by,
        reviewed_at=item.reviewed_at,
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
