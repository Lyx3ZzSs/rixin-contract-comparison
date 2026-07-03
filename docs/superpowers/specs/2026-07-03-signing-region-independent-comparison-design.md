# Signing Region Independent Comparison Design

## Goal

Separate signing-region differences from main contract-body differences while keeping real signing, seal, handwritten, and signing-date changes visible.

The comparison result should continue to count signing-region changes as task diffs, but they should appear in a dedicated signing/visual review group rather than the main body-diff stream.

## Context

The project already has several relevant boundaries:

- Seal blocks can produce `source_type="seal"` diffs through `build_seal_diffs()`.
- Header and footer diffs use a dedicated `source_type="header_footer"`.
- `ClauseSplitter` skips known non-body block types such as `seal`, `image`, `figure`, `header`, and `footer`.
- `DiffQualityProcessor` can already reclassify some signing-date clause diffs into `source_type="metadata"` with `section_type="signature"`.
- The frontend groups `seal` and `header_footer` outside the main audit group.

The missing piece is a unified signing-region boundary. Seal detection alone is too narrow because contract signing areas can contain signing dates, handwriting, participant signatures, signature labels, blank form fields, and image-like regions. Keyword matching alone is too broad because ordinary body clauses often contain terms such as `签字`, `盖章`, `日期`, `甲方`, and `乙方`.

## Decision

Use multi-signal local signing-region detection.

Do not assume signing regions are only at the end of a contract. A signing region may appear on a technical agreement cover, appendix cover, participant page, acceptance page, middle-page signing table, or final signing page.

Do not classify a region by global keyword matching. Text anchors are weak recall signals only. A signing-region candidate must be supported by local layout and/or visual evidence.

## Alternatives Considered

### A. Seal Blocks Only

Use OCR/layout `seal` blocks as the only signing-region signal.

This has the lowest false-positive risk, but misses signing dates, handwritten signatures, participant signatures, blank signing fields, and signing tables without a detected seal.

### B. Multi-Signal Local Signing Region Detection

Detect local candidate regions from text anchors, layout shape, table/form structure, visual blocks, handwriting-like fragments, and spatial clustering.

This is the recommended approach because it preserves real signing changes while avoiding body-clause pollution. It also fits the current PaddleOCR-based pipeline without introducing a separate document-layout stack.

### C. Post-Diff Reclassification Only

Let signing content enter the normal clause/table diff stream, then repair it in `DiffQualityProcessor`.

This is easier to add, but it allows signing OCR noise to affect clause splitting, matching, and main diff generation before cleanup. It should remain a fallback, not the primary architecture.

## Open Source Strategy

There is no mature one-step open source solution for Chinese contract signing-region semantics.

Use PaddleOCR's existing strengths as base signals:

- Layout detection classes such as `seal`, `image`, `figure`, `table`, `header`, and `footer`.
- Seal text recognition as a visual/seal signal.
- OCR text boxes, confidence, block coordinates, and table structure.

Do not introduce a separate generic layout framework such as LayoutParser for the first implementation. Those tools are useful for custom model training, but they do not directly solve Chinese contract signing-region semantics.

Future work can train or fine-tune a private detector with labels such as `signing_region`, `signature`, `handwriting_fill`, and `seal` after enough reviewed examples exist.

## Architecture

Add a deterministic signing-region layer between document preparation and clause splitting:

```text
Extraction
  -> DocumentUnderstanding
  -> DocumentPreparation
  -> SigningRegionDetector
  -> PreClauseDiffStage signing-region comparison
  -> ClauseSplitter
  -> ClauseMatcher / DiffEngine
  -> DiffQualityProcessor fallback reclassification
  -> Summary / frontend grouping
```

### SigningRegionDetector

`SigningRegionDetector` scans each page and builds local region candidates from nearby blocks.

Candidate signals:

- Text anchors: `签字`, `盖章`, `章`, `签署`, `签订日期`, `日期`, `授权代表`, `法定代表人`, `负责人`, `参与人员`, `甲方`, `乙方`.
- Layout signals: signing tables, two-column party blocks, blank form lines, label-value rows, compact label clusters, and local form-like structure.
- Visual signals: `seal` blocks, `image` or `figure` blocks near signing labels, handwriting-like OCR fragments, low-confidence fill text, red stamp-like or seal-recognition regions.
- Spatial signals: blocks form one local cluster with bounded page coordinates and plausible field adjacency.

Negative signals:

- Text appears inside a numbered body clause or long paragraph.
- Text is part of an obligation, validity, process, or dispute clause rather than a form area.
- The candidate has only a keyword and no local layout or visual support.
- The only signal is that the page is near the end of the PDF.

Detected region output should include:

- `page_no`
- `bbox`
- `role`, such as `party_signing_block`, `participant_signature`, `signing_date`, or `seal_signature_block`
- `confidence`
- `signals`
- `excluded_block_ids`
- debug-friendly reasons

For blocks inside a confident signing region, set:

- `block_role="signature_region"` or a more specific signature role
- `enter_clause_compare=False`

This prevents signing-region OCR from entering normal clause splitting.

### SigningRegionComparator

Add a comparator for signing regions before clause diffing. It should emit compatible `DiffItem` objects.

Matching should be local and conservative:

- Prefer same-page region match.
- Allow neighbor-page match only when page roles and region shape support it.
- Match by role, local bbox overlap or relative position, and surrounding anchors.
- Do not pair unrelated occurrences of `日期`, `甲方`, or `乙方` across a page or document.

Output rules:

- Signing dates with reliable extracted values become focused field diffs.
- Real signatures, participant handwriting, and seal additions/deletions become region-level diffs.
- Low-confidence OCR in a seal/signature region should not become textual body diff wording.
- If text is unreliable but visual evidence is meaningful, use a stable description such as `第34页新增参与人员签字区域`.
- If evidence is uncertain but potentially important, keep the diff with a review flag instead of suppressing it.

Recommended metadata:

- `section_type="signature"`
- `review_flags` includes `SIGNING_REGION_CHANGE` for region-level changes.
- `review_flags` includes `SIGNING_REGION_REVIEW` when classification or matching is uncertain.
- `review_flags` includes `SIGNING_DATE_FIELD_CHANGE` for signing-date field changes.

Use existing `source_type` values where possible to preserve API compatibility. `metadata` is appropriate for structured signing-date fields. `seal` remains appropriate for seal-region diffs. If a generic visual signing diff cannot be represented cleanly, prefer additive metadata before introducing a new public `source_type`.

## Data Flow

1. Existing OCR and structure extraction produces `Document`, `Page`, and `TextBlock` data.
2. Existing document understanding and preparation classify pages, tables, cover metadata, and non-body blocks.
3. `SigningRegionDetector` runs on both documents and records signing-region debug artifacts.
4. Blocks inside confident signing regions are excluded from clause comparison.
5. `SigningRegionComparator` compares original and compare signing regions and emits signing diffs.
6. Existing header/footer, metadata, table, seal, and clause comparison continue.
7. `DiffQualityProcessor` remains a fallback for signing-date changes, seal OCR fragments, signing-table label noise, and leaked signing OCR.
8. Summary filtering must not drop signing-region changes unless the user explicitly enabled a relevant ignore option and the source type matches that option.
9. Frontend grouping shows signing-region changes outside the main body group while still counting them in task totals.

## User-Facing Behavior

Main body diffs should describe contract content changes.

Signing-region diffs should describe signing, seal, handwritten, and signing-date changes. Examples:

- `签署日期：空白 -> 2026年5月6日`
- `第53页新增甲方盖章区域`
- `第34页新增参与人员签字区域`
- `第29页签章区内容需人工复核`

Signing-region changes are counted in total diffs. They are not mixed into the main body/key-field group.

