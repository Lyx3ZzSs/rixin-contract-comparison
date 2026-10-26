| Release Gate | 状态 | 验收依据 |
|---|---|---|
| Gate 1 Heading Recall | **PASS** | SHENNENG p2 已恢复；独立反例 21/21 拒绝；旧有 13 项过度提升未回归 |
| Gate 2 Table Contract | **PASS** | p4 一致 graph；p3 已进入明确安全 fallback，禁止发布 HTML / 结构比较 |
| Gate 3 Full-text Ownership | **PASS** | 冻结 320 项逐项归类；5 项原始 regression 已 CLOSED；1 项 ambiguous 有明确复核处置 |
| Gate 4 Evidence Human QA | **FAIL / PENDING_RECHECK** | 人工确认 E001、E003、E012、E015、E016、E017 原文与高亮不对应；等待修复后独立复核 |

`READY_FOR_FULL_GOLDEN = false`

Evidence 人工验收已失败，正在追踪并修复实验 evidence 链路；先前材料不能作为放行依据。**未启动 Full Golden、A–E E2E benchmark。** Gate 2 的 PASS 包含用户允许的安全 fallback，并不表示 p3 表格结构恢复成功。Gate 3 的 PASS 表示已知 regression 清零且 ambiguous 有处置，不代表业务方已经接受签署区归属。

## 范围与冻结输入

固定 PP-DocLayoutV3、实验 title threshold=0.4、Native + SemanticRoleResolver + Selective PP-OCRv6。只离线复放既有四份真实合同的 80 页：69 NATIVE_GOOD、3 MIXED、8 SCANNED；新增 OCR 请求 **0**，NATIVE_GOOD OCR block **0**。仅为定位两个失败扫描表格复放 structure/cell 模型，运行时禁止 Layout 和 OCR 模型。

B 的 25 个已裁决 boundary 没有重新分析，没有重建 114 条结果。B JSON、原始四份 ledger 和 320 项 review universe 已做 SHA256 冻结；202 个生产源码文件与阶段入口 lock 一致。改动留在实验目录及报告、证据目录。ClauseSplitter、PageAlignment、Matching、生产 extractor 均未改动。

入口证据：[input-lock.json](../../output/analysis/extraction-challenger-release-gate/input-lock.json)、[gate-status.json](../../output/analysis/extraction-challenger-release-gate/gate-status.json)、[离线复放](../../output/analysis/extraction-challenger-release-gate/replay-summary.json)。

## Gate 1：带冒号的编号标题

在 15 份现有真实合同中进行 native-only 检索，采集 368 条候选；这不是 Full Golden。人工标注小样本为 9 正例、21 反例，其中 SHENNENG 目标行单独留出，独立样本包含其他合同的付款标题、责任标题及多级编号标题，反例包含条件句、正文列表、联系人、主体、金额和签署字段。

规则复用现有 ClauseSplitter numbering parse、native line/style 记录和 NativeHeadingRepair 的 title 验证。仅对 raw text / 未分配且 native 文本可靠的编号冒号行考虑修复，需要：短标题、独立物理行、正文不比标题更大、相邻正文缩进/间距合理，以及十进制子级或前一同级编号加连续正文。条件句内部标点、括号字段、同基线续文、主体字段式后续内容、冒号引出的括号列表均不能仅因冒号被提升。

| 样本 | 接受 | 拒绝 |
|---|---:|---:|
| 独立正例 | 7 | 1 |
| 独立反例 | 0 | 21 |
| SHENNENG 目标留出 | 1 | 0 |
| 已撤销的 13 个过度提升 | 0 | 13 |

独立正例 H0189 有同基线旁注，规则保守拒绝；未为提高样本召回放宽该约束。保留规则的依据是两个独立合同中的 7 个正例及 21 个反例，不是目标合同名、页码或具体标题文字。

SHENNENG p2 `2、合同总价：` 现在为 `paragraph_title`，raw V3 `text`、score、polygon、bbox、order 和官方输出保持原样。修复来源独立记录为 `NativeHeadingRepair.numbered_colon_native_context`、`LAYOUT_RECALL_ERROR`。整份合同 challenger 从 252 到 253；baseline 253，当前 new/missing boundary 均 0。SemanticRoleResolver 未为此增加 native 标题提升规则。

证据：[原始检索](../../output/analysis/extraction-challenger-release-gate/heading/candidate-inventory.json)、[30 项标注](../../output/analysis/extraction-challenger-release-gate/heading/annotated-samples.json)、[小样本与 13 项回归结果](../../output/analysis/extraction-challenger-release-gate/heading/evaluation.json)、[目标页 ledger](../../output/analysis/extraction-challenger-release-gate/ledgers/SHENNENG_NAIMAN/clause-boundary-diff-ledger.json)。五张样本 contact sheet 保存在同一 heading 目录。

