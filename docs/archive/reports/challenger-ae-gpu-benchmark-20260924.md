# Production-like A–E GPU Performance Benchmark · 2026-09-24

1. **GPU Challenger 整体更快**。A–E 各 case Pipeline P50 之和从 519.87s 变为 372.52s（-28.3%）。但 D 退化 16.6%，收益不覆盖所有类型。
2. **B Extraction：72.27s → 30.33s（-58.0%）**。独立 B Gate 为 72.27s → 29.25s。
3. A–E Pipeline 总体改善：28.3%；按同一组 5 个 case 的 P50 相加计算，不代表生产流量加权收益。15 次实际 Pipeline 时间合计：Baseline 1559.13s、Challenger 1114.24s。
4. 实际外部 `/ocr` 请求：Baseline 30 → Challenger 213（+610.0%；所选正式运行合计）。Challenger 提取阶段 213 次，下游额外 OCR 0 次。HTTP 粒度从整文档变成逐页/区域，不能按请求数当作 OCR forward 数。PP-Structure 服务内部 OCR 次数不可观测，不能把 `/layout-parsing` HTTP 数直接换算成 OCR 模型调用减少量。
5. Challenger **`/layout-parsing = 0`**，整个 pipeline 均断言禁用该端点。
6. 最大累计阶段是 **ExtractionStage**；Extraction 内最大互斥项是 **layout_request_wall**。详见阶段和资源表。
7. **`PERFORMANCE_RESULT = MIXED`**。
8. **`READY_FOR_PRODUCTION_INTEGRATION=false`**，不建议全类型集成；Native/长合同的收益值得后续限定范围 Shadow，扫描路径先解决性能退化。本轮未执行集成。

**`READY_FOR_PRODUCTION=false`**。544 个 `DEFERRED_BUSINESS_REVIEW` 原样冻结，未裁决、未修改 Golden expectation，未修改或发布生产流程。

样本：A=8+8 页合成 Native；B=40+40 页合成 Native；C=119+120 页真实合同拼接；D=40 页真实 Native 与 40 页扫描修订；E=40+25 页不同结构真实合同。

## 核心性能表

| Case | Baseline Pipeline P50 | Challenger Pipeline P50 | Δ | Baseline Extraction P50 | Challenger Extraction P50 | Δ |
| ---- | --------------------: | ----------------------: | -: | ----------------------: | ------------------------: | -: |
| A | 16.96 | 8.19 | -51.7% | 14.48 | 5.93 | -59.0% |
| B | 113.66 | 73.78 | -35.1% | 72.27 | 30.33 | -58.0% |
| C | 231.14 | 140.52 | -39.2% | 174.76 | 81.47 | -53.4% |
| D | 82.16 | 95.77 | +16.6% | 60.47 | 69.88 | +15.6% |
| E | 75.93 | 54.26 | -28.5% | 46.19 | 26.27 | -43.1% |

**退化类型：D：Pipeline +16.6%。总体收益不能掩盖这些类型的损失。**

D 的 Challenger 提取 P50 成本：全页 OCR 33.64s、GPU Layout 请求 18.06s、表格处理 8.98s、渲染 6.29s。扫描页均只 OCR 一次；现有单页顺序请求和本机表格路径的组合仍可能比 Baseline 慢，本轮没有引入 batching 或改动表格算法。

单位秒；Δ=(Challenger/Baseline−1)，负数代表更快。Pipeline 是真实 backend compare 调用 wall time，包含正式业务持久化；不含 API 上传、浏览器渲染和 Benchmark 最终 JSON 报告写盘。双方统一关闭额外诊断写盘，没有改 Baseline OCR/Layout 参数或业务 pipeline。

## 路由与请求

