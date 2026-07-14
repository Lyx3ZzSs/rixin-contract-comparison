from __future__ import annotations

import json
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


def test_local_json_task_repository_orders_compare_tasks_by_created_at_desc(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)
    older_created_newer_updated = CompareTask(
        task_id="TOLDER_CREATED",
        created_at="2026-05-20T08:00:00+00:00",
        updated_at="2026-05-23T10:00:00+00:00",
    )
    newer_created_older_updated = CompareTask(
        task_id="TNEWER_CREATED",
        created_at="2026-05-22T08:00:00+00:00",
        updated_at="2026-05-22T10:00:00+00:00",
    )
    repository.save_compare_task(older_created_newer_updated)
    repository.task_json_path(older_created_newer_updated.task_id).write_text(
        json.dumps(json.loads(older_created_newer_updated.model_dump_json()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    repository.save_compare_task(newer_created_older_updated)
    repository.task_json_path(newer_created_older_updated.task_id).write_text(
        json.dumps(json.loads(newer_created_older_updated.model_dump_json()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert [task.task_id for task in repository.list_compare_tasks()] == ["TNEWER_CREATED", "TOLDER_CREATED"]


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


def test_local_json_task_repository_writes_compare_task_v2_structured_paths(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)
    task_dir = repository.task_dir("TPATHS")
    original_pdf = task_dir / "uploads" / "original_contract.pdf"
    compare_pdf = task_dir / "uploads" / "compare_contract.pdf"
    original_raw = task_dir / "ocr" / "original_contract_ppstructure_raw.json"
    compare_hybrid_raw = task_dir / "ocr" / "compare_contract_ppstructure_ocr_hybrid_raw.json"
    debug_path = task_dir / "debug" / "document_profiles.json"

    repository.save_compare_task(
        CompareTask(
            task_id="TPATHS",
            original_pdf_path=str(original_pdf),
            compare_pdf_path=str(compare_pdf),
            original_highlight_pdf_path="",
            compare_highlight_pdf_path="",
            report_pdf_path="",
            ocr_raw_result_path=f"{original_raw}\n{compare_hybrid_raw}",
            debug_artifact_paths={"document_profiles": str(debug_path)},
            diff_count=99,
            diffs=[DiffItem(diff_id="D001", diff_type="ADD")],
        )
    )

    raw = json.loads(repository.task_json_path("TPATHS").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2
    assert raw["diff_count"] == 1
    assert raw["original_pdf_path"] == "uploads/original_contract.pdf"
    assert raw["compare_pdf_path"] == "uploads/compare_contract.pdf"
    assert raw["original_highlight_pdf_path"] is None
    assert raw["compare_highlight_pdf_path"] is None
    assert raw["report_pdf_path"] is None
    assert raw["ocr_raw_result_path"] == ""
    assert raw["ocr_raw_result_paths"]["original"]["ppstructure"] == "ocr/original_contract_ppstructure_raw.json"
    assert raw["ocr_raw_result_paths"]["compare"]["hybrid"] == "ocr/compare_contract_ppstructure_ocr_hybrid_raw.json"
    assert raw["debug_artifact_paths"]["document_profiles"] == "debug/document_profiles.json"

    loaded = repository.load_compare_task("TPATHS")
    assert loaded.original_pdf_path == str(original_pdf)
    assert loaded.compare_pdf_path == str(compare_pdf)
    assert loaded.original_highlight_pdf_path is None
    assert loaded.report_pdf_path is None
    assert loaded.ocr_raw_result_paths.original.ppstructure == str(original_raw)
    assert loaded.ocr_raw_result_paths.compare.hybrid == str(compare_hybrid_raw)
    assert loaded.debug_artifact_paths["document_profiles"] == str(debug_path)


def test_local_json_task_repository_loads_legacy_compare_task_paths(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)
    task_dir = repository.task_dir("TLEGACY")
    task_dir.mkdir(parents=True)
    legacy_payload = {
        "task_id": "TLEGACY",
        "schema_version": 1,
        "status": "COMPLETED",
        "original_pdf_path": "uploads/original.pdf",
        "compare_pdf_path": "uploads/compare.pdf",
        "original_highlight_pdf_path": "",
        "compare_highlight_pdf_path": "",
        "report_pdf_path": "",
        "ocr_raw_result_path": "\n".join(
            [
                "ocr/original_contract_ppocrv5_raw.json",
                "ocr/compare_contract_ppstructure_raw.json",
            ]
        ),
        "diffs": [],
    }
    repository.task_json_path("TLEGACY").write_text(json.dumps(legacy_payload), encoding="utf-8")

    loaded = repository.load_compare_task("TLEGACY")

    assert loaded.original_pdf_path == str(task_dir / "uploads" / "original.pdf")
    assert loaded.compare_pdf_path == str(task_dir / "uploads" / "compare.pdf")
    assert loaded.original_highlight_pdf_path is None
    assert loaded.compare_highlight_pdf_path is None
    assert loaded.report_pdf_path is None
    assert loaded.ocr_raw_result_paths.original.ppocrv5 == str(task_dir / "ocr" / "original_contract_ppocrv5_raw.json")
    assert loaded.ocr_raw_result_paths.compare.ppstructure == str(
        task_dir / "ocr" / "compare_contract_ppstructure_raw.json"
    )


def test_local_json_task_repository_loads_legacy_task_without_owner_fields(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)
    task_dir = repository.task_dir("TLEGACY_OWNER")
    task_dir.mkdir(parents=True)
    repository.task_json_path("TLEGACY_OWNER").write_text(
        json.dumps({"task_id": "TLEGACY_OWNER", "status": "COMPLETED", "diffs": []}),
        encoding="utf-8",
    )

    loaded = repository.load_compare_task("TLEGACY_OWNER")

    assert loaded.owner_sub == ""
    assert loaded.owner_username == ""
