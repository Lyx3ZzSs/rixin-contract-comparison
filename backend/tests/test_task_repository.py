from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.config import settings
from app.config import Settings
from app.errors import TaskRepositoryReadError, TaskTransitionConflict
from app.infrastructure.task_repository import LocalJsonTaskRepository, build_task_repository
from app.models import CompareTask, DiffItem


def configure_task_storage(tmp_path: Path) -> LocalJsonTaskRepository:
    settings.storage_dir = tmp_path / "storage"
    settings.tasks_dir = settings.storage_dir / "tasks"
    return LocalJsonTaskRepository(settings)


def test_compare_listing_skips_legacy_extraction_payload(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)

    repository.save_compare_task(CompareTask(task_id="TCOMPARE", updated_at="2026-05-21T09:00:00+00:00"))
    (settings.tasks_dir / "TEXTRACT").mkdir(parents=True)
    (settings.tasks_dir / "TEXTRACT" / "task.json").write_text(
        json.dumps({"task_id": "TEXTRACT", "task_type": "extraction", "status": "COMPLETED"}),
        encoding="utf-8",
    )

    assert [task.task_id for task in repository.list_compare_tasks()] == ["TCOMPARE"]


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
    repository.save_compare_task(CompareTask(task_id="TPARTIAL", stage="first", original_filename="original.pdf"))
    first = repository.load_compare_task("TPARTIAL")

    updated = repository.update_compare_task(
        "TPARTIAL",
        lambda task: setattr(task, "stage", "second"),
    )

    assert updated.stage == "second"
    assert updated.original_filename == "original.pdf"
    assert updated.revision == first.revision + 1


def test_local_json_task_repository_update_returns_isolated_committed_snapshot(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)
    repository.save_compare_task(CompareTask(task_id="TISOLATED", stage="first"))

    updated = repository.update_compare_task(
        "TISOLATED",
        lambda task: setattr(task, "stage", "second"),
    )
    committed_revision = updated.revision
    updated.stage = "caller mutation"
    updated.revision = 999

    stored = repository.load_compare_task("TISOLATED")
    assert (stored.stage, stored.revision) == ("second", committed_revision)


def test_local_json_task_repository_validates_update_before_primary_write(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)
    repository.save_compare_task(CompareTask(task_id="TVALIDATE_BEFORE_WRITE", stage="before"))
    primary_path = repository.task_json_path("TVALIDATE_BEFORE_WRITE")
    before = primary_path.read_text(encoding="utf-8")

    with pytest.raises(PydanticValidationError):
        repository.update_compare_task(
            "TVALIDATE_BEFORE_WRITE",
            lambda task: setattr(task, "status", "INVALID"),
        )

    assert primary_path.read_text(encoding="utf-8") == before
    assert repository.load_compare_task("TVALIDATE_BEFORE_WRITE").stage == "before"


def test_load_compare_task_preserves_missing_task_error(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)

    with pytest.raises(FileNotFoundError, match="任务不存在"):
        repository.load_compare_task("TMISSING")


def test_load_compare_task_wraps_os_read_error_with_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = configure_task_storage(tmp_path)
    repository.save_compare_task(CompareTask(task_id="TREAD_OS_ERROR"))

    def fail_read(_task_id: str) -> dict[str, object]:
        raise OSError("primary unavailable")

    monkeypatch.setattr(repository, "_read_task_data", fail_read)

    with pytest.raises(TaskRepositoryReadError, match="TREAD_OS_ERROR") as raised:
        repository.load_compare_task("TREAD_OS_ERROR")

    assert isinstance(raised.value.__cause__, OSError)
    assert str(raised.value.__cause__) == "primary unavailable"


@pytest.mark.parametrize(
    ("payload", "cause_type"),
    [
        ("{", json.JSONDecodeError),
        ("[]", TypeError),
        (json.dumps({"task_id": "TINVALID_DATA", "status": "INVALID"}), PydanticValidationError),
    ],
)
def test_load_compare_task_wraps_corrupt_or_invalid_primary_data(
    tmp_path: Path,
    payload: str,
    cause_type: type[Exception],
) -> None:
    repository = configure_task_storage(tmp_path)
    task_id = "TINVALID_DATA"
    path = repository.task_json_path(task_id)
    path.parent.mkdir(parents=True)
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(TaskRepositoryReadError, match=task_id) as raised:
        repository.load_compare_task(task_id)

    assert isinstance(raised.value.__cause__, cause_type)


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
    repository.update_compare_task(
        "TCOMPARE", lambda task: task.diffs.append(DiffItem(diff_id="D001", diff_type="ADD"))
    )

    assert repository.task_json_path("TCOMPARE") == settings.tasks_dir / "TCOMPARE" / "task.json"
    assert (settings.tasks_dir / "TCOMPARE" / "manifest.json").exists()
    assert repository.load_compare_task("TCOMPARE").stage == "second"
    assert repository.load_compare_task("TCOMPARE").diffs[0].diff_id == "D001"
    assert [task.task_id for task in repository.list_compare_tasks()] == ["TCOMPARE"]


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


