import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import run_quality_regression
from scripts.run_quality_regression import (
    DEFAULT_REGRESSION_THRESHOLDS,
    apply_gates,
    compare_reports,
    load_thresholds,
    run_regression,
)


def _quality_report(
    *,
    precision: float = 1.0,
    recall: float = 1.0,
    evidence_hit_rate: float = 1.0,
    false_positive_count: int = 0,
    false_negative_count: int = 0,
    known_false_positive_regression_count: int = 0,
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
            "known_false_positive_regression_count": (
                known_false_positive_regression_count
            ),
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
    assert thresholds["min_recall"] == 0.95
    assert thresholds["max_false_positive_increase"] == 1
    assert thresholds["max_known_false_positive_regression_count"] == 0
    assert thresholds["max_known_false_positive_regression_increase"] == 0
    assert "max_false_positive_increase" in thresholds
    assert "max_false_negative_increase" in thresholds
    assert "max_known_false_positive_regression_increase" in thresholds
    assert "max_task_failure_increase" in thresholds
    assert "max_false_positive_count_increase" not in thresholds
    assert "max_false_negative_count_increase" not in thresholds


def test_load_thresholds_merges_json_file_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps({"min_recall": 0.92}), encoding="utf-8")

    thresholds = load_thresholds(path)

    assert thresholds["min_recall"] == 0.92
    assert thresholds["min_precision"] == DEFAULT_REGRESSION_THRESHOLDS["min_precision"]


def test_load_thresholds_rejects_unknown_key(tmp_path: Path) -> None:
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps({"unknown_threshold": 1}), encoding="utf-8")

    with pytest.raises(ValueError, match="Unknown regression threshold"):
        load_thresholds(path)


def test_load_thresholds_rejects_non_object_file(tmp_path: Path) -> None:
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps(["min_recall", 0.92]), encoding="utf-8")

    with pytest.raises(ValueError, match="must contain a JSON object"):
        load_thresholds(path)


def test_load_thresholds_rejects_non_numeric_value(tmp_path: Path) -> None:
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps({"min_recall": "0.92"}), encoding="utf-8")

    with pytest.raises(ValueError, match="must be numeric"):
        load_thresholds(path)


def test_compare_reports_returns_aggregate_and_case_deltas() -> None:
    baseline = _quality_report(
        precision=0.95,
        recall=1.0,
        evidence_hit_rate=0.9,
        false_positive_count=1,
        false_negative_count=0,
        known_false_positive_regression_count=0,
        cases=[_case_report("case_a", precision=0.95, recall=1.0)],
    )
    current = _quality_report(
        precision=0.9,
        recall=0.8,
        evidence_hit_rate=0.85,
        false_positive_count=3,
        false_negative_count=2,
        known_false_positive_regression_count=2,
        cases=[_case_report("case_a", precision=0.8, recall=0.75)],
    )

    comparison = compare_reports(current, baseline)

    assert comparison["baseline_available"] is True
    assert comparison["failed_gates"] == []
    assert comparison["aggregate_delta"]["precision"] == -0.05
    assert comparison["aggregate_delta"]["recall"] == -0.2
    assert comparison["aggregate_delta"]["evidence_hit_rate"] == -0.05
    assert comparison["aggregate_delta"]["false_positive_count"] == 2
    assert comparison["aggregate_delta"]["false_negative_count"] == 2
    assert comparison["aggregate_delta"]["known_false_positive_regression_count"] == 2
    assert comparison["case_deltas"][0]["case_id"] == "case_a"
    assert comparison["case_deltas"][0]["precision_delta"] == -0.15
    assert comparison["case_deltas"][0]["recall_delta"] == -0.25
    assert comparison["case_deltas"][0]["false_positive_delta"] == 0
    assert comparison["case_deltas"][0]["false_negative_delta"] == 0
    assert "false_positive_count_delta" not in comparison["case_deltas"][0]
    assert "false_negative_count_delta" not in comparison["case_deltas"][0]


