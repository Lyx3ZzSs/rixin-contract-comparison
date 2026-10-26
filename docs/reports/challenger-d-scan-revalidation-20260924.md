# D 扫描合同：PP-DocLayoutV3 2.0 重验（2026-09-24）

1. **Layout 串行吗？** 旧版是；本次客户端继续使用有界并发 8。新服务默认单图每次 forward 1 页；显式开启动态合批后，实际 GPU forward 出现 batch 2/4。
2. **采用的 `layout_concurrency`：8。** 沿用上轮最佳组合，不重新搜索 1/2/4/8。
3. **Layout wall P50：**同一新服务的默认单图 **16.77s → 动态合批 16.11s**；历史冻结 Current Challenger 为 18.06s。
4. **OCR 与 Layout 并行有效吗？** 有。动态模式的 OCR/Layout HTTP 重叠 P50 为 **5.33s**；40 个 SCANNED 页提前发送全页 OCR，MIXED 依赖关系不变。
5. **D Extraction P50：69.88s → 52.78s**（相对历史 Current Challenger -24.5%；新服务默认单图为 53.23s）。
6. **D Pipeline P50：95.77s → 78.21s**（相对历史 Current Challenger -18.3%；新服务默认单图为 79.10s）。
7. **低于历史 Baseline 82.16s 吗？** 是，快 **4.8%**；Extraction 也低于历史 Baseline 60.47s。
8. **D_RESULT = FAIL。** 正式 3 次完整业务 output hash 各不相同，原严格稳定门槛仍未通过。差异仍限于既有 Matching 候选的 `semantic_score` 波动；没有放宽 hash 口径。

本报告只重验 D 的 40 页 native original + 40 页扫描 compare，不覆盖[上轮 D 报告](../archive/reports/challenger-d-scan-performance.md)或 A–E 历史数据。两种模式分别预热 1 次、正式 3 次；历史 Baseline/Current Challenger 是冻结参考值，本次没有将它们伪装为同日重测。服务端版本、模型权重与源码 SHA 在每次运行前后核对，`benchmark-freeze.json` 中 310 个冻结文件也在每次运行前后核对。

| Variant | Extraction P50 s | Pipeline P50 s | Layout wall P50 s | Full-page OCR active P50 s | Table P50 s | Render P50 s |
|---|---:|---:|---:|---:|---:|---:|
| Baseline（冻结历史） | 60.47 | 82.16 | 不适用 | 不可同口径拆分 | 不可同口径拆分 | 不可同口径拆分 |
| Current Challenger（冻结历史） | 69.88 | 95.77 | 18.06 | 33.64 | 8.98 | 6.29 |
| 上轮 C-c8（旧服务） | 52.88 | 79.32 | 16.62 | 39.01 | 8.01 | 6.35 |
| 新服务默认单图，c8 + OCR 并行 | **53.23** | **79.10** | **16.77** | 39.52 | 8.26 | 6.34 |
| 新服务动态合批，c8 + OCR 并行 | **52.78** | **78.21** | **16.11** | 38.89 | 8.97 | 6.31 |

`Layout wall` 是请求区间并集。Layout、OCR、Table 可重叠，表中阶段数字不能相加为 Extraction。相对本次同版本默认单图，动态合批 Pipeline P50 缩短 **0.89s / 1.1%**；相对上轮旧服务 C-c8 缩短 **1.11s / 1.4%**。n=3，不能把约 1 秒的增量视为已稳定证明的端到端大幅收益。相对历史 Current Challenger 的 18.3% 改善包含先前客户端并发和 OCR/Layout 并行带来的收益，并非全部归因于服务端 batch。

## 本次模式与真实 batch 证据

本次使用[独立实验副本](../../experiments/challenger_d_scan_revalidation_20260924/)；`run_one.py`、`bench_common.py` 与上轮逐字节相同。`challenger.py` 只在动态模式的原单页 `/layout-detection` 请求增加 `dynamicBatch=true`，保持每页一请求、阈值 `{6:0.4,17:0.4}` 和 `layoutNms=false`；新服务默认模式不增加该字段。客户端仍由 8 个 worker 有界调度，页面按原顺序进入后续 pipeline。服务端最大 batch=4、单模型 worker、10ms 等待窗口、最多 8 个活动 HTTP 请求和 16 个在途页面。

服务端 `/metrics` 的 **真实 Paddle runner 输入 batch 直方图增量**：

| 正式运行 | batch 1 forward | batch 2 forward | batch 4 forward | 输入页合计 |
|---|---:|---:|---:|---:|
| 默认 r1/r2/r3（每次） | 80 | 0 | 0 | 80 |
| 动态 r1 | 42 | 1 | 9 | 80 |
| 动态 r2 | 40 | 2 | 9 | 80 |
| 动态 r3 | 42 | 1 | 9 | 80 |

