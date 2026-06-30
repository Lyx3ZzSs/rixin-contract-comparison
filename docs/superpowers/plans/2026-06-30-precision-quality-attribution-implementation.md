# Precision Phase 2A Quality Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline quality attribution report that explains false positives, false negatives, low-confidence alignments, suspicious match methods, and gold-case coverage risks from existing quality regression output and debug artifacts.

**Architecture:** Add a focused CLI script `backend/scripts/analyze_quality_attribution.py` that reads an existing regression run directory, combines `quality.json` with optional per-case debug artifacts, and writes `quality_attribution.json`. The first version is read-only and does not change matcher, diff, API, or quality gate behavior. A Chinese workflow document explains when to run it and how to use the output before Phase 2B tuning.

**Tech Stack:** Python 3.12, stdlib `argparse`/`json`/`pathlib`/`collections.Counter`, pytest, existing quality regression artifacts.

---

## File Structure

- Create: `backend/scripts/analyze_quality_attribution.py`
  - Responsibility: load a regression run directory, parse `quality.json`, read optional debug artifacts, compute attribution tags and suspicious match summaries, write `quality_attribution.json`, and expose a CLI.
- Create: `backend/tests/test_quality_attribution.py`
  - Responsibility: focused tests for aggregation, suspicious match extraction, malformed artifact handling, missing artifact warnings, writer behavior, and CLI smoke.
- Create: `docs/precision_quality_attribution_workflow.md`
  - Responsibility: Chinese user-facing instructions for running Phase 2A attribution and interpreting `quality_attribution.json`.

No production service, API route, frontend, matcher, diff, or OCR code should change in this phase.

## Task 1: Attribution Core Data Extraction

**Files:**
- Create: `backend/scripts/analyze_quality_attribution.py`
- Create: `backend/tests/test_quality_attribution.py`

- [ ] **Step 1: Write failing tests for core attribution from quality and debug artifacts**

Create `backend/tests/test_quality_attribution.py` with:

```python
from __future__ import annotations

import json
from pathlib import Path

from scripts.analyze_quality_attribution import analyze_run_dir


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_quality_report(run_dir: Path) -> None:
    _write_json(
        run_dir / "quality.json",
        {
            "case_count": 1,
            "aggregate": {
                "false_positive_count": 1,
                "false_negative_count": 0,
                "precision": 0.5,
                "recall": 1.0,
                "evidence_hit_rate": 1.0,
            },
            "cases": [
                {
                    "case_id": "case_a",
                    "status": "PASSED",
                    "false_positive_count": 1,
                    "false_negative_count": 0,
                    "unexpected_actual_diffs": [{"diff_id": "D_UNEXPECTED"}],
                    "missed_expected_diffs": [],
                    "annotation_summary": {"APPROVED": 1, "DRAFT": 0, "REJECTED": 0},
                }
            ],
        },
    )


def test_analyze_run_dir_combines_quality_and_alignment_debug_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_quality_report(run_dir)
    _write_json(
        run_dir / "debug" / "case_a" / "match_matrix_summary.json",
        {
            "method_counts": {"same_clause_key_weighted": 1},
            "low_confidence_alignment_count": 1,
            "alignment_risk_flag_counts": {
                "CRITICAL_TOKEN_MISMATCH": 1,
                "POSSIBLE_CLAUSE_MISALIGNMENT": 1,
            },
        },
    )
    _write_json(
        run_dir / "debug" / "case_a" / "clause_matches.json",
        [
            {
                "original_clause_id": "O001",
                "compare_clause_id": "N001",
                "match_method": "same_clause_key_weighted",
                "match_confidence": "LOW",
                "score_details": {
                    "body_length_coverage": 0.42,
                    "alignment": {
                        "body_similarity": 0.44,
                        "critical_token_overlap": 0.0,
                        "risk_flags": [
                            "CRITICAL_TOKEN_MISMATCH",
                            "POSSIBLE_CLAUSE_MISALIGNMENT",
                        ],
                    },
                },
            }
        ],
    )

    report = analyze_run_dir(run_dir)

    assert report["case_count"] == 1
    assert report["aggregate"]["false_positive_count"] == 1
    assert report["aggregate"]["low_confidence_alignment_count"] == 1
    assert report["aggregate"]["alignment_risk_flag_counts"] == {
        "CRITICAL_TOKEN_MISMATCH": 1,
        "POSSIBLE_CLAUSE_MISALIGNMENT": 1,
    }
    assert report["aggregate"]["match_method_counts"] == {"same_clause_key_weighted": 1}
    assert report["aggregate"]["attribution_counts"]["LOW_CONFIDENCE_ALIGNMENT"] == 1
    assert report["aggregate"]["attribution_counts"]["KEY_TOKEN_CONFLICT"] == 1
    assert report["aggregate"]["attribution_counts"]["SAME_KEY_LOW_BODY_COVERAGE"] == 1
    assert report["aggregate"]["attribution_counts"]["UNEXPECTED_ACTUAL_NEEDS_LABEL"] == 1
    case = report["cases"][0]
    assert case["case_id"] == "case_a"
    assert case["quality_status"] == "PASSED"
    assert case["false_positive_count"] == 1
    assert case["false_negative_count"] == 0
    assert case["low_confidence_alignment_count"] == 1
    assert case["alignment_risk_flag_counts"]["CRITICAL_TOKEN_MISMATCH"] == 1
    assert case["suspicious_matches"][0]["original_clause_id"] == "O001"
    assert case["suspicious_matches"][0]["risk_flags"] == [
        "CRITICAL_TOKEN_MISMATCH",
        "POSSIBLE_CLAUSE_MISALIGNMENT",
    ]
    assert "LOW_CONFIDENCE_ALIGNMENT" in case["attribution_tags"]
    assert case["warnings"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_quality_attribution.py::test_analyze_run_dir_combines_quality_and_alignment_debug_artifacts -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.analyze_quality_attribution'`.

