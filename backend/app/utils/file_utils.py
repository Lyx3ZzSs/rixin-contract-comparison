from __future__ import annotations

import re
from pathlib import Path

from fastapi import UploadFile

from app.config import settings


class FileValidationError(ValueError):
    pass


EXTRACTION_DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx"}
EXTRACTION_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp"}
EXTRACTION_SUPPORTED_EXTENSIONS = EXTRACTION_DOCUMENT_EXTENSIONS | EXTRACTION_IMAGE_EXTENSIONS
IMAGE_SIGNATURES = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".bmp": (b"BM",),
}


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


def validate_extraction_upload_bytes(content: bytes, filename: str) -> None:
    extension = Path(filename or "").suffix.lower()
    if extension not in EXTRACTION_SUPPORTED_EXTENSIONS:
        raise FileValidationError("仅支持 PDF、Word、PNG、JPG、JPEG、BMP 文件。")

    max_mb = (
        settings.extraction_max_image_size_mb
        if extension in EXTRACTION_IMAGE_EXTENSIONS
        else settings.extraction_max_document_size_mb
    )
    if len(content) > max_mb * 1024 * 1024:
        raise FileValidationError(f"文件超过 {max_mb}MB 限制。")

    if extension == ".pdf" and not content.startswith(b"%PDF"):
        raise FileValidationError("文件不是有效的 PDF。")
    if extension in IMAGE_SIGNATURES and not any(
        content.startswith(signature) for signature in IMAGE_SIGNATURES[extension]
    ):
        raise FileValidationError("文件不是有效的图片。")
    if extension in {".doc", ".docx"} and not content:
        raise FileValidationError("文件内容为空。")


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
    validate_extraction_upload_bytes(content, upload_file.filename or "")
    task_dir = settings.uploads_dir / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(upload_file.filename or "document")
    destination = task_dir / f"{label}_{filename}"
    destination.write_bytes(content)
    return destination


def assert_path_inside_storage(path: Path) -> None:
    from app.infrastructure.artifact_store import default_artifact_store

    default_artifact_store.assert_inside_storage(path)
