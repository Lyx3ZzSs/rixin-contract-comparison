# 合同比对优化实施与验证

日期：2026-09-16。范围：本地工作区代码、隔离的性能/质量实验；未部署、未改生产配置或历史任务。原有未提交修改保留，开始实施时的实际工作区代码已冻结，未用 Git HEAD 代替基线。

**后续启用更新：** 用户明确要求默认开启加速，现已将应用默认值及两份环境示例中的 `MATCH_PERFORMANCE_STRATEGY` 改为 `enabled`。本地 `.env` 无该项覆盖；新启动的后端进程将使用加速，已有进程需重新加载配置。冲突回退保留，`off` 仍可关闭。本节以下保留首次验收时的状态和实验边界：后续启用不表示两个无效模型路径或既有 Golden 失败已经解决。模型输入去重与原生页面路由仍关闭。

启用后验证：默认值、当前配置解析值和 `MatchStage` 实际创建的匹配器均为 `enabled`，显式 `off` 回退有效；后端普通回归再次通过 1,747 项。日志：`output/analysis/optimization-20260916/backend-default-enabled-tests.log`。

## 结论与当前启用状态

已交付输入文件提前预览、精确匹配的影子诊断与排除原因、OCR 服务显式计时采集，以及可复现的真实匹配基准入口。提前预览随本次代码生效；匹配加速、模型输入去重和原生页面路由继续保持默认关闭。

现有精确匹配策略在长样本上的五轮真实匹配中位数降低 **32.46%**，达到首轮 30% 目标；但完整流程仍约 **149 秒**，未达到 90 秒预算。OCR 服务端尚无部署入口，解析优化没有实施。不能把本次交付称为整个多阶段方案已完成。

## 本轮代码改动

| 模块 | 改动及作用 |
|---|---|
| API schema/presenter | 新增 `preview_available`，两侧输入文件都已发布才返回 true；继续使用现有鉴权和文件接口。无需数据库迁移。 |
| `ResultPage.tsx` | 处理中显示文档与阶段进度；支持缩放和同步滚动，最终状态到达后显示差异。处理中不显示零差异结论，不提供审核及报告导出。文档组件在完成时保持挂载。 |
| 进度 hook | 输入尚未发布时通过轮询取得预览可用性，之后切换到 SSE；保留持久化 revision 确认、失败与重试语义。 |
| `candidate_recall.py` / `matcher.py` | 记录精确候选的首个排除原因；shadow 模式记录候选对应是否与完整匹配一致，每次任务重置。未放宽原始全文相等、双侧唯一、结构一致、高置信单页对应等条件。 |
| `remote_metrics.py` / 两个 OCR extractor | 采集显式 `Server-Timing` 数值与合法 `logId`，服务计时和客户端请求时间分开；无服务计时时记录不可用。不记录认证头、任意响应元数据或描述文本。 |
| 基准脚本 | `benchmark_match_computation.py` 增加 live/replay、策略、输入去重参数；新增只读 `audit_exact_matching.py`，输出输入哈希、覆盖率与分歧。 |

精确匹配快路径、请求输入去重、页面渲染缓存和失败后的全量匹配回退在实施前已存在。本轮验证并补足诊断，没有把它们算作新实现。

## 性能结果

### 匹配阶段：真实 embedding/rerank，各五轮

固定相同匹配输入，基线/候选交替顺序运行。OCR 不在此计时内。20 次执行均无 HTTP 失败；每轮最终条款对应和原始文本一致。

| 样本 | 基线中位数 | 精确匹配候选中位数 | 降幅 | rerank 请求数 |
|---|---:|---:|---:|---:|
| 长合同 55＋54 页 | 15.876 s | 10.722 s | 32.46% | 367 → 230，减少 37.33% |
| 短合同 22＋22 页 | 3.057 s | 2.282 s | 25.37% | 73 → 47，减少 35.62% |

