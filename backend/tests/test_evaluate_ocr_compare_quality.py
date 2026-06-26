import json
import subprocess
import sys
from pathlib import Path

from app.models import PageOcrQualityProfile, TaskOcrQualitySummary
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


def test_evaluate_case_includes_model_route_records(tmp_path: Path) -> None:
    case_dir = tmp_path / "route_case"
    case_dir.mkdir()
    (case_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "route_case",
                "expected_diffs": [
                    {
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title_contains": "付款",
                        "original_contains": "100元",
                        "compare_contains": "120元",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "actual.json").write_text(
        json.dumps(
            {
                "task_id": "EVAL_ROUTE_CASE",
                "status": "COMPLETED",
                "parse_warning_details": [],
                "ocr_quality_summary": TaskOcrQualitySummary(
                    status="LOW_TEXT_CONFIDENCE",
                    requires_review=True,
                    profiles=[
                        PageOcrQualityProfile(
                            side="original",
                            page_no=1,
                            status="LOW_TEXT_CONFIDENCE",
                            reasons=["LOW_AVG_CONFIDENCE"],
                            affected_diff_ids=["D001"],
                        )
                    ],
                ).model_dump(mode="json"),
                "diffs": [
                    {
                        "diff_id": "D001",
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title": "付款",
                        "original_text": "付款金额为100元",
                        "compare_text": "付款金额为120元",
                        "review_flags": ["OCR_LOW_CONFIDENCE"],
                        "quality_status": "NEEDS_REVIEW",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_case(discover_cases(tmp_path)[0]).to_dict()

    assert result["model_routing"]["route_count"] == 1
    assert (
        result["model_routing"]["routes"][0]["recommended_route"]
        == "HIGH_DPI_PAGE_RETRY"
    )
    assert result["route_metrics"]["route_count_by_recommendation"] == {
        "HIGH_DPI_PAGE_RETRY": 1
    }
    assert result["route_metrics"]["page_count_by_type"] == {"scan_low_quality": 1}
    assert result["route_metrics"]["precision_by_recommendation"] == {}


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


def test_evaluate_case_root_aggregates_route_metrics(tmp_path: Path) -> None:
    first_case = tmp_path / "first_route_case"
    first_case.mkdir()
    (first_case / "expected.json").write_text(
        json.dumps({"case_id": "first_route_case", "expected_diffs": []}),
        encoding="utf-8",
    )
    (first_case / "actual.json").write_text(
        json.dumps(
            {
                "task_id": "EVAL_FIRST_ROUTE_CASE",
                "status": "COMPLETED",
                "parse_warning_details": [],
                "ocr_quality_summary": TaskOcrQualitySummary(
                    status="LOW_TEXT_CONFIDENCE",
                    requires_review=True,
                    profiles=[
                        PageOcrQualityProfile(
                            side="original",
                            page_no=1,
                            status="LOW_TEXT_CONFIDENCE",
                            reasons=["LOW_AVG_CONFIDENCE"],
                            affected_diff_ids=["D001"],
                        ),
                        PageOcrQualityProfile(
                            side="compare",
                            page_no=2,
                            status="TABLE_RISK",
                            reasons=["TABLE_CELL_UNMATCHED"],
                            affected_diff_ids=["D002"],
                        ),
                    ],
                ).model_dump(mode="json"),
                "diffs": [
                    {
                        "diff_id": "D001",
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title": "付款",
                        "original_text": "付款金额为100元",
                        "compare_text": "付款金额为120元",
                    },
                    {
                        "diff_id": "D002",
                        "diff_type": "MODIFY",
                        "source_type": "table",
                        "title": "报价表",
                        "original_text": "单价100元",
                        "compare_text": "单价120元",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    second_case = tmp_path / "second_route_case"
    second_case.mkdir()
    (second_case / "expected.json").write_text(
        json.dumps({"case_id": "second_route_case", "expected_diffs": []}),
        encoding="utf-8",
    )
    (second_case / "actual.json").write_text(
        json.dumps(
            {
                "task_id": "EVAL_SECOND_ROUTE_CASE",
                "status": "COMPLETED",
                "parse_warning_details": [],
                "ocr_quality_summary": TaskOcrQualitySummary(
                    status="SEAL_OR_SIGNATURE_RISK",
                    requires_review=True,
                    profiles=[
                        PageOcrQualityProfile(
                            side="original",
                            page_no=1,
                            status="SEAL_OR_SIGNATURE_RISK",
                            reasons=["SEAL_DETECTED"],
                            affected_diff_ids=["D003"],
                        )
                    ],
                ).model_dump(mode="json"),
                "diffs": [
                    {
                        "diff_id": "D003",
                        "diff_type": "MODIFY",
                        "source_type": "seal",
                        "title": "签章",
                        "original_text": "已盖章",
                        "compare_text": "未盖章",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = evaluate_case_root(tmp_path)
    route_metrics = report["aggregate"]["route_metrics"]

    assert route_metrics["route_count_by_recommendation"] == {
        "HIGH_DPI_PAGE_RETRY": 1,
        "TABLE_REGION_RETRY": 1,
        "MANUAL_REVIEW": 1,
    }
    assert route_metrics["page_count_by_type"] == {
        "scan_low_quality": 1,
        "table_heavy": 1,
        "seal_signature": 1,
    }
    assert route_metrics["retry_recommended_count"] == 2
    assert route_metrics["manual_review_recommended_count"] == 1
    assert route_metrics["precision_by_recommendation"] == {}
    assert route_metrics["recall_by_recommendation"] == {}
    assert route_metrics["evidence_hit_rate_by_recommendation"] == {}
    assert route_metrics["low_confidence_ratio_by_recommendation"] == {}
    assert "route_metrics" in report["cases"][0]


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
    index_html = index.read_text(encoding="utf-8")
    case_html = case_page.read_text(encoding="utf-8")
    assert "OCR comparison quality report" in index_html
    assert "Route recommendations" in index_html
    assert "simple_scanned" in case_html
    assert "OCR_LOW_CONFIDENCE" in case_html
    assert "Model routing" in case_html


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


def test_write_html_report_renders_gold_detail_sections(tmp_path: Path) -> None:
    report = {
        "case_count": 1,
        "threshold_failures": [],
        "aggregate": {
            "annotation_summary": {
                "approved_expected_count": 1,
                "draft_expected_count": 1,
                "rejected_expected_count": 0,
            },
            "route_metrics": {
                "route_count_by_recommendation": {"MANUAL_REVIEW": 1},
                "page_count_by_type": {"mixed": 1},
                "retry_recommended_count": 0,
                "manual_review_recommended_count": 1,
            },
        },
        "cases": [
            {
                "case_id": "gold_case",
                "status": "COMPLETED",
                "expected_count": 1,
                "actual_count": 2,
                "recall": 0.5,
                "precision": 0.5,
                "false_positive_count": 1,
                "false_negative_count": 1,
                "low_confidence_count": 1,
                "ocr_warning_count": 1,
                "issues": ["missed expected diff 1: Payment"],
                "annotation_summary": {
                    "approved_expected_count": 1,
                    "draft_expected_count": 1,
                    "rejected_expected_count": 0,
                },
                "route_metrics": {
                    "retry_recommended_count": 0,
                    "manual_review_recommended_count": 1,
                },
                "model_routing": {
                    "routes": [
                        {
                            "side": "compare",
                            "page_no": 1,
                            "page_type": "mixed",
                            "recommended_route": "MANUAL_REVIEW",
                        }
                    ]
                },
                "matches": [
                    {
                        "expected_index": 0,
                        "actual_index": 0,
                        "actual_diff_id": "D001",
                        "score": 0.95,
                        "evidence_hit": True,
                    }
                ],
                "missed_expected_diffs": [
                    {
                        "expected_index": 1,
                        "label": "Payment<script>",
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                    }
                ],
                "unexpected_actual_diffs": [
                    {
                        "actual_index": 1,
                        "diff_id": "D999",
                        "title": "Unexpected <b>cover</b>",
                        "source_type": "metadata",
                        "quality_status": "NEEDS_REVIEW",
                        "review_flags": ["POSSIBLE_COVER_OCR_FRAGMENT"],
                    }
                ],
                "evidence_drift_diffs": [
                    {
                        "expected_index": 0,
                        "actual_diff_id": "D001",
                        "label": "Payment",
                    }
                ],
            }
        ],
    }

    write_html_report(tmp_path, report)

    index_html = (tmp_path / "index.html").read_text(encoding="utf-8")
    case_html = (tmp_path / "gold_case.html").read_text(encoding="utf-8")
    assert "Annotation summary" in index_html
    assert "Expected" in index_html
    assert "Actual" in index_html
    assert "Evidence drift" in index_html
    assert (
        "<td><a href='gold_case.html'>gold_case</a></td><td>COMPLETED</td>"
        "<td>1</td><td>2</td><td>50.00%</td><td>50.00%</td>"
        "<td>1</td><td>1</td><td>1</td>"
    ) in index_html
    assert "Matched diffs" in case_html
    assert "Missed expected diffs" in case_html
    assert "Unexpected actual diffs" in case_html
    assert "Evidence drift" in case_html
    assert (
        "<td>0</td><td>0</td><td>D001</td><td>0.95</td><td>True</td>"
    ) in case_html
    assert "<td>0</td><td>D001</td><td>Payment</td>" in case_html
    assert "Payment&lt;script&gt;" in case_html
    assert "Unexpected &lt;b&gt;cover&lt;/b&gt;" in case_html


def test_evaluate_case_counts_only_approved_gold_diffs(tmp_path: Path) -> None:
    case_dir = tmp_path / "annotation_case"
    case_dir.mkdir()
    (case_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "annotation_case",
                "expected_diffs": [
                    {
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title_contains": "Payment",
                        "original_contains": "30 days",
                        "compare_contains": "45 days",
                        "review_status": "APPROVED",
                    },
                    {
                        "diff_type": "ADD",
                        "source_type": "metadata",
                        "title_contains": "Generated draft",
                        "review_status": "DRAFT",
                    },
                    {
                        "diff_type": "DELETE",
                        "source_type": "clause",
                        "title_contains": "Rejected",
                        "review_status": "REJECTED",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "actual.json").write_text(
        json.dumps(
            {
                "task_id": "EVAL_ANNOTATION_CASE",
                "status": "COMPLETED",
                "parse_warning_details": [],
                "diffs": [
                    {
                        "diff_id": "D001",
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title": "Payment term",
                        "original_text": "Payment is due in 30 days.",
                        "compare_text": "Payment is due in 45 days.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_case(discover_cases(tmp_path)[0]).to_dict()

    assert result["expected_count"] == 1
    assert result["true_positive_count"] == 1
    assert result["false_negative_count"] == 0
    assert result["annotation_summary"] == {
        "approved_expected_count": 1,
        "draft_expected_count": 1,
        "rejected_expected_count": 1,
    }


def test_evaluate_case_root_aggregates_annotation_summary(tmp_path: Path) -> None:
    for case_id, approved, draft, rejected in [
        ("case_one", 1, 2, 0),
        ("case_two", 2, 0, 1),
    ]:
        case_dir = tmp_path / case_id
        case_dir.mkdir()
        expected_diffs = []
        expected_diffs.extend(
            {
                "diff_type": "MODIFY",
                "source_type": "clause",
                "title_contains": f"approved-{index}",
                "review_status": "APPROVED",
            }
            for index in range(approved)
        )
        expected_diffs.extend(
            {
                "diff_type": "MODIFY",
                "source_type": "clause",
                "title_contains": f"draft-{index}",
                "review_status": "DRAFT",
            }
            for index in range(draft)
        )
        expected_diffs.extend(
            {
                "diff_type": "MODIFY",
                "source_type": "clause",
                "title_contains": f"rejected-{index}",
                "review_status": "REJECTED",
            }
            for index in range(rejected)
        )
        (case_dir / "expected.json").write_text(
            json.dumps({"case_id": case_id, "expected_diffs": expected_diffs}),
            encoding="utf-8",
        )
        (case_dir / "actual.json").write_text(
            json.dumps(
                {
                    "task_id": f"EVAL_{case_id.upper()}",
                    "status": "COMPLETED",
                    "parse_warning_details": [],
                    "diffs": [],
                }
            ),
            encoding="utf-8",
        )

    report = evaluate_case_root(tmp_path)

    assert report["aggregate"]["annotation_summary"] == {
        "approved_expected_count": 3,
        "draft_expected_count": 2,
        "rejected_expected_count": 1,
    }


def test_evaluate_case_emits_structured_match_details(tmp_path: Path) -> None:
    case_dir = tmp_path / "details_case"
    case_dir.mkdir()
    (case_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "details_case",
                "expected_diffs": [
                    {
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title_contains": "Payment",
                        "original_contains": "30 days",
                        "compare_contains": "45 days",
                        "expected_evidence": [
                            {
                                "side": "original",
                                "page_no": 1,
                                "bbox": {"x0": 10, "y0": 10, "x1": 60, "y1": 30},
                            }
                        ],
                    },
                    {
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title_contains": "Delivery",
                        "original_contains": "May",
                        "compare_contains": "June",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "actual.json").write_text(
        json.dumps(
            {
                "task_id": "EVAL_DETAILS_CASE",
                "status": "COMPLETED",
                "parse_warning_details": [],
                "diffs": [
                    {
                        "diff_id": "D001",
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title": "Payment term",
                        "original_text": "Payment is due in 30 days.",
                        "compare_text": "Payment is due in 45 days.",
                        "original_evidence": [
                            {
                                "page_no": 2,
                                "bbox": {"x0": 10, "y0": 10, "x1": 60, "y1": 30},
                            }
                        ],
                    },
                    {
                        "diff_id": "D999",
                        "diff_type": "ADD",
                        "source_type": "metadata",
                        "title": "Unexpected cover text",
                        "quality_status": "NEEDS_REVIEW",
                        "review_flags": ["POSSIBLE_COVER_OCR_FRAGMENT"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_case(discover_cases(tmp_path)[0]).to_dict()

    assert result["matches"] == [
        {
            "expected_index": 0,
            "actual_index": 0,
            "actual_diff_id": "D001",
            "score": 0.95,
            "evidence_hit": False,
        }
    ]
    assert result["missed_expected_diffs"][0]["label"] == "Delivery"
    assert result["unexpected_actual_diffs"] == [
        {
            "actual_index": 1,
            "diff_id": "D999",
            "title": "Unexpected cover text",
            "source_type": "metadata",
            "quality_status": "NEEDS_REVIEW",
            "review_flags": ["POSSIBLE_COVER_OCR_FRAGMENT"],
        }
    ]
    assert result["evidence_drift_diffs"] == [
        {"expected_index": 0, "actual_diff_id": "D001", "label": "Payment"}
    ]


def test_evaluate_case_structured_details_preserve_expected_source_indexes(
    tmp_path: Path,
) -> None:
    case_dir = tmp_path / "source_index_case"
    case_dir.mkdir()
    (case_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "source_index_case",
                "expected_diffs": [
                    {
                        "diff_type": "ADD",
                        "source_type": "metadata",
                        "title_contains": "Draft row",
                        "review_status": "DRAFT",
                    },
                    {
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title_contains": "Payment",
                        "original_contains": "30 days",
                        "compare_contains": "45 days",
                        "review_status": "APPROVED",
                        "expected_evidence": [
                            {
                                "side": "original",
                                "page_no": 1,
                                "bbox": {"x0": 10, "y0": 10, "x1": 60, "y1": 30},
                            }
                        ],
                    },
                    {
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title_contains": "Delivery",
                        "original_contains": "May",
                        "compare_contains": "June",
                        "review_status": "APPROVED",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "actual.json").write_text(
        json.dumps(
            {
                "task_id": "EVAL_SOURCE_INDEX_CASE",
                "status": "COMPLETED",
                "parse_warning_details": [],
                "diffs": [
                    {
                        "diff_id": "D001",
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title": "Payment term",
                        "original_text": "Payment is due in 30 days.",
                        "compare_text": "Payment is due in 45 days.",
                        "original_evidence": [
                            {
                                "page_no": 2,
                                "bbox": {"x0": 10, "y0": 10, "x1": 60, "y1": 30},
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_case(discover_cases(tmp_path)[0]).to_dict()

    assert result["expected_count"] == 2
    assert result["true_positive_count"] == 1
    assert result["false_negative_count"] == 1
    assert result["matches"][0]["expected_index"] == 1
    assert result["missed_expected_diffs"][0]["expected_index"] == 2
    assert result["evidence_drift_diffs"][0]["expected_index"] == 1
    assert any("expected diff 3" in issue for issue in result["issues"])
