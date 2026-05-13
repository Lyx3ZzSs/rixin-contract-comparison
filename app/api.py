from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.models import CompareTask
from app.services.compare_service import CompareService
from app.services.pdf_parser import PdfParseError
from app.utils.file_utils import FileValidationError, assert_path_inside_storage, save_upload_file
from app.utils.id_utils import generate_task_id
from app.utils.json_utils import load_task, to_jsonable

router = APIRouter(prefix="/api/compare", tags=["compare"])


@router.post("")
async def compare_contracts(
    original_file: UploadFile = File(...),
    compare_file: UploadFile = File(...),
    enable_ai_analysis: bool = Form(True),
) -> dict:
    task_id = generate_task_id()
    try:
        original_path = await save_upload_file(original_file, task_id, "original")
        compare_path = await save_upload_file(compare_file, task_id, "compare")
        task = CompareService().compare(
            original_path,
            compare_path,
            enable_ai_analysis=enable_ai_analysis,
            task_id=task_id,
            original_filename=original_file.filename,
            compare_filename=compare_file.filename,
        )
    except FileValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PdfParseError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"合同对比失败: {exc}") from exc

    return {
        "task_id": task.task_id,
        "status": task.status,
        "diff_count": task.diff_count,
        "high_risk_count": task.high_risk_count,
        "medium_risk_count": task.medium_risk_count,
        "low_risk_count": task.low_risk_count,
        "original_pdf_url": f"/api/compare/{task.task_id}/original",
        "compare_pdf_url": f"/api/compare/{task.task_id}/compare",
        "report_url": f"/api/compare/{task.task_id}/report",
        "original_highlight_pdf_url": f"/api/compare/{task.task_id}/highlight/original",
        "compare_highlight_pdf_url": f"/api/compare/{task.task_id}/highlight/compare",
        "errors": task.errors,
    }


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


@router.get("/{task_id}/report")
def download_report(task_id: str) -> FileResponse:
    task = _load_or_404(task_id)
    return _file_response(task.report_pdf_path, "AI合同差异分析报告.pdf", "application/pdf")


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


def _task_artifact_urls(task: CompareTask) -> dict[str, str]:
    return {
        "original_pdf_url": f"/api/compare/{task.task_id}/original" if task.original_pdf_path else "",
        "compare_pdf_url": f"/api/compare/{task.task_id}/compare" if task.compare_pdf_path else "",
        "report_url": f"/api/compare/{task.task_id}/report" if task.report_pdf_path else "",
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
