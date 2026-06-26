# OCR Gold Case Quality Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a file-based gold-case quality loop so completed comparison tasks can be exported, human-reviewed, and evaluated with structured precision, recall, evidence drift, OCR risk, and routing reports.

**Architecture:** Add a standalone exporter script that copies a task payload into a case directory and creates a human-review draft `expected.json`. Extend the existing OCR comparison evaluator in place, preserving current JSON fields while adding annotation filtering and structured match details. Enhance the existing HTML report with those structured details and document the workflow.

**Tech Stack:** Python 3.12, FastAPI/Pydantic models already in `backend/app`, JSON file fixtures, pytest, ruff, existing `backend/scripts/evaluate_ocr_compare_quality.py` evaluator.

---

## File Structure

- Create `backend/scripts/export_ocr_compare_gold_case.py`
  - Standalone CLI and importable `export_gold_case()` function.
  - Reads `task.json` from a task directory.
  - Writes `actual.json`, draft `expected.json`, and `README.md`.
  - Does not copy PDFs.

- Create `backend/tests/test_export_ocr_compare_gold_case.py`
  - Unit tests for exporter behavior and CLI.

- Modify `backend/scripts/evaluate_ocr_compare_quality.py`
  - Add annotation status filtering.
  - Add structured match details.
  - Add annotation summary aggregation.
  - Enhance HTML report sections.

- Modify `backend/tests/test_evaluate_ocr_compare_quality.py`
  - Add tests for filtering, structured details, aggregate annotation summary, and HTML sections.
  - Keep existing tests passing.

- Create `docs/ocr_compare_gold_case_workflow.md`
  - User-facing workflow documentation.

---

## Task 1: Gold Case Exporter Tests

**Files:**
- Create: `backend/tests/test_export_ocr_compare_gold_case.py`
- Create later: `backend/scripts/export_ocr_compare_gold_case.py`

- [ ] **Step 1: Write the failing exporter tests**

Create `backend/tests/test_export_ocr_compare_gold_case.py` with this content:

```python
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.export_ocr_compare_gold_case import export_gold_case


def _write_task(task_dir: Path) -> None:
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "task_id": "task-gold-001",
                "status": "COMPLETED",
                "original_filename": "original.pdf",
                "compare_filename": "compare.pdf",
                "created_at": "2026-06-26T08:00:00+00:00",
                "updated_at": "2026-06-26T08:01:00+00:00",
                "ocr_quality_summary": {
                    "status": "UNRELIABLE",
                    "requires_review": True,
                    "risk_page_count": 2,
                    "affected_diff_count": 2,
                },
                "diffs": [
                    {
                        "diff_id": "D001",
                        "diff_type": "MODIFY",
                        "source_type": "metadata",
                        "title": "Contract date",
                        "original_text": "Signed on 2024-04-01.",
                        "compare_text": "Signed on 2024-04-21.",
                        "quality_status": "NEEDS_REVIEW",
                        "review_flags": ["CRITICAL_VALUE_CHANGE", "OCR_LOW_CONFIDENCE"],
                    },
                    {
                        "diff_id": "D002",
                        "diff_type": "ADD",
                        "source_type": "seal",
                        "title": "Seal region",
                        "compare_text": "Company seal",
                        "quality_status": "NEEDS_REVIEW",
                        "review_flags": ["SEAL_REVIEW"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_export_gold_case_creates_review_draft_files(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    output_dir = tmp_path / "case_gold_001"
    _write_task(task_dir)

    summary = export_gold_case(task_dir, output_dir)

    assert summary == {
        "case_id": "case_gold_001",
        "task_id": "task-gold-001",
        "expected_diff_count": 2,
        "actual_diff_count": 2,
    }
    actual = json.loads((output_dir / "actual.json").read_text(encoding="utf-8"))
    expected = json.loads((output_dir / "expected.json").read_text(encoding="utf-8"))
    readme = (output_dir / "README.md").read_text(encoding="utf-8")

    assert actual["task_id"] == "task-gold-001"
    assert expected["case_id"] == "case_gold_001"
    assert expected["tags"] == ["exported", "requires_human_review"]
    assert expected["expected_diffs"][0]["source_actual_diff_id"] == "D001"
    assert expected["expected_diffs"][0]["review_status"] == "DRAFT"
    assert expected["expected_diffs"][0]["severity"] == "critical"
    assert expected["expected_diffs"][0]["title_contains"] == "Contract date"
    assert expected["expected_diffs"][0]["original_contains"] == "Signed on 2024-04-01."
    assert expected["expected_diffs"][0]["compare_contains"] == "Signed on 2024-04-21."
    assert expected["expected_diffs"][1]["severity"] == "critical"
    assert expected["quality_expectations"]["max_task_failures"] == 0
    assert "Generated draft, not reviewed gold" in readme
    assert "task-gold-001" in readme


def test_export_gold_case_refuses_to_overwrite_reviewed_expected(
    tmp_path: Path,
) -> None:
    task_dir = tmp_path / "task"
    output_dir = tmp_path / "case_gold_001"
    _write_task(task_dir)
    output_dir.mkdir()
    (output_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "case_gold_001",
                "expected_diffs": [{"review_status": "APPROVED"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(FileExistsError, match="reviewed expected.json"):
        export_gold_case(task_dir, output_dir)


def test_export_gold_case_force_overwrites_reviewed_expected(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    output_dir = tmp_path / "case_gold_001"
    _write_task(task_dir)
    output_dir.mkdir()
    (output_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "case_gold_001",
                "expected_diffs": [{"review_status": "APPROVED"}],
            }
        ),
        encoding="utf-8",
    )

    export_gold_case(task_dir, output_dir, force=True)

    expected = json.loads((output_dir / "expected.json").read_text(encoding="utf-8"))
    assert {item["review_status"] for item in expected["expected_diffs"]} == {"DRAFT"}


def test_export_gold_case_cli_writes_summary(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    output_dir = tmp_path / "case_gold_001"
    _write_task(task_dir)

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/export_ocr_compare_gold_case.py",
            str(task_dir),
            str(output_dir),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "case_gold_001" in completed.stdout
    assert (output_dir / "actual.json").exists()
    assert (output_dir / "expected.json").exists()
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
cd backend
python -m pytest tests/test_export_ocr_compare_gold_case.py -v
```

