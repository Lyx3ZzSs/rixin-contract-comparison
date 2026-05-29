from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.api_errors import http_error
from app.application.compare_tasks import default_compare_task_application
from app.api_presenters import (
    compare_diff_list_response,
    compare_record_list_response,
    compare_task_detail_response,
    compare_task_response,
    diff_review_response,
    task_execution_response,
)
from app.api_schemas import (
    CompareDiffListResponse,
    CompareRecordListResponse,
    CompareTaskDetailResponse,
    CompareTaskResponse,
    DiffReviewRequest,
    DiffReviewResponse,
    TaskExecutionResponse,
)
from app.infrastructure.artifact_store import default_artifact_store
from app.models import CompareTask
from app.services.pdf_highlighter import PdfHighlighter
from app.services.review_service import (
    CompareQualityService,
    DiffNotFoundError,
    InvalidReviewStateError,
)
from app.services.report_generator import build_report_filename
from app.utils.file_utils import FileValidationError, assert_path_inside_storage, save_upload_file
from app.utils.id_utils import generate_task_id

router = APIRouter(prefix="/api/compare", tags=["compare"])


@router.post("", response_model=CompareTaskResponse)
async def compare_contracts(
    original_file: UploadFile = File(...),
    compare_file: UploadFile = File(...),
) -> CompareTaskResponse:
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
        raise http_error(exc) from exc
    except Exception as exc:
        raise http_error(exc, fallback_prefix="合同对比任务创建失败") from exc

    default_compare_task_application.submit_compare(
        original_path=original_path,
        compare_path=compare_path,
        task_id=task_id,
        original_filename=original_file.filename,
        compare_filename=compare_file.filename,
    )
    return compare_task_response(task)


@router.get("/records", response_model=CompareRecordListResponse)
def list_records() -> CompareRecordListResponse:
    return compare_record_list_response(default_compare_task_application.list_compare_tasks())


@router.get("/{task_id}", response_model=CompareTaskDetailResponse)
def get_task(task_id: str) -> CompareTaskDetailResponse:
    task = _load_or_404(task_id)
    return compare_task_detail_response(task)


@router.get("/{task_id}/diffs", response_model=CompareDiffListResponse)
def get_diffs(task_id: str) -> CompareDiffListResponse:
    task = _load_or_404(task_id)
    return compare_diff_list_response(task)


@router.patch("/{task_id}/diffs/{diff_id}/review", response_model=DiffReviewResponse)
def update_diff_review(task_id: str, diff_id: str, payload: DiffReviewRequest) -> DiffReviewResponse:
    task = _load_or_404(task_id)
    try:
        task, diff = default_compare_task_application.update_diff_review(
            task,
            diff_id,
            payload.review_status,
            payload.review_comment,
            payload.reviewed_by,
        )
    except (InvalidReviewStateError, DiffNotFoundError) as exc:
        raise http_error(exc) from exc

    return diff_review_response(task, diff)


@router.get("/{task_id}/execution", response_model=TaskExecutionResponse)
def get_task_execution(task_id: str) -> TaskExecutionResponse:
    try:
        return task_execution_response(default_compare_task_application.load_execution(task_id))
    except Exception as exc:
        raise http_error(exc) from exc


@router.post("/{task_id}/cancel", response_model=TaskExecutionResponse)
def cancel_task(task_id: str) -> TaskExecutionResponse:
    try:
        return task_execution_response(default_compare_task_application.cancel_compare(task_id))
    except Exception as exc:
        raise http_error(exc) from exc


@router.post("/{task_id}/retry", response_model=TaskExecutionResponse)
def retry_task(task_id: str) -> TaskExecutionResponse:
    try:
        return task_execution_response(default_compare_task_application.retry_compare(task_id))
    except Exception as exc:
        raise http_error(exc) from exc


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
        task = default_compare_task_application.ensure_report(task)
    except Exception as exc:
        raise http_error(exc, fallback_prefix="审计报告生成失败") from exc
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
    return _file_response(
        str(_ensure_highlight_pdf(task, "original")),
        "original_highlighted.pdf",
        "application/pdf",
    )


@router.get("/{task_id}/highlight/compare")
def download_compare_highlight(task_id: str) -> FileResponse:
    task = _load_or_404(task_id)
    return _file_response(
        str(_ensure_highlight_pdf(task, "compare")),
        "compare_highlighted.pdf",
        "application/pdf",
    )


def _ensure_highlight_pdf(task: CompareTask, side: Literal["original", "compare"]) -> Path:
    if task.status != "COMPLETED":
        raise HTTPException(status_code=409, detail="任务尚未完成，暂不能导出高亮 PDF。")
    source_path_value = task.original_pdf_path if side == "original" else task.compare_pdf_path
    if not source_path_value:
        raise HTTPException(status_code=404, detail="源 PDF 尚未生成。")
    source_path = Path(source_path_value)
    try:
        assert_path_inside_storage(source_path)
    except FileValidationError as exc:
        raise http_error(exc) from exc
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="源 PDF 文件不存在。")

    output_path = default_artifact_store.highlighted_pdf_path(task.task_id, side)
    if output_path.exists():
        return output_path
    try:
        if side == "original":
            return PdfHighlighter().highlight_original(source_path, task.diffs, output_path)
        return PdfHighlighter().highlight_compare(source_path, task.diffs, output_path)
    except Exception as exc:
        raise http_error(exc, fallback_prefix="高亮 PDF 导出失败") from exc


def _load_or_404(task_id: str) -> CompareTask:
    try:
        return default_compare_task_application.load_compare_task(task_id)
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
        raise http_error(exc) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在。")
    return FileResponse(
        path,
        filename=filename,
        media_type=media_type,
        content_disposition_type=content_disposition_type,
    )
