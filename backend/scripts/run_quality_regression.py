from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_REGRESSION_THRESHOLDS: dict[str, float | int] = {
    "min_precision": 0.9,
    "min_recall": 0.9,
    "min_evidence_hit_rate": 0.9,
    "max_false_positive_count": 0,
    "max_false_negative_count": 0,
    "max_task_failure_count": 0,
    "max_precision_drop": 0.02,
    "max_recall_drop": 0.02,
    "max_evidence_hit_rate_drop": 0.02,
    "max_false_positive_count_increase": 0,
    "max_false_negative_count_increase": 0,
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
            | {
                f"{metric}_delta": delta
                for metric, delta in _metric_deltas(
                    current_case,
                    baseline_case,
                    (*RATE_METRICS, "false_positive_count", "false_negative_count"),
                ).items()
            }
        )
    return deltas


def _metric_deltas(
    current: dict[str, Any],
    baseline: dict[str, Any],
    metrics: tuple[str, ...],
) -> dict[str, float | int]:
    return {
        metric: _delta(current.get(metric, 0), baseline.get(metric, 0))
        for metric in metrics
    }


def _delta(current: Any, baseline: Any) -> float | int:
    delta = current - baseline
    if isinstance(current, float) or isinstance(baseline, float):
        return round(delta, 10)
    return delta


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
