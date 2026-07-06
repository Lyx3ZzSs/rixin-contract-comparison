# 独立签章区对比设计

## 背景

当前项目已有合同对比主链路，后端以 FastAPI + 流水线阶段组织，前端通过 Vite/React 展示差异。签章相关能力目前分散在多个位置：

- `backend/app/services/models/seal_detector.py` 只从 PP-Structure 布局结果中过滤 `seal` / `stamp` 区域。
- `backend/app/services/seal_ocr.py` 对印章边界框裁剪后调用 PP-OCRv5 做印章文字识别。
- `backend/app/services/seal_comparator.py` 按页码和位置顺序配对印章块，仅输出 `source_type="seal"` 的文字差异。
- `backend/app/services/pipeline_stages.py` 的 `PreClauseDiffStage` 同时负责页眉页脚、封面元数据、表格、印章 OCR 和印章差异。
- `frontend/src/pages/UploadPage.tsx` 文案是“排除签章区域”，但后端 `ignore_stamps` 实际只过滤 `seal`。
- `DiffQualityProcessor` 中已有签署日期、签章噪声、印章伪影等零散守卫，但没有统一签章区语义。

这些能力不能满足成熟合同比对中的“签章域”需求：印章、手写签名、签署日期、签章标签、签署表格和区域视觉变化应作为一个独立证据域对比，而不是散落在 `seal`、`table`、`metadata`、`header_footer` 或正文条款中。

本设计基于当前分支状态，允许为签章区目标做局部重构，但不迁移既有 OCR/layout 主链路。

## 已确认的设计决策

1. 首版目标采用完整方案：签章区独立建模，包含视觉/签名检测能力。
2. 主解析底座固定为现有 PaddleOCR / PP-Structure / PP-OCRv5，不接入 Docling、Surya 或通用解析回退链路。
3. 签名/视觉检测做成适配器，支持远程服务和本地 CPU 适配器契约；首个落地实现优先远程服务，本地适配器可先提供未配置降级实现。
4. 最终用户差异列表默认以 `source_type="signing_region"` 替代被覆盖的旧 `seal/table/metadata/header_footer` 签章碎片差异；调试产物保留原始差异和覆盖映射。
5. 关键词不能单独召回签章区。签章区候选必须有布局、视觉、表单结构、签署页上下文或双版本对应中的至少一个强信号。

## 目标

- 将签章区作为一等语义单元输出独立差异。
- 签章区内部聚合印章、签名、签署日期、签章标签、签署表格和区域视觉指纹。
- 让 `ignore_stamps=true` 的后端行为匹配前端“排除签章区域”文案。
- 降低印章 OCR 碎片、签署页表格碎片、页脚签名残影对最终差异列表的污染。
- 所有用户可见签章区差异都能回溯到 PDF 坐标证据。
- 视觉/签名模型不可用时主对比任务不失败，只降级并标记待复核。

## 非目标

- 不替换现有 PaddleOCR / PP-Structure 主解析链路。
- 不接入 Docling、Surya、LayoutParser 作为生产依赖。
- 不设计通用文档解析适配器 `DocumentParserAdapter`。
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
  - 从现有 Document 块检测签章区
  - 用视觉/签名适配器补充区域证据
  - 对比原版和新版签章区
  - 产出 signing_region_diffs
  - 生成旧签章碎片差异覆盖映射
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
  - 从最终用户列表隐藏已覆盖的旧差异
  - 在调试产物中保留覆盖细节
