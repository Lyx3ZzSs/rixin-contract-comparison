from __future__ import annotations

import re
from pathlib import Path

from fastapi import UploadFile

from app.config import settings


class FileValidationError(ValueError):
    pass


def safe_filename(filename: str) -> str:
    name = Path(filename or "contract.pdf").name
    name = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", name)
    return name or "contract.pdf"


def ensure_pdf_filename(filename: str) -> None:
    if not filename.lower().endswith(".pdf"):
        raise FileValidationError("仅支持 PDF 文件。")


def validate_pdf_bytes(content: bytes, filename: str) -> None:
    ensure_pdf_filename(filename)
    if not content.startswith(b"%PDF"):
        raise FileValidationError("文件不是有效的 PDF。")
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise FileValidationError(f"文件超过 {settings.max_upload_size_mb}MB 限制。")


async def save_upload_file(upload_file: UploadFile, task_id: str, label: str) -> Path:
    content = await upload_file.read()
    validate_pdf_bytes(content, upload_file.filename or "")
    task_dir = settings.uploads_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    destination = task_dir / f"{label}_{safe_filename(upload_file.filename or 'contract.pdf')}"
    destination.write_bytes(content)
    return destination


async def save_upload_file_generic(upload_file: UploadFile, task_id: str, label: str) -> Path:
    content = await upload_file.read()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise FileValidationError(f"文件超过 {settings.max_upload_size_mb}MB 限制。")
    task_dir = settings.uploads_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(upload_file.filename or "document")
    destination = task_dir / f"{label}_{filename}"
    destination.write_bytes(content)
    return destination


def assert_path_inside_storage(path: Path) -> None:
    path = path.resolve()
    storage = settings.storage_dir.resolve()
    if storage not in [path, *path.parents]:
        raise FileValidationError("非法文件路径。")

