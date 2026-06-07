# Layout Analysis

The comparison extraction path uses one shared PP-Structure response adapter for
`PPStructureExtractor` and `LayoutDetector`. The adapter validates coordinates,
preserves original region labels in `TextBlock.block_role`, keeps non-text
regions such as seals, and attaches table-cell coordinates with one-to-one
matching.

`PPStructureOCRHybridExtractor` matches OCR blocks to layout regions, retains
unmatched OCR blocks, appends layout-only regions, and assigns
`TextBlock.reading_order`. Layout diagnostics are written to
`storage/tasks/{task_id}/debug/layout_quality.json`.

V3 additionally writes `flow_role`, `layout_match_score`,
`layout_match_status`, and `layout_match_reason` on each block. Match statuses
distinguish clear matches, ambiguous matches, meaningful unmatched OCR, noise,
and structure-only regions. Page-level diagnostics report these counts and
reading-order conflicts.

## Rollout Modes

Configure `LAYOUT_ANALYSIS_MODE`:

- `legacy`: use the previous full-page fallback and discard empty regions.
- `shadow`: return legacy layout behavior while recording v2 quality and
  differences.
- `v2`: use validated v2 regions and reading order. This is the default.
- `v3_shadow`: keep V2 production behavior while recording V3 diagnostics.
- `v3`: classify flow roles, diagnose OCR/layout matches, and use V3 reading order.

## Regression Checks

Run the deterministic layout golden cases:

```bash
cd backend
python scripts/evaluate_layout_quality.py tests/fixtures/layout_cases
python scripts/evaluate_layout_quality.py tests/fixtures/layout_real_cases \
  --html-output .layout-report --fail-on-threshold
python -m pytest tests/test_layout_analysis.py tests/test_evaluate_layout_quality.py
```

The report includes region precision/recall, bbox hit rate, reading-order
accuracy, label macro F1, invalid bbox count, and threshold failures. Real
regression cases are replayed from approved PDFs and compressed raw model
responses; generated HTML reports are local artifacts and must not be committed.

The committed real regression baseline currently contains 8 approved documents,
71 pages, 32 tables, and 475 downstream clauses. Threshold checks cover valid
OCR matching, noise precision, table-cell association, non-text recall, reading
order, and downstream clause order.

Promote V3 by running `v3_shadow` first. Shadow mode keeps V2 production flow
while emitting V3 diagnostics. Switch to `v3` only after the real regression
suite passes and at least 20 shadow-mode production tasks show no severe layout
warnings or downstream regressions.
