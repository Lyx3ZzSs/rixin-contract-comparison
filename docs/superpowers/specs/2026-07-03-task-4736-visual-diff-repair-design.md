# Task 4736 Visual Diff Repair Design

## Background

Task `4736c006-cb0e-4ef0-8b39-3594ac73aced` compares a native-text contract PDF against a scanned contract PDF. Both PDFs have 53 pages. Visual review was performed from rendered page images under `tmp/pdfs/task-4736c006/`, including side-by-side renders and a high-resolution page 44 crop.

The current output has many false positives and several false negatives because OCR text, clause splitting, header/footer detection, and seal detection are being mixed into one main diff stream. The approved product behavior for this repair is option B:

- Main results report contract body and key field differences.
- Signature, seal, handwriting, and meaningful scanner artifacts are reported in a separate visual/signing/scan group.
- Low-information OCR fragments from scanner watermarks, edge marks, and footer handwriting do not appear as body differences.

## Goals

1. Reduce confirmed false positives from OCR noise, section drift, page footer handwriting, cover edge artifacts, and seal OCR fragments.
2. Recover confirmed false negatives for key fields on page 2 and signing-related pages.
3. Keep real body differences, option changes, dates, party names, amounts, seals, and signatures visible.
4. Keep `/api/compare/*` response compatibility by preserving the existing diff shape and using source/type metadata to support grouping.
5. Add task-level verification so the same visual regression can be rerun after the fix.

## Non-Goals

- Do not hardcode task IDs, file names, diff IDs, or contract-specific company names in runtime logic.
- Do not build a pixel-level visual diff engine.
- Do not replace the OCR engine or the whole clause matcher.
- Do not suppress uncertain protected-value changes unless another reliable source proves equality.
- Do not remove signature, seal, handwriting, or meaningful scanner-mark changes from the task output; move them out of the main body-diff stream.
- Do not commit real contract PDFs or rendered images as normal regression fixtures without a separate desensitization decision.

## Confirmed False Positives

The following existing outputs should no longer appear as main body/key-field diffs:

- `D002-D009`: footer or handwritten fragments such as `怀意`, `岳`, `吾怀爽`, `哥意`, `意`, `平怀`, `综`, and `S`.
- `D013`, `D016`, `D017`, `D018`, `D020`: cover or scan-edge fragments produced by watermark, edge marks, or handwriting OCR.
- `D062-D064`: page 27 right-edge OCR omissions where the scanned page visually contains the same material text.
- `D075`: bracket-style false change where both sides are `〔2006〕34号`; original native text proves equality.
- `D084`, `D098`, `D099`, `D101-D109`, `D113`: section heading add/delete noise where headings such as `定义`, `服务期限与进度要求`, `合同价格及支付`, `双方义务`, `验收`, `项目联系人`, `知识产权`, `保密`, `转让与分包`, `合同变更终止`, `不可抗力`, `违约责任`, and `争议解决` exist on both sides.

The following outputs represent real visual changes but need better classification or cleaner text:

- `D025-D030`: seal/signature regions on pages 12, 29, and 53.
- Page 34 handwritten participant signatures.
- Page 29 and page 53 signing-page signature, date, and seal regions.

## Confirmed False Negatives

The fixed comparison should surface these differences:

- Page 2 contract title changed from `国能长源随州发电有限公司随县分公司 2026 年新能源场站功率预测系统授权服务合同` to `长源电力随州公司 2026 年新能源场站功率预测系统授权服务单一来源项目合同`.
- Page 2 party A changed from `国能长源随州发电有限公司随县分公司` to `国能长源随州发电有限公司`.
- Page 36 technical agreement signing date changed from blank `2026年 月 日` to `2026年5月6日`.
- Page 34 participant signatures were added after `参与人员`.
- Signing pages contain real signature, date, and seal visual additions that should be grouped separately from body text.

## Architecture

Add conservative validators around the existing comparison pipeline instead of replacing the pipeline:

```text
Extraction / DocumentUnderstanding
  -> NativeTextPageIndex
  -> DocumentPreparation visual-role classification
  -> Cover and signing-field extraction
  -> Existing clause split and matcher
  -> DiffQualityProcessor
       -> NativeTextEqualityGuard
       -> NonBodyVisualNoiseGuard
       -> HeadingCoverageGuard
       -> SigningAndSealClassifier
  -> Visualization / Summary
```

