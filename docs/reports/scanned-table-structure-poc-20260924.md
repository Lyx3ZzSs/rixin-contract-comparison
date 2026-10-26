# Scanned Table Structure Recognition POC

```text
POC_SCOPE = 3_GOLDEN_CONTRACTS
THIS_IS_NOT_FULL_GOLDEN
```

## 十项直接结论

1. **三份合同**：`ff24e3cb-92b7-427d-8be8-842d295f7af0` = `RETURN_CONFIRMATION`；`abdb9c3b-6d44-4fa6-a61a-40d4b6cda578` = `KAILI_FOUR_PROJECTS`；第三份 `NANJING_DINGYI_XUZHOU_BANGYU`，Golden source run ID `593d2c7b-ffae-4822-bd6a-4c369f5c1909`。第三份 Original/Compare 均为 8 页扫描 PDF，双方有真实 table region，Original 已有 1 张结构化表，报价表含合并单元格，适合检查复杂表泛化。
2. **五张已知误判表**：正式 baseline 为 `ff24...` 的 D007/D008/D009 和 `abdb...` 的 D006/D007，均显示 `DELETE`。表格专属语义保护后，这五条均不再是 `DELETE`。其中两张封面表被既有签署字段调和转成待复核日期 `ADD`；三张报价/场站表变成 `TABLE_STRUCTURE_UNRESOLVED` 候选，界面标签为“待复核”。这属于 Compare 语义保护，**不是结构识别成功**。
3. **RapidTable/SLANet_plus**：16 个扫描 table crop 中 3 个通过当前结构与坐标检查。两张简单表正确；复杂报价表出现缺格、越界、重叠，PP-OCRv6 行框归属 749/957。HTML 含关键金额，但金额未可靠落入对应 cell。
4. **Paddle TableRecognitionV2**：同 16 个 crop 中 7 个通过；行框归属 944/957。启用了 `PP-LCNet_x1_0_table_cls`、`SLANeXt_wired`、`SLANeXt_wireless` 与两套 cell detector，但分类器将本轮 16 个区域全部路由为 `wired_table`，因此无线模型只完成加载，**没有本轮推理样本**。复杂表仍有 HTML cell 数与 bbox 数不一致、缺格和重叠。
5. **MinerU 当前方案**：表格先做方向归一化；Basic/medium 对 table crop 单独 OCR，分类有线/无线；无线识别先运行，有线或低置信无线再调用有线识别并可接收无线 HTML；成功时回填 HTML，失败保留回退。其路由可借鉴，但其 table crop OCR 会重复执行，不能直接套进本项目的 OCR 复用约束。[MinerU 4.0.5 table pipeline](https://github.com/opendatalab/MinerU/blob/94828a88d80ab8a72c21956de0604bf14f4cc9fc/mineru/backend/analysis/pdf/tables.py#L530-L904)
6. **POC 推荐**：下一轮若继续优化，优先以 Paddle TableRecognitionV2 为 candidate 基础。依据是本三份合同的结构检查和坐标/文字归属，而非速度。当前两个 candidate 都不足以进入正式 app。
7. **重复 OCR**：本 POC 无需重复 PP-OCRv6。RapidTable 复用外部 OCR result，并对内部 OCR 工厂设置失败陷阱；Paddle 以 `use_ocr_model=False` 和 `overall_ocr_res` 输入，GeneralOCR 未初始化，cell OCR 分支关闭。这里的“无重复”指本轮结构推理，不是 MinerU 原实现。
8. **cell/evidence 坐标**：统一产物保存 crop bbox、render crop、逆仿射、cell crop bbox、page/PDF bbox、OCR token 证据，坐标链在简单表可用，例如“广核淮阴风电”的 Paddle cell PDF bbox 为 `[159.64,200.93,426.99,216.96]`。复杂表存在 cell 数不齐、越界或重叠，**不能整体满足 Evidence 需求**。
9. **第三份 regression**：完整 pipeline 前后均为 19 条 diff，ID 顺序相同，非表格 diff 的类型与文字未变；原 D014 `DELETE` 转待复核表格 `MODIFY`。结构 POC 对该合同 6 个扫描区域也跑完，Paddle 2/6 通过，Rapid 0/6；未见新的明显业务 regression，但复杂表未过结构 Gate。
10. **正式 app 集成**：暂不建议。先修复复杂合并单元格拓扑、HTML-cell bbox 对齐和金额 cell 归属，再以独立样本验证；本轮不改生产默认模型，不部署。

## Baseline 冻结与业务复跑

正式代码在任何表格 Compare 改动前对三份合同完整运行并保存于 [baseline 目录](../../output/analysis/scanned-table-structure-poc-20260924/baseline)；语义保护后的同三份完整运行保存于 [after 目录](../../output/analysis/scanned-table-structure-poc-20260924/after)。两阶段均未改 Golden expectation。下表的“表区”按 PP-DocLayoutV3 `layout_block_id` 去重；“OCR 块”是该区内 PP-OCRv6 文本行 block 数，均非重新 OCR 的次数。`N/M/S` 分别为 Native/Mixed/Scanned 页。

| 合同 | 侧 | 页数 N/M/S | 表区 | 结构化 | 无结构表区 | 表内 OCR 块 | baseline 表 DELETE/MODIFY/INSERT |
|---|---|---:|---:|---:|---:|---:|---:|
| RETURN_CONFIRMATION | Original | 14 / 0 / 0 | 6 | 6 | 0 | 0 | 3 / 0 / 0（合同级） |
| RETURN_CONFIRMATION | Compare | 0 / 0 / 15 | 7 | 0 | 7 | 485 | 同上 |
| KAILI_FOUR_PROJECTS | Original | 9 / 0 / 0 | 3 | 3 | 0 | 0 | 2 / 0 / 0（合同级） |
| KAILI_FOUR_PROJECTS | Compare | 0 / 0 / 9 | 3 | 0 | 3 | 172 | 同上 |
| NANJING_DINGYI_XUZHOU_BANGYU | Original | 0 / 0 / 8 | 3 | 1 | 2 | 150 | 1 / 0 / 0（合同级） |
| NANJING_DINGYI_XUZHOU_BANGYU | Compare | 0 / 0 / 8 | 3 | 0 | 3 | 150 | 同上 |

最终复跑三份合同的 diff 总数仍为 27、19、19；表格 DELETE 为 0、0、0。五张已知表的 baseline 和最终产物逐项保存在 [known-table-results.json](../../output/analysis/scanned-table-structure-poc-20260924/known-table-results.json)，其中包含两模型各片段的**完整 HTML、cell、bbox、OCR 归属和耗时**。

真实日期变化仍可见：`RETURN_CONFIRMATION` 的 D005 保留“2日”字段变化，D007 输出待复核签署日期 `2026年4月2日`；`KAILI_FOUR_PROJECTS` 的 D006 输出待复核 `2026年4月21日`。这两条日期由现有签署字段逻辑生成，本轮没有修改该逻辑；其 `ADD` 表示当前算法的呈现方式，不能当作已验证的完整日期差分。

## 历史 Challenger 复用结论

已搜索 `SLANet`、`SLANet_plus`、`table structure`、`table_recognition`、`TableStructure`、`TableArtifact`。旧 [RapidVisual adapter](../../archive/experiments/2026-09-24/layout_decoupling/adapters/models.py) 使用 **RapidTable 3.0.0 + SLANet_plus ONNX**，权重在 `archive/experiments/2026-09-24/layout_decoupling/.models/rapid/slanet-plus.onnx`，冻结 SHA256 为 `d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b`。调用输入是 **table crop**，不是整页；`ocr_results=[外部行框、文字、置信度]`。`use_ocr=False` 在该版本会连 HTML 匹配一起跳过，因此旧 adapter 保持 `use_ocr=True`，覆盖内部 OCR 工厂为失败陷阱。输出有 HTML、`cell_bboxes`、`logic_points`；旧 `match_table` 把外部 OCR 行框匹配到 cell。

旧 Challenger 使用的外部 OCR 当时为 PP-OCRv5；本 POC 保留调用结构但换成**同一正式 PP-OCRv6 结果**。旧实验未迁入正式 app 的记录原因是结构/证据质量和业务质量均未过门槛，Rapid 路线还有第二读法、性能和留出样本证据缺口；当时不是“没有 adapter”。[历史实验结论](../../archive/experiments/2026-09-24/layout_decoupling/README.md)；[生产切换记录](ppdoclayoutv3-production-cutover.md)。

## 公平输入与统一 TableArtifact

每个候选使用当前正式 PP-DocLayoutV3 `layout_bbox`、同一 144 DPI PDF render crop、同一组 PP-OCRv6 文本行及 bbox。原生页没有 PP-OCRv6 结果，故候选只对三份合同内 **16 个扫描 table region** 做结构推理；完整 pipeline 则分别跑两侧。每个输入的 `page_id`、PDF `table_bbox`、crop 大小、完整 SHA256 和 OCR 文字+bbox SHA256 在 [input-ledger.json](../../output/analysis/scanned-table-structure-poc-20260924/input-ledger.json)。16/16 两模型输入 hash 与 bbox 完全相同。

实验目录的 [TableArtifact 适配](../../experiments/scanned_table_structure_poc/candidates.py) 输出 `page_id/table_bbox/source/rows/cols/cells/html/structure_status/structure_score/failure_reason`；每个 cell 有 `row/col/rowspan/colspan/text/bbox/confidence/evidence`，另附 `page_bbox`。坐标从 crop local 加 render crop 原点回到页面像素，再经 PDF render 逆仿射回到 PDF/page 坐标。当前 `structure_score=null`，因为本轮收集质量数据，**没有制定生产阈值**。候选产物只在实验目录，未送入正式 table compare。

Gate 实测检查 HTML 行列拓扑、`rowspan/colspan` 占位完整性、HTML cell 数与 bbox 数、cell bbox crop 边界、严重 cell 重叠、OCR 行框唯一归属、表内 OCR 文字覆盖。`ocr_words_total/assigned/rate` 和 `ocr_text_coverage` 在各片段 JSON 中；关键数字另逐格人工核对。通过 Gate 也只表示本次检查无结构错误，不等于准确率得到统计保证。

## 五张误判表：结构与人工核对

下表 `r×c/cells；assigned/total` 为每个片段的行列、cell 数和 OCR 行框归属。完整 HTML 在 [五表结果 JSON](../../output/analysis/scanned-table-structure-poc-20260924/known-table-results.json) 的 `candidates.<model>[].html`；对应 bbox 和证据紧邻该字段。长表跨页，逐页列出，不把片段数误当成一张独立表。

| 合同 / baseline | 片段 | RapidTable | Paddle | 人工核对 |
|---|---|---|---|---|
| ff24 D007 `DELETE` | p1 | 4×2/8；8/8，Gate OK | 4×2/8；8/8，Gate OK | 签订日期在右下 cell；两模型基本正确。最终变待复核日期 D007。 |
| ff24 D008 `DELETE` | p3 场站表 | 7×3/21；21/21，Gate OK | 7×3/21；21/21，Gate OK | “广核淮阴风电”在第 2 行第 2 列，右邻“风电”；视觉核对正确。最终 D008 为待复核候选。 |
| ff24 D009 `DELETE` | p3 报价首片 | 5×10/43；48/56，失败 | 5×9/37；56/56，Gate OK | “预测服务器”两模型均在产品名称列；首片 Paddle 基本正确。 |
| ff24 D009 | p4 | 17×11/133；111/143，失败 | 16×9/117；142/143，失败 | `64500` 在图像中为金额列“小计”；Rapid 未分配到 cell，Paddle 虽分配到 x≈734 的金额区域，逻辑列号为 2 且与 `500` 混入同格，不能认定正确。 |
| ff24 D009 | p5 | 16×9/103；74/106，失败 | 15×9/98；105/106，失败 | HTML/bbox 拓扑不一致。 |
| ff24 D009 | p6 | 19×9/131；106/143，失败 | 18×9/124；134/143，失败 | 再次出现 `64500`；Rapid 未可靠入格，Paddle 拓扑缺格/重叠。最终 D009 仅语义待复核。 |
| abdb D006 `DELETE` | p1 | 4×2/8；8/8，1 个 bbox 越界，失败 | 4×2/8；8/8，Gate OK | 文字和日期在正确 2 列，但 Rapid cell 几何越界；最终变待复核日期 D006。 |
| abdb D007 `DELETE` | p3 报价首片 | 15×10/99；90/116，失败 | 15×10/106；116/116，失败 | 图像“小计 17100”在右侧金额列；Rapid 未可靠入格；Paddle 放到第 8 列，但 HTML 106 cells 对应仅 95 boxes，不能用于 Evidence。 |
| abdb D007 | p4 报价续片 | 9×10/48；39/48，失败 | 9×9/43；48/48，失败 | `12400` 在小计金额列，Paddle 入第 7 列但拓扑缺格；`118000` 应是“四套合计”金额，Paddle 却放到逻辑第 0 列并与 `29500` 混格。Rapid 两数均未可靠入格。最终 D007 仅语义待复核。 |

人工核对用的是 [实际 table crop 图像](../../output/analysis/scanned-table-structure-poc-20260924/crops)，包括上述场站表、报价首片、金额续片及两张封面表。关键字“存在于 HTML”不算通过；金额的逻辑 cell 与 bbox 不能同时解释正确时，记为 `STRUCTURE_UNRESOLVED` / `EVIDENCE_COORDS_INSUFFICIENT`，不发布结构化结果。

## 候选质量、坐标和局部性能

| 指标（16 个扫描区域） | RapidTable/SLANet_plus | Paddle TableRecognitionV2 |
|---|---:|---:|
| 当前 Gate `STRUCTURE_OK` | 3/16 | 7/16 |
| PP-OCRv6 行框唯一归属 | 749/957 = 78.3% | 944/957 = 98.6% |
| 越界 cell 问题片段 | 多个，46 次记录 | 0 次记录 |
| `CELL_COUNT_MISMATCH` 问题片段 | 0 | 5 |
| 单表推理耗时 min/median/max，CPU | 0.029 / 0.064 / 0.216 s | 1.823 / 2.005 / 2.781 s |
| 含表页推理耗时 median/max，CPU | 0.086 / 0.216 s | 2.026 / 3.912 s |
| GPU memory delta | 不适用：本轮 CPU | 不适用：本轮 CPU |

模型初始化、正式 Layout/OCR、整份合同 pipeline 耗时均未混进上述局部推理数。Paddle 在复杂表的 OCR 归属率高，但 cell 拓扑错误依然使坐标不可信；Rapid 的速度优势不能抵消该差距。没有重跑 A–E 性能 benchmark。

## Compare 语义保护与边界

结构实验结束后，才在 [表格 Comparator](../../backend/app/services/table_compare/facade.py) 增加定向保护：对 `STRUCTURED_TABLE` 的未匹配 `DELETE`，只在页对齐且 Compare 同位置存在含文字的无结构 table region 时，标记 `compare_table_state=UNSTRUCTURED_TABLE_PRESENT`、`change_decision=CANDIDATE`、`decision_reason=TABLE_STRUCTURE_UNRESOLVED`，改成待复核表格候选并带双侧 region 证据；页面缺失或区域不重叠时不触发。界面对此状态显示“待复核”。没有按合同 ID 写特殊规则，也没有关闭整页 Diff。

这一步没有把结构 POC 结果注入正式 app；结构恢复仍是独立候选实验。`TABLE_ABSENT` 尚无可靠视觉证明，本轮不声称对所有真实整表删除做完判定。两张封面表经原有签署字段调和变成日期候选，保留了真实文本变化。三个合同的其他 diff 类型和文字未变化。

## MinerU 架构对照

MinerU 4.0.5 Basic/medium 的 table 路径在 crop 方向归一化后单独做 OCR；`TableCls` 失败时走 wireless，平时先批量跑 wireless，再对 wired 或低置信 wireless 跑 wired，可将 wireless HTML 作为参考；有效 HTML 回填表块，否则保持原有后备路径。txt 模式还先尝试 native 表格，复杂/失败时落入视觉结构分支。[方向与回退](https://github.com/opendatalab/MinerU/blob/94828a88d80ab8a72c21956de0604bf14f4cc9fc/mineru/backend/analysis/pdf/tables.py#L201-L559)；[crop OCR 与路由](https://github.com/opendatalab/MinerU/blob/94828a88d80ab8a72c21956de0604bf14f4cc9fc/mineru/backend/analysis/pdf/tables.py#L697-L904)。本项目保留 PP-DocLayoutV3、PP-OCRv6 和现有 PDF 坐标链，只借鉴分类/回退思路；没有安装 MinerU 或引入其 document pipeline。

## 验证及结论

仅这三份合同完成前后完整 pipeline，均无运行错误；`backend/tests/test_table_compare_structured.py` 与 `test_table_compare_merge.py` 共 **141 passed**，前端 `npm run build` 通过，实验脚本与表格 Comparator 的 Ruff 检查通过。前端构建仅提示既有大 chunk 警告。所有 16 个 table crop 两候选输入 hash 一致。第三份合同未见新增明显业务 regression，但复杂表结构 Gate 未通过。完整 15 份 Golden、A–E、生产部署均未运行。

当前失败类型：`merged cell failure`、`OCR-cell assignment failure`、`bbox unreliable`；复杂有线表同时表现为 `CELL_COUNT_MISMATCH`、`INCOMPLETE_GRID` 和 overlap。`wireless table failure` 与 `rotation failure` **未由本三份样本证实**，不能推断通过。若后续继续，可另行决定是否试 UniTable/TATR；本轮到此停止。

```text
POC_RESULT = INSUFFICIENT
POC_RECOMMENDED_CANDIDATE = Paddle TableRecognitionV2 (仅作为下一轮研究起点)
TABLE_STRUCTURE_POC = INSUFFICIENT
RECOMMENDED_CANDIDATE = Paddle TableRecognitionV2 / SLANeXt_wired
READY_FOR_APP_INTEGRATION = false
READY_FOR_PRODUCTION = false
FULL_GOLDEN_NOT_RUN = true
```
