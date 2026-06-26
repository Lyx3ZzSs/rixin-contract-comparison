# OCR Model Routing And Retry Evaluation Design

## Purpose

Phase 4A adds a measurable model-routing and page-level retry evaluation layer for OCR-driven contract comparison risk. Phase 1 created a scanned-contract quality baseline. Phase 2 added runtime OCR quality profiles. Phase 3A and 3B added OCR remediation planning and deterministic evidence relocation. Phase 4A should answer the next product question: whether the current OCR and layout model combination is good enough for each page type, and which pages should be retried, routed differently, or left for manual review.

This phase is evaluation-first. It should not replace the current production OCR path, call new external model services by default, or rewrite comparison output. It should create stable routing records, debug artifacts, and evaluator metrics so later phases can safely execute selected retry strategies.

## Goals

- Classify pages into comparison-relevant routing categories using existing extraction and OCR quality signals.
- Recommend page-level retry or routing strategies without changing production comparison results by default.
- Introduce an OCR retry adapter contract with a no-op default implementation.
- Record routing recommendations and estimated costs in task debug artifacts.
- Extend OCR comparison evaluation reports with route-level quality metrics.
- Preserve existing `/api/compare/*` and `/api/extract/*` compatibility.
- Keep the design compatible with future alternate OCR engines, high-DPI retries, table-region retries, and critical-field retries.

## Non-Goals

- Do not replace PP-OCRv5 or PP-Structure in the default compare path.
- Do not execute real OCR retries by default.
- Do not introduce a new public API endpoint in this phase.
- Do not rewrite `original_text`, `compare_text`, snippets, readable change text, or diff IDs.
- Do not suppress, merge, or delete diffs based on routing recommendations.
- Do not build a frontend analytics dashboard.
- Do not commit sensitive real contracts into Git.

## Product Behavior

After OCR quality profiling and remediation planning are available, the system should be able to produce a routing recommendation for each relevant page:

1. Classify the page type and risk category.
2. Recommend a route such as keep current output, retry with higher DPI, retry table regions, retry critical-field regions, or manual review.
3. Record why the route was recommended.
4. Estimate runtime and cost impact where possible.
5. Expose the recommendation through debug artifacts and evaluator reports.

The default production compare result should remain unchanged. The user-facing result page can continue showing existing OCR risk and remediation badges. Phase 4A creates evidence for future routing decisions rather than changing runtime behavior immediately.

## Routing Categories

The first version should use deterministic page categories:

- `text_heavy`: mostly body text or clauses, low table and seal density.
- `table_heavy`: structured table content is material to the comparison.
- `scan_low_quality`: low text confidence, blurred scan, missing character confidence, or blank/partial OCR.
- `seal_signature`: seal, signature, stamp, or signing-region risks dominate the page.
- `mixed`: multiple material page roles or risk categories occur together.

Categories are not legal conclusions. They are routing hints for OCR and layout strategy selection.

## Route Recommendations

Recommended route values:

- `KEEP_CURRENT`: current extraction quality is sufficient.
- `HIGH_DPI_PAGE_RETRY`: page-level OCR should be retried with higher rasterization quality.
- `TABLE_REGION_RETRY`: table regions should be retried or re-parsed with table-focused settings.
- `CRITICAL_FIELD_RETRY`: only important field regions such as amount, date, party, payment term, delivery item, liability, termination, or breach clauses should be retried.
- `MANUAL_REVIEW`: automatic retry is unlikely to be trustworthy or cost-effective.
- `NO_ROUTE`: no recommendation because the page is outside OCR risk scope.

Phase 4A only recommends these routes. A later phase can enable selected route execution behind an explicit feature flag.

## Architecture

Keep the current comparison pipeline intact:

```text
Extraction
→ LayoutAnalysis
→ Clause/Diff
→ Evidence
→ OCRQuality
→ OCRRemediation
→ DiffQuality
→ Visualization
→ Summary
```

Phase 4A adds routing analysis after OCR quality and remediation signals are available. The routing layer may run in the pipeline as a lightweight debug-only stage or as an evaluator service invoked by `evaluate_ocr_compare_quality.py`. The first implementation should prefer minimal production risk:

1. Add a pure `ModelRoutingAnalyzer` service.
2. Add a no-op `OcrRetryAdapter` contract.
3. Add optional debug artifact writing from the pipeline when data is available.
4. Extend the evaluator to aggregate route metrics.

## Backend Components

### `model_routing.py`

Create `backend/app/services/model_routing.py`.

Responsibilities:

- Classify page type from `PageOcrQualityProfile`, layout metrics, extraction warnings, table signals, and affected diffs.
- Recommend a route and explain the reason.
- Estimate cost impact using simple local metadata such as page count, table count, and retry type.
- Return immutable or model-backed route records without mutating diffs.

Suggested records:

```text
PageModelRoute
- side
- page_no
- page_type
- recommended_route
- reason_codes
- affected_diff_ids
- quality_status
- estimated_cost_level
- should_execute
- notes

TaskModelRoutingSummary
- status
- route_count
- retry_recommended_count
- manual_review_recommended_count
- page_count_by_type
- route_count_by_recommendation
- routes
```

`should_execute` should default to `false` in Phase 4A.

### `ocr_retry.py`

Create `backend/app/services/ocr_retry.py`.

Responsibilities:

- Define a page-level retry adapter contract.
- Provide `NoopOcrRetryAdapter` for default runtime.
- Return structured retry results that can later hold timing, engine, parameter, and quality delta data.

