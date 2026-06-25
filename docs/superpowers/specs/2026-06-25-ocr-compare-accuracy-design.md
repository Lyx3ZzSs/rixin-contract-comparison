# OCR Compare Accuracy Design

## Purpose

This design defines the next accuracy-focused optimization track for the contract comparison MVP. The priority is scanned contract stability: reduce OCR-driven failures, false positives, evidence drift, table misreads, and silent low-confidence results.

The system already has a strong comparison pipeline: PP-Structure plus PP-OCRv5 hybrid extraction, layout diagnostics, clause splitting, clause matching, table/header/footer/seal comparison, evidence coordinates, online PDF highlighting, review states, quality summaries, and debug artifacts. This design extends those capabilities with an evaluation-driven OCR quality loop.

## Market And Practice Signals

Product-grade contract comparison systems separate two concerns:

- High-trust document comparison: legal comparison tools such as Litera Compare emphasize reliable redlines across document formats, low false positives, and workflow integration.
- AI contract review: platforms such as ContractPodAi Leah and LegalOn focus on playbooks, review automation, explanations, and human oversight.

For this project, the near-term path is not a full CLM platform or autonomous legal agent. The best fit is a trustworthy scanned-PDF comparison foundation: measurable OCR quality, regression cases, evidence traceability, and review flags when confidence is not high enough.

Relevant governance practices from NIST AI RMF and OWASP GenAI guidance support the same direction: make model behavior measurable, keep audit evidence, expose uncertainty, prevent over-reliance, and avoid silently converting low-quality input into confident legal conclusions.

## Scope

In scope:

- Golden set structure for real scanned contract comparison cases.
- OCR and layout quality metrics that connect directly to downstream contract comparison quality.
- Runtime OCR quality profiles at page and task level.
- Low-quality page handling through repair, retry, or explicit review flags.
- Evaluation scripts, HTML reports, and regression gates.
- Integration with existing debug artifacts, quality summaries, and review flags.

Out of scope:

- User, tenant, permission, and enterprise account management.
- Full CLM workflow integration.
- A complete contract review agent with playbooks and legal drafting suggestions.
- Replacing the existing `/api/compare/*` or `/api/extract/*` contracts.
- Committing sensitive real contracts into Git.

## Golden Set

Create a scanned-contract golden set organized by comparison task:

```text
backend/tests/fixtures/ocr_compare_cases/
  case_id/
    original.pdf
    compare.pdf
    expected.json
    README.md
```

Each case represents one real comparison task. The sample set should cover:

- Text-heavy scanned contracts.
- Table-heavy contracts.
- Stamped and signed pages.
- Complex headers and footers.
- Low-resolution or blurred scans.
- Rotated or skewed pages.
- Cross-page clauses.
- Attachments, schedules, and item lists.

If PDFs contain sensitive content, do not commit them. Commit the case manifest and annotation template, then load PDF paths from a local or internal storage location by environment variable.

### Expected Annotation Shape

`expected.json` should stay close to current domain models instead of inventing a separate vocabulary. It should include:

- Case metadata: `case_id`, tags, document type, known difficulty notes.
- Key pages: page numbers and expected page roles.
- Expected diffs: `diff_type`, `source_type`, `clause_no`, `title`, `original_text`, `compare_text`.
- Expected evidence: `page_no`, `bbox`, `evidence_quality`.
- Critical fields: amount, date, party, term, delivery item, payment condition.
- Table expectations: table region bboxes and important cell values.
- OCR equivalence rules: acceptable full-width/half-width, punctuation, whitespace, and common OCR variants.

The annotation format should map naturally to existing fields such as `page_no`, `bbox`, `source_type`, `diff_type`, `clause_no`, `title`, `original_text`, `compare_text`, and `evidence_quality`.

## Metrics

Evaluate scanned comparison quality in four layers.

### OCR Text Layer

- Critical field character accuracy.
- Number, amount, date, and clause number accuracy.
- Low-confidence text ratio.
- Missing page or blank page detection.
- Duplicate text and repeated line rate.

### Layout Structure Layer

- Region bbox hit rate.
- Table region recall.
- Table-cell association accuracy.
- Reading order accuracy.
- Header, footer, seal, signature, and body classification accuracy.
- OCR block to PP-Structure region match quality.

### Downstream Comparison Layer

- Diff recall.
- False positive rate.
- Table diff accuracy.
- Evidence bbox hit rate.
- Low-confidence diff flagging rate.
- OCR-induced false positive count.

### Task Stability Layer

- Task failure rate.
- Average and p95 runtime.
- OCR service error rate.
- Retry success rate.
- Quality degradation trigger rate.

## Regression Gates

Start with trend gates before hard numeric thresholds. Early gates:

- Diff recall must not regress.
- OCR-induced false positives must not significantly increase.
- Task failure rate must not increase.
- Critical field OCR quality must not regress.
- Reading order, table region, and evidence bbox quality must stay above the current baseline.

After the golden set stabilizes, split it into:

- `smoke`: small PR gate for fast feedback.
- `full`: complete local and scheduled regression run.
- `release`: full set plus manually reviewed acceptance report.

## Runtime OCR Quality Profile

Add a page-level `ocr_quality_profile` that aggregates existing OCR, layout, confidence, and reading-order diagnostics. It should not replace `LayoutQualityReport`; it should translate low-level signals into comparison-relevant quality states.

Recommended page states:

- `OK`: page can enter normal comparison.
- `LOW_TEXT_CONFIDENCE`: OCR confidence is low, especially near critical fields.
- `LAYOUT_MISMATCH`: OCR text blocks do not align well with PP-Structure regions.
- `READING_ORDER_RISK`: block order may cause clause stitching errors.
- `TABLE_RISK`: table region, row, column, or cell structure is unreliable.
- `SEAL_OR_SIGNATURE_RISK`: non-text recognition is incomplete on signing pages.
- `RETRYABLE_FAILURE`: page is suitable for automatic retry or alternate OCR parameters.
- `UNRELIABLE`: results should not be presented as confident conclusions.

