import json
import subprocess
import sys
from pathlib import Path

from scripts.evaluate_ocr_compare_quality import (
    _bbox_iou,
    _expected_evidence_hits,
    _match_expected_diffs,
    discover_cases,
    evaluate_case,
    evaluate_case_root,
    load_case_inputs,
    threshold_failures,
    write_html_report,
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


def test_evaluate_case_reports_evidence_drift_for_matched_diff(
    tmp_path: Path,
) -> None:
    case_dir = tmp_path / "wrong_evidence"
    case_dir.mkdir()
    (case_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "wrong_evidence",
                "expected_diffs": [
                    {
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title_contains": "付款",
                        "original_contains": "30日",
                        "compare_contains": "45日",
                        "expected_evidence": [
                            {
                                "side": "original",
                                "page_no": 1,
                                "bbox": {"x0": 100, "y0": 200, "x1": 180, "y1": 230},
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "actual.json").write_text(
        json.dumps(
            {
                "task_id": "EVAL_WRONG_EVIDENCE",
                "status": "COMPLETED",
                "parse_warning_details": [],
                "diffs": [
                    {
                        "diff_id": "D001",
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title": "付款",
                        "original_text": "买方应在验收后30日内付款。",
                        "compare_text": "买方应在验收后45日内付款。",
                        "original_evidence": [
                            {
                                "page_no": 2,
                                "bbox": {
                                    "x0": 300,
                                    "y0": 400,
                                    "x1": 380,
                                    "y1": 430,
                                },
                                "confidence": 0.95,
                                "evidence_quality": "HIGH",
                            }
                        ],
                        "compare_evidence": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_case(discover_cases(tmp_path)[0])

    assert result.true_positive_count == 1
    assert result.evidence_hit_count == 0
    assert result.rates()["evidence_hit_rate"] == 0.0
    assert any("evidence drift" in issue for issue in result.issues)


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


def test_evaluate_case_root_reports_bad_case_as_failure(tmp_path: Path) -> None:
    case_dir = tmp_path / "bad_payload"
    case_dir.mkdir()
    (case_dir / "expected.json").write_text(
        json.dumps({"case_id": "bad_payload", "expected_diffs": []}),
        encoding="utf-8",
    )
    (case_dir / "actual.json").write_text("{bad json", encoding="utf-8")

    report = evaluate_case_root(tmp_path)

    assert report["case_count"] == 1
    assert report["cases"][0]["status"] == "FAILED"
    assert report["aggregate"]["task_failure_count"] == 1
    assert report["threshold_failures"]
    assert any("Expecting property name" in issue for issue in report["cases"][0]["issues"])


def test_threshold_failures_pass_for_smoke_fixture() -> None:
    report = evaluate_case_root(Path("tests/fixtures/ocr_compare_cases"))

    assert threshold_failures(report) == []


def test_cli_writes_json_output(tmp_path: Path) -> None:
    output = tmp_path / "ocr_compare_quality.json"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/evaluate_ocr_compare_quality.py",
            "tests/fixtures/ocr_compare_cases",
            "--output",
            str(output),
            "--fail-on-threshold",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["case_count"] == 1
    assert payload["threshold_failures"] == []
    assert payload["aggregate"]["recall"] == 1.0


def test_cli_writes_html_output(tmp_path: Path) -> None:
    html_output = tmp_path / "html"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/evaluate_ocr_compare_quality.py",
            "tests/fixtures/ocr_compare_cases",
            "--html-output",
            str(html_output),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0
    assert (html_output / "index.html").exists()
    assert (html_output / "simple_scanned.html").exists()


def test_write_html_report_creates_index_and_case_pages(tmp_path: Path) -> None:
    report = evaluate_case_root(Path("tests/fixtures/ocr_compare_cases"))

    write_html_report(tmp_path, report)

    index = tmp_path / "index.html"
    case_page = tmp_path / "simple_scanned.html"
    assert index.exists()
    assert case_page.exists()
    assert "OCR comparison quality report" in index.read_text(encoding="utf-8")
    assert "simple_scanned" in case_page.read_text(encoding="utf-8")
    assert "OCR_LOW_CONFIDENCE" in case_page.read_text(encoding="utf-8")


def test_write_html_report_sanitizes_filename_and_escapes_html(
    tmp_path: Path,
) -> None:
    output = tmp_path / "report"
    report = {
        "case_count": 1,
        "aggregate": {},
        "cases": [
            {
                "case_id": "../case<script>",
                "status": "BAD<script>",
                "recall": 0.5,
                "precision": 0.25,
                "false_positive_count": 1,
                "false_negative_count": 2,
                "low_confidence_count": 0,
                "ocr_warning_count": 3,
                "issues": ["<bad>"],
            }
        ],
    }

    write_html_report(output, report)

    assert not (tmp_path / "case<script>.html").exists()
    case_page = output / "_case_script_.html"
    assert case_page.exists()
    index_html = (output / "index.html").read_text(encoding="utf-8")
    case_html = case_page.read_text(encoding="utf-8")
    assert "href='_case_script_.html'" in index_html
    assert "&lt;script&gt;" in index_html
    assert "BAD&lt;script&gt;" in index_html
    assert "../case<script>" not in index_html
    assert "BAD<script>" not in index_html
    assert "&lt;bad&gt;" in case_html
    assert "<bad>" not in case_html
