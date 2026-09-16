# 合同比对性能优化实施与验证

日期：2026-09-15。依据：[实施方案](../plans/comparison-performance-optimization-20260915.md)。

## 结论

页面对齐缓存已达到阶段目标：代表长合同的准备后真实输入，三轮中位数由 **45.824 秒降至 13.989 秒，减少 69.5%**；短合同由 **4.598 秒降至 1.148 秒，减少 75.0%**。十二次组件执行的页对应、状态、分数、锚点均一致。

当前默认启用页面对齐缓存、条款特征缓存和有界页面渲染缓存。候选裁剪、模型输入去重、页面级原生识别已提供独立试验开关，保持关闭。图像复用未达到 30% 的预期收益，停止扩大改造。页面路由试验出现新增识别差异，不进入区域级识别简化。

**质量门禁未通过，不能据此发布“Golden 全通过”或“90 秒完整任务”结论。** 固定 OCR 的 17 组基线/默认候选，完整差异对象逐项一致，Golden 均为 55 条失败；其中两份合同的外部重排在原版本也发生输入超长错误，因此这两份不计入正常模型路径的验收通过数。

## 已实现的范围

| 阶段 | 实现 | 当前状态 |
|---|---|---|
| P0 | 冻结当前工作区代码、17 组 PDF 哈希与配置；独立数据库中的真实排队流程；真实 HTTP 捕获、严格缓存缺失检测、质量对照、进程内并发驱动 | 已实现 |
| P1 | 单次 `align()` 拥有的有界 LRU；复用页面组合、n-gram、布局、锚点和有向文本相似度；返回后释放缓存；不裁剪动态规划状态 | 默认启用 |
| P2a | 单次匹配复用规范化正文、编号和业务字段；同批 embedding 文本及相同有序 rerank 输入去重 | 特征缓存启用；模型去重关闭 |
| P2b | 仅对正文完全一致、双侧唯一、结构一致且单页高置信对应的条款尝试跳过宽召回/重排；分配冲突回退原流程；记录 shadow 命中与模型批次指标 | 试验开关关闭；未达到完整启用门槛 |
| P3 | 任务内只读像素缓存，文件内容、页码、旋转、缩放、颜色空间、裁剪、批注共同决定键；不缓存 PDF 句柄或内存中已修改文档；按字节淘汰 | 默认 64 MiB；收益有限 |
| P4a | 保守逐页原生文本评估；连续 OCR 分段、有界段数；页码/字符框/来源重映射；缺页、页序异常及识别异常回退整份文档 | 试验开关关闭 |
| P4b | 区域路由、取消部分第二路 OCR、服务端批处理改造 | P4a 未通过，未推进 |

业务代码改动限于 [12 个文件清单](../../output/analysis/compare-performance/final/changed-business-files.json)。模型并发度保持原配置，历史任务和人工审核结果使用原存储；所有验证任务写入独立运行目录。

## 页面对齐组件基准

输入取自完整流程进入 `PageAlignmentStage` 前的准备后文档。冻结版本和候选版本交替执行三轮，未调用外部模型。

| 样本 | 原版本中位数（范围） | 候选中位数（范围） | 输出 |
|---|---:|---:|---|
| 55 + 54 页 | 45.824 s（45.554–45.979） | 13.989 s（13.702–13.991） | 全部一致 |
| 22 + 22 页 | 4.598 s（4.509–4.600） | 1.148 s（1.129–1.159） | 全部一致 |

长合同候选组件最大 RSS 增量 43.41 MiB，原版本 3.42 MiB；这只描述该组件采样，不是完整任务的内存上限。缓存条目上限为每类操作 50,000，渲染缓存另设字节预算；完整任务还包含 OCR JSON、图像中间结果与条款数据。

证据：[三轮原始记录](../../output/analysis/compare-performance/p1/component.json)、[组件驱动](../../backend/scripts/benchmark_page_alignment.py)。

