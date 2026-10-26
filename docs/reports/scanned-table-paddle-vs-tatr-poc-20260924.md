# Scanned Table Structure POC：Paddle vs TATR

```text
POC_SCOPE = 3_GOLDEN_CONTRACTS
POC_RESULT = INSUFFICIENT
TABLE_STRUCTURE_POC = INSUFFICIENT
RECOMMENDED_CANDIDATE = NONE
READY_FOR_APP_INTEGRATION = false
READY_FOR_PRODUCTION = false
FULL_GOLDEN_NOT_RUN = true
REQUIRES_DUPLICATE_OCR = false
```

## 直接结论

1. 第三份选择 `NANJING_DINGYI_XUZHOU_BANGYU`（Golden source run ID `593d2c7b-ffae-4822-bd6a-4c369f5c1909`）：Original/Compare 各 8 页扫描页，双方都有真实表区，Original 还有 1 张原本已结构化的表，报价表含复杂合并格。它能检查已正常提取的表是否受影响，也能避免只适配前两份。
2. Paddle 实际调用 PaddleX `TableRecognitionV2` 的 `PP-LCNet_x1_0_table_cls`、`SLANeXt_wired` 和 `RT-DETR-L_wired_table_cell_det`。`SLANeXt_wireless`、无线 cell detector 已加载，但 16/16 被分类为 `wired_table`，两者**没有在本轮推理**。因此本样本无法比较 wired/wireless 路由效果。
3. TATR 实际调用 `microsoft/table-transformer-structure-recognition`，checkpoint `f4d4bdc85c3fe4b1fa49658882a5d38bbdd0f343`。只检测 row、column、column header、spanning cell、projected row header；没有运行页面 table detector。
4. 两路线使用完全相同的 16 张 144 DPI table crop 和 957 条已冻结 PP-OCRv6 行框。Paddle 通过 `overall_ocr_res` 且 `use_ocr_model=False` 输入外部 OCR；TATR 只产出几何，统一 [OCR→cell assignment](../../experiments/scanned_table_structure_poc/ocr_assignment.py) 填字。每张的 crop、文字、bbox、confidence 与 SHA256 在 [input-manifest.json](../../output/analysis/scanned-table-paddle-vs-tatr-20260924/input-manifest.json)。
5. 五张已知 false DELETE：**Paddle 只在 3 张简单表恢复了可检查的结构**（两张封面、一张场站表）；两张跨页报价表未恢复。**TATR 为 0/5**：三张简单表漏行且漏 OCR，复杂表也未通过关键金额和合并格核验。这里按整张表所有片段和人工图像核验计数；不能把单片 HTML 能解析当作整表成功。
6. 行列与 merged cell：Paddle 在简单表明显更准；复杂报价表两者都失败。TATR 的 cell 矩形在部分长表更规整，但经常低估行列数。原始模型输出的 spanning detection 没有解决金额合并格。
7. Evidence：957/957 OCR 框经 crop→page image→PDF 逆仿射回投，最大数值误差 `0.0 pt`。这证明坐标**转换链**正确；复杂表的 cell 框、逻辑格和文字归属不可靠，不能作为业务 Evidence。抽查红框见 [10 个页面样本](../../output/analysis/scanned-table-paddle-vs-tatr-20260924/overlays/contact-sheet.png)：5 普通、3 金额、2 merged。
8. “广核淮阴风电”落在 Paddle 场站表第 2 行第 2 列，邻格为“风电”；“预测服务器”落在 Paddle 报价首片的产品名称列。`64500`（两处）、`17100`、`12400`、`118000` 五处**无一**同时满足正确文字、行列、合并跨度和图像/PDF bbox。金额出现在 OCR 或某 cell 文本里不算恢复成功。[逐金额审查](../../output/analysis/scanned-table-paddle-vs-tatr-20260924/evaluation.json)保存具体格坐标及与人工框的 IoU。
9. 三合同的既有表格语义保护复跑结果中，五条已知整表 `DELETE` 均消失：两张封面变待复核日期 `ADD`，三张表变 `TABLE_STRUCTURE_UNRESOLVED` / `NEEDS_REVIEW` 的表格 `MODIFY`。这是**已存在的 Compare 语义保护**，与本轮两个模型无关；本轮没有把候选结构送入正式 Compare。
10. 日期差异仍在：`RETURN_CONFIRMATION` 的 D005 保留 `2026 年4 月`→`2026年4月2日`，D007 有待复核的 `2026年4月2日`；`KAILI_FOUR_PROJECTS` 的 D006 有待复核的 `2026年4月21日`。但当前封面调和结果把完整日期呈现为 `ADD`，未形成“原占位日期→新日期”的单条 `MODIFY`；不能宣称日期语义已完全修好。
11. 第三份既有完整 pipeline 的 baseline/语义保护后复跑均为 19 条 diff，非表格项 ID、类型和文字未变；原 D014 表格 `DELETE` 变待复核 `MODIFY`。本轮模型未接入 app，因此这只能证明语义保护没有新明显 regression，**不能**证明未来模型集成没有 regression。
12. 单表推理时间：Paddle CPU 中位数 `1.937 s`，TATR MPS 中位数 `0.192 s`；设备不同，不据此选型。每张时间见下表。OCR 归属中位数分别 `0.094 ms` / `0.320 ms`，artifact 构建中位数 `0.324 ms` / `0.116 ms`；未测 GPU VRAM。
13. MinerU 的方向归一化、有线/无线分类、先无线后有线/低置信回退、OCR 与 HTML 交接值得借鉴。其完整 table pipeline 会单独做 crop OCR，而本 POC 固定复用现有 PP-OCRv6，未安装或运行 MinerU。
14. **推荐 `NONE`**。Paddle 可作为后续研究起点，但两模型在关键金额与合并格 Evidence 上均未达标。按结构、文字格、坐标、扫描稳健性、速度、部署复杂度排序后，速度不能抵消前面三项失败。
15. **不建议进入正式 app 集成**。停止在这三份合同；未来若另立任务，可只评估 UniTable、RapidTable/SLANet_plus 或 SLANeXt 的其他配置。本轮不自动运行。

