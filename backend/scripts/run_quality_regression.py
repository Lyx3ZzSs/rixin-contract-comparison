from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_REGRESSION_THRESHOLDS: dict[str, float | int] = {
    "min_precision": 0.9,
    "min_recall": 0.95,
    "min_evidence_hit_rate": 0.9,
    "max_false_positive_count": 0,
    "max_false_negative_count": 0,
    "max_task_failure_count": 0,
    "max_precision_drop": 0.02,
    "max_recall_drop": 0.02,
    "max_evidence_hit_rate_drop": 0.02,
    "max_false_positive_increase": 0,
    "max_false_negative_increase": 0,
    "max_task_failure_increase": 0,
}

RATE_METRICS = ("precision", "recall", "evidence_hit_rate")
COUNT_METRICS = (
    "false_positive_count",
    "false_negative_count",
    "task_failure_count",
)


def load_thresholds(path: Path | None) -> dict[str, float | int]:
    if path is None:
        return dict(DEFAULT_REGRESSION_THRESHOLDS)
    overrides = _read_json(path)
    if not isinstance(overrides, dict):
        raise ValueError("Regression thresholds file must contain a JSON object.")
    for key, value in overrides.items():
        if key not in DEFAULT_REGRESSION_THRESHOLDS:
            raise ValueError(f"Unknown regression threshold: {key}")
        if not _is_number(value):
            raise ValueError(f"Regression threshold {key} must be numeric.")
    return DEFAULT_REGRESSION_THRESHOLDS | overrides


def compare_reports(
    current_report: dict[str, Any],
    baseline_report: dict[str, Any] | None,
) -> dict[str, Any]:
    if baseline_report is None:
        return {
            "baseline_available": False,
            "aggregate_delta": {},
            "case_deltas": [],
            "failed_gates": [],
        }

    return {
        "baseline_available": True,
        "aggregate_delta": _metric_deltas(
            current_report.get("aggregate", {}),
            baseline_report.get("aggregate", {}),
            (*RATE_METRICS, *COUNT_METRICS),
        ),
        "case_deltas": _case_deltas(
            current_report.get("cases", []),
            baseline_report.get("cases", []),
        ),
        "failed_gates": [],
    }


def _case_deltas(
    current_cases: list[dict[str, Any]],
    baseline_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    baseline_by_id = {
        case["case_id"]: case for case in baseline_cases if case.get("case_id")
    }
    deltas = []
    for current_case in current_cases:
        case_id = current_case.get("case_id")
        baseline_case = baseline_by_id.get(case_id)
        if baseline_case is None:
            continue
        deltas.append(
            {"case_id": case_id}
            | _case_metric_deltas(current_case, baseline_case)
        )
    return sorted(deltas, key=_case_delta_sort_key)


def _case_metric_deltas(
    current_case: dict[str, Any],
    baseline_case: dict[str, Any],
) -> dict[str, float | int | None]:
    deltas = _metric_deltas(
        current_case,
        baseline_case,
        (*RATE_METRICS, "false_positive_count", "false_negative_count"),
    )
    return {
        "precision_delta": deltas["precision"],
        "recall_delta": deltas["recall"],
        "evidence_hit_rate_delta": deltas["evidence_hit_rate"],
        "false_positive_delta": deltas["false_positive_count"],
        "false_negative_delta": deltas["false_negative_count"],
    }


def _case_delta_sort_key(case_delta: dict[str, Any]) -> tuple[float, float, float, float, float, str]:
    return (
        _low_sort_value(case_delta.get("recall_delta")),
        _low_sort_value(case_delta.get("precision_delta")),
        -_high_sort_value(case_delta.get("false_negative_delta")),
        -_high_sort_value(case_delta.get("false_positive_delta")),
        _low_sort_value(case_delta.get("evidence_hit_rate_delta")),
        str(case_delta.get("case_id", "")),
    )


def _metric_deltas(
    current: dict[str, Any],
    baseline: dict[str, Any],
    metrics: tuple[str, ...],
) -> dict[str, float | int | None]:
    return {
        metric: _delta(
            current[metric] if metric in current else None,
            baseline[metric] if metric in baseline else None,
        )
        for metric in metrics
    }


def _delta(current: Any, baseline: Any) -> float | int | None:
    if not _is_number(current) or not _is_number(baseline):
        return None
    delta = current - baseline
    if isinstance(current, float) or isinstance(baseline, float):
        return round(delta, 10)
    return delta


def _low_sort_value(value: Any) -> float:
    if not _is_number(value):
        return 0.0
    return float(value)


def _high_sort_value(value: Any) -> float:
    if not _is_number(value):
        return 0.0
    return float(value)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
