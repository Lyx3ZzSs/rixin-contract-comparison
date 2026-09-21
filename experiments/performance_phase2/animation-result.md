# Optimization 1：完成动画解耦

Root cause：终态发布被固定1200ms动画延迟阻塞，events请求以终态为前提。

Code changed：hooks立即发布已确认终态，completionAnimating独立计时并随任务取消；ResultPage继续显示完成进度动画，不占据结果布局。SSE/polling/revision校验保留。

Why semantics should remain unchanged：变更仅在后端终态已确认之后影响前端状态发布时间，不改变上传、后台计算、结果字段或审核操作。

30项相关前端测试与production build通过。30次浏览器运行每次仅1次GET events；15次candidate均观测到完成动画。新增测试验证任务切换取消旧定时器、动画结束不重建PDF/结果节点。

## Benchmark（各3次，baseline/candidate交替）

|Case|E2E baseline P50|candidate P50|delta|%|completed→events baseline/candidate|
|---|---:|---:|---:|---:|---:|
|A|16.1513s|15.1603s|-0.9910s|-6.14%|1.2039/0.0026s|
|B|101.0945s|98.9446s|-2.1499s|-2.13%|1.2052/0.0032s|
|C|221.2238s|222.3311s|+1.1073s|+0.50%|1.2083/0.0044s|
|D|81.8907s|80.8178s|-1.0729s|-1.31%|1.2064/0.0024s|
|E|77.8072s|76.8410s|-0.9662s|-1.24%|1.2075/0.0063s|

n=3，P95 nearest-rank等于max，不外推生产尾延迟。

|Case|baseline P95/max|candidate P95/max|CPU baseline/candidate|RSS MiB baseline/candidate|远端次数 baseline/candidate|远端并集 baseline/candidate|events bytes|
|---|---:|---:|---:|---:|---:|---:|---:|
|A|17.1649s|15.1828s|4.347/4.350|382.250/384.594|18.000/18.000|11.796/11.758|3257|
|B|102.0012s|100.0285s|51.050/50.520|607.344/584.047|41.000/41.000|58.527/58.366|457956|
|C|226.2222s|231.3554s|83.826/84.496|879.438/879.953|120.000/119.000|172.685/174.285|41367|
|D|82.8161s|81.7782s|28.572/28.976|963.891/990.641|117.000/119.000|63.443/63.613|180556|
|E|77.8382s|85.8865s|27.787/28.030|691.766/677.359|267.000/267.000|50.258/50.187|15500259|

## Golden result / page and evidence differences

完整Golden 15合同/255断言仍有55项既有失败，未达到全绿门槛，不能声称通过。

15组持久化diff/事件/审核相关字段差分中，归一化明确由task_id生成的事件命名空间后，13组全字段一致；E-r2/r3另有11处远端rerank分数及派生分数差异，例如22.36→22.24、85.05→85.03。原始差异完整保存在animation/remote-score-differences.json，没有从strict比较中抹除。两组的diff内容、分类、差异中的条款配对、page mapping、evidence/char_boxes、事件分组和审核标识均无变化。

C/D的远端请求次数也存在波动（主要为rerank成功调用数）；因此仅归因固定约1.20秒等待被移除，不将全部E2E差值归因于本patch。C E2E P50反而增加1.1073秒，已如实列出。

milestones：每轮browser.json保留completedReceived、events request.start/bodyEnd、firstResultDOM、visibleCanvasReady、renderEnd。PDF预览在终态前已可见，visibleCanvasReady不是结果证据高亮完成时间。E2E仍使用第一阶段全部事件DOM+两个ready canvas+两帧口径。

## Risk / Keep-Revert recommendation

**KEEP此独立前端patch，暂不标记为通过全局上线验收**：固定阻塞消除，动画和请求数量已验证；没有重写业务计算。全局Golden55项失败及E远端分数波动需单独解决/复核。不得宣称每个case都获整体提速或全字段完全相同。

原始证据：output/analysis/perf-phase2-20260921/animation/{baseline,candidate,comparison.json,remote-score-differences.json}。