Expected: FAIL during import with `ModuleNotFoundError` or `ImportError` because `scripts.export_ocr_compare_gold_case` does not exist.

- [ ] **Step 3: Commit the RED tests**

Do not commit this task separately unless the worker workflow requires a red-test checkpoint. If committing, use:

```bash
git add backend/tests/test_export_ocr_compare_gold_case.py
git commit -m "test: cover OCR gold case exporter"
```

---

## Task 2: Gold Case Exporter Implementation

**Files:**
- Create: `backend/scripts/export_ocr_compare_gold_case.py`
- Test: `backend/tests/test_export_ocr_compare_gold_case.py`

- [ ] **Step 1: Implement the exporter**

Create `backend/scripts/export_ocr_compare_gold_case.py` with these public entry points and behavior:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def export_gold_case(
    task_dir: Path,
    output_dir: Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    task = _read_json(task_dir / "task.json")
    _validate_task(task)
    output_dir.mkdir(parents=True, exist_ok=True)
    expected_path = output_dir / "expected.json"
    if expected_path.exists() and _has_reviewed_expected(expected_path) and not force:
        raise FileExistsError(
            f"{expected_path} contains reviewed expected.json entries; pass --force to overwrite"
        )

    expected = _build_expected_payload(output_dir.name, task)
    _write_json(output_dir / "actual.json", task)
    _write_json(expected_path, expected)
    (output_dir / "README.md").write_text(_readme(output_dir.name, task), encoding="utf-8")
    return {
        "case_id": output_dir.name,
        "task_id": str(task["task_id"]),
        "expected_diff_count": len(expected["expected_diffs"]),
        "actual_diff_count": len(task.get("diffs", [])),
    }
```

Add these helpers in the same file:

```python
def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate_task(task: dict[str, Any]) -> None:
    if not task.get("task_id"):
        raise ValueError("task.json is missing task_id")
    if not isinstance(task.get("diffs"), list):
        raise ValueError("task.json is missing diffs list")


def _has_reviewed_expected(path: Path) -> bool:
    payload = _read_json(path)
    return any(
        item.get("review_status") == "APPROVED"
        for item in payload.get("expected_diffs", [])
    )
```

Build expected payload:

```python
def _build_expected_payload(case_id: str, task: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "tags": ["exported", "requires_human_review"],
        "source_task_id": task["task_id"],
        "source_files": {
            "original_filename": task.get("original_filename", ""),
            "compare_filename": task.get("compare_filename", ""),
        },
        "annotation_status": "DRAFT",
        "expected_diffs": [_draft_expected_diff(diff) for diff in task.get("diffs", [])],
        "quality_expectations": {
            "max_task_failures": 0,
            "min_recall": 1.0,
            "max_false_positive_count": 0,
            "min_evidence_hit_rate": 1.0,
        },
    }
```

Draft each diff:

```python
def _draft_expected_diff(diff: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "diff_type": diff.get("diff_type", ""),
        "source_type": diff.get("source_type", ""),
        "title_contains": _snippet(diff.get("title", "")),
        "source_actual_diff_id": diff.get("diff_id", ""),
        "review_status": "DRAFT",
        "severity": _severity(diff),
        "notes": "Generated draft, not reviewed gold. Change review_status to APPROVED after human validation.",
    }
    original = _snippet(diff.get("original_text") or diff.get("original_snippet") or "")
    compare = _snippet(diff.get("compare_text") or diff.get("compare_snippet") or "")
    if original:
        payload["original_contains"] = original
    if compare:
        payload["compare_contains"] = compare
    return {key: value for key, value in payload.items() if value not in ("", [], None)}


def _snippet(value: Any, *, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _severity(diff: dict[str, Any]) -> str:
    flags = set(diff.get("review_flags", []))
    source_type = str(diff.get("source_type", ""))
    title = str(diff.get("title", ""))
    if "CRITICAL_VALUE_CHANGE" in flags or source_type == "seal":
        return "critical"
    if source_type == "metadata" and any(
        token in title.lower()
        for token in ("date", "amount", "price", "party", "contract", "number")
    ):
        return "critical"
    if diff.get("quality_status") == "NEEDS_REVIEW" or any("OCR" in flag for flag in flags):
        return "major"
    return "minor"
```

Add README and CLI:

```python
def _readme(case_id: str, task: dict[str, Any]) -> str:
    ocr_summary = task.get("ocr_quality_summary") or {}
    return "\n".join(
        [
            f"# OCR Compare Gold Case: {case_id}",
            "",
            "Generated draft, not reviewed gold.",
            "",
            f"- Source task: `{task.get('task_id', '')}`",
            f"- Original file: `{task.get('original_filename', '')}`",
            f"- Compare file: `{task.get('compare_filename', '')}`",
            f"- Task status: `{task.get('status', '')}`",
            f"- OCR status: `{ocr_summary.get('status', '')}`",
            f"- OCR risk pages: `{ocr_summary.get('risk_page_count', 0)}`",
            f"- OCR affected diffs: `{ocr_summary.get('affected_diff_count', 0)}`",
            "",
            "Review steps:",
            "1. Open `expected.json`.",
            "2. Remove generated entries that are not true contract differences.",
            "3. Add missing expected differences found by human review.",
            "4. Change validated entries from `DRAFT` to `APPROVED`.",
            "5. Keep sensitive PDFs out of Git unless explicitly approved.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a compare task as an OCR gold-case draft.")
    parser.add_argument("task_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    summary = export_gold_case(args.task_dir, args.output_dir, force=args.force)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run exporter tests**

Run:

```bash
cd backend
python -m pytest tests/test_export_ocr_compare_gold_case.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Run lint for exporter**

Run:

```bash
cd backend
python -m ruff check scripts/export_ocr_compare_gold_case.py tests/test_export_ocr_compare_gold_case.py
```

Expected: all checks pass.

- [ ] **Step 4: Commit exporter**

Run:

```bash
git add backend/scripts/export_ocr_compare_gold_case.py backend/tests/test_export_ocr_compare_gold_case.py
git commit -m "feat: add OCR gold case exporter"
```

---

## Task 3: Evaluator Annotation Filtering And Structured Match Details

**Files:**
- Modify: `backend/scripts/evaluate_ocr_compare_quality.py`
- Modify: `backend/tests/test_evaluate_ocr_compare_quality.py`

- [ ] **Step 1: Add failing evaluator tests**

Append these tests to `backend/tests/test_evaluate_ocr_compare_quality.py`:

```python
def test_evaluate_case_counts_only_approved_gold_diffs(tmp_path: Path) -> None:
    case_dir = tmp_path / "annotation_case"
    case_dir.mkdir()
    (case_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "annotation_case",
                "expected_diffs": [
                    {
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title_contains": "Payment",
                        "original_contains": "30 days",
                        "compare_contains": "45 days",
                        "review_status": "APPROVED",
                    },
                    {
                        "diff_type": "ADD",
                        "source_type": "metadata",
                        "title_contains": "Generated draft",
                        "review_status": "DRAFT",
                    },
                    {
                        "diff_type": "DELETE",
                        "source_type": "clause",
                        "title_contains": "Rejected",
                        "review_status": "REJECTED",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "actual.json").write_text(
        json.dumps(
            {
                "task_id": "EVAL_ANNOTATION_CASE",
                "status": "COMPLETED",
                "parse_warning_details": [],
                "diffs": [
                    {
                        "diff_id": "D001",
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title": "Payment term",
                        "original_text": "Payment is due in 30 days.",
                        "compare_text": "Payment is due in 45 days.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_case(discover_cases(tmp_path)[0]).to_dict()

    assert result["expected_count"] == 1
    assert result["true_positive_count"] == 1
    assert result["false_negative_count"] == 0
    assert result["annotation_summary"] == {
        "approved_expected_count": 1,
        "draft_expected_count": 1,
        "rejected_expected_count": 1,
    }


def test_evaluate_case_emits_structured_match_details(tmp_path: Path) -> None:
    case_dir = tmp_path / "details_case"
    case_dir.mkdir()
    (case_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "details_case",
                "expected_diffs": [
                    {
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title_contains": "Payment",
                        "original_contains": "30 days",
                        "compare_contains": "45 days",
                        "expected_evidence": [
                            {
                                "side": "original",
                                "page_no": 1,
                                "bbox": {"x0": 10, "y0": 10, "x1": 60, "y1": 30},
                            }
                        ],
                    },
                    {
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title_contains": "Delivery",
                        "original_contains": "May",
                        "compare_contains": "June",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "actual.json").write_text(
        json.dumps(
            {
                "task_id": "EVAL_DETAILS_CASE",
                "status": "COMPLETED",
                "parse_warning_details": [],
                "diffs": [
                    {
                        "diff_id": "D001",
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title": "Payment term",
                        "original_text": "Payment is due in 30 days.",
                        "compare_text": "Payment is due in 45 days.",
                        "original_evidence": [
                            {
                                "page_no": 2,
                                "bbox": {"x0": 10, "y0": 10, "x1": 60, "y1": 30},
                            }
                        ],
                    },
                    {
                        "diff_id": "D999",
                        "diff_type": "ADD",
                        "source_type": "metadata",
                        "title": "Unexpected cover text",
                        "quality_status": "NEEDS_REVIEW",
                        "review_flags": ["POSSIBLE_COVER_OCR_FRAGMENT"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_case(discover_cases(tmp_path)[0]).to_dict()

    assert result["matches"] == [
        {
            "expected_index": 0,
            "actual_index": 0,
            "actual_diff_id": "D001",
            "score": 0.95,
            "evidence_hit": False,
        }
    ]
    assert result["missed_expected_diffs"][0]["label"] == "Delivery"
    assert result["unexpected_actual_diffs"] == [
        {
            "actual_index": 1,
            "diff_id": "D999",
            "title": "Unexpected cover text",
            "source_type": "metadata",
            "quality_status": "NEEDS_REVIEW",
            "review_flags": ["POSSIBLE_COVER_OCR_FRAGMENT"],
        }
    ]
    assert result["evidence_drift_diffs"] == [
        {"expected_index": 0, "actual_diff_id": "D001", "label": "Payment"}
    ]
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_counts_only_approved_gold_diffs tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_emits_structured_match_details -v
```

Expected: FAIL because `annotation_summary`, `matches`, `missed_expected_diffs`, `unexpected_actual_diffs`, and `evidence_drift_diffs` are missing and draft/rejected expected diffs are counted.

- [ ] **Step 3: Add evaluator data fields**

In `OcrCompareCaseResult`, add fields after `route_metrics`:

```python
annotation_summary: dict[str, int]
matches: list[dict[str, Any]]
missed_expected_diffs: list[dict[str, Any]]
unexpected_actual_diffs: list[dict[str, Any]]
evidence_drift_diffs: list[dict[str, Any]]
```

Update `failure()` defaults:

```python
annotation_summary=_empty_annotation_summary(),
matches=[],
missed_expected_diffs=[],
unexpected_actual_diffs=[],
evidence_drift_diffs=[],
```

- [ ] **Step 4: Replace tuple matches with scored match records**

Add this dataclass near `OcrCompareCaseResult`:

```python
@dataclass(frozen=True)
class DiffMatch:
    expected_index: int
    actual_index: int
    score: float
```

Change `_match_expected_diffs()` return type to `list[DiffMatch]` and append `DiffMatch(...)`:

```python
def _match_expected_diffs(
    expected: list[dict[str, Any]], actual: list[dict[str, Any]]
) -> list[DiffMatch]:
    matches: list[DiffMatch] = []
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
        if (
            best_index is not None
            and best_score >= 0.72
            and _has_matching_signal(expected_diff, actual[best_index])
        ):
            used_actual.add(best_index)
            matches.append(
                DiffMatch(
                    expected_index=expected_index,
                    actual_index=best_index,
                    score=round(best_score, 4),
                )
            )
    return matches
```

Update functions that iterate matches:

```python
matched_actual_indexes = {match.actual_index for match in matches}
```

And:

```python
for match in matches:
    expected_diff = expected[match.expected_index]
    actual_diff = actual[match.actual_index]
```

- [ ] **Step 5: Add annotation helpers**

Add helpers near `_match_expected_diffs()`:

```python
def _approved_expected_diffs(expected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in expected
        if item.get("review_status") in (None, "", "APPROVED")
    ]


def _annotation_summary(expected: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "approved_expected_count": sum(
            1 for item in expected if item.get("review_status") in (None, "", "APPROVED")
        ),
        "draft_expected_count": sum(
            1 for item in expected if item.get("review_status") == "DRAFT"
        ),
        "rejected_expected_count": sum(
            1 for item in expected if item.get("review_status") == "REJECTED"
        ),
    }


def _empty_annotation_summary() -> dict[str, int]:
    return {
        "approved_expected_count": 0,
        "draft_expected_count": 0,
        "rejected_expected_count": 0,
    }
```

- [ ] **Step 6: Add structured detail helpers**

Add:

```python
def _match_details(
    matches: list[DiffMatch],
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "expected_index": match.expected_index,
            "actual_index": match.actual_index,
            "actual_diff_id": str(actual[match.actual_index].get("diff_id", "")),
            "score": match.score,
            "evidence_hit": _expected_evidence_hit_or_not_required(
                expected[match.expected_index],
                actual[match.actual_index],
            ),
        }
        for match in matches
    ]


def _missed_expected_details(
    expected: list[dict[str, Any]], matches: list[DiffMatch]
) -> list[dict[str, Any]]:
    matched_expected = {match.expected_index for match in matches}
    return [
        {
            "expected_index": index,
            "label": _diff_label(item),
            "diff_type": str(item.get("diff_type", "")),
            "source_type": str(item.get("source_type", "")),
        }
        for index, item in enumerate(expected)
        if index not in matched_expected
    ]


def _unexpected_actual_details(
    actual: list[dict[str, Any]], matches: list[DiffMatch]
) -> list[dict[str, Any]]:
    matched_actual = {match.actual_index for match in matches}
    return [
        {
            "actual_index": index,
            "diff_id": str(item.get("diff_id", "")),
            "title": str(item.get("title", "")),
            "source_type": str(item.get("source_type", "")),
            "quality_status": str(item.get("quality_status", "")),
            "review_flags": list(item.get("review_flags", [])),
        }
        for index, item in enumerate(actual)
        if index not in matched_actual
    ]


def _evidence_drift_details(
    matches: list[DiffMatch],
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "expected_index": match.expected_index,
            "actual_diff_id": str(actual[match.actual_index].get("diff_id", "")),
            "label": _diff_label(expected[match.expected_index]),
        }
        for match in matches
        if expected[match.expected_index].get("expected_evidence", [])
        and not _expected_evidence_hits(
            expected[match.expected_index],
            actual[match.actual_index],
        )
    ]
```

- [ ] **Step 7: Wire helpers in `evaluate_case()`**

Change the start of `evaluate_case()`:

```python
expected_payload, actual_task = load_case_inputs(case)
all_expected_diffs = expected_payload.get("expected_diffs", [])
expected_diffs = _approved_expected_diffs(all_expected_diffs)
actual_diffs = [diff.model_dump(mode="json") for diff in actual_task.diffs]
matches = _match_expected_diffs(expected_diffs, actual_diffs)
matched_actual_indexes = {match.actual_index for match in matches}
issues = _case_issues(expected_diffs, actual_diffs, matches)
```

Add result fields:

```python
annotation_summary=_annotation_summary(all_expected_diffs),
matches=_match_details(matches, expected_diffs, actual_diffs),
missed_expected_diffs=_missed_expected_details(expected_diffs, matches),
unexpected_actual_diffs=_unexpected_actual_details(actual_diffs, matches),
evidence_drift_diffs=_evidence_drift_details(matches, expected_diffs, actual_diffs),
```

- [ ] **Step 8: Update tuple-based tests if needed**

Existing direct assertions against `_match_expected_diffs()` may expect tuple values. Update them to compare match indexes:

```python
matches = _match_expected_diffs(expected, actual)
assert [(match.expected_index, match.actual_index) for match in matches] == []
```

- [ ] **Step 9: Run evaluator tests**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_counts_only_approved_gold_diffs tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_emits_structured_match_details -v
python -m pytest tests/test_evaluate_ocr_compare_quality.py -v
```

Expected: all evaluator tests pass.

- [ ] **Step 10: Commit evaluator structured details**

Run:

```bash
git add backend/scripts/evaluate_ocr_compare_quality.py backend/tests/test_evaluate_ocr_compare_quality.py
git commit -m "feat: add OCR gold annotation metrics"
```

---

## Task 4: Aggregate Annotation Summary

**Files:**
- Modify: `backend/scripts/evaluate_ocr_compare_quality.py`
- Modify: `backend/tests/test_evaluate_ocr_compare_quality.py`

- [ ] **Step 1: Add failing aggregate test**

Append:

```python
def test_evaluate_case_root_aggregates_annotation_summary(tmp_path: Path) -> None:
    for case_id, approved, draft, rejected in [
        ("case_one", 1, 2, 0),
        ("case_two", 2, 0, 1),
    ]:
        case_dir = tmp_path / case_id
        case_dir.mkdir()
        expected_diffs = []
        expected_diffs.extend(
            {
                "diff_type": "MODIFY",
                "source_type": "clause",
                "title_contains": f"approved-{index}",
                "review_status": "APPROVED",
            }
            for index in range(approved)
        )
        expected_diffs.extend(
            {
                "diff_type": "MODIFY",
                "source_type": "clause",
                "title_contains": f"draft-{index}",
                "review_status": "DRAFT",
            }
            for index in range(draft)
        )
        expected_diffs.extend(
            {
                "diff_type": "MODIFY",
                "source_type": "clause",
                "title_contains": f"rejected-{index}",
                "review_status": "REJECTED",
            }
            for index in range(rejected)
        )
        (case_dir / "expected.json").write_text(
            json.dumps({"case_id": case_id, "expected_diffs": expected_diffs}),
            encoding="utf-8",
        )
        (case_dir / "actual.json").write_text(
            json.dumps(
                {
                    "task_id": f"EVAL_{case_id.upper()}",
                    "status": "COMPLETED",
                    "parse_warning_details": [],
                    "diffs": [],
                }
            ),
            encoding="utf-8",
        )

    report = evaluate_case_root(tmp_path)

    assert report["aggregate"]["annotation_summary"] == {
        "approved_expected_count": 3,
        "draft_expected_count": 2,
        "rejected_expected_count": 1,
    }
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_root_aggregates_annotation_summary -v
```

Expected: FAIL because aggregate `annotation_summary` is missing.

- [ ] **Step 3: Implement aggregate helper**

Add near `_aggregate_route_metrics()`:

```python
def _aggregate_annotation_summary(
    results: list[OcrCompareCaseResult],
) -> dict[str, int]:
    summary = Counter()
    for result in results:
        summary.update(result.annotation_summary)
    return {
        "approved_expected_count": int(summary.get("approved_expected_count", 0)),
        "draft_expected_count": int(summary.get("draft_expected_count", 0)),
        "rejected_expected_count": int(summary.get("rejected_expected_count", 0)),
    }
```

Update `_aggregate()`:

```python
payload = aggregate.to_dict()
payload["route_metrics"] = _aggregate_route_metrics(results)
payload["annotation_summary"] = _aggregate_annotation_summary(results)
return payload
```

- [ ] **Step 4: Run focused and full evaluator tests**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_root_aggregates_annotation_summary -v
python -m pytest tests/test_evaluate_ocr_compare_quality.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit aggregate annotation summary**

Run:

```bash
git add backend/scripts/evaluate_ocr_compare_quality.py backend/tests/test_evaluate_ocr_compare_quality.py
git commit -m "feat: aggregate OCR gold annotation summary"
```

---

## Task 5: HTML Report Gold Detail Sections

**Files:**
- Modify: `backend/scripts/evaluate_ocr_compare_quality.py`
- Modify: `backend/tests/test_evaluate_ocr_compare_quality.py`

- [ ] **Step 1: Add failing HTML test**

Append:

```python
def test_write_html_report_renders_gold_detail_sections(tmp_path: Path) -> None:
    report = {
        "case_count": 1,
        "threshold_failures": [],
        "aggregate": {
            "annotation_summary": {
                "approved_expected_count": 1,
                "draft_expected_count": 1,
                "rejected_expected_count": 0,
            },
            "route_metrics": {
                "route_count_by_recommendation": {"MANUAL_REVIEW": 1},
                "page_count_by_type": {"mixed": 1},
                "retry_recommended_count": 0,
                "manual_review_recommended_count": 1,
            },
        },
        "cases": [
            {
                "case_id": "gold_case",
                "status": "COMPLETED",
                "expected_count": 1,
                "actual_count": 2,
                "recall": 0.5,
                "precision": 0.5,
                "false_positive_count": 1,
                "false_negative_count": 1,
                "low_confidence_count": 1,
                "ocr_warning_count": 1,
                "issues": ["missed expected diff 1: Payment"],
                "annotation_summary": {
                    "approved_expected_count": 1,
                    "draft_expected_count": 1,
                    "rejected_expected_count": 0,
                },
                "route_metrics": {
                    "retry_recommended_count": 0,
                    "manual_review_recommended_count": 1,
                },
                "model_routing": {
                    "routes": [
                        {
                            "side": "compare",
                            "page_no": 1,
                            "page_type": "mixed",
                            "recommended_route": "MANUAL_REVIEW",
                        }
                    ]
                },
                "matches": [
                    {
                        "expected_index": 0,
                        "actual_index": 0,
                        "actual_diff_id": "D001",
                        "score": 0.95,
                        "evidence_hit": True,
                    }
                ],
                "missed_expected_diffs": [
                    {
                        "expected_index": 1,
                        "label": "Payment<script>",
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                    }
                ],
                "unexpected_actual_diffs": [
                    {
                        "actual_index": 1,
                        "diff_id": "D999",
                        "title": "Unexpected <b>cover</b>",
                        "source_type": "metadata",
                        "quality_status": "NEEDS_REVIEW",
                        "review_flags": ["POSSIBLE_COVER_OCR_FRAGMENT"],
                    }
                ],
                "evidence_drift_diffs": [
                    {
                        "expected_index": 0,
                        "actual_diff_id": "D001",
                        "label": "Payment",
                    }
                ],
            }
        ],
    }

    write_html_report(tmp_path, report)

    index_html = (tmp_path / "index.html").read_text(encoding="utf-8")
    case_html = (tmp_path / "gold_case.html").read_text(encoding="utf-8")
    assert "Annotation summary" in index_html
    assert "Expected" in index_html
    assert "Actual" in index_html
    assert "Evidence drift" in index_html
    assert "Matched diffs" in case_html
    assert "Missed expected diffs" in case_html
    assert "Unexpected actual diffs" in case_html
    assert "Evidence drift" in case_html
    assert "Payment&lt;script&gt;" in case_html
    assert "Unexpected &lt;b&gt;cover&lt;/b&gt;" in case_html
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_write_html_report_renders_gold_detail_sections -v
```

Expected: FAIL because the HTML sections and columns are not rendered.

- [ ] **Step 3: Add small HTML helper functions**

In `evaluate_ocr_compare_quality.py`, add near `_case_html()`:

```python
def _html_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{html.escape(_format_cell(cell))}</td>" for cell in row)
        + "</tr>"
        for row in rows
    )
    return f"<table><tr>{head}</tr>{body}</table>"


def _format_cell(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)
```

- [ ] **Step 4: Enhance index table**

In `write_html_report()`, add columns to each case row:

```python
f"<td>{case.get('expected_count', 0)}</td>"
f"<td>{case.get('actual_count', 0)}</td>"
f"<td>{len(case.get('evidence_drift_diffs', []))}</td>"
```

Update the header so it includes:

```html
<th>Expected</th><th>Actual</th>
```

and:

```html
<th>Evidence drift</th>
```

Add an annotation summary section:

```python
annotation_summary = html.escape(
    json.dumps(
        report.get("aggregate", {}).get("annotation_summary", {}),
        ensure_ascii=False,
        indent=2,
    )
)
```

Insert:

```python
f"<h2>Annotation summary</h2><pre>{annotation_summary}</pre>"
```

- [ ] **Step 5: Enhance case page**

In `_case_html()`, build table HTML before the return:

```python
matches_table = _html_table(
    ["Expected index", "Actual index", "Actual diff", "Score", "Evidence hit"],
    [
        [
            item.get("expected_index", ""),
            item.get("actual_index", ""),
            item.get("actual_diff_id", ""),
            item.get("score", ""),
            item.get("evidence_hit", ""),
        ]
        for item in case.get("matches", [])
    ],
)
missed_table = _html_table(
    ["Expected index", "Label", "Diff type", "Source type"],
    [
        [
            item.get("expected_index", ""),
            item.get("label", ""),
            item.get("diff_type", ""),
            item.get("source_type", ""),
        ]
        for item in case.get("missed_expected_diffs", [])
    ],
)
unexpected_table = _html_table(
    ["Actual index", "Diff ID", "Title", "Source type", "Quality", "Flags"],
    [
        [
            item.get("actual_index", ""),
            item.get("diff_id", ""),
            item.get("title", ""),
            item.get("source_type", ""),
            item.get("quality_status", ""),
            item.get("review_flags", []),
        ]
        for item in case.get("unexpected_actual_diffs", [])
    ],
)
drift_table = _html_table(
    ["Expected index", "Actual diff", "Label"],
    [
        [
            item.get("expected_index", ""),
            item.get("actual_diff_id", ""),
            item.get("label", ""),
        ]
        for item in case.get("evidence_drift_diffs", [])
    ],
)
```

Add these sections in the returned HTML before `Issues`:

```python
f"<h2>Matched diffs</h2>{matches_table}"
f"<h2>Missed expected diffs</h2>{missed_table}"
f"<h2>Unexpected actual diffs</h2>{unexpected_table}"
f"<h2>Evidence drift</h2>{drift_table}"
```

- [ ] **Step 6: Run HTML tests**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_write_html_report_renders_gold_detail_sections tests/test_evaluate_ocr_compare_quality.py::test_write_html_report_sanitizes_filename_and_escapes_html -v
python -m pytest tests/test_evaluate_ocr_compare_quality.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit HTML report enhancements**

Run:

```bash
git add backend/scripts/evaluate_ocr_compare_quality.py backend/tests/test_evaluate_ocr_compare_quality.py
git commit -m "feat: show OCR gold detail report sections"
```

---

## Task 6: Gold Case Workflow Documentation

**Files:**
- Create: `docs/ocr_compare_gold_case_workflow.md`

- [ ] **Step 1: Create workflow documentation**

Create `docs/ocr_compare_gold_case_workflow.md` with this content:

```markdown
# OCR Compare Gold Case Workflow

## Purpose

A gold case is a human-reviewed contract comparison case used to measure comparison quality. It pairs system output in `actual.json` with human-approved expectations in `expected.json`.

## Export A Draft Case

Run a comparison task first. Then export the task:

```bash
cd backend
python scripts/export_ocr_compare_gold_case.py \
  ../storage/tasks/<task_id> \
  tests/fixtures/ocr_compare_cases/<case_id>
```

The exporter creates:

```text
expected.json
actual.json
README.md
```

Generated expected diffs are marked `DRAFT`. They are not counted as gold until a human changes them to `APPROVED`.

## Review `expected.json`

For each generated `expected_diffs` entry:

1. Check the original and compare PDFs.
2. Delete entries that are not real differences.
3. Edit `title_contains`, `original_contains`, and `compare_contains` so they contain stable reviewed text.
4. Add missing expected differences that the system did not detect.
5. Change validated entries to `"review_status": "APPROVED"`.
6. Keep uncertain entries as `"review_status": "DRAFT"` or mark them `"REJECTED"`.

## Matching Fields

Use these fields for stable matching:

- `diff_type`: `ADD`, `DELETE`, or `MODIFY`.
- `source_type`: `metadata`, `clause`, `table`, `seal`, or `header_footer`.
- `title_contains`: reviewed title fragment.
- `original_contains`: reviewed original-side fragment.
- `compare_contains`: reviewed compare-side fragment.

## Evidence Boxes

Add `expected_evidence` when location matters:

```json
{
  "side": "original",
  "page_no": 1,
  "bbox": { "x0": 100, "y0": 200, "x1": 260, "y1": 230 }
}
```

The evaluator treats evidence as matched when the actual evidence box overlaps the expected box with IoU at least 0.5.

## Run Evaluation

```bash
cd backend
python scripts/evaluate_ocr_compare_quality.py tests/fixtures/ocr_compare_cases \
  --output .ocr-compare-quality/ocr_compare_quality.json \
  --html-output .ocr-compare-quality/html \
  --fail-on-threshold
```

Open:

```text
backend/.ocr-compare-quality/html/index.html
```

## Read The Report

Use these metrics:

- `recall`: approved expected differences found by the system.
- `precision`: system differences that match approved expected differences.
- `false_positive_count`: unexpected actual differences.
- `false_negative_count`: missed approved expected differences.
- `evidence_hit_rate`: matched diffs whose evidence location is correct.
- `low_confidence_ratio`: actual diffs with OCR or evidence risk.

## Sensitive Data

Do not commit sensitive real contracts, local PDFs, customer data, or unapproved extracted payloads. Keep sensitive `original.pdf`, `compare.pdf`, and exported real task cases local unless they have been explicitly approved for version control.
```

- [ ] **Step 2: Check documentation for forbidden placeholders**

Run:

```bash
rg -n "TBD|TODO|placeholder" docs/ocr_compare_gold_case_workflow.md
```

Expected: no output.

- [ ] **Step 3: Commit documentation**

Run:

```bash
git add docs/ocr_compare_gold_case_workflow.md
git commit -m "docs: add OCR gold case workflow"
```

---

## Task 7: Smoke Export Current Local Tasks

**Files:**
- No committed fixture files by default.
- Writes local output under `backend/.ocr-compare-quality/`.

- [ ] **Step 1: Run exporter against one current local task**

Run:

```bash
cd backend
python scripts/export_ocr_compare_gold_case.py \
  ../storage/tasks/218b3d1b-7a28-4a94-96cb-19395655b6f8 \
  .ocr-compare-quality/local_gold_cases/case_218b3d1b \
  --force
```

Expected: command exits 0 and prints JSON with:

```json
{
  "case_id": "case_218b3d1b",
  "task_id": "218b3d1b-7a28-4a94-96cb-19395655b6f8"
}
```

The exact diff counts can vary with local task payloads.

- [ ] **Step 2: Verify generated draft is excluded from gold metrics**

Run:

```bash
cd backend
python scripts/evaluate_ocr_compare_quality.py .ocr-compare-quality/local_gold_cases \
  --output .ocr-compare-quality/local_gold_quality.json \
  --html-output .ocr-compare-quality/local_gold_html
```

Expected: command exits 0. Because all exported entries are `DRAFT`, the evaluated case should have `expected_count` 0 and annotation summary should show draft entries.

- [ ] **Step 3: Inspect local quality JSON**

Run:

```bash
cd backend
jq '{case_count, aggregate: {expected_count: .aggregate.expected_count, annotation_summary: .aggregate.annotation_summary}, cases: [.cases[] | {case_id, expected_count, annotation_summary}]}' .ocr-compare-quality/local_gold_quality.json
```

Expected: `aggregate.expected_count` is 0 and `draft_expected_count` is greater than 0.

- [ ] **Step 4: Do not commit local generated cases**

Run:

```bash
git status --short
```

Expected: local generated output remains under ignored or untracked quality-output paths. Do not add `backend/.ocr-compare-quality/` or `storage/`.

---

## Task 8: Final Verification

**Files:**
- Verification only.

- [ ] **Step 1: Run targeted backend tests**

Run:

```bash
cd backend
python -m pytest tests/test_export_ocr_compare_gold_case.py tests/test_evaluate_ocr_compare_quality.py -v
```

Expected: all selected tests pass.

- [ ] **Step 2: Run backend syntax and lint**

Run:

```bash
cd backend
python -m compileall app tests scripts
python -m ruff check .
```

Expected: both commands pass.

- [ ] **Step 3: Run evaluator fixture smoke**

Run:

```bash
cd backend
python scripts/evaluate_ocr_compare_quality.py tests/fixtures/ocr_compare_cases \
  --output .ocr-compare-quality/ocr_compare_quality.json \
  --html-output .ocr-compare-quality/html \
  --fail-on-threshold
```

Expected: command exits 0.

- [ ] **Step 4: Inspect evaluator route and annotation output**

Run:

```bash
cd backend
jq '{threshold_failures, aggregate: {annotation_summary: .aggregate.annotation_summary, route_metrics: .aggregate.route_metrics}}' .ocr-compare-quality/ocr_compare_quality.json
```

Expected: `threshold_failures` is an empty list for existing smoke fixture.

- [ ] **Step 5: Run full backend tests**

Run:

```bash
cd backend
python -m pytest
```

Expected: all backend tests pass.

- [ ] **Step 6: Run frontend tests and build**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: both commands pass.

- [ ] **Step 7: Check git status**

Run:

```bash
git status --short --branch
```

Expected: only intended committed changes plus existing untracked local directories. Do not commit `.agents/`, `backend/.ocr-compare-quality/`, `picture/`, `skills-lock.json`, or `storage/`.

---

## Self-Review Checklist

- Spec coverage: exporter, explicit human review, sensitive-data safety, evaluator details, HTML reporting, docs, and compatibility are covered.
- TDD coverage: each behavior starts with a failing test except documentation and smoke verification.
- Public API safety: no `/api/compare/*` or `/api/extract/*` route changes are planned.
- Gold safety: generated entries are `DRAFT` and excluded from metrics until approved.
- Fixture compatibility: missing `review_status` remains approved for existing cases.
- Route metrics: existing empty route-quality maps remain untouched.
