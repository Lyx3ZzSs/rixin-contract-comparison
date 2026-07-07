# 签署结构识别与正文剥离重构设计

## 背景

当前分支已经实现了独立签章区对比的工程骨架，包括 `SigningRegionStage`、`SigningRegion` 模型、签章区差异、报告展示、开关和调试产物。但任务 `2659cb76-02a3-4938-b80d-d9a42cc53852` 暴露出当前实现没有达到“识别签署页，并把签署页或签署块从正文中剥离”的标准。

该任务的实际情况是：

- 原版真实签署栏位于第 10 页底部。
- 新版真实签署栏位于第 11 页顶部。
- 第 1 页只是封面签约信息表，包含甲方、乙方、签订地点、签订日期。
- 当前系统把第 1 页封面表格误识别为 `signing_region`。
- 当前系统没有识别原第 10 页和新第 11 页的真实签署栏。
- 普通正文差异 `D011` 混入了 `(盖章)`、`(签字)`、`日期：`、`法人代表或授权委托人` 等签署栏内容。

根因不是 OCR 页码错位，而是当前签章区识别粒度过细、判定信号不足、缺少正文剥离机制。当前实现直接从 OCR/layout blocks 中寻找“签章区”，没有先稳定识别“签署块/签署页”，因此会把封面签约信息表误判成签章区，也无法处理正文页底部或跨页移动的签署栏。

## 已确认决策

1. 采用方案 B：以规则版签署块识别为主，OpenCV 作为视觉辅助，后续检测模型保持可插拔。
2. 继续使用现有 PaddleOCR / PP-OCRv5 / PP-Structure 主解析底座。
3. 不引入 Docling、Surya 或 LayoutParser 作为生产链路。
4. OpenCV 不需要训练数据，只做图像处理、视觉指纹、红章/手写痕迹辅助检测。
5. 高置信签署块从正文条款对比中剥离；混合页只剥离签署块，不整页剥离。
6. 关键词只做召回弱信号，不能单独决定签署块或签章区。

## 目标

- 正确排除第 1 页封面签约信息表，避免生成“签章区（第1页）”。
- 识别原版第 10 页底部的真实签署块。
- 识别新版第 11 页顶部的真实签署块。
- 将高置信签署块从正文条款分割输入中剥离，避免签署栏内容进入普通条款差异。
- 支持原第 10 页和新第 11 页之间的跨页签署块匹配。
- 在签署块内部继续生成独立签章区差异，包括盖章、签字、日期、视觉区域变化。
- 保留原始 PDF 坐标证据，确保报告高亮和调试产物可追溯。
- 在视觉能力不可用时降级为规则识别，不中断主对比任务。

## 非目标

- 不替换现有 OCR/PP-Structure 服务。
- 不实现电子签章证书链验签。
- 不在本阶段训练 PP-YOLOE、RT-DETR 或其他检测模型。
- 不把 OpenCV 作为唯一签署块判定来源。
- 不重写完整条款匹配引擎，只调整签署块对条款分割输入的边界影响。
- 不针对任务 ID、页码、公司名称或合同编号写硬编码规则。

## 概念分层

### SigningPage

`SigningPage` 是页级语义，回答“这一页是否主要承担签署功能”。

典型场景：

- 独立签署页。
- “以下无正文，为签署页”页面。
- 低正文密度且签署栏占主要区域的页面。

用途：

- 判断是否整页从正文条款对比中剥离。
- 辅助签署块匹配和报告说明。
- 给跨页签署结构提供页级上下文。

### SigningBlock

`SigningBlock` 是页内结构语义，回答“页面里的哪一块区域是签署栏”。

典型场景：

- 正文页底部的甲乙方盖章、代表人、签字、日期区域。
- 新版跨页后位于下一页顶部的签署栏。
- 签署页中的甲方/乙方左右栏区域。

用途：

- 作为正文剥离的最小安全单位。
- 作为签章区细分的父级区域。
- 支持混合页只剥离签署块，不剥离正文条款。

### SigningRegion

`SigningRegion` 是签署块内部的细粒度对比区域，回答“签署块中的哪些签章元素发生变化”。

典型元素：

- 盖章区。
- 签字区。
- 日期区。
- 甲方/乙方签署栏标签。
- 印章或手写视觉区域。

用途：

- 生成最终 `source_type="signing_region"` 的独立签章差异。
- 提供报告展示、高亮和复核信息。

## 架构

现有 `SigningRegionStage` 保留在 `PreClauseDiffStage` 之后、`SplitStage` 之前，但职责升级为签署结构识别与正文剥离准备。

