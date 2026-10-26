# 固定 PP-DocLayoutV3 的 Extraction Challenger

已固定 PP-DocLayoutV3 为唯一 Layout 模型，并实现、运行实验管线。没有再推理 plus-L、测试第三模型或修改生产代码。当前状态为 **EXPERIMENTAL_CHALLENGER_EXECUTED，完整生产业务验收尚未通过**，不能把管线跑通等同于最终 diff/evidence 兼容。

代码位于 [`experiments/extraction_challenger/`](../../experiments/extraction_challenger/README.md)，证据位于 [`output/analysis/extraction-challenger-v3-20260923/`](../../output/analysis/extraction-challenger-v3-20260923/)。旧选型报告和 README 已标记为历史资料，原始证据未删除。

## 1. 已落实的路径和职责

```text
PDF native reader
  → PP-DocLayoutV3（仅 Layout）
  → native / polygon geometry merge
  → SemanticRoleResolver
  → coverage analysis
  → selective PP-OCRv6
  → Unified Page Artifact
  → 现有 TextBlock / Page
  → 现有 ClauseSplitter
```

OCR 新增文字再经同一 Resolver 赋予最终角色。Layout 不提供 canonical text，不承担 OCR Recognition，也不修改 ClauseSplitter 规则。NATIVE_GOOD 的 canonical text 全取 native；MIXED 遮盖已覆盖 native glyph 后只送缺失区域；SCANNED 只送一次整页 OCR。没有调用 `/layout-parsing`、LLM 或通过整页 OCR 补判标题。

原始网络 bbox、score、label、order、无损 masks 已保存。网络 masks 是原始 polygon 的来源，额外用官方 decoder 生成全部 title-like polygon，并保存全部候选阈值的 official postprocess result。业务结果单独写 `resolved_block_type/confidence/reason`；`raw_layout_type/raw_score/raw_polygon/raw_bbox/raw_network_order/raw_model_order/raw_full_model_sequence` 保持原值。[原始文件 hash 清单](../../output/analysis/extraction-challenger-v3-20260923/raw-layout-manifest.json)。

每页 artifact 同时保留 polygon、bbox、PDF cropbox/rotation 对应的 coordinate_transform、native chars/lines/font/size。Polygon 真正参与字符归属；旧 Page 使用 enclosing bbox，原 polygon 留在 artifact。单行边界标点只在已有区域与该 glyph 相交、有同一 native 行证据时附着，并单独写 assignment_reason，不改 V3 polygon。

Reading order 首先使用 V3 完整模型序列，因此 `order=null` 的 table/image 不会被移动到页尾。没有 V3 region 的块按邻近列与几何关系插入，记录 `v3_model` 或 `geometry_fallback`。旋转坐标往返、polygon 与 bbox 区别、null order、数组打乱不变性均有回归测试。

## 2. Baseline subtype 链审计

审计保存了 21 个生产函数的代码、行号和 hash：[baseline-role-chain-audit.json](../../output/analysis/extraction-challenger-v3-20260923/baseline-role-chain-audit.json)。链路为：

| 阶段 | 当前生产行为 |
|---|---|
| PPStructureExtractor → PPStructureLayoutAdapter | raw parsing label 转为 layout region，再生成 TextBlock |
| hybrid `_attach_structure` / `_merge_page_blocks` | 把匹配到的 Structure 类型和 geometry 贴到 OCR 文字，执行合并与短注释修复 |
| ExtractionStage → NativeHeadingRepair | 使用同一 PDF 的 native heading/样式证据修复或插入标题 |
| DocumentPreparationStage → DocumentPreparer | 配对角色恢复、section context、重复标题过滤等，可修改最终 block_type/role/enter_clause_compare |
| cover extractor / ClauseHeading / ClauseSplitter | 消费最终 subtype；doc_title 与 paragraph_title 在封面过滤、无编号标题和 evidence ownership 上不同 |

所以，生产最终 subtype 本来就可以经过上下文修复。但 **B p13 的 doc_title 特例来自 retained PP-Structure raw label**：p1/p14 raw 与 extraction 均为 paragraph_title，p13 raw 与 extraction 均为 doc_title；三个 retained block 的 semantic_reasons 均为空。没有发现可以把这个差异合理推广的上下文规则，因此没有按页号、Chapter 数字或 baseline 答案拟合。

