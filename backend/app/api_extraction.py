from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.application.extraction_tasks import default_extraction_task_application
from app.api_presenters import extraction_record_summary
from app.models_extraction import ExtractionFieldDef
from app.services.ppocrv5_llm_extraction import ExtractionFilePreprocessor, PPOCRV5LLMExtractionError
from app.services.extraction_service import ExtractionService
from app.utils.file_utils import FileValidationError, assert_path_inside_storage, save_upload_file_generic
from app.utils.id_utils import generate_task_id
from app.utils.json_utils import list_extraction_tasks, load_extraction_task, to_jsonable

router = APIRouter(prefix="/api/extract", tags=["extraction"])


@router.post("")
async def extract_fields(
    file: UploadFile = File(...),
    fields: str = Form(...),
) -> dict:
    try:
        field_defs = [ExtractionFieldDef(**f) for f in json.loads(fields)]
    except (json.JSONDecodeError, Exception) as exc:
        raise HTTPException(status_code=400, detail=f"字段定义格式错误: {exc}") from exc

    if not field_defs:
        raise HTTPException(status_code=400, detail="至少需要一个提取字段。")

    task_id = generate_task_id()
    try:
        file_path = await save_upload_file_generic(file, task_id, "source")
    except FileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    service = ExtractionService()
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

    data = to_jsonable(task)
    data["file_url"] = f"/api/extract/{task.task_id}/file"
    return data


@router.get("/records")
def list_extraction_records() -> dict:
    records = [extraction_record_summary(task) for task in list_extraction_tasks()]
    return {"records": records}


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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PPOCRV5LLMExtractionError as exc:
        raise HTTPException(status_code=500, detail=f"预览失败: {exc}") from exc

    return FileResponse(
        preview_path,
        filename=f"{file_path.stem}.pdf",
        media_type="application/pdf",
        content_disposition_type="inline",
    )


@router.get("/{task_id}")
def get_extraction_task(task_id: str) -> dict:
    task = _load_or_404(task_id)
    data = to_jsonable(task)
    data["file_url"] = f"/api/extract/{task.task_id}/file"
    return data


@router.get("/{task_id}/file")
def preview_file(task_id: str) -> FileResponse:
    task = _load_or_404(task_id)
    if not task.file_path:
        raise HTTPException(status_code=404, detail="文件不存在。")
    path = Path(task.file_path)
    try:
        assert_path_inside_storage(path)
    except FileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在。")
    return FileResponse(path, filename=task.filename or "document", content_disposition_type="inline")


def _load_or_404(task_id: str):
    try:
        return load_extraction_task(task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
