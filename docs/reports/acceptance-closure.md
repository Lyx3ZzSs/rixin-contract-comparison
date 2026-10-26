# Table Structure Safety Gate：有限集成验收收口

日期：2026-09-24。代码提交：`848067e3e2de78e3a4aeef0489674b94e576e32a`（本轮验收测试、冻结回放脚本、结果页及报告消费端修正）。本报告对应当前共享工作树中的 V3 提取与 Table Compare 实现；基线代码尚有大量既存未提交改动，见末尾 BLOCKED。

## 结论

冻结输入能从正式 `TableResolver.resolve()` 走到 `to_page()`、JSON 往返、Parser、Compare、签署字段调和、事件投影与最终结果。16 个现有 cell graph 中 3 个发布、13 个保持不可发布；9 个 PDF vector 表均发布。NANJING p3 / p4 的冻结 MinerU 结构经同一个 Resolver 入口仍被 validator 拦下。五条历史整表 `DELETE` 在当前最终结果中均不存在；两条真正的封面日期 `MODIFY` 保留。

**有限验收通过，完整线上链路仍有两个 BLOCKED**：冻结包没有 embedding/rerank 响应，因此三合同业务回放明确关闭了这两项外部调用；生产代码目前也没有 `TABLE_ABSENT` 写入点，因此真实“明确无表”的产生链路尚无法用这批合同验收。没有放宽 validator/provider gate，没有接入新模型、重新请求 Layout/OCR/TSR、裁图、部署或运行其余 12 份 Golden。

## 冻结输入与回放边界

[输入哈希清单](../../output/analysis/table-structure-safety-gate-20260924/acceptance-closure/input-hashes.json) SHA256 为 `157b6ecf46069625912610539ec85056b76fba6e9ff7ca520b83a4b839e24d57`，逐项列出 8 张 crop、各自冻结 OCR digest / Paddle / MinerU 产物、16 个 graph、9 个 vector 表所在 page artifact、共 23 个有表的 page artifact、6 份冻结提取文档。8 张 crop 的原始 manifest 为 [input-manifest.json](../../output/analysis/scanned-table-paddle-vs-tatr-20260924/input-manifest.json)；测试同时核对 crop 像素 hash 与 OCR JSON digest。已有 [八图 validator 结果](../../output/analysis/table-structure-safety-gate-20260924/frozen-eight-validation.json) 是规则冻结依据，本轮未调阈值。

[冻结回放脚本](../../experiments/scanned_table_structure_poc/run_acceptance_closure.py) 逐个从 page artifact 取 table region，在 `_tokens`、`_native_cells`、`upright_table_crop`、`_raster_cells`、`build_graph` 边界回填已保存的 OCR、cell graph 或 vector cells，然后实际执行 `TableResolver.resolve()`。16 个 graph 回放只需冻结的 graph 输出，传给已替换的 crop 边界的是 1×1 占位图，**没有重新裁图或结构推理**。vector 回放从冻结 vector cells 进入同一个 Resolver。脚本把 Resolver 新产出的完整表区 blocks 按页号及 1 pt 内的 region bbox 匹配回 6 份冻结提取文档，保留其它 blocks，随后做 `Document.model_dump_json()` / `model_validate_json()`，运行正式 `CompareService` 的后续业务 pipeline。每个 region 的来源、状态、诊断与替换 block 数在 [三份 Resolver trace](../../output/analysis/table-structure-safety-gate-20260924/acceptance-closure/) 中，完整任务 JSON 与 [断言结果](../../output/analysis/table-structure-safety-gate-20260924/acceptance-closure/assertions.json) 在同目录。

| 要求 | 真实链路证据 |
|---|---|
| validator FAIL 后保留表区、OCR 和诊断，禁用 HTML/cells | `test_frozen_validator_failure_survives_resolver_and_json_round_trip`；NANJING p3 `LOGICAL_SLOT_OVERLAP`，p4 `LOW_OCR_ASSIGNMENT` / `UNCOVERED_OCR_ROW_BAND`；round-trip 后 `raw_html`、`table_cell_bboxes` 均空，Parser 得到 0 张可信表 |
| validator PASS 不能覆盖 provider 拒绝 | `test_frozen_provider_failure_cannot_be_upgraded_by_validator_pass`；RETURN compare p3 graph 的 validator 本身无 failure reason，最终仅有 provider `RASTER_RULE_NOT_CONFIRMED`，不发布 |
| 两个 gate 均 PASS 保留正常结构比较 | `test_frozen_provider_and_validator_pass_keep_structured_compare`；RETURN compare p3 冻结 Paddle 表可解析；自身比较为零差异 |
| PDF vector 正常发布 | `test_frozen_pdf_vector_resolver_publishes_and_round_trips`；`test_all_frozen_production_providers_keep_publication_monotonic` 覆盖全部 9 张 |
| 全部正式 provider 维持单调发布 | 同一参数化回放覆盖 16 graph + 9 vector；最终 3/16 graph、9/9 vector 发布，13 个 graph 不发布 |
| 异常不升级为 absent | `test_frozen_resolver_geometry_exception_stays_present_not_absent` 在真实 Resolver 的几何边界注入异常，输出仍是 `UNSTRUCTURED_TABLE_PRESENT`、无 HTML/cells；这是故障注入，不冒充冻结模型异常 |
| JSON 往返及 Compare | 以上测试的 `Page` round-trip；脚本的 `Document` 与最终 `CompareTask` round-trip；`test_resolver_failure_reaches_compare_without_deterministic_delete` 与 [assertions.json](../../output/analysis/table-structure-safety-gate-20260924/acceptance-closure/assertions.json) |