实验 `SemanticRoleResolver` 直接调用已有 ClauseHeading/ClauseSplitter、cover predicates、NativeHeadingRepair native candidates、DocumentPreparer section predicates、repeated_title_ids。没有复制一套相似分类器。没有调用完整 DocumentPreparer.prepare 或 NativeHeadingRepair.repair，因为这些入口还会改文字、插块或执行图像分析；此次只复用所需角色规则。双文档 peer 修复没有拿 baseline 充当输入。

## 3. V3 标题阈值证据

采集 **84 页、17 个 PDF**：B 全文 40 页；既有 Golden/真实合同 44 页。B 是已有性能 fixture，不能当作真实合同。每份 Golden original 选择封面、第二页和中间页，另外沿用已有 scan/table/mixed 页面，采样不看模型分数。

实际 **84 次 Layout forward，Layout-only 阶段 OCR/PP-Structure 调用为 0**。所有阈值复用同一次网络结果，执行官方后处理，没有自定义 NMS。共保存 **6,018 个 raw title-like proposal**，包括 doc_title、paragraph_title、figure_title。[完整候选](../../output/analysis/extraction-challenger-v3-20260923/all-title-like-proposals.json)、[逐页 ledger](../../output/analysis/extraction-challenger-v3-20260923/layout-ledger.json)。

本轮结合页面图像和 native 行复核了 11 页、64 条独立视觉标题行；这是 agent 复核，不冒称独立人工真值。3 份真实合同、9 条标题行用于校准；其余 8 页用于文档留出检查，其中有 5 页 B fixture。**真实合同部分合计只有 19 条标题行**，不能用大量 B 标题掩盖验证集偏小。[标注及口径](../../output/analysis/extraction-challenger-v3-20260923/title-annotations.json)。

标题独立成行才计入视觉标题真值；正文中的编号、字段名前缀不自动成为视觉标题。标题行召回以独立标注为分母，漏检不会消失。Proposal 的 true_title/non_title 分布另存，[score distribution](../../output/analysis/extraction-challenger-v3-20260923/title-score-distribution.json)、[分数直方分箱](../../output/analysis/extraction-challenger-v3-20260923/title-score-histogram.json)。

| title 阈值；其他类别固定 0.5 | 校准集召回 | 留出集召回 | 全部标题行召回 | 新增误报区域 | duplicate pairs |
|---|---:|---:|---:|---:|---:|
| 0.50 | 6/9 | 44/55 | 50/64 | 0 | 0 |
| 0.45 | 7/9 | 47/55 | 54/64 | 0 | 0 |
| **0.40** | **8/9** | **50/55** | **58/64** | **0** | **0** |
| 0.35 | 8/9 | 51/55 | 59/64 | 1 | 0 |
| 0.30 | 8/9 | 51/55 | 59/64 | 2 | 0 |

误报来自包含正文的“二、质量要求…”区域，以及正文金额标签“2、合同总价：”。0.35 能补回天津辛口的“气象服务合同”，同时也增加正文误报。

选择规则为：在真实合同校准集误报为 0 的候选中最大化标题召回，同分选较高阈值。因此当前实验选择 **T=0.4**，不是根据 B p14=0.453 单点设置。真实合同 19 条标题行的纯 Layout 召回为 **9/19 → 13/19**；B p1/p14/p17/p19 均补回。`figure_title` 是单独的 caption 类别，完整收集但本轮不降其 0.5 阈值。

策略由评估器写入 [title-policy.json](../../output/analysis/extraction-challenger-v3-20260923/title-policy.json)，runner 读取策略；没有写入生产默认值。[逐页阈值结果](../../output/analysis/extraction-challenger-v3-20260923/title-threshold-study.json)。

## 4. 融合与 Selective OCR 运行结果

| 路由 | 页面数 | OCR 请求范围 |
|---|---:|---|
| NATIVE_GOOD | 69 | 0 次 |
| MIXED | 8 | 合并重叠后 11 个 region 请求 |
| SCANNED | 7 | 每页 1 次，共 7 个 page 请求 |