| Case | Native pages | Mixed pages | Scanned pages | V3 calls | Full OCR pages | Region OCR | PP-Structure calls |
| ---- | -----------: | ----------: | ------------: | -------: | -------------: | ---------: | -----------------: |
| A | 16 | 0 | 0 | 16 | 0 | 0 | 0 |
| B | 80 | 0 | 0 | 80 | 0 | 0 | 0 |
| C | 235 | 4 | 0 | 239 | 0 | 4 | 0 |
| D | 39 | 1 | 40 | 80 | 40 | 1 | 0 |
| E | 39 | 26 | 0 | 65 | 0 | 26 | 0 |

本表为每次合同对的提取阶段计数；若重复间有变化以 `/` 展示，未将下游 cover/signing/evidence 局部 OCR 混入 Selective OCR。每页一次 GPU 请求，未开启 batching。全页 OCR 仅限 SCANNED、每页一次；NATIVE_GOOD 无提取 OCR，MIXED 仅覆盖计划要求的区域。

| Case | Baseline `/ocr` | Challenger `/ocr` | Baseline `/layout-parsing` | Challenger `/layout-parsing` |
| ---- | --------------: | ----------------: | ------------------------: | --------------------------: |
| A | 2 | 0 | 2 | 0 |
| B | 2 | 0 | 2 | 0 |
| C | 2 | 4 | 2 | 0 |
| D | 2 | 41 | 2 | 0 |
| E | 2 | 26 | 2 | 0 |

| Case | pages | table_pages | table_regions | table_structure_calls | artifact builds | artifact serialize / deserialize |
| ---- | ----: | ----------: | ------------: | --------------------: | --------------: | -------------------------------: |
| A | 16 | 0 | 0 | 0 | 16 | 0 / 0 |
| B | 80 | 0 | 0 | 0 | 80 | 0 / 0 |
| C | 239 | 46 | 46 | 4 | 239 | 0 / 0 |
| D | 80 | 2 | 2 | 1 | 80 | 0 / 0 |
| E | 65 | 3 | 3 | 0 | 65 | 0 / 0 |

上表为每次合同对的 P50；table_structure_calls 是实际模型结构调用次数，native vector 表格不会调用结构模型。HTTP 表为整个 Pipeline 的请求数 P50；Baseline `/layout-parsing` 可能是整文档请求，不能与逐页 GPU 请求数直接当作模型次数对比。

## B Gate 与冻结输出

B_GATE_PASS=true；STOP_AE=false。双方先各预热一次，然后 Baseline / Challenger 交替三轮；只有 Extraction 与 Pipeline P50 均下降且 Challenger 输出稳定才进入矩阵。完整矩阵重新计数 30 次，未复用 Gate 或旧 CPU 数据。

| Repeat | Variant | Pipeline s | Extraction s | Events | Clauses original/compare | Diffs | Output hash |
| ------ | ------- | ---------: | -----------: | -----: | ----------------------: | ----: | ----------- |
| 1 | baseline | 112.60 | 71.82 | 9 | 114/113 | 9 | `5a210e8d896b0b274ab2970d19afdb148f7e3309dfd9b1c175dc62fcc5b19525` |
| 1 | challenger | 70.64 | 29.25 | 0 | 139/139 | 0 | `46926033749742d83b89b2ef522138def29649c8c5b72ce10b352d95140576fe` |
| 2 | baseline | 113.33 | 72.32 | 9 | 114/113 | 9 | `5a210e8d896b0b274ab2970d19afdb148f7e3309dfd9b1c175dc62fcc5b19525` |
| 2 | challenger | 72.13 | 28.79 | 0 | 139/139 | 0 | `46926033749742d83b89b2ef522138def29649c8c5b72ce10b352d95140576fe` |
| 3 | baseline | 113.84 | 72.27 | 9 | 114/113 | 9 | `5a210e8d896b0b274ab2970d19afdb148f7e3309dfd9b1c175dc62fcc5b19525` |
| 3 | challenger | 73.67 | 30.31 | 0 | 139/139 | 0 | `46926033749742d83b89b2ef522138def29649c8c5b72ce10b352d95140576fe` |

