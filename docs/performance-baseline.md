# 合同对比性能基线与 P0 采集说明

## 目标

P0 只增加性能观测与基准工具，不改变对比结果、路由或任务状态语义。

当前冷启动单任务目标：

- 两份 PDF 合计 30 页：P95 不超过 45 秒。
- 两份 PDF 合计 100 页：P95 不超过 120 秒。
- 关键字段、表格、签章不得新增漏报。
- 总体 Precision、Recall、F1 的下降均不得超过 0.5 个百分点。

## 任务指标

完成或失败的对比任务继续使用原有 `metrics` 字段。细粒度数据位于：

```text
metrics.performance.extraction
metrics.performance.matching
```

解析指标包含：

- 原版、新版各自的文件大小、页数、提取器和总耗时；
- PP-OCRv5、PP-Structure 的请求体构建、HTTP、JSON 解码、结果转换和落盘耗时；
- HTTP 请求数、失败数、响应大小、远端页数；
- 混合文档合并、标题修复、重复覆盖过滤和 profile 刷新耗时。

匹配指标包含：

- 原版、新版条款数和候选数；
- 候选生成、全局分配和结构漂移修复耗时；
- 语义召回条款数、embedding 批次及 HTTP 请求数；
- rerank 组数、文档数及 HTTP 请求数；
- 匹配调试产物写入耗时。

这些字段属于诊断信息，不改变 `/api/compare/*` 的请求或响应结构。

## OCR 并发基准

基准脚本不会创建对比任务，也不会保存 OCR 原始结果。它会直接调用当前环境配置的 OCR 服务。
不要使用未经批准的敏感合同作为基准输入。

分别测试 PP-OCRv5：

```bash
cd backend
python scripts/benchmark_ocr_concurrency.py \
  --extractor ppocrv5 \
  --pdf /path/to/original.pdf \
  --pdf /path/to/compare.pdf \
  --concurrency 1 2 4 \
  --repetitions 3 \
  --output ../tmp/performance/ppocrv5-concurrency.json
```

分别测试 PP-Structure：

```bash
python scripts/benchmark_ocr_concurrency.py \
  --extractor ppstructure \
  --pdf /path/to/original.pdf \
  --pdf /path/to/compare.pdf \
  --concurrency 1 2 4 \
  --repetitions 3 \
  --output ../tmp/performance/ppstructure-concurrency.json
```

`ppstructure_ocr_hybrid` 可用于观察完整提取器，但是否并行原版和新版，应主要依据两个独立服务的结果。

## 测试方法

1. 使用 10、30、100 页三档脱敏合同，每档至少三对。
2. 每个并发档位至少重复三次，正式 P95 建议累计至少 20 次。
3. 冷启动测试不使用提取缓存。
4. 测试期间记录 OCR 服务 GPU、CPU、显存、队列长度、超时和错误。
5. 1、2、4 路测试使用相同输入集合，避免把合同复杂度差异误判为并发影响。
6. 性能测试之后运行质量回归门禁。

## 两路并发启用条件

同时满足以下条件，P1 才能启用“原版与新版两路并行”：

- 并发 2 的总墙钟时间比并发 1 至少降低 25%；
- 单请求 P95 增幅不超过 20%；
- 无新增超时、远端错误、显存溢出或结果差异；
- 并发 4 不作为默认值，只用于确认服务饱和点；
- 两个后台任务同时运行时，服务仍能稳定完成。

如果并发 2 未通过，保留串行 OCR，后续依靠严格的原生文本快速路径达到冷启动目标。

## P1 灰度能力

P1 提供两项不改变匹配算法权重和输出结构的优化：

- 原版、新版使用两个独立解析器实例并行处理，底层 OCR HTTP 连接池仍复用；
- embedding 在首次语义召回时一次性预批量处理双方条款，rerank 在单次匹配内复用
  HTTP keep-alive 连接。

双文档并行默认关闭。OCR 两路并发满足上面的启用条件后，设置：

```bash
COMPARE_PARALLEL_EXTRACTION_ENABLED=true
COMPARE_PARALLEL_EXTRACTION_MAX_INFLIGHT=2
```

开启后可通过 `metrics.performance.extraction.mode` 确认实际模式：

- `parallel`：已使用两个独立解析器实例；
- `serial`：保持原有顺序处理。

如果调用方注入了单个解析器且没有提供实例工厂，或者第二个解析器实例构建失败，
系统会自动保持串行处理，并记录 `parallel_fallback_reason`。任一实际 OCR 请求失败时仍按
原有失败语义结束任务，不重复发送请求。进程级信号量会把多个后台任务合计的在途文档
解析数限制在 `COMPARE_PARALLEL_EXTRACTION_MAX_INFLIGHT`，各侧等待时间记录在
`queue_wait_duration_seconds`。

## P2 严格原生 PDF 快速路径

P2 在结构化 OCR 前增加双侧原生文本门禁。只有原版和新版同时满足全部条件时，
才直接使用 PyMuPDF 结果；任一侧不满足时，两侧仍执行原有结构化 OCR，避免产生
一侧原生、一侧 OCR 的非对称结果。

门禁采用失败关闭策略，拒绝以下文档：

- 任一页面文本少于配置阈值，或字符坐标覆盖不足；
- 存在乱码、私有区字符、旋转页或交互式表单；
- 包含嵌入图片、表格网格、明显表头/数值单元格；
- 存在多栏排版或签字、盖章、签章区域关键词；
- PDF 特征检查失败。

先使用影子模式，只计算资格但不改变解析结果：

```bash
COMPARE_NATIVE_FAST_PATH_MODE=shadow
```

观察 `metrics.performance.extraction.native_fast_path`。当脱敏样本确认没有关键字段、
表格或签章漏报后，再灰度启用：