```text
ExtractionStage
DocumentUnderstandingStage
DocumentPreparationStage
PreClauseDiffStage
SigningStructureStage
  - SigningPageClassifier
  - SigningBlockDetector
  - SigningRegionExtractor
  - OpenCvSigningVisualAnalyzer
  - ClauseDocumentBuilder
SplitStage
  - 使用剥离签署块后的 clause_document
MatchStage
ClauseDiffStage
EvidenceStage
OcrQualityStage
OcrRemediationStage
ModelRoutingStage
DiffQualityStage
VisualizationStage
SummaryStage
```

命名上可以继续复用 `SigningRegionStage`，也可以在实现中重命名为 `SigningStructureStage`。如果重命名成本较高，优先保持类名兼容，在内部拆分职责。

## 新增与调整模块

```text
backend/app/services/signing_region/
  models.py
  page_classifier.py
  block_detector.py
  extractor.py
  visual.py
  matcher.py
  comparator.py
  diff_builder.py
  coverage.py
  clause_document.py
```

职责：

- `models.py`：新增 `SigningPage`、`SigningBlock`、`SigningBlockRole`，保留并扩展 `SigningRegion`。
- `page_classifier.py`：基于页级文本密度、页角色、签署上下文、签署块密度判断签署页。
- `block_detector.py`：基于多信号评分识别签署块，是本次修复核心。
- `extractor.py`：从签署块内部生成签章区和签署元素，不再直接从整页 blocks 生成最终签章区。
- `visual.py`：实现 OpenCV 区域视觉分析和既有远程视觉适配器契约。
- `matcher.py`：从签章区匹配升级为签署块优先匹配，再匹配块内签章区。
- `clause_document.py`：根据高置信签署块生成正文条款专用文档。

## 数据模型

新增内部模型：

```text
SigningPage
  page_no
  bbox
  page_role
  signing_page_type: full_page | mixed_page | continuation_page | unknown
  confidence
  confidence_reasons
  block_ids
  exclude_full_page_from_clause_diff

SigningBlock
  block_id
  page_no
  bbox
  block_role: both_parties | party_a | party_b | continuation | unknown
  confidence
  confidence_level: high | medium | low
  confidence_reasons
  source_block_ids
  text
  elements
  visual_features
  exclude_from_clause_diff

SigningVisualFeatures
  status
  has_red_seal
  has_handwriting
  visual_hash
  detected_bboxes
  confidence
  reasons
```

现有 `SigningRegion` 继续表示块内对比区域，但需要增加父级引用：

```text
SigningRegion
  signing_block_id
  page_no
  bbox
  region_role
  confidence
  confidence_reasons
  elements
```

`PipelineContext` 需要增加：

```text
signing_pages_original
signing_pages_compare
signing_blocks_original
signing_blocks_compare
clause_document_original
clause_document_compare
signing_region_debug
```

## 签署块识别规则

签署块识别采用多信号评分，不允许单关键词独立通过。

### 正向信号

文本信号：

- `甲方`、`乙方`、`丙方`、`丁方`。
- `盖章`、`签章`、`签字`、`签署`。
- `法定代表人`、`法人代表`、`授权代表`、`授权委托人`、`法人代表或授权委托人`。
- `日期：`、`签订日期`、`签署日期`。
- `以下无正文`、`签署页`、`签字页`。

空间信号：

- 页面底部出现成组签署标签。
- 页面顶部出现签署栏延续，且上一页接近合同正文结束。
- 左右两栏中分别出现甲方/乙方签署信息。
- 签署标签之间的垂直间距较小，形成一个稳定区域。
- 签署块与正文之间有明显空白或位于正文末尾之后。

结构信号：

- 甲乙方同时出现。
- 盖章、代表人、签字、日期至少出现两类以上。
- 同一水平带或相邻水平带形成左右对称签署栏。
- 文本块短而标签化，不是长句条款。

页面信号：

- `page_role="cover"` 时强降权。
- 正文页底部允许识别混合页签署块。
- 低正文密度页或空白页顶部允许识别签署延续块。
- 附件页、目录页、封面页必须有更强证据才可通过。

视觉信号：

- OpenCV 检测到红色印章痕迹。
- OpenCV 检测到手写笔迹。
- PP-Structure 返回 `seal`、`stamp`、`image`、`figure`。
- 签署块视觉 hash 可用于后续差异判断。

### 负向信号

