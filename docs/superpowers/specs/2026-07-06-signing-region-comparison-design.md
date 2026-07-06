# 独立签章区对比设计

## 背景

当前项目已有合同对比主链路，后端以 FastAPI + pipeline stages 组织，前端通过 Vite/React 展示差异。签章相关能力目前分散在多个位置：

- `backend/app/services/models/seal_detector.py` 只从 PP-Structure 布局结果中过滤 `seal` / `stamp` 区域。
- `backend/app/services/seal_ocr.py` 对 seal bbox 裁剪后调用 PP-OCRv5 做印章文字识别。
- `backend/app/services/seal_comparator.py` 按页码和位置顺序配对 seal block，仅输出 `source_type="seal"` 的文字差异。
- `backend/app/services/pipeline_stages.py` 的 `PreClauseDiffStage` 同时负责 header/footer、metadata、table、seal OCR 和 seal diff。
- `frontend/src/pages/UploadPage.tsx` 文案是“排除签章区域”，但后端 `ignore_stamps` 实际只过滤 `seal`。
- `DiffQualityProcessor` 中已有签署日期、签章噪声、seal artifact 等零散守卫，但没有统一签章区语义。

这些能力不能满足成熟合同比对中的“签章域”需求：印章、手写签名、签署日期、签章标签、签署表格和区域视觉变化应作为一个独立证据域对比，而不是散落在 `seal`、`table`、`metadata`、`header_footer` 或正文条款中。

本设计基于当前分支状态，允许为签章区目标做局部重构，但不迁移既有 OCR/layout 主链路。

## 已确认的设计决策

1. 首版目标采用完整方案：签章区独立建模，包含视觉/签名检测能力。
2. 主解析底座固定为现有 PaddleOCR / PP-Structure / PP-OCRv5，不接入 Docling、Surya 或通用 parser fallback。
3. 签名/视觉检测做成 adapter，支持远程服务和本地 CPU adapter 契约；首个落地实现优先远程服务，本地 adapter 可先提供未配置降级实现。
4. 最终用户差异列表默认以 `source_type="signing_region"` 替代被覆盖的旧 `seal/table/metadata/header_footer` 签章碎片差异；debug artifact 保留原始差异和覆盖映射。
5. 关键词不能单独召回签章区。签章区候选必须有布局、视觉、表单结构、签署页上下文或双版本对应中的至少一个强信号。

## 目标

- 将签章区作为一等语义单元输出独立差异。
- 签章区内部聚合印章、签名、签署日期、签章标签、签署表格和区域视觉指纹。
- 让 `ignore_stamps=true` 的后端行为匹配前端“排除签章区域”文案。
- 降低 seal OCR 碎片、签署页表格碎片、页脚签名残影对最终差异列表的污染。
- 所有用户可见签章区差异都能回溯到 PDF 坐标证据。
- 视觉/签名模型不可用时主对比任务不失败，只降级并标记待复核。

## 非目标

- 不替换现有 PaddleOCR / PP-Structure 主解析链路。
- 不接入 Docling、Surya、LayoutParser 作为生产依赖。
- 不设计通用 `DocumentParserAdapter`。
- 不实现电子签章证书链验签。若 PDF 数字签名验签以后进入范围，应作为单独子项目。
- 不在首版重写完整表格对比逻辑；签章区内仅消费签署相关表格单元格信号。

## 架构

新增 `SigningRegionStage`，插入 `PreClauseDiffStage` 之后、`SplitStage` 之前。该 stage 消费已经生成的结构类差异和文档 blocks，生成签章区差异和旧差异覆盖映射。

```text
ExtractionStage
DocumentUnderstandingStage
DocumentPreparationStage
PreClauseDiffStage
  - header_footer_diffs
  - metadata_diffs
  - table_diffs
  - seal_diffs
SigningRegionStage
  - detect signing regions from existing Document blocks
  - enrich regions with visual/signature adapters
  - compare original vs compare regions
  - emit signing_region_diffs
  - build coverage map for legacy signing fragments
SplitStage
MatchStage
ClauseDiffStage
EvidenceStage
OcrQualityStage
OcrRemediationStage
ModelRoutingStage
DiffQualityStage
VisualizationStage
SummaryStage
  - hide covered legacy diffs from final user list
  - keep coverage details in debug artifacts
```