- [ ] **Step 3: Implement minimal attribution core**

Create `backend/scripts/analyze_quality_attribution.py`:

```python
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SUSPICIOUS_MATCH_METHODS = {
    "same_clause_key_weighted",
    "same_clause_no_low_similarity",
    "body_weighted_similarity",
    "partial_body_similarity",
    "section_mismatch_blocked",
}


def analyze_run_dir(run_dir: Path) -> dict[str, Any]:
    quality_path = run_dir / "quality.json"
    if not run_dir.exists():
        raise FileNotFoundError(f"run directory does not exist: {run_dir}")
    if not quality_path.exists():
        raise FileNotFoundError(f"quality report does not exist: {quality_path}")

    quality = _read_json_object(quality_path)
    cases = [_analyze_case(run_dir, case) for case in _list_cases(quality)]
    return {
        "run_id": _run_id(run_dir),
        "case_count": len(cases),
        "aggregate": _aggregate(quality, cases),
        "cases": cases,
    }


def _list_cases(quality: dict[str, Any]) -> list[dict[str, Any]]:
    cases = quality.get("cases", [])
    return cases if isinstance(cases, list) else []


def _run_id(run_dir: Path) -> str:
    summary_path = run_dir / "run_summary.json"
    if summary_path.exists():
        summary = _read_json_object(summary_path)
        run_id = summary.get("run_id")
        if isinstance(run_id, str) and run_id:
            return run_id
    return run_dir.name


def _analyze_case(run_dir: Path, case: dict[str, Any]) -> dict[str, Any]:
    case_id = str(case.get("case_id", ""))
    warnings: list[str] = []
    summary = _read_optional_debug_json(run_dir, case_id, "match_matrix_summary.json", warnings)
    matches = _read_optional_debug_json(run_dir, case_id, "clause_matches.json", warnings)

    summary_obj = summary if isinstance(summary, dict) else {}
    match_items = matches if isinstance(matches, list) else []
    risk_counts = _risk_counts(summary_obj, match_items)
    method_counts = _method_counts(summary_obj, match_items)
    suspicious_matches = _suspicious_matches(match_items)
    tags = _attribution_tags(case, risk_counts, method_counts, suspicious_matches, summary_obj)

    return {
        "case_id": case_id,
        "quality_status": case.get("status", ""),
        "false_positive_count": _int_value(case.get("false_positive_count")),
        "false_negative_count": _int_value(case.get("false_negative_count")),
        "low_confidence_alignment_count": _low_confidence_alignment_count(summary_obj, match_items),
        "alignment_risk_flag_counts": dict(risk_counts),
        "match_method_counts": dict(method_counts),
        "suspicious_matches": suspicious_matches,
        "attribution_tags": tags,
        "warnings": warnings,
    }


def _read_optional_debug_json(
    run_dir: Path,
    case_id: str,
    filename: str,
    warnings: list[str],
) -> Any:
    for root in _debug_roots(run_dir, case_id):
        path = root / filename
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            warnings.append(f"{path}: invalid json: {error.msg}")
            return None
    warnings.append(f"{case_id}: missing debug artifact {filename}")
    return None


def _debug_roots(run_dir: Path, case_id: str) -> list[Path]:
    return [
        run_dir / "debug" / case_id,
        run_dir / case_id / "debug",
        run_dir / "cases" / case_id / "debug",
    ]


def _risk_counts(summary: dict[str, Any], matches: list[Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    summary_counts = summary.get("alignment_risk_flag_counts")
    if isinstance(summary_counts, dict):
        for flag, count in summary_counts.items():
            if isinstance(flag, str) and isinstance(count, int | float):
                counts[flag] += int(count)
        if counts:
            return counts
    for match in matches:
        counts.update(_risk_flags_from_match(match))
    return counts


def _method_counts(summary: dict[str, Any], matches: list[Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    summary_counts = summary.get("method_counts")
    if isinstance(summary_counts, dict):
        for method, count in summary_counts.items():
            if isinstance(method, str) and isinstance(count, int | float):
                counts[method] += int(count)
        if counts:
            return counts
    for match in matches:
        if isinstance(match, dict) and isinstance(match.get("match_method"), str):
            counts[str(match["match_method"])] += 1
    return counts


def _low_confidence_alignment_count(summary: dict[str, Any], matches: list[Any]) -> int:
    value = summary.get("low_confidence_alignment_count")
    if isinstance(value, int | float):
        return int(value)
    return sum(1 for match in matches if _risk_flags_from_match(match))


def _suspicious_matches(matches: list[Any]) -> list[dict[str, Any]]:
    result = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        method = str(match.get("match_method", ""))
        confidence = str(match.get("match_confidence", ""))
        details = match.get("score_details", {})
        alignment = details.get("alignment", {}) if isinstance(details, dict) else {}
        risk_flags = _risk_flags_from_match(match)
        body_similarity = alignment.get("body_similarity") if isinstance(alignment, dict) else None
        token_overlap = alignment.get("critical_token_overlap") if isinstance(alignment, dict) else None
        body_length_coverage = details.get("body_length_coverage") if isinstance(details, dict) else None
        if not _is_suspicious(method, confidence, risk_flags, body_length_coverage):
            continue
        result.append(
            {
                "original_clause_id": match.get("original_clause_id"),
                "compare_clause_id": match.get("compare_clause_id"),
                "match_method": method,
                "match_confidence": confidence,
                "risk_flags": risk_flags,
                "body_similarity": body_similarity,
                "critical_token_overlap": token_overlap,
                "body_length_coverage": body_length_coverage,
            }
        )
    return result[:50]


def _is_suspicious(
    method: str,
    confidence: str,
    risk_flags: list[str],
    body_length_coverage: Any,
) -> bool:
    if confidence == "LOW" or risk_flags:
        return True
    if method in SUSPICIOUS_MATCH_METHODS:
        return True
    if method == "same_clause_key_weighted" and isinstance(body_length_coverage, int | float) and body_length_coverage < 0.70:
        return True
    return False


def _risk_flags_from_match(match: Any) -> list[str]:
    if not isinstance(match, dict):
        return []
    details = match.get("score_details", {})
    alignment = details.get("alignment", {}) if isinstance(details, dict) else {}
    risk_flags = alignment.get("risk_flags") if isinstance(alignment, dict) else None
    if not isinstance(risk_flags, (list, tuple, set)):
        return []
    return [flag for flag in risk_flags if isinstance(flag, str) and flag]


def _attribution_tags(
    case: dict[str, Any],
    risk_counts: Counter[str],
    method_counts: Counter[str],
    suspicious_matches: list[dict[str, Any]],
    summary: dict[str, Any],
) -> list[str]:
    tags: set[str] = set()
    if _low_confidence_alignment_count(summary, suspicious_matches) > 0 or risk_counts:
        tags.add("LOW_CONFIDENCE_ALIGNMENT")
    if "CRITICAL_TOKEN_MISMATCH" in risk_counts:
        tags.add("KEY_TOKEN_CONFLICT")
    if any(flag in risk_counts for flag in ("TEXT_MATCH_NUMBER_MISMATCH", "TITLE_MATCH_TEXT_MISMATCH")):
        tags.add("POSSIBLE_CLAUSE_MISALIGNMENT")
    if any(method in method_counts for method in SUSPICIOUS_MATCH_METHODS):
        tags.add("SUSPICIOUS_MATCH_METHOD")
    if any(match.get("match_method") == "same_clause_key_weighted" and _number_value(match.get("body_length_coverage")) < 0.70 for match in suspicious_matches):
        tags.add("SAME_KEY_LOW_BODY_COVERAGE")
    if _int_value(case.get("false_positive_count")) > 0:
        tags.add("UNEXPECTED_ACTUAL_NEEDS_LABEL")
    if _int_value(case.get("false_negative_count")) > 0:
        tags.add("KEY_TOKEN_RECALL_RISK")
    annotation_summary = case.get("annotation_summary", {})
    if isinstance(annotation_summary, dict) and _int_value(annotation_summary.get("APPROVED")) <= 1:
        tags.add("GOLD_CASE_NEEDS_REVIEW")
    return sorted(tags)


def _aggregate(quality: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate = quality.get("aggregate", {})
    result = {
        "false_positive_count": _int_value(aggregate.get("false_positive_count") if isinstance(aggregate, dict) else None),
        "false_negative_count": _int_value(aggregate.get("false_negative_count") if isinstance(aggregate, dict) else None),
        "low_confidence_alignment_count": sum(case["low_confidence_alignment_count"] for case in cases),
        "alignment_risk_flag_counts": dict(_sum_counters(case["alignment_risk_flag_counts"] for case in cases)),
        "match_method_counts": dict(_sum_counters(case["match_method_counts"] for case in cases)),
        "attribution_counts": dict(Counter(tag for case in cases for tag in case["attribution_tags"])),
    }
    return result


def _sum_counters(items: Any) -> Counter[str]:
    counts: Counter[str] = Counter()
    for item in items:
        if isinstance(item, dict):
            for key, value in item.items():
                if isinstance(key, str) and isinstance(value, int | float):
                    counts[key] += int(value)
    return counts


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _int_value(value: Any) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _number_value(value: Any) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 1.0


def write_attribution_report(run_dir: Path, report: dict[str, Any]) -> Path:
    path = run_dir / "quality_attribution.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
cd backend
python -m pytest tests/test_quality_attribution.py::test_analyze_run_dir_combines_quality_and_alignment_debug_artifacts -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/analyze_quality_attribution.py backend/tests/test_quality_attribution.py
git commit -m "feat: add quality attribution analyzer"
```