The main implementation boundaries are:

- Native text indexing belongs near document extraction or document preparation because it describes page-level source text.
- Field recovery belongs in cover/signing-aware services, not in generic diff filtering.
- Suppression and reclassification belong in `DiffQualityProcessor` or adjacent quality guards.
- UI grouping should rely on `source_type`, `review_flags`, and stable classification metadata, not text heuristics in the frontend.

## NativeTextPageIndex

Create or extend a page-level index that stores normalized native PDF text when available. The index should be optional and side-specific.

Minimum fields:

- `page_number`
- `text`
- `text_key`
- `line_keys`
- `field_like_lines`

Use cases:

- Refute OCR-only symbol differences such as `〔2006〕34号` versus `(2006)34`.
- Recover original-side title and party fields when OCR rendering is damaged by non-embedded fonts.
- Support same-page and neighbor-page heading coverage checks.

Failure behavior:

- If native text cannot be extracted, skip native-text guards and keep existing OCR-based behavior.
- Never use native text alone to suppress a protected-value difference if the opposite scanned page lacks confirming OCR or visual context.

## NonBodyVisualNoiseGuard

Classify low-information visual artifacts before they become body diffs.

Suppress from the main stream when the changed text is:

- A short repeated footer or bottom-page handwritten fragment.
- A scanner watermark or CamScanner-style edge artifact.
- An isolated edge mark with no body-text context.
- A one-character or low-information OCR glyph overlapping a seal/signature region.

Move to the visual/signing/scan group, rather than suppress entirely, when the changed region is:

- A signature.
- A seal.
- A signing date field.
- A handwritten participant entry.
- A meaningful scanner mark that users may need to inspect.

Guardrails:

- Do not suppress dates, amounts, percentages, party names, contract numbers, option selections, or liability terms.
- If artifact classification is uncertain and protected text is present, keep the diff with a review flag.

## HeadingCoverageGuard

Suppress section-title ADD/DELETE diffs caused by clause boundary drift when page-level coverage proves the heading exists on both sides.

Eligible examples:

- `定义`
- `服务期限与进度要求`
- `合同价格及支付`
- `双方义务`
- `验收`
- `项目联系人`
- `知识产权`
- `保密`
- `转让与分包`
- `合同变更终止`
- `不可抗力`
- `违约责任`
- `争议解决`

The guard should check:

- Same page first, then previous/next page if the clause spans a page break.
- Native text where available.
- OCR page text and line keys on the scanned side.
- Whether matched child clauses already cover the section content.

It must not suppress a heading when the heading text carries a real option, date, amount, or party-name change.

## Key Field Recovery

Add targeted extraction for field-level differences that current clause comparison misses.

### Page 2 Contract Identity

Extract and compare:

- Contract title.
- Party A.
- Party B if available.

The extractor should use a source priority:

1. Native PDF text for the original side when available.
2. OCR lines with high title/field confidence.
3. Existing cover metadata and quote metadata blocks.

Expected output:

- A key-field diff for the title change.
- A key-field diff for the party A name change.

### Signing and Appendix Covers

Detect signing-form and appendix-cover pages that contain signing metadata outside normal body clauses.

Extract:

- Signing date.
- Signer or participant handwritten entries when visible.
- Seal and signature regions as visual events.

Expected output:

- Page 36 date change is reported as a focused signing-date field diff, not a large appendix text diff.
- Page 34 participant signatures are reported in the visual/signing/scan group.
- Page 29 and page 53 signing visuals remain visible but are not described as body text changes.

## Seal and Signature Output

Change seal/signature diff descriptions to prefer stable region descriptions over low-confidence OCR text.

Rules:

- If OCR text in a seal/signature region is empty, HTML-like, one-character, or low-confidence, emit a region-level description.
- If a date or signer field is high-confidence and field-like, emit it as a structured field diff.
- Preserve page number, bounding box, and evidence image coordinates for review.

Examples:

- `第53页新增甲方印章区域`
- `第53页新增签名区域`
- `第36页技术协议签订日期：空白 -> 2026年5月6日`

## Data Flow

