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

Generated expected diffs are marked `DRAFT`. Generated drafts are not trusted gold and are not counted as gold until a human changes the relevant entries to `APPROVED`.

## Review `expected.json`

For each generated `expected_diffs` entry:

1. Check the original and compare PDFs.
2. Delete entries that are not real differences.
3. Edit `title_contains`, `original_contains`, and `compare_contains` so they contain stable reviewed text.
4. Add missing expected differences that the system did not detect.
5. Change validated entries to `"review_status": "APPROVED"`.
6. Keep uncertain entries as `"review_status": "DRAFT"` or mark them `"REJECTED"`.

## Required Matching Fields

Use these fields for stable matching:

- `diff_type`: `ADD`, `DELETE`, or `MODIFY`.
- `source_type`: `metadata`, `clause`, `table`, `seal`, or `header_footer`.
- `title_contains`: reviewed title fragment.
- `original_contains`: reviewed original-side fragment.
- `compare_contains`: reviewed compare-side fragment.

Each approved expected diff should include the matching fields that apply to that diff. For example, `ADD` entries usually require `compare_contains`, while `DELETE` entries usually require `original_contains`.

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

JSON reports are best for automation, threshold checks, and comparing exact metric values across runs. HTML reports are best for human review because they group case summaries, matching details, false positives, false negatives, and evidence drift into browsable sections.

## Sensitive Data

Do not commit sensitive real contracts, local PDFs, customer data, extracted `actual.json`, or other unapproved extracted payloads. Snippets in `expected.json`, including `title_contains`, `original_contains`, and `compare_contains`, may contain real contract content and must not be committed unless explicitly approved. Keep sensitive `original.pdf`, `compare.pdf`, and exported real task cases local unless they have been approved for version control.