def test_compare_reports_returns_empty_baseline_comparison_without_baseline() -> None:
    comparison = compare_reports(_quality_report(), None)

    assert comparison["baseline_available"] is False
    assert comparison["aggregate_delta"] == {}
    assert comparison["case_deltas"] == []
    assert comparison["failed_gates"] == []


def test_compare_reports_returns_none_delta_for_missing_none_or_non_numeric_metrics() -> None:
    baseline = _quality_report(precision=0.95)
    current = _quality_report(precision=0.9)
    del baseline["aggregate"]["precision"]
    baseline["aggregate"]["recall"] = None
    current["aggregate"]["evidence_hit_rate"] = "n/a"

    comparison = compare_reports(current, baseline)

    assert comparison["aggregate_delta"]["precision"] is None
    assert comparison["aggregate_delta"]["recall"] is None
    assert comparison["aggregate_delta"]["evidence_hit_rate"] is None


def test_compare_reports_sorts_case_deltas_by_regression_risk() -> None:
    baseline = _quality_report(
        cases=[
            _case_report("case_recall", precision=0.9, recall=0.9),
            _case_report("case_precision", precision=0.9, recall=0.9),
            _case_report("case_fn", precision=0.9, recall=0.9),
            _case_report("case_fp", precision=0.9, recall=0.9),
            _case_report("case_evidence", precision=0.9, recall=0.9, evidence_hit_rate=0.9),
            _case_report("case_a", precision=0.9, recall=0.9),
            _case_report("case_b", precision=0.9, recall=0.9),
        ]
    )
    current = _quality_report(
        cases=[
            _case_report("case_evidence", precision=0.9, recall=0.9, evidence_hit_rate=0.2),
            _case_report("case_b", precision=0.9, recall=0.9),
            _case_report("case_fp", precision=0.9, recall=0.9, false_positive_count=5),
            _case_report("case_a", precision=0.9, recall=0.9),
            _case_report("case_fn", precision=0.9, recall=0.9, false_negative_count=3),
            _case_report("case_precision", precision=0.1, recall=0.9),
            _case_report("case_recall", precision=0.9, recall=0.1),
        ]
    )

    comparison = compare_reports(current, baseline)

    assert [case["case_id"] for case in comparison["case_deltas"]] == [
        "case_recall",
        "case_precision",
        "case_fn",
        "case_fp",
        "case_evidence",
        "case_a",
        "case_b",
    ]


def test_apply_gates_detects_absolute_failures_without_baseline() -> None:
    current = _quality_report(
        precision=0.8,
        recall=0.7,
        evidence_hit_rate=0.6,
        false_positive_count=2,
        false_negative_count=1,
        task_failure_count=1,
    )
    comparison = compare_reports(current, None)

    failures = apply_gates(
        current,
        comparison,
        {
            "min_recall": 0.95,
            "min_precision": 0.9,
            "min_evidence_hit_rate": 0.9,
            "max_task_failure_count": 0,
            "max_false_positive_count": 0,
            "max_false_negative_count": 0,
            "max_known_false_positive_regression_count": 0,
            "max_recall_drop": 0.02,
            "max_precision_drop": 0.02,
            "max_evidence_hit_rate_drop": 0.02,
            "max_false_positive_increase": 1,
            "max_false_negative_increase": 0,
            "max_known_false_positive_regression_increase": 0,
            "max_task_failure_increase": 0,
        },
    )

    assert "min_recall" in {item["gate"] for item in failures}
    assert "min_precision" in {item["gate"] for item in failures}
    assert "min_evidence_hit_rate" in {item["gate"] for item in failures}
    assert "max_task_failure_count" in {item["gate"] for item in failures}
    assert "max_false_positive_count" in {item["gate"] for item in failures}
    assert "max_false_negative_count" in {item["gate"] for item in failures}


def test_apply_gates_fails_on_known_false_positive_regression_count() -> None:
    current = _quality_report(known_false_positive_regression_count=1)
    comparison = compare_reports(current, None)
    thresholds = DEFAULT_REGRESSION_THRESHOLDS | {
        "max_known_false_positive_regression_count": 0,
        "max_known_false_positive_regression_increase": 0,
    }

    failures = apply_gates(current, comparison, thresholds)

    assert {
        "gate": "max_known_false_positive_regression_count",
        "value": 1,
        "limit": 0,
    } in failures


