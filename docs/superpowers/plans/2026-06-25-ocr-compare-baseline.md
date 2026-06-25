# OCR Compare Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Phase 1 of the OCR comparison accuracy track: a scanned-contract golden set format, evaluator, JSON/HTML reports, and smoke regression gate without changing default comparison algorithms.

**Architecture:** Add a dedicated evaluator script under `backend/scripts/` that runs against case directories and consumes either saved `actual.json` task payloads or real `original.pdf` plus `compare.pdf` files through `CompareService`. Keep metric parsing and report generation local to the script for Phase 1, matching existing evaluator style. Add deterministic fixtures and tests under `backend/tests/` so the evaluator can be developed without external OCR services.

**Tech Stack:** Python 3, Pydantic task payloads via `CompareTask`, FastAPI service layer through `CompareService`, pytest, JSON fixtures, static HTML report output.

---

## Scope

This plan implements only Phase 1 from the approved design:

- Golden set directory format for OCR comparison cases.
- Baseline evaluator script.
- JSON and HTML output.
- Smoke threshold gate.
- Deterministic tests using saved `actual.json`.

This plan does not implement runtime `ocr_quality_profile`, OCR retry, low-quality page repair, frontend indicators, or report warnings. Those belong to later phase plans after the baseline metrics exist.

## File Structure

- Create `backend/scripts/evaluate_ocr_compare_quality.py`
  - Owns case discovery, annotation parsing, actual task loading/running, metric calculation, threshold checking, JSON output, HTML output, and CLI.
  - Follows the style of `evaluate_compare_quality.py` and `evaluate_layout_quality.py`.
- Create `backend/tests/test_evaluate_ocr_compare_quality.py`
  - Unit and integration tests for the evaluator using local fixtures.
- Create `backend/tests/fixtures/ocr_compare_cases/README.md`
  - Documents the golden set format and sensitive PDF handling.
- Create `backend/tests/fixtures/ocr_compare_cases/simple_scanned/expected.json`
  - Deterministic expected annotation fixture.
- Create `backend/tests/fixtures/ocr_compare_cases/simple_scanned/actual.json`
  - Saved `CompareTask` payload with OCR/layout warning signals and one matched diff.
- Create `backend/tests/fixtures/ocr_compare_cases/simple_scanned/README.md`
  - Human-readable case notes.
- Modify `docs/superpowers/specs/2026-06-25-ocr-compare-accuracy-design.md`
  - Add a short link to the Phase 1 plan after the plan is complete.

---

### Task 1: Add OCR Compare Fixture Format

**Files:**
- Create: `backend/tests/fixtures/ocr_compare_cases/README.md`
- Create: `backend/tests/fixtures/ocr_compare_cases/simple_scanned/README.md`
- Create: `backend/tests/fixtures/ocr_compare_cases/simple_scanned/expected.json`
- Create: `backend/tests/fixtures/ocr_compare_cases/simple_scanned/actual.json`
- Test: `backend/tests/test_evaluate_ocr_compare_quality.py`

- [ ] **Step 1: Write the failing fixture discovery test**

Create `backend/tests/test_evaluate_ocr_compare_quality.py` with this initial content:

```python
from pathlib import Path

from scripts.evaluate_ocr_compare_quality import discover_cases


def test_discover_cases_ignores_incomplete_directories() -> None:
    cases = discover_cases(Path("tests/fixtures/ocr_compare_cases"))

    assert [case.case_id for case in cases] == ["simple_scanned"]
    assert cases[0].case_dir == Path("tests/fixtures/ocr_compare_cases/simple_scanned")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_discover_cases_ignores_incomplete_directories -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.evaluate_ocr_compare_quality'`.

- [ ] **Step 3: Create fixture documentation**

Create `backend/tests/fixtures/ocr_compare_cases/README.md`:

```markdown
# OCR Compare Cases

Each child directory is one scanned contract comparison case.

Required files for committed deterministic tests:

- `expected.json`: human-reviewed expected output.
- `actual.json`: saved `CompareTask` payload used by tests that do not call OCR services.
- `README.md`: case notes and known difficulty tags.

Optional files for local or internal full runs:

- `original.pdf`
- `compare.pdf`

Sensitive real contracts must not be committed. For sensitive cases, commit only annotation files and load PDFs from an internal path when running the evaluator locally.
```

Create `backend/tests/fixtures/ocr_compare_cases/simple_scanned/README.md`:

```markdown
# simple_scanned

Deterministic OCR comparison smoke case.

Tags:

- scanned
- low_text_confidence
- payment_clause

The fixture uses `actual.json` so tests can run without external OCR services.
```

- [ ] **Step 4: Create expected annotation fixture**

Create `backend/tests/fixtures/ocr_compare_cases/simple_scanned/expected.json`:

```json
{
  "case_id": "simple_scanned",
  "tags": ["scanned", "low_text_confidence", "payment_clause"],
  "critical_fields": [
    {
      "field_id": "payment_days",
      "label": "付款期限",
      "expected_original": "30日",
      "expected_compare": "45日",
      "page_no": 1
    }
  ],
  "expected_diffs": [
    {
      "diff_type": "MODIFY",
      "source_type": "clause",
      "title_contains": "付款",
      "original_contains": "30日",
      "compare_contains": "45日",
      "expected_evidence": [
        {
          "side": "original",
          "page_no": 1,
          "bbox": { "x0": 100, "y0": 200, "x1": 180, "y1": 230 }
        },
        {
          "side": "compare",
          "page_no": 1,
          "bbox": { "x0": 100, "y0": 200, "x1": 180, "y1": 230 }
        }
      ]
    }
  ],
  "quality_expectations": {
    "max_task_failures": 0,
    "min_recall": 1.0,
    "max_false_positive_count": 0,
    "min_evidence_hit_rate": 1.0
  }
}
```

- [ ] **Step 5: Create saved actual task fixture**

Create `backend/tests/fixtures/ocr_compare_cases/simple_scanned/actual.json`:

```json
{
  "task_id": "EVAL_OCR_SIMPLE_SCANNED",
  "status": "COMPLETED",
  "stage": "已完成",
  "progress_percent": 100,
  "original_filename": "original.pdf",
  "compare_filename": "compare.pdf",
  "original_pdf_path": "original.pdf",
  "compare_pdf_path": "compare.pdf",
  "diff_count": 1,
  "high_risk_count": 0,
  "parse_warnings": ["第1页 OCR 置信度偏低，已进入质量摘要。"],
  "parse_warning_details": [
    {
      "code": "LOW_TEXT_CONFIDENCE",
      "message": "第1页 OCR 置信度偏低，已进入质量摘要。",
      "severity": "WARNING",
      "page_no": 1,
      "source": "ocr"
    }
  ],
  "debug_artifact_paths": {
    "layout_quality": "storage/tasks/EVAL_OCR_SIMPLE_SCANNED/debug/layout_quality.json"
  },
  "document_profiles": {},
  "diffs": [
    {
      "diff_id": "D001",
      "diff_type": "MODIFY",
      "original_clause_id": "O001",
      "compare_clause_id": "C001",
      "clause_no": "3.1",
      "title": "付款",
      "original_text": "买方应在验收后30日内付款。",
      "compare_text": "买方应在验收后45日内付款。",
      "original_snippet": "30日",
      "compare_snippet": "45日",
      "readable_change": "付款期限由30日调整为45日。",
      "source_type": "clause",
      "match_score": 92.5,
      "match_method": "same_clause_no_weighted",
      "review_flags": ["OCR_LOW_CONFIDENCE"],
      "quality_status": "NEEDS_REVIEW",
      "text_confidence": 0.72,
      "original_evidence": [
        {
          "page_no": 1,
          "bbox": { "x0": 102, "y0": 202, "x1": 178, "y1": 228 },
          "method": "char_exact",
          "text": "30日",
          "highlight_type": "MODIFY",
          "confidence": 0.82,
          "evidence_quality": "HIGH",
          "text_confidence": 0.72
        }
      ],
      "compare_evidence": [
        {
          "page_no": 1,
          "bbox": { "x0": 104, "y0": 203, "x1": 179, "y1": 229 },
          "method": "char_exact",
          "text": "45日",
          "highlight_type": "MODIFY",
          "confidence": 0.81,
          "evidence_quality": "HIGH",
          "text_confidence": 0.72
        }
      ]
    }
  ]
}
```

- [ ] **Step 6: Add minimal case discovery implementation**

Create `backend/scripts/evaluate_ocr_compare_quality.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OcrCompareCase:
    case_id: str
    case_dir: Path


def discover_cases(case_root: Path) -> list[OcrCompareCase]:
    return [
        OcrCompareCase(case_id=path.name, case_dir=path)
        for path in sorted(case_root.iterdir())
        if path.is_dir() and (path / "expected.json").exists() and _has_actual_source(path)
    ]


def _has_actual_source(case_dir: Path) -> bool:
    return (case_dir / "actual.json").exists() or (
        (case_dir / "original.pdf").exists() and (case_dir / "compare.pdf").exists()
    )
```

- [ ] **Step 7: Run test to verify it passes**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_discover_cases_ignores_incomplete_directories -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/scripts/evaluate_ocr_compare_quality.py backend/tests/test_evaluate_ocr_compare_quality.py backend/tests/fixtures/ocr_compare_cases
git commit -m "test: add OCR comparison golden fixture"
```

---

### Task 2: Parse Expected Cases And Actual Task Payloads

**Files:**
- Modify: `backend/scripts/evaluate_ocr_compare_quality.py`
- Modify: `backend/tests/test_evaluate_ocr_compare_quality.py`

- [ ] **Step 1: Write failing parser test**

Append to `backend/tests/test_evaluate_ocr_compare_quality.py`:

```python
from scripts.evaluate_ocr_compare_quality import load_case_inputs


