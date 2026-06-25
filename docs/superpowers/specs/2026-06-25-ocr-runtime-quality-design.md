# OCR Runtime Quality Design

## Purpose

Phase 2 adds runtime OCR quality profiling to the contract comparison pipeline. The goal is to prevent low-quality OCR or layout recognition from silently producing high-confidence contract difference conclusions.

This phase focuses on the review loop:

- Build page-level OCR quality profiles from existing extraction diagnostics.
- Summarize task-level OCR risk.
- Propagate OCR risk to affected diffs through review flags and `NEEDS_REVIEW`.
- Expose the summary through existing task and quality APIs.
- Add lightweight frontend badges using the current audit item pattern.

This phase does not retry OCR, repair OCR output, replace the diff algorithm, or add a complex page heatmap.

## Current Context

The current pipeline already records useful signals:

- `LayoutQualityReport` and `PageLayoutQualityReport`
- `DocumentProfile` and `PageProfile`
- `ParseWarningDetail`
- `TextBlock.confidence`, `layout_match_status`, `layout_match_score`, `reading_order`, `block_role`, `flow_role`
- `DiffItem.review_flags`
- `DiffItem.quality_status`
- `CompareQualityService`
- debug artifacts under `storage/tasks/{task_id}/debug`

Phase 2 should reuse these signals instead of adding new OCR calls.

## Data Model

Add these models to `backend/app/models.py`.

### OcrQualityStatus

Status values:

- `OK`
- `LOW_TEXT_CONFIDENCE`
- `LAYOUT_MISMATCH`
- `READING_ORDER_RISK`
- `TABLE_RISK`
- `SEAL_OR_SIGNATURE_RISK`
- `UNRELIABLE`

These statuses mean "review risk", not proof that the diff is wrong.

### PageOcrQualityProfile

Fields:

- `side`: `original` or `compare`
- `page_no`: page number
- `status`: `OcrQualityStatus`
- `score`: float from 0.0 to 1.0; higher is more reliable
- `reasons`: structured reason codes
- `metrics`: page-level diagnostic counters
- `affected_diff_ids`: diff IDs mapped to this page after propagation

Recommended reason codes:

- `LOW_AVG_CONFIDENCE`
- `LOW_CONFIDENCE_BLOCK_RATIO`
- `MISSING_CHAR_CONFIDENCE`
- `MEANINGFUL_UNMATCHED_OCR`
- `LOW_LAYOUT_MATCH_RATE`
- `LAYOUT_LOW_MATCH_RATE`
- `READING_ORDER_CONFLICT`
- `TABLE_CELL_UNMATCHED`
- `TABLE_HEAVY_WITHOUT_STRUCTURE`
- `SEAL_OR_SIGNATURE_INCOMPLETE`
- `EXTRACTION_ERROR_WARNING`

### TaskOcrQualitySummary

Fields:

- `status`: highest task risk status
- `requires_review`: whether the task should be manually reviewed for OCR quality
- `page_count_by_status`: status counts
- `risk_page_count`: count of non-OK pages
- `affected_diff_count`: count of diffs touched by OCR risk propagation
- `profiles`: sorted page-level profiles

Add to `CompareTask`:

```python
ocr_quality_summary: TaskOcrQualitySummary | None = None
```

## Profiling Rules

Create `backend/app/services/ocr_quality.py` with an `OcrQualityProfiler`.

Inputs:

- `ExtractionResult.document`
- `ExtractionResult.profile`
- `ExtractionResult.layout_quality`
- `ExtractionResult.warnings`

Outputs:

- page-level profiles for one document side
- merged task-level summary for original and compare sides

The profiler must be deterministic and must not call external OCR services.

### Status Rules

`LOW_TEXT_CONFIDENCE`:

- page average OCR confidence is below `0.75`
- or low-confidence block ratio is above `20%`
- or important text blocks have no character confidence

`LAYOUT_MISMATCH`:

- `meaningful_unmatched_count > 0`
- or `matched_ocr_block_count / ocr_block_count < 0.85`
- or warning code includes `LAYOUT_LOW_MATCH_RATE`

`READING_ORDER_RISK`:

- `reading_order_conflict_count > 0`
- or later clause/page logic reports clear reading order conflict

`TABLE_RISK`:

- table cells are unmatched
- or a table-heavy page lacks structured table information
- or table repair/debug signals report low table quality

`SEAL_OR_SIGNATURE_RISK`:

- signing, seal, or non-text regions exist but structure/text recognition is incomplete

`UNRELIABLE`:

- two or more risk categories occur on the same page
- or any extraction warning has severity `ERROR`
- or OCR/layout metrics are severely missing or inconsistent

### Score

Start at `1.0` and deduct:

- `0.25` for low text confidence
- `0.25` for layout mismatch
- `0.20` for reading order risk
- `0.20` for table risk
- `0.15` for seal or signature risk

Clamp the score at `0.0`.

Status priority:

```text
UNRELIABLE > TABLE_RISK > READING_ORDER_RISK > LAYOUT_MISMATCH > LOW_TEXT_CONFIDENCE > SEAL_OR_SIGNATURE_RISK > OK
```

Thresholds should be constants in the profiler so Phase 1 golden-set metrics can tune them later.

## Pipeline Integration

Add an `OcrQualityStage` after `EvidenceStage` and before `DiffQualityStage`.

Reason:

- after `EvidenceStage`, diffs have evidence page numbers and coordinates
- before `DiffQualityStage`, OCR flags can be incorporated into normal quality processing and debug artifacts

Stage responsibilities:

1. Generate page profiles for original and compare extractions.
2. Build task-level `ocr_quality_summary`.
3. Map risk pages to diffs via evidence page numbers.
4. Append OCR review flags to affected diffs.
5. Set `quality_status = NEEDS_REVIEW` when risk severity requires it.
6. Backfill `affected_diff_ids` on page profiles.
7. Write `ocr_quality.json` through `CompareDebugWriter` or a focused writer helper.
8. Add `ocr_quality` to `task.debug_artifact_paths`.
9. Add page-level `ParseWarningDetail` entries for risk pages.

## Diff Risk Propagation

Risk mapping:

- `original_evidence.page_no` maps to original-side profiles
- `compare_evidence.page_no` maps to compare-side profiles
- diffs without evidence are marked `EVIDENCE_UNRELIABLE` if the task has OCR risk pages and the diff came from clause, page, table, or metadata flow

Review flags:

- `OCR_LOW_CONFIDENCE`
- `PAGE_UNRELIABLE`
- `LAYOUT_MISMATCH_RISK`
- `READING_ORDER_RISK`
- `TABLE_STRUCTURE_UNRELIABLE`
- `SEAL_OR_SIGNATURE_RISK`
- `EVIDENCE_UNRELIABLE`

`quality_status` rules:

- `UNRELIABLE`, `TABLE_RISK`, and `READING_ORDER_RISK` force `NEEDS_REVIEW`
- `LAYOUT_MISMATCH` forces `NEEDS_REVIEW` when evidence lands on the affected page
- `LOW_TEXT_CONFIDENCE` adds a flag, but only forces `NEEDS_REVIEW` when the diff contains business-critical tokens such as amount, date, party, payment, delivery, liability, termination, or breach terms
- missing evidence with OCR risk forces `NEEDS_REVIEW`

Risk propagation must not delete diffs, alter diff text, or change evidence coordinates.

## API Output

Add `ocr_quality_summary` to:

- `CompareTaskResponse`
- `CompareTaskDetailResponse`
- frontend `CompareResponse` / `CompareTask`

Add to `CompareQualityService.build_summary()`:

- `ocr_quality_summary`
- `ocr_risk_page_count`
- `ocr_affected_diff_count`

The existing `debug_artifact_paths` response should include `ocr_quality` once the artifact is written.

## Frontend Scope

Update `frontend/src/types.ts` with OCR quality types.

Result page first version:

- reuse existing audit badge patterns
- show `OCR_LOW_CONFIDENCE` as "低置信 OCR"
- show `PAGE_UNRELIABLE` as "页面不可靠"
- show `TABLE_STRUCTURE_UNRELIABLE` as "表格识别风险"
- optionally show risk page count and affected diff count in the quality panel if `/quality` is loaded

Do not add:

- OCR retry buttons
- page heatmap
- new page navigator
- a separate OCR diagnostics screen

## Testing

Backend tests:

- `OcrQualityProfiler` creates `LOW_TEXT_CONFIDENCE` from low page confidence
- layout unmatched signals create `LAYOUT_MISMATCH`
- reading order conflicts create `READING_ORDER_RISK`
- table cell unmatched signals create `TABLE_RISK`
- multiple risks create `UNRELIABLE`
- `OcrQualityStage` maps risk pages to diff evidence pages
- severe risk flags set `quality_status = NEEDS_REVIEW`
- low confidence plus business-critical tokens sets `NEEDS_REVIEW`
- missing evidence with OCR risk sets `EVIDENCE_UNRELIABLE`
- `CompareTaskResponse` exposes `ocr_quality_summary`
- `/api/compare/{task_id}/quality` exposes `ocr_quality_summary`, `ocr_risk_page_count`, and `ocr_affected_diff_count`
- `ocr_quality.json` debug artifact is written

Frontend tests:

- OCR review flags render expected badges
- `CompareQualitySummary` parses OCR quality fields
- existing result page tests keep passing

Verification:

```bash
cd backend
python -m pytest tests/test_ocr_quality.py tests/test_api.py tests/test_pipeline.py
python -m pytest
python -m ruff check .
cd ../frontend
npm test
npm run build
```

Phase 1 evaluator should continue to pass the smoke gate:

```bash
cd backend
python scripts/evaluate_ocr_compare_quality.py tests/fixtures/ocr_compare_cases \
  --output .ocr-compare-quality/ocr_compare_quality.json \
  --html-output .ocr-compare-quality/html \
  --fail-on-threshold
```

## Acceptance Criteria

- OCR quality profiles are generated without calling OCR again.
- Risk pages are visible in `ocr_quality.json`.
- Affected diffs receive OCR-specific review flags.
- Severe OCR risk changes affected diffs to `NEEDS_REVIEW`.
- `/api/compare/{task_id}/quality` reports OCR risk page count and affected diff count.
- Existing public API paths remain compatible.
- Backend and frontend tests pass.
- Phase 1 evaluator still passes the smoke gate.

## Out Of Scope

- OCR retry or repair.
- PP-OCRv6 or PP-StructureV3 shadow routing.
- LLM/VLM-based OCR correction.
- Rewriting diff text based on OCR quality.
- Complex page heatmaps or a new diagnostics UI.