def test_apply_gates_detects_regressions_against_baseline() -> None:
    baseline = _quality_report(
        precision=1.0,
        recall=1.0,
        evidence_hit_rate=1.0,
        false_positive_count=0,
        false_negative_count=0,
        known_false_positive_regression_count=0,
        task_failure_count=0,
    )
    current = _quality_report(
        precision=0.9,
        recall=0.8,
        evidence_hit_rate=0.85,
        false_positive_count=2,
        false_negative_count=1,
        known_false_positive_regression_count=1,
        task_failure_count=1,
    )
    comparison = compare_reports(current, baseline)

    failures = apply_gates(current, comparison, DEFAULT_REGRESSION_THRESHOLDS)

    assert "max_recall_drop" in {item["gate"] for item in failures}
    assert "max_precision_drop" in {item["gate"] for item in failures}
    assert "max_evidence_hit_rate_drop" in {item["gate"] for item in failures}
    assert "max_false_positive_increase" in {item["gate"] for item in failures}
    assert "max_false_negative_increase" in {item["gate"] for item in failures}
    assert "max_known_false_positive_regression_increase" in {
        item["gate"] for item in failures
    }
    assert "max_task_failure_increase" in {item["gate"] for item in failures}


def test_apply_gates_detects_known_false_positive_regression_increase() -> None:
    baseline = _quality_report(known_false_positive_regression_count=0)
    current = _quality_report(known_false_positive_regression_count=2)
    comparison = compare_reports(current, baseline)

    failures = apply_gates(current, comparison, DEFAULT_REGRESSION_THRESHOLDS)

    assert {
        "gate": "max_known_false_positive_regression_increase",
        "value": 2,
        "limit": 0,
    } in failures


def test_apply_gates_allows_metrics_that_exactly_meet_limits() -> None:
    thresholds = {
        "min_recall": 0.95,
        "min_precision": 0.9,
        "min_evidence_hit_rate": 0.9,
        "max_task_failure_count": 0,
        "max_false_positive_count": 1,
        "max_false_negative_count": 0,
        "max_known_false_positive_regression_count": 0,
        "max_recall_drop": 0.02,
        "max_precision_drop": 0.02,
        "max_evidence_hit_rate_drop": 0.02,
        "max_false_positive_increase": 1,
        "max_false_negative_increase": 0,
        "max_known_false_positive_regression_increase": 0,
        "max_task_failure_increase": 0,
    }
    baseline = _quality_report(
        precision=0.92,
        recall=0.97,
        evidence_hit_rate=0.92,
        false_positive_count=0,
        false_negative_count=0,
        task_failure_count=0,
    )
    current = _quality_report(
        precision=0.9,
        recall=0.95,
        evidence_hit_rate=0.9,
        false_positive_count=1,
        false_negative_count=0,
        task_failure_count=0,
    )
    comparison = compare_reports(current, baseline)

    failures = apply_gates(current, comparison, thresholds)

    assert failures == []
    assert comparison["failed_gates"] == []


def test_apply_gates_reports_drop_failure_value_as_positive_magnitude() -> None:
    baseline = _quality_report(recall=1.0)
    current = _quality_report(recall=0.9)
    comparison = compare_reports(current, baseline)

    failures = apply_gates(current, comparison, DEFAULT_REGRESSION_THRESHOLDS)

    recall_failure = next(item for item in failures if item["gate"] == "max_recall_drop")
    assert recall_failure == {
        "gate": "max_recall_drop",
        "value": 0.1,
        "delta": -0.1,
        "limit": 0.02,
    }
    assert comparison["aggregate_delta"]["recall"] == -0.1


def test_apply_gates_ignores_non_numeric_or_missing_regression_deltas() -> None:
    baseline = _quality_report()
    current = _quality_report()
    comparison = compare_reports(current, baseline)
    comparison["aggregate_delta"]["recall"] = None
    comparison["aggregate_delta"]["precision"] = "n/a"
    comparison["aggregate_delta"].pop("evidence_hit_rate")

    failures = apply_gates(current, comparison, DEFAULT_REGRESSION_THRESHOLDS)

    assert failures == []
    assert comparison["failed_gates"] == []


