from __future__ import annotations

from pathlib import Path

from app.models import CompareTask
from app.models_extraction import ExtractionTask
from app.services.report_generator import build_report_filename
from app.utils.json_utils import to_jsonable


def compare_task_response(task: CompareTask) -> dict:
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
        "parse_warning_details": to_jsonable(task).get("parse_warning_details", []),
        "document_profiles": to_jsonable(task).get("document_profiles", {}),
        "debug_artifact_paths": task.debug_artifact_paths,
        "errors": task.errors,
    }
    data.update(compare_task_artifact_urls(task))
    return data


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


def compare_record_summary(task: CompareTask) -> dict:
    return {
        "task_id": task.task_id,
        "status": task.status,
        "stage": task.stage,
        "progress_percent": task.progress_percent,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "original_filename": task.original_filename,
        "compare_filename": task.compare_filename,
        "diff_count": task.diff_count,
        "high_risk_count": task.high_risk_count,
        "medium_risk_count": task.medium_risk_count,
        "low_risk_count": task.low_risk_count,
        "report_url": f"/api/compare/{task.task_id}/report" if task.status == "COMPLETED" else "",
    }


def diff_response(task_id: str, diff) -> dict:
    data = to_jsonable(diff)
    data["original_screenshot_url"] = screenshot_url(task_id, diff.original_screenshot)
    data["compare_screenshot_url"] = screenshot_url(task_id, diff.compare_screenshot)
    return data


def screenshot_url(task_id: str, screenshot_path: str) -> str:
    if not screenshot_path:
        return ""
    return f"/api/compare/{task_id}/screenshot/{Path(screenshot_path).name}"


def review_stats(task: CompareTask) -> dict[str, int]:
    return {
        "reviewed_count": task.reviewed_count,
        "confirmed_count": task.confirmed_count,
        "false_positive_count": task.false_positive_count,
        "manual_review_count": task.manual_review_count,
        "ignored_count": task.ignored_count,
    }


def extraction_record_summary(task: ExtractionTask) -> dict:
    found_count = sum(1 for result in task.results if result.status == "found")
    not_found_count = sum(1 for result in task.results if result.status == "not_found")
    error_count = sum(1 for result in task.results if result.status == "error")
    return {
        "task_id": task.task_id,
        "task_type": task.task_type,
        "status": task.status,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "filename": task.filename,
        "file_url": f"/api/extract/{task.task_id}/file" if task.file_path else "",
        "extractor_used": task.extractor_used,
        "field_count": len(task.fields),
        "found_count": found_count,
        "not_found_count": not_found_count,
        "error_count": error_count,
    }