这证明动态模式实际执行了多图 GPU forward，而非仅多个 HTTP 请求重叠。动态模式的 Layout 请求 latency P50 为 **407ms**、正式样本最大 **926ms**；默认单图为 **462ms / 996ms**。Layout wall P50 下降约 **3.9%**，但本次 Extraction 只下降约 **0.45s**。OCR/Layout 重叠 P50 为 5.33s，默认模式为 5.45s。

## 业务输出与可靠性

两种模式的全部 8 次运行（含预热）均为：原文/扫描件条款 **248/244**、diff **24**、event **24**；页面对齐、两侧条款、diff、event 的字段哈希与冻结 D-r1 相同。40 个 SCANNED 全页 OCR、1 个 MIXED 区域 OCR、39 个 NATIVE_GOOD；Table 始终 2 页、2 个 region、1 次结构调用。OCR payload hash 与冻结参考逐页一致，每次只 render 80 页且页面顺序一致。Layout 最大在途数不超过 8，扫描 OCR 最大在途 1，总 OCR 最大在途 2。

正式 6 次运行 GPU HTTP error=0、其它 HTTP error=0、retry=0、Table error=0；每次服务端完成 80 页，没有本次新增的拒绝请求或推理错误。每次运行前后服务健康且权重/服务源码 SHA 相同；结束后 `/health` 仍为 healthy，模型 worker ready，无 fatal error。本次未重新采集整卡显存曲线，不把服务健康等同于新的显存峰值证明。部署时的显存测量见[服务端 batch 部署报告](ppdoclayoutv3-batch-deployment-20260924.md)。

**完整 output hash 门槛：**默认单图正式 3 次有 3 个不同 hash；动态合批正式 3 次也有 3 个不同 hash。逐字段递归比较冻结 D-r1，全部差异路径均为 `pairs/*/match_candidates/*/score_details/semantic_score`；这与上轮 C-c8 中的既有波动范围一致。动态合批的原始 Layout boxes 可随 batch 大小变化，但本次 D 的业务字段没有新增差异。不能据此宣称其它合同也等价，544 个 `DEFERRED_BUSINESS_REVIEW` 和 Golden expectation 继续冻结。

| Run | Extraction s | Pipeline s | full output hash |
|---|---:|---:|---|
| 默认 r0（预热） | 53.929 | 80.313 | `e7711ad5f19671ddfc170c4c46498f4ed3e8e0d0a0570749fdcd0b6b9939d33c` |
| 默认 r1 | 53.262 | 79.101 | `734cafb1d44cb19d743aa3f298969f203cfa23d89aa47080ee88bc2195f7658c` |
| 默认 r2 | 52.595 | 78.904 | `180f90760c2b08c156e34f2c9cc4b594f164a43d3ad840e7e04f3eeab67922ba` |
| 默认 r3 | 53.232 | 79.120 | `de664d3f53daafd4b1d2dad123f1e1fbd3f9b95c60317a042f8604503e8a5d24` |
| 动态 r0（预热） | 51.717 | 77.208 | `01e13ae16ebd39ec275b32fba8ca75e3bbe93d3d609d285d0c45030e224d8413` |
| 动态 r1 | 52.782 | 78.207 | `ea828ea765f76d202c229282457327bf34ae4ed7a74ca05a4183f5ec5e0977a7` |
| 动态 r2 | 52.820 | 78.762 | `180f90760c2b08c156e34f2c9cc4b594f164a43d3ad840e7e04f3eeab67922ba` |
| 动态 r3 | 52.505 | 78.153 | `03037897a700ad34e86c3e3d7ee74a22061bce92e2ea53dbe0a858e88427751a` |

原始输出与复现：`output/analysis/challenger-d-scan-revalidation-20260924/`，包含各 run 的 `result.json`、`observation.json`、`analysis.json`、`network.json`、`extraction-metrics.json`、`page-timeline.csv`、服务 health/metrics 前后快照、`validation.json`、源码身份与冻结身份。运行命令：

```sh
archive/experiments/2026-09-24/layout_decoupling/.venv/bin/python experiments/challenger_d_scan_revalidation_20260924/controller.py
```

该控制器故意拒绝覆盖现有 run；复跑时先复制实验目录，将副本中的 `OUT` 指向新的唯一输出目录，再执行。历史样本与本次观察均应保留原样。

本轮只验证 D。**D_RESULT = FAIL** 的原因是原完整输出 hash 稳定要求未满足；Pipeline 和 Extraction 耗时门槛已满足。没有修改模型、阈值、OCR、Table、ClauseSplitter、PageAlignment、Matching、Diff、Evidence 或 Golden expectation；没有继续 A–E。