Output hash 覆盖 diffs、events、page alignment、pairs、clauses、warnings/errors；排除任务标识、运行时间和 page alignment performance 统计，完整原始结果另存。双方进程固定 PYTHONHASHSEED=0，避免集合迭代顺序制造虚假差异。B 的 Baseline 9 / Challenger 0 event 仍为 DEFERRED_BUSINESS_REVIEW；这不是业务通过证据，也不是按 event 数折算的加速。

## B Extraction 互斥拆分

| 阶段 | B Gate Challenger P50 s |
| ---- | --------------------: |
| pdf_native_read | 0.358833 |
| page_render | 3.178832 |
| layout_request_wall | 14.697046 |
| layout_response_parse | 0.008267 |
| native_layout_merge | 2.173620 |
| semantic_resolver | 8.630691 |
| coverage_analysis | 0.043872 |
| ocr_full_page | 0.000000 |
| ocr_region | 0.037056 |
| table_structure | 0.000000 |
| artifact_build | 0.631918 |
| serialization | 0.064779 |
| other | 0.066141 |
| extraction_total | 29.244549 |
| layout_server_reported_time（嵌套，不相加） | 12.020351 |

| B Gate repeat | 互斥阶段合计 s | extraction_total s | 差值 s |
| ------------- | -------------: | -----------------: | -----: |
| 1 | 29.244549 | 29.244549 | 0.000000000 |
| 2 | 28.791980 | 28.791980 | 0.000000000 |
| 3 | 30.308717 | 30.308717 | 0.000000000 |

B Gate 提取之后的剩余 Pipeline P50：Baseline 41.02s、Challenger 43.33s；提取收益单独测量，没有按 event=0 推导加速。

每次互斥项之和与该次 extraction_total 的误差小于 0.00001 秒。表中各项独立 P50 的和不要求恰好等于总时长 P50；原始逐次明细可精确对账。server reported time 是 layout_request_wall 内部指标，不能重复相加。服务 inferenceSeconds 包括模型 predict 与 boxes 整理，不是纯 GPU kernel 时间。NativeHeadingRepair 和 canonical repair 保持原函数，计入 semantic_resolver；artifact_build 计入最终 Page/Document 适配，首次 canonical artifact 建立随 native_layout_merge 计时。

## GPU Layout 请求与资源

| Case | V3 requests（3次合计） | pages | 请求 P50 ms | max ms | HTTP errors | retries | response bytes |
| ---- | -------------------: | ----: | ----------: | -----: | ----------: | ------: | -------------: |
| A | 48 | 48 | 178.22 | 211.54 | 0 | 0 | 212931 |
| B | 240 | 240 | 186.42 | 258.70 | 0 | 0 | 862684 |
| C | 717 | 717 | 192.90 | 315.84 | 0 | 0 | 1768408 |
| D | 240 | 240 | 220.69 | 384.47 | 0 | 0 | 639624 |
| E | 195 | 195 | 210.53 | 256.46 | 0 | 0 | 536434 |

| Case | Variant | CPU s P50 | 平均CPU核占用 | Peak RSS MiB P50 | Network wait union s P50 | OCR wait union s P50 | GPU request wall s P50 |
| ---- | ------- | --------: | ------------: | ---------------: | ----------------------: | ------------------: | --------------------: |
| A | baseline | 6.60 | 0.39 | 598.9 | 14.09 | 9.37 | 0.00 |
| A | challenger | 7.42 | 0.90 | 782.8 | 3.04 | 0.00 | 2.83 |
| B | baseline | 63.10 | 0.56 | 1137.3 | 70.00 | 46.38 | 0.00 |
| B | challenger | 67.25 | 0.91 | 1766.3 | 17.80 | 0.00 | 14.79 |
| C | baseline | 112.98 | 0.49 | 1391.5 | 175.74 | 131.91 | 0.00 |
| C | challenger | 119.04 | 0.87 | 2901.0 | 55.26 | 0.64 | 45.45 |
| D | baseline | 39.24 | 0.48 | 1301.0 | 63.50 | 49.69 | 0.00 |
| D | challenger | 57.55 | 0.59 | 3090.9 | 55.29 | 29.88 | 18.06 |
| E | baseline | 36.29 | 0.48 | 1007.5 | 52.81 | 34.49 | 0.00 |
| E | challenger | 39.74 | 0.73 | 1995.2 | 22.40 | 1.20 | 13.75 |

