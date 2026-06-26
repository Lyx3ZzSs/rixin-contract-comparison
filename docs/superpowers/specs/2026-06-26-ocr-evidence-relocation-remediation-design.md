# OCR Evidence Relocation Remediation Design

## Purpose

Phase 3B turns the planned `RELOCATE_EVIDENCE` remediation action into a deterministic self-healing step. Phase 3A can already detect OCR-risk diffs, create remediation actions, expose them through API responses, and show compact frontend badges. Phase 3B should execute only the safest remediation action first: evidence re-location.

The goal is to improve evidence confidence and highlight reliability without changing the diff text, suppressing diffs, retrying OCR, or repairing tables.

## Goals

- Execute `RELOCATE_EVIDENCE` actions for diffs marked with `EVIDENCE_UNRELIABLE`.
- Re-locate evidence using existing deterministic text and coordinate signals.
- Update evidence only when the replacement is higher confidence and semantically tied to the same diff side.
- Record before/after evidence quality in `OcrRemediationAction`.
- Mark successful actions as `SUCCEEDED`; failed actions remain unresolved and keep manual review signals.
- Preserve `/api/compare/*` and `/api/extract/*` compatibility.
- Keep remediation debug artifacts and quality summaries consistent after merge and final dedupe.

## Non-Goals

- Do not retry OCR or call external OCR/model services.
- Do not repair table structure.
- Do not rewrite `original_text`, `compare_text`, snippets, change ranges, or readable change text.
- Do not hide, delete, or suppress diffs.
- Do not add manual feedback endpoints in this phase.
- Do not build a new frontend review workflow beyond existing remediation status display.

## Product Behavior

For a diff with a planned `RELOCATE_EVIDENCE` action:

1. The remediation stage attempts evidence re-location on the action side and page.
2. If stronger evidence is found, the stage replaces only the affected side's evidence.
3. The action becomes `SUCCEEDED`.
4. `changed_evidence` becomes `true`.
5. `after_quality` records the new evidence method, confidence, page, and count.
6. The diff keeps its text and remains visible.
7. If the action fails, the action becomes `FAILED` or remains unresolved according to the failure reason, and the diff stays `NEEDS_REVIEW`.

This phase should be conservative. No evidence update is better than a plausible but wrong highlight.

## Action Eligibility

An action is eligible for execution when all of these are true:

- `action_type == "RELOCATE_EVIDENCE"`
- `status == "PLANNED"`
- `diff_id` maps to an existing diff after current remapping rules
- `side` is `original` or `compare`
- the diff has a usable snippet or change range for that side
- the target page is absent or matches the candidate page

The action should be skipped when:

- the side is unknown
- the diff no longer exists
- the diff has no side-specific text signal
- the existing evidence is already high confidence
- the candidate crosses to an unrelated page
- the candidate does not improve evidence confidence

## Architecture

Keep the existing pipeline position:

```text
Evidence
→ OCRQuality
→ OCRRemediation
→ DiffQuality
→ Visualization
→ Summary
```

Phase 3B expands `OcrRemediationStage` from planning-only to planning plus deterministic execution:

1. Build or reuse remediation actions from `OcrRemediationPlanner`.
2. Execute eligible `RELOCATE_EVIDENCE` actions.
3. Update action status and before/after quality.
4. Update diff review flags and quality status.
5. Write `ocr_remediation.json`.
6. Let `DiffQualityStage` and `SummaryStage` continue to preserve/remap remediation metadata.

The execution path should be isolated in a new service so the stage remains orchestration code.

## Backend Components

### `evidence_relocator.py`

Create `backend/app/services/evidence_relocator.py`.

Responsibilities:

- Accept a diff, side, optional page number, and source documents/PDF paths.
- Build candidate evidence using existing deterministic signals.
- Score candidates against current evidence.
- Return a structured relocation result.
- Avoid mutating the diff directly; the stage applies accepted results.

Suggested public API:

```text
EvidenceRelocator.relocate(diff, side, page_no, original_pdf, compare_pdf) -> EvidenceRelocationResult
```

The result should include:

- `status`: `SUCCEEDED`, `FAILED`, or `SKIPPED`
- `reason`
- `before_quality`
- `after_quality`
- `evidence`
- `notes`

### Candidate Sources

Use sources in this order:

1. Existing char-level evidence from clause ranges when available.
2. Native PDF text search through `TextCoordinateLocator` behavior.
3. Existing fallback evidence only if it can be upgraded in method/confidence.

Do not introduce fuzzy LLM matching. If a fuzzy string fallback is needed later, it should be a separate design because it can create false highlights.

### Evidence Acceptance Rules

Accept a candidate only when:

- candidate method confidence is higher than current side evidence confidence
- candidate page matches the requested action page when one is provided
- candidate text is non-empty or derived from a known change range
- candidate highlight type is valid for the side
- candidate count is small enough for a focused diff highlight