## Task 2: Robust Missing and Malformed Artifact Handling

**Files:**
- Modify: `backend/scripts/analyze_quality_attribution.py`
- Modify: `backend/tests/test_quality_attribution.py`

- [ ] **Step 1: Add failing tests for missing and malformed debug artifacts**

Append to `backend/tests/test_quality_attribution.py`:

```python
def test_analyze_run_dir_records_warnings_for_missing_debug_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_quality_report(run_dir)

    report = analyze_run_dir(run_dir)

    case = report["cases"][0]
    assert case["low_confidence_alignment_count"] == 0
    assert case["alignment_risk_flag_counts"] == {}
    assert "case_a: missing debug artifact match_matrix_summary.json" in case["warnings"]
    assert "case_a: missing debug artifact clause_matches.json" in case["warnings"]


def test_analyze_run_dir_ignores_malformed_alignment_shapes(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    _write_quality_report(run_dir)
    _write_json(
        run_dir / "debug" / "case_a" / "match_matrix_summary.json",
        {
            "method_counts": {"body_weighted_similarity": 1},
            "alignment_risk_flag_counts": "not-a-dict",
            "low_confidence_alignment_count": "not-a-number",
        },
    )
    _write_json(
        run_dir / "debug" / "case_a" / "clause_matches.json",
        [
            {"match_method": "body_weighted_similarity", "score_details": {"alignment": "bad"}},
            {"match_method": "same_clause_key_weighted", "score_details": {"alignment": {"risk_flags": "bad"}}},
        ],
    )

    report = analyze_run_dir(run_dir)

    case = report["cases"][0]
    assert case["alignment_risk_flag_counts"] == {}
    assert case["match_method_counts"] == {"body_weighted_similarity": 1}
    assert "SUSPICIOUS_MATCH_METHOD" in case["attribution_tags"]
```