Low-value OCR noise from stamps, signatures, handwriting fragments, scanner edges, or HTML-like image fragments should not appear as a normal body text difference.

## Error Handling

Classification must be conservative.

- If a keyword appears without local layout or visual support, keep the content in normal body comparison.
- If a candidate signing region has low confidence, keep the original diff and add `SIGNING_REGION_REVIEW`.
- If OCR text in a seal/signature region is empty, HTML-like, one-character noise, or low-confidence, do not generate a text-change diff from it.
- If a signing date, company name, amount, contract number, or other protected value changes, preserve the change as a focused field diff.
- If original and compare regions cannot be matched, emit a one-sided region `ADD` or `DELETE` with page and bbox evidence.
- If PaddleOCR does not detect a seal, the detector may still use form layout, image blocks, handwriting-like fragments, and local labels, but never keyword-only logic.
- If evidence conflicts, keep the diff with `NEEDS_REVIEW` instead of suppressing it.

## Testing

### Unit Tests

Add positive tests:

- A middle-of-document signing table is detected as `signature_region`.
- A technical-agreement or appendix cover signing block is detected without relying on last-page position.
- A signing date changed from blank placeholder to a real date emits `section_type="signature"`.
- Added participant handwriting emits a region-level signing diff.
- Added seal/signature visual evidence emits a region-level signing diff.

Add negative tests:

- Body clause text such as `本合同经双方签字盖章后生效` remains in normal clause comparison.
- Ordinary clauses about authorization, seal process, validity, or dispute handling are not signing regions.
- A late-page normal body clause is not classified as signing just because it is near the end.
- Isolated words such as `日期`, `甲方`, or `乙方` do not trigger signing-region classification without layout or visual support.

Add noise tests:

- Seal OCR single-character fragments are suppressed or converted to stable region descriptions.
- HTML image fragments inside stamp/signature regions are not textual diffs.
- Edge handwriting or scanner fragments do not become main body diffs.
- Real seal, signature, signing-date, and participant-handwriting changes are not suppressed.

### Rendered PDF Verification

Use an existing current-project comparison task that contains multiple signing regions as a rendered-PDF verification case.

The verification should render the original and compare PDFs, inspect relevant pages, and compare post-change diff output against visual evidence. This is required because signing-region behavior depends on page layout, bbox relationships, OCR blocks, seal/image regions, and handwritten marks that are not fully represented by text-only unit fixtures.

Verification expectations:

- Multiple signing regions in one task are detected independently.
- Signing regions outside the final pages are detected when local signals support them.
- Signing-region changes are visible in the dedicated group and counted in total diffs.
- Signing OCR noise is absent from the main body group.
- Main contract body differences, especially protected fields, remain visible.
- Debug artifacts explain each signing-region detection, rejection, suppression, and reclassification.

Suggested artifacts:

- Rendered original/compare page PNGs for pages with signing regions.
- Side-by-side images with evidence boxes where available.
- A before/after diff summary grouped by `source_type`, `section_type`, and review flags.
- Signing-region detector debug JSON.

## Rollout

1. Add signing-region detection data structures and debug artifact writer.
2. Implement conservative `SigningRegionDetector`.
3. Exclude confident signing-region blocks from clause comparison.
4. Implement `SigningRegionComparator` for dates, seals, signatures, and handwriting-like region events.
5. Add fallback quality rules for leaked signing-region OCR.
6. Update frontend grouping only if existing `section_type="signature"` and review flags are insufficient.
7. Add unit tests and rendered-PDF verification.

## Success Criteria

- Signing, seal, handwriting, and signing-date changes are preserved and counted.
- Signing-region changes do not pollute the main body/key-field diff group.
- Keyword-only false positives are avoided.
- Signing regions are detected outside the contract end when local layout and visual evidence support them.
- Multiple signing regions in the same comparison task are handled independently.
- Low-value signing OCR noise is suppressed or converted to stable region-level descriptions.
- Existing `/api/compare/*` response compatibility is preserved.
