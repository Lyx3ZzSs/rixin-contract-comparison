# Optimization 5：保持原始评分的 cutoff 实验

## Root cause / code changed

`candidate_scoring.py` 的 body 公式为 `max(ratio,capped_token,capped_partial)`，之后仍有身份加分、接受阈值、风险封顶、semantic/rerank等分支。原始partial是公开诊断字段，并参与后续决策；不能因为body不变就将partial填0。

`score_cutoff.py` 仅在实验中使用：从公式导出 `cutoff=max(ratio,capped_token)`，调用同一个RapidFuzz partial_ratio；cutoff未命中则再次执行原无cutoff计算，物化原始分数。不截断输入，不改变候选召回、top-k、阈值或rerank数量。

## Why semantics should remain unchanged

所有候选仍计算原来的评分组成；没有依据经验阈值删除候选。250组固定种子Unicode/空串/长短比例与cap边界测试逐字段相等。这里 `partial_score_pruned=0`：实验利用原库的内部cutoff搜索，不能称为跳过了整个expensive component。

正式B候选域为1331，logical chars_processed=2,134,490；1331次cutoff调用加61次原分数fallback，fallback另处理63,230输入字符。最初4158候选的探索运行未加载当前exact-match策略，只保留为探索记录，不用于正式收益。

## Baseline / Candidate / Benchmark delta

同一B、现有全部模型配置、独立进程，baseline/candidate交替三轮。下表是pipeline，**未测候选浏览器E2E**。

|指标P50|baseline|candidate|绝对delta|百分比delta|
|---|---:|---:|---:|---:|
|pipeline|110.1518s|110.9200s|+0.7683s|+0.70%|
|规则评分|10.7689s|5.1758s|−5.5931s|−51.94%|
|MatchStage全部|12.4095s|12.7186s|+0.3091s|+2.49%|
|CPU|51.3277s|46.2520s|−5.0757s|−9.89%|
|RSS peak|599.0938MiB|602.1406MiB|+3.0469MiB|+0.51%|
|远端次数|41|41|0|0%|
|远端时间并集|70.0893s|76.2949s|+6.2056s|+8.85%|
|events bytes|457928|457928|0|0%|
|diff / event count|9 / 9|9 / 9|0|0%|

pipeline三轮分别110.8644/110.1518/108.9876 → 111.2164/105.0909/110.9200秒。P95 nearest-rank与max为110.8644→111.2164秒，n=3不估计生产尾延迟。

前模型组件隔离实验（同样1331候选，双方均无远端provider）P50为10.7201→5.2230秒。真实pipeline的第一、三组rerank批次从1.454/1.478秒升至7.361/8.968秒，每组仍37请求；第二组为1.447→1.486秒。客户端记录证明这些等待抵消了本地CPU收益，服务端没有提供内部时序，不能推断GPU或队列原因。

## Any page/evidence difference / Golden result

三组真实B完整diff、pairs（包括全部候选与分数）、sections、events和page entries逐字段相同；离线预模型ordering及最终选中pair也完全一致。证据坐标和char_boxes没有差异。完整Golden仍在执行；初始未修改baseline为55失败，不能用B差分代替Golden。

## C-pathological

固定第一阶段病理输入，两侧133/131条，最长原文74,209字符，实际match_text均61,828字符。最长一对baseline在总进程300秒预算内未完成；candidate实际评分0.236606秒返回。baseline没有分数，因此这组**既不能计算精确收益百分比，也不能证明原始评分完全相同**。

完整pipeline另设每次300秒硬超时，baseline与candidate均超时。baseline在210.567秒提取及1.606秒准备后、candidate在210.539秒提取及1.721秒准备后，截止时均未完成PageAlignmentStage，尚未进入candidate scoring。没有同时启用页对齐剪枝绕过此前阶段，禁止将单对评分结果当作完整病理合同已解决。

## Risk / Keep-Revert recommendation

**REVERT / 不接入生产评分路径**：局部CPU收益明确，但本轮B整条pipeline P50没有改善，病理评分等价及完整Golden门槛尚未完成。保留独立实验与原始数据便于复核，不在生产保留额外复杂度。没有进入正文截断或改变语义的Long-Clause路径。

证据：`scoring-configured/B-stage.json`、`cpu-pipelines/scoring/numeric-summary.json`、`cpu-pipelines/report.json`，均位于 `output/analysis/perf-phase2-20260921/`。
