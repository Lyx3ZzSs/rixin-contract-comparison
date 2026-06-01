from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.config import Settings
from app.infrastructure.task_repository import LocalJsonTaskRepository, build_task_repository
from app.models import CompareTask, DiffItem
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


def test_local_json_task_repository_partial_update_increments_revision(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)
    repository.save_compare_task(
        CompareTask(task_id="TPARTIAL", stage="first", original_filename="original.pdf")
    )
    first = repository.load_compare_task("TPARTIAL")

    updated = repository.update_compare_task(
        "TPARTIAL",
        lambda task: setattr(task, "stage", "second"),
    )

    assert updated.stage == "second"
    assert updated.original_filename == "original.pdf"
    assert updated.revision == first.revision + 1


def test_local_json_task_repository_skips_invalid_list_entries(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)
    repository.save_compare_task(CompareTask(task_id="TVALID"))
    broken_dir = settings.tasks_dir / "TBROKEN"
    broken_dir.mkdir(parents=True)
    (broken_dir / "task.json").write_text("{", encoding="utf-8")

    assert [task.task_id for task in repository.list_compare_tasks()] == ["TVALID"]


def test_task_repository_factory_always_uses_local_file_backend(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage")

    repository = build_task_repository(app_settings)

    assert isinstance(repository, LocalJsonTaskRepository)


def test_local_json_task_repository_contract_in_task_directory(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)

    repository.save_compare_task(CompareTask(task_id="TCOMPARE", stage="first"))
    repository.save_compare_task(CompareTask(task_id="TCOMPARE", stage="second"))
    repository.save_extraction_task(ExtractionTask(task_id="TEXTRACT"))
    repository.update_compare_task("TCOMPARE", lambda task: task.diffs.append(DiffItem(diff_id="D001", diff_type="ADD")))

    assert repository.task_json_path("TCOMPARE") == settings.tasks_dir / "TCOMPARE" / "task.json"
    assert (settings.tasks_dir / "TCOMPARE" / "manifest.json").exists()
    assert repository.load_compare_task("TCOMPARE").stage == "second"
    assert repository.load_compare_task("TCOMPARE").diffs[0].diff_id == "D001"
    assert [task.task_id for task in repository.list_compare_tasks()] == ["TCOMPARE"]
    assert [task.task_id for task in repository.list_extraction_tasks()] == ["TEXTRACT"]
