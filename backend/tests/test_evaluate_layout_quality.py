from pathlib import Path

from scripts.evaluate_layout_quality import evaluate_case_root, write_html_report


def test_evaluate_layout_quality_fixture() -> None:
    report = evaluate_case_root(Path("tests/fixtures/layout_cases"))

    assert report["case_count"] == 1
    assert report["aggregate"]["region_precision"] == 1.0
    assert report["aggregate"]["region_recall"] == 1.0
    assert report["aggregate"]["bbox_hit_rate"] == 1.0
    assert report["aggregate"]["reading_order_accuracy"] == 1.0
    assert report["aggregate"]["reading_order_pair_accuracy"] == 1.0
    assert report["aggregate"]["label_macro_f1"] == 1.0
    assert report["aggregate"]["invalid_bbox_count"] == 0
    assert report["threshold_failures"] == []


def test_evaluate_layout_quality_writes_html_report(tmp_path: Path) -> None:
    report = evaluate_case_root(Path("tests/fixtures/layout_cases"))

    write_html_report(tmp_path, report)

    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "basic.html").exists()


def test_approved_real_layout_cases_pass_thresholds() -> None:
    case_root = Path("tests/fixtures/layout_real_cases")

    report = evaluate_case_root(case_root)

    assert report["case_count"] == 8
    assert report["threshold_failures"] == []
    assert all(case["issues"] == [] for case in report["cases"])