The task should also expose a summary:

- Count of pages by quality state.
- Top reasons for OCR risk.
- Affected diff IDs.
- Affected page numbers.
- Whether the task requires manual review.

## Low-Quality Page Handling

Use three levels of handling.

### Level 1: Automatic Repair

For mild issues, keep the task on the normal path and apply deterministic repair:

- Text cleanup.
- Duplicate line removal.
- Header and footer suppression.
- Reading order repair.
- Table boundary repair.

### Level 2: Local Retry

For medium issues, retry only the affected page or region:

- Enable direction classification for rotated or skewed pages.
- Increase rasterization resolution for low-quality pages.
- Re-run OCR on table regions.
- Re-run OCR on critical-field regions.

Retries must be recorded in task debug artifacts and metrics. They should not hide the fact that the first pass was low quality.

### Level 3: Explicit Review

For severe issues, continue producing a task result when possible, but mark the affected output:

- `OCR_LOW_CONFIDENCE`
- `EVIDENCE_UNRELIABLE`
- `PAGE_UNRELIABLE`
- `TABLE_STRUCTURE_UNRELIABLE`
- `READING_ORDER_RISK`

Low-quality OCR must not silently produce a confident "no difference" conclusion. If unreliable pages cover main contract text, payment tables, party information, or signing pages, the task or related diffs should be marked `NEEDS_REVIEW`.

## Repository Integration

Add a dedicated evaluation script:

```text
backend/scripts/evaluate_ocr_compare_quality.py
```

The script should run comparison tasks through the same service/application boundary as production-like execution. It should not bypass the pipeline by calling only internal algorithm helpers.

Outputs:

- JSON: machine-readable metrics for CI, for example `ocr_compare_quality.json`.
- HTML: case-by-case review report with OCR risk pages, false positives, missed diffs, evidence offsets, and links to debug artifacts.
- Console summary: compact pass/fail and top regressions.

Reuse current infrastructure:

- `storage/tasks/{task_id}/debug` artifacts.
- `layout_quality.json`.
- `CompareQualityService` for low-confidence diffs and evidence quality.
- `parse_warning_details`.
- `PipelineMetrics` for timing and future retry stats.

## Phased Delivery

### Phase 1: Baseline And Golden Set

Do not change default algorithms. Add the golden set format, evaluation script, HTML report, and smoke gate. Run current `ppstructure_ocr_hybrid` over the first real samples and record the baseline.

Acceptance:

- Every case produces a task result or a clear failure reason.
- The report lists missed diffs, false positives, evidence drift, and OCR risk pages.
- The baseline identifies the top scanned-contract failure types.

### Phase 2: Runtime Quality Diagnostics

Add page-level `ocr_quality_profile` and task-level OCR quality summary. Wire results into debug artifacts, parse warnings, quality API output, frontend indicators, and report warnings.

Acceptance:

- Low-quality pages are visible by page number and reason.
- Results depending on unreliable OCR are marked for review.
- No low-quality page is silently treated as normal.

### Phase 3: Repair And Local Retry

Add targeted repair and retry for low-confidence pages, reading-order risk, and table risk.

Acceptance:

- Full golden set task failure rate decreases.
- Reading order and table-region metrics improve.
- Diff recall does not decrease.
- Retry attempts and outcomes are visible in metrics and debug artifacts.

### Phase 4: OCR-Noise-Resistant Comparison

Make clause splitting, matching, diffing, and evidence location consume OCR quality signals.

Acceptance:

- OCR-induced false positives decrease.
- Real amount, date, party, payment, and delivery differences keep their recall.
- Low-confidence text is not promoted to confident legal conclusions.

### Phase 5: Continuous Quality Operations

Split regression sets into smoke, full, and release. Add version-over-version reporting and a process for promoting production failure cases into the golden set.

Acceptance:

- Every OCR or comparison algorithm change can show which case types improved or regressed.
- Release candidates include a quality report with known limitations.
- New failure cases can be added without changing evaluator code.

## Testing Strategy

Backend tests should cover:

- Golden annotation parsing.
- Metric calculation edge cases.
- OCR quality state classification.
- Retry decision logic.
- Review flag propagation.
- Evaluator JSON output stability.

Integration checks should cover:

- Running a smoke case end to end.
- Verifying debug artifact links.
- Verifying that unreliable pages affect quality summaries.
- Verifying that low-confidence pages do not produce silent confident no-diff outcomes.

The broader verification baseline remains:

```bash
cd backend
python -m compileall app tests
python -m pytest
cd ..
cd frontend && npm test && npm run build
```

## Open Decisions For Implementation Planning

- Exact hard thresholds for OCR text, layout, table, and evidence metrics should be set after the first baseline run.
- The first golden set size should be small enough for fast iteration, then expanded after the annotation format settles.
- Sensitive PDF storage should be decided before adding real cases.
- The first retry strategies should be selected from observed baseline failures, not guessed upfront.

## References

- Litera Compare: https://www.litera.com/products/litera-compare/
- ContractPodAi Leah: https://contractpodai.com/leah/
- LegalOn My Playbooks coverage: https://www.lawnext.com/2025/01/legalon-expands-ai-contract-review-platform-with-feature-to-create-custom-playbooks.html
- NIST AI Risk Management Framework: https://www.nist.gov/itl/ai-risk-management-framework
- OWASP GenAI security guidance: https://owasp.org/www-project-top-10-for-large-language-model-applications/
