# Optimization 4：强锚点可行上界 + 精确 DP 剪枝

## Root cause / code changed

B的原始页对齐执行6320次page/group similarity、1600次anchor similarity、236720次text ratio（均为LRU miss，即实际函数求值）。现有全空间搜索叠加anchor字符串比较占用约15秒。

候选仅位于 `anchor_bound.py`；生产PageSequenceAligner未改。通过当前源代码生成实验函数，并断言插入点唯一；不是建议直接发布的实现方式。使用唯一、有序、高置信的稳定prefix/suffix作为可行路径锚点，gap调用原DP，得到可行上界。**不强制锚点配对**，原DP全部转移及relax顺序仍存在。重复/冲突/缺失/低置信锚点FULL_DP_FALLBACK。

下界证明：原match成本≥0.05/两页，group≥0.22/三页，gap≥0.42/一页，因此剩余页数×0.025不会高估剩余成本。仅在 current+lower 严格超过可行上界及累计浮点/tie余量时剪枝。已有“唯一exact锚点不属于原最优路径”反例测试，防止将锚点误当必须经过的边。

精度前提已追到原`_similarity`：进入DP前即返回四位小数，gap回溯分数可用于重建原路径代价。回归测试保护此精度和成本下界前提。矩阵分配、外层遍历仍为O(P×Q)，本实验减少的是昂贵similarity的实际求值，未声称消除最坏情况复杂度或矩阵内存。

## Baseline / candidate / benchmark delta

真实B原输入，独立进程，baseline/candidate交替三轮，只切换PHASE2_PAGE_BOUND。该数字是CompareService流水线，**不是浏览器E2E**。

|指标（P50）|baseline|candidate|delta|
|---|---:|---:|---:|
|pipeline|109.5331s|95.2647s|−14.2685s（−13.03%）|
|PageAlignmentStage|15.0596s|0.4401s|−14.6194s（−97.08%）|
|CPU|50.8752s|36.4944s|−14.3808s（−28.27%）|
|RSS peak|605.2813MiB|646.2031MiB|+40.9219MiB（+6.76%）|
|远端请求数|41|41|0|
|远端时间并集|70.1986s|70.1814s|−0.0172s|
|events bytes|457928|457928|0|
|diff / event count|9 / 9|9 / 9|0|

pipeline三轮：110.4317/109.5331/109.3330 → 95.5978/95.1027/95.2647秒。P95 nearest-rank/max为110.4317→95.5978秒，n=3不外推生产尾延迟。

实际求值：similarity 6320→235；anchor similarity 1600→79；text ratio 236720→11611；40个strong anchors、1640个DP状态被剪、fallback=0。没有把cache hit当实际CPU函数调用减少。GC collections三代分别1718/156/12→1691/154/12；没有修改GC设置，不能仅凭这些数解释RSS增加。

## Semantics / Golden / risk

三组完整pipeline的diff、pairs（含候选与评分）、sections、events及未舍入page alignment entries逐字段完全一致。B离线重建的baseline mapping也与第一阶段保存结果一致。测试覆盖same/insert/delete/1→2/2→1/reorder/header-footer/nearly-duplicate、明确fallback、80组固定种子变换与exact-anchor反例。

完整Golden正在用未修改的15合同/255断言验证；原始baseline已有55失败，不能预写PASS。最终结果另见总报告与golden-comparison.json。

**候选保留用于验证，生产默认不启用**。B真实pipeline收益明确，RSS上升需要保留在验收中；A/C/D/E候选浏览器E2E未完成，不将B外推。实验函数需转换为可维护的普通实现并对成本下界的前提设置回归保护，才能进入发布审查；当前不把动态源码生成放入生产。

证据：`output/analysis/perf-phase2-20260921/alignment/B-stage.json`、`cpu-pipelines/alignment/numeric-summary.json`及完整baseline/candidate结果。
