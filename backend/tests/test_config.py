from __future__ import annotations

from pathlib import Path

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