@pytest.mark.parametrize(
    ("payload", "expected_reason", "expected_report_revision"),
    [
        ({"status": "PROCESSING"}, "NONE", 0),
        ({"status": "COMPLETED"}, "NONE", 1),
        ({"status": "FAILED", "stage": "已取消"}, "CANCELLED", 0),
        ({"status": "FAILED", "errors": ["任务已取消。"]}, "CANCELLED", 0),
        ({"status": "FAILED", "errors": ["pipeline failed"]}, "EXECUTION_FAILED", 0),
    ],
)
def test_local_json_task_repository_projects_legacy_terminal_state(
    tmp_path: Path,
    payload: dict[str, object],
    expected_reason: str,
    expected_report_revision: int,
) -> None:
    repository = configure_task_storage(tmp_path)
    task_dir = repository.task_dir("TLEGACY_STATE")
    task_dir.mkdir(parents=True)
    repository.task_json_path("TLEGACY_STATE").write_text(
        json.dumps({"task_id": "TLEGACY_STATE", **payload}),
        encoding="utf-8",
    )

    loaded = repository.load_compare_task("TLEGACY_STATE")

    assert loaded.terminal_reason == expected_reason
    assert loaded.report_revision == expected_report_revision


def test_compare_task_preserves_explicit_terminal_and_report_revisions() -> None:
    failed = CompareTask(
        task_id="TSUBMISSION",
        status="FAILED",
        terminal_reason="SUBMISSION_FAILED",
        report_revision=4,
    )
    completed_without_a_report = CompareTask(
        task_id="TCOMPLETED_WITH_ZERO",
        status="COMPLETED",
        report_revision=0,
    )
    inconsistent_nonterminal = CompareTask(
        task_id="TPROCESSING_REASON",
        status="PROCESSING",
        terminal_reason="CANCELLED",
    )

    assert failed.terminal_reason == "SUBMISSION_FAILED"
    assert failed.report_revision == 4
    assert completed_without_a_report.report_revision == 0
    assert inconsistent_nonterminal.terminal_reason == "NONE"


def test_compare_task_allows_only_legal_state_transitions() -> None:
    processing = CompareTask(task_id="TPROCESSING", active_job_id="compare:TPROCESSING:1")
    execution_failed = CompareTask(
        task_id="TEXECUTION_FAILED",
        status="FAILED",
        terminal_reason="EXECUTION_FAILED",
    )
    submission_failed = CompareTask(
        task_id="TSUBMISSION_FAILED",
        status="FAILED",
        terminal_reason="SUBMISSION_FAILED",
    )

    processing.ensure_transition_allowed(
        "COMPLETED",
        terminal_reason="NONE",
        job_id="compare:TPROCESSING:1",
    )
    execution_failed.ensure_transition_allowed("PROCESSING")
    submission_failed.ensure_transition_allowed("PROCESSING", validated_inputs_exist=True)


@pytest.mark.parametrize("job_id", ["", "compare:TACTIVE_SUBMISSION:1"])
def test_compare_task_rejects_submission_failure_from_non_active_job(job_id: str) -> None:
    task = CompareTask(
        task_id="TACTIVE_SUBMISSION",
        active_job_id="compare:TACTIVE_SUBMISSION:2",
    )

    with pytest.raises(TaskTransitionConflict):
        task.ensure_transition_allowed(
            "FAILED",
            terminal_reason="SUBMISSION_FAILED",
            job_id=job_id,
        )


def test_compare_task_allows_submission_failure_without_job_when_no_job_is_active() -> None:
    task = CompareTask(task_id="TNO_ACTIVE_SUBMISSION")

    task.ensure_transition_allowed(
        "FAILED",
        terminal_reason="SUBMISSION_FAILED",
    )


@pytest.mark.parametrize(
    ("task", "target_status", "terminal_reason", "job_id", "validated_inputs_exist"),
    [
        (CompareTask(task_id="TCOMPLETED", status="COMPLETED"), "FAILED", "EXECUTION_FAILED", "", False),
        (
            CompareTask(task_id="TCANCELLED", status="FAILED", terminal_reason="CANCELLED"),
            "PROCESSING",
            "NONE",
            "",
            True,
        ),
        (
            CompareTask(task_id="TSUBMIT", status="FAILED", terminal_reason="SUBMISSION_FAILED"),
            "PROCESSING",
            "NONE",
            "",
            False,
        ),
        (
            CompareTask(task_id="TSTALE", active_job_id="compare:TSTALE:2"),
            "FAILED",
            "EXECUTION_FAILED",
            "compare:TSTALE:1",
            False,
        ),
    ],
)
def test_compare_task_rejects_illegal_state_transitions(
    task: CompareTask,
    target_status: str,
    terminal_reason: str,
    job_id: str,
    validated_inputs_exist: bool,
) -> None:
    with pytest.raises(TaskTransitionConflict):
        task.ensure_transition_allowed(
            target_status,
            terminal_reason=terminal_reason,
            job_id=job_id,
            validated_inputs_exist=validated_inputs_exist,
        )
