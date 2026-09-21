# Optimization 2：Native 配置矩阵 — 三种配置均 REVERT

## Root cause / configuration / downstream path

配置key：`COMPARE_NATIVE_FAST_PATH_MODE`、`COMPARE_NATIVE_PAGE_MODE`，值为off/shadow/enabled，默认off。ExtractionStage先尝试整文件fast path；双方接受后直接返回native提取，未命中才进入factory的PageRoutingExtractor。因此both在B上与fast命中路径相同，并非两项收益相加。

NativeFastPathEvaluator要求每页至少80字符、char box覆盖≥0.98、可疑字符率≤0.01，拒绝低文本、多栏、表格/签章线索、图像、旋转、表单、注释和复杂矢量。PageRouting逐页检查并将其余页合成连续OCR段，超过4段回退整文件。已有test_native_fast_path.py / test_comparison_performance.py。默认关闭与代码中的保守pilot定位一致，没有发现可以直接全量启用的验收证据。

## Baseline / candidate

优先在B进行配置-only验证。每个配置首轮即违反语义门槛，按要求停止，不继续三轮或其他case来追逐收益。

|配置fast/page|B extraction baseline|candidate|OCR calls baseline/candidate|layout calls baseline/candidate|结论|
|---|---:|---:|---:|---:|---|
|off/off → enabled/off|70.840s|4.631s|2/0|2/0|REVERT|
|off/off → off/enabled|71.139s|6.937s|2/0|2/0|REVERT|
|off/off → enabled/enabled|71.241s|4.885s|2/0|2/0|REVERT|

这些是**单次失败试验的阶段观测，不是三轮成功benchmark或E2E收益**。候选在发现切分改变后终止，完整pipeline/E2E、最终diff/event和Golden不可得；不能填0。提前终止没有保留下完整comparison终止记录，候选CPU/RSS峰值同样标记未取得。baseline原始CPU/RSS/远端区间保留在各自spans.jsonl。

## Why semantics did NOT remain unchanged

- 两侧页数均保持40，char_boxes数量均为70,710；这些计数相同仍不足以证明等价。
- 每侧block数1360→400。baseline有335/334个paragraph_title、25/26个doc_title；candidate全部400个block都是text，标题类型信息消失。
- 原/新版section数114/113→100/100；最长条款9,049→50,191字符。前端无关、模型尚未调用，变化已经发生在native提取到split之间。
- char_boxes完整有序列表也不相等，含分组后局部text_index等字段；不将这种不一致未经验证就称为定位更准确或更差。
- 核心根因：当前native输出的block粒度/类型与结构化OCR输出不等价，ClauseSplitter依赖这些信息。跳过OCR/layout后既改变条款划分，也制造更长的昂贵匹配输入。

## Golden / risk / Keep-Revert

**REVERT / 保持两个生产开关关闭**。没有修改native实现，没有修改Golden。语义门槛失败后不再执行该配置的live Golden或其余A/C/D/E完整流水线。

预检仍覆盖了固定A–E原文件：整文件gate接受A/B，拒绝C/D/E；D扫描侧没有原生文本，拒绝native。此预检不是D逐页模式端到端安全验收，也不是C性能结果。不能据此声称扫描件已通过全部候选配置的Golden。

Code changed：只有run_native.py实验入口，添加切分计数不一致即停止的保护；生产提取逻辑和默认配置未变。首次运行由外部guardian在发现同一条件后停止子进程，报告中CalledProcessError代表主动拒绝；具体原因保存在各candidate/early-rejection.json，不是未知服务故障。

证据：`output/analysis/perf-phase2-20260921/native/{fast,page,both}/B-1-{baseline,candidate}`，包括完整extraction对象、spans、输入校验、early-rejection。gate预检为 `native-eligibility.json`。
