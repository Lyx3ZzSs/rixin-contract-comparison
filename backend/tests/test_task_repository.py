from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.infrastructure.task_repository import SQLiteTaskRepository
from app.models import CompareTask, DiffItem


def repository(tmp_path: Path) -> SQLiteTaskRepository:
    return SQLiteTaskRepository(Settings(storage_dir=tmp_path / "storage"))


def test_sqlite_is_the_only_structured_task_authority(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    task = CompareTask(
        task_id="T1",
        owner_sub="owner",
        original_filename="原合同.pdf",
        compare_filename="新合同.pdf",
        diffs=[DiffItem(diff_id="D1", diff_type="ADD", compare_text="新增")],
    )

    repo.save_compare_task(task)
    loaded = repo.load_compare_task("T1")

    assert loaded.diffs[0].compare_text == "新增"
    assert loaded.original_pdf_path.endswith("tasks/T1/input/original/原合同.pdf")
    assert loaded.compare_pdf_path.endswith("tasks/T1/input/compare/新合同.pdf")
    assert repo.settings.task_database_path.exists()
    assert not (repo.settings.tasks_dir / "T1" / "task.json").exists()
    assert not (repo.settings.storage_dir / "indexes").exists()


def test_summary_query_does_not_need_task_json(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.save_compare_task(
        CompareTask(
            task_id="T1",
            owner_sub="owner",
            original_filename="a.pdf",
            compare_filename="b.pdf",
            diffs=[DiffItem(diff_id="D1", diff_type="ADD")],
        )
    )

    assert repo.list_compare_record_summaries()[0]["diff_count"] == 1


def test_update_is_transactional_and_owner_is_immutable(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.save_compare_task(CompareTask(task_id="T1", owner_sub="owner"))
    revision = repo.load_compare_task("T1").revision

    updated = repo.update_compare_task("T1", lambda task: setattr(task, "stage", "处理中"))
    assert updated.revision == revision + 1
    assert repo.load_compare_task("T1").stage == "处理中"

    with pytest.raises(ValueError, match="所有者"):
        repo.update_compare_task("T1", lambda task: object.__setattr__(task, "owner_sub", "other"))


def test_startup_marks_all_processing_tasks_failed_without_reading_legacy_files(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    repo.save_compare_task(CompareTask(task_id="RUNNING", status="PROCESSING"))
    repo.save_compare_task(CompareTask(task_id="DONE", status="COMPLETED"))
    legacy = repo.settings.tasks_dir / "legacy-extraction" / "task.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"task_type":"extraction"}', encoding="utf-8")

    assert repo.fail_interrupted_tasks() == 1
    failed = repo.load_compare_task("RUNNING")
    assert failed.status == "FAILED"
    assert failed.execution_error_code == "SERVICE_RESTARTED"
    assert repo.load_compare_task("DONE").status == "COMPLETED"
    assert legacy.exists()
