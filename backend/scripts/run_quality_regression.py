from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

from scripts.evaluate_ocr_compare_quality import evaluate_case_root, write_html_report

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
    _validate_threshold_keys(thresholds)
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


def _validate_threshold_keys(thresholds: dict[str, float | int]) -> None:
    for gate in DEFAULT_REGRESSION_THRESHOLDS:
        if gate not in thresholds:
            raise KeyError(gate)


def _check_min_gate(
    failures: list[dict[str, Any]],
    gate: str,
    value: Any,
    thresholds: dict[str, float | int],
) -> None:
    limit = thresholds[gate]
    if _is_number(value) and _is_number(limit) and value < limit:
        failures.append({"gate": gate, "value": value, "limit": limit})


def _check_max_gate(
    failures: list[dict[str, Any]],
    gate: str,
    value: Any,
    thresholds: dict[str, float | int],
) -> None:
    limit = thresholds[gate]
    if _is_number(value) and _is_number(limit) and value > limit:
        failures.append({"gate": gate, "value": value, "limit": limit})


def _check_drop_gate(
    failures: list[dict[str, Any]],
    gate: str,
    delta: Any,
    thresholds: dict[str, float | int],
) -> None:
    limit = thresholds[gate]
    if _is_number(delta) and _is_number(limit) and delta < -limit:
        failures.append({"gate": gate, "value": abs(delta), "delta": delta, "limit": limit})


def _check_increase_gate(
    failures: list[dict[str, Any]],
    gate: str,
    delta: Any,
    thresholds: dict[str, float | int],
) -> None:
    limit = thresholds[gate]
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


def run_regression(
    *,
    case_root: Path,
    output_dir: Path,
    baseline_path: Path | None,
    thresholds_path: Path | None,
    run_id: str | None,
    html_output: Path | None,
    write_baseline: Path | None,
) -> dict[str, Any]:
    if not case_root.exists():
        raise FileNotFoundError(f"case root does not exist: {case_root}")
    if baseline_path is not None and not baseline_path.exists():
        raise FileNotFoundError(f"baseline report does not exist: {baseline_path}")
    thresholds = load_thresholds(thresholds_path)
    current_report = evaluate_case_root(case_root)
    baseline_report = _read_json(baseline_path) if baseline_path else None
    comparison = compare_reports(current_report, baseline_report)
    failed_gates = apply_gates(current_report, comparison, thresholds)
    current_run_id = run_id or _default_run_id()
    output_dir.mkdir(parents=True, exist_ok=True)
    html_dir = html_output or output_dir / "html"
    current_report["regression"] = {
        "run_id": current_run_id,
        "git": git_metadata(),
        "baseline_path": str(baseline_path) if baseline_path else None,
        "thresholds": thresholds,
        "comparison": comparison,
        "failed_gates": failed_gates,
        "status": "FAILED" if failed_gates else "PASSED",
    }
    _write_json(output_dir / "quality.json", current_report)
    _write_json(output_dir / "baseline_comparison.json", comparison)
    write_html_report(html_dir, current_report)
    summary = {
        "run_id": current_run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git": current_report["regression"]["git"],
        "case_root": str(case_root),
        "baseline_path": str(baseline_path) if baseline_path else None,
        "thresholds": thresholds,
        "current_report_path": "quality.json",
        "baseline_comparison_path": "baseline_comparison.json",
        "html_report_path": str(html_dir / "index.html"),
        "status": current_report["regression"]["status"],
        "failed_gates": failed_gates,
    }
    _write_json(output_dir / "run_summary.json", summary)
    if write_baseline:
        write_baseline.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output_dir / "quality.json", write_baseline)
    return summary


def git_metadata() -> dict[str, Any]:
    return {
        "branch": _git_output(["git", "rev-parse", "--abbrev-ref", "HEAD"]),
        "commit": _git_output(["git", "rev-parse", "HEAD"]),
        "dirty": bool(_git_output(["git", "status", "--porcelain"])),
    }


def _git_output(command: list[str]) -> str:
    try:
        return subprocess.check_output(command, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run OCR compare quality regression against approved gold cases."
    )
    parser.add_argument("--case-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--thresholds", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--html-output", type=Path, default=None)
    parser.add_argument("--write-baseline", type=Path, default=None)
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args(argv)

    summary = run_regression(
        case_root=args.case_root,
        output_dir=args.output_dir,
        baseline_path=args.baseline,
        thresholds_path=args.thresholds,
        run_id=args.run_id,
        html_output=args.html_output,
        write_baseline=args.write_baseline,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_regression and summary["failed_gates"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