Initial adapter behavior:

```text
NoopOcrRetryAdapter.retry_page(...) -> OcrRetryResult(status="SKIPPED", reason="RETRY_DISABLED")
```

The adapter should not be wired to external services in this phase.

### Pipeline Debug Integration

If added to the production pipeline, the routing stage should:

- Read `ctx.task.ocr_quality_summary`.
- Read current diffs and evidence.
- Generate `task.model_routing_summary` only if a model field exists, or write a debug artifact only if adding a task field would expand public API too early.
- Write `ocr_model_routing.json` through `CompareDebugWriter` or a focused writer helper.
- Not change diffs, evidence, review flags, or task status.

The recommended first implementation is debug artifact plus evaluator output. Public task model fields can be added later if frontend or API consumers need them.

## Classification Rules

Use existing signals before adding new ones.

`scan_low_quality` when any of these hold:

- page status is `LOW_TEXT_CONFIDENCE` or `UNRELIABLE`
- reason includes `LOW_AVG_CONFIDENCE`, `LOW_CONFIDENCE_BLOCK_RATIO`, or `MISSING_CHAR_CONFIDENCE`
- extraction warnings include OCR engine errors or severe missing text

`table_heavy` when any of these hold:

- page status is `TABLE_RISK`
- reason includes `TABLE_CELL_UNMATCHED` or `TABLE_HEAVY_WITHOUT_STRUCTURE`
- affected diffs include material table diffs

`seal_signature` when any of these hold:

- page status is `SEAL_OR_SIGNATURE_RISK`
- reason includes seal, signature, stamp, or non-text region warnings

`mixed` when two or more material categories apply.

`text_heavy` when the page has OCR risk but no table or seal dominant signal.

## Recommendation Rules

`KEEP_CURRENT`:

- page status is `OK`
- no affected diffs require OCR review

`HIGH_DPI_PAGE_RETRY`:

- page type is `scan_low_quality`
- affected diffs include critical business tokens
- the page is not already marked too unreliable for automated recovery

`TABLE_REGION_RETRY`:

- page type is `table_heavy`
- affected diffs are table diffs or evidence points into table regions

`CRITICAL_FIELD_RETRY`:

- page is text-heavy or mixed
- affected snippets contain amount, date, party, payment, delivery, liability, termination, breach, or quantity signals
- full page retry would be excessive

`MANUAL_REVIEW`:

- page status is `UNRELIABLE`
- multiple severe categories apply
- extraction failed or warnings indicate retry is unlikely to recover trust

`NO_ROUTE`:

- page has no OCR quality profile or no relevant signals

## Evaluator Changes

Extend `backend/scripts/evaluate_ocr_compare_quality.py` to include route metrics.

JSON output should include:

```text
route_metrics
- route_count_by_recommendation
- page_count_by_type
- retry_recommended_count
- manual_review_recommended_count
- precision_by_recommendation
- recall_by_recommendation
- evidence_hit_rate_by_recommendation
- low_confidence_ratio_by_recommendation
```

Case output should include:

- page route records
- affected diff IDs
- recommendation reasons
- whether expected diffs were found
- whether evidence was hit
- quality warnings

HTML output should add a compact route section per case. It should not become a large dashboard.

## Data And Compatibility

This phase should avoid public API expansion unless implementation shows that an internal model is needed for persistence. If a model is added to `CompareTask`, it must be optional and default to `None`, matching the existing OCR quality and remediation compatibility pattern.

No existing request payload should change.

No existing response consumer should be required to read routing fields.

## Testing Strategy

Backend unit tests:

- classify scan-low-quality pages
- classify table-heavy pages
- classify seal/signature pages
- classify mixed pages when multiple signals apply
- recommend high-DPI retry for critical low-confidence text pages
- recommend table-region retry for table risk
- recommend manual review for unreliable pages
- verify no-op retry adapter returns skipped status

Pipeline or writer tests:

- routing debug artifact is written when enabled
- routing does not mutate diffs, evidence, snippets, or task status

Evaluator tests:

- route metrics are included in JSON output
- route metrics aggregate by recommendation
- HTML report includes route recommendation labels
- existing threshold behavior remains unchanged

Compatibility tests:

- legacy tasks without routing data still serialize and load
- full backend test suite remains green

## Success Criteria

Phase 4A is successful when:

- The evaluator can report which pages would keep current OCR, retry high-DPI, retry table regions, retry critical fields, or require manual review.
- Route metrics can be compared against precision, recall, evidence hit rate, and low-confidence ratio.
- Default production comparison output remains unchanged.
- No new external OCR/model dependency is required.
- Existing backend and frontend tests pass.
- OCR quality evaluation still has no threshold failures.

## Risks And Mitigations

Risk: routing recommendations are mistaken for executed remediation.

Mitigation: keep `should_execute=false`, use recommendation wording, and avoid changing diff output.

Risk: route labels become too coarse to guide model choices.

Mitigation: record reason codes and affected diff IDs so later analysis can split categories.

Risk: evaluator reports grow too complex.

Mitigation: add compact route metrics first; defer dashboards.

Risk: routing rules duplicate OCR quality logic.

Mitigation: consume `TaskOcrQualitySummary` and page reason codes instead of recalculating low-level OCR quality.

## Deferred Work

- Execute selected retry routes at runtime.
- Add alternate OCR engine adapters.
- Add frontend route summary display.
- Add reviewer feedback endpoints for route correctness.
- Train or tune routing rules from accumulated reviewed cases.