长样本满足当前严格条件的是 **137 对**，短样本 **26 对**；没有用研究阶段较宽松的 286 对估算收益。rerank 减半的研究目标未达成。五轮只用于中位数观察，不代表稳定 P95。

### 完整流程：真实 OCR + 模型，各一组前后对照

| 样本 | 基线完整耗时 | 候选完整耗时 | 解析阶段基线 → 候选 | 最终差异数 |
|---|---:|---:|---:|---:|
| 长合同 | 156.164 s | 149.425 s | 91.889 → 96.873 s | 79 → 79 |
| 短合同 | 50.494 s | 50.952 s | 30.195 → 31.633 s | 36 → 36 |

这四次运行均无 HTTP 失败或缓存缺失。长样本完整耗时约降低 4.32%，短样本增加约 0.91%；只有一组完整对照，不能断言稳定提升或退化。解析等待抵消了部分匹配收益。完整长样本的外层匹配计时为 22.301 → 10.869 秒，明显受服务负载影响，因此首轮匹配结论采用前述五轮中位数。

两组最终事件快照（含页面对应）、签章轮廓、文档画像、OCR 质量结果及审核字段均相同。长样本 diff 全字段相同；短样本仅 4 项 `match_candidates` 诊断不同，正文、方向、证据和最终事件不变。不能写成所有返回字段完全一致。

### 模型输入去重：独立小范围实验

单独保持匹配策略 off，仅启用去重，长/短样本的 embedding 请求数分别从 23 → 14、6 → 4；rerank 仍为 367、73。两组条款对应和文本不变；长样本 9 项候选诊断发生变化，短样本完整匹配对象相同。

该实验只有每组一次真实请求，尚未完成完整业务输出和五轮成对验收，不叠加启用、不宣称稳定提速。原生页面路由也未启用。

## 质量与回归

- 17 组保存语料的精确候选审计：775 对均与原有最终对应一致，0 分歧。
- 17 组候选回放：业务结果均相同；16 组 diff 全字段相同，另 1 组仅上述候选诊断变化。事件快照和嵌套页面对应逐项核对，未将不存在的顶层字段当作验证依据。
- Golden：55 条既有失败仍为 55 条，细分为 36 条 `FALSE_NEGATIVE_REGRESSION`、19 条 `DECISION_REGRESSION`；逐条 case、类型、预期、实际与原因完整记录相同，无新增、无抵消。真值文件未修改。
- 两组回放不是有效正常模型路径：`SUIZHOU_CHANGYUAN` 有 6 次缺失响应，`HUANENG_DINGBIAN` 有 16 次缺失响应。已有基线涉及超长 rerank 输入与降级，这两组不纳入正常路径性能统计，17 组正式质量门槛仍未通过。
- 后端普通回归：**1,747 passed，1 个 Golden 阻断门禁未包含在普通回归中**；Golden 已通过上述独立前后结果核对报告失败，不称“全通过”。前端 **154 passed / 23 个测试文件**。生产构建通过，保留现有 PDF chunk 超过 500 kB 的提示。
- 浏览器：隔离模拟 API＋合成 PDF；处理中 4 个页面 canvas 就绪、报告导出禁用、无零差异结论、两侧滚动位置均到 500；完成后 canvas 节点与滚动位置保留，导出开放。截图捕获超时，证据为 DOM、canvas 状态与真实交互，未取得截图证据。临时服务已停止并清理。

## OCR 服务诊断与下一步入口

已核实服务公开接口：PP-Structure 的 `/health`、`/layout-parsing`，PPOCRv5 的 `/health`、`/ocr`。公开 schema 未提供服务端分阶段分析入口。客户端已能接受显式服务计时，本次真实请求未得到 `Server-Timing`，不能把请求墙钟时间归因到 GPU 推理。

