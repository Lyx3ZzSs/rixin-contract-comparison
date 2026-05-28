from __future__ import annotations

from pathlib import Path

from app.api_schemas import (
    CompareDiffListResponse,
    CompareDiffResponse,
    CompareRecordListResponse,
    CompareRecordResponse,
    CompareTaskDetailResponse,
    CompareTaskResponse,
    DiffReviewResponse,
    ExtractionRecordListResponse,
    ExtractionRecordResponse,
    ExtractionTaskResponse,
    ReviewStatsResponse,
)
from app.models import CompareTask
from app.models_extraction import ExtractionTask
from app.services.report_generator import build_report_filename
from app.utils.json_utils import to_jsonable


def compare_task_response(task: CompareTask) -> CompareTaskResponse:
    data = {
        "task_id": task.task_id,
        "status": task.status,
        "stage": task.stage,
        "progress_percent": task.progress_percent,
        "diff_count": task.diff_count,
        "high_risk_count": task.high_risk_count,
        "medium_risk_count": task.medium_risk_count,
        "low_risk_count": task.low_risk_count,
        "reviewed_count": task.reviewed_count,
        "confirmed_count": task.confirmed_count,
        "false_positive_count": task.false_positive_count,
        "manual_review_count": task.manual_review_count,
        "ignored_count": task.ignored_count,
        "extractor_used": task.extractor_used,
        "parse_warnings": task.parse_warnings,
        "parse_warning_details": [to_jsonable(item) for item in task.parse_warning_details],
        "document_profiles": {side: to_jsonable(profile) for side, profile in task.document_profiles.items()},
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
            "ai_summary": task.ai_summary,
            "report_ai_analysis": to_jsonable(task.report_ai_analysis) if task.report_ai_analysis else None,
            "original_page_screenshots": screenshot_urls(task.task_id, task.original_page_screenshots),
            "compare_page_screenshots": screenshot_urls(task.task_id, task.compare_page_screenshots),
        }
    )
    return CompareTaskDetailResponse(**data)


def compare_task_artifact_urls(task: CompareTask) -> dict[str, str]:
    return {
        "original_pdf_url": f"/api/compare/{task.task_id}/original" if task.original_pdf_path else "",
        "compare_pdf_url": f"/api/compare/{task.task_id}/compare" if task.compare_pdf_path else "",
        "report_url": f"/api/compare/{task.task_id}/report" if task.status == "COMPLETED" else "",
        "report_filename": build_report_filename(task),
        "original_highlight_pdf_url": (
            f"/api/compare/{task.task_id}/highlight/original" if task.original_highlight_pdf_path else ""
        ),
        "compare_highlight_pdf_url": (
            f"/api/compare/{task.task_id}/highlight/compare" if task.compare_highlight_pdf_path else ""
        ),
    }


def compare_record_summary(task: CompareTask) -> CompareRecordResponse:
    return CompareRecordResponse(
        task_id=task.task_id,
        status=task.status,
        stage=task.stage,
        progress_percent=task.progress_percent,
        created_at=task.created_at,
        updated_at=task.updated_at,
        original_filename=task.original_filename,
        compare_filename=task.compare_filename,
        diff_count=task.diff_count,
        high_risk_count=task.high_risk_count,
        medium_risk_count=task.medium_risk_count,
        low_risk_count=task.low_risk_count,
        report_url=f"/api/compare/{task.task_id}/report" if task.status == "COMPLETED" else "",
    )


def compare_record_list_response(tasks: list[CompareTask]) -> CompareRecordListResponse:
    return CompareRecordListResponse(records=[compare_record_summary(task) for task in tasks])


def diff_response(task_id: str, diff) -> CompareDiffResponse:
    data = to_jsonable(diff)
    data["original_screenshot"] = Path(diff.original_screenshot).name if diff.original_screenshot else ""
    data["compare_screenshot"] = Path(diff.compare_screenshot).name if diff.compare_screenshot else ""
    data["original_screenshot_url"] = screenshot_url(task_id, diff.original_screenshot)
    data["compare_screenshot_url"] = screenshot_url(task_id, diff.compare_screenshot)
    return CompareDiffResponse(**data)


def compare_diff_list_response(task: CompareTask) -> CompareDiffListResponse:
    return CompareDiffListResponse(
        task_id=task.task_id,
        diffs=[diff_response(task.task_id, diff) for diff in task.diffs],
    )


def screenshot_url(task_id: str, screenshot_path: str) -> str:
    if not screenshot_path:
        return ""
    return f"/api/compare/{task_id}/screenshot/{Path(screenshot_path).name}"


def screenshot_urls(task_id: str, paths: list[str]) -> list[str]:
    return [url for path in paths if (url := screenshot_url(task_id, path))]


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
        diff=diff_response(task.task_id, diff),
        review_stats=review_stats(task),
    )


def extraction_task_response(task: ExtractionTask) -> ExtractionTaskResponse:
    return ExtractionTaskResponse(
        task_id=task.task_id,
        task_type=task.task_type,
        status=task.status,
        stage=task.stage,
        created_at=task.created_at,
        updated_at=task.updated_at,
        filename=task.filename,
        file_url=f"/api/extract/{task.task_id}/file" if task.file_path else "",
        extractor_used=task.extractor_used,
        fields=[to_jsonable(field) for field in task.fields],
        results=[to_jsonable(result) for result in task.results],
        errors=task.errors,
    )


def extraction_record_summary(task: ExtractionTask) -> ExtractionRecordResponse:
    found_count = sum(1 for result in task.results if result.status == "found")
    not_found_count = sum(1 for result in task.results if result.status == "not_found")
    error_count = sum(1 for result in task.results if result.status == "error")
    return ExtractionRecordResponse(
        task_id=task.task_id,
        task_type=task.task_type,
        status=task.status,
        created_at=task.created_at,
        updated_at=task.updated_at,
        filename=task.filename,
        file_url=f"/api/extract/{task.task_id}/file" if task.file_path else "",
        extractor_used=task.extractor_used,
        field_count=len(task.fields),
        found_count=found_count,
        not_found_count=not_found_count,
        error_count=error_count,
    )


def extraction_record_list_response(tasks: list[ExtractionTask]) -> ExtractionRecordListResponse:
    return ExtractionRecordListResponse(records=[extraction_record_summary(task) for task in tasks])