def test_apply_gates_fails_fast_for_incomplete_thresholds() -> None:
    current = _quality_report(recall=0.7)
    comparison = compare_reports(current, None)
    thresholds = dict(DEFAULT_REGRESSION_THRESHOLDS)
    del thresholds["min_recall"]

    with pytest.raises(KeyError, match="min_recall"):
        apply_gates(current, comparison, thresholds)


def _write_stub_html_report(path: Path, _payload: dict) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "index.html").write_text("html", encoding="utf-8")


def test_run_regression_writes_artifacts_and_baseline(
    tmp_path: Path, monkeypatch
) -> None:
    case_root = tmp_path / "cases"
    case_root.mkdir()
    output_dir = tmp_path / "run"
    write_baseline = tmp_path / "baseline.json"
    report = _quality_report(
        precision=1.0,
        recall=1.0,
        evidence_hit_rate=1.0,
        cases=[_case_report("case_a")],
    )

    monkeypatch.setattr(
        run_quality_regression,
        "evaluate_case_root",
        lambda path, *, dataset_splits=None: dict(report),
    )
    monkeypatch.setattr(
        run_quality_regression,
        "write_html_report",
        _write_stub_html_report,
    )
    monkeypatch.setattr(
        run_quality_regression,
        "git_metadata",
        lambda: {"branch": "v0.0.2", "commit": "abc123", "dirty": False},
    )

    result = run_regression(
        case_root=case_root,
        output_dir=output_dir,
        baseline_path=None,
        thresholds_path=None,
        run_id="manual-run",
        html_output=None,
        write_baseline=write_baseline,
    )

    assert result["status"] == "PASSED"
    assert (output_dir / "quality.json").exists()
    assert (output_dir / "baseline_comparison.json").exists()
    assert (output_dir / "run_summary.json").exists()
    assert (output_dir / "html" / "index.html").exists()
    assert write_baseline.exists()
    summary = json.loads((output_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["run_id"] == "manual-run"
    assert summary["git"]["commit"] == "abc123"
    assert summary["current_report_path"] == "quality.json"
    written_quality = json.loads((output_dir / "quality.json").read_text(encoding="utf-8"))
    written_baseline = json.loads(write_baseline.read_text(encoding="utf-8"))
    assert written_baseline == written_quality


def test_run_regression_compares_existing_baseline(
    tmp_path: Path, monkeypatch
) -> None:
    case_root = tmp_path / "cases"
    case_root.mkdir()
    output_dir = tmp_path / "run"
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(_quality_report(precision=0.9, recall=0.95)),
        encoding="utf-8",
    )
    current_report = _quality_report(precision=1.0, recall=1.0)

    monkeypatch.setattr(
        run_quality_regression,
        "evaluate_case_root",
        lambda path, *, dataset_splits=None: dict(current_report),
    )
    monkeypatch.setattr(
        run_quality_regression,
        "write_html_report",
        _write_stub_html_report,
    )
    monkeypatch.setattr(
        run_quality_regression,
        "git_metadata",
        lambda: {"branch": "v0.0.2", "commit": "abc123", "dirty": False},
    )

    run_regression(
        case_root=case_root,
        output_dir=output_dir,
        baseline_path=baseline_path,
        thresholds_path=None,
        run_id="baseline-run",
        html_output=None,
        write_baseline=None,
    )

    comparison = json.loads(
        (output_dir / "baseline_comparison.json").read_text(encoding="utf-8")
    )
    assert comparison["baseline_available"] is True
    assert comparison["aggregate_delta"]["precision"] == 0.1
    assert comparison["aggregate_delta"]["recall"] == 0.05


def test_run_regression_marks_failed_gate_status(
    tmp_path: Path, monkeypatch
) -> None:
    case_root = tmp_path / "cases"
    case_root.mkdir()
    output_dir = tmp_path / "run"
    current_report = _quality_report(precision=0.8, recall=0.7)

    monkeypatch.setattr(
        run_quality_regression,
        "evaluate_case_root",
        lambda path, *, dataset_splits=None: dict(current_report),
    )
    monkeypatch.setattr(
        run_quality_regression,
        "write_html_report",
        _write_stub_html_report,
    )
    monkeypatch.setattr(
        run_quality_regression,
        "git_metadata",
        lambda: {"branch": "v0.0.2", "commit": "abc123", "dirty": False},
    )

    summary = run_regression(
        case_root=case_root,
        output_dir=output_dir,
        baseline_path=None,
        thresholds_path=None,
        run_id="failed-run",
        html_output=None,
        write_baseline=None,
    )

    written_summary = json.loads(
        (output_dir / "run_summary.json").read_text(encoding="utf-8")
    )
    quality = json.loads((output_dir / "quality.json").read_text(encoding="utf-8"))
    assert summary["status"] == "FAILED"
    assert written_summary["status"] == "FAILED"
    assert quality["regression"]["status"] == "FAILED"
    assert {item["gate"] for item in summary["failed_gates"]} >= {
        "min_precision",
        "min_recall",
    }


def test_run_regression_uses_html_output_override(
    tmp_path: Path, monkeypatch
) -> None:
    case_root = tmp_path / "cases"
    case_root.mkdir()
    output_dir = tmp_path / "run"
    html_output = tmp_path / "custom-html"
    current_report = _quality_report()

    monkeypatch.setattr(
        run_quality_regression,
        "evaluate_case_root",
        lambda path, *, dataset_splits=None: dict(current_report),
    )
    monkeypatch.setattr(
        run_quality_regression,
        "write_html_report",
        _write_stub_html_report,
    )
    monkeypatch.setattr(
        run_quality_regression,
        "git_metadata",
        lambda: {"branch": "v0.0.2", "commit": "abc123", "dirty": False},
    )

    summary = run_regression(
        case_root=case_root,
        output_dir=output_dir,
        baseline_path=None,
        thresholds_path=None,
        run_id="html-run",
        html_output=html_output,
        write_baseline=None,
    )

    assert (html_output / "index.html").exists()
    assert not (output_dir / "html" / "index.html").exists()
    assert summary["html_report_path"] == str(html_output / "index.html")


def test_run_regression_rejects_missing_case_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="case root does not exist"):
        run_regression(
            case_root=tmp_path / "missing-cases",
            output_dir=tmp_path / "run",
            baseline_path=None,
            thresholds_path=None,
            run_id=None,
            html_output=None,
            write_baseline=None,
        )