| Case | Baseline `/layout-parsing` wall union P50 s | Challenger `/layout-parsing` wall union P50 s |
| ---- | ----------------------------------------: | ------------------------------------------: |
| A | 13.81 | 0.00 |
| B | 68.55 | 0.00 |
| C | 170.09 | 0.00 |
| D | 59.42 | 0.00 |
| E | 45.06 | 0.00 |

CPU 为本机进程 CPU 秒（含已结束子进程），RSS 为 100ms 采样的进程树峰值，采样持续到最终结果保存完成，包含 Benchmark 最终结果序列化的内存，因此是观测进程峰值，未独立隔离纯 Pipeline 峰值；短时峰值可能遗漏。Network/OCR wait 是 HTTP 区间并集，可能与本机计算重叠，不能加到 Pipeline 上；GPU request wall 包含远端服务处理与传输。`request wall − server totalSeconds` 仅是客户端/传输开销估计，不能分离纯网络传输。本轮 Challenger 各 case 的本机 CPU 秒均高于 Baseline，观测进程 RSS 也更高，不能宣称 CPU/内存节省。Layout 工作转移到了 RTX 4090 服务；远端 GPU 利用率/能耗未采集，不声明系统总体算力下降。

## Artifact、诊断及业务冻结

- BENCHMARK_DIAGNOSTICS=off；禁用 raw proposal JSON、逐函数 profile、重复 adapter JSON、逐页 before/after、OCR crop/response dump 和实验截图；保留业务 provenance、错误、阶段计时、计数及最终结果。
- 每页 canonical Artifact 构建 1 次，提取链 artifact_serialize_count=0、artifact_deserialize_count=0；同一对象供 OCR、表格和最终 adapter 使用。冻结 SemanticRoleResolver 内部所需 Page 视图仍保留，没有声称所有临时对象构建均消失。
- Table preparation / structure 只对 V3 检出表格的页执行；无表页不加载表格模型。native vector 与模型 TableCellGraph 两条路径通过冻结产物的逐字段 IO 适配一致性检查，table fallback 未改。
- local_layout_model_invocation=0；本机 PaddleOCR LayoutDetection 不允许加载，表格模型边界拦截本地 layout/text detector/recognizer。GPU 端点确认 model=PP-DocLayoutV3、device=gpu:0，权重 hash 与冻结本地模型一致；运行库与 CPU 历史轮次不同，不宣称语义等价。
- GPU API 不返回原始 proposal tensor；raw_network_order 字段保留为 null。官方 polygon、bbox、score、order 和完整结果序列均保留，业务 reading order 规则未改。
- Baseline 使用冻结生产 CompareService 的 `/ocr` 与 `/layout-parsing`；两侧统一只禁用诊断 writer，正式业务及结果持久化保留；没有修改 title threshold、SemanticRoleResolver、NativeHeadingRepair、coverage/OCR routing、table fallback、ClauseSplitter、PageAlignment、Matching、Diff、Evidence 或 Golden。

## 稳定性与范围

| Case | Baseline Pipeline min–max s | Challenger Pipeline min–max s | Challenger output hash unique |
| ---- | --------------------------: | ----------------------------: | ----------------------------: |
| A | 16.82–17.03 | 8.19–8.21 | 1 |
| B | 112.21–113.91 | 72.39–73.93 | 1 |
| C | 230.35–233.01 | 136.76–140.72 | 3 |
| D | 82.00–82.54 | 95.50–98.16 | 3 |
| E | 75.45–75.93 | 53.50–54.37 | 3 |

共享远端服务错误（全部保留，未按结果筛除慢样本；业务使用冻结的原有降级逻辑）：

