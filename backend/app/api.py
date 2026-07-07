from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from math import ceil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.api_errors import http_error
from app.application.compare_tasks import default_compare_task_application
from app.api_presenters import (
    audit_item_review_response,
    compare_diff_list_response,
    compare_record_list_response,
    compare_task_detail_response,
    compare_task_response,
    diff_review_response,
    task_execution_response,
)
from app.api_schemas import (
    AuditItemReviewUpdateResponse,
    CompareDiffListResponse,
    CompareRecordListResponse,
    CompareTaskDetailResponse,
    CompareTaskResponse,
    DiffReviewRequest,
    DiffReviewResponse,
    TaskExecutionResponse,
)
from app.models import CompareOptions, CompareTask
from app.services.review_service import (
    AuditItemNotFoundError,
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
    ignore_stamps: bool = Form(False),
    ignore_headers_footers: bool = Form(False),
    signing_region_mode: Literal["full", "off"] = Form("full"),
) -> CompareTaskResponse:
    task_id = generate_task_id()
    compare_options = CompareOptions(
        ignore_stamps=ignore_stamps,
        ignore_headers_footers=ignore_headers_footers,
        signing_region_mode=signing_region_mode,
    )
    try:
        original_path = await save_upload_file(original_file, task_id, "original")
        compare_path = await save_upload_file(compare_file, task_id, "compare")
        task = default_compare_task_application.create_queued_task(
            task_id=task_id,
            original_path=original_path,
            compare_path=compare_path,
            original_filename=original_file.filename or original_path.name,
            compare_filename=compare_file.filename or compare_path.name,
            compare_options=compare_options,
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
        compare_options=compare_options,
    )
    return compare_task_response(task)


@router.get("/records", response_model=CompareRecordListResponse)
def list_records(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    start_date: date | None = None,
    end_date: date | None = None,
) -> CompareRecordListResponse:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期。")

    tasks = default_compare_task_application.list_compare_tasks()
    filtered_tasks = [
        task
        for task in tasks
        if _is_task_in_created_date_range(task, start_date=start_date, end_date=end_date)
    ]
    total = len(filtered_tasks)
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    page_tasks = filtered_tasks[start_index:end_index]
    total_pages = ceil(total / page_size) if total else 0
    return compare_record_list_response(
        page_tasks,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/{task_id}", response_model=CompareTaskDetailResponse)
def get_task(task_id: str) -> CompareTaskDetailResponse:
    task = _load_or_404(task_id)
    return compare_task_detail_response(task)


@router.get("/{task_id}/progress")
async def stream_progress(task_id: str):
    try:
        current_task = default_compare_task_application.load_compare_task(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    from app.services.progress_bus import ProgressBus

    bus = ProgressBus.get_instance()
    queue = await bus.subscribe(task_id)
    current_task = default_compare_task_application.load_compare_task(task_id)

    async def event_stream():
        try:
            initial_payload = {
                "task_id": current_task.task_id,
                "stage": current_task.stage,
                "progress_percent": current_task.progress_percent,
                "status": current_task.status,
            }
            yield f"data: {json.dumps(initial_payload, ensure_ascii=False)}\n\n"
            if current_task.status in ("COMPLETED", "FAILED"):
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                payload = {
                    "task_id": event.task_id,
                    "stage": event.stage,
                    "progress_percent": event.progress_percent,
                    "status": event.status,
                }
                if event.detail:
                    payload["detail"] = event.detail
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if event.status in ("COMPLETED", "FAILED"):
                    break
        finally:
            bus.unsubscribe(task_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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


@router.patch(
    "/{task_id}/audit-items/{audit_item_id}/review",
    response_model=AuditItemReviewUpdateResponse,
)
def update_audit_item_review(
    task_id: str,
    audit_item_id: str,
    payload: DiffReviewRequest,
) -> AuditItemReviewUpdateResponse:
    task = _load_or_404(task_id)
    try:
        task, review = default_compare_task_application.update_audit_item_review(
            task,
            audit_item_id,
            payload.review_status,
            payload.review_comment,
            payload.reviewed_by,
        )
    except (InvalidReviewStateError, AuditItemNotFoundError) as exc:
        raise http_error(exc) from exc

    return audit_item_review_response(task, audit_item_id, review)


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


def _load_or_404(task_id: str) -> CompareTask:
    try:
        return default_compare_task_application.load_compare_task(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _is_task_in_created_date_range(
    task: CompareTask,
    *,
    start_date: date | None,
    end_date: date | None,
) -> bool:
    if start_date is None and end_date is None:
        return True
    task_date = _task_created_date(task)
    if task_date is None:
        return False
    if start_date and task_date < start_date:
        return False
    if end_date and task_date > end_date:
        return False
    return True


def _task_created_date(task: CompareTask) -> date | None:
    value = task.created_at or task.updated_at
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


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