首次取得 18 份独立 OCR 响应，规则修正时只回放这些响应，没有再次识别。每个请求保存输入 PNG/hash、masked native indices、crop offset、原始响应和回投 polygon。服务家族使用用户确认的 PP-OCRv6；现有适配器类名仍叫 PPOCRV5Extractor，此处仅复用其响应解包函数，不加载本地 v5 模型。精确权重和服务内部 forward 数未暴露，明确保留 unknown，不能把 18 个 HTTP 输入写成已证明的 18 次 Recognition 网络 forward。[OCR ledger](../../output/analysis/extraction-challenger-v3-20260923/ocr-ledger.json)。

84 页的可靠 native codepoint 均只消费一次，canonical 字符可由 provenance 反向重建。不可用 native 字符仍保存在 raw 列表，仅对应区域进入 OCR。复用了现有 suspicious-char 检查；复杂错误编码、完整隐藏文本层可靠性尚未做专项认证。

所有采集页面已经生成 Unified Artifact、Page adapter 和连续页面组 ClauseSplitter 输出。采样中不连续的页面不会被伪造成相邻页。

## 5. 分账结果与剩余差距

以下标题/geometry/order 真值范围限上述 11 页；native conservation 和路由检查覆盖全部 84 页。

| 错误类别 | 当前观察 | 解释 |
|---|---:|---|
| LAYOUT_RECALL_ERROR | 6 条标题行 | 4 条 RETURN_CONFIRMATION 封面标题、1 条天津辛口封面标题、1 条黄河海西“七、付款方式” |
| SEMANTIC_ROLE_ERROR | 0 | 在已召回标题及复核非标题块中未观察到错误；不代表全部 84 页都已标注 |
| GEOMETRY_ASSIGNMENT_ERROR | 0 | 标题边界标点拆块已修复；不代表所有表格/多栏 geometry 已认证 |
| READING_ORDER_ERROR | 0 | 复核标题顺序无反转；复杂全文顺序仍需专门真值 |
| OCR_ROUTING_ERROR | 0 | 69 native 页无请求、7 scan 页各一次；无已知 native/OCR 重复归属 |

6 条 Layout 漏检中，5 条封面标题由已有 NativeHeadingRepair 证据恢复为 doc_title，原 Layout 漏检账仍保留。黄河海西“七、付款方式”仍为 text；ClauseSplitter 可以依据文字编号处理条款边界，但这不算 Layout 召回成功。未用 ClauseHeading 的条款起点判断无条件提升 native 正文，因为编号正文与视觉标题是不同概念。[failure ledger](../../output/analysis/extraction-challenger-v3-20260923/failure-ledger.json)。

B 全文切分已执行，baseline 为 114 条、Challenger 为 139 条，存在实际 boundary/text/page membership 差异。第一个差异涉及后页 Chapter 标题原先并入上一条正文、现在被当作章节标题。计数只是摘要，保存了逐条内容、标题、页号、section_path 差异；没有用计数相同替代兼容验收，也没有把 baseline subtype 当作语义真值。[逐条诊断](../../output/analysis/extraction-challenger-v3-20260923/baseline-clause-diagnostic.json)。现有三位编号规则没有修改。

后续工作固定在 V3：扩大真实合同独立标注和最终业务差异验收；补齐原生 table HTML/cell adapter；检验 scanned/mixed 的文字、公式及 evidence 精度；验证全文 reading order 与已有 Golden 最终 diff/evidence。这里没有启动完整 E2E/性能验收，也没有宣称这些未验证契约已经通过。

## 6. 验证与隔离

**14 个回归测试通过**，覆盖真实封面规则复用、B 一致角色处理、正文不因编号被提升、字符守恒、原始结果不变、polygon 归属、标点边界、旋转变换、null order、fallback 插入、三种 OCR 路由及重复区域合并。官方后处理 0.4 的离线重放与原采集结果在全部 84 页逐字段相同。

运行前后核对 `backend/app/**/*.py` hash，全部未变。实验只在新目录及证据目录运行，旧文档仅增加决策失效提示。没有改生产模型、ExtractionStage、ClauseSplitter 或服务部署。[verification.json](../../output/analysis/extraction-challenger-v3-20260923/verification.json)。