def test_load_case_inputs_reads_expected_and_actual_task() -> None:
    case = discover_cases(Path("tests/fixtures/ocr_compare_cases"))[0]

    expected, task = load_case_inputs(case)

    assert expected["case_id"] == "simple_scanned"
    assert expected["expected_diffs"][0]["diff_type"] == "MODIFY"
    assert task.task_id == "EVAL_OCR_SIMPLE_SCANNED"
    assert task.status == "COMPLETED"
    assert task.diffs[0].review_flags == ["OCR_LOW_CONFIDENCE"]
```

- [ ] **Step 2: Run parser test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_load_case_inputs_reads_expected_and_actual_task -v
```

Expected: FAIL with `ImportError` or `AttributeError` for `load_case_inputs`.

- [ ] **Step 3: Implement parser and task runner fallback**

Replace `backend/scripts/evaluate_ocr_compare_quality.py` with:

```python
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import CompareTask  # noqa: E402
from app.services.compare_service import CompareService  # noqa: E402


@dataclass(frozen=True)
class OcrCompareCase:
    case_id: str
    case_dir: Path


def discover_cases(case_root: Path) -> list[OcrCompareCase]:
    return [
        OcrCompareCase(case_id=path.name, case_dir=path)
        for path in sorted(case_root.iterdir())
        if path.is_dir() and (path / "expected.json").exists() and _has_actual_source(path)
    ]


def load_case_inputs(case: OcrCompareCase) -> tuple[dict[str, Any], CompareTask]:
    expected = _read_json(case.case_dir / "expected.json")
    return expected, _load_or_run_actual(case)


def _load_or_run_actual(case: OcrCompareCase) -> CompareTask:
    actual_json = case.case_dir / "actual.json"
    if actual_json.exists():
        return CompareTask(**_read_json(actual_json))

    original_pdf = case.case_dir / "original.pdf"
    compare_pdf = case.case_dir / "compare.pdf"
    if not original_pdf.exists() or not compare_pdf.exists():
        raise FileNotFoundError(
            f"{case.case_dir} must contain actual.json or original.pdf plus compare.pdf"
        )
    return CompareService().compare(
        original_pdf,
        compare_pdf,
        task_id=f"EVAL_OCR_{case.case_id.upper()}",
        original_filename=original_pdf.name,
        compare_filename=compare_pdf.name,
    )


def _has_actual_source(case_dir: Path) -> bool:
    return (case_dir / "actual.json").exists() or (
        (case_dir / "original.pdf").exists() and (case_dir / "compare.pdf").exists()
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run parser test to verify it passes**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_load_case_inputs_reads_expected_and_actual_task -v
```

Expected: PASS.

- [ ] **Step 5: Run all evaluator tests so far**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/evaluate_ocr_compare_quality.py backend/tests/test_evaluate_ocr_compare_quality.py
git commit -m "feat: load OCR comparison evaluation cases"
```

---

### Task 3: Calculate Diff, Evidence, OCR Warning, And Stability Metrics

**Files:**
- Modify: `backend/scripts/evaluate_ocr_compare_quality.py`
- Modify: `backend/tests/test_evaluate_ocr_compare_quality.py`

- [ ] **Step 1: Write failing evaluation test**

Append to `backend/tests/test_evaluate_ocr_compare_quality.py`:

```python
from scripts.evaluate_ocr_compare_quality import evaluate_case, evaluate_case_root


def test_evaluate_case_reports_ocr_compare_metrics() -> None:
    case = discover_cases(Path("tests/fixtures/ocr_compare_cases"))[0]

    result = evaluate_case(case)

    assert result.case_id == "simple_scanned"
    assert result.status == "COMPLETED"
    assert result.expected_count == 1
    assert result.actual_count == 1
    assert result.true_positive_count == 1
    assert result.false_positive_count == 0
    assert result.false_negative_count == 0
    assert result.evidence_hit_count == 1
    assert result.low_confidence_count == 1
    assert result.ocr_warning_count == 1
    assert result.task_failure_count == 0
    assert result.rates()["recall"] == 1.0
    assert result.rates()["precision"] == 1.0
    assert result.rates()["evidence_hit_rate"] == 1.0
    assert result.rates()["low_confidence_ratio"] == 1.0


def test_evaluate_case_root_aggregates_metrics() -> None:
    report = evaluate_case_root(Path("tests/fixtures/ocr_compare_cases"))

    assert report["case_count"] == 1
    assert report["aggregate"]["expected_count"] == 1
    assert report["aggregate"]["true_positive_count"] == 1
    assert report["aggregate"]["false_positive_count"] == 0
    assert report["aggregate"]["false_negative_count"] == 0
    assert report["aggregate"]["ocr_warning_count"] == 1
    assert report["aggregate"]["task_failure_count"] == 0
    assert report["aggregate"]["recall"] == 1.0
