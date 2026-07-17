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


def test_local_artifact_store_publishes_staged_upload_without_overwriting_existing(tmp_path: Path) -> None:
    store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    staged = store.staging_path("T001", "attempt-1", "original", "contract.pdf")
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"new")
    destination = store.upload_path("T001", "original", "contract.pdf")

    published = store.publish_staged(staged, destination)

    assert published == destination
    assert destination.read_bytes() == b"new"
    assert not staged.exists()

    staged.write_bytes(b"replacement")
    with pytest.raises(FileExistsError):
        store.publish_staged(staged, destination)
    assert destination.read_bytes() == b"new"


def test_publish_reports_success_after_destination_commit_when_derived_manifest_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    staged = store.staging_path("T001", "attempt-1", "original", "contract.pdf")
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"new")
    destination = store.upload_path("T001", "original", "contract.pdf")
    monkeypatch.setattr(store, "_record_manifest", lambda *_args: (_ for _ in ()).throw(OSError("manifest")))

    assert store.publish_staged(staged, destination) == destination
    assert destination.read_bytes() == b"new"
    assert not staged.exists()


def test_publish_reports_success_and_leaves_staging_for_compensation_when_unlink_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    staged = store.staging_path("T001", "attempt-1", "original", "contract.pdf")
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"new")
    destination = store.upload_path("T001", "original", "contract.pdf")
    unlink = Path.unlink

    def fail_staged_unlink(path: Path, *args, **kwargs) -> None:
        if path == staged:
            raise OSError("staging unlink")
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_staged_unlink)

    assert store.publish_staged(staged, destination) == destination
    assert destination.read_bytes() == b"new"
    assert staged.exists()


def test_publish_created_callback_failure_rolls_back_destination_and_preserves_primary(tmp_path: Path) -> None:
    store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    staged = store.staging_path("T001", "attempt-1", "original", "contract.pdf")
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"new")
    destination = store.upload_path("T001", "original", "contract.pdf")

    with pytest.raises(OSError, match="callback primary"):
        store.publish_staged(
            staged,
            destination,
            on_created=lambda _path: (_ for _ in ()).throw(OSError("callback primary")),
        )

    assert staged.read_bytes() == b"new"
    assert not destination.exists()
