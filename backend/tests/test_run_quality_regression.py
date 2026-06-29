import json
from pathlib import Path

from scripts.run_quality_regression import (
    DEFAULT_REGRESSION_THRESHOLDS,
    compare_reports,
    load_thresholds,
)


def _quality_report(
    *,
    precision: float = 1.0,
    recall: float = 1.0,
    evidence_hit_rate: float = 1.0,
    false_positive_count: int = 0,
    false_negative_count: int = 0,
    task_failure_count: int = 0,
    cases: list[dict] | None = None,
) -> dict:
    return {
        "case_count": len(cases or []),
        "aggregate": {
            "precision": precision,
            "recall": recall,
            "evidence_hit_rate": evidence_hit_rate,
            "false_positive_count": false_positive_count,
            "false_negative_count": false_negative_count,
            "task_failure_count": task_failure_count,
        },
        "cases": cases or [],
    }


def _case_report(
    case_id: str,
    *,
    precision: float = 1.0,
    recall: float = 1.0,
    evidence_hit_rate: float = 1.0,
    false_positive_count: int = 0,
    false_negative_count: int = 0,
) -> dict:
    return {
        "case_id": case_id,
        "precision": precision,
        "recall": recall,
        "evidence_hit_rate": evidence_hit_rate,
        "false_positive_count": false_positive_count,
        "false_negative_count": false_negative_count,
    }


def test_load_thresholds_uses_defaults_when_path_is_none() -> None:
    thresholds = load_thresholds(None)

    assert thresholds == DEFAULT_REGRESSION_THRESHOLDS


def test_load_thresholds_merges_json_file_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps({"min_recall": 0.92}), encoding="utf-8")

    thresholds = load_thresholds(path)

    assert thresholds["min_recall"] == 0.92
    assert thresholds["min_precision"] == DEFAULT_REGRESSION_THRESHOLDS["min_precision"]


def test_compare_reports_returns_aggregate_and_case_deltas() -> None:
    baseline = _quality_report(
        precision=0.95,
        recall=1.0,
        evidence_hit_rate=0.9,
        false_positive_count=1,
        false_negative_count=0,
        cases=[_case_report("case_a", precision=0.95, recall=1.0)],
    )
    current = _quality_report(
        precision=0.9,
        recall=0.8,
        evidence_hit_rate=0.85,
        false_positive_count=3,
        false_negative_count=2,
        cases=[_case_report("case_a", precision=0.8, recall=0.75)],
    )

    comparison = compare_reports(current, baseline)

    assert comparison["baseline_available"] is True
    assert comparison["aggregate_delta"]["precision"] == -0.05
    assert comparison["aggregate_delta"]["recall"] == -0.2
    assert comparison["aggregate_delta"]["evidence_hit_rate"] == -0.05
    assert comparison["aggregate_delta"]["false_positive_count"] == 2
    assert comparison["aggregate_delta"]["false_negative_count"] == 2
    assert comparison["case_deltas"][0]["case_id"] == "case_a"
    assert comparison["case_deltas"][0]["precision_delta"] == -0.15
    assert comparison["case_deltas"][0]["recall_delta"] == -0.25


def test_compare_reports_returns_empty_baseline_comparison_without_baseline() -> None:
    comparison = compare_reports(_quality_report(), None)

    assert comparison["baseline_available"] is False
    assert comparison["aggregate_delta"] == {}
    assert comparison["case_deltas"] == []
    assert comparison["failed_gates"] == []
