# E9 Visual False Positive Suppression Design

## Purpose

Reduce false positives found by page-by-page visual review of task `e9debd86-ada8-45d6-a810-bf1185033fc7` while preserving real handwriting, seal, date, amount, and option-selection differences.

The confirmed false positives come from four root causes:

- Low-coverage `split_original` / `contained_original` matches that compare a short original slice against a larger compare clause.
- OCR text exists visually on both pages, but the final OCR/clause text is missing a heading or splits continuous page content into non-contiguous diff snippets.
- Form lines, slashes, underlines, and table column separators are serialized differently by OCR.
- Seal/signature regions generate isolated OCR fragments that do not represent standalone textual differences.

## Non-Goals

- Do not hardcode task IDs, page numbers, diff IDs, names, or contract-specific wording.
- Do not suppress real handwriting, seals, signatures, dates, amounts, percentages, party names, or option changes.
- Do not replace the existing clause matcher or rewrite OCR extraction.
- Do not introduce a full visual diff engine in this iteration.
- Do not auto-suppress uncertain right-edge crop cases such as D066/D067 without stronger evidence.

## Confirmed Target False Positives

These are the expected behaviors for this design:

- D011: suppress metadata deletion where `1. 定义` exists visually on both sides.
- D093: suppress `1.9` compare-side additions caused by a low-coverage split of an existing clause.
- D094: suppress `2. 服务内容` and service-content additions caused by OCR heading loss and cross-page clause drift.
- D113: suppress existing payment text that was cut into a compare-side ADD.
- D036: suppress form slash / underline / punctuation OCR differences when the visual form fields are equivalent.
- D053: suppress filled copy-count text differences when the same values are present visually.
- D098: suppress `(以下无正文)` deletion when the text exists on both sides.
- D020: suppress or downgrade signing-table label/company text differences caused by seal occlusion when the material text remains visible.
- D091: suppress a large false delete where the safety agreement cover/body exists on both sides but clause matching drifted.
- D027: suppress isolated seal-area OCR fragment `图` that has no independent visual text.
- D023: suppress table header serialization differences where both headers exist visually.

The following related diffs must remain:

- D050: `第一种方式` versus `第二种方式` is a real option change.
- D032: amount-in-words differs visually and remains critical.
- D073 and D082: real textual/symbol changes.
- D018 and D002-D008: compare-side handwriting/footer annotations are visually present.
- D021/D022/D024-D026/D028/D029/D090: signature, seal, or date additions are visually real.

## Architecture

Add conservative quality-stage guards around the existing `DiffQualityProcessor` and `ClauseBoundaryCoverageFilter`.

```text
DiffQualityProcessor
  -> low-value / OCR noise checks
  -> ClauseBoundaryCoverageFilter
       -> PageCoverageIndex
       -> StructuralDriftCoverageGuard
       -> FormAndTableVisualNoiseGuard
       -> SealArtifactNoiseGuard
```

The design keeps suppression in post-diff quality filtering. Matcher behavior remains intact, but low-coverage split matches are treated as risky evidence that requires stronger page coverage before reporting a diff.

## PageCoverageIndex

Build a per-side index from `Document.pages`:

- `page_text_key`: normalized full page text.
- `line_keys`: normalized block/line keys.
- `short_heading_keys`: headings such as `2服务内容`, `以下无正文`, `1定义`.
- `bare_number_keys`: page lines like `2.` that can pair with nearby child/body text.
- Optional `neighbor_pages`: same page plus previous/next page.

Normalization should be consistent with existing `normalize_for_coverage`:

- NFKC normalization.
- Remove whitespace, layout punctuation, and separator noise.
- Normalize Chinese and ASCII punctuation.
- Keep protected values detectable before removing all punctuation.

For non-contiguous snippets, support fragment coverage:

- Split changed snippets by OCR evidence boxes, line breaks, clause markers, and punctuation boundaries.
- Drop very short noise fragments unless they are headings.
- Treat the changed text as covered when all material fragments appear on the opposite same/neighbor page or in the opposite clause window.

## StructuralDriftCoverageGuard

Apply only to structurally risky clause diffs:

- `source_type == "clause"`.
- `diff_type in {"ADD", "DELETE", "MODIFY"}`.
- At least one of:
  - `LOW_COVERAGE_MATCH_REVIEW`
  - `PARTIAL_CLAUSE_MATCH`
  - `POSSIBLE_SPLIT_CLAUSE`
  - `POSSIBLE_MERGED_CLAUSE`
  - `TEXT_FOUND_IN_OTHER_CLAUSE`
  - `READING_ORDER_RISK`
  - `READING_ORDER_REPAIRED`
  - `PARAGRAPH_MERGED`

Suppress when:

- The changed fragments are fully covered by opposite same/neighbor page text, even if the original snippet is empty.
- A short heading exists on one side while the opposite page has a bare parent number and matching body/child continuation.
- A low-coverage split pair reports compare text that is already covered by the unsplit parent clause or opposite page.