- 封面签约信息表只包含甲方、乙方、签订地点、签订日期，且没有盖章、签字、代表人、签署页上下文或视觉签章证据。
- 正文编号条款中出现 `签章`、`签订`、`盖章后生效` 等合同条款表述。
- 长段落含有 `应当`、`负责`、`承担`、`履行`、`支付`、`违约`、`权利`、`义务`、`合同经`、`协商`、`约定` 等正文谓语。
- 单个 `甲方`、`乙方`、`日期` 或 `签订日期` 不得单独通过。
- 页边骑缝章或边缘红章不能单独使封面信息表成为签署块。

### 置信度分层

高置信签署块：

- 至少有甲乙方结构，并且盖章/签字/代表人/日期中出现两类以上。
- 或有明确签署页上下文，并且有表单结构。
- 或有签署栏文本结构并由 OpenCV/PP-Structure 视觉信号确认。

中置信签署块：

- 有较完整签署标签结构，但视觉信号缺失。
- 可进入签署对比候选，但是否剥离正文需要结合页级上下文。

低置信候选：

- 仅有弱关键词或位置相似。
- 只进入 debug，不生成最终差异，不剥离正文。

## OpenCV 设计

OpenCV 不需要训练数据。它作为 `SigningBlockDetector` 后的视觉辅助模块，不单独决定签署块成立。

处理流程：

```text
候选签署块 bbox
  -> PDF 页面渲染
  -> 区域裁剪
  -> HSV 红色阈值
  -> 连通域与轮廓过滤
  -> 灰度/边缘/纹理统计
  -> 视觉 hash
  -> 返回 SigningVisualFeatures
```

首版能力：

- 红章痕迹检测：识别红色连通域、过滤页边噪声和过小碎片。
- 手写痕迹辅助：识别局部非印刷黑色线条密度变化，只作为弱证据。
- 区域视觉 hash：对签署块整体生成稳定指纹，用于判断视觉变化。

失败行为：

- `cv2` 不可用、PDF 渲染失败、区域裁剪失败时返回 `status="unavailable"`。
- 不中断合同对比任务。
- 在 `debug/signing_region.json` 记录失败原因。

## 正文剥离设计

新增 `ClauseDocumentBuilder`，基于原始 `Document` 和高置信 `SigningBlock` 生成条款专用文档。

原则：

- 原始文档不变，用于证据定位、高亮、报告下载和调试。
- 条款文档移除或隐藏高置信签署块覆盖的 blocks。
- `SplitStage` 使用条款文档，而不是完整原始文档。
- 被剥离的 block id 写入 debug。

剥离策略：

- 对高置信 `SigningBlock.exclude_from_clause_diff=true` 的块，移除与其 bbox 高重叠的 OCR/layout blocks。
- 对整页签署页，可整页从条款文档移除。
- 对混合页，只移除签署块区域，不移除同页正文。
- 对中置信块，默认不剥离，除非两侧均能匹配并且上下文强确认。

该设计必须解决 `D011` 类型问题：签署栏不能继续并入 `14.2` 条款，也不能跨签署块继续把后续附件页内容合并进同一条款。

## 匹配设计

签署匹配分两级：

1. 匹配 `SigningBlock`。
2. 在匹配到的签署块内部匹配 `SigningRegion` / `SigningElement`。

`SigningBlock` 匹配不只依赖页码和坐标：

- 甲乙方名称相似度。
- 签署栏结构相似度。
- `block_role` 是否一致。
- 页码相邻或发生合理位移。
- 在文档结构上是否靠近合同正文结束。
- 视觉 hash 或红章/手写特征是否可比。

允许场景：

- 原第 10 页底部匹配新第 11 页顶部。
- 原签署块在混合页，新签署块在独立延续页。
- 新增一页导致签署块整体后移一页。

不允许场景：

- 封面信息表匹配真实签署栏。
- 正文中含签章关键词的条款匹配签署块。
- 页边骑缝章单独匹配为签署块。

## 差异输出

最终用户可见差异保持 `source_type="signing_region"`，但标题和证据应支持跨页表达。

示例：

```text
签署区（原第10页 / 新第11页）
```

差异类型：

- 签署块新增/删除。
- 甲乙方签署栏文字变化。
- 盖章区变化。
- 签字区变化。
- 日期区变化。
- 视觉区域变化。

正文差异中不再重复报告已剥离签署块内容。

## Debug 产物

`debug/signing_region.json` 需要扩展：

- `signing_pages`：页级评分和原因。
- `signing_blocks`：块级 bbox、source block ids、评分原因、置信等级。
- `excluded_cover_candidates`：被排除的封面签约信息表等候选。
- `clause_exclusion`：从条款文档剥离的 block ids 和原因。
- `block_matches`：跨页匹配分数和理由。
- `opencv_status`：OpenCV 可用性、失败原因、视觉特征摘要。
- `suppressed_low_confidence_candidates`：低置信候选。