- [ ] **Step 2: Run tests to verify current behavior**

Run:

```bash
cd backend
python -m pytest tests/test_quality_attribution.py::test_analyze_run_dir_records_warnings_for_missing_debug_artifacts tests/test_quality_attribution.py::test_analyze_run_dir_ignores_malformed_alignment_shapes -v
```

Expected: PASS if Task 1 implementation already handles these cases. If it fails, use the next step to fix the exact failure.

- [ ] **Step 3: Tighten malformed input handling if needed**

If the malformed test fails, update these functions in `backend/scripts/analyze_quality_attribution.py` exactly as follows:

```python
def _risk_counts(summary: dict[str, Any], matches: list[Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    summary_counts = summary.get("alignment_risk_flag_counts")
    if isinstance(summary_counts, dict):
        for flag, count in summary_counts.items():
            if isinstance(flag, str) and isinstance(count, int | float) and not isinstance(count, bool):
                counts[flag] += int(count)
        if counts:
            return counts
    for match in matches:
        counts.update(_risk_flags_from_match(match))
    return counts


def _method_counts(summary: dict[str, Any], matches: list[Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    summary_counts = summary.get("method_counts")
    if isinstance(summary_counts, dict):
        for method, count in summary_counts.items():
            if isinstance(method, str) and isinstance(count, int | float) and not isinstance(count, bool):
                counts[method] += int(count)
        if counts:
            return counts
    for match in matches:
        if isinstance(match, dict) and isinstance(match.get("match_method"), str):
            counts[str(match["match_method"])] += 1
    return counts
```

