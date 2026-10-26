# Extraction Challenger — Business Equivalence Closure

状态：**未达到 Full Golden / A–E 进入条件**。本轮完成 B 的逐边界归因、实验层修复、4 份完整真实合同的 diff ledger，以及 table/evidence 契约检查。仍有明确阻塞，不能把条款数量接近当作业务验收通过。

## 首先回答十个问题

| 问题 | 实测结论 |
|---|---|
| 1. B 为什么 114 → 139？ | 同一份 PDF、相同 1,360 个 canonical source blocks，25 个独立 Chapter 标题在 baseline 最终为 `doc_title`，Challenger 为 `paragraph_title`；现有 ClauseHeading 因此建立了独立边界。 |
| 2. 25 个净新增来自什么机制？ | 25 个都是 subtype 输入变化经 ClauseHeading 触发。分层统计为 23 个 score ≥ 0.5 的标题，以及 2 个固定 0.4 策略召回的低分标题（p17 / p19）。这是互斥分层，不把“标题召回”和“subtype”两条因果轴重复相加。 |
| 3. B 中 Challenger regression？ | 0 / 25。该结论仅限 B 的 25 个边界；真实合同仍有 1 个未修复的明确边界回归。 |
| 4. B 中 baseline defect exposed？ | 25 / 25。PDF 的 Chapter 字体、独立位置、递增编号及后续条款支持独立章节。未把 baseline label 当正确答案。 |
| 5. B 中 ambiguous？ | 0 / 25。真实合同另有 1 组签署区 boundary shift（2 个有符号事件）及 320 项已对齐条款变化待审查。 |
| 6. Resolver 需要修改吗？ | B 不需要。真实合同需要：移除无 title region 的隐式提升；把同样式、冒号引出的正文列表保留为正文；通过现有 DocumentPreparation + ClauseHeading 恢复附件安全协议的标题角色；补齐 V3 公式／页边图片到生产类型的适配。 |
| 7. NativeHeadingRepair 过度提升吗？ | 有。历史 24 例 raw non-title → title 中，13 例是正文／签署字段，已撤销；6 例有封面证据，保留；5 例证据不足，停止提升并复核。13 例单页反事实的新增 clause 数均为 0，不能误计成 B 的 25 个新增。 |
| 8. Table 闭环了吗？ | 没有。19 个表格中 17 个通过自动结构／token assignment 检查，2 个扫描表格结构与 cell bbox 数量不一致，阻止发布 HTML。新增 table OCR = 0。 |
| 9. Evidence coordinates 闭环了吗？ | 自动坐标链和 Agent 视觉抽检通过；独立人工签收未完成。9 处真实页面高亮、4 种受控旋转已查看。OCR 英文词级框不能宣称单 glyph 精度。 |
| 10. 可以 Full Golden / A–E 吗？ | 不可以：尚有 1 个 Layout recall 回归、2 个 table 结构失败，签署区及全文内容归属仍需 review，且未获人工 evidence 验收。未运行性能 benchmark。 |

## 冻结范围与证据

仅使用 PP-DocLayoutV3；title-family threshold=0.4、其他类别=0.5。没有模型选型、阈值搜索、性能 benchmark、PageAlignment／Matching 改动。生产源文件 hash 验证保持不变。修改限于 `experiments/extraction_challenger/` 及本报告。

证据目录：[business-equivalence](../../output/analysis/extraction-challenger-business-equivalence/)。历史 raw V3、官方后处理、网络输出和原始 B artifacts 保留在 [V3 原始证据](../../output/analysis/extraction-challenger-v3-20260923/)。新字段只记录 resolved role、repair evidence、geometry merge 和坐标 provenance，不覆盖 raw label / score / bbox / polygon / order。

## B：精确归因与独立正确性

[全文 ledger](../../output/analysis/extraction-challenger-business-equivalence/B/clause-boundary-diff-ledger.json) 包含每个 source block 的页、id、文本、native indices，以及两侧类型、clause id/no、heading、body membership、section path 和页归属。[因果探针](../../output/analysis/extraction-challenger-business-equivalence/B/attribution.json)、[逐项裁决](../../output/analysis/extraction-challenger-business-equivalence/B/adjudication.json) 和 [最终代码回放](../../output/analysis/extraction-challenger-business-equivalence/B/final-verification.json) 分开保存。

