# Table Structure Safety Gate — 2026-09-24

## 结论与范围

本轮把“表格结构恢复失败”和“对侧确实没有表格”拆成四种业务状态：`STRUCTURED_TABLE`、`UNSTRUCTURED_TABLE_PRESENT`、`TABLE_ABSENT`、`UNKNOWN`。未解析出 `<table>` 不再推导整表删除。两份指定历史任务对应的五条原整表 `DELETE` 均消失；NANJING p3、p4 被同一个确定性 validator 自动拦下。没有引入新模型、重新 OCR、重新裁图、运行其它 12 份 Golden 或修改 Golden expectation。

验证范围仅为三份现有合同、冻结的 8 张 crop 与其 PP-OCRv6 / Paddle / MinerU 产物，以及这三份合同的既有 V3 cell graph。业务回归用冻结的完整提取文档替换提取阶段，继续执行后续正式业务 pipeline；因此没有重新请求 Layout、OCR 或 TSR。两份已知任务沿用 `ff24e3cb-92b7-427d-8be8-842d295f7af0`、`abdb9c3b-6d44-4fa6-a61a-40d4b6cda578` 作为隔离回归运行的 task ID；第三份沿用 `593d2c7b-ffae-4822-bd6a-4c369f5c1909`。

## 正式语义与代码

[状态字段](../../backend/app/models.py) 在 `TextBlock.structure_status`、`Page.table_presence_status` 上使用四值类型，历史 JSON 缺字段时默认 `UNKNOWN`。[Parser](../../backend/app/services/table_compare/parser.py) 从明确状态或有文字的 table region 判断表格存在；只有结构化表进入 cell-level 比较。[Compare](../../backend/app/services/table_compare/facade.py) 对未匹配整表双向检查同位置表区：对侧有 OCR table region 时输出 `MODIFY`、`CANDIDATE`、`NEEDS_REVIEW`、`TABLE_STRUCTURE_UNRESOLVED`；对侧证据不足时输出 `MODIFY`、`TABLE_PRESENCE_UNKNOWN`。仅当对齐页面有明确 `TABLE_ABSENT` 标记、且该页没有冲突 table block 时，保留真实 `DELETE/ADD`。既有数据不会因为缺新字段而要求数据库迁移；历史任务 JSON 已成功用当前 `CompareTask` 读取。

[TableStructureValidator](../../backend/app/services/extractors/contract_v3/table_structure_validator.py) 独立于结构提供者。正式 [TableResolver](../../backend/app/services/extractors/contract_v3/tables.py) 对 PDF vector 和 `table_cell_graph` 均调用它。PASS 才发布 HTML/cells；FAIL 保留表区、OCR 原文、页面证据、`validation` 与 provider issues，状态为 `UNSTRUCTURED_TABLE_PRESENT`，不发布可信 cells。旧 graph 的 raster rule / token 等 provider failure 也并入诊断原因。没有接入 MinerU 生产推理。

规则阈值保持保守、可解释：cell 必须在正行列网格内，任意 logical slot 冲突即失败；两个不同 cell 的几何交面积达到较小 cell 的 **50%** 且 IoU 达 **0.10** 才是严重 bbox overlap。OCR 使用统一 `assign_ocr_to_cells()`；至少 5 条 OCR、至少 3 条未分配、分配率低于 **0.80** 才触发 `LOW_OCR_ASSIGNMENT`。未分配文字按 y 中心和行高聚类，允许相邻折行合并；至少 2 条文字横跨表宽 **25%**、位于表内且整个带无 cell 覆盖，触发 `UNCOVERED_OCR_ROW_BAND`。大跨度 merged cell 单独不失败，只有伴随 slot/bbox 冲突才标记可疑。每张表保存 `cell_coverage_ratio` 与 `ocr_weighted_coverage_ratio`，面积覆盖率本身不作为唯一 Gate。人工 expected rows/cols 未进入生产规则。

## 冻结 8 张 crop：同一 Validator 对两种 provider

输入 SHA256 与冻结 manifest 逐张一致，直接读取已有 JSON，没有模型 inference。完整机器结果和每张表的 `reasons/metrics` 在 [frozen-eight-validation.json](../../output/analysis/table-structure-safety-gate-20260924/frozen-eight-validation.json)。另外六张 MinerU overlay 已逐张目视复核：未见本轮规则针对的明显逻辑重叠或整行 OCR 裸露；这不是完整逐格人工真值验收。

| Crop | MinerU | Paddle |
|---|---|---|
| RETURN p3 | PASS | PASS |
| RETURN p4 | PASS | PASS |
| RETURN p5 | PASS | FAIL：`INVALID_CELL_BBOX`、严重 bbox overlap、可疑合并格 |
| RETURN p6 | PASS | FAIL：严重 bbox overlap、可疑合并格 |
| KAILI p3 | PASS | FAIL：`INVALID_CELL_BBOX` |
| KAILI p4 | PASS | PASS |
| NANJING p3 | **FAIL**：`LOGICAL_SLOT_OVERLAP`、`SUSPICIOUS_MERGED_CELL` | FAIL：`INVALID_CELL_BBOX`、严重 bbox overlap、可疑合并格 |
| NANJING p4 | **FAIL**：`LOW_OCR_ASSIGNMENT`、`UNCOVERED_OCR_ROW_BAND` | FAIL：`INVALID_CELL_BBOX` |

MinerU：**6 PASS / 2 FAIL**。Paddle：**3 PASS / 5 FAIL**。同一个 validator 使用统一 cell / OCR 结构，没有读取 provider 名称作决策。PASS 仅说明这些确定性灾难规则未触发，不代表整表语义完全正确；例如原冻结报告所述的金额 `5/5` 也不能替代整表验收。