## 质量验证

### 固定 OCR、保留正式语义匹配配置

冻结的是实施前实际工作区，包含此前尚未提交的修改，并非 `git HEAD`。基线执行真实 embedding/rerank；候选使用捕获的真实响应。未使用会关闭 semantic/rerank 的通用离线回放入口。

两轮 17 组对照均没有新增业务结果或 oracle 失败。最终对照 **17/17 完整 `diffs` 对象一致**，包括证据、质量状态、匹配诊断和差异 ID；PDF 哈希一致。

| 项目 | 结果 |
|---|---|
| 输入 | 15 份 Golden 合同 + 两组长短参考任务 |
| 正常外部响应路径 | 15/17；剩余两份存在下述原版本模型错误 |
| Golden 失败数 | 基线 55，候选 55，新增 0 |
| 既有失败分布 | `HUANGHE_HAIXI` 1、`STATE_GRID_NORTH_CHINA` 1、`SUIZHOU_CHANGYUAN` 48、`HANENG_GONGHE` 2、`TIELING_SERVICE` 2、`NUOYANG` 1 |
| 受保护基线完整性 | 实施前快照中的 28 个相关文件均未改变 |

`HUANENG_DINGBIAN`、`SUIZHOU_CHANGYUAN` 的重排请求在冻结版本重跑时出现 HTTP 400，服务返回最大上下文 2,048 token、实际至少 2,049 token。原 provider 遇失败后会关闭后续重排；并发调度使受影响请求数变化。旧捕获器未保存失败响应，回放还会产生缓存缺失。驱动将这些执行明确判为不合格，不把失败后的更短耗时作为优化收益。捕获器现已保存错误响应，并仍对 HTTP 失败返回非零退出码。

旧共享响应缓存连冻结版本自身回放也出现缺失；后续捕获按合同隔离。现有证据不能证明首次缺失的所有成因，不能将其归因为本次特征缓存或输入去重。

证据：[最终质量对照](../../output/analysis/compare-performance/final/quality.json)、[基线请求记录](../../output/analysis/compare-performance/final/baseline-corpus/http-calls.json)、[候选请求记录](../../output/analysis/compare-performance/final/candidate-corpus/http-calls.json)、[基线文件完整性](../../output/analysis/compare-performance/final/protected-baseline-integrity.json)。

### 真实 OCR 门禁与测试

`make golden-contract` 首次执行在第四份合同因 PP-Structure HTTP 502 中断，保留 [首轮日志](../../output/analysis/compare-performance/golden-live.log)，随后从头重跑完整门禁。重跑 **15 expected / 15 loaded / 15 executed / 0 skipped**；55 条失败的合同、case ID、failure type 与冻结基线完全一致，新增 0、解决 0。完整门禁仍失败。证据：[真实 Golden 质量汇总](../../output/analysis/compare-performance/final/golden-live-quality.json)、[重跑日志](../../output/analysis/compare-performance/golden-live-retry.log)。

普通后端回归 **1,717 passed / 1 deselected**（被排除的是另行真实执行的 Golden）；随后新增的 rerank 去重顺序测试也通过，该性能测试文件合计 13 项通过。缓存边界覆盖淘汰、参数隔离、文档修改、并发页对齐、模型去重索引及页面路由失败回退。新脚本、路由模块、缓存模块和对应测试的 Ruff 检查通过。证据：[后端回归日志](../../output/analysis/compare-performance/final-backend-suite.log)。

Golden 基线变更检查相对 `HEAD` 返回 `GOLDEN_BASELINE_CHANGED`，退出码 2，识别出任务开始前已有的 v1 → v3 变更。本次未设置审批绕过变量，也未修改真值。证据：[检查日志](../../output/analysis/compare-performance/golden-baseline-change-check.log)。

## 路由试验的取舍

