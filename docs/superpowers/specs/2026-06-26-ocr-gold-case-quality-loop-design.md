# OCR Gold Case Quality Loop Design

## Purpose

Phase 4B turns ad-hoc contract comparison runs into a repeatable quality measurement loop. The current system can complete comparison tasks, produce OCR quality profiles, generate remediation plans, emit model routing recommendations, and show quality artifacts. The missing product capability is a reliable way to convert real runs into human-reviewed gold cases and use them to measure whether future OCR, routing, parsing, and diff changes improve or regress quality.

This phase should make quality evaluation operational: export a task into a reviewable case, let a human mark the expected diffs, run the evaluator, and inspect precision, recall, false positives, false negatives, evidence drift, OCR risk, and routing metrics in JSON and HTML reports.

## Goals

- Add a script that exports an existing compare task into an OCR comparison gold-case draft.
- Keep human review explicit: generated `expected.json` entries must be marked as draft until reviewed.
- Preserve sensitive-contract safety by keeping PDFs and raw real customer artifacts out of Git by default.
- Extend the OCR comparison evaluator with clearer per-case matching details.
- Improve the HTML quality report so users can see missed expected diffs, unexpected actual diffs, evidence drift, OCR risk, and model routing context.
- Document the end-to-end gold-case workflow for local use and committed deterministic fixtures.
- Preserve existing `/api/compare/*` and `/api/extract/*` behavior.

## Non-Goals

- Do not execute OCR retries or change the default compare pipeline.
- Do not tune OCR, layout, model routing, or diff logic in this phase.
- Do not create a frontend annotation UI.
- Do not require committed real contracts, sensitive PDFs, or production task storage.
- Do not treat system-generated diffs as gold without human review.
- Do not migrate existing fixture formats in a breaking way.
- Do not add a database dependency for annotation storage.

## Product Behavior

A user who has run a real comparison task should be able to create a gold-case draft from local task storage:

```bash
cd backend
python scripts/export_ocr_compare_gold_case.py \
  ../storage/tasks/<task_id> \
  tests/fixtures/ocr_compare_cases/<case_id>
```

The exporter should create:

```text
tests/fixtures/ocr_compare_cases/<case_id>/
  actual.json
  expected.json
  README.md
```

`actual.json` is a copy of the `CompareTask` payload. `expected.json` is a human-review draft built from actual diffs so the reviewer can approve, edit, or remove entries. The draft must make it obvious that the file is not yet a trusted gold case.

After human review, the user should run:

```bash
cd backend
python scripts/evaluate_ocr_compare_quality.py tests/fixtures/ocr_compare_cases \
  --output .ocr-compare-quality/ocr_compare_quality.json \
  --html-output .ocr-compare-quality/html \
  --fail-on-threshold
```

The evaluator should report case-level and aggregate quality in a way that answers:

- Which expected diffs were found?
- Which expected diffs were missed?
- Which actual diffs were unexpected?
- Which matched diffs have evidence drift?
- Which actual diffs were low confidence or OCR-risk affected?
- Which model routing recommendations apply to the risky pages?

## Gold Case Directory Contract

Each case directory contains a stable set of review artifacts:

```text
<case_id>/
  expected.json
  actual.json
  README.md
  original.pdf       # optional local-only file
  compare.pdf        # optional local-only file
```

Required for evaluator use:

- `expected.json`
- `actual.json`

Recommended for humans:

- `README.md`

Local-only and untracked for sensitive cases:

- `original.pdf`
- `compare.pdf`

The exporter should not copy PDFs by default. If a later option adds PDF copying, it must be explicit and documented as unsafe for sensitive contracts.

## `expected.json` Shape

The existing evaluator fields remain supported:

```json
{
  "case_id": "case_218b3d1b",
  "tags": ["real_contract", "cover_metadata", "seal_page"],
  "critical_fields": [
    {
      "field_id": "sign_date",
      "label": "签订日期",
      "expected_original": "2024年4月1日",
      "expected_compare": "2024年4月21日",
      "page_no": 1
    }
  ],
  "expected_diffs": [
    {
      "diff_type": "MODIFY",
      "source_type": "metadata",
      "title_contains": "签订日期",
      "original_contains": "2024年4月1日",
      "compare_contains": "2024年4月21日",
      "expected_evidence": [
        {
          "side": "original",
          "page_no": 1,
          "bbox": { "x0": 100, "y0": 200, "x1": 260, "y1": 230 }
        }
      ],
      "review_status": "APPROVED",
      "severity": "critical",
      "notes": "人工确认签订日期变化。"
    }
  ],
  "quality_expectations": {
    "max_task_failures": 0,
    "min_recall": 0.9,
    "max_false_positive_count": 2,
    "min_evidence_hit_rate": 0.8
  }
}
```

New optional fields:

- `review_status`: `DRAFT`, `APPROVED`, or `REJECTED`.
- `severity`: `critical`, `major`, or `minor`.
- `notes`: human-readable review note.
- `source_actual_diff_id`: actual diff ID that seeded the draft entry.

Evaluator matching should only treat entries as expected gold diffs when `review_status` is missing or equal to `APPROVED`. Draft entries can remain in the file for reviewer convenience, but they should not inflate recall or false-negative counts. This keeps generated drafts safe: they are useful starting points but not truth.

## Exporter Design

Create `backend/scripts/export_ocr_compare_gold_case.py`.

Responsibilities:

- Accept a task directory path and output case directory path.
- Load `task.json`.
- Validate the task is a compare task payload with a task ID and `diffs`.
- Write `actual.json` using the task payload.
- Write a draft `expected.json` generated from actual diffs.
- Write a `README.md` with file names, task ID, run timestamp, OCR risk summary, and review instructions.
- Refuse to overwrite an existing reviewed `expected.json` unless `--force` is provided.

Draft `expected_diffs` should include:

- `diff_type`
- `source_type`
- `title_contains`
- `original_contains`
- `compare_contains`
- `source_actual_diff_id`
- `review_status: "DRAFT"`
- `severity`
- `notes`

Draft severity should be deterministic:

- `critical` when review flags include `CRITICAL_VALUE_CHANGE`, source type is `metadata` for key fields, or source type is `seal`.
- `major` when quality status is `NEEDS_REVIEW` or OCR review flags are present.
- `minor` otherwise.

Draft snippets should be short enough to review and stable enough to match:

- Use the existing diff title for `title_contains`.
- Use concise normalized substrings from `original_text` and `compare_text`.
- If text is absent, omit the corresponding field instead of writing an empty string.

The exporter should not try to infer missed expected diffs. Humans add those manually.

## Evaluator Enhancements

Extend `backend/scripts/evaluate_ocr_compare_quality.py` without breaking existing fixture cases.

### Review Status Filtering

Before matching, normalize expected diffs:

- Include entries with no `review_status` for backwards compatibility.
- Include entries with `review_status == "APPROVED"`.
- Exclude `DRAFT` and `REJECTED` from metrics.

Excluded draft/rejected entries can be counted in a non-blocking `annotation_summary`.

### Matching Details

Add structured case output:

```json
{
  "matches": [
    {
      "expected_index": 0,
      "actual_index": 2,
      "actual_diff_id": "D003",
      "score": 0.95,
      "evidence_hit": true
    }
  ],
  "missed_expected_diffs": [
    {
      "expected_index": 1,
      "label": "付款期限",
      "diff_type": "MODIFY",
      "source_type": "clause"
    }
  ],
  "unexpected_actual_diffs": [
    {
      "actual_index": 3,
      "diff_id": "D004",
      "title": "封面额外文本",
      "source_type": "metadata",
      "quality_status": "NEEDS_REVIEW",
      "review_flags": ["POSSIBLE_COVER_OCR_FRAGMENT"]
    }
  ],
  "evidence_drift_diffs": [
    {
      "expected_index": 0,
      "actual_diff_id": "D003",
      "label": "签订日期"
    }
  ]
}
```

Existing `issues` strings should remain for compatibility, but the new structured lists should drive the HTML report.

### Annotation Summary

Add per-case annotation summary:

```json
{
  "annotation_summary": {
    "approved_expected_count": 5,
    "draft_expected_count": 2,
    "rejected_expected_count": 1
  }
}
```

Aggregate output should include summed annotation counts.

