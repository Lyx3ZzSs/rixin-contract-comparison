from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_ensure_storage_only_precreates_tasks_directory(tmp_path: Path) -> None:
    app_settings = Settings(
        storage_dir=tmp_path / "storage",
        uploads_dir=None,
        tasks_dir=None,
        reports_dir=None,
        ocr_dir=None,
        debug_dir=None,
        cache_dir=None,
    )

    app_settings.ensure_storage()

    assert app_settings.tasks_dir.exists()
    for directory in [
        app_settings.uploads_dir,
        app_settings.reports_dir,
        app_settings.ocr_dir,
        app_settings.debug_dir,
        app_settings.cache_dir,
    ]:
        assert not directory.exists()


def test_compare_defaults_to_strict_structured_ocr() -> None:
    app_settings = Settings()

    assert app_settings.compare_document_extractor == "ppstructure_ocr_hybrid"
    assert app_settings.compare_require_structured_ocr is True


def test_compare_rejects_non_structured_extractor_in_strict_mode() -> None:
    with pytest.raises(ValidationError, match="COMPARE_DOCUMENT_EXTRACTOR"):
        Settings(
            compare_document_extractor="pymupdf",
            compare_require_structured_ocr=True,
        )


def test_compare_allows_non_structured_extractor_when_strict_mode_is_disabled() -> None:
    app_settings = Settings(
        compare_document_extractor="pymupdf",
        compare_require_structured_ocr=False,
    )

    assert app_settings.compare_document_extractor == "pymupdf"