视觉组件三轮验证的输出也全部一致，但矩阵图形模块长合同渲染仍为 110 次，中位数 10.279 → 10.317 秒；短合同 52 → 51 次，4.357 → 4.397 秒。测试期间存在其他质量检查负载，小幅时间波动不代表稳定退化或提速；渲染次数明确表明当前复用空间很小。保留有界缓存及指标，不宣称实现 30% 提速，不再扩大视觉改造。证据：[视觉组件记录](../../output/analysis/compare-performance/p3/component.json)。

服务当前 OpenAPI 暴露整文件/图片输入，未见页范围或批大小字段，因此分段由客户端生成临时 PDF；服务端排队和推理耗时仍不可获得。

长合同原文 55 页中只有 24 页通过现有保守原生评估，扫描件 54 页中为 0 页。其余原文页面分成 8 段，超过默认 4 段预算会回退。试验临时允许 8 段，长合同耗时 150.35 秒、差异 82 条，原参考执行 202.00 秒、差异 79 条。该试验同时启用了匹配试验且存在其他测试负载，耗时不能独立归因于页面路由。

新增三条待核差异涉及页眉缺失及两处 `ISO-9000` / `IS0-9000` 识别差异，尚未证明业务正确，故不启用。短合同双侧均无页面通过原生评估；混合试验耗时 47.60 秒，但也不能当作默认配置的结果。

证据：[页面资格评估](../../output/analysis/compare-performance/p4/eligibility.json)、[混合试验结果](../../output/analysis/compare-performance/p4/live-fixed/summary.json)、[服务能力](../../output/analysis/compare-performance/service-capabilities.json)。

## 配置与复现

两个 `.env.example` 已同步公开开关。当前默认值：

```dotenv
COMPARE_PAGE_ALIGNMENT_CACHE_SIZE=50000
COMPARE_PAGE_RENDER_CACHE_MB=64
MATCH_FEATURE_CACHE_ENABLED=true
MATCH_PERFORMANCE_STRATEGY=off
MATCH_DEDUPLICATE_MODEL_INPUTS=false
COMPARE_NATIVE_PAGE_MODE=off
COMPARE_NATIVE_PAGE_MAX_OCR_SEGMENTS=4
```

相似度/渲染缓存设为 `0`、特征缓存设为 `false` 可禁用相应复用；模型策略和页面路由各支持 `off|shadow|enabled`，不能将页面路由试验参数直接用作已验收默认值。

复现工具：

- [完整单任务驱动](../../backend/scripts/benchmark_compare_performance.py)：`--extraction live|saved`、`--http-mode live|replay`、`--code-root`、`--overrides`；新捕获使用独立 `--cache` 和 `--cache-by-task`。
- [质量对照](../../backend/scripts/compare_performance_results.py)：检查输入、完整差异对象和新增语义失败；HTTP 失败和缓存缺失导致非零退出码。
- [进程内队列并发驱动](../../backend/scripts/benchmark_compare_concurrency.py)：`--workers 1|2`，使用真实 OCR、embedding、rerank，保存等待时间、总吞吐与峰值 RSS。

服务端缓存状态未知。真实调用计时与保存 OCR/模型响应的回放计时分别记录，不将两者相减宣称端到端收益。P2/P4 未满足启用条件，本轮不继续其五组正式性能验收，不报告 P95 或 SLA。

## 最终运行记录

### 默认配置真实任务

以下均使用真实 OCR、embedding、rerank；时间统一取任务执行开始至结束，不包含上传时间。

| 样本 | 冻结版本成功执行 | 默认候选成功执行 | 本次观测变化 | 完整差异对象 |
|---|---:|---:|---:|---|
| 55 + 54 页 | 201.84 s | 171.78 s | 减少 14.9% | 完全一致，79 条 |
| 22 + 22 页 | 52.63 s | 50.20 s | 减少 4.6% | 完全一致，36 条 |

