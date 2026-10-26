1. **当前 Layout 是否串行？是。** 原版 tracing 的 80 次 Layout 最大在途数为 1，请求严格不重叠；Layout 与 OCR HTTP 重叠为 0。
2. **最优 layout_concurrency：8**（Experiment A 的 Pipeline P50 最低；完整输出 hash 门槛另列，不代表可采用）。
3. **Layout wall：18.06s → 11.04s**（A 最优；组合 C 为 16.62s；本轮 c1 对照 17.62s）。
4. **OCR 与 Layout 并行是否有效？** B Pipeline P50=82.03s；组合 C=79.32s，实际 HTTP 重叠见下表。
5. **D Extraction：69.88s → 52.88s**（最快实测 C-c8，-24.3%）。
6. **D Pipeline：95.77s → 79.32s**（-17.2%）。
7. **是否低于 Baseline 82.16s？是**，相对 Baseline -3.5%；Extraction 相对 60.47s -12.6%。
8. **D_RESULT = FAIL**。速度结论不能替代完整输出稳定门槛；本轮未放宽 hash 校验。

# D 扫描合同：仅调度实验

仅测试 D：40 页 native original + 40 页扫描 compare。每个参数 warmup 1、正式 3 次，预热不纳入 P50。未继续 A–E，未修改服务端 API/模型/阈值/业务算法或 Golden；544 个 `DEFERRED_BUSINESS_REVIEW` 保持冻结，`READY_FOR_PRODUCTION=false`。

## 性能结果

| Variant | Extraction P50 | Pipeline P50 | Layout wall union | Full-page OCR active wall | Table | Render |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline（冻结历史） | 60.47 | 82.16 | 不适用 | 不可同口径拆分 | 不可同口径拆分 | 不可同口径拆分 |
| Current Challenger（冻结历史） | 69.88 | 95.77 | 18.06 | 33.64 | 8.98 | 6.29 |
| A-c1 / Current 本轮对照 | 68.69 | 94.95 | 17.62 | 33.80 | 7.61 | 6.21 |
| A-c2 | 60.75 | 86.30 | 14.06 | 33.58 | 8.04 | 6.27 |
| A-c4 | 56.94 | 84.06 | 11.44 | 33.63 | 8.40 | 6.05 |
| A-c8 | 56.16 | 82.56 | 11.04 | 34.03 | 8.10 | 6.06 |
| B-c1 | 55.58 | 82.03 | 17.60 | 35.05 | 8.01 | 6.25 |
| C-c8 | 52.88 | 79.32 | 16.62 | 39.01 | 8.01 | 6.35 |

单位秒。历史 Baseline/Current 来自冻结 GPU A–E 报告，未伪装成本轮新测；本轮 A-c1 提供同版本、同输入、同服务的现场对照。并发阶段重叠，表内各项不能相加当作 Extraction。

| Candidate | Pipeline vs Baseline | Pipeline vs frozen Current | Extraction vs Baseline | Extraction vs frozen Current |
| --- | ---: | ---: | ---: | ---: |
| A-c1 | +15.6% | -0.9% | +13.6% | -1.7% |
| A-c2 | +5.0% | -9.9% | +0.5% | -13.1% |
| A-c4 | +2.3% | -12.2% | -5.8% | -18.5% |
| A-c8 | +0.5% | -13.8% | -7.1% | -19.6% |
| B-c1 | -0.2% | -14.4% | -8.1% | -20.5% |
| C-c8 | -3.5% | -17.2% | -12.6% | -24.3% |

最快实测配置相对 Baseline 的 Pipeline 改善为 3.5%，未达到 10% 明显收益门槛。

## Experiment A：有限 Layout 并发

