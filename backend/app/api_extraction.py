from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError as PydanticValidationError

from app.api_errors import http_error
from app.application.extraction_tasks import default_extraction_task_application
from app.api_presenters import extraction_record_list_response, extraction_task_response, task_execution_response
from app.api_schemas import (
    ExtractionFieldRequest,
    ExtractionRecordListResponse,
    ExtractionTaskResponse,
    TaskExecutionResponse,
)
from app.errors import ValidationError
from app.models_extraction import ExtractionFieldDef
from app.services.extraction_service import ExtractionService
from app.services.ppocrv5_llm_extraction import ExtractionFilePreprocessor, PPOCRV5LLMExtractionError
from app.utils.file_utils import FileValidationError, assert_path_inside_storage, save_upload_file_generic
from app.utils.id_utils import generate_task_id

router = APIRouter(prefix="/api/extract", tags=["extraction"])


@router.post("", response_model=ExtractionTaskResponse)
async def extract_fields(
    file: UploadFile = File(...),
    fields: str = Form(...),
) -> ExtractionTaskResponse:
    try:
        field_requests = [ExtractionFieldRequest(**f) for f in json.loads(fields)]
    except (json.JSONDecodeError, TypeError, PydanticValidationError) as exc:
        raise http_error(ValidationError(f"字段定义格式错误: {exc}")) from exc

    if not field_requests:
        raise http_error(ValidationError("至少需要一个提取字段。"))

    field_defs = [ExtractionFieldDef(**field.model_dump()) for field in field_requests]
    task_id = generate_task_id()
    try:
        file_path = await save_upload_file_generic(file, task_id, "source")
    except FileValidationError as exc:
        raise http_error(exc) from exc

    service = ExtractionService(repository=default_extraction_task_application.repository)
    task = default_extraction_task_application.create_queued_task(
        task_id=task_id,
        file_path=file_path,
        filename=file.filename or "",
        fields=field_defs,
        service=service,
    )

    default_extraction_task_application.submit_extraction(
        service=service,
        file_path=file_path,
        field_defs=field_defs,
        task_id=task_id,
        filename=file.filename or "",
    )

    return extraction_task_response(task)


@router.get("/records", response_model=ExtractionRecordListResponse)
def list_extraction_records() -> ExtractionRecordListResponse:
    return extraction_record_list_response(default_extraction_task_application.list_extraction_tasks())


@router.post("/preview")
async def preview_uploaded_file(file: UploadFile = File(...)) -> FileResponse:
    task_id = generate_task_id()
    try:
        file_path = await save_upload_file_generic(file, task_id, "preview")
        extension = file_path.suffix.lower()
        if extension == ".pdf":
            preview_path = file_path
        elif extension in {".doc", ".docx"}:
            preview_path = ExtractionFilePreprocessor().prepare(file_path).path
        else:
            raise FileValidationError("仅支持 PDF 或 Word 文件预览。")
    except FileValidationError as exc:
        raise http_error(exc) from exc
    except PPOCRV5LLMExtractionError as exc:
        raise http_error(exc, fallback_prefix="预览失败") from exc

    return FileResponse(
        preview_path,
        filename=f"{file_path.stem}.pdf",
        media_type="application/pdf",
        content_disposition_type="inline",
    )


@router.get("/{task_id}", response_model=ExtractionTaskResponse)
def get_extraction_task(task_id: str) -> ExtractionTaskResponse:
    task = _load_or_404(task_id)
    return extraction_task_response(task)


@router.get("/{task_id}/execution", response_model=TaskExecutionResponse)
def get_extraction_execution(task_id: str) -> TaskExecutionResponse:
    try:
        return task_execution_response(default_extraction_task_application.load_execution(task_id))
    except Exception as exc:
        raise http_error(exc) from exc


@router.post("/{task_id}/cancel", response_model=TaskExecutionResponse)
def cancel_extraction_task(task_id: str) -> TaskExecutionResponse:
    try:
        return task_execution_response(default_extraction_task_application.cancel_extraction(task_id))
    except Exception as exc:
        raise http_error(exc) from exc


@router.post("/{task_id}/retry", response_model=TaskExecutionResponse)
def retry_extraction_task(task_id: str) -> TaskExecutionResponse:
    try:
        return task_execution_response(default_extraction_task_application.retry_extraction(task_id))
    except Exception as exc:
        raise http_error(exc) from exc


@router.get("/{task_id}/file")
def preview_file(task_id: str) -> FileResponse:
    task = _load_or_404(task_id)
    if not task.file_path:
        raise HTTPException(status_code=404, detail="文件不存在。")
    path = Path(task.file_path)
    try:
        assert_path_inside_storage(path)
    except FileValidationError as exc:
        raise http_error(exc) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在。")
    return FileResponse(path, filename=task.filename or "document", content_disposition_type="inline")


def _load_or_404(task_id: str):
    try:
        return default_extraction_task_application.load_extraction_task(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
