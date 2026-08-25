from __future__ import annotations

import asyncio
import logging
import uuid
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

import fitz
from fastapi import UploadFile

from app.config import settings
from app.errors import ValidationError

logger = logging.getLogger(__name__)
UPLOAD_CHUNK_SIZE = 64 * 1024

if TYPE_CHECKING:
    from app.infrastructure.artifact_store import ArtifactStore


class FileValidationError(ValidationError):
    pass


def safe_filename(filename: str) -> str:
    if not filename or filename in {".", ".."}:
        raise FileValidationError("文件名不合法。")
    if "/" in filename or "\\" in filename or any(unicodedata.category(char).startswith("C") for char in filename):
        raise FileValidationError("文件名不合法。")
    if len(filename.encode("utf-8")) > 255:
        raise FileValidationError("文件名过长。")
    ensure_pdf_filename(filename)
    return filename


def ensure_pdf_filename(filename: str) -> None:
    if not filename.lower().endswith(".pdf"):
        raise FileValidationError("仅支持 PDF 文件。")


def validate_pdf_bytes(content: bytes, filename: str) -> None:
    ensure_pdf_filename(filename)
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise FileValidationError(f"文件超过 {settings.max_upload_size_mb}MB 限制。")
    validate_pdf_structure(content)


def validate_pdf_structure(content: bytes) -> None:
    if not content.startswith(b"%PDF"):
        raise FileValidationError("文件不是有效的 PDF。")

    try:
        pdf = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise FileValidationError("PDF 文件已损坏或格式无效。") from exc

    try:
        encrypt_type, _ = pdf.xref_get_key(-1, "Encrypt")
        if pdf.needs_pass or pdf.is_encrypted or encrypt_type != "null":
            raise FileValidationError("PDF 文件已加密，请上传未加密版本。")
        if pdf.page_count <= 0:
            raise FileValidationError("PDF 文件不包含有效页面。")
    except FileValidationError:
        raise
    except Exception as exc:
        raise FileValidationError("PDF 文件已损坏或格式无效。") from exc
    finally:
        pdf.close()


def validate_pdf_path(path: Path, filename: str) -> None:
    ensure_pdf_filename(filename)
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if path.stat().st_size > max_bytes:
        raise FileValidationError(f"文件超过 {settings.max_upload_size_mb}MB 限制。")
    with path.open("rb") as stream:
        if stream.read(4) != b"%PDF":
            raise FileValidationError("文件不是有效的 PDF。")
    try:
        pdf = fitz.open(path)
    except Exception as exc:
        raise FileValidationError("PDF 文件已损坏或格式无效。") from exc
    try:
        encrypt_type, _ = pdf.xref_get_key(-1, "Encrypt")
        if pdf.needs_pass or pdf.is_encrypted or encrypt_type != "null":
            raise FileValidationError("PDF 文件已加密，请上传未加密版本。")
        if pdf.page_count <= 0:
            raise FileValidationError("PDF 文件不包含有效页面。")
    except FileValidationError:
        raise
    except Exception as exc:
        raise FileValidationError("PDF 文件已损坏或格式无效。") from exc
    finally:
        pdf.close()


async def stream_upload_to_path(upload_file: UploadFile, destination: Path) -> Path:
    ensure_pdf_filename(upload_file.filename or "")
    destination.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    total = 0
    try:
        with destination.open("xb") as stream:
            while chunk := await upload_file.read(UPLOAD_CHUNK_SIZE):
                total += len(chunk)
                if total > max_bytes:
                    raise FileValidationError(f"文件超过 {settings.max_upload_size_mb}MB 限制。")
                stream.write(chunk)
        return destination
    except asyncio.CancelledError:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            logger.error("Failed to clean cancelled partial upload: path=%s", destination.name, exc_info=True)
        raise
    except Exception:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            logger.error("Failed to clean partial upload: path=%s", destination.name, exc_info=True)
        raise


async def save_upload_file(
    upload_file: UploadFile,
    task_id: str,
    label: str,
    artifact_store: "ArtifactStore | None" = None,
) -> Path:
    store = _artifact_store(artifact_store)
    filename = safe_filename(upload_file.filename or "contract.pdf")
    staged = store.staging_path(task_id, uuid.uuid4().hex, label, filename)
    await stream_upload_to_path(upload_file, staged)
    try:
        validate_pdf_path(staged, upload_file.filename or "")
        return store.publish_staged(staged, store.upload_path(task_id, label, filename))
    finally:
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            logger.error("Failed to clean legacy staged upload: path=%s", staged, exc_info=True)


def assert_path_inside_storage(path: Path) -> None:
    _artifact_store().assert_inside_storage(path)


def _artifact_store(artifact_store: "ArtifactStore | None" = None) -> "ArtifactStore":
    if artifact_store is not None:
        return artifact_store

    from app.infrastructure.artifact_store import default_artifact_store

    return default_artifact_store