| Layout concurrency | Layout wall P50 s | 首发到末收 P50 s | 请求 latency P50 ms / max ms | 最大在途数 | GPU HTTP error | retry | CPU s P50 | 平均 CPU 核 | RSS MiB P50 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 17.62 | 24.61 | 223.2 / 283.9 | 1 | 0 | 0 | 57.11 | 0.60 | 3665.8 |
| 2 | 14.06 | 15.36 | 241.2 / 509.9 | 2 | 0 | 0 | 55.76 | 0.66 | 3090.9 |
| 4 | 11.44 | 11.44 | 472.3 / 662.9 | 4 | 0 | 0 | 56.57 | 0.68 | 3291.4 |
| 8 | 11.04 | 11.04 | 965.3 / 1228.2 | 8 | 0 | 0 | 56.45 | 0.68 | 3559.6 |

请求 latency 汇总每组正式运行的 240 个请求；Layout wall 为请求区间并集，首发到末收窗口含请求间空隙。服务端仍单进程、模型锁串行、batch_size=1。客户端只改变请求调度；没有新增 batch API。FIFO 队列和 worker 均限制为配置并发数，PyMuPDF 操作留在主线程，结果按 original/compare、page_index 原顺序进入后续 pipeline。

## Experiment B / C：SCANNED OCR 与 Layout 并行

| Candidate | OCR/Layout HTTP overlap P50 s | OCR HTTP wall P50 s | Full-page OCR active P50 s | Layout request P50 / max ms | Layout max inflight | OCR max inflight | CPU s P50 | RSS MiB P50 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A-c1 | 0.00 | 30.11 | 33.80 | 221.9 / 283.9 | 1 | 1 | 57.11 | 3665.8 |
| B-c1 | 4.61 | 29.87 | 35.05 | 224.8 / 305.2 | 1 | 2 | 57.57 | 3665.8 |
| C-c8 | 4.90 | 29.72 | 39.01 | 460.0 / 1091.5 | 8 | 2 | 57.32 | 3580.3 |

真实依赖：无 usable native 字符时，冻结 coverage_plan 的 SCANNED 分支只需要页面尺寸；D 的 40 张扫描页均无 native glyph。全页 OCR payload 不依赖 Layout；OCR 返回后的 visual owner、provenance 和 reading order 依赖 Layout。因此只提前发送同一全页请求，等 Layout 和原有前置语义处理完成，再用冻结代码消费响应。MIXED 的区域、mask、coverage 依赖仍原样保留。

扫描页 OCR worker=1，最多 2 个未完成扫描 OCR 任务；全页 OCR 最大在途数始终为 1。实际 tracing 发现原文侧的 1 次 MIXED 区域 OCR 可与修订侧最后一个扫描 OCR 重叠，因此总 OCR 最大在途数为 2，而不是 1。MIXED 仍在自身 Layout/coverage 完成后才发送；这是两页独立工作的额外重叠，需与纯 Layout/OCR 重叠区分。40 页全页请求 + 1 次 MIXED 区域请求保持不变。`ocr_await_consume` 是等待/消费的诊断包络，不能重复加进 OCR active wall。

**render duplication does not exist**：Layout 与 OCR 共用同一张 144 DPI PIL 页面图，每页仅一次 get_pixmap。OCR 从共享图 crop、遮盖、编码 PNG，编码不是再次 render PDF。未改变 OCR endpoint 协议。

**Table 算法未改**：每次 2 table pages、2 table regions、1 table structure call；native vector 和 model fallback 原样保留。两页的 Table 时间线记录共同 batch 区间，不虚构单页独占耗时。

## 输出、错误与严格门槛

| Candidate | clauses original/compare | diff / event | full hash unique / 3 | 与冻结 full hash 相同次数 | OCR payload 相同 | GPU HTTP error / retry | 所有 HTTP error |
| --- | --- | --- | ---: | ---: | --- | --- | --- |
| A-c1 | 248/244 | 24/24 | 2 | 1 | True | 0 / 0 | `{}` |
| A-c2 | 248/244 | 24/24 | 3 | 0 | True | 0 / 0 | `{}` |
| A-c4 | 248/244 | 24/24 | 3 | 0 | True | 0 / 0 | `{}` |
| A-c8 | 248/244 | 24/24 | 3 | 0 | True | 0 / 0 | `{}` |
| B-c1 | 248/244 | 24/24 | 3 | 1 | True | 0 / 0 | `{}` |
| C-c8 | 248/244 | 24/24 | 3 | 0 | True | 0 / 0 | `{}` |

