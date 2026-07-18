from __future__ import annotations

import json
from pathlib import Path

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