1. Load original and compare PDFs and existing OCR output.
2. Build optional native text page indexes for each side.
3. Prepare documents with stronger visual-role labels for footer handwriting, scanner marks, signatures, seals, and signing forms.
4. Extract cover and signing key fields before generic clause diffing.
5. Run existing split, match, and diff generation.
6. Apply quality guards:
   - Native text equality.
   - Non-body visual noise.
   - Heading coverage.
   - Seal/signature reclassification.
7. Emit main diffs and visual/signing/scan diffs with stable metadata.
8. Render key PDF pages for manual spot verification after rerun.

## API and Frontend Compatibility

The backend should keep the current diff list schema compatible. Any new grouping should use additive metadata:

- `source_type`: use existing values where possible; add narrowly-scoped values only if necessary.
- `review_flags`: add machine-readable reasons such as `VISUAL_SIGNING_ARTIFACT`, `NATIVE_TEXT_CONFIRMED_EQUAL`, or `SECTION_HEADING_COVERED`.
- `category` or `display_group`: only add if the frontend needs a stable grouping field and existing fields are insufficient.

Frontend behavior:

- Main body/key-field diffs remain the primary list.
- Signature, seal, handwriting, and meaningful scanner marks are displayed in a separate group.
- Suppressed OCR noise is not displayed as a normal diff.

## Error Handling

- Missing native text index: skip native-text checks and keep OCR behavior.
- Missing evidence coordinates: keep the diff unless page text coverage is strong enough to suppress safely.
- Conflicting sources: prefer keeping the diff with `NEEDS_REVIEW`.
- Visual group classification failure: keep real seal/signature/date/meaningful-scan-mark differences in output rather than dropping them.
- Protected-value uncertainty: never suppress automatically.

## Testing

Add focused unit tests around existing services:

- Header/footer and bottom handwriting fragments are excluded from main diffs.
- Cover extra text ignores scanner watermark and edge artifacts.
- Native text equality suppresses bracket OCR false positives.
- Heading coverage suppresses section-title drift when the opposite page contains the same heading.
- Page 2 title and party A are recovered as key-field diffs.
- Page 36 signing date is extracted as a focused field diff.
- Seal/signature regions with low-confidence OCR use stable descriptions.

Add protection tests:

- `第一种方式` versus `第二种方式` remains a real diff.
- `2026年 月 日` versus `2026年5月6日` remains a real date diff.
- Real party-name changes are not suppressed.
- Real seal/signature additions remain visible in the visual/signing/scan group.

## Task-Level Verification

After implementation, rerun task `4736c006-cb0e-4ef0-8b39-3594ac73aced` using the same uploaded PDFs.

Render and inspect at least:

- Page 2: contract title and party A.
- Page 27: D062-D064 right-edge OCR omissions.
- Page 34: participant handwritten signatures.
- Page 36: technical agreement signing date.
- Page 44: `〔2006〕34号` bracket equality.
- Pages 12, 29, and 53: seal/signature regions.

Expected verification outcome:

- Confirmed footer, watermark, edge, heading, and bracket OCR false positives do not appear in the main diff list.
- Confirmed title, party A, and page 36 signing-date false negatives appear as focused key-field diffs.
- Signature, seal, participant handwriting, and meaningful scan-mark changes appear in the separate visual/signing/scan group.
- Real option change `第一种方式 -> 第二种方式` remains in the main diff list.
- Debug decisions explain every suppression or reclassification.

## Rollout

1. Implement native text indexing and equality guard.
2. Tighten non-body visual classification for footer, watermark, edge, seal, and signature artifacts.
3. Add key-field extraction for page 2 identity fields and signing/appendix cover fields.
4. Add heading coverage suppression.
5. Stabilize seal/signature descriptions and grouping metadata.
6. Run unit tests, compile checks, and targeted task rerun.
7. Render key pages and compare before/after task outputs.

## Success Criteria

- Main diff results are no longer dominated by OCR noise from footers, watermarks, headings, and seal fragments.
- The known page 2 title change, page 2 party A change, and page 36 signing-date change are reported.
- Real signature, seal, handwriting, and meaningful scan-mark differences remain visible but are grouped separately.
- Existing public compare API behavior stays backward compatible.
- The fix is covered by unit tests and task-level PDF render verification.
