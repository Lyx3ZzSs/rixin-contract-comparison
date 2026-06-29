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
    "max_false_positive_increase": 1,
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


def apply_gates(
    current_report: dict[str, Any],
    comparison: dict[str, Any],
    thresholds: dict[str, float | int],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    aggregate = current_report.get("aggregate", {})

    _check_min_gate(failures, "min_recall", aggregate.get("recall"), thresholds)
    _check_min_gate(failures, "min_precision", aggregate.get("precision"), thresholds)
    _check_min_gate(
        failures,
        "min_evidence_hit_rate",
        aggregate.get("evidence_hit_rate"),
        thresholds,
    )
    _check_max_gate(
        failures,
        "max_task_failure_count",
        aggregate.get("task_failure_count"),
        thresholds,
    )
    _check_max_gate(
        failures,
        "max_false_positive_count",
        aggregate.get("false_positive_count"),
        thresholds,
    )
    _check_max_gate(
        failures,
        "max_false_negative_count",
        aggregate.get("false_negative_count"),
        thresholds,
    )

    if comparison.get("baseline_available") is True:
        aggregate_delta = comparison.get("aggregate_delta", {})
        _check_drop_gate(
            failures,
            "max_recall_drop",
            aggregate_delta.get("recall"),
            thresholds,
        )
        _check_drop_gate(
            failures,
            "max_precision_drop",
            aggregate_delta.get("precision"),
            thresholds,
        )
        _check_drop_gate(
            failures,
            "max_evidence_hit_rate_drop",
            aggregate_delta.get("evidence_hit_rate"),
            thresholds,
        )
        _check_increase_gate(
            failures,
            "max_false_positive_increase",
            aggregate_delta.get("false_positive_count"),
            thresholds,
        )
        _check_increase_gate(
            failures,
            "max_false_negative_increase",
            aggregate_delta.get("false_negative_count"),
            thresholds,
        )
        _check_increase_gate(
            failures,
            "max_task_failure_increase",
            aggregate_delta.get("task_failure_count"),
            thresholds,
        )

    comparison["failed_gates"] = failures
    return failures


def _check_min_gate(
    failures: list[dict[str, Any]],
    gate: str,
    value: Any,
    thresholds: dict[str, float | int],
) -> None:
    limit = thresholds.get(gate)
    if _is_number(value) and _is_number(limit) and value < limit:
        failures.append({"gate": gate, "value": value, "limit": limit})


def _check_max_gate(
    failures: list[dict[str, Any]],
    gate: str,
    value: Any,
    thresholds: dict[str, float | int],
) -> None:
    limit = thresholds.get(gate)
    if _is_number(value) and _is_number(limit) and value > limit:
        failures.append({"gate": gate, "value": value, "limit": limit})


def _check_drop_gate(
    failures: list[dict[str, Any]],
    gate: str,
    delta: Any,
    thresholds: dict[str, float | int],
) -> None:
    limit = thresholds.get(gate)
    if _is_number(delta) and _is_number(limit) and delta < -limit:
        failures.append({"gate": gate, "value": delta, "limit": limit})


def _check_increase_gate(
    failures: list[dict[str, Any]],
    gate: str,
    delta: Any,
    thresholds: dict[str, float | int],
) -> None:
    limit = thresholds.get(gate)
    if _is_number(delta) and _is_number(limit) and delta > limit:
        failures.append({"gate": gate, "value": delta, "limit": limit})


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
