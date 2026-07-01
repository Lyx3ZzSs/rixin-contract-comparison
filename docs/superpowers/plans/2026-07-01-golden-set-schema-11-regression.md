# Golden Set Schema 1.1 Regression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the contract comparison golden set from positive-only approved diff checks to a versioned Schema 1.1 regression system that covers positive gold, negative gold, manual false-negative entries, dataset splits, and reviewer metadata.

**Architecture:** Keep the current lightweight `actual.json + expected.json + README.md` fixture model. Extend the existing exporter, evaluator, regression script, and quality workbench service/API so new Schema 1.1 fields are optional and backward compatible with existing Schema 1.0 fixtures.

**Tech Stack:** Python 3.12, pytest, FastAPI, Pydantic, existing backend scripts under `backend/scripts`, existing fixture root `backend/tests/fixtures/ocr_compare_cases`.

---

## File Structure

- Modify `backend/scripts/export_ocr_compare_gold_case.py`
  - Emit Schema 1.1 case-level metadata and diff-level metadata defaults.
- Modify `backend/scripts/evaluate_ocr_compare_quality.py`
  - Support dataset split filtering.
  - Treat approved entries as positive gold.
  - Treat selected rejected entries as negative gold.
  - Report known false-positive regressions separately from generic unexpected diffs.
- Modify `backend/scripts/run_quality_regression.py`
  - Forward dataset split selection into `evaluate_case_root`.
  - Add gates for known false-positive regressions.
- Modify `backend/app/services/quality_workbench.py`
  - Include Schema 1.1 summary fields.
  - Allow safe updates to new expected diff fields.
- Modify `backend/app/api_quality_schemas.py`
  - Expose new summary fields and patchable diff metadata.
- Modify `backend/tests/test_evaluate_ocr_compare_quality.py`
  - Add unit coverage for Schema 1.1 fields, negative gold, manual missed expected diffs, and dataset split filtering.
- Modify `backend/tests/test_quality_workbench.py`
  - Add service coverage for Schema 1.1 summaries and allowed patch fields.
- Modify `backend/tests/test_api_quality.py`
  - Add API coverage for new patch fields.
- Modify `docs/golden_set_regression_sop.md`
  - Document Schema 1.1, negative gold, false-negative 补录, and dataset split usage.

Do not introduce Label Studio, Argilla, Ragas, or DeepEval in this phase. This phase is about making the in-repository golden set harder and more useful for precision regression.

---

## Schema 1.1 Contract

Expected files must remain backward compatible. Existing fixtures without these fields must still evaluate.

Recommended case-level fields:

```json
{
  "schema_version": "1.1",
  "case_id": "case-001",
  "source_task_id": "task-001",
  "dataset_split": "regression",
  "case_tags": ["exported", "requires_human_review"],
  "annotation_status": "DRAFT",
  "baseline_required": true
}
```

Allowed `dataset_split` values:

```text
dev
regression
holdout
adversarial
```

Recommended diff-level fields:

```json
{
  "diff_type": "MODIFY",
  "source_type": "metadata",
  "title_contains": "签订日期",
  "original_contains": "2026年4月 日",
  "compare_contains": "2026年4月21日",
  "review_status": "APPROVED",
  "severity": "critical",
  "reviewer": "human",
  "reviewed_at": "2026-07-01T10:00:00+08:00",
  "notes": "人工确认真实日期差异"
}
```

Negative gold fields:

```json
{
  "review_status": "REJECTED",
  "false_positive_reason": "header_footer",
  "should_not_match_again": true,
  "title_contains": "版本号",
  "notes": "页眉版本号变化，不属于合同正文差异"
}
```

Manual false-negative fields use `APPROVED` entries that do not need `source_actual_diff_id`:

```json
{
  "review_status": "APPROVED",
  "diff_type": "MODIFY",
  "source_type": "metadata",
  "title_contains": "签订日期",
  "original_contains": "2026年4月 日",
  "compare_contains": "2026年4月21日",
  "false_negative_reason": "system_missed_metadata_date",
  "notes": "人工发现系统漏检"
}
```