## Gate 2：两个扫描表格

此前的 HTML cell 计数混入了生产 HTMLParser 在 `_grid_to_rows` 补出的空槽。此次直接保留原始 `<td>`、span、结构 decoder bbox、detector bbox、NMS 与 reprocessing 输出，并从实际 token 而非补齐后的 grid 计数。

| 阶段 | NANJING p3 | NANJING p4 |
|---|---:|---:|
| 原报告的 HTML cells | 127 | 23 |
| 真正结构 `<td>` / 同 head bbox | 112 / 112 | 19 / 19 |
| HTML parser 补出的虚拟空槽 | 15 | 4 |
| cell detector → NMS → 最终 geometry | 103 → 103 → 103 | 19 → 19 → 19 |
| 同一 graph 的逻辑 cells | 103 个候选，校验失败 | 19 |
| 已有 OCR token 唯一分配 | 511 / 516 | 129 / 129 |
| 最终发布 HTML | **无** | 从 19-cell graph 生成 |

两份原始输出复现一致。无 polygon filtering 丢失、NMS 删除或后处理删除 geometry。结构 decoder 在没有文字输入时产生空 `<td>` 是结构输出本身，不能据此认定 empty cell 不存在。p3 的结构输出 112 与 cell detector 103 不一致，空 OCR 输入下 reprocessing 没有补出 geometry；p4 计数虽然相等，但结构模型给出各行 8/2/4/4/1 cells，geometry 与实际表格线给出 9/3/3/3/1，不能按序 zip。

新增显式 `TableCellGraph.v1`：每个 cell 保存 id、row、column、rowspan、colspan、polygon/bbox、原 geometry index、文字 token refs 和 reading order。通过 cell edges 建立占位 grid，要求无重叠、无无法解释的空洞、每个 token 唯一归属，并用 crop 原图的实际表格线验证四边。HTML 只由通过校验的 graph 生成；独立 structure tokens 仅作为诊断保留，不按序强配。merged cells、empty cells、HTML tokens 均单独保存。

p4 所有校验通过，并能从 HTML round-trip 回到同一拓扑。p3 的小计行存在互相矛盾的 detector 候选：cell 102 跨 9 列，同时 cell 92 占第 6–7 列、cell 96 占第 8 列，造成 3 个重叠 slot 和 5 个 token 的双重归属。恢复需要裁决并改变模型给出的 cell 外框；当前输出不能直接构成唯一一致 graph，因此采用允许的安全 fallback：

```text
status = TABLE_STRUCTURE_UNRESOLVED
html = null
publish_html = false
structured_comparison_allowed = false
mode = canonical_text_and_page_evidence_only
review_required = true
```

保留原页、crop、结构 token、所有 cell 候选及已有 OCR 文字/provenance，交由人工结构 review；不再识别一次表格，也不发布猜测的 HTML。这个结论是当前输出不能无歧义自动发布，**不是宣称原 PDF 永远不可恢复**。

证据：[两个 case 汇总](../../output/analysis/extraction-challenger-release-gate/tables/summary.json)、[p3 全阶段 trace](../../output/analysis/extraction-challenger-release-gate/tables/NANJING_DINGYI_XUZHOU_BANGYU-p3-v3-3/trace.json)、[p3 graph 与错误明细](../../output/analysis/extraction-challenger-release-gate/tables/NANJING_DINGYI_XUZHOU_BANGYU-p3-v3-3/table-cell-graph.json)、[p4 graph](../../output/analysis/extraction-challenger-release-gate/tables/NANJING_DINGYI_XUZHOU_BANGYU-p4-v3-0/table-cell-graph.json)、[p4 HTML](../../output/analysis/extraction-challenger-release-gate/tables/NANJING_DINGYI_XUZHOU_BANGYU-p4-v3-0/graph.html)。每个目录另有 raw decoder arrays、vocabulary 与 table crop。

## Gate 3：320 项精确收账

冻结原始 C001–C320，不因为重放后只剩 317 条原始字段差异就删去历史记录。每项保存原 diff、冻结及当前 clause id、source block ids、anchor、root-cause fingerprint、equivalence class、兼容差异、原始业务判断、当前处置和复核证据。

| 修复后互斥处置 | 数量 |
|---|---:|
| NON_SEMANTIC_DIFFERENCE | 301 |
| 已接受变化的传播 | 13 |
| BASELINE_DEFECT_EXPOSED | 5 |
| 未关闭 CHALLENGER_REGRESSION | 0 |
| AMBIGUOUS | 1 |
| **总计** | **320** |

301 项包括 5 项已修复回归的当前输出；它们的原始 `business_correctness=CHALLENGER_REGRESSION` 和 `regression_resolution=CLOSED` 仍保留。按原始业务判断计数则为：308 non-semantic、6 baseline defect、5 challenger regression、1 ambiguous。两个统计口径分别描述原始问题与发布状态，不混用。