## Baseline 冻结和输入

在已有表格语义改动之前捕获的正式 `ContractExtractionV3` 结果保存在 [baseline](../../output/analysis/scanned-table-structure-poc-20260924/baseline)；同三合同语义保护后结果保存在 [after](../../output/analysis/scanned-table-structure-poc-20260924/after)。这次没有重跑其余 12 份 Golden，也没有修改 expectation、正式服务或生产默认配置。各侧 page image、crop、表框、OCR 文字/bbox/confidence、原始 table artifact 与 compare result 均可由两个目录及 [新冻结输入](../../output/analysis/scanned-table-paddle-vs-tatr-20260924)复核。

下表 `N/M/S` 为 Native/Mixed/Scanned 页。表区按 PP-DocLayoutV3 `layout_block_id` 去重；表内 OCR 数为 PP-OCRv6 行框数。Table Compare 四类计数在合同级列出。

| 合同 | 侧 | 页数 N/M/S | 表区 | 结构化 | 无结构表区 | 表内 OCR | baseline 表格 I/D/M/NR |
|---|---|---:|---:|---:|---:|---:|---:|
| RETURN_CONFIRMATION | Original | 14/0/0 | 6 | 6 | 0 | 0 | 0/3/0/3 |
| RETURN_CONFIRMATION | Compare | 0/0/15 | 7 | 0 | 7 | 485 | 同上 |
| KAILI_FOUR_PROJECTS | Original | 9/0/0 | 3 | 3 | 0 | 0 | 0/2/0/2 |
| KAILI_FOUR_PROJECTS | Compare | 0/0/9 | 3 | 0 | 3 | 172 | 同上 |
| NANJING_DINGYI_XUZHOU_BANGYU | Original | 0/0/8 | 3 | 1 | 2 | 150 | 0/1/0/1 |
| NANJING_DINGYI_XUZHOU_BANGYU | Compare | 0/0/8 | 3 | 0 | 3 | 150 | 同上 |

`I/D/M/NR` = INSERT/DELETE/MODIFY/NEEDS_REVIEW。五条冻结 ID 为 `RETURN_CONFIRMATION` D007/D008/D009 与 `KAILI_FOUR_PROJECTS` D006/D007；源任务分别为 `ff24e3cb-92b7-427d-8be8-842d295f7af0` 和 `abdb9c3b-6d44-4fa6-a61a-40d4b6cda578`。两次完整 Compare 的差异产物可逐条核对，不能把模型 candidate 成功归因于既有语义保护。