`SigningRegionStage` should not be folded into `PreClauseDiffStage`: the existing stage already mixes multiple responsibilities, while signing region comparison must consume and sometimes suppress those pre-clause outputs.

## New Modules

Create a focused package:

```text
backend/app/services/signing_region/
  __init__.py
  models.py
  extractor.py
  visual.py
  matcher.py
  comparator.py
  diff_builder.py
  coverage.py
```

Responsibilities:

- `models.py`: internal Pydantic/dataclass models for signing regions, elements, visual detections, comparisons, and coverage.
- `extractor.py`: convert existing `Document` blocks into `SigningRegion` candidates.
- `visual.py`: OpenCV fingerprinting plus `VisualSignatureDetector` protocol and adapters.
- `matcher.py`: match original and compare regions.
- `comparator.py`: compare matched regions by element type and visual signals.
- `diff_builder.py`: convert region comparisons to `DiffItem(source_type="signing_region")`.
- `coverage.py`: determine which legacy diffs are covered by signing region diffs.

## Data Model

Internal models live under `backend/app/services/signing_region/models.py`:

```text
SigningRegion
  region_id
  page_no
  bbox
  region_role: party_a | party_b | both_parties | signature_page | unknown
  confidence
  confidence_reasons
  elements: list[SigningElement]

SigningElement
  element_id
  element_type: seal | signature | label | date_field | signing_table | visual_area
  bbox
  page_no
  text
  confidence
  source: layout | ocr | visual_model | visual_fingerprint | inferred
  visual_hash
  model_name
  raw_ref

SigningRegionComparison
  comparison_id
  original_region
  compare_region
  diff_type
  match_confidence
  seal_changes
  signature_changes
  date_changes
  label_changes
  table_changes
  visual_changes
  review_flags

SigningCoverageMap
  signing_region_diff_id
  covered_diff_ids
  reasons
```

Public model changes are intentionally small:

- Extend `DiffSourceType` with `"signing_region"`.
- Extend `CompareOptions` with `signing_region_mode: Literal["full", "off"] = "full"`.
- Keep `ignore_stamps`; when `true`, final user-visible `seal` and `signing_region` diffs are both hidden.

Evidence methods:

- `signing_region`
- `signing_region_element`
- `signing_region_visual`

## Detection Rules

The extractor uses multi-signal detection from existing `Document.pages[].blocks`.

Positive signals:

- `block_type in {"seal", "stamp", "image", "figure", "table"}` in the lower page area or near signing labels.
- Table or text cells containing signing labels such as `甲方`, `乙方`, `盖章`, `签章`, `签字`, `签订日期`, `法定代表人`, `授权代表`.
- Form-like structure: multiple colons, underline placeholders, blank date placeholders, paired left/right signature columns.
- Explicit signing page context such as `以下无正文`, `签署页`, `签字页`, `合同签署页`.
- Opposite-side support: if one document has a high-confidence signing region, the corresponding page/position in the other document may produce a lower-confidence paired region.
- Optional visual adapter output for signatures or visible signing marks.

Strict constraints:

- A lone keyword never creates a signing region.
- `甲方` / `乙方` / `盖章` / `签字` inside numbered clauses, long body paragraphs, or normal contractual sentences is excluded.
- Text containing body predicates such as `应当`, `负责`, `承担`, `履行`, `支付`, `违约`, `权利`, `义务` is excluded unless supported by strong visual/layout evidence.
- Long text blocks are not signing labels.
- Low-confidence candidates can enter `debug/signing_region.json`, but do not produce final `signing_region` diffs.

Confidence tiers:

- High: seal/image/table or visual model evidence plus signing label/form context.
- Medium: form/signature page context plus paired role layout.
- Low: keyword-heavy signing context without visual/layout evidence, used only for debug or paired weak-side completion.

## Visual and Signature Adapter

`visual.py` defines:

```text
VisualSignatureDetector
  detect(pdf_path, regions, task_id) -> VisualDetectionResult

RemoteVisualSignatureDetector
  calls configured HTTP service

LocalCpuVisualSignatureDetector
  adapter contract for local model files
  may return unavailable until configured

OpenCvSigningRegionFingerprinter
  crop region
  compute perceptual hash / color stats / simple visual similarity inputs
```

Failure behavior:

- Remote timeout, HTTP error, malformed payload, missing local model, or crop failure never fails the compare task.
- The stage records adapter status in debug and adds review flags such as `SIGNING_MODEL_UNAVAILABLE`, `SIGNING_VISUAL_UNAVAILABLE`, or `SIGNING_VISUAL_LOW_CONFIDENCE`.

## Matching

Match original and compare signing regions before element comparison.

Region match score:

- Page number equality or `±1` page tolerance.
- BBox proximity or IoU.
- Region role agreement.
- Label text similarity.
- Seal OCR text similarity.
- Visual hash / region similarity.

首版签章区数量通常很少，可以先实现带阈值的确定性贪心匹配。若真实任务中出现明显歧义，再在不改变外部接口的前提下替换为匈牙利匹配。

## Comparison

Element comparisons:

- `seal`: count, position, OCR text, visual hash, red-area ratio.
- `signature`: presence, count, position, model confidence, visual similarity.
- `date_field`: placeholder/blank/actual date, normalized to `YYYY-MM-DD` where possible.
- `label`: party and signing labels by role.
- `signing_table`: only signing-related cells, not the full table diff algorithm.
- `visual_area`: region-level image fingerprint and similarity.

Output one `DiffItem(source_type="signing_region")` per changed region. Example readable changes:

- `甲方签章区：新增印章，签署日期由空白变为 2026-05-06`
- `乙方签章区：检测到新增手写签名`

Review flags:

- `SIGNING_SEAL_CHANGE`
- `SIGNING_SIGNATURE_CHANGE`
- `SIGNING_DATE_CHANGE`
- `SIGNING_LABEL_CHANGE`
- `SIGNING_VISUAL_CHANGE`
- `SIGNING_SEAL_OCR_UNAVAILABLE`
- `SIGNING_MODEL_UNAVAILABLE`
- `SIGNING_VISUAL_UNAVAILABLE`
- `SIGNING_VISUAL_LOW_CONFIDENCE`

## Legacy Diff Coverage

`SigningRegionCoverage` hides legacy signing fragments from the final user-visible list.

Coverage only applies when:

- legacy diff `source_type` is one of `seal`, `table`, `metadata`, `header_footer`; and
- evidence bbox overlaps a confirmed signing region; and
- text/title/review flags indicate signing-related content, or the legacy source is `seal`.

Coverage does not apply to `clause` diffs in the first version. If a clause diff later proves to be a signing-page OCR fragment, handle that as a separate guarded enhancement.

`SummaryStage` uses `ctx.signing_region_covered_diff_ids` to filter final diffs. It also writes debug data so reviewers can inspect what was hidden and why.

## Pipeline Changes

`PipelineContext` additions:

```text
signing_regions_original
signing_regions_compare
signing_region_diffs
signing_region_covered_diff_ids
signing_region_debug
```

`ClauseDiffStage` changes:

- Include `len(ctx.signing_region_diffs)` in `pre_clause_count`.
- Merge `ctx.signing_region_diffs` into the diff list before clause diffs.

`SummaryStage` changes:

- If `ignore_stamps` or `signing_region_mode == "off"`, filter `seal` and `signing_region`.
- Filter covered legacy diff ids from the final list.

`CompareDebugWriter` changes:

- Add `write_signing_region(...)`.

## API and Frontend

Backend API:

- Keep existing `/api/compare`.
- Add optional form field `signing_region_mode`.
- Preserve old clients: omitted mode means `"full"`.
- `ignore_stamps=true` hides both old `seal` diffs and new `signing_region` diffs from final results.

Frontend:

- Keep the existing upload checkbox text: `排除签章区域`.
- Do not add a complex signing mode selector in the first version.
- `ResultPage` adds a `SIGNING` group:
  - 正文差异
  - 签章区差异
  - 结构与质量提示
  - 其他差异
- `PdfDocumentViewer` adds a dedicated signing region highlight style.

Report:

- Add source label `signing_region: "签章区"`.
- Sort signing regions near other high-value differences, before low-level seal/header-footer noise.

## Debug Artifact

Write `debug/signing_region.json`:

```text
original_regions
compare_regions
matches
comparisons
visual_adapter_status
covered_legacy_diffs
suppressed_low_confidence_candidates
configuration
```

This artifact is required because final results intentionally hide covered legacy diffs.

## Error Handling

- PP-Structure has no `seal/image/table`: do not fall back to keyword-only signing regions.
- Seal OCR failure: keep region, add `SIGNING_SEAL_OCR_UNAVAILABLE` if relevant.
- Visual service unavailable: keep non-visual comparison, add `SIGNING_MODEL_UNAVAILABLE`.
- Crop failure: keep text/layout comparison, add `SIGNING_VISUAL_UNAVAILABLE`.
- Low-confidence candidate: debug only, no final `DiffItem`.

## Test Plan

Detection tests:

- Seal + label + date creates one signing region.
- Left/right party columns create two regions.
- Lone keywords do not create regions.
- Body sentences containing signing words do not create regions.
- Bottom-of-page body text does not create regions.
- Form structure with signing context creates a region.
- Opposite-side confirmed region can weakly complete a paired region.

Visual adapter tests:

- Remote adapter parses signature bbox.
- Remote timeout/error returns unavailable.
- Local adapter without model returns unavailable.
- OpenCV fingerprinting handles crop failure gracefully.

Comparison tests:

- Added/deleted seal.
- Seal OCR text change.
- Added/deleted signature.
- Date placeholder to actual date.
- Label text change.
- Visual change with unchanged OCR.
- Identical regions produce no diff.
- Low-confidence candidates are debug-only.

Pipeline/frontend tests:

- `ignore_stamps=true` hides `seal` and `signing_region`.
- Covered legacy diffs are absent from final diffs.
- Debug artifact records covered diff ids and reasons.
- Result page groups signing region diffs separately.
- PDF viewer renders signing region evidence boxes.
- Existing `test_seal_comparator.py` and current pipeline tests continue to pass.

## Acceptance Criteria

- Signing region differences are shown in an independent frontend group.
- Legacy signing fragments do not pollute final user-visible results.
- Keyword-only matching cannot create signing region diffs.
- Visual/signature model failures do not fail the task.
- Every final signing region diff has PDF bbox evidence.
- `ignore_stamps` behavior matches the existing UI copy.
- A task like `4736c006-cb0e-4ef0-8b39-3594ac73aced` should aggregate seal fragments into signing region differences instead of surfacing multiple isolated seal OCR diffs.

## Phasing

1. Phase 1: signing region models, extractor, and debug artifact.
2. Phase 2: region matching, comparison, coverage map, and final-list replacement.
3. Phase 3: visual/signature adapter and OpenCV fingerprints.
4. Phase 4: frontend grouping, highlight style, and report label.
5. Phase 5: real-task regression and threshold tuning.

## Sources Considered

- PaddleOCR / PP-Structure as the existing OCR/layout family: https://github.com/PaddlePaddle/PaddleOCR
- OpenCV image hash module for lightweight visual fingerprints: https://docs.opencv.org/4.x/d4/d93/group__img__hash.html
- Signature detection model direction for optional visual adapter design: https://huggingface.co/blog/samuellimabraz/signature-detection-model
- Docling, LayoutParser, and Surya were discussed as parser alternatives, then explicitly excluded from the first version by design decision.