所有候选（含预热）相对冻结 D-r1 的差异仅限 `pairs/*/match_candidates/*/score_details/semantic_score`。完整 hash 口径沿用原报告；冻结三次原有 hash 都作为可接受参照，但候选三次自身还必须只有一个完整 hash，未删掉分数字段，也未冻结/重放下游响应。原 D 三次历史 hash 已各不相同，本轮原版 tracing 也复现分数波动；这不能证明并发引入业务变化，但也不能声称满足用户的完整输出稳定要求。

各业务分项 hash 和具体差异路径保存于每次 `analysis.json`。diffs、events、page alignment、两侧 clauses 均与冻结结果逐字段一致；差异只在候选 semantic_score。

GPU 服务健康检查保留模型权重/运行库身份。A 最后一轮的 controller 在子进程完成后被会话中断；完整子进程结果原样恢复，晚采的健康响应单独标为 recovered，没有重跑或替换样本。用户随后提供 SSH 访问，核对运行容器 StartedAt 早于整个实验且 RestartCount=0、OOMKilled=false；B/C 每次运行前后都另存容器状态，并按 1 秒采样整卡显存（gpu-memory-bc.csv）。覆盖 B/C 及后续核验的整卡显存采样共 1052 次，均为 22,520 MiB / 24,564 MiB；不是单个服务的显存。A 没有连续显存采样，不声称已测其显存峰值。

服务端补充核验发现，冻结 Baseline 所用 PP-StructureV3 实际为请求内 pipeline/Layout batch=4、跨请求单工作线程串行；独立 DocLayout 仍为 batch=1、模型锁串行。该配置差异原已存在，本轮未更改。详见 [服务端 batch/并行核验](layout-server-batch-parallel-20260924.md)。服务端四页探针在 A 完成后、B 开始前执行，没有与 D 测速重叠。

CPU 为本机进程及已结束子进程 CPU 秒；平均 CPU 核数为 CPU 秒 / Pipeline wall。RSS 采用原 runner 的 100ms 进程树采样，包含结果保存阶段，短时峰值可能遗漏。n=3、共享远端服务未隔离外部负载，不外推生产 P95。用户后来要求探索 batch；一个 nice=19、单线程、禁用 CUDA 的独立 CPU batch 探针与 C 最后一次运行部分重叠，随后暂停到 C 结束，未进行 GPU batch 或更改在线服务。C 三次 Pipeline 为 79.320/79.442/79.307s，该样本原样保留；不能宣称完全隔离的服务器负载。

## 逐次观察（不删除慢样本）