`ocr_result_sha256` 覆盖每条行框的 ID、text、bbox、confidence；旧 ledger 的 `ocr_text_bbox_sha256` 另保留为兼容字段。16/16 两候选的输入 hash 相同。原生 Original 页没有 PP-OCRv6 输入，本轮不让候选重复 OCR；第三份 Original 的扫描表区也进入候选，合计 16 个 crop。

## 统一产物、Validator 与逐表结果

[实验目录](../../experiments/scanned_table_structure_poc)有 `baseline.py`、`paddle_candidate.py`、`tatr_candidate.py`、`ocr_assignment.py`、`artifact.py`、`validator.py`、`evaluate.py`、`run_three_golden.py`。Paddle HTML 先解析逻辑 cell，TATR 从 row/column 交集与 spanning detection 构建 cell；两者都由同一 `assign_ocr_to_cells()` 唯一归属 OCR。TableArtifact 的 cell 同时保存 `TABLE_CROP_PIXELS` bbox、`PAGE_IMAGE_PIXELS` bbox、`PDF_PAGE_COORDS` bbox、文字、OCR word IDs、置信度。模型原 HTML 与统一重建 HTML 分开保存。Artifact 不进入正式比较器。

Validator 检查空网格、非法跨度、槽缺失或覆盖、缺失/越界/负尺寸 bbox、严重框重叠、HTML-cell 框数量、唯一 OCR 归属，并输出 assignment rate、OCR text coverage 和关键字入格记录。`STRUCTURE_PASS` 只是**几何/拓扑自动 sanity check**，`EVIDENCE_PASS` 还要求所有 OCR 行框可唯一归属；两者都不是生产阈值，也不能代替人工核对。TATR 有 13/16 通过自动结构 sanity check，但 0/16 通过 Evidence；它的规则网格会掩盖漏行，故五张表人工恢复数仍为 0。

16 个 crop 的自动 Gate：Paddle 结构/Evidence 均 `7/16`，OCR 唯一归属 `946/957 = 98.9%`；TATR 结构 `13/16`、Evidence `0/16`，OCR 唯一归属 `839/957 = 87.7%`。这些数字只描述输出，不能替代逐表正确性。

每张 artifact 都有 `structure_score`：Paddle 当前返回值未暴露可比较的结构分数，记 `null`；TATR 记录 cell 的 row/column 检测分数均值。它是模型内排序线索，**不是**结构正确率；高分漏行样本已出现。

五张已知表按**整表所有片段**汇总如下。`HTML/Evidence` 表示经人工核验可用于完整结构/证据，不是仅能解析标签。延迟为该表片段的推理秒数之和。

| 已知表 | Baseline | Paddle：恢复；OCR；HTML/Evidence；延迟 | TATR：恢复；OCR；HTML/Evidence；延迟 |
|---|---|---|---|
| ff24 D007 | DELETE，4×2 结构在 Compare 缺失 | 是；8/8；是/是；1.859 s | 否；6/8；否/否；0.123 s |
| ff24 D008 | DELETE，7×3 结构在 Compare 缺失 | 是；21/21；是/是；1.867 s | 否；15/21；否/否；0.190 s |
| ff24 D009 | DELETE，跨 p3–p6 | 否；439/448；否/否；8.163 s | 否；410/448；否/否；0.712 s |
| abdb D006 | DELETE，4×2 结构在 Compare 缺失 | 是；8/8；是/是；2.594 s | 否；4/8；否/否；0.734 s |
| abdb D007 | DELETE，跨 p3–p4 | 否；164/164；否/否；4.036 s | 否；139/164；否/否；0.396 s |

