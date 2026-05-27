from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.application.compare_tasks import default_compare_task_application
from app.api_presenters import (
    compare_record_summary,
    compare_task_artifact_urls,
    compare_task_response,
    diff_response,
    review_stats,
)
from app.models import CompareTask, ReviewStatus
from app.services.review_service import (
    CompareQualityService,
    CompareReviewService,
    DiffNotFoundError,
    InvalidReviewStateError,
)
from app.services.compare_service import CompareService
from app.services.report_generator import build_report_filename
from app.utils.file_utils import FileValidationError, assert_path_inside_storage, save_upload_file
from app.utils.id_utils import generate_task_id
from app.utils.json_utils import list_compare_tasks, load_task, to_jsonable

router = APIRouter(prefix="/api/compare", tags=["compare"])


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
        task = default_compare_task_application.create_queued_task(
            task_id=task_id,
            original_path=original_path,
            compare_path=compare_path,
            original_filename=original_file.filename or original_path.name,
            compare_filename=compare_file.filename or compare_path.name,
        )
    except FileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"合同对比任务创建失败: {exc}") from exc

    default_compare_task_application.submit_compare(
        original_path=original_path,
        compare_path=compare_path,
        task_id=task_id,
        original_filename=original_file.filename,
        compare_filename=compare_file.filename,
    )
    return compare_task_response(task)


@router.get("/records")
def list_records() -> dict:
    return {"records": [compare_record_summary(task) for task in list_compare_tasks()]}


@router.get("/{task_id}")
def get_task(task_id: str) -> dict:
    task = _load_or_404(task_id)
    data = to_jsonable(task)
    data.pop("diffs", None)
    data.update(compare_task_artifact_urls(task))
    return data


@router.get("/{task_id}/diffs")
def get_diffs(task_id: str) -> dict:
    task = _load_or_404(task_id)
    diffs = []
    for diff in task.diffs:
        diffs.append(diff_response(task.task_id, diff))
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
        "review_stats": review_stats(task),
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