NANJING p3：预测的长合并格和另一 cell 共同占据 logical slot `[15,7]`，cell IDs 为 `c0058`、`c0102`，所以自动失败；不依赖识别“11000”文字。OCR 分配 `126/127=99.21%`，说明仅凭分配率会漏掉该错误。NANJING p4：OCR 分配 `8/13=61.54%`，未分配的 5 条文字形成表内 `[8,146,957,293]` 的连续横向 band，整个 band 无 cell，故两个独立原因同时失败；没有对“备注”做文字特判。

## 三份合同的业务回归

[隔离回归结果](../../output/analysis/table-structure-safety-gate-20260924/three-contract-replay/summary.json)保存三份合同的 diff 状态和错误；完整结果 JSON 在同一目录。与上一轮语义保护后的冻结业务结果相比，三份均无 error，diff 总数仍为 **27 / 19 / 19**，非表格 diff 的 `id/type/original_text/compare_text` **全部一致**。第三份合同没有新增明显业务 regression；其表格 D014 仍为 `TABLE_STRUCTURE_UNRESOLVED` 的 `MODIFY`。三份合同表格 `DELETE=0`、`INSERT=0`、`MODIFY=2/1/1`。

| 历史误删 | 本轮状态 |
|---|---|
| ff24 D007 | 签署字段调和后的日期 `ADD` 候选，`NEEDS_REVIEW`、`TABLE_STRUCTURE_UNRESOLVED`；已不是整表 `DELETE` |
| ff24 D008 | 表格 `MODIFY`，`CANDIDATE`、`NEEDS_REVIEW`、`TABLE_STRUCTURE_UNRESOLVED` |
| ff24 D009 | 表格 `MODIFY`，`CANDIDATE`、`NEEDS_REVIEW`、`TABLE_STRUCTURE_UNRESOLVED` |
| abdb D006 | 签署字段调和后的日期 `ADD` 候选，`NEEDS_REVIEW`、`TABLE_STRUCTURE_UNRESOLVED`；已不是整表 `DELETE` |
| abdb D007 | 表格 `MODIFY`，`CANDIDATE`、`NEEDS_REVIEW`、`TABLE_STRUCTURE_UNRESOLVED` |

两条真实日期变化仍在结果中：`2026 年4 月日 → 2026年4月2日`（ff24 D004），`2026 年月日 → 2026年4月21日`（abdb D002）。这两条均为 `MODIFY`，没有被表格安全降级吞掉。封面表自身转日期候选属于既有签署字段调和行为，本轮没有修改该逻辑。

## 正式 cell graph 与耗时

只读取这三份合同在上一轮已保存的 [16 个 cell graph](../../output/analysis/table-structure-safety-gate-20260924/frozen-three-cell-graphs.json)，不重建 crop / OCR：原先 `CONSISTENT` 的 3 个结构均通过 validator，**新增拦下 0 个**；原先 `TABLE_STRUCTURE_UNRESOLVED` 的 13 个仍不发布，其中 6 个也被 validator 拦下，7 个虽通过本轮确定性规则，仍因原 provider rule 失败而不发布。另读同三份合同冻结的 [9 个 PDF vector 表](../../output/analysis/table-structure-safety-gate-20260924/frozen-three-vector-tables.json)，均通过 validator。正式新代码已保证 provider issues 继续阻止发布。

Validator 单表耗时：MinerU 八张的 median **2.58 ms**、max **15.88 ms**；Paddle 八张 median **2.83 ms**、max **9.02 ms**；三合同 16 个冻结 graph 合计 **44.55 ms**、median **0.94 ms**、max **8.36 ms**。均远低于秒级模型推理。这个耗时是几何规则本身，不包含读取 JSON。

| 合同 | 旧完整 pipeline | 本轮冻结提取回放 | 可比的提取后业务阶段：旧 → 本轮 | 表格准备 `ruled_tables`：旧 → 本轮 |
|---|---:|---:|---:|---:|
| RETURN | 20.459 s | 7.123 s | 6.789 → 6.890 s | 1.361 → 1.410 s |
| KAILI | 13.039 s | 5.240 s | 5.186 → 5.110 s | 0.641 → 0.652 s |
| NANJING | 15.812 s | 5.683 s | 5.868 → 5.647 s | 0.754 → 0.751 s |

完整 pipeline 两列不可直接比较性能：旧运行包含提取、Layout、OCR，本轮为满足“不要重新 OCR”而注入冻结提取文档。`ruled_tables` 是既有业务准备计时；本轮 validator 在冻结 artifact 上单独计时，没有把在线新提取成本伪装成业务回放计时。

## 验证与后续边界

目标单元测试覆盖 slot/bbox overlap、OCR 分配率、无 cell 行带、有效大合并格、双向 Compare、真实 absent、日期差异与提取 PASS/FAIL；相关 3 个测试文件 **146 passed**，Ruff 检查通过。没有运行全部 15 份 Golden，也没有部署。下一阶段可另立研究，对合同表格标注数据后的专用 fine-tuning 和只处理 unresolved 表的 VLM fallback 做比较；本轮证据不足以选择或接入其中任一方案。当前不宣称生产就绪。

```text
TABLE_ABSENT_SEMANTICS_FIXED = true
FALSE_TABLE_DELETE_FIXED = true
TABLE_STRUCTURE_VALIDATOR = PASS
NANJING_P3_CAUGHT = true
NANJING_P4_CAUGHT = true
NEW_MODEL_INTRODUCED = false
THREE_GOLDEN_RUN = true
FULL_GOLDEN_NOT_RUN = true
READY_FOR_NEXT_TABLE_STAGE = true
READY_FOR_PRODUCTION = false
```
