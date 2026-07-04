from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.quality_workbench import (
    InvalidQualityWorkbenchIdError,
    QualityExpectedDiffNotFoundError,
    QualityTaskNotFoundError,
    QualityWorkbenchService,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_case(case_root: Path, case_id: str = "case-001") -> Path:
    case_dir = case_root / case_id
    _write_json(
        case_dir / "expected.json",
        {
            "case_id": case_id,
            "source_task_id": "task-001",
            "source_files": {
                "original_filename": "original.pdf",
                "compare_filename": "compare.pdf",
            },
            "expected_diffs": [
                {"review_status": "APPROVED", "title_contains": "date"},
                {"review_status": "DRAFT", "title_contains": "amount"},
                {"review_status": "REJECTED", "title_contains": "footer"},
            ],
        },
    )
    _write_json(
        case_dir / "actual.json",
        {
            "task_id": "task-001",
            "status": "COMPLETED",
            "stage": "已完成",
            "progress_percent": 100,
            "created_at": "2026-06-30T00:00:00+00:00",
            "updated_at": "2026-06-30T00:01:00+00:00",
            "original_filename": "original.pdf",
            "compare_filename": "compare.pdf",
            "diffs": [
                {
                    "diff_id": "D001",
                    "title": "date",
                    "diff_type": "MODIFY",
                    "source_type": "metadata",
                    "original_text": "date: 2026-06-01",
                    "compare_text": "date: 2026-06-30",
                },
                {
                    "diff_id": "D002",
                    "title": "amount",
                    "diff_type": "ADD",
                    "source_type": "clause",
                    "original_text": "",
                    "compare_text": "amount: 200",
                },
            ],
        },
    )
    (case_dir / "README.md").write_text(
        "# OCR Compare Gold Case: case-001\n", encoding="utf-8"
    )
    return case_dir


def test_list_cases_summarizes_annotation_counts(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    _make_case(case_root)

    cases = service.list_cases()

    assert len(cases) == 1
    assert cases[0]["case_id"] == "case-001"
    assert cases[0]["source_task_id"] == "task-001"
    assert cases[0]["approved_expected_count"] == 1
    assert cases[0]["draft_expected_count"] == 1
    assert cases[0]["rejected_expected_count"] == 1
    assert cases[0]["actual_diff_count"] == 2
    assert cases[0]["has_actual_json"] is True
    assert cases[0]["has_source_pdfs"] is False


def test_list_cases_includes_schema_11_metadata(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    case_dir = _make_case(case_root)
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    expected["schema_version"] = "1.1"
    expected["dataset_split"] = "regression"
    expected["case_tags"] = ["metadata", "date"]
    expected["baseline_required"] = True
    _write_json(case_dir / "expected.json", expected)

    cases = service.list_cases()

    assert cases[0]["schema_version"] == "1.1"
    assert cases[0]["dataset_split"] == "regression"
    assert cases[0]["case_tags"] == ["metadata", "date"]
    assert cases[0]["baseline_required"] is True


def test_list_cases_prefers_empty_schema_11_case_tags_over_legacy_tags(
    tmp_path: Path,
) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    case_dir = _make_case(case_root)
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    expected["case_tags"] = []
    expected["tags"] = ["legacy-tag"]
    _write_json(case_dir / "expected.json", expected)

    cases = service.list_cases()

    assert cases[0]["case_tags"] == []


def test_list_cases_counts_blank_expected_status_as_approved(
    tmp_path: Path,
) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    case_dir = _make_case(case_root)
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    expected["expected_diffs"] = [
        {"review_status": "APPROVED", "title_contains": "date"},
        {"review_status": "", "title_contains": "legacy reviewed diff"},
        {"title_contains": "missing legacy reviewed diff"},
        {"review_status": "DRAFT", "title_contains": "amount"},
    ]
    _write_json(case_dir / "expected.json", expected)

    cases = service.list_cases()

    assert cases[0]["approved_expected_count"] == 3
    assert cases[0]["draft_expected_count"] == 1
    assert cases[0]["rejected_expected_count"] == 0


def test_list_cases_requires_both_source_pdfs_for_pdf_availability(
    tmp_path: Path,
) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    case_dir = _make_case(case_root)
    (case_dir / "original.pdf").write_bytes(b"%PDF-1.4\n")

    cases = service.list_cases()

    assert cases[0]["has_source_pdfs"] is False

    (case_dir / "compare.pdf").write_bytes(b"%PDF-1.4\n")

    cases = service.list_cases()

    assert cases[0]["has_source_pdfs"] is True


def test_quality_workbench_rejects_path_traversal_ids(tmp_path: Path) -> None:
    service = QualityWorkbenchService(
        case_root=tmp_path / "cases",
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )

    with pytest.raises(InvalidQualityWorkbenchIdError):
        service.get_case("../secret")

    with pytest.raises(InvalidQualityWorkbenchIdError):
        service.get_case("..")

    with pytest.raises(InvalidQualityWorkbenchIdError):
        service.get_case(".")


def test_export_case_creates_draft_case_from_task(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    task_dir = task_root / "task-001"
    _write_json(
        task_dir / "task.json",
        {
            "task_id": "task-001",
            "status": "COMPLETED",
            "original_filename": "original.pdf",
            "compare_filename": "compare.pdf",
            "diffs": [
                {
                    "diff_id": "D001",
                    "title": "合同金额",
                    "diff_type": "MODIFY",
                    "source_type": "metadata",
                    "original_text": "金额为100元",
                    "compare_text": "金额为200元",
                }
            ],
        },
    )
    service = QualityWorkbenchService(
        case_root=tmp_path / "cases",
        task_root=task_root,
        output_root=tmp_path / ".ocr-compare-quality",
    )

    result = service.export_case("task-001", "case-001")

    assert result == {
        "case_id": "case-001",
        "task_id": "task-001",
        "expected_diff_count": 1,
        "actual_diff_count": 1,
    }
    case = service.get_case("case-001")
    assert case["expected"]["source_task_id"] == "task-001"
    assert case["expected"]["expected_diffs"][0]["review_status"] == "DRAFT"
    assert case["actual_diffs"][0]["diff_id"] == "D001"


def test_evaluate_cases_runs_quality_report_with_dataset_split(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    case_dir = _make_case(case_root)
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    expected["dataset_split"] = "regression"
    expected["expected_diffs"] = [
        {
            "review_status": "APPROVED",
            "title_contains": "date",
            "diff_type": "MODIFY",
            "source_type": "metadata",
            "original_contains": "2026-06-01",
            "compare_contains": "2026-06-30",
        },
        {
            "review_status": "APPROVED",
            "title_contains": "amount",
            "diff_type": "ADD",
            "source_type": "clause",
            "compare_contains": "200",
        },
    ]
    _write_json(case_dir / "expected.json", expected)

    result = service.evaluate_cases(
        dataset_splits={"regression"},
        run_id="eval-001",
    )

    assert result["run_id"] == "eval-001"
    assert result["status"] == "COMPLETED"
    assert result["report"]["dataset_splits"] == ["regression"]
    assert result["report"]["case_count"] == 1
    assert result["report"]["aggregate"]["recall"] == 1.0


def test_evaluate_cases_rejects_unsafe_run_id(tmp_path: Path) -> None:
    service = QualityWorkbenchService(
        case_root=tmp_path / "cases",
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )

    with pytest.raises(InvalidQualityWorkbenchIdError):
        service.evaluate_cases(run_id="../bad")


def test_run_regression_returns_gate_status(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    case_dir = _make_case(case_root)
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    expected["dataset_split"] = "regression"
    expected["expected_diffs"] = [
        {
            "review_status": "APPROVED",
            "title_contains": "date",
            "diff_type": "MODIFY",
            "source_type": "metadata",
            "original_contains": "2026-06-01",
            "compare_contains": "2026-06-30",
        },
        {
            "review_status": "APPROVED",
            "title_contains": "amount",
            "diff_type": "ADD",
            "source_type": "clause",
            "compare_contains": "200",
        },
    ]
    _write_json(case_dir / "expected.json", expected)

    result = service.run_regression(
        dataset_splits={"regression"},
        baseline_name="missing-baseline",
        run_id="regression-001",
    )

    assert result["run_id"] == "regression-001"
    assert result["status"] == "PASSED"
    assert result["report"]["regression"]["run_id"] == "regression-001"
    assert result["report"]["dataset_splits"] == ["regression"]
    assert result["comparison"]["baseline_available"] is False


def test_run_regression_rejects_unsafe_run_id(tmp_path: Path) -> None:
    service = QualityWorkbenchService(
        case_root=tmp_path / "cases",
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )

    with pytest.raises(InvalidQualityWorkbenchIdError):
        service.run_regression(run_id="../bad")


def test_run_regression_rejects_unsafe_baseline_name(tmp_path: Path) -> None:
    service = QualityWorkbenchService(
        case_root=tmp_path / "cases",
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )

    with pytest.raises(InvalidQualityWorkbenchIdError):
        service.run_regression(baseline_name="../bad")


def test_update_expected_diff_writes_allowed_fields(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    _make_case(case_root)

    result = service.update_expected_diff(
        "case-001",
        0,
        {
            "review_status": "DRAFT",
            "severity": "major",
            "notes": "Needs reviewer confirmation",
            "unexpected_field": "must not be written",
        },
    )

    diff = result["expected"]["expected_diffs"][0]
    assert diff["title_contains"] == "date"
    assert diff["review_status"] == "DRAFT"
    assert diff["severity"] == "major"
    assert diff["notes"] == "Needs reviewer confirmation"
    assert "unexpected_field" not in diff
    expected_text = (case_root / "case-001" / "expected.json").read_text(
        encoding="utf-8"
    )
    assert expected_text.endswith("\n")


def test_update_expected_diff_allows_schema_11_review_fields(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    _make_case(case_root)

    result = service.update_expected_diff(
        "case-001",
        2,
        {
            "review_status": "REJECTED",
            "reviewer": "quality@example.com",
            "reviewed_at": "2026-07-01T10:30:00+08:00",
            "false_positive_reason": "Footer text is outside contract body",
            "should_not_match_again": True,
            "false_negative_reason": "Date metadata was missed",
        },
    )

    diff = result["expected"]["expected_diffs"][2]
    assert diff["review_status"] == "REJECTED"
    assert diff["reviewer"] == "quality@example.com"
    assert diff["reviewed_at"] == "2026-07-01T10:30:00+08:00"
    assert diff["false_positive_reason"] == "Footer text is outside contract body"
    assert diff["should_not_match_again"] is True
    assert diff["false_negative_reason"] == "Date metadata was missed"


def test_create_and_delete_expected_diff(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    _make_case(case_root)

    created = service.create_expected_diff(
        "case-001",
        {
            "diff_type": "ADD",
            "source_type": "clause",
            "title_contains": "warranty",
            "review_status": "DRAFT",
            "unexpected_field": "must not be written",
        },
    )

    expected_diffs = created["expected"]["expected_diffs"]
    assert len(expected_diffs) == 4
    assert expected_diffs[-1] == {
        "diff_type": "ADD",
        "source_type": "clause",
        "title_contains": "warranty",
        "review_status": "DRAFT",
    }

    deleted = service.delete_expected_diff("case-001", 3)

    assert len(deleted["expected"]["expected_diffs"]) == 3
    assert all(
        diff.get("title_contains") != "warranty"
        for diff in deleted["expected"]["expected_diffs"]
    )


def test_create_expected_diff_is_idempotent_for_same_negative_actual_diff(
    tmp_path: Path,
) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    _make_case(case_root)

    payload = {
        "review_status": "REJECTED",
        "should_not_match_again": True,
        "false_positive_reason": "manual_false_positive",
        "source_actual_diff_id": "D001",
        "diff_type": "MODIFY",
        "source_type": "metadata",
        "title_contains": "date",
    }

    first = service.create_expected_diff("case-001", payload)
    second = service.create_expected_diff("case-001", payload)

    first_diffs = first["expected"]["expected_diffs"]
    second_diffs = second["expected"]["expected_diffs"]
    negative_matches = [
        diff
        for diff in second_diffs
        if diff.get("source_actual_diff_id") == "D001"
        and diff.get("review_status") == "REJECTED"
        and diff.get("should_not_match_again") is True
    ]

    assert len(first_diffs) == 4
    assert len(second_diffs) == 4
    assert negative_matches == [payload]


def test_create_expected_diff_appends_same_actual_diff_when_not_negative(
    tmp_path: Path,
) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    _make_case(case_root)

    payload = {
        "review_status": "APPROVED",
        "source_actual_diff_id": "D001",
        "diff_type": "MODIFY",
        "source_type": "metadata",
        "title_contains": "date",
    }

    first = service.create_expected_diff("case-001", payload)
    second = service.create_expected_diff("case-001", payload)
    approved_matches = [
        diff
        for diff in second["expected"]["expected_diffs"]
        if diff.get("source_actual_diff_id") == "D001"
        and diff.get("review_status") == "APPROVED"
    ]

    assert len(first["expected"]["expected_diffs"]) == 4
    assert len(second["expected"]["expected_diffs"]) == 5
    assert approved_matches == [payload, payload]


def test_expected_diff_index_out_of_range_raises(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    _make_case(case_root)

    with pytest.raises(QualityExpectedDiffNotFoundError):
        service.update_expected_diff("case-001", 99, {"review_status": "DRAFT"})

    with pytest.raises(QualityExpectedDiffNotFoundError):
        service.delete_expected_diff("case-001", -1)


def test_export_case_rejects_unsafe_ids_and_missing_task(tmp_path: Path) -> None:
    service = QualityWorkbenchService(
        case_root=tmp_path / "cases",
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )

    with pytest.raises(InvalidQualityWorkbenchIdError):
        service.export_case("../task", "case-001")

    with pytest.raises(InvalidQualityWorkbenchIdError):
        service.export_case("..", "case-001")

    with pytest.raises(InvalidQualityWorkbenchIdError):
        service.export_case("task-001", "../case")

    with pytest.raises(InvalidQualityWorkbenchIdError):
        service.export_case("task-001", ".")

    with pytest.raises(QualityTaskNotFoundError):
        service.export_case("missing-task", "case-001")


def test_review_task_replays_quality_filter_and_reports_suppressed_diffs(
    tmp_path: Path,
) -> None:
    task_root = tmp_path / "tasks"
    task_dir = task_root / "task-001"
    _write_json(
        task_dir / "task.json",
        {
            "task_id": "task-001",
            "status": "COMPLETED",
            "original_filename": "original.pdf",
            "compare_filename": "compare.pdf",
            "ocr_quality_summary": {
                "status": "LOW_TEXT_CONFIDENCE",
                "risk_page_count": 1,
                "affected_diff_count": 1,
            },
            "diffs": [
                {
                    "diff_id": "D001",
                    "title": "OCR punctuation",
                    "diff_type": "MODIFY",
                    "source_type": "clause",
                    "original_snippet": "/",
                    "compare_snippet": "∠",
                    "match_score": 99,
                    "review_flags": ["POSSIBLE_OCR_NOISE"],
                    "quality_status": "NEEDS_REVIEW",
                },
                {
                    "diff_id": "D002",
                    "title": "Contract amount",
                    "diff_type": "MODIFY",
                    "source_type": "clause",
                    "original_snippet": "1.5%",
                    "compare_snippet": "15%",
                    "match_score": 99,
                },
            ],
        },
    )
    debug_dir = task_dir / "debug"
    for name in ("diff_quality", "diff_decisions", "ocr_quality"):
        _write_json(debug_dir / f"{name}.json", {"present": True})
    service = QualityWorkbenchService(
        case_root=tmp_path / "cases",
        task_root=task_root,
        output_root=tmp_path / ".ocr-compare-quality",
    )

    result = service.review_task("task-001")

    assert result["task_id"] == "task-001"
    assert result["status"] == "COMPLETED"
    assert result["original_filename"] == "original.pdf"
    assert result["compare_filename"] == "compare.pdf"
    assert result["historical_diff_count"] == 2
    assert result["retained_diff_count"] == 1
    assert result["suppressed_diff_count"] == 1
    assert result["ocr_quality_summary"]["status"] == "LOW_TEXT_CONFIDENCE"
    assert [diff["diff_id"] for diff in result["retained_diffs"]] == ["D002"]
    assert result["suppressed_diffs"][0]["diff_id"] == "D001"
    assert result["suppressed_diffs"][0]["suppression_reason"] == "clause_ocr_noise"
    assert "original_text" not in result["retained_diffs"][0]
    assert "compare_text" not in result["suppressed_diffs"][0]
    assert any(
        decision["action"] == "suppressed_low_value_noise"
        and decision["diff_id"] == "D001"
        for decision in result["quality_decisions"]
    )
    assert result["debug_artifacts"] == {
        "has_diff_quality": True,
        "has_diff_decisions": True,
        "has_ocr_quality": True,
        "has_clause_matches": False,
    }


def test_review_task_rejects_unsafe_task_id(tmp_path: Path) -> None:
    service = QualityWorkbenchService(
        case_root=tmp_path / "cases",
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )

    with pytest.raises(InvalidQualityWorkbenchIdError):
        service.review_task("../task")


def test_review_task_missing_task_raises(tmp_path: Path) -> None:
    service = QualityWorkbenchService(
        case_root=tmp_path / "cases",
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )

    with pytest.raises(QualityTaskNotFoundError):
        service.review_task("missing-task")


def test_review_task_reports_cross_source_merged_suppressed_diff(
    tmp_path: Path,
) -> None:
    task_root = tmp_path / "tasks"
    _write_json(
        task_root / "task-001" / "task.json",
        {
            "task_id": "task-001",
            "status": "COMPLETED",
            "diffs": [
                {
                    "diff_id": "D001",
                    "title": "签订日期",
                    "diff_type": "MODIFY",
                    "source_type": "metadata",
                    "original_text": "签订日期：2024年1月1日",
                    "compare_text": "签订日期：2024年1月2日",
                    "original_snippet": "2024年1月1日",
                    "compare_snippet": "2024年1月2日",
                },
                {
                    "diff_id": "D002",
                    "title": "签订日期",
                    "diff_type": "MODIFY",
                    "source_type": "table",
                    "original_text": "签订日期：2024年1月1日",
                    "compare_text": "签订日期：2024年1月2日",
                    "original_snippet": "2024年1月1日",
                    "compare_snippet": "2024年1月2日",
                },
            ],
        },
    )
    service = QualityWorkbenchService(
        case_root=tmp_path / "cases",
        task_root=task_root,
        output_root=tmp_path / ".ocr-compare-quality",
    )

    result = service.review_task("task-001")

    assert [diff["diff_id"] for diff in result["retained_diffs"]] == ["D001"]
    assert result["suppressed_diffs"] == [
        {
            "diff_id": "D002",
            "diff_type": "MODIFY",
            "source_type": "table",
            "title": "签订日期",
            "quality_status": "NORMAL",
            "review_flags": [],
            "match_score": None,
            "original_snippet": "2024年1月1日",
            "compare_snippet": "2024年1月2日",
            "suppression_reason": "cross_source_merged",
            "quality_decisions": ["cross_source_merged"],
        }
    ]
