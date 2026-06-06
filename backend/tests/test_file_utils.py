from __future__ import annotations

from types import SimpleNamespace

import fitz
import pytest

from app.config import settings
from app.utils.file_utils import (
    FileValidationError,
    validate_extraction_upload_bytes,
    validate_pdf_bytes,
    validate_pdf_structure,
)


def make_pdf_bytes(*, user_password: str | None = None) -> bytes:
    pdf = fitz.open()
    pdf.new_page()
    try:
        if user_password is not None:
            return pdf.tobytes(
                encryption=fitz.PDF_ENCRYPT_AES_256,
                owner_pw="owner",
                user_pw=user_password,
            )
        return pdf.tobytes()
    finally:
        pdf.close()


def test_validate_pdf_bytes_accepts_valid_pdf() -> None:
    validate_pdf_bytes(make_pdf_bytes(), "contract.pdf")


def test_validate_pdf_structure_rejects_damaged_pdf() -> None:
    with pytest.raises(FileValidationError, match="已损坏或格式无效"):
        validate_pdf_structure(b"%PDF-not-a-real-document")


def test_validate_pdf_structure_rejects_encrypted_pdf() -> None:
    with pytest.raises(FileValidationError, match="已加密"):
        validate_pdf_structure(make_pdf_bytes(user_password="user"))


def test_validate_pdf_structure_rejects_owner_only_encrypted_pdf() -> None:
    with pytest.raises(FileValidationError, match="已加密"):
        validate_pdf_structure(make_pdf_bytes(user_password=""))


def test_validate_pdf_structure_rejects_pdf_without_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pdf = SimpleNamespace(
        needs_pass=False,
        is_encrypted=False,
        page_count=0,
        xref_get_key=lambda *_: ("null", "null"),
        close=lambda: None,
    )
    monkeypatch.setattr("app.utils.file_utils.fitz.open", lambda **_: fake_pdf)

    with pytest.raises(FileValidationError, match="不包含有效页面"):
        validate_pdf_structure(b"%PDF-empty")


def test_validate_pdf_structure_maps_structure_read_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenPdf:
        needs_pass = False
        is_encrypted = False

        def xref_get_key(self, *_: object) -> tuple[str, str]:
            return ("null", "null")

        @property
        def page_count(self) -> int:
            raise RuntimeError("broken xref")

        def close(self) -> None:
            return None

    monkeypatch.setattr("app.utils.file_utils.fitz.open", lambda **_: BrokenPdf())

    with pytest.raises(FileValidationError, match="已损坏或格式无效"):
        validate_pdf_structure(b"%PDF-broken")


def test_validate_pdf_bytes_checks_size_before_structure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)

    with pytest.raises(FileValidationError, match="超过 1MB"):
        validate_pdf_bytes(b"%PDF" + b"x" * (1024 * 1024), "large.pdf")


def test_extraction_pdf_uses_same_structure_validation() -> None:
    with pytest.raises(FileValidationError, match="已损坏或格式无效"):
        validate_extraction_upload_bytes(b"%PDF-not-a-real-document", "contract.pdf")
