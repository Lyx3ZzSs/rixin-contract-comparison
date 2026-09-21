# Optimization 7：evidence 字典 wire pilot — REVERT 当前方案

## Root cause / consumer map

`backend/app/services/diff_events/__init__.py:event_response` 同时组装事件 diff projection 与 children，其中重复携带 evidence、char_boxes 和坐标。

|字段|真实消费者|
|---|---|
|original_evidence / compare_evidence|frontend/src/types.ts:EvidenceBox、DiffItem|
|events[].diff|ResultPage.tsx 的 diffs projection、locate → PdfDocumentViewer|
|events[].children|ResultPage.tsx 内联事件卡片明细与 locate(event, child)；没有独立 EventCard 文件|
|page_no / bbox / char_boxes|PdfDocumentViewer.tsx:getScrollTargetEvidence、getPageHighlights|
|event_id / audit_item_ids / raw_diff_ids|api_events.py:review_event → review_service；没有修改这些字段|
|原始证据与高亮类型|audit_summary.py 的审计项拆分；report_generator.py 的位置与导出|
|page_alignment|ResultPage 同步翻页和缺失侧对应页定位|

## Code changed / semantics

只增加 experiments 下的 pack/unpack、测试和观测入口，生产 API 与消费者完全未变。试点使用 wire_version=2 + evidence_by_id + 引用列表；旧 v1 保留。解码深复制每个引用，保留 v1 独立对象语义。拒绝悬空引用和hash碰撞。

单测覆盖重复引用、空数组、坐标、char_boxes、审核ID和可变对象隔离。保存的真实A–E响应各三轮交替编码，Python和浏览器都验证完整还原内容相等；未使用diff数量代替等价。

## Baseline / candidate / delta

|Case|v1 bytes|v2 bytes|delta|
|---|---:|---:|---:|
|A|3257|3294|+37|
|B|457956|432607|−25349|
|C|41367|41404|+37|
|D|180556|175218|−5338|
|E|15500259|9840018|−5660241（−36.52%）|

E 后端 build+pack+model_encode+json_encode P50：2.0273→1.9185s，−0.1088s（−5.37%）。去重新增约167ms；model encode 673→320ms，JSON encode 127→72ms。各项median不能相加替代总median。

浏览器使用同一TaskSpace、同一loopback静态服务、no-store请求，baseline/candidate交替三轮；不并行运行后端benchmark。E：

|指标|baseline P50|candidate P50|delta|
|---|---:|---:|---:|
|fetch完整body|50.4ms|34.4ms|−16.0ms|
|JSON.parse|23.4ms|14.9ms|−8.5ms|
|解引用深复制|0ms|54.7ms|+54.7ms|
|fetch→完整v1对象就绪|73.1ms|98.7ms|+25.6ms（+35.02%）|

候选最大428.1ms，包含GC波动。CDP记录heap_before/after；出现负delta，不能据此声称内存下降。验证对象的canonical hash计算在计时和heap采样之外；没有强制GC，heap不是峰值或retained-size测量。

## Golden / page-evidence / risk / recommendation

- 完整响应逐字段还原无差异，包括原文、分类、配对字段、页映射、原/新版证据、char_boxes、事件投影、审核标识。浏览器只测传输/解析/还原组件，**未声称实际ResultPage逐事件点击、审核、导出已验收**。
- 全局Golden仍有55项既有失败，未达到上线门槛。
- 这里没有click-to-result E2E，没有重新执行后台流水线，不能把response缩小36.5%称为E2E提速36.5%。后端和浏览器来自独立组件实验，也不能将两个median相减后宣称线上净收益。
- **REVERT / 不接入生产当前深复制还原方案**。保留可复现实验，不删除旧字段。不继续揉入“共享可变evidence对象”等另一变量来挽救本实验。

证据：`output/analysis/perf-phase2-20260921/event-wire/{report.json,browser.json}`；真实合同响应只保存在私有本地输出，不纳入git。