```bash
COMPARE_NATIVE_FAST_PATH_MODE=enabled
```

也可以在不创建任务、不调用 OCR 的情况下离线评估脱敏语料：

```bash
cd backend
python scripts/benchmark_native_fast_path.py \
  --pair /path/to/case-1-original.pdf /path/to/case-1-compare.pdf \
  --pair /path/to/case-2-original.pdf /path/to/case-2-compare.pdf \
  --output ../tmp/performance/native-fast-path.json
```

`decision` 可能为：

- `accepted`：双侧命中并使用原生快速路径；
- `rejected`：至少一侧未通过，已使用结构化 OCR；
- `shadow_eligible` / `shadow_rejected`：影子判断结果；
- `evaluation_failed`：检查异常，已安全回退；
- `not_evaluated`：功能关闭。

## P3 混合 OCR 请求优化

当前本地 shadow 语料的严格原生快速路径命中率为 0%，因此 P3 继续优化所有任务都会经过的
`ppstructure_ocr_hybrid`。

混合解析原先会为 PP-OCRv5 和 PP-Structure 分别读取同一份 PDF 并执行 Base64 编码。
P3 改为只读盘、编码一次，并让两个组件共享同一个不可变字符串。该路径无需开关，保持
请求字段、解析、回退和合并结果不变，同时减少大文件的重复 CPU、读盘和瞬时内存分配。
对本地 29,132,387 字节 PDF 执行 8 轮热缓存微基准，重复读盘/编码中位数为
0.082538 秒，共享编码为 0.040268 秒，请求准备时间减少 51.21%。该收益只覆盖客户端
请求准备阶段，不应等同于端到端耗时降幅。

可以通过以下指标确认共享载荷生效：

```text
metrics.performance.extraction.sides.*.details.counters.shared_request_payload_supported
metrics.performance.extraction.sides.*.details.counters.shared_request_payload_used
metrics.performance.extraction.sides.*.details.counters.shared_encoded_file_chars
metrics.performance.extraction.sides.*.details.operations.shared_request_file_encode
metrics.performance.extraction.sides.*.details.components.*.counters.shared_encoded_file_used
```

P3 同时实现了 OCR 与结构服务的组件级并发能力。原有顺序是同一份 PDF 先完成
PP-OCRv5，再开始 PP-Structure；如果两个服务具有独立算力，并发可将组件墙钟时间从
两者耗时之和降低到接近两者较大值。

灰度开关默认关闭：

```bash
HYBRID_COMPONENT_PARALLEL_ENABLED=true
```

这项优化保持以下语义：

- OCR 失败仍优先报告 OCR 错误；
- PP-Structure 失败且严格结构模式开启时，任务仍失败；
- 非严格模式下，PP-Structure 失败仍回退 OCR-only；
- 合并顺序、警告顺序和最终文档保持不变；
- 原始 OCR 与结构结果使用不同产物路径，可以并发写入。

通过以下指标确认实际运行方式：

```text
metrics.performance.extraction.sides.*.details.counters.component_parallel_requested
metrics.performance.extraction.sides.*.details.counters.component_parallel_used
metrics.performance.extraction.sides.*.details.operations.component_extraction
metrics.performance.extraction.sides.*.details.operations.ppocrv5_component
metrics.performance.extraction.sides.*.details.operations.ppstructure_component
```

如果同时启用 P1 双文档并行，每个 OCR 服务最多会接收
`COMPARE_PARALLEL_EXTRACTION_MAX_INFLIGHT` 个并发请求。正式启用前仍应执行 1/2/4 路
OCR 服务压测，并确认错误率、单请求 P95 和结果内容不回退。

2026-07-24 使用当前本地最小完整任务进行 4+4 页远端 A/B：串行为 15.999 秒，
组件并行为 18.128 秒，性能下降 13.31%，两侧文档输出哈希保持一致。当前 OCR 与结构
端点存在共享算力竞争，因此并发开关维持关闭；在服务拆分到独立算力前不得默认启用。

## P0 基线报告

汇总已有对比任务：

```bash
cd backend
python scripts/summarize_compare_performance.py \
  --tasks-dir ../storage/tasks \
  --output ../tmp/performance/compare-baseline.json
```

汇总器会跳过历史字段抽取任务、缺少流水线指标的任务以及无法读取的损坏文件，不会修改或删除存量数据。

2026-07-24 对现有 `storage/tasks` 的只读汇总结果：

| 合计页数 | 样本数 | P50 | P95 | 平均耗时 |
| --- | ---: | ---: | ---: | ---: |
| 0–30 页 | 8 | 52.11 秒 | 78.03 秒 | 54.91 秒 |
| 31–100 页 | 2 | 95.96 秒 | 196.34 秒 | 146.15 秒 |
| 101 页以上 | 1 | 245.55 秒 | 245.55 秒 | 245.55 秒 |
| 全部 | 11 | 69.48 秒 | 245.55 秒 | 88.83 秒 |

这些历史样本中，解析阶段平均占总耗时 76.75%，匹配阶段平均占 17.54%。
样本量较小，尤其是 31 页以上分档，因此这里只用于定位热点，不能直接作为正式 SLO
验收结果。历史任务是在细粒度采集上线前生成的，所以 embedding、rerank 和 OCR HTTP
请求数为零；新任务完成后才会写入这些计数。

每轮基线应记录：

- Git commit 和 dirty 状态；
- 后端、OCR 服务和模型服务版本；
- 输入页数与文件大小；
- 任务 `metrics.performance`；
- OCR 并发报告；
- 质量回归结果；
- 是否满足进入 P1 的条件。

真实合同生成的指标和报告只保存在受控环境，不提交到版本库。