Do not suppress when:

- Uncovered protected values remain.
- The diff contains an actual option marker change such as `第一` vs `第二`.
- Amount-in-words, numeric amounts, dates, percentages, contract numbers, party names, or liability terms differ materially.

Expected debug decision:

```json
{
  "action": "suppressed_by_neighbor_clause_coverage",
  "diff_id": "D093",
  "detail": {
    "reason": "low_coverage_split_page_fragment_covered",
    "coverage_source": "opposite_page",
    "fragments_checked": 8
  }
}
```

## FormAndTableVisualNoiseGuard

Handle OCR-only differences from layout serialization.

Eligible diffs:

- `source_type in {"clause", "table"}`.
- Has table/form/repaired reading flags or table region review flags.
- Changed text is mostly labels, short headings, separators, slashes, underlines, or duplicated table headers.

Rules:

- Suppress slash/underline/form-line-only changes when both sides contain the same surrounding option labels and no protected value differs.
- Suppress table header concatenation differences when both side page/table text contains the same header tokens, regardless of whitespace or vertical separator loss.
- For signing/contact tables, suppress label/company-name OCR changes caused by seal occlusion only when both company names or labels remain recoverable from the row/page context.
- Keep signature, date, seal, and handwriting additions as reviewable/real differences.

Expected debug reasons:

- `form_separator_equivalent`
- `table_header_serialization_equivalent`
- `seal_occluded_signing_label_covered`

## SealArtifactNoiseGuard

Handle isolated OCR fragments inside seal/signature regions.

Suppress only when all are true:

- Changed text is a short low-information fragment, such as one CJK character or OCR glyph.
- Evidence box overlaps or is adjacent to a seal/signature/table-title region.
- The fragment has no corresponding standalone visual text outside the seal/signature artifact.
- No date, company name, contract number, or signature value is being removed.

This suppresses D027-like `图` noise while preserving real seal diffs and signature/date additions.

## Data Flow

1. Build `BoundaryCoverageContext` with clauses and original/compare documents.
2. Construct `PageCoverageIndex` lazily inside the boundary coverage filter.
3. For each diff, run existing trimming and suppression checks.
4. Run the new guards in this order:
   - `StructuralDriftCoverageGuard`
   - `FormAndTableVisualNoiseGuard`
   - `SealArtifactNoiseGuard`
5. Emit a debug decision for every suppression or downgrade.
6. Return filtered diffs with real differences unchanged.

## Error Handling

- If page text is unavailable, skip page coverage suppression and keep the diff.
- If evidence pages are missing, use clause page numbers; if those are missing too, keep the diff.
- If a guard finds both covered noise and uncovered protected value changes, keep the diff and add a review flag instead of suppressing.
- If OCR quality is unreliable but visual-risk signals indicate seal/signature/date additions, keep the diff.

## Testing

Add unit tests in `backend/tests/test_text_cleaning_quality.py` or adjacent quality tests:

- `split_original` low-coverage existing clause text is suppressed.
- Non-contiguous changed fragments are covered by opposite page text.
- Short heading with bare opposite number and matching body is suppressed.
- `(以下无正文)` false delete is suppressed.
- Form slash/underline equivalent change is suppressed.
- Table header serialization equivalent change is suppressed.
- Signing table label/company text occluded by seal is suppressed or downgraded without hiding signature/date additions.
- Isolated seal-region one-character OCR fragment is suppressed.

Add protection tests:

- `第一` vs `第二` option changes remain.
- Amount-in-words changes remain.
- Seal/signature/date additions remain.
- Contract numbers, party names, phone numbers, percentages, dates, and amounts are not suppressed unless normalized values are equal.
- D066/D067-style edge-cropped text remains `NEEDS_REVIEW` rather than suppressed.

## Verification

Run:

```bash
uv run pytest backend/tests/test_text_cleaning_quality.py -q
uv run pytest backend/tests/test_table_compare_structured.py backend/tests/test_matcher_optimization.py -q
uv run ruff check backend/app/services/diff_quality.py backend/app/services/diff/boundary_coverage.py backend/tests/test_text_cleaning_quality.py
uv run python -m compileall backend/app backend/tests
uv run pytest backend/tests -q
```

Then rerun a task-level spot check for `e9debd86-ada8-45d6-a810-bf1185033fc7` and verify:

- Confirmed target false positives are removed or downgraded as designed.
- Real handwriting, seal, date, amount, and option differences remain.
- Debug decisions explain every suppression reason.

## Rollout

1. Implement the index and guards behind existing quality processing.
2. Keep decisions conservative and tied to structural-risk flags.
3. Run targeted and full tests.
4. Reprocess the affected task and compare kept/suppressed diff IDs.
5. Use the results to decide whether to broaden thresholds in later tasks.
