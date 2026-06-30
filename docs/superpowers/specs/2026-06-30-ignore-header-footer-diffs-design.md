# Exclude Header and Footer Diff Items Design

## Goal

Add an upload option for contract comparison that lets users exclude header and footer diff items from a compare task. When enabled, the final compare result, diff list, and generated report should not include differences whose source is the dedicated header/footer comparator.

The option is off by default.

## Scope

This feature covers diff items with `source_type="header_footer"`, including detected page headers, page footers, and page number changes.

This feature does not mask or suppress page-level visual differences caused by header/footer pixels. Page-level visual diff suppression requires a separate region-based design because it can accidentally hide body text near page margins.

This feature does not affect clause, table, metadata, seal, or page diff items.

## Existing Context

`CompareOptions` already has an `ignore_headers_footers` field, but the current active upload API and frontend do not expose it.

The comparison pipeline generates header/footer diffs in `PreClauseDiffStage` through `HeaderFooterComparator.build_diffs()`. Those diffs are stored in `ctx.header_footer_diffs`, merged into the pre-clause diff result, and eventually appear in the final task diffs, diff APIs, and reports.

The recently added stamp exclusion option established the expected option flow:

1. Frontend form controls an option and sends a multipart field only when checked.
2. Backend API converts the form field into `CompareOptions`.
3. The queued task and background job payload persist `compare_options`.
4. Pipeline stages read `task.compare_options`.
5. Summary stage performs a final source-type filter as a defensive guard.

## Proposed Behavior

On the upload page, add a checkbox labeled `排除页眉页脚差异项`. It should be unchecked by default and sit alongside the existing `排除签章区域` option.

When unchecked, behavior remains unchanged: header/footer comparator runs, and header/footer diff items can appear.

When checked:

1. Frontend submits `ignore_headers_footers=true` in the compare request.
2. Backend stores `CompareOptions(ignore_headers_footers=True, ...)` on the queued task and background job payload.
3. `PreClauseDiffStage` skips `HeaderFooterComparator.build_diffs()` and uses an empty `header_footer_diffs` list.
4. Downstream diff indexing remains stable by calculating metadata, table, and seal start indexes from the empty header/footer list.
5. `SummaryStage` filters any remaining `DiffItem` with `source_type="header_footer"` before final dedupe and stats refresh.

## API Design

`POST /api/compare` accepts a new multipart form field:

- `ignore_headers_footers`: boolean, default `false`

The response schema remains unchanged. `compare_options` remains an internal task/job setting and is not returned in the compare creation response.

Legacy or future fields for unsupported options should not be introduced by this feature.

## Frontend Design

`CompareContractOptions` should include:

- `ignoreStamps`
- `ignoreHeadersFooters`

`compareContracts()` should append `ignore_headers_footers=true` only when `ignoreHeadersFooters` is true. The default request should not include this field.

The upload page should maintain separate checkbox state for stamp exclusion and header/footer exclusion. Both options can be selected independently.

Suggested copy:

- Label: `排除页眉页脚差异项`
- Helper text: `不生成页眉、页脚、页码等差异`

## Backend Design

In `api.py`, `compare_contracts()` should accept `ignore_headers_footers: bool = Form(False)` and include it when constructing `CompareOptions`.

In `PreClauseDiffStage.execute()`:

- If `task.compare_options.ignore_headers_footers` is true, set `header_footer_diffs=[]` and emit a skipped progress marker such as `header_footer_diff_skipped`.
- Otherwise, run `self.header_footer.build_diffs(...)` and emit the existing `header_footer_diff_done` marker.

In the existing compare-option final filter, add `source_type="header_footer"` when `ignore_headers_footers` is true. This guard should coexist with the stamp filter.

## Error Handling

Invalid boolean multipart values should rely on FastAPI's normal validation behavior.

If an old task lacks `compare_options`, the model default keeps all exclusion options disabled.

If a queued job lacks `compare_options`, background execution should validate an empty dict and run with defaults.

## Testing

Backend tests:

- API creation persists `ignore_headers_footers=true` in task storage and job payload while leaving unrelated options unchanged.
- `PreClauseDiffStage` produces no header/footer diffs when the option is enabled.
- Final summary filtering removes a `header_footer` diff if one is already present in the pipeline context.

Frontend tests:

- Default upload request omits `ignore_headers_footers`.
- `compareContracts(..., { ignoreHeadersFooters: true })` sends `ignore_headers_footers=true`.
- Upload page renders the unchecked header/footer option by default.
- Selecting the option submits `{ ignoreHeadersFooters: true }`.

Build verification:

- `python -m pytest` targeted backend tests.
- `npm test -- src/lib/api.test.ts src/pages/UploadPage.test.tsx`.
- `python -m compileall backend/app backend/tests`.
- `npm run build`.

## Out of Scope

This design does not implement pixel-level masking, OCR region deletion, or comparison-time cropping of header/footer areas. Those behaviors should be specified separately because they require page coordinate thresholds, overlap rules, and safeguards against hiding body text.