调试信息必须能解释为什么第 1 页封面表格被排除，以及为什么第 10/11 页签署栏被识别。

## 测试计划

新增后端测试优先覆盖以下场景：

1. 封面签约信息表不生成签署块。
2. 第 10 页底部甲乙方、盖章、代表人、签字、日期结构生成高置信签署块。
3. 第 11 页顶部签署延续结构生成高置信签署块。
4. 单个正文句子“本合同经双方盖章后生效”不生成签署块。
5. `法人代表或授权委托人`、`日期：` 可作为签署栏强信号参与评分。
6. 高置信签署块从条款文档剥离。
7. `SplitStage` 使用剥离后的条款文档。
8. 原第 10 页底部和新第 11 页顶部签署块可以匹配。
9. `D011` 类型正文差异不包含签署栏文本。
10. OpenCV 不可用时不影响主流程，只记录 debug 状态。

回归测试需要保证：

- 现有签章区开关 `signing_region_mode="off"` 仍生效。
- `ignore_stamps=true` 仍隐藏 `seal` 和 `signing_region`。
- 既有表格、封面元数据、页眉页脚差异不被无关影响。
- 现有视觉适配器配置为空时不发起网络调用。

## 分阶段实施

### 阶段一：签署结构模型与回归测试

- 新增 `SigningPage`、`SigningBlock`、视觉特征模型。
- 为当前任务抽象出最小测试夹具。
- 先写失败测试，证明第 1 页误检和第 10/11 页漏检。

### 阶段二：签署块检测

- 实现 `SigningBlockDetector`。
- 增强签署关键词，但只作为多信号评分的一部分。
- 增加封面签约信息表排除规则。
- 生成签署块 debug。

### 阶段三：签章区细分与兼容

- 调整 `SigningRegionExtractor`，从签署块内部生成 `SigningRegion`。
- 保持既有 `SigningRegionComparator` 和 `DiffBuilder` 的外部行为。
- 调整 matcher 支持块级跨页匹配。

### 阶段四：正文剥离

- 实现 `ClauseDocumentBuilder`。
- 扩展 `PipelineContext` 保存条款文档。
- 修改 `SplitStage` 使用剥离后的条款文档。
- 验证签署栏不再进入普通正文差异。

### 阶段五：OpenCV 辅助

- 实现区域渲染和裁剪。
- 实现红章/手写痕迹弱检测。
- 实现签署块视觉 hash。
- 只作为加分、复核和视觉差异证据，不作为唯一判定来源。

### 阶段六：报告与前端核对

- 确保跨页标题和证据页码正确展示。
- 确保 PDF 高亮仍使用原始文档坐标。
- 确保签署区差异在前端归类到签章/签署分组。

## 验收标准

针对任务 `2659cb76-02a3-4938-b80d-d9a42cc53852`：

- 不再生成“签章区（第1页）”。
- 第 1 页封面签约信息表进入 `excluded_cover_candidates` debug。
- 原版第 10 页底部识别为高置信 `SigningBlock`。
- 新版第 11 页顶部识别为高置信 `SigningBlock`。
- 两个签署块被匹配，匹配理由包含角色、结构和相邻页位移。
- `D011` 不再包含 `(盖章)`、`(签字)`、`日期：`、`法人代表或授权委托人`。
- 最终报告中签署区差异独立展示，并正确标注原第 10 页 / 新第 11 页。

全局验收：

- 后端测试通过。
- Ruff 检查通过。
- 前端测试和构建不因数据结构变化失败。
- 视觉能力未配置时主流程稳定降级。
- 调试产物可解释每个签署候选的通过或排除原因。

## 风险与缓解

风险：规则过严导致部分无章无签字的签署页漏检。

缓解：中置信候选进入 debug，不剥离正文；后续通过样本校准阈值。

风险：规则过宽导致封面、目录、正文条款再次污染签署块。

缓解：封面签约信息表、正文长句、编号条款、正文谓语作为强负向信号。

风险：剥离签署块后影响正文连续性。

缓解：只剥离高置信签署块；混合页保留块外正文；剥离 block ids 写入 debug。

风险：OpenCV 在扫描件偏色、骑缝章、页边章上误判。

缓解：OpenCV 仅加分和提供视觉证据，不单独决定签署块成立；页边连通域作为弱信号处理。

## 实施后续

本设计确认后，下一步进入实施计划编写。实施计划应先覆盖测试与签署块识别，再进入正文剥离和 OpenCV 辅助，避免在视觉增强前继续扩大关键词规则的影响面。