- [ ] **Step 4: Run full attribution tests**

Run:

```bash
cd backend
python -m pytest tests/test_quality_attribution.py -v
```

Expected: all tests in `test_quality_attribution.py` PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/analyze_quality_attribution.py backend/tests/test_quality_attribution.py
git commit -m "test: cover quality attribution artifact edge cases"
```

## Task 3: CLI Writer and Exit Behavior

**Files:**
- Modify: `backend/scripts/analyze_quality_attribution.py`
- Modify: `backend/tests/test_quality_attribution.py`

- [ ] **Step 1: Write failing tests for writer and CLI**

Append to `backend/tests/test_quality_attribution.py`:

```python
def test_write_attribution_report_writes_quality_attribution_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    path = write_attribution_report(run_dir, {"run_id": "run", "cases": []})

    assert path == run_dir / "quality_attribution.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"run_id": "run", "cases": []}


def test_main_writes_report_and_prints_summary(tmp_path: Path, capsys) -> None:
    from scripts import analyze_quality_attribution

    run_dir = tmp_path / "run"
    _write_quality_report(run_dir)

    exit_code = analyze_quality_attribution.main(["--run-dir", str(run_dir)])

    assert exit_code == 0
    payload = json.loads((run_dir / "quality_attribution.json").read_text(encoding="utf-8"))
    assert payload["case_count"] == 1
    captured = json.loads(capsys.readouterr().out)
    assert captured["output_path"] == str(run_dir / "quality_attribution.json")
    assert captured["case_count"] == 1


