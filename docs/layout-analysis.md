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
