from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.infrastructure.artifact_store import LocalArtifactStore
from app.utils.file_utils import FileValidationError


def test_local_artifact_store_resolves_task_artifact_paths(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")
    store = LocalArtifactStore(app_settings)

    assert store.upload_path("T/001", "original", "a.pdf") == (
        app_settings.tasks_dir / "T_001" / "uploads" / "original_a.pdf"
    )
    assert store.report_pdf_path("T001") == (
        app_settings.tasks_dir / "T001" / "reports" / "contract_compare_report.pdf"
    )
    assert store.raw_json_path("T001", "合同 初稿.pdf", "ppocrv5_raw").name == "合同_初稿_ppocrv5_raw.json"


def test_local_artifact_store_guards_writes_outside_storage(tmp_path: Path) -> None:
    store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))

    with pytest.raises(FileValidationError):
        store.write_json(tmp_path / "outside.json", {"ok": True})


def test_local_artifact_store_writes_json_inside_storage(tmp_path: Path) -> None:
    store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    path = store.debug_json_path("T001", "debug.json")

    written = store.write_json(path, {"ok": True})

    assert written == path
    assert path.read_text(encoding="utf-8").strip().startswith("{")
    assert (path.parents[1] / "manifest.json").exists()