| baseline 误判 | 片段 | Paddle：行×列/cell/merged，OCR | TATR：行×列/cell/merged，OCR | 人工判定 |
|---|---|---|---|---|
| ff24 D007 DELETE | p1 | 4×2/8/0，8/8 | 3×2/6/0，6/8 | Paddle 封面格正确；TATR 漏一行。 |
| ff24 D008 DELETE | p3 场站 | 7×3/21/0，21/21 | 5×3/15/0，15/21 | Paddle 的“广核淮阴风电”格正确；TATR 漏两行。 |
| ff24 D009 DELETE | p3 报价首片 | 5×9/37/1，56/56 | 4×9/36/0，46/56 | Paddle“预测服务器”位置可用；续片未通过。 |
| ff24 D009 | p4 | 16×9/117/5，143/143 | 16×8/118/3，141/143 | `64500` 两者跨度/bbox 错；Paddle 还混入 `500`。 |
| ff24 D009 | p5 | 15×9/98/6，105/106 | 14×9/109/6，103/106 | 两者均有拓扑/归属问题。 |
| ff24 D009 | p6 | 18×9/124/7，135/143 | 17×9/143/4，120/143 | 第二处 `64500` 仍不在正确 merged 金额格。 |
| abdb D006 DELETE | p1 | 4×2/8/0，8/8 | 2×2/4/0，4/8 | Paddle 封面格正确；TATR 漏两行。 |
| abdb D007 DELETE | p3 报价首片 | 15×10/106/9，116/116 | 12×9/98/2，99/116 | `17100` 两者逻辑行/跨度错。 |
| abdb D007 | p4 | 9×9/43/8，48/48 | 7×9/52/5，40/48 | `12400`、`118000` 未可靠入正确格。 |

金额检查的人工期望坐标与两个候选的 `row/col/rowspan/colspan/bbox/text` 均在 [evaluation.json](../../output/analysis/scanned-table-paddle-vs-tatr-20260924/evaluation.json)。例如 `17100` 的 Paddle bbox 与人工框 IoU `0.861`，但模型给 `r7 c8 1×1`，实际为 `r8 c6 1×2`；仅凭 bbox 接近不能判成功。`118000` 的 TATR cell 框落在上一行 `29500` 附近。人工红框抽查显示 5 普通格中 Paddle 的 4 个可解释，TATR 封面样本实为下一行；3 金额、2 merged 均暴露错误。其余候选 artifact、完整 HTML、逐词映射及每张表诊断在 [artifacts](../../output/analysis/scanned-table-paddle-vs-tatr-20260924/artifacts)。

## 每张表的局部耗时

推理秒数不含模型初始化、完整 extraction/Compare、页面渲染。`assign` 和 `build` 为 Paddle/TATR 各自毫秒数；两者使用同一代码实现。Paddle 在 CPU，TATR 在 MPS；CUDA runtime 不可用。实验环境：PaddleOCR 3.2.0、PaddleX 3.2.0、PaddlePaddle 3.2.2、Torch 2.12.1、Transformers 4.57.6。TATR 需要的 `timm 1.0.20` 源码仅置于 POC 输出目录，没有进入正式 requirements。

| 合同/侧/页/region | Paddle s | TATR s | assign ms P/T | build ms P/T |
|---|---:|---:|---:|---:|
| KAILI C p1 `e03a2a194fd2-p1:v3:2` | 2.594 | 0.734 | 0.018/0.053 | 0.170/0.094 |
| KAILI C p3 `e03a2a194fd2-p3:v3:3` | 2.091 | 0.202 | 0.657/1.881 | 1.178/0.341 |
| KAILI C p4 `e03a2a194fd2-p4:v3:0` | 1.945 | 0.194 | 0.145/0.562 | 0.418/0.183 |
| NANJING C p1 `3d706111bdf7-p1:v3:2` | 1.849 | 0.121 | 0.018/0.068 | 0.129/0.061 |
| NANJING C p3 `3d706111bdf7-p3:v3:3` | 2.021 | 0.223 | 0.835/1.603 | 1.353/0.334 |
| NANJING C p4 `3d706111bdf7-p4:v3:0` | 1.929 | 0.144 | 0.030/0.045 | 0.229/0.061 |
| NANJING O p1 `7d76c9387a1c-p1:v3:3` | 1.827 | 0.176 | 0.017/0.060 | 0.114/0.042 |
| NANJING O p3 `7d76c9387a1c-p3:v3:3` | 2.035 | 0.229 | 0.938/1.642 | 1.032/0.334 |
| NANJING O p4 `7d76c9387a1c-p4:v3:0` | 1.888 | 0.147 | 0.033/0.057 | 0.219/0.054 |
| RETURN C p1 `d8301f12f62e-p1:v3:4` | 1.859 | 0.123 | 0.013/0.031 | 0.117/0.067 |
| RETURN C p3 `d8301f12f62e-p3:v3:4` | 1.867 | 0.190 | 0.043/0.108 | 0.215/0.073 |
| RETURN C p3 `d8301f12f62e-p3:v3:5` | 1.883 | 0.196 | 1.744/0.531 | 0.690/0.139 |
| RETURN C p4 `d8301f12f62e-p4:v3:1` | 2.057 | 0.205 | 0.932/1.523 | 0.984/0.440 |
| RETURN C p5 `d8301f12f62e-p5:v3:0` | 2.020 | 0.176 | 0.680/0.928 | 1.122/0.386 |
| RETURN C p6 `d8301f12f62e-p6:v3:0` | 2.203 | 0.135 | 2.175/5.082 | 1.142/0.655 |
| RETURN C p7 `d8301f12f62e-p7:v3:0` | 1.851 | 0.438 | 0.017/0.036 | 0.153/0.044 |