Baseline = 15 个 Chapter 边界 + 99 个数字编号边界 = 114；Challenger = 40 + 99 = 139。NEW_BOUNDARY=25、MISSING_BOUNDARY=0。1360 个块全部用文本和几何准确对齐，native 粒度未改变。B 全部 NATIVE_GOOD，OCR request=0。

| 机制 | 对 B 净新增的贡献 |
|---|---:|
| 高分 raw subtype 差异 | 23 |
| 固定策略召回的低分 title，随后同样发生 subtype 差异 | 2 |
| SemanticRoleResolver 改写 subtype | 0 |
| NativeHeadingRepair | 0 |
| Native block splitting / geometry / reading order | 0 |
| OCR inserted blocks | 0 |
| ClauseSplitter numbering / 三位编号规则 | 0 |
| ClauseHeading | 全部 25 个的下游触发器，不能再加一次 |

只保留 raw V3 类型、去掉 native repair、改用几何顺序，均仍为 139。仅在诊断副本中替换 baseline 类型才回到 114；该替换绝不进入抽取规则。逐个只替换一个差异块的类型，每次从 139 变为 138，且恰好移除对应边界。

| 页 / Chapter | raw score | 分层 | 正确性 |
|---:|---:|---|---|
| 2 | 0.760821 | 高分 subtype | Baseline defect exposed |
| 3 | 0.755455 | 高分 subtype | Baseline defect exposed |
| 4 | 0.652243 | 高分 subtype | Baseline defect exposed |
| 5 | 0.781892 | 高分 subtype | Baseline defect exposed |
| 7 | 0.685389 | 高分 subtype | Baseline defect exposed |
| 8 | 0.738608 | 高分 subtype | Baseline defect exposed |
| 9 | 0.718819 | 高分 subtype | Baseline defect exposed |
| 10 | 0.582949 | 高分 subtype | Baseline defect exposed |
| 11 | 0.511405 | 高分 subtype | Baseline defect exposed |
| 12 | 0.614522 | 高分 subtype | Baseline defect exposed |
| 13 | 0.522544 | 高分 subtype | Baseline defect exposed |
| 17 | 0.434359 | 低分召回 | Baseline defect exposed |
| 19 | 0.485884 | 低分召回 | Baseline defect exposed |
| 20 | 0.656484 | 高分 subtype | Baseline defect exposed |
| 22 | 0.629132 | 高分 subtype | Baseline defect exposed |
| 23 | 0.554543 | 高分 subtype | Baseline defect exposed |
| 24 | 0.585568 | 高分 subtype | Baseline defect exposed |
| 25 | 0.599867 | 高分 subtype | Baseline defect exposed |
| 27 | 0.698066 | 高分 subtype | Baseline defect exposed |
| 29 | 0.607634 | 高分 subtype | Baseline defect exposed |
| 30 | 0.683259 | 高分 subtype | Baseline defect exposed |
| 32 | 0.685352 | 高分 subtype | Baseline defect exposed |
| 34 | 0.603864 | 高分 subtype | Baseline defect exposed |
| 37 | 0.667294 | 高分 subtype | Baseline defect exposed |
| 40 | 0.696453 | 高分 subtype | Baseline defect exposed |

PDF 原文中标题为 14pt，条款正文为 10pt；每页独立 Chapter 标题后跟 8 个连续编号条款。另有生成器 `experiments/e2e_diagnosis/build_cases.py` 的独立结构证据。[全部 25 处视觉核查图](../../output/analysis/extraction-challenger-business-equivalence/B/all-25-boundaries.png) 已由 Codex 查看，未冒称获得独立人工签收。

20 个已有 clause 同时出现 TEXT_CHANGED / PAGE_MEMBERSHIP_CHANGED，是新增 Chapter 边界导致正文与页面重新归属；源文本没有被改写，也不是额外 20 个新增 clause。B ledger 已把这类传播变化与边界事件分账。

三位编号另立 [独立 issue](extraction-challenger-three-digit-numbering-issue.md)：100–320 共 221 个编号，两边都未形成边界。parser 能解析 100，但 `_is_quantity_or_amount_marker` 将三位数过滤为数量值。本轮不改 ClauseSplitter，也不把这 221 项归入 25 个净增量。