原始 provider 产物只在测试回放边界和 diagnostics 中出现。Resolver FAIL 后，传给 Parser 的 `TextBlock` 有 `UNSTRUCTURED_TABLE_PRESENT`，且不带可信 HTML/cells；Parser 不会把 provider 候选再次解析成结构。NANJING p3/p4 这条诊断链分别由上述测试的 `table.validation.reasons` 断言固定。其余六张 MinerU 为 6 PASS / 0 FAIL；八张总计 6 PASS / 2 FAIL。Paddle 同一 validator 为 3 PASS / 5 FAIL，详见既有 [安全门报告](table-structure-safety-gate-20260924.md)。这些是确定性安全规则的结论，不等于逐格语义正确。

## 最终差异与历史误删

原始结果取 [RETURN baseline](../../output/analysis/scanned-table-structure-poc-20260924/baseline/RETURN_CONFIRMATION/result.json) 和 [KAILI baseline](../../output/analysis/scanned-table-structure-poc-20260924/baseline/KAILI_FOUR_PROJECTS/result.json)；语义保护结果取同目录 `after/*/result.json`；当前结果取 [acceptance-closure](../../output/analysis/table-structure-safety-gate-20260924/acceptance-closure/) 中对应合同 JSON。逐项机器对应在 [five-transition.json](../../output/analysis/table-structure-safety-gate-20260924/acceptance-closure/five-transition.json)。以下 `ADD` 是实际枚举；界面文案“新增”，旧讨论中的 `INSERT` 指同一方向，不另计一类。

| 原结果 | 语义保护结果 | 当前 Resolver→最终结果 |
|---|---|---|
| ff24 D007：table `DELETE` | metadata `ADD` 候选，`TABLE_STRUCTURE_UNRESOLVED` | 同左；`NEEDS_REVIEW`，事件投影仍为待复核候选 |
| ff24 D008：table `DELETE` | table `MODIFY` 候选，`TABLE_STRUCTURE_UNRESOLVED` | 同左 |
| ff24 D009：table `DELETE` | table `MODIFY` 候选，`TABLE_STRUCTURE_UNRESOLVED` | 同左 |
| abdb D006：table `DELETE` | metadata `ADD` 候选，`TABLE_STRUCTURE_UNRESOLVED` | 同左；`NEEDS_REVIEW`，事件投影仍为待复核候选 |
| abdb D007：table `DELETE` | table `MODIFY` 候选，`TABLE_STRUCTURE_UNRESOLVED` | 同左 |

这里没有删除 ff24 D007 / abdb D006 日期候选，也没有改签署字段调和逻辑。两条真实封面差异仍是 ff24 D004 `2026 年4 月日 → 2026年4月2日`、abdb D002 `2026 年月日 → 2026年4月21日`，均为 metadata `MODIFY`。当前三份结果分别为 **27 / 19 / 17** 条，无 pipeline error，table `DELETE=0`、table `ADD=0`、table `MODIFY=2/1/1`。

NANJING 从先前仅替换提取文档的 19 条变为真实 Resolver block 回放的 17 条：先前的占位日期 `DELETE` 消失，原版和新版封面 OCR 实际都含 `2026年`、`月`、`日`；先前签章区的地址 `MODIFY` 不再单列，但地址两侧仍以 metadata `DELETE` / `ADD` 保留。其表格变化仍为 `TABLE_STRUCTURE_UNRESOLVED` 的 `MODIFY`，无新增确定性整表增删。该变化属于表区 block 状态/ID 从冻结旧文档换成真实 Resolver 输出后，既有后处理的结果；它限制“与旧结果逐项完全一致”的结论，但未发现这份合同的明显新内容丢失。

## 不确定性在消费端

`CompareTask` JSON 往返后，ff24 D007、abdb D006 均仍为 `ADD + NEEDS_REVIEW + TABLE_STRUCTURE_UNRESOLVED`。`event_response()` 投影仍保留 reason 与 `NEEDS_REVIEW`；`build_audit_stats()` 对两份合同的**确定新增都是 0**。`event_response().counts.insert` 分别为 16 和 8，它是既有 `textin-top-level-changes-v1` 的**全部方向事件数**，包含待复核候选，不是确定新增统计；当前前端不消费该数作确认统计。

