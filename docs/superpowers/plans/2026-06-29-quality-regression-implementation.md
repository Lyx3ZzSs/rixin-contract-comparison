# Phase 5 质量回归体系 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建轻量质量回归体系，让开发者用一个命令基于 gold case 运行质量评估、对比 baseline、执行门禁、生成 JSON/HTML 运行产物。

**Architecture:** 新增 `backend/scripts/run_quality_regression.py` 作为编排层，复用 `scripts.evaluate_ocr_compare_quality.evaluate_case_root()` 与 `write_html_report()`，不复制 OCR 比对评估逻辑。回归 runner 负责加载阈值、读取 baseline、计算 aggregate/case delta、执行 absolute/regression gates、记录 git/run metadata，并把 regression metadata 注入现有 HTML 报告。

**Tech Stack:** Python 3.12 标准库、pytest、现有 backend scripts、现有 OCR compare quality evaluator。

---

## 文件结构

- Create: `backend/scripts/run_quality_regression.py`
  - 职责：质量回归命令入口、阈值加载、baseline 对比、门禁判断、run artifact 写入、baseline 复制、CLI exit code。
- Create: `backend/tests/test_run_quality_regression.py`
  - 职责：覆盖阈值、delta、门禁、run metadata、CLI 行为、baseline 写入。
- Modify: `backend/scripts/evaluate_ocr_compare_quality.py`
  - 职责：让现有 HTML 报告在 `report["regression"]` 存在时渲染回归摘要；保持无 regression metadata 时的现有行为。
- Modify: `backend/tests/test_evaluate_ocr_compare_quality.py`
  - 职责：补充 HTML regression section 的最小测试。
- Create: `docs/quality_regression_workflow.md`
  - 职责：中文说明 Phase 5 使用流程、baseline 建立、回归运行、报告解读、CI 接入方式。

## Task 1: 回归阈值与 baseline 对比核心

**Files:**
- Create: `backend/scripts/run_quality_regression.py`
- Create: `backend/tests/test_run_quality_regression.py`

- [ ] **Step 1: 写失败测试，覆盖默认阈值、阈值文件合并、aggregate/case delta**

Add to `backend/tests/test_run_quality_regression.py`:

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run:

```bash
cd backend
python -m pytest tests/test_run_quality_regression.py -v
```

Expected: FAIL，错误包含 `ModuleNotFoundError: No module named 'scripts.run_quality_regression'` 或缺少目标函数。

- [ ] **Step 3: 实现阈值加载与 baseline 对比核心**

Create `backend/scripts/run_quality_regression.py`:

```python
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_ocr_compare_quality import (  # noqa: E402
    evaluate_case_root,
    write_html_report,
)

DEFAULT_REGRESSION_THRESHOLDS: dict[str, float | int] = {
    "min_recall": 0.95,
    "min_precision": 0.9,
    "min_evidence_hit_rate": 0.9,
    "max_task_failure_count": 0,
    "max_false_positive_count": 0,
    "max_false_negative_count": 0,
    "max_recall_drop": 0.02,
    "max_precision_drop": 0.02,
    "max_evidence_hit_rate_drop": 0.02,
    "max_false_positive_increase": 1,
    "max_false_negative_increase": 0,
    "max_task_failure_increase": 0,
}


def load_thresholds(path: Path | None) -> dict[str, float | int]:
    thresholds = dict(DEFAULT_REGRESSION_THRESHOLDS)
    if path is None:
        return thresholds
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    for key, value in payload.items():
        if key not in thresholds:
            raise ValueError(f"Unknown quality threshold: {key}")
        if not isinstance(value, int | float):
            raise ValueError(f"Quality threshold {key} must be numeric")
        thresholds[key] = value
    return thresholds


def compare_reports(
    current_report: dict[str, Any], baseline_report: dict[str, Any] | None
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
        "aggregate_delta": _metric_delta(
            current_report.get("aggregate", {}),
            baseline_report.get("aggregate", {}),
            [
                "precision",
                "recall",
                "evidence_hit_rate",
                "false_positive_count",
                "false_negative_count",
                "task_failure_count",
            ],
        ),
        "case_deltas": _case_deltas(
            current_report.get("cases", []), baseline_report.get("cases", [])
        ),
        "failed_gates": [],
    }


def _case_deltas(
    current_cases: list[dict[str, Any]], baseline_cases: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    baseline_by_case = {
        str(item.get("case_id", "")): item for item in baseline_cases if item.get("case_id")
    }
    deltas = []
    for current in current_cases:
        case_id = str(current.get("case_id", ""))
        baseline = baseline_by_case.get(case_id)
        if not baseline:
            continue
        delta = _metric_delta(
            current,
            baseline,
            [
                "precision",
                "recall",
                "evidence_hit_rate",
                "false_positive_count",
                "false_negative_count",
            ],
        )
        deltas.append(
            {
                "case_id": case_id,
                "precision_delta": delta.get("precision"),
                "recall_delta": delta.get("recall"),
                "evidence_hit_rate_delta": delta.get("evidence_hit_rate"),
                "false_positive_delta": delta.get("false_positive_count"),
                "false_negative_delta": delta.get("false_negative_count"),
            }
        )
    return sorted(deltas, key=_case_delta_risk_key)


def _case_delta_risk_key(item: dict[str, Any]) -> tuple[float, float, float, float, float]:
    return (
        float(item.get("recall_delta") or 0),
        float(item.get("precision_delta") or 0),
        -float(item.get("false_negative_delta") or 0),
        -float(item.get("false_positive_delta") or 0),
        float(item.get("evidence_hit_rate_delta") or 0),
    )


def _metric_delta(
    current: dict[str, Any], baseline: dict[str, Any], metrics: list[str]
) -> dict[str, float | int | None]:
    return {
        metric: _subtract_metric(current.get(metric), baseline.get(metric))
        for metric in metrics
    }


def _subtract_metric(current: Any, baseline: Any) -> float | int | None:
    if current is None or baseline is None:
        return None
    if not isinstance(current, int | float) or not isinstance(baseline, int | float):
        return None
    delta = current - baseline
    if isinstance(current, int) and isinstance(baseline, int):
        return int(delta)
    return round(float(delta), 4)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: 运行测试，确认通过**

Run:

```bash
cd backend
python -m pytest tests/test_run_quality_regression.py -v
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/scripts/run_quality_regression.py backend/tests/test_run_quality_regression.py
git commit -m "feat: add quality regression comparison core"
```

## Task 2: 质量门禁判断

**Files:**
- Modify: `backend/scripts/run_quality_regression.py`
- Modify: `backend/tests/test_run_quality_regression.py`

- [ ] **Step 1: 写失败测试，覆盖 absolute gates 和 regression gates**

Append to `backend/tests/test_run_quality_regression.py`:

```python
from scripts.run_quality_regression import apply_gates


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
            "max_recall_drop": 0.02,
            "max_precision_drop": 0.02,
            "max_evidence_hit_rate_drop": 0.02,
            "max_false_positive_increase": 1,
            "max_false_negative_increase": 0,
            "max_task_failure_increase": 0,
        },
    )

    assert "min_recall" in {item["gate"] for item in failures}
    assert "min_precision" in {item["gate"] for item in failures}
    assert "min_evidence_hit_rate" in {item["gate"] for item in failures}
    assert "max_task_failure_count" in {item["gate"] for item in failures}
    assert "max_false_positive_count" in {item["gate"] for item in failures}
    assert "max_false_negative_count" in {item["gate"] for item in failures}


def test_apply_gates_detects_regressions_against_baseline() -> None:
    baseline = _quality_report(
        precision=1.0,
        recall=1.0,
        evidence_hit_rate=1.0,
        false_positive_count=0,
        false_negative_count=0,
        task_failure_count=0,
    )
    current = _quality_report(
        precision=0.9,
        recall=0.8,
        evidence_hit_rate=0.85,
        false_positive_count=2,
        false_negative_count=1,
        task_failure_count=1,
    )
    comparison = compare_reports(current, baseline)

    failures = apply_gates(current, comparison, DEFAULT_REGRESSION_THRESHOLDS)

    assert "max_recall_drop" in {item["gate"] for item in failures}
    assert "max_precision_drop" in {item["gate"] for item in failures}
    assert "max_evidence_hit_rate_drop" in {item["gate"] for item in failures}
    assert "max_false_positive_increase" in {item["gate"] for item in failures}
    assert "max_false_negative_increase" in {item["gate"] for item in failures}
    assert "max_task_failure_increase" in {item["gate"] for item in failures}