尚需 OCR 服务部署目录或现成 SSH/容器管理入口。取得入口后，应在服务端以请求 ID 关联下列耗时：排队、PDF 渲染、版面分析、文字检测/识别、表格处理、整合。响应可使用例如 `Server-Timing: queue;dur=12, render;dur=30, layout;dur=100`，单位为毫秒；这些是协议示例值，不是测量结果。每页计时、GPU 利用率及模型版本放在关联日志中。存在重叠的并行阶段不能简单相加。

先测出关键路径，再选择有界页面批处理、基础识别产物复用或排队调度。当前两份文档与两条识别组件已经并行，不盲目提高客户端并发。服务版本与硬件资源在现有 API 中不能完整确认，未伪造固定服务负载条件。

## 方案批次状态与回退

| 批次 | 当前状态 | 继续条件 |
|---|---|---|
| A 基线与可观测性 | 已冻结工作区、输入哈希；完成失败明细、影子审计及客户端计时接入 | 补齐服务端阶段计时与正常模型路径 |
| B 减少匹配工作量 | 精确对应候选完成五轮匹配与 17 组回放；独立去重实验完成 | 解决两组模型路径无效问题及严格质量门槛，再默认开启；去重需独立完整验收 |
| C 解析优化 | 仅完成 API 诊断和计时接入 | OCR 服务部署入口、资源与版本信息 |
| D 产物复用 | 未新增缓存/跳过检查 | 先取得可观重复工作的耗时证据 |
| E 提前展示 | 文档提前预览已交付 | 基础差异版本化另行完成；SummaryStage 仍会增删/合并事件，不能提前发布最终差异 |
| F 综合验收 | 当前改动回归与代表性实验完成 | 尚未达到 90 秒预算，未进行默认启用后的混合负载验收 |

匹配配置继续为 `MATCH_PERFORMANCE_STRATEGY=off`、`MATCH_DEDUPLICATE_MODEL_INPUTS=false`、`COMPARE_NATIVE_PAGE_MODE=off`；没有修改运行环境文件。shadow 可用于诊断但不改变正式匹配结果。提前预览兼容旧后端（缺少标志时继续等待完成）和旧前端（忽略新字段）；没有引入部分事件快照、审核迁移或数据库回滚要求。

## 复现与证据

证据根目录：`output/analysis/optimization-20260916/`。完整模型/OCR响应仅用于本地隔离回放，应用任务库和历史审核记录未被实验写入。TextIn 凭据未写入代码、配置或报告。

关键文件：

- `baseline-code/`、`baseline-hashes.json`、`baseline-status.txt`：实施前实际工作区；`inputs.json`：输入 PDF 哈希。
- `exact-audit.json`：775 对候选及排除原因；`quality.json`、`known-golden-failures.json`、`initial-quality-comparison.json`：17 组结果和逐条既有失败。
- `live-matching-comparison.json`、`live-matching/`：五轮匹配原始结果；`live-quality.json`、`live-full-comparison.json`：完整流程字段核对及阶段耗时。
- `dedup-comparison.json`：输入去重的独立实验边界。
- `service-capabilities.json`、`browser-checks.json`、`backend-full-tests.log`、`frontend-full-tests.log`、`frontend-build.log`：接口与回归证据。

在项目根目录执行（`<...>` 替换为实际路径，输出目录必须尚不存在）：

```sh
.venv/bin/python backend/scripts/audit_exact_matching.py --input-root <保存完整基线的目录> --output <审计结果.json>
.venv/bin/python backend/scripts/benchmark_match_computation.py --input <matching-input.json> --source-task-id <原任务ID> --output <新基准目录> --cache <HTTP缓存目录> --http-mode live --performance-strategy enabled
.venv/bin/pytest backend/tests -q -m 'not golden_contract_blocking'
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

`--performance-strategy off|shadow|enabled` 独立选择策略；去重独立加 `--deduplicate-model-inputs`。使用 `--http-mode replay` 时缺失响应必须视为实验无效，不能当作“更快”。