## MinerU 架构研究与本项目对照

按 MinerU 当前 [table pipeline 源码](https://github.com/opendatalab/MinerU/blob/master/mineru/backend/pipeline/batch_analyze.py) 和 [wired recognizer](https://github.com/opendatalab/MinerU/blob/master/mineru/model/table/rec/unet_table/main.py)核对：① table orientation classifier 先预测方向，对 90°/270° crop 旋转，失败则用原图；② `TableCls` 判断 wired/wireless，分类失败保留默认路径；③ wired 使用 `TSRUnet` 线结构与 `TableRecover`；④ wireless 使用 [RapidTable/SLANet_plus](https://github.com/opendatalab/MinerU/blob/master/mineru/model/table/rec/slanet_plus/main.py)；⑤ table crop 单独 OCR det/rec，wired 接口也允许外部 `ocr_result`，但默认空格补字分支可能再 OCR；⑥ wireless 先填 HTML，wired 可接收该 HTML 作参考并回填结果；⑦ wireless 分类低置信或 wired 分类会再走 wired，识别失败保留后备文本/原结果。这里归纳的是源码路径，不代表本 POC 跑过 MinerU。

本项目继续由 PP-DocLayoutV3 找表区，PP-OCRv6 提供冻结文字与框；Paddle/TATR 只做 table crop 结构，统一 TableArtifact 与 Evidence 坐标链。可吸收 MinerU 的 orientation、classification、model routing、OCR handoff、fallback 思路，但不能直接引入其重复 OCR 或整套 pipeline。参考固定版本的 [方向/路由/回退实现](https://github.com/opendatalab/MinerU/blob/94828a88d80ab8a72c21956de0604bf14f4cc9fc/mineru/backend/analysis/pdf/tables.py)。

## Compare 语义保护、验证和停止点

当前工作树在本轮开始前已含表格专属语义保护：`STRUCTURED_TABLE` 遇到对齐页有字的 `UNSTRUCTURED_TABLE_PRESENT`，不直接发布 `DELETE`，而标成 `CANDIDATE`、`NEEDS_REVIEW`、`TABLE_STRUCTURE_UNRESOLVED`，并带两侧证据。这次候选对比**未修改** `table_compare/parser.py` 或 `table_compare/facade.py`，也未改 Clause/Matching/Diff 非表格逻辑或按任务 ID 特判。`TABLE_ABSENT` 与 `UNKNOWN` 仍需在正式集成设计中明确为状态；本轮不宣称完整状态机已落地。五条 false DELETE 消失来自既有保护，模型候选尚未改善 Compare 输出。

本次执行 `run_three_golden.py` 只处理上述三份合同的 16 个扫描表区。独立 `evaluate.py` 检查相同 hash 和 957 个 OCR 坐标回投；实验模块 `py_compile` 通过；表格 Compare 定向单测 `141 passed`。完整 15 Golden、A–E benchmark、生产部署均未执行。失败类型为 `merged cell failure`、`wired table failure`、`OCR assignment failure`、`cell bbox failure`；本样本没有足够的 wireless/rotation 数据，不能给它们下结论。报告生成到此停止。
