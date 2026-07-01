import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.export_ocr_compare_gold_case import _severity, export_gold_case


def _write_task(task_dir: Path) -> None:
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "task_id": "task-gold-001",
                "status": "COMPLETED",
                "original_filename": "original.pdf",
                "compare_filename": "compare.pdf",
                "created_at": "2026-06-26T08:00:00+00:00",
                "updated_at": "2026-06-26T08:01:00+00:00",
                "ocr_quality_summary": {
                    "status": "UNRELIABLE",
                    "requires_review": True,
                    "risk_page_count": 2,
                    "affected_diff_count": 2,
                },
                "diffs": [
                    {
                        "diff_id": "D001",
                        "diff_type": "MODIFY",
                        "source_type": "metadata",
                        "title": "Contract date",
                        "original_text": "Signed on 2024-04-01.",
                        "compare_text": "Signed on 2024-04-21.",
                        "quality_status": "NEEDS_REVIEW",
                        "review_flags": [
                            "CRITICAL_VALUE_CHANGE",
                            "OCR_LOW_CONFIDENCE",
                        ],
                    },
                    {
                        "diff_id": "D002",
                        "diff_type": "ADD",
                        "source_type": "seal",
                        "title": "Seal region",
                        "compare_text": "Company seal",
                        "quality_status": "NEEDS_REVIEW",
                        "review_flags": ["SEAL_REVIEW"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_export_gold_case_creates_review_draft_files(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    output_dir = tmp_path / "case_gold_001"
    _write_task(task_dir)

    summary = export_gold_case(task_dir, output_dir)

    assert summary == {
        "case_id": "case_gold_001",
        "task_id": "task-gold-001",
        "expected_diff_count": 2,
        "actual_diff_count": 2,
    }
    actual = json.loads((output_dir / "actual.json").read_text(encoding="utf-8"))
    expected = json.loads((output_dir / "expected.json").read_text(encoding="utf-8"))
    readme = (output_dir / "README.md").read_text(encoding="utf-8")

    assert actual["task_id"] == "task-gold-001"
    assert expected["case_id"] == "case_gold_001"
    assert expected["tags"] == ["exported", "requires_human_review"]
    assert expected["expected_diffs"][0]["source_actual_diff_id"] == "D001"
    assert expected["expected_diffs"][0]["review_status"] == "DRAFT"
    assert expected["expected_diffs"][0]["severity"] == "critical"
    assert expected["expected_diffs"][0]["title_contains"] == "Contract date"
    assert expected["expected_diffs"][0]["original_contains"] == (
        "Signed on 2024-04-01."
    )
    assert expected["expected_diffs"][0]["compare_contains"] == (
        "Signed on 2024-04-21."
    )
    assert expected["expected_diffs"][1]["severity"] == "critical"
    assert expected["quality_expectations"]["max_task_failures"] == 0
    assert "Generated draft, not reviewed gold" in readme
    assert "actual.json" in readme
    assert "expected.json" in readme
    assert "sensitive contract or business data" in readme
    assert "task-gold-001" in readme


def test_export_gold_case_writes_schema_11_metadata(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    output_dir = tmp_path / "case_gold_001"
    _write_task(task_dir)

    export_gold_case(task_dir, output_dir)

    expected = json.loads((output_dir / "expected.json").read_text(encoding="utf-8"))
    first_diff = expected["expected_diffs"][0]
    assert expected["schema_version"] == "1.1"
    assert expected["dataset_split"] == "dev"
    assert expected["case_tags"] == ["exported", "requires_human_review"]
    assert expected["baseline_required"] is False
    assert (
        expected["quality_expectations"][
            "max_known_false_positive_regression_count"
        ]
        == 0
    )
    assert first_diff["review_status"] == "DRAFT"
    assert first_diff["reviewer"] == ""
    assert first_diff["reviewed_at"] == ""
    assert first_diff["false_positive_reason"] == ""
    assert first_diff["false_negative_reason"] == ""
    assert first_diff["should_not_match_again"] is False


def test_export_gold_case_refuses_to_overwrite_reviewed_expected(
    tmp_path: Path,
) -> None:
    task_dir = tmp_path / "task"
    output_dir = tmp_path / "case_gold_001"
    _write_task(task_dir)
    output_dir.mkdir()
    (output_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "case_gold_001",
                "expected_diffs": [{"review_status": "APPROVED"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(FileExistsError, match="reviewed expected.json"):
        export_gold_case(task_dir, output_dir)


def test_export_gold_case_refuses_to_overwrite_rejected_expected(
    tmp_path: Path,
) -> None:
    task_dir = tmp_path / "task"
    output_dir = tmp_path / "case_gold_001"
    _write_task(task_dir)
    output_dir.mkdir()
    (output_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "case_gold_001",
                "expected_diffs": [{"review_status": "REJECTED"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(FileExistsError, match="reviewed expected.json"):
        export_gold_case(task_dir, output_dir)


def test_export_gold_case_refuses_to_overwrite_legacy_reviewed_expected(
    tmp_path: Path,
) -> None:
    task_dir = tmp_path / "task"
    output_dir = tmp_path / "case_gold_001"
    _write_task(task_dir)
    output_dir.mkdir()
    (output_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "case_gold_001",
                "expected_diffs": [{"title_contains": "Legacy reviewed diff"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(FileExistsError, match="reviewed expected.json"):
        export_gold_case(task_dir, output_dir)


def test_export_gold_case_force_overwrites_reviewed_expected(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    output_dir = tmp_path / "case_gold_001"
    _write_task(task_dir)
    output_dir.mkdir()
    (output_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "case_gold_001",
                "expected_diffs": [{"review_status": "APPROVED"}],
            }
        ),
        encoding="utf-8",
    )

    export_gold_case(task_dir, output_dir, force=True)

    expected = json.loads((output_dir / "expected.json").read_text(encoding="utf-8"))
    assert {item["review_status"] for item in expected["expected_diffs"]} == {"DRAFT"}


def test_export_gold_case_cli_writes_summary(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    output_dir = tmp_path / "case_gold_001"
    _write_task(task_dir)

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/export_ocr_compare_gold_case.py",
            str(task_dir),
            str(output_dir),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "case_gold_001" in completed.stdout
    assert (output_dir / "actual.json").exists()
    assert (output_dir / "expected.json").exists()


def test_severity_classifies_chinese_metadata_key_fields_as_critical() -> None:
    for title in ("封面字段：签订日期", "合同编号", "合同金额"):
        assert (
            _severity(
                {
                    "source_type": "metadata",
                    "title": title,
                    "quality_status": "NORMAL",
                    "review_flags": [],
                }
            )
            == "critical"
        )
