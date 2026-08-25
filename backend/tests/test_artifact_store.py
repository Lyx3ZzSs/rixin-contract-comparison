from __future__ import annotations

import os
import time
from pathlib import Path

from app.config import Settings
from app.infrastructure.artifact_store import ArtifactStore


def test_fixed_layout_preserves_original_filenames(tmp_path: Path) -> None:
    store = ArtifactStore(Settings(storage_dir=tmp_path / "storage"))

    assert store.upload_path("T1", "original", "合同 初稿.pdf") == (
        tmp_path / "storage" / "tasks" / "T1" / "input" / "original" / "合同 初稿.pdf"
    )
    assert store.upload_path("T1", "compare", "合同 终稿.pdf") == (
        tmp_path / "storage" / "tasks" / "T1" / "input" / "compare" / "合同 终稿.pdf"
    )
    assert store.report_pdf_path("T1", 3).name == "report-r3.pdf"


def test_publish_uses_atomic_move_and_does_not_create_manifest(tmp_path: Path) -> None:
    store = ArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    source = store.staging_path("T1", "attempt", "original", "合同.pdf")
    destination = store.upload_path("T1", "original", "合同.pdf")
    source.parent.mkdir(parents=True)
    source.write_bytes(b"pdf")

    assert store.publish_staged(source, destination) == destination
    assert destination.read_bytes() == b"pdf"
    assert not source.exists()
    assert not (store.task_root("T1") / "manifest.json").exists()


def test_diagnostics_and_expired_staging_have_simple_cleanup(tmp_path: Path) -> None:
    store = ArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    diagnostic = store.debug_json_path("T1", "quality.json")
    store.write_json(diagnostic, {"ok": True})
    stale = store.staging_path("T1", "stale", "original", "a.pdf")
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"x")
    old = time.time() - 2 * 24 * 60 * 60
    os.utime(stale.parent.parent, (old, old))

    store.remove_diagnostics("T1")
    store.remove_expired_staging("T1")

    assert not diagnostic.exists()
    assert not stale.exists()
