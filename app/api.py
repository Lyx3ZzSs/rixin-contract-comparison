from __future__ import annotations

import asyncio
import functools
import logging
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.models import CompareTask, ReviewStatus
from app.services.review_service import CompareQualityService, CompareReviewService, DiffNotFoundError, InvalidReviewStateError
from app.services.compare_service import CompareService
from app.services.report_generator import build_report_filename
from app.utils.file_utils import FileValidationError, assert_path_inside_storage, save_upload_file
from app.utils.id_utils import generate_task_id
from app.utils.json_utils import list_compare_tasks, load_task, save_task, to_jsonable

router = APIRouter(prefix="/api/compare", tags=["compare"])
logger = logging.getLogger(__name__)


class DiffReviewRequest(BaseModel):
    review_status: ReviewStatus
    review_comment: str = ""
    reviewed_by: str = ""


@router.post("")
async def compare_contracts(
    original_file: UploadFile = File(...),
    compare_file: UploadFile = File(...),
) -> dict:
    task_id = generate_task_id()
    try:
        original_path = await save_upload_file(original_file, task_id, "original")
        compare_path = await save_upload_file(compare_file, task_id, "compare")
        task = CompareTask(
            task_id=task_id,
            stage="排队中",
            progress_percent=3,
            original_filename=original_file.filename or original_path.name,
            compare_filename=compare_file.filename or compare_path.name,
            original_pdf_path=str(original_path),
            compare_pdf_path=str(compare_path),
        )
        save_task(task)
    except FileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"合同对比任务创建失败: {exc}") from exc

    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        None,
        functools.partial(
            _run_compare_task,
            original_path=original_path,
            compare_path=compare_path,
            task_id=task_id,
            original_filename=original_file.filename,
            compare_filename=compare_file.filename,
        ),
    )
    return _task_response(task)


@router.get("/records")
def list_records() -> dict:
    records = []
    for task in list_compare_tasks():
        records.append(
            {
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
        )
    return {"records": records}


@router.get("/{task_id}")
def get_task(task_id: str) -> dict:
    task = _load_or_404(task_id)
    data = to_jsonable(task)
    data.pop("diffs", None)
    data.update(_task_artifact_urls(task))
    return data


@router.get("/{task_id}/diffs")
def get_diffs(task_id: str) -> dict:
    task = _load_or_404(task_id)
    diffs = []
    for diff in task.diffs:
        data = to_jsonable(diff)
        data["original_screenshot_url"] = _screenshot_url(task.task_id, diff.original_screenshot)
        data["compare_screenshot_url"] = _screenshot_url(task.task_id, diff.compare_screenshot)
        diffs.append(data)
    return {"task_id": task.task_id, "diffs": diffs}


@router.patch("/{task_id}/diffs/{diff_id}/review")
def update_diff_review(task_id: str, diff_id: str, payload: DiffReviewRequest) -> dict:
    task = _load_or_404(task_id)
    try:
        task, diff = CompareReviewService().update_diff_review(
            task,
            diff_id,
            payload.review_status,
            payload.review_comment,
            payload.reviewed_by,
        )
    except InvalidReviewStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DiffNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "task_id": task.task_id,
        "diff": to_jsonable(diff),
        "review_stats": _review_stats(task),
    }


@router.get("/{task_id}/quality")
def get_quality_summary(task_id: str) -> dict:
    task = _load_or_404(task_id)
    return CompareQualityService().build_summary(task)


@router.get("/{task_id}/report")
def download_report(task_id: str) -> FileResponse:
    task = _load_or_404(task_id)
    if task.status != "COMPLETED":
        raise HTTPException(status_code=409, detail="任务尚未完成，暂不能生成报告。")
    try:
        task = CompareService().ensure_report(task)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"审计报告生成失败: {exc}") from exc
    return _file_response(task.report_pdf_path, build_report_filename(task), "application/pdf")


@router.get("/{task_id}/original")
def preview_original_pdf(task_id: str) -> FileResponse:
    task = _load_or_404(task_id)
    return _file_response(task.original_pdf_path, task.original_filename or "original.pdf", "application/pdf", "inline")


@router.get("/{task_id}/compare")
def preview_compare_pdf(task_id: str) -> FileResponse:
    task = _load_or_404(task_id)
    return _file_response(task.compare_pdf_path, task.compare_filename or "compare.pdf", "application/pdf", "inline")


@router.get("/{task_id}/highlight/original")
def download_original_highlight(task_id: str) -> FileResponse:
    task = _load_or_404(task_id)
    return _file_response(task.original_highlight_pdf_path, "original_highlighted.pdf", "application/pdf")


@router.get("/{task_id}/highlight/compare")
def download_compare_highlight(task_id: str) -> FileResponse:
    task = _load_or_404(task_id)
    return _file_response(task.compare_highlight_pdf_path, "compare_highlighted.pdf", "application/pdf")


@router.get("/{task_id}/screenshot/{filename}")
def download_screenshot(task_id: str, filename: str) -> FileResponse:
    _load_or_404(task_id)
    from app.config import settings

    path = settings.screenshots_dir / task_id / Path(filename).name
    return _file_response(str(path), filename, "image/png")


def _load_or_404(task_id: str) -> CompareTask:
    try:
        return load_task(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _run_compare_task(
    original_path: Path,
    compare_path: Path,
    task_id: str,
    original_filename: str | None,
    compare_filename: str | None,
) -> None:
    try:
        CompareService().compare(
            original_path,
            compare_path,
            task_id=task_id,
            original_filename=original_filename,
            compare_filename=compare_filename,
        )
    except Exception:
        logger.exception("Background compare task failed: %s", task_id)


def _task_response(task: CompareTask) -> dict:
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
    data.update(_task_artifact_urls(task))
    return data


def _review_stats(task: CompareTask) -> dict[str, int]:
    return {
        "reviewed_count": task.reviewed_count,
        "confirmed_count": task.confirmed_count,
        "false_positive_count": task.false_positive_count,
        "manual_review_count": task.manual_review_count,
        "ignored_count": task.ignored_count,
    }


def _file_response(
    path_value: str,
    filename: str,
    media_type: str,
    content_disposition_type: str = "attachment",
) -> FileResponse:
    if not path_value:
        raise HTTPException(status_code=404, detail="文件尚未生成。")
    path = Path(path_value)
    try:
        assert_path_inside_storage(path)
    except FileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在。")
    return FileResponse(
        path,
        filename=filename,
        media_type=media_type,
        content_disposition_type=content_disposition_type,
    )


def _task_artifact_urls(task: CompareTask) -> dict[str, str]:
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


def _screenshot_url(task_id: str, screenshot_path: str) -> str:
    if not screenshot_path:
        return ""
    return f"/api/compare/{task_id}/screenshot/{Path(screenshot_path).name}"
