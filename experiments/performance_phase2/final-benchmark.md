# 最终 A–E 对照：生产代码中保留的动画解耦

前提：双方均含OpenCV兼容修复，未启用native、页对齐剪枝、评分cutoff、跨任务cache或events v2。算法实验收益不能与下表相加。每组3次交替，数值为P50；E2E终点沿用第一阶段。

`terminal_to_renderEnd`为终态收到至全部事件DOM与两个ready canvas加两帧；`terminal_to_firstResultDOM`为首个结果节点；`terminal_to_visibleCanvasReady`可为负数，因为PDF预览在终态前已显示，该项不计算收益百分比。

|Case|指标|Before|After|Delta|Delta %|
|---|---|---:|---:|---:|---:|
|A|E2E|16.1513|15.1603|-0.9910|-6.14%|
|A|ExtractionStage|11.8798|11.8325|-0.0473|-0.40%|
|A|PageAlignmentStage|0.4355|0.4252|-0.0103|-2.37%|
|A|MatchStage|0.4552|0.4575|+0.0023|+0.50%|
|A|events_bytes|3257.0000|3257.0000|+0.0000|+0.00%|
|A|CPU|4.3466|4.3501|+0.0035|+0.08%|
|A|RSS_MiB|382.2500|384.5938|+2.3438|+0.61%|
|A|remote_calls|18.0000|18.0000|+0.0000|+0.00%|
|A|remote_union|11.7962|11.7576|-0.0385|-0.33%|
|A|terminal_to_renderEnd|2.4170|1.3297|-1.0873|-44.99%|
|A|terminal_to_firstResultDOM|1.2158|0.0132|-1.2026|-98.91%|
|A|terminal_to_visibleCanvasReady|-12.8125|-12.7891|+0.0234|—|
|B|E2E|101.0945|98.9446|-2.1499|-2.13%|
|B|ExtractionStage|59.7232|59.6433|-0.0799|-0.13%|
|B|PageAlignmentStage|14.9264|14.8394|-0.0870|-0.58%|
|B|MatchStage|12.3824|12.2094|-0.1730|-1.40%|
|B|events_bytes|457956.0000|457956.0000|+0.0000|+0.00%|
|B|CPU|51.0499|50.5202|-0.5297|-1.04%|
|B|RSS_MiB|607.3438|584.0469|-23.2969|-3.84%|
|B|remote_calls|41.0000|41.0000|+0.0000|+0.00%|
|B|remote_union|58.5271|58.3661|-0.1611|-0.28%|
|B|terminal_to_renderEnd|3.2112|1.3572|-1.8540|-57.74%|
|B|terminal_to_firstResultDOM|1.2574|0.0520|-1.2054|-95.86%|
|B|terminal_to_visibleCanvasReady|-96.8886|-96.5932|+0.2954|—|
|C|E2E|221.2238|222.3311|+1.1073|+0.50%|
|C|ExtractionStage|169.9858|171.2194|+1.2336|+0.73%|
|C|PageAlignmentStage|17.6620|17.8807|+0.2187|+1.24%|
|C|MatchStage|12.4384|12.8869|+0.4485|+3.61%|
|C|events_bytes|41367.0000|41367.0000|+0.0000|+0.00%|
|C|CPU|83.8261|84.4961|+0.6700|+0.80%|
|C|RSS_MiB|879.4375|879.9531|+0.5156|+0.06%|
|C|remote_calls|120.0000|119.0000|-1.0000|-0.83%|
|C|remote_union|172.6850|174.2846|+1.5996|+0.93%|
|C|terminal_to_renderEnd|2.5159|1.3245|-1.1914|-47.35%|
|C|terminal_to_firstResultDOM|1.2308|0.0228|-1.2080|-98.15%|
|C|terminal_to_visibleCanvasReady|-216.8685|-219.3831|-2.5146|—|
|D|E2E|81.8907|80.8178|-1.0729|-1.31%|
|D|ExtractionStage|60.3107|60.4514|+0.1407|+0.23%|
|D|PageAlignmentStage|3.9559|3.9183|-0.0376|-0.95%|
|D|MatchStage|5.8977|5.9946|+0.0969|+1.64%|
|D|events_bytes|180556.0000|180556.0000|+0.0000|+0.00%|
|D|CPU|28.5720|28.9759|+0.4039|+1.41%|
|D|RSS_MiB|963.8906|990.6406|+26.7500|+2.78%|
|D|remote_calls|117.0000|119.0000|+2.0000|+1.71%|
|D|remote_union|63.4428|63.6130|+0.1702|+0.27%|
|D|terminal_to_renderEnd|2.7310|1.6653|-1.0657|-39.02%|
|D|terminal_to_firstResultDOM|1.2442|0.0443|-1.1999|-96.44%|
|D|terminal_to_visibleCanvasReady|-77.1434|-76.7470|+0.3964|—|
|E|E2E|77.8072|76.8410|-0.9662|-1.24%|
|E|ExtractionStage|45.8287|45.6571|-0.1716|-0.37%|
|E|PageAlignmentStage|2.5362|2.6209|+0.0847|+3.34%|
|E|MatchStage|12.7946|12.7823|-0.0123|-0.10%|
|E|events_bytes|15500259.0000|15500259.0000|+0.0000|+0.00%|
|E|CPU|27.7869|28.0297|+0.2428|+0.87%|
|E|RSS_MiB|691.7656|677.3594|-14.4062|-2.08%|
|E|remote_calls|267.0000|267.0000|+0.0000|+0.00%|
|E|remote_union|50.2575|50.1866|-0.0710|-0.14%|
|E|terminal_to_renderEnd|5.2657|4.1211|-1.1446|-21.74%|
|E|terminal_to_firstResultDOM|3.8706|2.6264|-1.2442|-32.14%|
|E|terminal_to_visibleCanvasReady|-69.7657|-71.7146|-1.9489|—|

单位：时间为秒，RSS为MiB，events为bytes。C整体E2E增加已保留；其它阶段的波动不能归因于前端动画。

语义：13/15组完整持久化结果在task-scoped事件ID双射归一化后相等；E-r2/r3有11处远端rerank及派生分数差异，定位/分类/内容/事件分组/审核标识未变。原始差异在animation/remote-score-differences.json。完整Golden仍须独立查看，不以此表宣称全绿。