| Run | Extraction s | Pipeline s | output hash |
| --- | ---: | ---: | --- |
| A-c1-r0 (warmup) | 76.293 | 102.410 | `758829baaaa7355b6aa5992bdf5b386c1d35cba463ef730964618f8567a307e1` |
| A-c1-r1 | 68.278 | 94.945 | `a035cd2ede964242242b623c7d144d65fe659a986950fb10ada9c0718661be9b` |
| A-c1-r2 | 68.694 | 94.642 | `85787e7101b8a6f5c80c7a91e357d04b6f88e12dbc5ccbdeb683da97156ca729` |
| A-c1-r3 | 68.997 | 95.239 | `a035cd2ede964242242b623c7d144d65fe659a986950fb10ada9c0718661be9b` |
| A-c2-r0 (warmup) | 60.688 | 85.987 | `734cafb1d44cb19d743aa3f298969f203cfa23d89aa47080ee88bc2195f7658c` |
| A-c2-r1 | 57.815 | 84.129 | `03037897a700ad34e86c3e3d7ee74a22061bce92e2ea53dbe0a858e88427751a` |
| A-c2-r2 | 61.023 | 88.183 | `02dcda19285b475ed169e151593deaca7f6b45463f473d8925e0f3144bd49ab8` |
| A-c2-r3 | 60.748 | 86.304 | `a035cd2ede964242242b623c7d144d65fe659a986950fb10ada9c0718661be9b` |
| A-c4-r0 (warmup) | 56.768 | 83.443 | `9aad17746e248dc2c19ec93c3049741f6e23f2b505427db51e931bf548a8905b` |
| A-c4-r1 | 56.940 | 84.055 | `16be3f781cab4832dd5c69258879c6008deb5d9fa760ba36487445bd7be97382` |
| A-c4-r2 | 57.555 | 84.477 | `2c3f40bf7aaab76e046d604a842fc590888b98c62b6367c15c9fdbad3ea0ad34` |
| A-c4-r3 | 56.687 | 83.614 | `70e18fa8106b04ae0babbca2bff062afc3a3ac0fc4aa72d0757243f7415dc792` |
| A-c8-r0 (warmup) | 57.322 | 83.218 | `180f90760c2b08c156e34f2c9cc4b594f164a43d3ad840e7e04f3eeab67922ba` |
| A-c8-r1 | 56.338 | 83.122 | `180f90760c2b08c156e34f2c9cc4b594f164a43d3ad840e7e04f3eeab67922ba` |
| A-c8-r2 | 56.159 | 82.059 | `44f6ed634c7232ce7fd161045ed4ca34c01f20cf02a583f685ae1952fc067eb8` |
| A-c8-r3 | 55.539 | 82.559 | `02dcda19285b475ed169e151593deaca7f6b45463f473d8925e0f3144bd49ab8` |
| B-c1-r0 (warmup) | 55.972 | 81.464 | `01e13ae16ebd39ec275b32fba8ca75e3bbe93d3d609d285d0c45030e224d8413` |
| B-c1-r1 | 55.551 | 82.027 | `422fbb406c0a9c7dc5ec1d2471999645ab9baf47fb4d33ba17afbc60b926cd45` |
| B-c1-r2 | 57.689 | 83.448 | `ea828ea765f76d202c229282457327bf34ae4ed7a74ca05a4183f5ec5e0977a7` |
| B-c1-r3 | 55.576 | 82.004 | `a548693066c774c77c5b5cbb3f28530b18565016b94d52b376540b120ffc432c` |
| C-c8-r0 (warmup) | 52.587 | 79.163 | `a035cd2ede964242242b623c7d144d65fe659a986950fb10ada9c0718661be9b` |
| C-c8-r1 | 52.400 | 79.320 | `03037897a700ad34e86c3e3d7ee74a22061bce92e2ea53dbe0a858e88427751a` |
| C-c8-r2 | 53.120 | 79.442 | `a548693066c774c77c5b5cbb3f28530b18565016b94d52b376540b120ffc432c` |
| C-c8-r3 | 52.879 | 79.307 | `01e13ae16ebd39ec275b32fba8ca75e3bbe93d3d609d285d0c45030e224d8413` |

## 时间线与复现

- 原版逐页 CSV：[page-timeline.csv](../../output/analysis/challenger-d-scan-performance/trace/page-timeline.csv)；图：[timeline.svg](../../output/analysis/challenger-d-scan-performance/trace/timeline.svg)。
- 组合候选时间线：[C-c8-r1 timeline.svg](../../output/analysis/challenger-d-scan-performance/runs/C-c8-r1/timeline.svg)。
- 每次原始 observation / network / stages / extraction-metrics / result / health-before / health-after 均在 `output/analysis/challenger-d-scan-performance/runs/`。
- 入口与参数：[实验 README](../../experiments/challenger_d_scan_performance/README.md)。实验源码按 phase 存于 `source-A/B/C/`，原版 tracing 存于 `serial_trace.py`。
- 业务 freeze：`bc19e5232fd3b601380e463853124c76774469dbf8c5f1efb38e347239ed61e8`。每次运行前后 verify；本轮结束再次核对通过。544 个待审、Golden 和模型/业务源码未变。
- 验证：D 的 OCR transport seam 两项测试通过；实际 traces 校验 Layout 最大在途上限、扫描 OCR 单请求及总 OCR 两请求上限、页面顺序、请求 payload hash 和结果字段。

**D_RESULT = FAIL**。完成 D 报告后停止；未自动扩展 A–E、Table 优化或服务端 batch inference。