def test_main_returns_error_for_missing_run_dir(tmp_path: Path, capsys) -> None:
    from scripts import analyze_quality_attribution

    exit_code = analyze_quality_attribution.main(["--run-dir", str(tmp_path / "missing")])

    assert exit_code == 1
    captured = json.loads(capsys.readouterr().out)
    assert "run directory does not exist" in captured["error"]
```

Also update the import block at the top of `backend/tests/test_quality_attribution.py`:

```python
from scripts.analyze_quality_attribution import analyze_run_dir, write_attribution_report
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend
python -m pytest tests/test_quality_attribution.py::test_main_writes_report_and_prints_summary tests/test_quality_attribution.py::test_main_returns_error_for_missing_run_dir -v
```

Expected: FAIL with `AttributeError: module 'scripts.analyze_quality_attribution' has no attribute 'main'`.

- [ ] **Step 3: Implement CLI**

Append this to `backend/scripts/analyze_quality_attribution.py`:

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Analyze quality attribution from an OCR compare regression run directory."
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        report = analyze_run_dir(args.run_dir)
        output_path = write_attribution_report(args.run_dir, report)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "FAILED", "error": str(error)}, ensure_ascii=False, indent=2))
        return 1

    print(
        json.dumps(
            {
                "status": "PASSED",
                "run_id": report["run_id"],
                "case_count": report["case_count"],
                "output_path": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
cd backend
python -m pytest tests/test_quality_attribution.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Run CLI smoke against an existing regression run**

First create a small regression run if needed:

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/precision-p2a-plan-smoke \
  --run-id precision-p2a-plan-smoke \
  --fail-on-regression
```

Then run:

```bash
python scripts/analyze_quality_attribution.py \
  --run-dir .ocr-compare-quality/runs/precision-p2a-plan-smoke
```

Expected: exit 0 and output JSON with `"status": "PASSED"` and `"output_path": ".ocr-compare-quality/runs/precision-p2a-plan-smoke/quality_attribution.json"`.

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/analyze_quality_attribution.py backend/tests/test_quality_attribution.py
git commit -m "feat: add quality attribution CLI"
```

## Task 4: Attribution Workflow Documentation

**Files:**
- Create: `docs/precision_quality_attribution_workflow.md`

- [ ] **Step 1: Create Chinese workflow documentation**

Create `docs/precision_quality_attribution_workflow.md`:

```markdown
# Precision Phase 2A 质量归因使用说明

## 目的

质量归因用于回答：当前合同差异比对的误报、漏报和低置信条款对齐，主要来自 matcher 错配、关键字段冲突、diff 抑制，还是 gold case 标注覆盖不足。

本阶段只做离线分析，不改变线上匹配、diff 结论、API 返回结构或质量门禁。

## 前置条件

先运行质量回归：

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/precision-p2a \
  --run-id precision-p2a \
  --fail-on-regression
```

## 生成归因报告

```bash
cd backend
python scripts/analyze_quality_attribution.py \
  --run-dir .ocr-compare-quality/runs/precision-p2a