13 项传播中，C061 链接到此前已接受的 LIANJIANG 封面/正文边界；另外 12 项链接到 C185 的编号后空格导致的标题摘录变化。后者是 section path 展示字符串传播，不伪称新增 Chapter boundary。B 的 25 项不属于这个 320 项集合，也没有被重新裁决。

自动通过要求现有 `TextNormalizer.normalize_for_diff` 下全文、heading、page membership、section path 相同；标题截断另需证明是同一全文和编号的前缀。金额、百分比、千分号、文件名下划线、数字范围等真实差异不靠全局删标点抹平。13 项正文非等价候选已逐项查看原 PDF crop。

| 原始回归 | 根因与修复 | 当前状态 |
|---|---|---|
| C169 | 独立 `/` 字段被固定 splitter 的噪声规则忽略；native merge 按同物理行合并 label 与纯符号字段，保留全部 native indices | CLOSED |
| C223 | Gate 1 漏边界导致合同总价正文进入上一条；恢复原边界 | CLOSED |
| C315 | 页脚水印进入正文；复用 `HeaderFooterComparator.owned_footer_blocks` 的全文重复与位置证据 | CLOSED |
| C316 | 横向签署字段跨过印章而被整个标记为 seal；根据越界几何与同基线对应字段纠正文字归属，保留 raw seal | CLOSED |
| C317 | OCR 将 3‰ 识别为 3%；复用生产 `PPOCRV5Extractor` 原文/raster glyph 验证，不调用 OCR；保留 raw OCR 和字符索引 | CLOSED |

C169 修复后 `/` 跟随原来的标签同一物理行，标题摘录因此包含空字段符号；这不是照 baseline 重新拆行。全文归一化、编号和页归属一致，PDF 显示的是未填写字段，已记录该展示差异的独立裁决。C079/C080 的下划线是原 PDF 绘图，C299 的下划线是文件名字符，二者分开判定。

5 项直接 baseline defect 为 C073（仟误为任）、C093（‰误为%）、C225（范围横线误为一）、C294（签署字段的相对次序）、C299（文件名下划线丢失）。都有对应 PDF crop；这是 Agent 对原始材料的视觉检查，不冒充独立人工签收。

唯一剩余 ambiguous：**C104，LIANJIANG p13 签署区归属**。两方名称和日期信息都保留，但两列签署字段与“以下无正文”之间的 clause ownership 需要业务方决定。处置为保留原始 block 和两侧证据，签署区域 ownership 暂不自动验收，由业务负责人决定其独立签署段或附着方式；不修改 ClauseSplitter、不整体修改 reading order。当前无 unknown 项；三位编号既有问题仍留在原独立 issue，不作为本次调 splitter 的理由。

等价类的精确分组如下；每组成员列表可在机器 ledger 查到。fingerprint 向量首项为主要机制，其余为该差异的归一化/归属特征；另有 primary_root_cause_fingerprint 明确主因：

| Class | 数量 | Fingerprint | 当前处置 |
|---|---:|---|---|
| EC001 | 1 | reading_order + native_vs_ocr_text + line_merge_split | ambiguous |
| EC002 | 3 | native_vs_ocr_text | baseline_defect_exposed |
| EC003 | 1 | native_vs_ocr_text + heading_role | baseline_defect_exposed |
| EC004 | 1 | reading_order + native_vs_ocr_text + line_merge_split | baseline_defect_exposed |
| EC005 | 1 | clause_boundary_propagation + native_vs_ocr_text + line_merge_split | non_semantic |
| EC006 | 1 | heading_role + whitespace_only + section_path_propagation | non_semantic |
| EC007 | 1 | line_merge_split + native_vs_ocr_text | non_semantic |
| EC008 | 1 | line_merge_split + native_vs_ocr_text + heading_role + section_path_propagation | non_semantic |
| EC009 | 1 | native_vs_ocr_text + heading_role | non_semantic |
| EC010 | 1 | ocr_difference + heading_role | non_semantic |
| EC011 | 1 | page_assignment + ocr_difference + line_merge_split | non_semantic |
| EC012 | 4 | punctuation_only | non_semantic |
| EC013 | 1 | unmatched_source_block + ocr_difference + line_merge_split | non_semantic |
| EC014 | 201 | whitespace_only | non_semantic |
| EC015 | 84 | whitespace_only + heading_role | non_semantic |
| EC016 | 4 | whitespace_only + line_merge_split | non_semantic |
| EC017 | 1 | clause_boundary_propagation + section_path_propagation | propagation_from_accepted_change |
| EC018 | 3 | section_path_propagation + whitespace_only | propagation_from_accepted_change |
| EC019 | 9 | section_path_propagation + whitespace_only + heading_role | propagation_from_accepted_change |

