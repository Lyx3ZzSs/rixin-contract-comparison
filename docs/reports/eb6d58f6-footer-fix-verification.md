# 页脚签注漏标修复与验证

本次修复针对 `eb6d58f6-0b44-426c-b200-9fac135b4da8` 中用户指出的页脚签注：**印刷第26页，即PDF第27页，三处新增手写签注已完整进入差异证据；PDF第26页原先遗漏的右侧签注也被覆盖。**

实际接口验证任务为 `3b088e08-60ba-4525-8488-988a8e47b426`，状态 `COMPLETED / SUCCEEDED`，无执行错误。两份输入PDF与历史任务的SHA-256完全相同。本地API已重启加载本次代码，历史任务记录未覆盖。本次没有重新调用TextIn。

## 修复内容

1. **保留版面独有的页脚候选。** Hybrid融合保留页底的 `footnote / vision_footnote / footer / page_footer` 候选，并携带真实位置及 `UNVERIFIED_LAYOUT_FOOTER` 标记。版面OCR只读出的单字不再冒充整个签注区域的完整读数。
2. **按页码或版面区域确定视觉搜索范围。** 有可靠页码位置时，搜索其下方的页脚各列；有版面页脚候选时，使用候选区域。既覆盖中、右侧签注，也避免原先91.5%固定上边界裁掉上伸笔画。缺少这些依据的页面仍采用既有兜底范围。
3. **排除边缘扫描阴影。** 在截取页脚之前清除与原图边界相连的扫描墨迹，避免截取操作把扫描边框切成“孤立手写块”。实际PDF第4、6、8页曾触发这一问题，已增加失败回归并验证排除。
4. **沿用配准与去重。** 同页OCR片段被完整视觉区域覆盖后不重复计数；两侧相同墨迹不生成差异。文字不能确认时输出“检测到页脚手写签注”，明确 `NEEDS_REVIEW`，不直接断言姓名为“郭学童”。

改动位置：

- [Hybrid融合](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/extractors/ppstructure_ocr_hybrid.py:583)
- [页脚视觉比较](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/footer_annotation_visual.py:503)
- [页脚文字候选过滤](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/app/services/header_footer_compare.py:204)
- [新增回归测试](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/backend/tests/test_footer_region_recovery.py)

## 实际接口结果

| 位置 | 差异 | 结果 |
|---|---|---|
| PDF第26页／印刷第25页 | D028，新增页脚手写签注 | 完整覆盖三处签注，右侧不再遗漏 |
| PDF第27页／印刷第26页 | D029，新增页脚手写签注 | 完整覆盖三处签注及上伸笔画 |

上述两页均只有一条覆盖签注组的差异，不再叠加同一位置的OCR差异。API `/diffs` 返回了实际坐标，前端高亮函数会将这些证据送入PDF绘制。

**识别区域与读准姓名是两个判断。** 本次确认的是原件空白、比对件新增了手写内容；三处手写的完整姓名尚未可靠识别，因此保留待核实状态。

## 数量变化及回放差异

| 执行结果 | 原始差异 | 展示事件 | 页脚原始差异 | 非页脚原始差异 |
|---|---:|---:|---:|---:|
| 此前新进程缓存回放 | 32 | 31 | 7 | 25 |
| 本轮初次缓存回放 | 78 | 77 | 53 | 25 |
| 本轮实际API新任务 | 79 | 78 | 54 | 25 |
| 补齐版面质量后的最终缓存回放 | 79 | 78 | 54 | 25 |

数量增加集中在其他页面同类页脚签注的补检。实际API中的页脚差异包含50条逐页视觉差异和4条OCR聚合差异；“54条页脚差异”并不表示每页每个签注都已完整识别。正文、印章、签署信息的25条差异，其类型、文字和所在页与此前修复回放一致，没有新增或丢失。

本轮没有修改 `textin-events-v3` 统计规则。78个展示事件包含全部79条原始差异，映射无遗漏和重复，不能将这个数量视为已经与TextIn完全一致。

实际API比初次缓存回放多的一条是跨PDF第1、3、43页的“童”。图像中确有这些新增签注。差异原因已定位：缓存回放的 `ExtractionResult` 未携带版面质量信息，使该条缺少 `LAYOUT_MISMATCH_RISK / OCR_REMEDIATION_PLANNED` 标记，随后被 `header_footer_noise` 规则过滤；实际提取器返回了这部分信息并保留该条。已修复回放脚本，使其重建并传递与实际提取器相同的版面质量报告。最终回放与实际API的79条差异在类型、文字、所在页上逐条一致，均通过23项目标验收。

## 验证证据

- 新增4个回归用例：版面独有候选保留、不可信姓名隔离、中右侧完整签注召回、相同墨迹不报差异、边缘扫描阴影排除（部分检查合并在同一用例中）。失败先复现，再修复。
- 后端相关测试：431项通过。
- 前端PDF高亮测试：19项通过。
- 目标任务验收：实际API结果的23项检查全部通过，包含原图笔画位置覆盖、同页去重、待核实状态、既有25条非页脚差异保留，以及事件映射完整性。
- 本次应用代码的回放哈希与工作区一致；缓存回放的8次局部OCR调用均命中既有同输入哈希响应。
- 生成了54组页脚原图与证据框对照，并抽查PDF第1—6、25—30、49—54页。目标页的上伸笔画和最右侧签注均位于框内。

[实际API验收明细](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/eb6d58f6-footer-fix/api-verification.json) · [接口原始差异](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/eb6d58f6-footer-fix/api-diffs.json) · [原图与证据框对照PDF](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/eb6d58f6-footer-fix/footer-evidence.pdf)

## 范围

本次完成结论限定于所报告的印刷第26页漏标及同类页脚区域召回修复，不等于全合同零漏报或所有手写姓名读准。例如，PDF第54页目前仍仅有“郭”的页脚证据，其余签注不能据本轮结果宣称已经全部覆盖；第54页是独立的页面对应问题：原件有55页，比对件有54页；页序诊断将比对件第54页标为 `UNCERTAIN / page_evidence_not_reliably_observed`。当前页脚视觉比较按同页号尝试配准，未取得该页完整证据。本次没有将这个独立问题描述为已修复。

[最终回放验收](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/eb6d58f6-footer-fix/metadata-replay-verification.json) · [回放与API一致性](/Users/liyuanxin/PyCharmMiscProject/rixin-contract-comparison/output/analysis/eb6d58f6-footer-fix/api-replay-parity.json)
