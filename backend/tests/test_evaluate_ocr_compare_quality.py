import json
from pathlib import Path

from scripts.evaluate_ocr_compare_quality import (
    _bbox_iou,
    _expected_evidence_hits,
    _match_expected_diffs,
    discover_cases,
    evaluate_case,
    evaluate_case_root,
    load_case_inputs,
)


def test_discover_cases_ignores_incomplete_directories() -> None:
    cases = discover_cases(Path("tests/fixtures/ocr_compare_cases"))

    assert [case.case_id for case in cases] == ["simple_scanned"]
    assert cases[0].case_dir == Path("tests/fixtures/ocr_compare_cases/simple_scanned")


def test_load_case_inputs_reads_expected_and_actual_task() -> None:
    case = discover_cases(Path("tests/fixtures/ocr_compare_cases"))[0]

    expected, task = load_case_inputs(case)

    assert expected["case_id"] == "simple_scanned"
    assert expected["expected_diffs"][0]["diff_type"] == "MODIFY"
    assert task.task_id == "EVAL_OCR_SIMPLE_SCANNED"
    assert task.status == "COMPLETED"
    assert task.diffs[0].review_flags == ["OCR_LOW_CONFIDENCE"]


def test_evaluate_case_reports_ocr_compare_metrics() -> None:
    case = discover_cases(Path("tests/fixtures/ocr_compare_cases"))[0]

    result = evaluate_case(case)

    assert result.case_id == "simple_scanned"
    assert result.status == "COMPLETED"
    assert result.expected_count == 1
    assert result.actual_count == 1
    assert result.true_positive_count == 1
    assert result.false_positive_count == 0
    assert result.false_negative_count == 0
    assert result.evidence_hit_count == 1
    assert result.low_confidence_count == 1
    assert result.ocr_warning_count == 1
    assert result.task_failure_count == 0
    assert result.rates()["recall"] == 1.0
    assert result.rates()["precision"] == 1.0
    assert result.rates()["evidence_hit_rate"] == 1.0
    assert result.rates()["low_confidence_ratio"] == 1.0


def test_evaluate_case_root_aggregates_metrics() -> None:
    report = evaluate_case_root(Path("tests/fixtures/ocr_compare_cases"))

    assert report["case_count"] == 1
    assert report["aggregate"]["status"] == "COMPLETED"
    assert report["aggregate"]["expected_count"] == 1
    assert report["aggregate"]["actual_count"] == 1
    assert report["aggregate"]["true_positive_count"] == 1
    assert report["aggregate"]["false_positive_count"] == 0
    assert report["aggregate"]["false_negative_count"] == 0
    assert report["aggregate"]["evidence_hit_count"] == 1
    assert report["aggregate"]["low_confidence_count"] == 1
    assert report["aggregate"]["ocr_warning_count"] == 1
    assert report["aggregate"]["task_failure_count"] == 0
    assert report["aggregate"]["recall"] == 1.0
    assert report["aggregate"]["precision"] == 1.0
    assert report["aggregate"]["evidence_hit_rate"] == 1.0
    assert report["aggregate"]["low_confidence_ratio"] == 1.0


def test_matching_requires_semantic_or_evidence_signal() -> None:
    expected = [{"diff_type": "MODIFY", "source_type": "clause"}]
    actual = [
        {
            "diff_type": "MODIFY",
            "source_type": "clause",
            "title": "unrelated",
            "original_text": "alpha",
            "compare_text": "beta",
        }
    ]

    assert _match_expected_diffs(expected, actual) == []


def test_malformed_bboxes_do_not_match_evidence() -> None:
    expected = {
        "expected_evidence": [
            {
                "side": "original",
                "page_no": 1,
                "bbox": {"x0": 0, "y0": 0, "x1": 10, "y1": 10},
            }
        ]
    }
    actual = {
        "original_evidence": [
            {
                "page_no": 1,
                "bbox": {"x0": "bad", "y0": 0, "x1": 10, "y1": 10},
            }
        ],
        "compare_evidence": [],
    }

    assert _bbox_iou({"x0": "bad", "y0": 0, "x1": 10, "y1": 10}, expected["expected_evidence"][0]["bbox"]) == 0.0
    assert _bbox_iou({"x0": 10, "y0": 0, "x1": 0, "y1": 10}, expected["expected_evidence"][0]["bbox"]) == 0.0
    assert not _expected_evidence_hits(expected, actual)


def test_evaluate_case_counts_ocr_warning_source_case_insensitively(
    tmp_path: Path,
) -> None:
    case_dir = tmp_path / "source_warning"
    case_dir.mkdir()
    (case_dir / "expected.json").write_text(
        json.dumps({"case_id": "source_warning", "expected_diffs": []}),
        encoding="utf-8",
    )
    (case_dir / "actual.json").write_text(
        json.dumps(
            {
                "task_id": "EVAL_SOURCE_WARNING",
                "status": "COMPLETED",
                "parse_warning_details": [
                    {
                        "code": "LOW_TEXT_CONFIDENCE",
                        "message": "low text confidence",
                        "severity": "WARNING",
                        "page_no": 1,
                        "source": "OCR",
                    }
                ],
                "diffs": [],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_case(discover_cases(tmp_path)[0])

    assert result.ocr_warning_count == 1


def test_evaluate_case_root_empty_directory_reports_no_cases(tmp_path: Path) -> None:
    report = evaluate_case_root(tmp_path)

    assert report["case_count"] == 0
    assert report["aggregate"]["status"] == "NO_CASES"