---

### Task 1: Export Schema 1.1 Draft Gold Cases

**Files:**
- Modify: `backend/scripts/export_ocr_compare_gold_case.py`
- Modify: `backend/tests/test_export_ocr_compare_gold_case.py`

- [ ] **Step 1: Write failing export schema test**

Add this test to `backend/tests/test_export_ocr_compare_gold_case.py`:

```python
def test_export_gold_case_writes_schema_11_metadata(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / "task-001"
    output_dir = tmp_path / "cases" / "case-001"
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text(
        json.dumps(
            {
                "task_id": "task-001",
                "status": "COMPLETED",
                "original_filename": "original.pdf",
                "compare_filename": "compare.pdf",
                "diffs": [
                    {
                        "diff_id": "D001",
                        "title": "合同金额",
                        "diff_type": "MODIFY",
                        "source_type": "metadata",
                        "original_text": "合同金额为100元",
                        "compare_text": "合同金额为200元",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    export_gold_case(task_dir, output_dir)

    expected = json.loads((output_dir / "expected.json").read_text(encoding="utf-8"))
    assert expected["schema_version"] == "1.1"
    assert expected["dataset_split"] == "dev"
    assert expected["case_tags"] == ["exported", "requires_human_review"]
    assert expected["baseline_required"] is False
    assert expected["expected_diffs"][0]["review_status"] == "DRAFT"
    assert expected["expected_diffs"][0]["reviewer"] == ""
    assert expected["expected_diffs"][0]["reviewed_at"] == ""
    assert expected["expected_diffs"][0]["false_positive_reason"] == ""
    assert expected["expected_diffs"][0]["false_negative_reason"] == ""
    assert expected["expected_diffs"][0]["should_not_match_again"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_export_ocr_compare_gold_case.py::test_export_gold_case_writes_schema_11_metadata -v
```

Expected: FAIL because `schema_version`, `dataset_split`, and diff metadata fields are missing.

- [ ] **Step 3: Update export payload**

In `backend/scripts/export_ocr_compare_gold_case.py`, update `_build_expected_payload`:

```python
def _build_expected_payload(case_id: str, task: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "case_id": case_id,
        "dataset_split": "dev",
        "case_tags": ["exported", "requires_human_review"],
        "baseline_required": False,
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
            "max_known_false_positive_regression_count": 0,
            "min_evidence_hit_rate": 1.0,
        },
    }
```

Update `_draft_expected_diff` defaults:

```python
payload: dict[str, Any] = {
    "diff_type": diff.get("diff_type", ""),
    "source_type": diff.get("source_type", ""),
    "title_contains": _snippet(diff.get("title", "")),
    "source_actual_diff_id": diff.get("diff_id", ""),
    "review_status": "DRAFT",
    "severity": _severity(diff),
    "reviewer": "",
    "reviewed_at": "",
    "false_positive_reason": "",
    "false_negative_reason": "",
    "should_not_match_again": False,
    "notes": (
        "Generated draft, not reviewed gold. Change review_status to APPROVED "
        "after human validation."
    ),
}
```

Keep the final filter, but do not remove `False` values:

```python
return {key: value for key, value in payload.items() if value not in ("", [], None)}
```

- [ ] **Step 4: Run export tests**

Run:

```bash
cd backend
python -m pytest tests/test_export_ocr_compare_gold_case.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/export_ocr_compare_gold_case.py backend/tests/test_export_ocr_compare_gold_case.py
git commit -m "feat: export schema 1.1 gold cases"
```

---

### Task 2: Add Negative Gold Evaluation

**Files:**
- Modify: `backend/scripts/evaluate_ocr_compare_quality.py`
- Modify: `backend/tests/test_evaluate_ocr_compare_quality.py`

- [ ] **Step 1: Write failing negative gold test**

Add this test to `backend/tests/test_evaluate_ocr_compare_quality.py`:

```python
def test_evaluate_case_reports_known_false_positive_regressions(tmp_path: Path) -> None:
    case_dir = tmp_path / "negative_gold_case"
    case_dir.mkdir()
    (case_dir / "expected.json").write_text(
        json.dumps(
            {
                "schema_version": "1.1",
                "case_id": "negative_gold_case",
                "dataset_split": "regression",
                "expected_diffs": [
                    {
                        "review_status": "REJECTED",
                        "should_not_match_again": True,
                        "false_positive_reason": "header_footer",
                        "diff_type": "MODIFY",
                        "source_type": "header_footer",
                        "title_contains": "版本号",
                        "compare_contains": "V2.0",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (case_dir / "actual.json").write_text(
        json.dumps(
            {
                "task_id": "EVAL_NEGATIVE_GOLD",
                "status": "COMPLETED",
                "parse_warning_details": [],
                "diffs": [
                    {
                        "diff_id": "D_FP",
                        "diff_type": "MODIFY",
                        "source_type": "header_footer",
                        "title": "版本号",
                        "original_text": "V1.0",
                        "compare_text": "V2.0",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = evaluate_case(discover_cases(tmp_path)[0]).to_dict()

    assert result["expected_count"] == 0
    assert result["false_positive_count"] == 1
    assert result["known_false_positive_regression_count"] == 1
    assert result["known_false_positive_regressions"] == [
        {
            "expected_index": 0,
            "actual_index": 0,
            "actual_diff_id": "D_FP",
            "score": 0.75,
            "false_positive_reason": "header_footer",
            "label": "版本号",
        }
    ]
    assert any("known false positive regression" in issue for issue in result["issues"])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_reports_known_false_positive_regressions -v
```

Expected: FAIL because the evaluator does not yet expose `known_false_positive_regression_count`.

- [ ] **Step 3: Extend result dataclass**

In `OcrCompareCaseResult`, add:

```python
known_false_positive_regression_count: int
known_false_positive_regressions: list[dict[str, Any]]
```

Update `failure()` with zero/empty defaults.

- [ ] **Step 4: Add negative gold helpers**

Add these helpers near `_approved_expected_diffs`:

```python
def _negative_expected_diffs(
    expected: list[dict[str, Any]],
) -> list[ApprovedExpectedDiff]:
    return [
        ApprovedExpectedDiff(source_index=index, payload=item)
        for index, item in enumerate(expected)
        if item.get("review_status") == "REJECTED"
        and item.get("should_not_match_again") is True
    ]


def _known_false_positive_matches(
    negative_expected: list[ApprovedExpectedDiff],
    actual: list[dict[str, Any]],
) -> list[DiffMatch]:
    return _match_expected_diffs(negative_expected, actual)


def _known_false_positive_details(
    matches: list[DiffMatch],
    negative_expected: list[ApprovedExpectedDiff],
    actual: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected_by_source_index = _expected_by_source_index(negative_expected)
    details = []
    for match in matches:
        expected_diff = expected_by_source_index[match.expected_index].payload
        details.append(
            {
                "expected_index": match.expected_index,
                "actual_index": match.actual_index,
                "actual_diff_id": str(actual[match.actual_index].get("diff_id", "")),
                "score": match.score,
                "false_positive_reason": str(
                    expected_diff.get("false_positive_reason", "")
                ),
                "label": _diff_label(expected_diff),
            }
        )
    return details
```

- [ ] **Step 5: Wire negative gold into `evaluate_case`**

In `evaluate_case`, after `expected_diffs = _approved_expected_diffs(...)`, add:

```python
negative_expected_diffs = _negative_expected_diffs(all_expected_diffs)
known_fp_matches = _known_false_positive_matches(
    negative_expected_diffs,
    actual_diffs,
)
known_fp_details = _known_false_positive_details(
    known_fp_matches,
    negative_expected_diffs,
    actual_diffs,
)
```

Pass into result:

```python
known_false_positive_regression_count=len(known_fp_details),
known_false_positive_regressions=known_fp_details,
```

Append issues:

```python
issues.extend(
    f"known false positive regression for expected diff {item['expected_index'] + 1}: {item['label']}"
    for item in known_fp_details
)
```