候选两任务顺序执行共 222.17 秒，493 次 HTTP、0 失败，进程峰值 RSS 2,142.44 MiB。长合同分段耗时为 OCR 97.42 秒、页面对齐 14.08 秒、条款匹配 23.00 秒、文档准备 11.41 秒、汇总 10.66 秒。当前 OCR 阶段本身已超过 90 秒目标，后续关键工作仍在识别服务与识别路径。

**这些是单次成功观测，不是稳定提升比例。** 本轮随后安排的冻结基线顺序重测，两任务均因 PP-Structure 502 失败，因此表中使用较早的成功基线，无法给出五组稳定成对统计。失败执行不参与速度或成功吞吐计算。服务健康接口仍返回 200，不能据此判断真实识别请求一定成功。

证据：[成功执行对照](../../output/analysis/compare-performance/final/live-reference-comparison.json)、[默认候选顺序执行](../../output/analysis/compare-performance/final/candidate-sequential/summary.json)、[失败基线请求记录](../../output/analysis/compare-performance/final/baseline-sequential/http-calls.json)。失败基线产物由旧版驱动生成，其 `tasks_per_minute` 字段按提交数计算，不能视为成功吞吐；新驱动已修正为只计算 `COMPLETED`。

### 默认双任务并发

同一进程、同一队列、两个 worker，执行上述两份真实参考任务：

| 指标 | 一个 worker 顺序执行 | 两个 worker 并发执行 |
|---|---:|---:|
| 两任务总时间 | 222.17 s | 180.88 s |
| 成功吞吐 | 0.540 任务/分钟 | 0.663 任务/分钟 |
| 进程峰值 RSS | 2,142.44 MiB | 2,209.17 MiB |
| 长任务从提交到完成 | 171.79 s | 180.75 s |
| 短任务从提交到完成 | 222.02 s（含排队） | 156.04 s |
| HTTP 结果 | 493 次，0 失败 | 493 次，0 失败 |

这一轮总吞吐提升约 22.8%，但单独执行约 50 秒的短任务在并发时等待约 156 秒：其文档解析阶段增至 116.85 秒，并出现全局解析槽位等待。保持现有并发上限，不能据此继续增大 OCR 或模型并发。

长合同完整差异对象相同。短合同唯一变化为第五条差异的第三个匹配候选中 `semantic_score` 从 68.03 变为 68.04；差异文本、证据、质量状态、最终配对和最终匹配分数均未变化。真实模型响应没有固定，短合同的严格诊断对象等价因此记为 `false`，不隐藏该差异。

渲染缓存两任务峰值分别 63.56 / 63.97 MiB，均在 64 MiB 限制内。另在同一进程中用两个独立的 55 + 54 页输入副本同时调用同一 aligner，两个输出均与冻结基线一致，页面对齐组件 RSS 增量 **73.95 MiB**，低于两任务合计 **128 MiB** 的组件预算。该预算不包含整个流水线的全部内存。

证据：[双任务运行](../../output/analysis/compare-performance/final/candidate-concurrent/summary.json)、[并发质量记录](../../output/analysis/compare-performance/final/concurrency-quality.json)、[唯一诊断差异](../../output/analysis/compare-performance/final/concurrency-diff-details.json)、[双长合同组件内存](../../output/analysis/compare-performance/p1/concurrency.json)。

## 后续优先级

1. 处理已存在的 55 条 Golden 失败与重排服务输入长度问题，再扩大匹配裁剪试验。现阶段模型失败不能作为少调用、快返回的收益。
2. 与 OCR 服务侧定位 PP-Structure 502、排队和扫描件约 97 秒的主路径；当前接口未提供足够服务端分段计时，不能仅靠本仓库参数判断推理瓶颈。
3. 页面原生路由先解决页眉与 `ISO/IS0` 新差异，再验证独立 P4a；通过后才考虑区域路由和减少双路 OCR。

本次交付为源代码、配置样例、基准工具及上述实测证据，尚未部署。