| Run | HTTP errors |
| --- | ----------- |
| matrix/B-r1-baseline | `{"/rerank:429": 5}` |
| matrix/C-r1-baseline | `{"/rerank:400": 4}` |
| matrix/C-r2-baseline | `{"/rerank:400": 4}` |
| matrix/C-r3-baseline | `{"/rerank:400": 4}` |
| matrix/D-r1-baseline | `{"/rerank:400": 4}` |
| matrix/D-r2-baseline | `{"/rerank:400": 4}` |
| matrix/D-r3-baseline | `{"/rerank:400": 4}` |

B 首轮 Baseline 的 `/rerank` 429 已确认触发降级并改变 matching 分数；C/D Baseline 另有 400，均保留现有降级行为。它们会影响 Pipeline，未改变提取阶段计时。C/D/E Challenger 没有 HTTP 错误，但三次完整输出 hash 仍各不相同，不能将这些变化归因于已观察到的 Baseline 限流；本轮未裁决或修复业务差异。GPU Layout 的错误/重试单独统计。

以下仅作无 HTTP 错误观察的敏感性核对，**不替代主表，也不删除任何正式观察**：

| Case | Baseline clean n / Pipeline P50 s | Challenger clean n / Pipeline P50 s |
| ---- | -------------------------------: | ---------------------------------: |
| A | 3 / 16.96 | 3 / 8.19 |
| B | 2 / 113.79 | 3 / 73.78 |
| C | 0 / 无此类观察 | 3 / 140.52 |
| D | 0 / 无此类观察 | 3 / 95.77 |
| E | 3 / 75.93 | 3 / 54.26 |

Challenger 正式观察的阶段累计（同一 case 三次均计入，用于定位本次工作负载成本）：

| Stage | 累计秒 |
| ----- | -----: |
| ExtractionStage | 641.98 |
| MatchStage | 189.08 |
| PageAlignmentStage | 136.24 |
| PreClauseDiffStage | 36.94 |
| SummaryStage | 36.57 |
| SigningRegionStage | 30.49 |
| DocumentPreparationStage | 22.37 |
| SplitStage | 10.12 |
| DiffQualityStage | 3.99 |
| EvidenceStage | 2.79 |
| ClauseDiffStage | 2.19 |
| DocumentUnderstandingStage | 0.52 |
| OcrQualityStage | 0.04 |
| ModelRoutingStage | 0.01 |
| OcrRemediationStage | 0.01 |
| VisualizationStage | 0.00 |

样本 n=3，不能据此估计生产 P95。PASS 的报告判据为完整矩阵总体下降、至少 4/5 case 下降且无 case 超过 10% 退化；MIXED 表示类型间收益不一致或矩阵未完整，FAIL 表示 B Gate 失败或整体无优势。未将单页 Layout 倍数外推成整份合同倍数。共享 GPU 服务没有在本轮引入 batching；服务外部负载未锁定，波动保留。

## 证据与复现

- 数据：`output/analysis/challenger-ae-gpu-benchmark-20260924/`，含 observations、b-gate、gpu-health、gpu-openapi、逐次 network/stages/extraction-metrics/result。
- Freeze ID：`bc19e5232fd3b601380e463853124c76774469dbf8c5f1efb38e347239ed61e8`；执行前后核对业务源码、PDF、review/Golden 与配置 hash，凭证不写入报告。
- 入口：`experiments/layout_decoupling/.venv/bin/python experiments/challenger_ae_gpu_benchmark/controller.py`；脚本拒绝覆盖同名观察。
- 旧 CPU 轮次完整保留，标记文件为 `output/analysis/challenger-ae-benchmark-20260923/NOT_PRODUCTION_REPRESENTATIVE.json`；72.38s / 293.06s 不能作为本轮收益基线。
- `preflight/` 是准备、适配验证及已排除运行，不计入正式 Gate 或 30 次矩阵。
- table execution errors：0。本轮结束后停止，不更改生产路由。