## baseline 的最终 subtype 不是 raw model truth

[生产链审计](../../output/analysis/extraction-challenger-v3-20260923/baseline-role-chain-audit.json) 保存了 raw PP-Structure → Layout parser → hybrid extractor 的 `_merge_page_blocks` / `_attach_structure` → native heading repair → document role repair → DocumentPreparation / cover → TextBlock 的相关源码与 hash。现有生产确实有上下文修复；baseline label 只用于对照与诊断，不进入 Resolver。

## 实验修复与 NativeHeadingRepair 审计

- `native_repair.py` 独立执行已有 production native candidate / 字体与邻接规则；封面恢复必须有实际 V3 title anchor。编号恢复另用 production native heading index、精确位置和局部编号层级，明确记为 `LAYOUT_RECALL_ERROR` 的 repair。
- `semantic.py` 只对已有 title family 作业务标题修正；公式和页边图像另作既有生产类型映射。正文列表和安全协议规则均保留决策原因。
- `artifact.py` 在 V3 排序前重组同一视觉行的 native 碎片，保留每个 fragment 和 raw region；修复 2.10 / 2.18 与独立编号 tab stop 被拆开的问题。
- `ocr.py` 保存远端真实 word polygon / crop origin / affine transform / text index，并向现有 adapter 提供 CharBox；未合成等分字框。

[历史 24 例审计](../../output/analysis/extraction-challenger-business-equivalence/native-repair/audit.json) 逐项含原文、页、字体／字号／flags、baseline type、最终类型、reason、单页 ClauseSplitter 反事实。13 个错误提升分别是 8 个签署字段、3 个主体信息字段、2 行连续正文；撤销后文本仍保留。其单页边界增量为 0，错误主要是语义角色与正文归属风险。6 个正确封面恢复中 2 个有单页 +1 边界作用；5 个 ambiguous 中 1 个有单页 +1，均没有被算成 B 归因。单页反事实不能替代全文验收。

[当前真实合同提升](../../output/analysis/extraction-challenger-business-equivalence/native-repair/current-real-promotions.json) 共 5 例：RETURN_CONFIRMATION 的 4 行封面及 SHENNENG_NAIMAN 的 `5. 其他`。后者复用了已有 native heading index 与 `5.1 / 5.2` 层级证据，原始 V3 text 不变；它仍计为 Layout recall repair，不伪装成 Resolver 正确分类。

## 四份完整真实合同

覆盖复杂封面、多级标题、附件、安全协议、原生表格、mixed 公式／图像和 scanned 表格。共 80 页：69 NATIVE_GOOD、3 MIXED、8 SCANNED。固定 V3 新增 66 页 forward，14 页复用冻结结果；没有其他 Layout 模型。12 个独立 OCR 输入（8 个 scanned page + 4 个 mixed crop），首次本轮调用 7 个、复用 5 个；后续规则回放全部命中缓存。NATIVE_GOOD OCR=0，table 结构阶段 OCR=0，PP-Structure request=0。这些是路由计数，不是性能测试。

| 合同 | 页数 | Baseline | 修复前 Challenger | 当前 Challenger | 当前新 / 缺边界 |
|---|---:|---:|---:|---:|---|
| RETURN_CONFIRMATION | 14 | 83 | 87 | 83 | 0 / 0 |
| LIANJIANG_PINGPO | 27 | 226 | 223 | 226 | 2 / 2 |
| SHENNENG_NAIMAN | 31 | 253 | 242 | 252 | 0 / 1 |
| NANJING_DINGYI_XUZHOU_BANGYU | 8 | 62 | 64 | 62 | 0 / 0 |

每份合同的 `before/` 与 `after/` 都保存全文 ledger 和 clauses。当前边界裁决按有符号事件计为：**1 Challenger regression、2 Baseline defect exposed、2 Ambiguous**；两个新／缺事件配对为一个 BOUNDARY_SHIFT，所以按业务问题计为 **1 回归、1 个 baseline cover defect、1 个签署区 ambiguous**，不可混用计数单位。