```

- [ ] **Step 2: Run evaluation tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_reports_ocr_compare_metrics tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_root_aggregates_metrics -v
```

Expected: FAIL with missing `evaluate_case` and `evaluate_case_root`.

- [ ] **Step 3: Implement result model, matching, and rates**

Append this code to `backend/scripts/evaluate_ocr_compare_quality.py` after `_read_json`:

```python

@dataclass
class OcrCompareCaseResult:
    case_id: str
    status: str
    expected_count: int
    actual_count: int
    true_positive_count: int
    false_positive_count: int
    false_negative_count: int
    evidence_hit_count: int
    low_confidence_count: int
    ocr_warning_count: int
    task_failure_count: int
    issues: list[str]

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__ | self.rates()

    def rates(self) -> dict[str, float]:
        precision_denominator = self.true_positive_count + self.false_positive_count
        recall_denominator = self.true_positive_count + self.false_negative_count
        return {
            "precision": _safe_div(self.true_positive_count, precision_denominator),
            "recall": _safe_div(self.true_positive_count, recall_denominator),
            "evidence_hit_rate": _safe_div(self.evidence_hit_count, self.actual_count),
            "low_confidence_ratio": _safe_div(self.low_confidence_count, self.actual_count),
        }


def evaluate_case_root(case_root: Path) -> dict[str, Any]:
    cases = discover_cases(case_root)
    results = [evaluate_case(case) for case in cases]
    aggregate = _aggregate(results)
    return {
        "case_root": str(case_root),
        "case_count": len(results),
        "aggregate": aggregate,
        "cases": [result.to_dict() for result in results],
    }


def evaluate_case(case: OcrCompareCase) -> OcrCompareCaseResult:
    expected_payload, actual_task = load_case_inputs(case)
    expected_diffs = expected_payload.get("expected_diffs", [])
    actual_diffs = [diff.model_dump(mode="json") for diff in actual_task.diffs]
    matches = _match_expected_diffs(expected_diffs, actual_diffs)
    matched_actual_indexes = {actual_index for _, actual_index in matches}
    issues = _case_issues(expected_diffs, actual_diffs, matches)
    return OcrCompareCaseResult(
        case_id=case.case_id,
        status=actual_task.status,
        expected_count=len(expected_diffs),
        actual_count=len(actual_diffs),
        true_positive_count=len(matches),
        false_positive_count=len(actual_diffs) - len(matched_actual_indexes),
        false_negative_count=len(expected_diffs) - len(matches),
        evidence_hit_count=sum(1 for diff in actual_diffs if _has_high_quality_evidence(diff)),
        low_confidence_count=sum(1 for diff in actual_diffs if _is_low_confidence(diff)),
        ocr_warning_count=sum(1 for warning in actual_task.parse_warning_details if "OCR" in warning.code or "OCR" in warning.message),
        task_failure_count=0 if actual_task.status == "COMPLETED" else 1,
        issues=issues,
    )


def _match_expected_diffs(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> list[tuple[int, int]]:
    matches: list[tuple[int, int]] = []
    used_actual: set[int] = set()
    for expected_index, expected_diff in enumerate(expected):
        best_index = None
        best_score = 0.0
        for actual_index, actual_diff in enumerate(actual):
            if actual_index in used_actual:
                continue
            score = _diff_match_score(expected_diff, actual_diff)
            if score > best_score:
                best_index = actual_index
                best_score = score
        if best_index is not None and best_score >= 0.72:
            used_actual.add(best_index)
            matches.append((expected_index, best_index))
    return matches


def _diff_match_score(expected: dict[str, Any], actual: dict[str, Any]) -> float:
    score = 0.0
    if expected.get("diff_type") == actual.get("diff_type"):
        score += 0.25
    if expected.get("source_type") and expected.get("source_type") == actual.get("source_type"):
        score += 0.15
    if _contains(actual.get("title", ""), expected.get("title_contains", "")):
        score += 0.15
    if _contains(actual.get("original_text", "") + actual.get("original_snippet", ""), expected.get("original_contains", "")):
        score += 0.20
    if _contains(actual.get("compare_text", "") + actual.get("compare_snippet", ""), expected.get("compare_contains", "")):
        score += 0.20
    if _expected_evidence_hits(expected, actual):
        score += 0.05
    return score


def _contains(text: str, needle: str) -> bool:
    return not needle or needle in (text or "")


def _expected_evidence_hits(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    expected_evidence = expected.get("expected_evidence", [])
    if not expected_evidence:
        return True
    actual_by_side = {
        "original": actual.get("original_evidence", []),
        "compare": actual.get("compare_evidence", []),
    }
    return all(
        any(
            evidence.get("page_no") == expected_item.get("page_no")
            and _bbox_iou(evidence.get("bbox"), expected_item.get("bbox")) >= 0.5
            for evidence in actual_by_side.get(expected_item.get("side", ""), [])
        )
        for expected_item in expected_evidence
    )


def _has_high_quality_evidence(diff: dict[str, Any]) -> bool:
    evidences = [*diff.get("original_evidence", []), *diff.get("compare_evidence", [])]
    return bool(evidences) and any(
        evidence.get("evidence_quality") in {"MEDIUM", "HIGH"} and float(evidence.get("confidence", 0.0)) >= 0.6
        for evidence in evidences
    )


def _is_low_confidence(diff: dict[str, Any]) -> bool:
    if diff.get("quality_status") == "NEEDS_REVIEW":
        return True
    if any("OCR" in flag or "LOW_CONFIDENCE" in flag for flag in diff.get("review_flags", [])):
        return True
    evidences = [*diff.get("original_evidence", []), *diff.get("compare_evidence", [])]
    if not evidences:
        return True
    return any(
        evidence.get("evidence_quality") == "LOW" or float(evidence.get("confidence", 1.0)) < 0.6
        for evidence in evidences
    )


def _case_issues(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    matches: list[tuple[int, int]],
) -> list[str]:
    matched_expected = {expected_index for expected_index, _ in matches}
    matched_actual = {actual_index for _, actual_index in matches}
    issues = [
        f"missed expected diff {index + 1}: {item.get('title_contains') or item.get('source_type') or item.get('diff_type')}"
        for index, item in enumerate(expected)
        if index not in matched_expected
    ]
    issues.extend(
        f"unexpected actual diff {index + 1}: {item.get('title') or item.get('source_type') or item.get('diff_type')}"
        for index, item in enumerate(actual)
        if index not in matched_actual
    )
    return issues


def _aggregate(results: list[OcrCompareCaseResult]) -> dict[str, Any]:
    aggregate = OcrCompareCaseResult(
        case_id="TOTAL",
        status="COMPLETED" if all(item.status == "COMPLETED" for item in results) else "FAILED",
        expected_count=sum(item.expected_count for item in results),
        actual_count=sum(item.actual_count for item in results),
        true_positive_count=sum(item.true_positive_count for item in results),
        false_positive_count=sum(item.false_positive_count for item in results),
        false_negative_count=sum(item.false_negative_count for item in results),
        evidence_hit_count=sum(item.evidence_hit_count for item in results),
        low_confidence_count=sum(item.low_confidence_count for item in results),
        ocr_warning_count=sum(item.ocr_warning_count for item in results),
        task_failure_count=sum(item.task_failure_count for item in results),
        issues=[issue for item in results for issue in item.issues],
    )
    return aggregate.to_dict()


def _bbox_iou(left: dict[str, Any] | None, right: dict[str, Any] | None) -> float:
    if not left or not right:
        return 0.0
    x0 = max(float(left.get("x0", 0)), float(right.get("x0", 0)))
    y0 = max(float(left.get("y0", 0)), float(right.get("y0", 0)))
    x1 = min(float(left.get("x1", 0)), float(right.get("x1", 0)))
    y1 = min(float(left.get("y1", 0)), float(right.get("y1", 0)))
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    left_area = max(0.0, float(left.get("x1", 0)) - float(left.get("x0", 0))) * max(0.0, float(left.get("y1", 0)) - float(left.get("y0", 0)))
    right_area = max(0.0, float(right.get("x1", 0)) - float(right.get("x0", 0))) * max(0.0, float(right.get("y1", 0)) - float(right.get("y0", 0)))
    denominator = left_area + right_area - intersection
    return _safe_div(intersection, denominator)


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)
```