- [ ] **Step 6: Update aggregate**

In `_aggregate`, sum:

```python
known_false_positive_regression_count=sum(
    item.known_false_positive_regression_count for item in results
),
known_false_positive_regressions=[
    regression
    for item in results
    for regression in item.known_false_positive_regressions
],
```

- [ ] **Step 7: Run evaluator tests**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/scripts/evaluate_ocr_compare_quality.py backend/tests/test_evaluate_ocr_compare_quality.py
git commit -m "feat: evaluate negative gold regressions"
```

---

### Task 3: Add Dataset Split Filtering

**Files:**
- Modify: `backend/scripts/evaluate_ocr_compare_quality.py`
- Modify: `backend/scripts/run_quality_regression.py`
- Modify: `backend/tests/test_evaluate_ocr_compare_quality.py`
- Modify: `backend/tests/test_run_quality_regression.py`

- [ ] **Step 1: Write failing evaluator split test**

Add this test to `backend/tests/test_evaluate_ocr_compare_quality.py`:

```python
def test_evaluate_case_root_filters_dataset_split(tmp_path: Path) -> None:
    for case_id, split in [
        ("regression_case", "regression"),
        ("dev_case", "dev"),
        ("legacy_case", ""),
    ]:
        case_dir = tmp_path / case_id
        case_dir.mkdir()
        expected = {"case_id": case_id, "expected_diffs": []}
        if split:
            expected["dataset_split"] = split
        (case_dir / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
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

    report = evaluate_case_root(tmp_path, dataset_splits={"regression"})

    assert report["case_count"] == 1
    assert [case["case_id"] for case in report["cases"]] == ["regression_case"]
    assert report["dataset_splits"] == ["regression"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_root_filters_dataset_split -v
```

Expected: FAIL because `evaluate_case_root` does not accept `dataset_splits`.

- [ ] **Step 3: Update discovery/evaluation signatures**

Change signatures:

```python
def discover_cases(
    case_root: Path,
    *,
    dataset_splits: set[str] | None = None,
) -> list[OcrCompareCase]:
```

```python
def evaluate_case_root(
    case_root: Path,
    *,
    dataset_splits: set[str] | None = None,
) -> dict[str, Any]:
```

Add helper:

```python
def _case_dataset_split(case_dir: Path) -> str:
    try:
        expected = _read_json(case_dir / "expected.json")
    except Exception:  # noqa: BLE001
        return "legacy"
    return str(expected.get("dataset_split") or "legacy")
```

Filter in `discover_cases`:

```python
if dataset_splits is not None and _case_dataset_split(path) not in dataset_splits:
    continue
```

Add report metadata:

```python
"dataset_splits": sorted(dataset_splits) if dataset_splits is not None else [],
```

- [ ] **Step 4: Update CLI parser**

In `main()` of `evaluate_ocr_compare_quality.py`, add:

```python
parser.add_argument(
    "--dataset-split",
    action="append",
    dest="dataset_splits",
    choices=["dev", "regression", "holdout", "adversarial", "legacy"],
    help="Only evaluate cases in this dataset split. Can be passed multiple times.",
)
```

Call:

```python
report = evaluate_case_root(
    args.case_root,
    dataset_splits=set(args.dataset_splits) if args.dataset_splits else None,
)
```

- [ ] **Step 5: Update regression script**

In `backend/scripts/run_quality_regression.py`, add parser option:

```python
parser.add_argument(
    "--dataset-split",
    action="append",
    dest="dataset_splits",
    choices=["dev", "regression", "holdout", "adversarial", "legacy"],
    help="Only run regression for cases in this dataset split. Can be passed multiple times.",
)
```

Call:

```python
current_report = evaluate_case_root(
    args.case_root,
    dataset_splits=set(args.dataset_splits) if args.dataset_splits else None,
)
```

- [ ] **Step 6: Run split-related tests**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py tests/test_run_quality_regression.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/scripts/evaluate_ocr_compare_quality.py backend/scripts/run_quality_regression.py backend/tests/test_evaluate_ocr_compare_quality.py backend/tests/test_run_quality_regression.py
git commit -m "feat: filter quality evaluation by dataset split"
```

---

### Task 4: Add Regression Gates for Known False Positives

**Files:**
- Modify: `backend/scripts/run_quality_regression.py`
- Modify: `backend/tests/test_run_quality_regression.py`

- [ ] **Step 1: Write failing gate test**

Add this test to `backend/tests/test_run_quality_regression.py`:

```python
def test_apply_gates_fails_on_known_false_positive_regression_count() -> None:
    current_report = {
        "aggregate": {
            "precision": 1.0,
            "recall": 1.0,
            "evidence_hit_rate": 1.0,
            "task_failure_count": 0,
            "false_positive_count": 0,
            "false_negative_count": 0,
            "known_false_positive_regression_count": 1,
        }
    }
    comparison = {"baseline_available": False, "aggregate_delta": {}}
    thresholds = DEFAULT_REGRESSION_THRESHOLDS | {
        "max_known_false_positive_regression_count": 0,
        "max_known_false_positive_regression_increase": 0,
    }

    failures = apply_gates(current_report, comparison, thresholds)

    assert {
        "gate": "max_known_false_positive_regression_count",
        "value": 1,
        "limit": 0,
    } in failures
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_run_quality_regression.py::test_apply_gates_fails_on_known_false_positive_regression_count -v
```

Expected: FAIL because the new threshold keys are unknown or not checked.

- [ ] **Step 3: Update threshold constants**

In `DEFAULT_REGRESSION_THRESHOLDS`, add:

```python
"max_known_false_positive_regression_count": 0,
"max_known_false_positive_regression_increase": 0,
```

In `COUNT_METRICS`, add:

```python
"known_false_positive_regression_count",
```

In `apply_gates`, add absolute gate:

```python
_check_max_gate(
    failures,
    "max_known_false_positive_regression_count",
    aggregate.get("known_false_positive_regression_count"),
    thresholds,
)
```

Add baseline increase gate:

```python
_check_increase_gate(
    failures,
    "max_known_false_positive_regression_increase",
    aggregate_delta.get("known_false_positive_regression_count"),
    thresholds,
)
```

- [ ] **Step 4: Run regression tests**

Run:

```bash
cd backend
python -m pytest tests/test_run_quality_regression.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/run_quality_regression.py backend/tests/test_run_quality_regression.py
git commit -m "feat: gate known false positive regressions"
```

---

### Task 5: Expose Schema 1.1 Fields in Quality Workbench

**Files:**
- Modify: `backend/app/services/quality_workbench.py`
- Modify: `backend/app/api_quality_schemas.py`
- Modify: `backend/tests/test_quality_workbench.py`
- Modify: `backend/tests/test_api_quality.py`

- [ ] **Step 1: Write failing service summary test**

Add this test to `backend/tests/test_quality_workbench.py`:

```python
def test_list_cases_includes_schema_11_metadata(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    case_dir = _make_case(case_root)
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    expected["schema_version"] = "1.1"
    expected["dataset_split"] = "regression"
    expected["case_tags"] = ["metadata", "date"]
    expected["baseline_required"] = True
    _write_json(case_dir / "expected.json", expected)

    cases = service.list_cases()

    assert cases[0]["schema_version"] == "1.1"
    assert cases[0]["dataset_split"] == "regression"
    assert cases[0]["case_tags"] == ["metadata", "date"]
    assert cases[0]["baseline_required"] is True
```

- [ ] **Step 2: Write failing patch field test**

Add this test to `backend/tests/test_quality_workbench.py`:

```python
def test_update_expected_diff_allows_schema_11_review_fields(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    _make_case(case_root)

    result = service.update_expected_diff(
        "case-001",
        2,
        {
            "review_status": "REJECTED",
            "reviewer": "legal-reviewer",
            "reviewed_at": "2026-07-01T10:00:00+08:00",
            "false_positive_reason": "header_footer",
            "should_not_match_again": True,
            "false_negative_reason": "not-used-for-rejected",
        },
    )

    diff = result["expected"]["expected_diffs"][2]
    assert diff["reviewer"] == "legal-reviewer"
    assert diff["reviewed_at"] == "2026-07-01T10:00:00+08:00"
    assert diff["false_positive_reason"] == "header_footer"
    assert diff["should_not_match_again"] is True
    assert diff["false_negative_reason"] == "not-used-for-rejected"
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py::test_list_cases_includes_schema_11_metadata tests/test_quality_workbench.py::test_update_expected_diff_allows_schema_11_review_fields -v
```

Expected: FAIL because summary and allowlist fields are missing.

- [ ] **Step 4: Update service field allowlist**

In `EXPECTED_DIFF_ALLOWED_FIELDS`, add:

```python
"reviewer",
"reviewed_at",
"false_positive_reason",
"false_negative_reason",
"should_not_match_again",
```

In `_build_summary`, add:

```python
"schema_version": str(expected.get("schema_version") or "1.0"),
"dataset_split": str(expected.get("dataset_split") or "legacy"),
"case_tags": list(expected.get("case_tags") or expected.get("tags") or []),
"baseline_required": bool(expected.get("baseline_required", False)),
```

- [ ] **Step 5: Update API schemas**

In `QualityCaseSummaryResponse`, add:

```python
schema_version: str = "1.0"
dataset_split: str = "legacy"
case_tags: list[str] = Field(default_factory=list)
baseline_required: bool = False
```

In `ExpectedDiffPatchRequest`, add:

```python
reviewer: str | None = None
reviewed_at: str | None = None
false_positive_reason: str | None = None
false_negative_reason: str | None = None
should_not_match_again: bool | None = None
```

- [ ] **Step 6: Add API patch coverage**

Add this test to `backend/tests/test_api_quality.py`:

```python
def test_update_expected_diff_accepts_schema_11_fields(client: TestClient, tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    _make_quality_case(case_root, "case-001")

    response = client.patch(
        "/api/quality/cases/case-001/expected-diffs/0",
        json={
            "review_status": "REJECTED",
            "reviewer": "legal-reviewer",
            "reviewed_at": "2026-07-01T10:00:00+08:00",
            "false_positive_reason": "header_footer",
            "should_not_match_again": True,
        },
    )

    assert response.status_code == 200
    diff = response.json()["expected"]["expected_diffs"][0]
    assert diff["reviewer"] == "legal-reviewer"
    assert diff["false_positive_reason"] == "header_footer"
    assert diff["should_not_match_again"] is True
```

If this project's API test fixture uses a different client setup, copy the existing client fixture pattern in `backend/tests/test_api_quality.py` and only change the request/assertions.

- [ ] **Step 7: Run workbench/API tests**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py tests/test_api_quality.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/quality_workbench.py backend/app/api_quality_schemas.py backend/tests/test_quality_workbench.py backend/tests/test_api_quality.py
git commit -m "feat: expose schema 1.1 gold metadata"
```

---

### Task 6: Document Schema 1.1 Golden Set Workflow

**Files:**
- Modify: `docs/golden_set_regression_sop.md`
- Modify: `docs/superpowers/specs/2026-07-01-open-source-golden-set-design.md` only if wording needs a link to this implementation phase.

- [ ] **Step 1: Add Schema 1.1 section**

Add a section after `expected.json` in `docs/golden_set_regression_sop.md`:

```markdown
### Schema 1.1 元数据

新版 `expected.json` 支持 case 级元数据：

- `schema_version`: 当前推荐为 `1.1`。
- `dataset_split`: `dev`、`regression`、`holdout`、`adversarial` 或旧数据默认 `legacy`。
- `case_tags`: 用于描述样本类型，例如 `metadata_date`、`header_footer`、`ocr_noise`。
- `baseline_required`: 是否要求进入正式回归门禁。

推荐日常导出的 draft case 先使用 `dataset_split: dev`，人工确认稳定后再改为 `regression`。
```

- [ ] **Step 2: Add negative gold section**

Add:

```markdown
### 误报作为负向 Golden Set

当某条系统输出被人工确认是误报时，标为：

```json
{
  "review_status": "REJECTED",
  "false_positive_reason": "header_footer",
  "should_not_match_again": true,
  "title_contains": "版本号"
}
```

如果后续 actual 中再次出现与该 rejected 条目相似的差异，评估器会计入 `known_false_positive_regression_count`。
```

- [ ] **Step 3: Add manual false-negative section**

Add:

```markdown
### 人工补录漏报

如果人工发现真实差异但系统未输出，应新增一条 `APPROVED` expected diff：

```json
{
  "review_status": "APPROVED",
  "diff_type": "MODIFY",
  "source_type": "metadata",
  "title_contains": "签订日期",
  "original_contains": "2026年4月 日",
  "compare_contains": "2026年4月21日",
  "false_negative_reason": "system_missed_metadata_date"
}
```

后续评估时，如果 actual 无法匹配该条目，会计入 false negative。
```

- [ ] **Step 4: Add regression commands**

Add:

```markdown
只跑正式回归集：

```bash
cd backend
python scripts/evaluate_ocr_compare_quality.py \
  tests/fixtures/ocr_compare_cases \
  --dataset-split regression
```

运行质量回归门禁：

```bash
cd backend
python scripts/run_quality_regression.py \
  tests/fixtures/ocr_compare_cases \
  --dataset-split regression
```
```

- [ ] **Step 5: Review docs**

Run:

```bash
cd backend
python -m pytest tests/test_export_ocr_compare_gold_case.py tests/test_evaluate_ocr_compare_quality.py tests/test_run_quality_regression.py tests/test_quality_workbench.py tests/test_api_quality.py -v
```

Expected: PASS. This is not because docs need tests, but to ensure the documented commands match implemented behavior.

- [ ] **Step 6: Commit**

```bash
git add docs/golden_set_regression_sop.md docs/superpowers/specs/2026-07-01-open-source-golden-set-design.md
git commit -m "docs: document schema 1.1 golden set workflow"
```

---

## Final Verification

Run:

```bash
cd backend
python -m pytest tests/test_export_ocr_compare_gold_case.py tests/test_evaluate_ocr_compare_quality.py tests/test_run_quality_regression.py tests/test_quality_workbench.py tests/test_api_quality.py -v
```

Expected: all selected tests pass.

Run:

```bash
cd backend
python -m compileall app tests scripts
```

Expected: command completes without syntax errors.

Run:

```bash
cd backend
python -m ruff check scripts/evaluate_ocr_compare_quality.py scripts/export_ocr_compare_gold_case.py scripts/run_quality_regression.py app/services/quality_workbench.py app/api_quality_schemas.py tests/test_evaluate_ocr_compare_quality.py tests/test_export_ocr_compare_gold_case.py tests/test_run_quality_regression.py tests/test_quality_workbench.py tests/test_api_quality.py
```

Expected: `All checks passed!`

---

## Acceptance Criteria

- New exported gold cases contain `schema_version: "1.1"`, `dataset_split`, `case_tags`, and `baseline_required`.
- Existing Schema 1.0 cases without new fields still evaluate.
- `APPROVED`, blank, or missing `review_status` entries remain positive gold.
- `DRAFT` entries remain excluded from core precision/recall.
- `REJECTED` entries remain excluded from positive recall.
- `REJECTED + should_not_match_again: true` entries are evaluated as negative gold.
- If actual output matches negative gold, the report includes `known_false_positive_regression_count`.
- Manual `APPROVED` entries with no `source_actual_diff_id` still count as expected diffs and produce false negatives when unmatched.
- Evaluation and regression can be filtered by `--dataset-split regression`.
- Quality workbench API can read and write Schema 1.1 review metadata.
- SOP explains how humans should mark true diffs, false positives, and false negatives.

---

## Execution Notes

- Keep all changes backward compatible.
- Do not migrate existing fixtures in bulk unless a test requires it.
- Do not delete sensitive local task artifacts or fixture directories.
- If the worktree has unrelated staged or modified files, only stage files touched by this plan.
- Use small commits after each task if the user asks for commits.
