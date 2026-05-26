from pathlib import Path

from scripts.evaluate_compare_quality import evaluate_case_root


def test_evaluate_compare_quality_fixture() -> None:
    report = evaluate_case_root(Path("tests/fixtures/compare_cases"))

    assert report["case_count"] == 1
    assert report["aggregate"]["true_positive_count"] == 1
    assert report["aggregate"]["false_positive_count"] == 0
    assert report["aggregate"]["false_negative_count"] == 0
    assert report["aggregate"]["precision"] == 1.0
    assert report["aggregate"]["recall"] == 1.0
    assert report["aggregate"]["evidence_hit_rate"] == 1.0