- [ ] **Step 4: Run metric tests to verify they pass**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_reports_ocr_compare_metrics tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_root_aggregates_metrics -v
```

Expected: PASS.

- [ ] **Step 5: Run all evaluator tests**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/evaluate_ocr_compare_quality.py backend/tests/test_evaluate_ocr_compare_quality.py
git commit -m "feat: calculate OCR comparison baseline metrics"
```

---

### Task 4: Add Threshold Gate And CLI JSON Output

**Files:**
- Modify: `backend/scripts/evaluate_ocr_compare_quality.py`
- Modify: `backend/tests/test_evaluate_ocr_compare_quality.py`

- [ ] **Step 1: Write failing threshold and CLI tests**

Append to `backend/tests/test_evaluate_ocr_compare_quality.py`:

```python
import json
import subprocess
import sys

from scripts.evaluate_ocr_compare_quality import threshold_failures


def test_threshold_failures_pass_for_smoke_fixture() -> None:
    report = evaluate_case_root(Path("tests/fixtures/ocr_compare_cases"))

    assert threshold_failures(report) == []


def test_cli_writes_json_output(tmp_path: Path) -> None:
    output = tmp_path / "ocr_compare_quality.json"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/evaluate_ocr_compare_quality.py",
            "tests/fixtures/ocr_compare_cases",
            "--output",
            str(output),
            "--fail-on-threshold",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["case_count"] == 1
    assert payload["threshold_failures"] == []
    assert payload["aggregate"]["recall"] == 1.0
```