证据：[完整 320 ledger](../../output/analysis/extraction-challenger-release-gate/content-ledger.json)、[可筛选 CSV](../../output/analysis/extraction-challenger-release-gate/content-review.csv)、[唯一待复核项](../../output/analysis/extraction-challenger-release-gate/content-review-queue.json)、[原始 320 冻结记录](../../output/analysis/extraction-challenger-release-gate/frozen-320.json)。四份全文 block→clause ledger 在 `ledgers/`；PDF 视觉材料在 `content-review/`。

## Gate 4：独立 Evidence 验收材料

[打开 evidence-review-pack](../../output/analysis/extraction-challenger-release-gate/evidence-review-pack/index.html)；[evidence-review.csv](../../output/analysis/extraction-challenger-release-gate/evidence-review-pack/evidence-review.csv)。

原始生成时 17 项为 PENDING；现已收到 6 项人工 FAIL。最新 [根因报告](evidence-coordinate-root-cause.md) 和 [before / after 待复核包](../../output/analysis/extraction-challenger-release-gate/evidence-coordinate-recheck/index.html) 取代下方原始状态描述。6 项保持 PENDING_REVIEW，其余 11 项保持 PENDING；Gate 4 保持 FAIL / PENDING_RECHECK。

原始覆盖：

- E001–E004：Native 正文、标题、金额、表格文字。
- E005–E007：Mixed native 区域、OCR region、native/OCR 邻接边界。
- E008–E011：Scanned 正文、表格、低置信 OCR、印章交叠字段。
- E012–E014：已纠正的千分号、Mixed 低置信公式、p3 未解决表格中的文字证据。
- E015–E017：真实 Native 页受控 90/180/270 度旋转，用于坐标验收；不计作额外真实旋转合同。

每项包含原页图、highlight overlay、detail、完整 canonical text、选中字串、native/ocr 来源、page、bbox、逐字符/词 polygon、confidence、坐标转换和 provenance。Native recognition confidence 为 null，不拿 Layout score 充当识别置信度。

自动复核了 5,147 个 OCR 字符索引与坐标 provenance，但这不能证明高亮足够准确。**E012 的原 OCR 字符框较窄，E010/E013 为真实低置信输出，保留原样供人工判定，不因字符串已修复就签 PASS。** OCR 多字符 word 共用 word polygon 的精度限制也在包内说明。实际审阅人应检查原文字形覆盖、金额符号、邻近字段、印章和旋转，再填写 CSV 状态/评论并提供审阅身份、时间和 CSV hash；任一 FAIL/PENDING 均不能放行。

本阶段没有任何 Codex 代填的人工 PASS。

## 验证与复现

35 个针对性测试通过，覆盖真实 heading 修复、21 个反例、13 个历史过度提升、TableCellGraph 拓扑/拒绝发布、真实 OCR 字符修复/页脚/印章归属、native 字符守恒、320 项总账和人工状态保持 PENDING。202 个生产文件 hash、V3 raw regions、已裁决 B 及冻结输入均通过守恒检查。

从仓库根目录执行（只复用既有数据）：

```bash
archive/experiments/2026-09-24/layout_decoupling/.venv/bin/python experiments/extraction_challenger/release_heading_eval.py
archive/experiments/2026-09-24/layout_decoupling/.venv/bin/python experiments/extraction_challenger/release_tables.py
archive/experiments/2026-09-24/layout_decoupling/.venv/bin/python experiments/extraction_challenger/release_replay.py
archive/experiments/2026-09-24/layout_decoupling/.venv/bin/python experiments/extraction_challenger/release_content.py
archive/experiments/2026-09-24/layout_decoupling/.venv/bin/python experiments/extraction_challenger/release_evidence_pack.py
archive/experiments/2026-09-24/layout_decoupling/.venv/bin/python experiments/extraction_challenger/release_verify.py
archive/experiments/2026-09-24/layout_decoupling/.venv/bin/python -m pytest experiments/extraction_challenger/test_challenger.py experiments/extraction_challenger/test_business.py experiments/extraction_challenger/test_release_gate.py -q
```

`release_evidence_pack.py` 拒绝覆盖已有实际人工评论或非 PENDING 状态；`release_verify.py` 是本阶段待签收材料的验证器，不会把任意 CSV 修改直接当作独立签收，也不会启动 Golden。首次 structure-only trace 的脚本为 `release_table_trace.py`，已有 trace 不再推理。

下一步必须对修复后的全部 17 项独立人工复核；当前 Evidence Human QA = FAIL / PENDING_RECHECK，禁止 Full Golden 和 A–E benchmark。收到有效人工 PASS 后再明确检查进入条件；Full Golden 通过业务门槛后才允许 A–E benchmark。production integration、feature flag、shadow mode、rollback 继续推迟到 Full Golden 之后。
