from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.infrastructure.artifact_store import ArtifactStore
from app.infrastructure.report_store import ReportStore
from app.models import CompareTask


class RecordingGenerator:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.calls = 0
        self.failure = failure

    def generate(self, _task: CompareTask, output_path: str | Path) -> Path:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        path = Path(output_path)
        path.write_bytes(b"report")
        return path


def _store(tmp_path: Path, generator: RecordingGenerator) -> tuple[ReportStore, ArtifactStore]:
    artifacts = ArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    return ReportStore(artifact_store=artifacts, generator=generator), artifacts


def test_report_path_is_revision_cache_key_without_manifest(tmp_path: Path) -> None:
    generator = RecordingGenerator()
    store, artifacts = _store(tmp_path, generator)

    path = store.ensure_report(CompareTask(task_id="T1", status="COMPLETED", report_revision=12))

    assert path == artifacts.task_root("T1") / "report" / "report-r12.pdf"
    assert generator.calls == 1
    assert not list(path.parent.glob("*.manifest.json"))


def test_valid_current_report_is_reused(tmp_path: Path) -> None:
    generator = RecordingGenerator()
    store, _artifacts = _store(tmp_path, generator)
    task = CompareTask(task_id="T1", status="COMPLETED", report_revision=1)

    first = store.ensure_report(task)
    second = store.ensure_report(task)

    assert first == second
    assert generator.calls == 1


def test_empty_report_is_regenerated_and_old_revision_removed(tmp_path: Path) -> None:
    generator = RecordingGenerator()
    store, artifacts = _store(tmp_path, generator)
    empty = artifacts.report_pdf_path("T1", 2)
    old = artifacts.report_pdf_path("T1", 1)
    empty.parent.mkdir(parents=True)
    empty.write_bytes(b"")
    old.write_bytes(b"old")

    current = store.ensure_report(CompareTask(task_id="T1", status="COMPLETED", report_revision=2))

    assert current.read_bytes() == b"report"
    assert not old.exists()


def test_generation_failure_keeps_previous_revision_and_cleans_temp(tmp_path: Path) -> None:
    generator = RecordingGenerator(failure=RuntimeError("generation failed"))
    store, artifacts = _store(tmp_path, generator)
    old = artifacts.report_pdf_path("T1", 1)
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")

    with pytest.raises(RuntimeError, match="generation failed"):
        store.ensure_report(CompareTask(task_id="T1", status="COMPLETED", report_revision=2))

    assert old.read_bytes() == b"old"
    assert not list(old.parent.glob("*.tmp"))