- [ ] **Step 2: Run new tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_threshold_failures_pass_for_smoke_fixture tests/test_evaluate_ocr_compare_quality.py::test_cli_writes_json_output -v
```

Expected: FAIL with missing `threshold_failures` and missing CLI behavior.

- [ ] **Step 3: Implement thresholds and CLI**

Add these imports near the top of `backend/scripts/evaluate_ocr_compare_quality.py`:

```python
import argparse
```

Add this constant after imports:

```python
DEFAULT_THRESHOLDS = {
    "min_recall": 1.0,
    "max_false_positive_count": 0,
    "max_task_failure_count": 0,
    "min_evidence_hit_rate": 1.0,
}
```

Change `evaluate_case_root` so it adds threshold failures:

```python
def evaluate_case_root(case_root: Path) -> dict[str, Any]:
    cases = discover_cases(case_root)
    results = [evaluate_case(case) for case in cases]
    aggregate = _aggregate(results)
    report = {
        "case_root": str(case_root),
        "case_count": len(results),
        "thresholds": DEFAULT_THRESHOLDS,
        "aggregate": aggregate,
        "cases": [result.to_dict() for result in results],
    }
    report["threshold_failures"] = threshold_failures(report)
    return report
```

Add these functions before `if __name__ == "__main__"`:

```python
def threshold_failures(report: dict[str, Any]) -> list[str]:
    aggregate = report["aggregate"]
    failures: list[str] = []
    if aggregate["recall"] < DEFAULT_THRESHOLDS["min_recall"]:
        failures.append(
            f"recall={aggregate['recall']:.4f} < {DEFAULT_THRESHOLDS['min_recall']:.4f}"
        )
    if aggregate["false_positive_count"] > DEFAULT_THRESHOLDS["max_false_positive_count"]:
        failures.append(
            f"false_positive_count={aggregate['false_positive_count']} > {DEFAULT_THRESHOLDS['max_false_positive_count']}"
        )
    if aggregate["task_failure_count"] > DEFAULT_THRESHOLDS["max_task_failure_count"]:
        failures.append(
            f"task_failure_count={aggregate['task_failure_count']} > {DEFAULT_THRESHOLDS['max_task_failure_count']}"
        )
    if aggregate["evidence_hit_rate"] < DEFAULT_THRESHOLDS["min_evidence_hit_rate"]:
        failures.append(
            f"evidence_hit_rate={aggregate['evidence_hit_rate']:.4f} < {DEFAULT_THRESHOLDS['min_evidence_hit_rate']:.4f}"
        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate OCR-driven contract comparison quality.")
    parser.add_argument("case_root", type=Path, help="Directory containing OCR compare case subdirectories.")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path.")
    parser.add_argument("--fail-on-threshold", action="store_true", help="Exit 1 if smoke thresholds fail.")
    args = parser.parse_args()

    report = evaluate_case_root(args.case_root)
    content = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content)
    if args.fail_on_threshold and report["threshold_failures"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run threshold and CLI tests**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_threshold_failures_pass_for_smoke_fixture tests/test_evaluate_ocr_compare_quality.py::test_cli_writes_json_output -v
```

Expected: PASS.

- [ ] **Step 5: Run CLI manually**

Run:

```bash
cd backend
python scripts/evaluate_ocr_compare_quality.py tests/fixtures/ocr_compare_cases --fail-on-threshold
```

Expected: JSON printed to stdout with `"case_count": 1` and `"threshold_failures": []`.

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/evaluate_ocr_compare_quality.py backend/tests/test_evaluate_ocr_compare_quality.py
git commit -m "feat: add OCR comparison smoke gate"
```

---

### Task 5: Add HTML Report Output

**Files:**
- Modify: `backend/scripts/evaluate_ocr_compare_quality.py`
- Modify: `backend/tests/test_evaluate_ocr_compare_quality.py`

- [ ] **Step 1: Write failing HTML report test**

Append to `backend/tests/test_evaluate_ocr_compare_quality.py`:

```python
from scripts.evaluate_ocr_compare_quality import write_html_report


def test_write_html_report_creates_index_and_case_pages(tmp_path: Path) -> None:
    report = evaluate_case_root(Path("tests/fixtures/ocr_compare_cases"))

    write_html_report(tmp_path, report)

    index = tmp_path / "index.html"
    case_page = tmp_path / "simple_scanned.html"
    assert index.exists()
    assert case_page.exists()
    assert "OCR comparison quality report" in index.read_text(encoding="utf-8")
    assert "simple_scanned" in case_page.read_text(encoding="utf-8")
    assert "OCR_LOW_CONFIDENCE" in case_page.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run HTML test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_write_html_report_creates_index_and_case_pages -v
```

Expected: FAIL with missing `write_html_report`.

- [ ] **Step 3: Implement HTML report**

Add this import near the top of `backend/scripts/evaluate_ocr_compare_quality.py`:

```python
import html
```

Add this function before `main`:

```python
def write_html_report(path: Path, report: dict[str, Any]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in report["cases"]:
        case_filename = f"{case['case_id']}.html"
        (path / case_filename).write_text(_case_html(case), encoding="utf-8")
        rows.append(
            "<tr>"
            f"<td><a href='{html.escape(case_filename)}'>{html.escape(case['case_id'])}</a></td>"
            f"<td>{case['status']}</td>"
            f"<td>{case['recall']:.2%}</td>"
            f"<td>{case['precision']:.2%}</td>"
            f"<td>{case['false_positive_count']}</td>"
            f"<td>{case['false_negative_count']}</td>"
            f"<td>{case['low_confidence_count']}</td>"
            f"<td>{case['ocr_warning_count']}</td>"
            "</tr>"
        )
    failures = "<br>".join(html.escape(item) for item in report["threshold_failures"]) or "None"
    index = (
        "<!doctype html><meta charset='utf-8'>"
        "<title>OCR comparison quality report</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px;color:#1f2933}"
        "table{border-collapse:collapse;width:100%;margin-top:16px}"
        "td,th{border:1px solid #cbd5e1;padding:6px;text-align:left}"
        ".failures{padding:10px;background:#fff7ed;border:1px solid #fed7aa}</style>"
        "<h1>OCR comparison quality report</h1>"
        f"<p>Case count: {report['case_count']}</p>"
        f"<p class='failures'>Threshold failures: {failures}</p>"
        "<table><tr><th>Case</th><th>Status</th><th>Recall</th><th>Precision</th>"
        "<th>False positives</th><th>Missed diffs</th><th>Low confidence</th><th>OCR warnings</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    (path / "index.html").write_text(index, encoding="utf-8")


def _case_html(case: dict[str, Any]) -> str:
    issues = "".join(f"<li>{html.escape(issue)}</li>" for issue in case["issues"]) or "<li>None</li>"
    return (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>{html.escape(case['case_id'])}</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px;color:#1f2933}"
        "dl{display:grid;grid-template-columns:220px 1fr;gap:6px}"
        "dt{font-weight:700}</style>"
        f"<h1>{html.escape(case['case_id'])}</h1>"
        "<dl>"
        f"<dt>Status</dt><dd>{html.escape(case['status'])}</dd>"
        f"<dt>Recall</dt><dd>{case['recall']:.2%}</dd>"
        f"<dt>Precision</dt><dd>{case['precision']:.2%}</dd>"
        f"<dt>False positives</dt><dd>{case['false_positive_count']}</dd>"
        f"<dt>Missed diffs</dt><dd>{case['false_negative_count']}</dd>"
        f"<dt>Low-confidence diffs</dt><dd>{case['low_confidence_count']}</dd>"
        f"<dt>OCR warnings</dt><dd>{case['ocr_warning_count']}</dd>"
        f"<dt>Review signal</dt><dd>{'OCR_LOW_CONFIDENCE' if case['low_confidence_count'] else 'None'}</dd>"
        "</dl>"
        f"<h2>Issues</h2><ul>{issues}</ul>"
    )
```

Modify `main` to accept `--html-output` and write HTML:

```python
def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate OCR-driven contract comparison quality.")
    parser.add_argument("case_root", type=Path, help="Directory containing OCR compare case subdirectories.")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path.")
    parser.add_argument("--html-output", type=Path, default=None, help="Optional directory for HTML report.")
    parser.add_argument("--fail-on-threshold", action="store_true", help="Exit 1 if smoke thresholds fail.")
    args = parser.parse_args()

    report = evaluate_case_root(args.case_root)
    content = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content)
    if args.html_output:
        write_html_report(args.html_output, report)
    if args.fail_on_threshold and report["threshold_failures"]:
        return 1
    return 0
```

- [ ] **Step 4: Run HTML report test**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_write_html_report_creates_index_and_case_pages -v
```

Expected: PASS.

- [ ] **Step 5: Run full evaluator test file**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py -v
```

Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/evaluate_ocr_compare_quality.py backend/tests/test_evaluate_ocr_compare_quality.py
git commit -m "feat: render OCR comparison quality report"
```

---

### Task 6: Add Baseline Documentation And Spec Link

**Files:**
- Modify: `backend/tests/fixtures/ocr_compare_cases/README.md`
- Modify: `docs/superpowers/specs/2026-06-25-ocr-compare-accuracy-design.md`
- Test: manual CLI smoke command

- [ ] **Step 1: Expand fixture README with run commands**

Replace `backend/tests/fixtures/ocr_compare_cases/README.md` with:

```markdown
# OCR Compare Cases

Each child directory is one scanned contract comparison case.

Required files for committed deterministic tests:

- `expected.json`: human-reviewed expected output.
- `actual.json`: saved `CompareTask` payload used by tests that do not call OCR services.
- `README.md`: case notes and known difficulty tags.

Optional files for local or internal full runs:

- `original.pdf`
- `compare.pdf`

Sensitive real contracts must not be committed. For sensitive cases, commit only annotation files and load PDFs from an internal path when running the evaluator locally.

## Expected JSON Fields

- `case_id`: stable case identifier matching the directory name.
- `tags`: case traits such as `scanned`, `table_heavy`, `seal_page`, or `low_text_confidence`.
- `critical_fields`: reviewed fields that are sensitive to OCR errors.
- `expected_diffs`: reviewed diff expectations. Use `diff_type`, `source_type`, `title_contains`, `original_contains`, `compare_contains`, and `expected_evidence`.
- `quality_expectations`: case-specific expectations used by humans when reviewing the report.

## Smoke Command

```bash
cd backend
python scripts/evaluate_ocr_compare_quality.py tests/fixtures/ocr_compare_cases \
  --output .ocr-compare-quality/ocr_compare_quality.json \
  --html-output .ocr-compare-quality/html \
  --fail-on-threshold
```
```

- [ ] **Step 2: Link Phase 1 plan from the approved spec**

Add this section near the end of `docs/superpowers/specs/2026-06-25-ocr-compare-accuracy-design.md`, before `## References`:

```markdown
## Implementation Plans

- Phase 1 baseline and golden set: `docs/superpowers/plans/2026-06-25-ocr-compare-baseline.md`
```

- [ ] **Step 3: Run tests**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py -v
```

Expected: all tests in the file pass.

- [ ] **Step 4: Run manual smoke command**

Run:

```bash
cd backend
python scripts/evaluate_ocr_compare_quality.py tests/fixtures/ocr_compare_cases \
  --output .ocr-compare-quality/ocr_compare_quality.json \
  --html-output .ocr-compare-quality/html \
  --fail-on-threshold
```

Expected:

- Command exits with status 0.
- `.ocr-compare-quality/ocr_compare_quality.json` exists.
- `.ocr-compare-quality/html/index.html` exists.
- JSON contains `"threshold_failures": []`.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/fixtures/ocr_compare_cases/README.md docs/superpowers/specs/2026-06-25-ocr-compare-accuracy-design.md
git commit -m "docs: document OCR comparison baseline evaluation"
```

---

### Task 7: Run Existing Regression Checks

**Files:**
- No source changes expected.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py tests/test_evaluate_compare_quality.py tests/test_evaluate_layout_quality.py -v
```

Expected: all selected tests pass.

- [ ] **Step 2: Run backend syntax check**

Run:

```bash
cd backend
python -m compileall app tests scripts
```

Expected: command exits with status 0.

- [ ] **Step 3: Run backend test suite**

Run:

```bash
cd backend
python -m pytest
```

Expected: all backend tests pass.

- [ ] **Step 4: Run backend lint if ruff is installed**

Run:

```bash
cd backend
python -m ruff check .
```

Expected: command exits with status 0. If `ruff` is not installed, record the exact missing-module output in the handoff.

- [ ] **Step 5: Commit final verification note only if source changed during fixes**

If verification required code or doc fixes, commit those fixes:

```bash
git add backend/scripts/evaluate_ocr_compare_quality.py backend/tests/test_evaluate_ocr_compare_quality.py backend/tests/fixtures/ocr_compare_cases docs/superpowers/specs/2026-06-25-ocr-compare-accuracy-design.md
git commit -m "fix: stabilize OCR comparison evaluator"
```

If no files changed, skip this commit.

---

## Self-Review Notes

Spec coverage:

- Golden set structure is covered by Tasks 1 and 6.
- Evaluation script is covered by Tasks 2, 3, 4, and 5.
- JSON output is covered by Task 4.
- HTML output is covered by Task 5.
- Smoke regression gate is covered by Task 4.
- Sensitive PDF handling is covered by Tasks 1 and 6.
- Baseline verification is covered by Task 7.

The remaining spec phases are intentionally excluded from this Phase 1 plan:

- Runtime OCR quality profile.
- Low-quality page repair and local retry.
- OCR-noise-resistant comparison logic.
- Frontend and report quality warnings.
- Continuous quality operations beyond the first smoke gate.

Those should be planned after this baseline evaluator is merged and has produced an initial report on real annotated samples.