### Route Metrics

Keep existing route metrics. Do not attempt route precision or route recall until there is a separate reviewed route attribution model. Existing maps should remain empty:

- `precision_by_recommendation`
- `recall_by_recommendation`
- `evidence_hit_rate_by_recommendation`
- `low_confidence_ratio_by_recommendation`

## HTML Report Enhancements

Enhance the existing evaluator HTML output.

Index page should show:

- Aggregate precision, recall, false positives, false negatives, evidence hit rate.
- Annotation summary: approved, draft, rejected.
- Route recommendations summary.
- Case table columns for:
  - expected count
  - actual count
  - recall
  - precision
  - false positives
  - false negatives
  - evidence drift count
  - low confidence count
  - retry routes
  - manual routes

Case pages should show:

- OCR warning count and OCR risk counts.
- Matched diffs table.
- Missed expected diffs table.
- Unexpected actual diffs table.
- Evidence drift table.
- Existing model routing list.
- Existing raw issues list as a fallback.

All strings derived from task data or annotations must be escaped with `html.escape`.

## Documentation

Create `docs/ocr_compare_gold_case_workflow.md`.

The document should explain:

- What a gold case is.
- How to export a case from `storage/tasks/<task_id>`.
- How to edit `expected.json`.
- Which fields are required for matching.
- How to add `expected_evidence`.
- How to run the evaluator.
- How to read JSON and HTML reports.
- How to avoid committing sensitive PDFs or real contract content.

The document should explicitly state that a generated draft is not a trusted gold case until a human changes relevant entries to `APPROVED`.

## Testing Strategy

Use TDD for each behavior.

Backend tests should cover:

- Exporter creates `actual.json`, draft `expected.json`, and `README.md`.
- Exporter refuses to overwrite reviewed expected annotations without `--force`.
- Draft expected diffs are marked `DRAFT`.
- Evaluator excludes `DRAFT` and `REJECTED` entries from precision and recall.
- Evaluator includes legacy expected diffs with missing `review_status`.
- Evaluator emits structured `matches`, `missed_expected_diffs`, `unexpected_actual_diffs`, and `evidence_drift_diffs`.
- Evaluator aggregate includes annotation summary.
- HTML report renders the new sections and escapes unsafe text.

Recommended commands:

```bash
cd backend
python -m pytest tests/test_export_ocr_compare_gold_case.py -v
python -m pytest tests/test_evaluate_ocr_compare_quality.py -v
python -m ruff check scripts/export_ocr_compare_gold_case.py scripts/evaluate_ocr_compare_quality.py tests/test_export_ocr_compare_gold_case.py tests/test_evaluate_ocr_compare_quality.py
python -m compileall app tests scripts
```

Final verification should include:

```bash
cd backend
python -m pytest
python -m ruff check .
cd ../frontend
npm test
npm run build
```

## Risks And Mitigations

Risk: Users may treat generated drafts as gold.

Mitigation: Draft entries use `review_status: "DRAFT"` and are excluded from metrics. README and workflow docs explain the review gate.

Risk: Sensitive real contracts or task payloads may be committed.

Mitigation: Exporter does not copy PDFs by default. Documentation warns about sensitive content. The repository guidance already says not to commit sensitive contracts.

Risk: New evaluator fields break existing reports.

Mitigation: Keep existing fields and `issues` output. Add fields as optional extensions. Existing fixture cases with no `review_status` remain valid.

Risk: Matching details expose unstable indexes.

Mitigation: Include both indexes and stable labels such as `actual_diff_id`, `title`, `source_type`, and `diff_type`.

Risk: Case export grows into a full annotation product.

Mitigation: This phase remains file-based. No frontend annotation UI, database, or collaborative review workflow.

## Acceptance Criteria

- A user can export a completed task into a gold-case draft directory.
- Draft expected diffs are not counted as gold until approved.
- Legacy fixture cases still evaluate successfully.
- Approved expected diffs produce precision, recall, false-positive, false-negative, and evidence-hit metrics.
- HTML reports show matched, missed, unexpected, and evidence drift details.
- Documentation explains the workflow end to end.
- No public compare or extract API behavior changes.
- Full backend and frontend verification passes.