def test_run_regression_rejects_missing_baseline_path(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    case_root.mkdir()

    with pytest.raises(FileNotFoundError, match="baseline report does not exist"):
        run_regression(
            case_root=case_root,
            output_dir=tmp_path / "run",
            baseline_path=tmp_path / "missing-baseline.json",
            thresholds_path=None,
            run_id=None,
            html_output=None,
            write_baseline=None,
        )


def test_git_metadata_returns_empty_values_when_git_commands_fail(
    monkeypatch,
) -> None:
    def raise_os_error(command: list[str], *, text: bool) -> str:
        raise OSError("git unavailable")

    monkeypatch.setattr(
        run_quality_regression.subprocess,
        "check_output",
        raise_os_error,
    )

    metadata = run_quality_regression.git_metadata()

    assert metadata == {"branch": "", "commit": "", "dirty": False}


def test_main_returns_zero_when_gates_fail_without_fail_flag(
    tmp_path: Path, monkeypatch
) -> None:
    case_root = tmp_path / "cases"
    case_root.mkdir()
    output_dir = tmp_path / "run"

    monkeypatch.setattr(
        run_quality_regression,
        "evaluate_case_root",
        lambda path, *, dataset_splits=None: _quality_report(
            precision=0.1,
            recall=0.1,
            evidence_hit_rate=0.1,
        ),
    )
    monkeypatch.setattr(
        run_quality_regression,
        "write_html_report",
        _write_stub_html_report,
    )

    exit_code = run_quality_regression.main(
        [
            "--case-root",
            str(case_root),
            "--output-dir",
            str(output_dir),
            "--run-id",
            "cli-run",
        ]
    )

    assert exit_code == 0
    assert json.loads((output_dir / "run_summary.json").read_text(encoding="utf-8"))[
        "status"
    ] == "FAILED"


def test_main_returns_one_when_gates_fail_with_fail_flag(
    tmp_path: Path, monkeypatch
) -> None:
    case_root = tmp_path / "cases"
    case_root.mkdir()
    output_dir = tmp_path / "run"

    monkeypatch.setattr(
        run_quality_regression,
        "evaluate_case_root",
        lambda path, *, dataset_splits=None: _quality_report(
            precision=0.1,
            recall=0.1,
            evidence_hit_rate=0.1,
        ),
    )
    monkeypatch.setattr(
        run_quality_regression,
        "write_html_report",
        _write_stub_html_report,
    )

    exit_code = run_quality_regression.main(
        [
            "--case-root",
            str(case_root),
            "--output-dir",
            str(output_dir),
            "--fail-on-regression",
        ]
    )

    assert exit_code == 1


def test_direct_script_help_succeeds_from_backend_cwd() -> None:
    backend_dir = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [sys.executable, "scripts/run_quality_regression.py", "--help"],
        cwd=backend_dir,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Run OCR compare quality regression" in result.stdout


def test_main_returns_zero_when_fail_flag_has_no_failed_gates(
    tmp_path: Path, monkeypatch
) -> None:
    case_root = tmp_path / "cases"
    case_root.mkdir()
    output_dir = tmp_path / "run"

    monkeypatch.setattr(
        run_quality_regression,
        "evaluate_case_root",
        lambda path, *, dataset_splits=None: _quality_report(),
    )
    monkeypatch.setattr(
        run_quality_regression,
        "write_html_report",
        _write_stub_html_report,
    )

    exit_code = run_quality_regression.main(
        [
            "--case-root",
            str(case_root),
            "--output-dir",
            str(output_dir),
            "--fail-on-regression",
        ]
    )

    assert exit_code == 0


def test_main_forwards_optional_paths_to_run_regression(
    tmp_path: Path, monkeypatch
) -> None:
    case_root = tmp_path / "cases"
    output_dir = tmp_path / "run"
    baseline = tmp_path / "baseline.json"
    thresholds = tmp_path / "thresholds.json"
    html_output = tmp_path / "html"
    write_baseline = tmp_path / "new-baseline.json"
    captured: dict[str, object] = {}

    def capture_run_regression(**kwargs: object) -> dict:
        captured.update(kwargs)
        return {
            "run_id": "forwarded-run",
            "status": "PASSED",
            "failed_gates": [],
        }

    monkeypatch.setattr(
        run_quality_regression,
        "run_regression",
        capture_run_regression,
    )

    exit_code = run_quality_regression.main(
        [
            "--case-root",
            str(case_root),
            "--output-dir",
            str(output_dir),
            "--baseline",
            str(baseline),
            "--thresholds",
            str(thresholds),
            "--run-id",
            "forwarded-run",
            "--html-output",
            str(html_output),
            "--write-baseline",
            str(write_baseline),
            "--dataset-split",
            "regression",
            "--dataset-split",
            "dev",
        ]
    )

    assert exit_code == 0
    assert captured == {
        "case_root": case_root,
        "output_dir": output_dir,
        "baseline_path": baseline,
        "thresholds_path": thresholds,
        "run_id": "forwarded-run",
        "html_output": html_output,
        "write_baseline": write_baseline,
        "dataset_splits": {"dev", "regression"},
    }


def test_main_prints_json_summary(capsys, monkeypatch, tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    output_dir = tmp_path / "run"
    summary = {
        "run_id": "stdout-run",
        "status": "PASSED",
        "failed_gates": [],
    }

    monkeypatch.setattr(
        run_quality_regression,
        "run_regression",
        lambda **kwargs: summary,
    )

    exit_code = run_quality_regression.main(
        [
            "--case-root",
            str(case_root),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == summary