本轮修了两个实际消费缺口：[结果页](../../frontend/src/pages/ResultPage.tsx) 现在对 metadata/table 的两种未决 reason 都显示“待复核”；[事件 PDF 导出](../../backend/app/services/report_comparison.py) 的单侧候选写“待复核·疑似新增 / 疑似删除”和“对应内容未确认”，[摘要](../../backend/app/services/report_generator.py) 只把非 `NEEDS_REVIEW` 项计入“确定新增/删除/修改”。前端测试覆盖两种 reason；PDF 测试和两份真实任务导出 [export-checks.json](../../output/analysis/table-structure-safety-gate-20260924/acceptance-closure/export-checks.json) 核对候选标识、未确认提示及真实日期文字。没有新增统计框架。

## `TABLE_ABSENT` 来源与历史兼容

生产代码中 **`TABLE_ABSENT` 写入点为 0**：`Page.table_presence_status` 默认 `UNKNOWN`；Parser 只读取 block 状态，Compare 只读取明确标记且同页无冲突 table block 时才允许整表 `ADD` / `DELETE`。[状态/保护代码](../../backend/app/services/table_compare/facade.py) 的证据检查要求对齐页齐全、页面显式 `TABLE_ABSENT`、该页无 table block。未解析 `<table>`、空 parse 结果、Resolver 几何异常、历史 JSON 缺字段、对齐页证据不足都不会单独赋成 absent。测试 `test_empty_or_historical_missing_status_is_unknown_not_absent`、`test_unparsed_html_and_unaligned_absence_do_not_prove_delete`、Resolver 故障注入分别覆盖这些情况。

`test_claimed_absence_with_conflicting_table_block_cannot_delete_or_add` 验证冲突 block 反向都阻止确定方向；`test_true_absent_table_can_delete` / `test_true_absent_table_can_insert` 用显式 absent 的**合成证据**验证左右交换后的 `DELETE` / `ADD`。历史 JSON 缺 `structure_status`、`table_validation`、`table_presence_status` 可往返读取；有旧 HTML 的 table block 仍按历史规则解析为结构化表，空页默认 `UNKNOWN`，不会升级为 absent。真实生产的 absent 赋值链仍待后续单独实现和验收，不能把合成测试冒充该链路已经存在。

## 测试、FAIL 与 BLOCKED

实际命令及结果：

```text
PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/test_table_structure_safety_gate.py backend/tests/test_table_structure_acceptance_frozen.py backend/tests/test_table_compare_structured.py backend/tests/test_report_comparison.py -q --disable-warnings --maxfail=1
151 passed（最终补充异常测试后；仅这四个文件）

npm test -- --run src/pages/ResultPage.test.tsx
24 passed

npm run build
passed（Vite 有既有 chunk-size 提示）

backend/.venv/bin/ruff check backend/app/services/extractors/contract_v3/tables.py backend/app/services/report_comparison.py backend/app/services/report_generator.py backend/tests/test_table_structure_acceptance_frozen.py backend/tests/test_table_structure_safety_gate.py backend/tests/test_report_comparison.py experiments/scanned_table_structure_poc/run_acceptance_closure.py
passed

PYTHONPATH=backend backend/.venv/bin/python experiments/scanned_table_structure_poc/run_acceptance_closure.py
RETURN 27、KAILI 19、NANJING 17；0 error；五条保护断言、两条日期断言、事件投影断言通过
```

**FAIL：无本轮验收断言失败。** 既有冻结 validator 判定的 NANJING p3/p4 为预期的结构 FAIL，属于安全拦截。

**BLOCKED / 结论限制：**

1. 六份冻结提取文档没有 embedding/rerank 外部响应；正式业务回放将 `MATCH_ENABLE_SEMANTIC_MATCH`、`MATCH_ENABLE_RERANK` 设为 `false`，故不能声称验证了这两项线上外部调用的逐项等价。首次探测环境端点返回 502，此后回放已禁用请求；没有用新推理补产物。
2. 生产没有 `TABLE_ABSENT` writer，本轮只能验证“未知不升级”和显式 absent 的 Compare 分支，无法证明真实无表观测链路。
3. 当前仓库的 V3 提取基础代码仍是本轮开始前已存在的未提交工作树内容；本轮代码提交只包含收口增量。仅检出 `848067e3` 的干净 HEAD 不包含完整 V3 基础实现，不能独立重放本报告的集成脚本；冻结产物也保存在本地 `output/analysis`。这是提交可移植性的限制，不能当作生产就绪证明。

`FULL_GOLDEN_NOT_RUN = true`；`NEW_MODEL_INTRODUCED = false`；`READY_FOR_PRODUCTION = false`。本轮到此停止，不启动模型研究、fine-tuning、VLM 或 MinerU 生产接入。
