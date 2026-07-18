from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from app.config import settings
from app.infrastructure.task_index import CompareTaskIndex
from app.infrastructure.task_repository import LocalJsonTaskRepository
from app.models import CompareTask, DiffItem


def configure_task_storage(tmp_path: Path) -> LocalJsonTaskRepository:
    settings.storage_dir = tmp_path / "storage"
    settings.tasks_dir = settings.storage_dir / "tasks"
    return LocalJsonTaskRepository(settings)


def test_index_persists_only_comparison_record_summary_fields(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)
    repository.save_compare_task(
        CompareTask(
            task_id="TINDEX",
            owner_sub="user-1",
            original_filename="original.pdf",
            compare_filename="compare.pdf",
            diffs=[DiffItem(diff_id="D001", diff_type="ADD", original_text="confidential contract text")],
            ocr_raw_result_path="ocr/raw.json",
            document_profiles={"original": {"raw_text": "OCR content"}},
        )
    )

    payload = json.loads((settings.storage_dir / "indexes" / "compare_records.json").read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert set(payload["records"]["TINDEX"]) == {
        "task_id",
        "owner_sub",
        "status",
        "terminal_reason",
        "revision",
        "report_revision",
        "stage",
        "progress_percent",
        "created_at",
        "updated_at",
        "original_filename",
        "compare_filename",
        "diff_count",
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "confidential contract text" not in serialized
    assert "OCR content" not in serialized
    assert "ocr/raw.json" not in serialized


def test_task_write_survives_recoverable_index_update_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = configure_task_storage(tmp_path)

    def fail_update(_task: CompareTask) -> None:
        raise OSError("index unavailable")

    monkeypatch.setattr(repository.task_index, "upsert", fail_update)

    repository.save_compare_task(CompareTask(task_id="TINDEX_FAILURE"))

    assert repository.load_compare_task("TINDEX_FAILURE").task_id == "TINDEX_FAILURE"
    assert "Comparison record index refresh failed after authoritative task commit" in caplog.text


def test_rebuild_index_is_deterministic_and_skips_invalid_or_extraction_payloads(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)
    repository.save_compare_task(
        CompareTask(
            task_id="TVALID",
            owner_sub="user-1",
            created_at="2026-05-21T08:00:00+00:00",
            original_filename="valid-original.pdf",
            compare_filename="valid-compare.pdf",
        )
    )
    extraction_path = settings.tasks_dir / "TEXTRACTION" / "task.json"
    extraction_path.parent.mkdir(parents=True)
    extraction_path.write_text(
        json.dumps({"task_id": "TEXTRACTION", "task_type": "extraction", "status": "COMPLETED"}),
        encoding="utf-8",
    )
    broken_path = settings.tasks_dir / "TBROKEN" / "task.json"
    broken_path.parent.mkdir(parents=True)
    broken_path.write_text("{", encoding="utf-8")

    index = CompareTaskIndex(settings)
    first = index.rebuild()
    first_payload = json.loads(index.path.read_text(encoding="utf-8"))
    second = index.rebuild()
    second_payload = json.loads(index.path.read_text(encoding="utf-8"))

    assert first == second == 1
    assert first_payload["records"] == second_payload["records"] == {"TVALID": first_payload["records"]["TVALID"]}
    assert list(first_payload["records"]) == ["TVALID"]


def test_rebuild_script_runs_from_repository_root(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "backend/scripts/rebuild_task_index.py"],
        cwd=repository_root,
        env={**os.environ, "STORAGE_DIR": str(tmp_path / "storage")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "Rebuilt 0 comparison record index entries.\n"


def test_rebuild_does_not_drop_upsert_started_during_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = configure_task_storage(tmp_path)
    repository.save_compare_task(CompareTask(task_id="TOLD"))
    index = CompareTaskIndex(settings)
    snapshot_read_started = threading.Event()
    resume_rebuild = threading.Event()
    upsert_started = threading.Event()
    original_read_text = Path.read_text
    original_upsert = repository.task_index.upsert

    def pause_snapshot_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == repository.task_json_path("TOLD"):
            snapshot_read_started.set()
            resume_rebuild.wait(timeout=5)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", pause_snapshot_read)
    rebuild_errors: list[BaseException] = []
    writer_errors: list[BaseException] = []

    def signal_upsert(task: CompareTask) -> None:
        upsert_started.set()
        original_upsert(task)

    monkeypatch.setattr(repository.task_index, "upsert", signal_upsert)

    def rebuild() -> None:
        try:
            index.rebuild()
        except BaseException as exc:  # pragma: no cover - surfaced below
            rebuild_errors.append(exc)

    rebuild_thread = threading.Thread(target=rebuild)
    rebuild_thread.start()
    assert snapshot_read_started.wait(timeout=5)

    def save_task() -> None:
        try:
            repository.save_compare_task(CompareTask(task_id="TNEW"))
        except BaseException as exc:  # pragma: no cover - surfaced below
            writer_errors.append(exc)

    writer_thread = threading.Thread(target=save_task)
    writer_thread.start()
    assert upsert_started.wait(timeout=5)
    resume_rebuild.set()
    rebuild_thread.join(timeout=5)
    writer_thread.join(timeout=5)

    assert not rebuild_thread.is_alive()
    assert not writer_thread.is_alive()
    assert not rebuild_errors
    assert not writer_errors
    assert {record["task_id"] for record in index.list_records()} == {"TOLD", "TNEW"}