```

`SigningRegionStage` 不应并入 `PreClauseDiffStage`：现有阶段已经混合多个职责，而签章区对比需要消费并有条件抑制这些预条款差异输出。

## 新增模块

新增一个聚焦的包：

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

职责：

- `models.py`：签章区、签章元素、视觉检测、区域对比和覆盖映射的内部 Pydantic / dataclass 模型。
- `extractor.py`：把现有 `Document` 块转换为 `SigningRegion` 候选。
- `visual.py`：OpenCV 视觉指纹，以及 `VisualSignatureDetector` 协议和适配器。
- `matcher.py`：匹配原版和新版签章区。
- `comparator.py`：按元素类型和视觉信号对比已匹配签章区。
- `diff_builder.py`：把区域对比结果转换为 `DiffItem(source_type="signing_region")`。
- `coverage.py`：判断哪些旧差异被签章区差异覆盖。

## 数据模型

内部模型放在 `backend/app/services/signing_region/models.py`：

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

公共模型改动保持最小：

- `DiffSourceType` 增加 `"signing_region"`。
- `CompareOptions` 增加 `signing_region_mode: Literal["full", "off"] = "full"`。
- 保留 `ignore_stamps`；当它为 `true` 时，最终用户可见列表同时隐藏 `seal` 和 `signing_region` 差异。

证据方法：

- `signing_region`
- `signing_region_element`
- `signing_region_visual`

## 检测规则

提取器基于现有 `Document.pages[].blocks` 做多信号检测。

正向信号：

- 页面下方或签章标签附近存在 `block_type in {"seal", "stamp", "image", "figure", "table"}`。
- 表格或文本单元中包含签章标签，例如 `甲方`、`乙方`、`盖章`、`签章`、`签字`、`签订日期`、`法定代表人`、`授权代表`。
- 表单式结构：多个冒号、下划线占位、空白日期占位、左右成对签署栏。
- 明确签署页上下文，例如 `以下无正文`、`签署页`、`签字页`、`合同签署页`。
- 对侧支撑：如果一份文档在对应位置有高置信签章区，另一份文档同页或相近位置可以生成低置信配对区域。
- 可选视觉适配器输出的签名或可见签章痕迹。

严格约束：

- 单个关键词永远不能独立生成签章区。
- 出现在编号条款、正文长段落或普通合同句子中的 `甲方` / `乙方` / `盖章` / `签字` 必须排除。
- 包含 `应当`、`负责`、`承担`、`履行`、`支付`、`违约`、`权利`、`义务` 等正文谓语的文本，除非有强视觉或版面证据支撑，否则必须排除。
- 长文本块不能作为签章标签。
- 低置信候选可以进入 `debug/signing_region.json`，但不生成最终 `signing_region` 差异。

置信度分层：

- 高置信：印章、图片、表格或视觉模型证据，加上签章标签或表单上下文。
- 中置信：表单或签署页上下文，加上成对角色布局。
- 低置信：有较多签章关键词但缺少视觉或版面证据，仅用于调试或弱侧配对补全。

## 视觉与签名适配器

`visual.py` 定义：

```text
VisualSignatureDetector
  detect(pdf_path, regions, task_id) -> VisualDetectionResult

RemoteVisualSignatureDetector
  调用已配置的 HTTP 服务

LocalCpuVisualSignatureDetector
  本地模型文件适配器契约
  未配置时可以返回不可用

OpenCvSigningRegionFingerprinter
  裁剪签章区域
  计算感知哈希、颜色统计和简单视觉相似度输入