```

输出文件：

```text
.ocr-compare-quality/runs/precision-p2a/quality_attribution.json
```

## 重点字段

- `aggregate.false_positive_count`：当前回归中的误报数量。
- `aggregate.false_negative_count`：当前回归中的漏报数量。
- `aggregate.low_confidence_alignment_count`：存在 alignment 风险的条款匹配数量。
- `aggregate.alignment_risk_flag_counts`：各类 alignment risk flag 的分布。
- `aggregate.match_method_counts`：匹配方法分布。
- `aggregate.attribution_counts`：归因标签分布。
- `cases[].suspicious_matches`：可疑条款匹配样本。
- `cases[].warnings`：缺失或异常 debug artifact。

## 常见归因标签

- `LOW_CONFIDENCE_ALIGNMENT`：存在低置信条款对齐。
- `KEY_TOKEN_CONFLICT`：金额、日期、期限、主体、数量或税率等关键 token 冲突。
- `SUSPICIOUS_MATCH_METHOD`：命中了需要关注的匹配方法。
- `SAME_KEY_LOW_BODY_COVERAGE`：同 clause key 但正文覆盖不足。
- `UNEXPECTED_ACTUAL_NEEDS_LABEL`：存在未被 expected/gold case 覆盖的实际 diff。
- `KEY_TOKEN_RECALL_RISK`：存在漏报风险，需要检查关键字段差异是否被压制。
- `GOLD_CASE_NEEDS_REVIEW`：gold case 覆盖偏少或需要人工复核。

## 如何用于下一阶段

进入 Phase 2B 前，先查看：

1. `alignment_risk_flag_counts` 中最高频的风险。
2. `suspicious_matches` 中是否集中出现某个 `match_method`。
3. 误报是否主要来自 `UNEXPECTED_ACTUAL_NEEDS_LABEL`，如果是，应优先补 gold case。
4. 漏报是否伴随 `KEY_TOKEN_RECALL_RISK`，如果是，应优先做关键字段差异保护。

只有当归因显示 matcher 错配是主要问题时，才进入 matcher 策略调优。
```

- [ ] **Step 2: Verify documentation file is readable**

Run:

```bash
sed -n '1,220p' docs/precision_quality_attribution_workflow.md
```

Expected: command prints the Chinese workflow without malformed code fences.

- [ ] **Step 3: Commit**

```bash
git add docs/precision_quality_attribution_workflow.md
git commit -m "docs: add quality attribution workflow"
```

## Task 5: Final Verification and Handoff

**Files:**
- No code changes expected.

- [ ] **Step 1: Run focused attribution tests**

Run:

```bash
cd backend
python -m pytest tests/test_quality_attribution.py -v
```

Expected: all attribution tests PASS.

- [ ] **Step 2: Run related regression tests**

Run:

```bash
cd backend
python -m pytest tests/test_run_quality_regression.py tests/test_evaluate_ocr_compare_quality.py -v
```

Expected: all related quality regression tests PASS.

- [ ] **Step 3: Run backend syntax and lint checks**

Run:

```bash
cd backend
python -m compileall app tests scripts
python -m ruff check .
```

Expected: both commands exit 0.

- [ ] **Step 4: Run quality regression and attribution smoke**

Run:

```bash
cd backend
python scripts/run_quality_regression.py \
  --case-root tests/fixtures/ocr_compare_cases \
  --output-dir .ocr-compare-quality/runs/precision-p2a-final \
  --run-id precision-p2a-final \
  --fail-on-regression
python scripts/analyze_quality_attribution.py \
  --run-dir .ocr-compare-quality/runs/precision-p2a-final
```

Expected:

- `run_quality_regression.py` exits 0 with `"status": "PASSED"` and `"failed_gates": []`.
- `analyze_quality_attribution.py` exits 0 with `"status": "PASSED"`.
- `.ocr-compare-quality/runs/precision-p2a-final/quality_attribution.json` exists.

- [ ] **Step 5: Run full backend tests**

Run:

```bash
cd backend
python -m pytest
```

Expected: full backend test suite PASS.

- [ ] **Step 6: Check git status**

Run:

```bash
git status --short
```

Expected: only known unrelated local files remain, such as `frontend/src/picture/favicon.ico`, `.agents/`, `.ocr-compare-quality/`, `picture/`, `skills-lock.json`, and `storage/`.

- [ ] **Step 7: Final review**

Request final code review with this checklist:

```text
Please review Precision Phase 2A quality attribution.

Scope:
- backend/scripts/analyze_quality_attribution.py
- backend/tests/test_quality_attribution.py
- docs/precision_quality_attribution_workflow.md

Check:
- Does not change matcher, diff, API, OCR, or quality gate behavior.
- Correctly reads quality.json and optional debug artifacts.
- Handles missing/malformed debug artifacts without crashing.
- Produces stable quality_attribution.json.
- CLI exit behavior is deterministic.
- Tests and docs are sufficient.
```

Expected: reviewer returns PASS or lists concrete fixes.