Reject a candidate when:

- it has lower or equal confidence
- it lands on an unrelated page
- it creates broad page-level evidence for a narrow text diff
- it conflicts with existing high-confidence evidence

## Data Model Usage

Reuse existing models:

- `OcrRemediationAction`
- `TaskOcrRemediationSummary`
- `EvidenceBox`
- `DiffItem`

No new public response schema is required for Phase 3B if existing action fields are sufficient.

Update action fields as follows:

- `status`: `SUCCEEDED`, `FAILED`, or `SKIPPED`
- `before_quality`: snapshot of current evidence method/confidence/page/count
- `after_quality`: snapshot of replacement evidence method/confidence/page/count
- `changed_evidence`: `true` only when evidence is replaced
- `changed_diff_text`: always `false`
- `review_flags_added`: include `OCR_REMEDIATION_EVIDENCE_RELOCATED` on success
- `notes`: concise machine-readable explanations

Update task summary counts:

- `successful_action_count`
- `unresolved_action_count`
- `risk_reduced_diff_count`
- `manual_review_required_count`
- `status`

## Diff State Rules

When relocation succeeds:

- Replace only `diff.original_evidence` or `diff.compare_evidence` for the action side.
- Add `OCR_REMEDIATION_EVIDENCE_RELOCATED`.
- Remove `EVIDENCE_UNRELIABLE` only if both sides with unreliable evidence are resolved.
- Keep `quality_status = NEEDS_REVIEW` unless all OCR remediation actions for the diff succeeded and no other review flags require review.

When relocation fails:

- Keep original evidence unchanged.
- Keep `EVIDENCE_UNRELIABLE`.
- Keep or add `OCR_REMEDIATION_UNRESOLVED`.
- Keep `quality_status = NEEDS_REVIEW`.

This avoids overstating confidence. The first version may keep `NEEDS_REVIEW` even after success if there are other OCR risks.

## Debug Artifact

`ocr_remediation.json` should continue to exist, but Phase 3B should make it action-execution aware:

- action status
- before quality
- after quality
- changed evidence boolean
- notes

If `DiffQualityStage` or `SummaryStage` remaps action diff IDs later, the persisted task summary remains authoritative. A later phase can add a final post-remap remediation artifact if needed.

## API and Frontend

No new API endpoint is required.

Existing task and quality responses already expose:

- `ocr_remediation_summary`
- action status
- before/after quality
- counts

Frontend badge behavior can remain compact:

- `PLANNED`: `处置规划`
- `SUCCEEDED`: `已自动处置`
- `FAILED` / `SKIPPED`: `处置未完成`
- `MANUAL_REVIEW_REQUIRED`: `需人工处置`

If Phase 3B updates action statuses, current frontend badge mapping should already show `已自动处置` for successful actions.

## Evaluation

Add focused backend tests before a standalone evaluator.

Key metrics:

- number of executed `RELOCATE_EVIDENCE` actions
- success count
- unresolved count
- evidence confidence delta
- evidence method change
- no change to diff text

The existing OCR compare quality gate should continue to pass. This phase should not require a new threshold gate until there are enough relocation fixtures.

## Delivery Plan

### Phase 3B.1: Relocation Service

- Add `EvidenceRelocator`.
- Add result model or dataclass internal to the service.
- Unit-test success, skip, and failure paths.

### Phase 3B.2: Pipeline Execution

- Update `OcrRemediationStage` to execute eligible `RELOCATE_EVIDENCE` actions.
- Update task summary counts after execution.
- Preserve all existing remap/dedupe behavior.

### Phase 3B.3: API and Frontend Compatibility

- Verify existing responses carry updated action statuses and quality fields.
- Add frontend tests only if the current badge mapping does not already cover `SUCCEEDED`.

### Phase 3B.4: Verification

- Focused relocation tests.
- Pipeline tests.
- API compatibility tests.
- Full backend tests.
- Frontend tests/build.
- OCR quality gate.

## Testing Strategy

Backend tests should cover:

- relocating low-confidence fallback evidence to text-exact evidence
- skipping already high-confidence evidence
- rejecting same-or-lower-confidence candidates
- rejecting wrong-page candidates
- updating action status and quality snapshots
- preserving diff text
- preserving unresolved/manual review state on failure
- final merge/dedupe still preserves remediation status and flags

Frontend tests should cover:

- `SUCCEEDED` action renders `已自动处置`
- null or absent remediation summary still renders normally

## Acceptance Criteria

- `RELOCATE_EVIDENCE` actions can execute deterministically.
- Successful relocation improves evidence confidence or method quality.
- Failed relocation does not degrade evidence.
- Diff text is never changed by remediation.
- Diffs are not hidden, deleted, or suppressed by remediation execution.
- Action status and before/after quality are visible in API responses.
- Existing compatibility and quality gates pass.

