# OCR Remediation and Model Routing Design

## Purpose

Phase 3 turns OCR quality risk from a passive warning into an operational loop. Runtime OCR quality profiling already propagates OCR risk to affected diffs; this phase decides what to do with those risks and executes low-risk remediation where deterministic.

The goal is not to replace the current comparison pipeline or introduce broad agentic judgment. The goal is to make OCR risk handling measurable, auditable, and incremental.

## Goals

- Generate a remediation plan from `ocr_quality_summary`, page profiles, evidence quality, and diff review flags.
- Support deterministic remediation actions before adding expensive model retries.
- Record before/after quality signals for every attempted action.
- Expose remediation summaries through existing compare and quality APIs without breaking existing clients.
- Add lightweight frontend visibility for remediation status and manual review decisions.
- Record model-routing diagnostics by page type and quality outcome.

## Non-Goals

- Do not automatically rewrite diff text through LLM judgment.
- Do not replace the default OCR extractor in this phase.
- Do not run whole-document OCR retries when only a page or region is risky.
- Do not build a large analytics dashboard before action-level data exists.
- Do not change `/api/compare/*` or `/api/extract/*` request compatibility.

## Product Behavior

When a comparison task completes OCR quality profiling, the pipeline should classify each risky page or diff into an action:

- `NO_ACTION`: risk is absent or too weak to require handling.
- `MARK_REVIEW`: keep the diff visible and mark it for manual review.
- `RELOCATE_EVIDENCE`: attempt deterministic evidence re-location.
- `REPAIR_TABLE`: attempt deterministic table structure repair or table evidence re-mapping.
- `RETRY_OCR_PAGE`: queue or execute page-level OCR retry through an abstract retry adapter.
- `ESCALATE_MANUAL_REVIEW`: risk is too severe or remediation failed.

The initial implementation should be conservative. It may generate action plans before executing all action types. Action planning is useful by itself because it creates a stable contract for API, frontend, and future execution.

## Architecture

Add a remediation stage after OCR quality profiling and before final diff quality processing:

```text
Extraction
→ LayoutAnalysis
→ Clause/Diff
→ Evidence
→ OCRQuality
→ OCRRemediation
→ DiffQuality
→ Report
```

`OCRRemediationStage` should:

1. Read extraction results, diffs, evidence, and `ocr_quality_summary`.
2. Build a list of `OcrRemediationAction` records.
3. Execute only enabled deterministic actions.
4. Store before/after metrics for attempted actions.
5. Update diff review flags and quality status when remediation fails or manual review is required.
6. Write a debug artifact such as `ocr_remediation.json`.
7. Populate `CompareTask.ocr_remediation_summary`.

The stage must not delete diffs, hide diffs, or rewrite diff text. Evidence updates are allowed only for deterministic re-location when the new evidence has better confidence and preserves the original diff semantics.

## Backend Components

### `ocr_remediation.py`

Owns remediation planning and summary aggregation.

Primary responsibilities:

- Map OCR reasons and review flags to remediation action types.
- Prioritize actions by risk severity and expected cost.
- Deduplicate actions for the same side/page/diff/action type.
- Compute task-level remediation summary.

### `evidence_relocator.py`

Owns deterministic evidence re-location.

Initial scope:

- Re-run text-coordinate lookup for low-confidence evidence.
- Prefer exact or clause-range matches over block fallback.
- Reject candidates that cross pages unexpectedly.
- Return an explicit before/after confidence comparison.

### `ocr_retry.py`

Defines the retry adapter interface, not a hard dependency on a new model.

Initial scope:

- Page-level retry contract.
- Capture retry metadata and runtime cost.
- Support a no-op or disabled adapter for environments without alternate OCR.

### `model_routing.py`

Owns page classification and experiment recording.

Initial scope:

- Classify pages into text-heavy, table-heavy, scan-low-quality, seal/signature-heavy, or mixed.
- Select candidate routes.
- Record route inputs, outputs, quality signals, and runtime cost.

### `review_feedback.py`

Owns manual review feedback persistence and aggregation.

Initial scope:

- Save reviewer decisions for OCR-risk diffs.
- Keep feedback separate from generated diffs.
- Export feedback for future fixture creation and threshold tuning.

## Data Models

Add task-level remediation summary:

```text
TaskOcrRemediationSummary
- status
- attempted_action_count
- successful_action_count
- unresolved_action_count
- risk_reduced_page_count
- risk_reduced_diff_count
- manual_review_required_count
- actions
```

Add action records:

```text
OcrRemediationAction
- action_id
- side
- page_no
- diff_id
- reason
- action_type
- status
- before_quality
- after_quality
- changed_evidence
- changed_diff_text
- review_flags_added
- notes
```

`changed_diff_text` should default to `false` and remain false for Phase 3A and Phase 3B.

Add feedback records:

```text
ReviewFeedback
- feedback_id
- task_id
- diff_id
- reviewer_decision
- reason
- corrected_text
- corrected_evidence
- created_at
```

Suggested reviewer decisions:

- `REAL_DIFFERENCE`
- `OCR_FALSE_POSITIVE`
- `EVIDENCE_UNRELIABLE`
- `TABLE_STRUCTURE_ERROR`
- `IGNORE_RISK`

## API Contract

Extend existing task and quality responses additively:

- `ocr_remediation_summary`
- `ocr_remediation_action_count`
- `ocr_remediation_unresolved_count`
- `manual_review_required_count`

Existing clients should keep working when these fields are absent or `null`.

Add feedback endpoints only after the summary contract is stable:

```text
POST /api/compare/{task_id}/review-feedback
GET /api/compare/{task_id}/review-feedback
```

Feedback endpoints should validate that referenced `diff_id` belongs to the task.

## Frontend Behavior

Update the result page without turning it into a dashboard.

For OCR-risk diffs, show:

- Existing OCR risk badges.
- Remediation status: planned, attempted, repaired, unresolved, or manual review required.
- A compact manual decision control for OCR-risk diffs.

Add a summary area near existing quality information:

- OCR risk page count.
- Remediation attempted count.
- Remediation success count.
- Manual review required count.

Manual feedback should be explicit and reversible in later phases, but Phase 3 can start with append-only feedback if that matches existing persistence patterns.

## Delivery Plan

### Phase 3A: Remediation Planning

- Add action and summary models.
- Add `OcrRemediationPlanner`.
- Add `OCRRemediationStage` in planning-only mode.
- Expose summary through API and quality response.
- Write `ocr_remediation.json`.
- Add frontend display for planned actions.

### Phase 3B: Deterministic Evidence and Table Remediation

- Implement evidence re-location for `EVIDENCE_UNRELIABLE`.
- Implement table-focused remediation for `TABLE_STRUCTURE_UNRELIABLE`.
- Preserve diff text and original evidence history.
- Add tests for action success, failure, and no-op behavior.

### Phase 3C: Page Retry and Model Routing

- Add page-level retry adapter abstraction.
- Add model route classification.
- Record route diagnostics without changing the production default path.

### Phase 3D: Manual Review Feedback Loop

- Add review feedback persistence.
- Add API endpoints for feedback.
- Add frontend feedback controls.

## Testing Strategy

Backend tests should cover:

- planner mapping from OCR quality reasons to actions
- action deduplication and priority
- legacy task defaults when remediation summary is absent
- API compatibility
- deterministic evidence re-location success and rejection
- table remediation success and rejection
- pipeline persistence
- debug artifact creation
- feedback validation

Frontend tests should cover:

- remediation badges and summary rendering
- legacy API response without remediation fields
- manual feedback submission and error states

Evaluation tests should cover:

- remediation quality report generation
- threshold failure reporting
- no-regression behavior on existing OCR comparison fixtures

## Acceptance Criteria

- Existing `/api/compare/*` and `/api/extract/*` consumers remain compatible.
- OCR-risk diffs receive remediation actions or explicit manual review escalation.
- Deterministic remediation records before/after quality evidence.
- Remediation does not silently delete diffs or rewrite diff text.
- Quality artifacts include OCR remediation details.
- Frontend exposes remediation status without disrupting current result page flow.
- Full backend tests, frontend tests, build, and OCR quality gates pass.