```

失败行为：

- 远程超时、HTTP 错误、响应格式异常、本地模型缺失或裁剪失败，都不能导致合同对比任务失败。
- 阶段会在调试产物中记录适配器状态，并添加 `SIGNING_MODEL_UNAVAILABLE`、`SIGNING_VISUAL_UNAVAILABLE`、`SIGNING_VISUAL_LOW_CONFIDENCE` 等复核标记。

## 匹配

先匹配原版和新版签章区，再进入元素对比。

区域匹配得分：

- 页码一致或允许 `±1` 页容忍。
- 边界框接近度或 IoU。
- 区域角色一致性。
- 标签文本相似度。
- 印章 OCR 文本相似度。
- 视觉哈希或区域相似度。

首版签章区数量通常很少，可以先实现带阈值的确定性贪心匹配。若真实任务中出现明显歧义，再在不改变外部接口的前提下替换为匈牙利匹配。

## 对比

元素对比：

- `seal`：数量、位置、OCR 文本、视觉哈希、红色区域占比。
- `signature`：有无、数量、位置、模型置信度、视觉相似度。
- `date_field`：占位、空白或实际日期，能标准化时统一为 `YYYY-MM-DD`。
- `label`：按角色对比主体标签和签章标签。
- `signing_table`：只处理签署相关单元格，不复用完整表格差异算法。
- `visual_area`：区域级图像指纹和相似度。

每个发生实质变化的区域输出一个 `DiffItem(source_type="signing_region")`。可读变化示例：

- `甲方签章区：新增印章，签署日期由空白变为 2026-05-06`
- `乙方签章区：检测到新增手写签名`

复核标记：

- `SIGNING_SEAL_CHANGE`
- `SIGNING_SIGNATURE_CHANGE`
- `SIGNING_DATE_CHANGE`
- `SIGNING_LABEL_CHANGE`
- `SIGNING_VISUAL_CHANGE`
- `SIGNING_SEAL_OCR_UNAVAILABLE`
- `SIGNING_MODEL_UNAVAILABLE`
- `SIGNING_VISUAL_UNAVAILABLE`
- `SIGNING_VISUAL_LOW_CONFIDENCE`

## 旧差异覆盖

`SigningRegionCoverage` 负责从最终用户可见列表中隐藏旧的签章碎片差异。

只有同时满足以下条件才覆盖：

- 旧差异的 `source_type` 是 `seal`、`table`、`metadata`、`header_footer` 之一；且
- 证据边界框与已确认签章区重叠；且
- 文本、标题或复核标记指向签章相关内容，或旧差异来源本身是 `seal`。

首版不覆盖 `clause` 差异。如果后续确认某个条款差异其实是签署页 OCR 碎片，应作为独立且有保护条件的增强处理。

`SummaryStage` 使用 `ctx.signing_region_covered_diff_ids` 过滤最终差异，同时写入调试数据，让复核人员能看到隐藏了什么以及隐藏原因。

## 流水线改动

`PipelineContext` 新增字段：

```text
signing_regions_original
signing_regions_compare
signing_region_diffs
signing_region_covered_diff_ids
signing_region_debug
```

`ClauseDiffStage` 改动：

- `pre_clause_count` 计入 `len(ctx.signing_region_diffs)`。
- 在条款差异之前把 `ctx.signing_region_diffs` 合并进差异列表。

`SummaryStage` 改动：

- 如果 `ignore_stamps` 为真，或 `signing_region_mode == "off"`，过滤 `seal` 和 `signing_region`。
- 从最终列表过滤已覆盖的旧差异 ID。

`CompareDebugWriter` 改动：

- 增加 `write_signing_region(...)`。

## API 与前端

后端 API：

- 保持现有 `/api/compare`。
- 增加可选表单字段 `signing_region_mode`。
- 兼容旧客户端：未传该字段时视为 `"full"`。
- `ignore_stamps=true` 时，从最终结果隐藏旧 `seal` 差异和新 `signing_region` 差异。

前端：

- 保持现有上传页复选框文案：`排除签章区域`。
- 首版不增加复杂签章模式选择器。
- `ResultPage` 增加 `SIGNING` 分组：
  - 正文差异
  - 签章区差异
  - 结构与质量提示
  - 其他差异
- `PdfDocumentViewer` 增加签章区专用高亮样式。

报告：

- 增加来源标签 `signing_region: "签章区"`。
- 签章区差异排序靠近高价值差异，排在低层级印章或页眉页脚噪声之前。

## 调试产物

写入 `debug/signing_region.json`：

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

该产物是必需的，因为最终结果会有意隐藏已覆盖的旧差异。

## 错误处理

- PP-Structure 没有 `seal/image/table` 时，不能回退到仅凭关键词生成签章区。
- 印章 OCR 失败时，保留区域；如相关，添加 `SIGNING_SEAL_OCR_UNAVAILABLE`。
- 视觉服务不可用时，保留非视觉对比，添加 `SIGNING_MODEL_UNAVAILABLE`。
- 裁剪失败时，保留文本和版面对比，添加 `SIGNING_VISUAL_UNAVAILABLE`。
- 低置信候选只进入调试产物，不生成最终 `DiffItem`。

## 测试计划

检测测试：

- 印章 + 标签 + 日期能生成一个签章区。
- 左右主体签署栏能生成两个签章区。
- 单独关键词不能生成签章区。
- 含签章词的正文句子不能生成签章区。
- 页面底部正文不能生成签章区。
- 表单结构加签署上下文能生成签章区。
- 对侧已确认签章区时，弱侧可以补全配对区域。

视觉适配器测试：

- 远程适配器能解析签名边界框。
- 远程超时或错误时返回不可用。
- 本地适配器未配置模型时返回不可用。
- OpenCV 指纹计算能平稳处理裁剪失败。

对比测试：

- 新增或删除印章。
- 印章 OCR 文本变化。
- 新增或删除签名。
- 日期从占位变为实际日期。
- 标签文本变化。
- OCR 不变但视觉发生变化。
- 完全相同区域不产生差异。
- 低置信候选只进入调试产物。

流水线和前端测试：

- `ignore_stamps=true` 隐藏 `seal` 和 `signing_region`。
- 已覆盖的旧差异不出现在最终差异列表。
- 调试产物记录已覆盖差异 ID 和原因。
- 结果页能单独分组展示签章区差异。
- PDF 查看器能渲染签章区证据框。
- 现有 `test_seal_comparator.py` 和当前流水线测试继续通过。

## 验收标准

- 签章区差异在前端独立分组展示。
- 旧签章碎片不污染最终用户可见结果。
- 仅关键词匹配不能生成签章区差异。
- 视觉或签名模型失败不会导致任务失败。
- 每个最终签章区差异都有 PDF 边界框证据。
- `ignore_stamps` 行为与现有 UI 文案一致。
- 对 `4736c006-cb0e-4ef0-8b39-3594ac73aced` 这类任务，应把印章碎片聚合为签章区差异，而不是暴露多个孤立印章 OCR 差异。

## 分期

1. 阶段 1：签章区模型、提取器和调试产物。
2. 阶段 2：区域匹配、区域对比、覆盖映射和最终列表替代。
3. 阶段 3：视觉/签名适配器和 OpenCV 指纹。
4. 阶段 4：前端分组、高亮样式和报告标签。
5. 阶段 5：真实任务回归和阈值调优。

## 参考来源

- PaddleOCR / PP-Structure 作为现有 OCR 和版面解析体系：https://github.com/PaddlePaddle/PaddleOCR
- OpenCV 图像哈希模块用于轻量视觉指纹：https://docs.opencv.org/4.x/d4/d93/group__img__hash.html
- 签名检测模型方向用于可选视觉适配器设计：https://huggingface.co/blog/samuellimabraz/signature-detection-model
- Docling、LayoutParser 和 Surya 已作为解析替代方案讨论过，并按设计决策明确排除在首版之外。
