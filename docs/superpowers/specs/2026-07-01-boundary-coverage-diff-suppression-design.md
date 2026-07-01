# Boundary Coverage Diff Suppression Design

## Purpose

Reduce false positives caused by clause boundary drift and reading-order repair without introducing a formal signature-page detector or a new signature-page diff type.

This targets cases where the same content exists in both documents, but the clause splitter assigns it to different neighboring clauses or page contexts. The current examples are:

- An appendix heading such as `附件一:` exists on both sides, but only one side becomes an independent appendix clause.
- Two-column signing/contact information exists on both sides, but left-column and right-column fields are split into different neighboring clauses.
- Critical identifiers such as unified social credit codes are visually identical after cross-line joining, but OCR/clause text has different line breaks.

## Non-Goals

- Do not introduce a public `signature` source type.
- Do not hardcode task IDs, person names, addresses, phone numbers, emails, or contract-specific text.
- Do not try to fully identify or parse signing pages.
- Do not change normal clause matching for low-risk main-body clauses.

## Scope

Add a conservative post-diff coverage check for clause diffs that already show structural risk.

Eligible diffs:

- `source_type == "clause"`.
- The diff has at least one of:
  - `READING_ORDER_RISK`
  - `READING_ORDER_REPAIRED`
  - `PARAGRAPH_MERGED`
  - `LOW_COVERAGE_MATCH_REVIEW`
  - `POSSIBLE_SPLIT_DRIFT`
- Or the diff is a short appendix heading add/delete where `section_type == "appendix"`.

Ineligible diffs:

- Ordinary main-contract clause diffs without structural risk.
- Diffs where the changed value is not covered by the opposite-side neighboring context.
- True critical value changes, such as a phone number or credit code that normalizes to a different value.

## Architecture

Add a clause-boundary coverage filter in the diff quality stage or an adjacent post-processing component.

Suggested component:

```text
ClauseBoundaryCoverageFilter
```

Inputs:

- Current `DiffItem` list.
- Original clause list.
- Compare clause list.
- Optional page/block text context if available from debug or pipeline state.

Outputs:

- Filtered or downgraded `DiffItem` list.
- Debug decisions explaining each suppression or downgrade.

The filter should be invoked after normal diff construction and before final OCR remediation flags are reported.

## Data Flow

1. Receive a clause diff.
2. Check whether it is eligible by source type and risk flags.
3. Build an opposite-side coverage window:
   - Matched clause.
   - Previous and next 1-2 clauses by `order_index`.
   - Same-page OCR text if available.
4. Extract changed fragments from the diff:
   - For add/delete: added or deleted text.
   - For modify: snippets and field-like changed segments.
5. Normalize fragments and coverage text.
6. If all material changed fragments are covered in the opposite-side window, suppress the diff.
7. If only part is covered, keep the diff but downgrade critical flags and add a review flag.

## Normalization

Use lightweight field normalization:

- General text: NFKC, remove whitespace/newlines, normalize punctuation.
- Appendix heading: normalize `附件一:` and `附件一：` to the same key.
- Email: lowercase, remove spaces around `@` and dots.
- Phone/fax: keep digits, hyphen, plus sign; compare canonical digits where appropriate.
- Unified social credit code: remove whitespace/newlines and compare continuous alphanumeric values.

Example:

```text
9111010867
23891430
```

and

```text
91110108672
3891430
```

both normalize to:

```text
911101086723891430
```

## Suppression Rules

### Full Coverage

Suppress the diff when all material changed fragments are found in the opposite-side coverage window after normalization.

Record a debug decision:

```json
{
  "action": "suppressed_by_neighbor_clause_coverage",
  "diff_id": "...",
  "detail": {
    "covered_fragments": ["..."],
    "window": {"before": 2, "after": 2}
  }
}
```

### Partial Coverage

If some fragments are covered but others remain unmatched:

- Keep the diff.
- Remove `CRITICAL_VALUE_CHANGE` only if no uncovered protected value remains.
- Add `BOUNDARY_COVERAGE_REVIEW`.
- Set `quality_status = "NEEDS_REVIEW"`.

### No Coverage

Leave the diff unchanged.

## Safety Constraints

- Never suppress a protected value change unless both normalized values are equal.
- Protected values include:
  - phone numbers
  - fax numbers
  - email addresses
  - unified social credit codes
  - money amounts
  - dates
  - percentages
- Require structural-risk flags before applying neighbor coverage to normal main-contract clauses.
- Keep all suppression decisions in debug output.

## Expected Behavior

### Appendix Heading

If `附件一:` is deleted from one side but the opposite side has the same heading in same-page OCR/table context, suppress the diff.

### Two-Column Contact Fields

If `联系人:环加飞`, `电话:010-83582793`, or `传真:010-83582600` appears as added text only because it belongs to a neighboring original clause, suppress that portion of the diff.

### Cross-Line Credit Code

If both sides normalize to the same unified social credit code, suppress the credit-code change.

### True Change

If a phone number or credit code normalizes differently and is not covered in the opposite-side window, keep the diff and preserve critical review flags.

## Testing

Add regression tests for:

- Appendix heading exists on both sides but only one side is an independent clause.
- Signing/contact fields are split across neighboring clauses on one side and merged on the other.
- Unified social credit code line break differs but normalized value is equal.
- True phone number change is not suppressed.
- Ordinary low-risk body clause diffs do not trigger neighbor coverage suppression.

## Rollout

1. Implement behind the existing diff quality processing path.
2. Emit debug decisions for every suppression/downgrade.
3. Run targeted regression tests.
4. Re-run the affected task and inspect D015/D016.
5. Keep the behavior limited to structurally risky clause diffs until more golden-set evidence supports broader use.
