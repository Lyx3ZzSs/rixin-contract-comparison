from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.infrastructure.task_repository import LocalJsonTaskRepository
from app.models import CompareTask
from app.models_extraction import ExtractionTask


def configure_task_storage(tmp_path: Path) -> LocalJsonTaskRepository:
    settings.storage_dir = tmp_path / "storage"
    settings.tasks_dir = settings.storage_dir / "tasks"
    return LocalJsonTaskRepository(settings)


def test_local_json_task_repository_separates_task_types(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)

    repository.save_compare_task(CompareTask(task_id="TCOMPARE", updated_at="2026-05-21T09:00:00+00:00"))
    repository.save_extraction_task(ExtractionTask(task_id="TEXTRACT", updated_at="2026-05-21T10:00:00+00:00"))

    assert [task.task_id for task in repository.list_compare_tasks()] == ["TCOMPARE"]
    assert [task.task_id for task in repository.list_extraction_tasks()] == ["TEXTRACT"]


def test_local_json_task_repository_uses_last_write(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)

    repository.save_compare_task(CompareTask(task_id="TUPDATE", stage="first"))
    repository.save_compare_task(CompareTask(task_id="TUPDATE", stage="second"))

    assert repository.load_compare_task("TUPDATE").stage == "second"


def test_local_json_task_repository_skips_invalid_list_entries(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)
    repository.save_compare_task(CompareTask(task_id="TVALID"))
    (settings.tasks_dir / "broken.json").write_text("{", encoding="utf-8")

    assert [task.task_id for task in repository.list_compare_tasks()] == ["TVALID"]