```

- [ ] **Step 2: 运行测试，确认失败**

Run:

```bash
cd backend
python -m pytest tests/test_run_quality_regression.py::test_apply_gates_detects_absolute_failures_without_baseline tests/test_run_quality_regression.py::test_apply_gates_detects_regressions_against_baseline -v
```

Expected: FAIL，错误包含 `ImportError` 或 `NameError: apply_gates`。

- [ ] **Step 3: 实现 `apply_gates()`**

Append to `backend/scripts/run_quality_regression.py` after `compare_reports()` helpers:

```python
def apply_gates(
    current_report: dict[str, Any],
    comparison: dict[str, Any],
    thresholds: dict[str, float | int],
) -> list[dict[str, Any]]:
    aggregate = current_report.get("aggregate", {})
    failures: list[dict[str, Any]] = []
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
    if comparison.get("baseline_available"):
        delta = comparison.get("aggregate_delta", {})
        _check_drop_gate(failures, "max_recall_drop", delta.get("recall"), thresholds)
        _check_drop_gate(
            failures, "max_precision_drop", delta.get("precision"), thresholds
        )
        _check_drop_gate(
            failures,
            "max_evidence_hit_rate_drop",
            delta.get("evidence_hit_rate"),
            thresholds,
        )
        _check_increase_gate(
            failures,
            "max_false_positive_increase",
            delta.get("false_positive_count"),
            thresholds,
        )
        _check_increase_gate(
            failures,
            "max_false_negative_increase",
            delta.get("false_negative_count"),
            thresholds,
        )
        _check_increase_gate(
            failures,
            "max_task_failure_increase",
            delta.get("task_failure_count"),
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
    limit = thresholds[gate]
    if isinstance(value, int | float) and value < limit:
        failures.append({"gate": gate, "value": value, "limit": limit})


def _check_max_gate(
    failures: list[dict[str, Any]],
    gate: str,
    value: Any,
    thresholds: dict[str, float | int],
) -> None:
    limit = thresholds[gate]
    if isinstance(value, int | float) and value > limit:
        failures.append({"gate": gate, "value": value, "limit": limit})


def _check_drop_gate(
    failures: list[dict[str, Any]],
    gate: str,
    delta: Any,
    thresholds: dict[str, float | int],
) -> None:
    limit = thresholds[gate]
    if isinstance(delta, int | float) and delta < -float(limit):
        failures.append({"gate": gate, "value": abs(delta), "limit": limit})


def _check_increase_gate(
    failures: list[dict[str, Any]],
    gate: str,
    delta: Any,
    thresholds: dict[str, float | int],
) -> None:
    limit = thresholds[gate]
    if isinstance(delta, int | float) and delta > limit:
        failures.append({"gate": gate, "value": delta, "limit": limit})
```

- [ ] **Step 4: 运行测试，确认通过**

Run:

```bash
cd backend
python -m pytest tests/test_run_quality_regression.py -v
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/scripts/run_quality_regression.py backend/tests/test_run_quality_regression.py
git commit -m "feat: add quality regression gates"
```

## Task 3: Runner 产物、git 元数据与 baseline 写入

**Files:**
- Modify: `backend/scripts/run_quality_regression.py`
- Modify: `backend/tests/test_run_quality_regression.py`

- [ ] **Step 1: 写失败测试，覆盖 run artifacts 与 baseline 写入**

Append to `backend/tests/test_run_quality_regression.py`:

```python
from scripts import run_quality_regression
from scripts.run_quality_regression import run_regression


def _write_stub_html_report(path: Path, payload: dict) -> None:
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
        run_quality_regression, "evaluate_case_root", lambda path: dict(report)
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run:

```bash
cd backend
python -m pytest tests/test_run_quality_regression.py::test_run_regression_writes_artifacts_and_baseline -v
```

Expected: FAIL，错误包含 `ImportError` 或 `NameError: run_regression`。

- [ ] **Step 3: 实现 runner、run metadata、git metadata、baseline 写入**

Append to `backend/scripts/run_quality_regression.py` before `_read_json()`:

```python
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
```

- [ ] **Step 4: 运行测试，确认通过**

Run:

```bash
cd backend
python -m pytest tests/test_run_quality_regression.py -v
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/scripts/run_quality_regression.py backend/tests/test_run_quality_regression.py
git commit -m "feat: write quality regression run artifacts"
```

## Task 4: CLI 与退出码

**Files:**
- Modify: `backend/scripts/run_quality_regression.py`
- Modify: `backend/tests/test_run_quality_regression.py`

- [ ] **Step 1: 写失败测试，覆盖 CLI 成功与失败退出码**

Append to `backend/tests/test_run_quality_regression.py`:

```python
def test_main_returns_zero_when_gates_fail_without_fail_flag(
    tmp_path: Path, monkeypatch
) -> None:
    case_root = tmp_path / "cases"
    case_root.mkdir()
    output_dir = tmp_path / "run"

    monkeypatch.setattr(
        run_quality_regression,
        "evaluate_case_root",
        lambda path: _quality_report(precision=0.1, recall=0.1, evidence_hit_rate=0.1),
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
        lambda path: _quality_report(precision=0.1, recall=0.1, evidence_hit_rate=0.1),
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run:

```bash
cd backend
python -m pytest tests/test_run_quality_regression.py::test_main_returns_zero_when_gates_fail_without_fail_flag tests/test_run_quality_regression.py::test_main_returns_one_when_gates_fail_with_fail_flag -v
```

Expected: FAIL，错误包含 `AttributeError: module 'scripts.run_quality_regression' has no attribute 'main'`。

- [ ] **Step 3: 实现 CLI**

Append to bottom of `backend/scripts/run_quality_regression.py`:

```python
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
```

- [ ] **Step 4: 运行测试，确认通过**

Run:

```bash
cd backend
python -m pytest tests/test_run_quality_regression.py -v
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/scripts/run_quality_regression.py backend/tests/test_run_quality_regression.py
git commit -m "feat: add quality regression CLI"
```

## Task 5: HTML 报告回归区块

**Files:**
- Modify: `backend/scripts/evaluate_ocr_compare_quality.py`
- Modify: `backend/tests/test_evaluate_ocr_compare_quality.py`

- [ ] **Step 1: 写失败测试，确认 HTML 包含 run/gate/baseline 信息**

Append to `backend/tests/test_evaluate_ocr_compare_quality.py`:

```python
def test_write_html_report_includes_regression_summary(tmp_path: Path) -> None:
    report = evaluate_case_root(Path("tests/fixtures/ocr_compare_cases"))
    report["regression"] = {
        "run_id": "phase5-run",
        "git": {"branch": "v0.0.2", "commit": "abc123", "dirty": True},
        "baseline_path": "baseline.json",
        "status": "FAILED",
        "failed_gates": [
            {"gate": "max_recall_drop", "value": 0.1, "limit": 0.02}
        ],
        "comparison": {
            "baseline_available": True,
            "aggregate_delta": {
                "precision": -0.01,
                "recall": -0.1,
                "evidence_hit_rate": 0.0,
                "false_positive_count": 1,
                "false_negative_count": 2,
                "task_failure_count": 0,
            },
            "case_deltas": [
                {
                    "case_id": "simple_scanned",
                    "precision_delta": -0.01,
                    "recall_delta": -0.1,
                    "evidence_hit_rate_delta": 0.0,
                    "false_positive_delta": 1,
                    "false_negative_delta": 2,
                }
            ],
            "failed_gates": [],
        },
    }

    write_html_report(tmp_path, report)

    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "Quality regression" in index
    assert "phase5-run" in index
    assert "max_recall_drop" in index
    assert "simple_scanned" in index
```

- [ ] **Step 2: 运行测试，确认失败**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_write_html_report_includes_regression_summary -v
```

Expected: FAIL，HTML 中暂时没有 `Quality regression`。

- [ ] **Step 3: 添加 HTML regression section helper**

Modify `backend/scripts/evaluate_ocr_compare_quality.py` inside `write_html_report()`:

```python
    regression_section = _regression_html(report.get("regression"))
```

Add `regression_section` after the threshold failures paragraph in the `index` string:

```python
        f"<p class='failures'>Threshold failures: {failures}</p>"
        f"{regression_section}"
```

Add helper functions before `_case_page_filename()`:

```python
def _regression_html(regression: dict[str, Any] | None) -> str:
    if not regression:
        return ""
    failed_gates = regression.get("failed_gates", [])
    gate_rows = _html_table(
        ["Gate", "Value", "Limit"],
        [
            [item.get("gate", ""), item.get("value", ""), item.get("limit", "")]
            for item in failed_gates
        ],
    )
    comparison = regression.get("comparison", {})
    aggregate_delta = comparison.get("aggregate_delta", {})
    aggregate_rows = [
        [metric, aggregate_delta.get(metric, "")]
        for metric in [
            "precision",
            "recall",
            "evidence_hit_rate",
            "false_positive_count",
            "false_negative_count",
            "task_failure_count",
        ]
    ]
    case_rows = [
        [
            item.get("case_id", ""),
            item.get("recall_delta", ""),
            item.get("precision_delta", ""),
            item.get("false_negative_delta", ""),
            item.get("false_positive_delta", ""),
            item.get("evidence_hit_rate_delta", ""),
        ]
        for item in comparison.get("case_deltas", [])
    ]
    git = regression.get("git", {})
    return (
        "<h2>Quality regression</h2>"
        "<dl>"
        f"<dt>Run ID</dt><dd>{html.escape(str(regression.get('run_id', '')))}</dd>"
        f"<dt>Status</dt><dd>{html.escape(str(regression.get('status', '')))}</dd>"
        f"<dt>Branch</dt><dd>{html.escape(str(git.get('branch', '')))}</dd>"
        f"<dt>Commit</dt><dd>{html.escape(str(git.get('commit', '')))}</dd>"
        f"<dt>Dirty</dt><dd>{html.escape(str(git.get('dirty', '')))}</dd>"
        f"<dt>Baseline</dt><dd>{html.escape(str(regression.get('baseline_path') or 'None'))}</dd>"
        "</dl>"
        "<h3>Failed gates</h3>"
        f"{gate_rows}"
        "<h3>Aggregate delta</h3>"
        f"{_html_table(['Metric', 'Delta'], aggregate_rows)}"
        "<h3>Case regression ranking</h3>"
        f"{_html_table(['Case', 'Recall delta', 'Precision delta', 'False negative delta', 'False positive delta', 'Evidence delta'], case_rows)}"
    )
```

- [ ] **Step 4: 运行 HTML 测试和回归 runner 测试**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_write_html_report_includes_regression_summary tests/test_run_quality_regression.py -v
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/scripts/evaluate_ocr_compare_quality.py backend/tests/test_evaluate_ocr_compare_quality.py
git commit -m "feat: show quality regression in OCR report"
```

## Task 6: 中文使用文档

**Files:**
- Create: `docs/quality_regression_workflow.md`

- [ ] **Step 1: 写中文文档**

Create `docs/quality_regression_workflow.md`:

```markdown
# Phase 5 质量回归体系使用说明

## 目的

质量回归体系用于回答：本次代码、提示词、OCR、模型路由或配置变更，相比 baseline 是否让合同差异比对质量变好或变差。

它不直接提升比对算法精度，而是为后续精度优化提供可重复、可追踪、可门禁的质量判断。

## 前置条件

1. 已经有 gold case 目录。
2. `expected.json` 中需要计入质量指标的差异已经人工标注为 `APPROVED`。
3. `DRAFT` 和 `REJECTED` 不会计入 gold 指标。

## 建立 baseline

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/baseline-v0.0.2 \
  --write-baseline .ocr-compare-quality/baselines/v0.0.2.json
```

baseline 文件建议只保存经过确认的公开 fixture 结果。包含真实合同内容的 baseline 不应提交到版本库。

## 运行本地回归

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --baseline .ocr-compare-quality/baselines/v0.0.2.json \
  --output-dir .ocr-compare-quality/runs/local-check \
  --fail-on-regression
```

如果门禁失败且传入了 `--fail-on-regression`，命令会以退出码 `1` 结束，方便后续接入 CI。

## 输出文件

默认输出：

```text
<output-dir>/quality.json
<output-dir>/baseline_comparison.json
<output-dir>/run_summary.json
<output-dir>/html/index.html
```

`quality.json` 是当前完整质量评估结果。

`baseline_comparison.json` 是当前结果和 baseline 的差值。

`run_summary.json` 是本次运行摘要，包含 run id、git branch、git commit、dirty 状态、门禁结果和输出路径。

HTML 报告适合人工查看，重点看：

- Quality regression
- Failed gates
- Aggregate delta
- Case regression ranking
- Missed expected diffs
- Unexpected actual diffs
- Evidence drift

## 阈值配置

可以通过 JSON 文件覆盖默认阈值：

```json
{
  "min_recall": 0.95,
  "min_precision": 0.9,
  "min_evidence_hit_rate": 0.9,
  "max_false_positive_increase": 1,
  "max_false_negative_increase": 0,
  "max_recall_drop": 0.02
}
```

运行：

```bash
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --baseline .ocr-compare-quality/baselines/v0.0.2.json \
  --thresholds .ocr-compare-quality/quality_thresholds.json \
  --output-dir .ocr-compare-quality/runs/local-check \
  --fail-on-regression
```

## gold case 数量较少时如何解读

当 approved expected diff 很少时，precision、recall、evidence_hit_rate 会非常敏感。一个误报或漏检就可能导致大幅波动。

这种情况下不要只看 aggregate 指标，还要查看 case 级明细：

- 是哪个 case 退化？
- 是漏检、误报还是证据漂移？
- 是否因为 gold case 标注还不完整？
- 是否只是 DRAFT 尚未转为 APPROVED？

## CI 接入方式

后续 CI 可以直接调用：

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --baseline .ocr-compare-quality/baselines/v0.0.2.json \
  --output-dir .ocr-compare-quality/runs/ci \
  --fail-on-regression
```

CI 需要归档这些产物：

- `.ocr-compare-quality/runs/ci/run_summary.json`
- `.ocr-compare-quality/runs/ci/quality.json`
- `.ocr-compare-quality/runs/ci/baseline_comparison.json`
- `.ocr-compare-quality/runs/ci/html/index.html`

## 敏感数据注意事项

不要提交真实合同、客户数据、从真实任务导出的 `actual.json`、包含敏感片段的 `expected.json`、或者基于真实合同生成的质量报告。

公开 fixture、脱敏 gold case 和脱敏 baseline 才适合进入版本库。
```

- [ ] **Step 2: 提交文档**

```bash
git add docs/quality_regression_workflow.md
git commit -m "docs: add quality regression workflow"
```

## Task 7: 全量验证与收尾

**Files:**
- No source changes unless verification exposes issues.

- [ ] **Step 1: 运行新增测试**

Run:

```bash
cd backend
python -m pytest tests/test_run_quality_regression.py tests/test_evaluate_ocr_compare_quality.py -v
```

Expected: PASS。

- [ ] **Step 2: 运行语法检查**

Run:

```bash
cd backend
python -m compileall app tests scripts
```

Expected: PASS，无 Python syntax error。

- [ ] **Step 3: 运行 lint**

Run:

```bash
cd backend
python -m ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 4: 运行 backend 全量测试**

Run:

```bash
cd backend
python -m pytest
```

Expected: PASS。

- [ ] **Step 5: 运行质量回归 smoke**

Run:

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/phase5-smoke \
  --run-id phase5-smoke
```

Expected:

- Exit code `0`
- 生成 `.ocr-compare-quality/runs/phase5-smoke/quality.json`
- 生成 `.ocr-compare-quality/runs/phase5-smoke/baseline_comparison.json`
- 生成 `.ocr-compare-quality/runs/phase5-smoke/run_summary.json`
- 生成 `.ocr-compare-quality/runs/phase5-smoke/html/index.html`

- [ ] **Step 6: 运行 frontend 验证，确认未破坏前端**

Run:

```bash
cd frontend
npm test
```

Expected: PASS。

Run:

```bash
cd frontend
npm run build
```

Expected: PASS。

- [ ] **Step 7: 检查工作区，只提交 Phase 5 相关文件**

Run:

```bash
git status --short
```

Expected:

- Phase 5 源码、测试、文档已经提交。
- `.ocr-compare-quality/` 等本地运行产物保持未跟踪，不纳入提交。
- 不处理用户已有的 `frontend/src/picture/favicon.ico` 修改，除非用户明确要求。

- [ ] **Step 8: 最终审查**

Use `superpowers:verification-before-completion` before claiming completion. If using subagents, dispatch a final code reviewer for the whole Phase 5 implementation.

Expected final response includes:

- 实现摘要
- 验证命令和结果
- 关键使用命令
- 当前分支和未跟踪/未提交文件说明