- LIANJIANG p1：封面标题不应成为正文 clause；保留 Challenger 从前言起始正文的行为。
- LIANJIANG p13：甲乙方签署信息双列，V3 列顺序与 baseline 的行合并不同。签署字段仍在，但 ClauseSplitter 的正文归属不同；完整 ledger 和原页核查图保留，等待签署区业务 review。
- SHENNENG p2：`2、合同总价：` 是明确的编号小节，但 V3 raw=text，现有 native title 候选拒绝冒号结尾，导致边界缺失。这是尚未修复的 **LAYOUT_RECALL_ERROR**。不能在 Resolver 把该 native 行升为 title，也没有为这句话放宽生产规则。需另做有独立样本支持的 NativeHeadingRepair 修复。

[review queue](../../output/analysis/extraction-challenger-business-equivalence/review-queue.json) 另有 **320 项已对齐 clause** 的 TEXT／HEADING／BODY／PAGE／SECTION 变化（47 + 131 + 136 + 6），当前统一保留 Ambiguous，等待逐项原文复核。部分只是 OCR/native 文本形式差异，但没有未经检查就批量判为正确。这不是 320 个边界差异，也不计入 B 的 25。对应 source block ledger 另列 BODY_MEMBERSHIP_CHANGED；未映射 baseline 块保留 unmatched，不强行配对。

## Table contract

[19 个表格结果](../../output/analysis/extraction-challenger-business-equivalence/tables/summary.json) 保存 cell bbox / polygon、row / column / rowspan / colspan、reading order、native token 或已有 OCR token → cell assignment、HTML、坐标转换及失败原因。16 个区域用 PDF vector ruling；3 个 scanned 区域只调用 table structure / cell 模型，运行时禁止 OCR 和 Layout 模型。

17 个表格的 token 均恰好分配到一个 cell，生产 HTML parser 可消费；HTML 只附在一个 source block 上，原文本块及 provenance 保留。已查看原生 4×2 封面、带跨行跨列的验收单和 scanned 4×2 封面高亮图。

| 未通过区域 | HTML cells | Cell geometry | 处理 |
|---|---:|---:|---|
| NANJING p3 | 127 | 103 | 保存原始两种输出；不按序硬配，不发布 HTML |
| NANJING p4 | 23 | 19 | 同上 |

两项结构不一致仍阻塞 Table accepted。不会用整表 OCR 补救结构错误；当前 scanned 已做一次整页识别，复用其字词，额外 table OCR=0。原生表格没有 OCR 请求。

## Evidence precision

[证据回投与视觉记录](../../output/analysis/extraction-challenger-business-equivalence/evidence/review.json)：425 个 OCR 行、5147 个非空白字符映射，4293 个为单字框、854 个共享多字符词框，缺失映射=0。每个字符核对 canonical text index 与 crop → page affine；native 使用真实 glyph boxes。

已逐图查看 native / mixed / scanned 各 3 处，共 9 处真实页面；红框覆盖相应文字。另将真实 native 页面在内存中旋转到 0/90/180/270°，核对坐标往返并查看四张高亮图。该旋转是受控坐标测试，未声称来自自然旋转扫描件，也未重跑 OCR。

仍有验收边界：Agent 视觉核查不是独立人工签收；OCR word box 不等于单字 glyph box；未覆盖所有自然倾斜页。保留原 polygon，旧 adapter 只接 enclosing bbox。这些限制已写入 artifact，不以字符串正确替代定位检查。

## 验证与进入条件

`backend/.venv/bin/python -m pytest experiments/extraction_challenger/test_business.py experiments/extraction_challenger/test_challenger.py -q`：**24 passed**。验证了正文／签署字段不被提升、Resolver 不能补不存在的 title、native 分片编号重组、固定模型语义映射、正文列表、OCR crop/rotation 回投及失败 table 不发布。B 用最终代码独立回放仍为 139，production source lock 全部通过。

进入 Full Golden 的 gate 仍为 **false**。关闭条件：处理 SHENNENG p2 的明确召回回归；完成 2 个扫描表格的结构／几何一致性修复；对签署区和 320 项全文变化完成分级业务复核；完成 evidence 的独立人工验收。当前只生成可复核证据与队列，没有启动 Full Golden 或 A–E，也没有请求通过审批来跳过失败。
