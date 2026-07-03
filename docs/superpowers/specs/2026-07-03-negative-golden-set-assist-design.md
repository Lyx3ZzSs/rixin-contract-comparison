# 负向 Golden Set 辅助标注与误报回归闭环设计方案

## 背景

当前系统已经具备负向 golden set 的基础能力：

- `expected.json` 支持 `review_status: "REJECTED"`。
- `expected.json` 支持 `should_not_match_again: true`。
- `expected.json` 支持 `false_positive_reason`。
- 质量评估会统计 `known_false_positive_regression_count`。
- 质量工作台可以对已有 expected diff 执行“标为误报”。
- 任务复盘可以展示历史任务中当前仍保留的 diff。
- Draft golden set 导出可以把真实任务快速变成可标注 case。

当前缺口是：当用户在 `actual_diffs` 或任务复盘结果中确认某条系统输出是误报时，还需要手工把它转写成 expected diff，再标为 `REJECTED + should_not_match_again`。这一步机械、重复，并且容易遗漏 `source_actual_diff_id`、`false_positive_reason` 或稳定匹配片段。

Phase 4C-3 的目标是把“人工确认误报 -> 生成负向 golden set 条目 -> 回归门禁防止再犯”接成闭环。

## 目标

第一版只做辅助标注，不做自动判定。

目标：

1. 在质量工作台的 `actual_diffs` 列表中，为每条 actual diff 提供“标为负向误报”操作。
2. 用户点击后，系统基于该 actual diff 创建一条 expected diff。
3. 新 expected diff 默认写入：
   - `review_status: "REJECTED"`
   - `should_not_match_again: true`
   - `false_positive_reason`
   - `source_actual_diff_id`
   - `diff_type`
   - `source_type`
   - `title_contains`
   - 可用时写入 `original_contains`
   - 可用时写入 `compare_contains`
4. 创建成功后刷新当前 case 详情，并在 expected diff 列表中看到该负向样本。
5. 后续质量回归如果再次命中该 rejected expected diff，应计入 `known_false_positive_regression_count`。
6. UI 明确表达：这是人工确认误报后的操作，不是系统自动判定误报。

## 非目标

本阶段不做以下能力：

- 不自动识别误报。
- 不批量标注误报。
- 不直接从 suppressed diff 自动生成负向样本。
- 不自动删除 actual diff。
- 不修改质量评估匹配算法。
- 不新增后端接口。
- 不覆盖或改写已审核的 expected diff。
- 不对合同内容做自动脱敏。

原因：负向 golden set 影响 precision 回归门禁。如果把真实差异误标为负向样本，会导致后续系统刻意压掉真实差异。因此第一版必须保持人工确认。

## 方案选择

### 方案 A：从 `actual_diffs` 一键生成负向 expected diff

在已打开的 gold case 中，用户看到 actual diff 后点击“标为负向误报”。前端调用现有 `createQualityExpectedDiff()`，生成 rejected expected diff。

优点：

- 复用现有后端 `create_expected_diff()`。
- 直接写入当前 case 的 `expected.json`。
- 与现有质量工作台标注流程一致。
- 实现风险低。

缺点：

- 需要先把任务导出为 draft golden set。
- 第一版只能处理当前 case 的 actual diff。

### 方案 B：从任务复盘列表直接生成负向样本

在任务复盘的保留 diff 列表中直接点击“标为误报”，自动找到或创建 case，再写入 expected diff。

优点：

- 更接近真实任务排查入口。
- 操作路径更短。

缺点：

- 需要处理 task 到 case 的关联、case 是否存在、是否先导出等状态。
- 容易和 Phase 4C-2 的导出流程耦合。
- 第一版复杂度更高。

### 方案 C：批量选择误报后一次性生成负向样本

允许用户多选 actual diff 或 retained diff，然后批量写入 rejected expected diffs。

优点：

- 处理大量误报更快。

缺点：

- 误点风险更高。
- 需要重复检查、撤销、冲突处理。
- 当前样本规模还不足以证明需要批量。

## 推荐方案

推荐先做方案 A。

原因：

- 它最贴合当前已有质量工作台架构。
- 不需要新增后端接口。
- 不和任务复盘、导出流程产生复杂耦合。
- 它能立即把“人工确认误报”转成可回归的负向样本。

Phase 4C-3 完成后，再根据真实标注量决定是否做方案 B 或方案 C。

## 用户流程

推荐流程：

```text
1. 打开质量工作台
2. 打开一个已有 gold case，或先从任务复盘导出 draft golden set
3. 查看 ActualDiffList
4. 人工确认某条 actual diff 是误报
5. 点击“标为负向误报”
6. 选择或输入 false_positive_reason
7. 保存后，expected diff 列表新增 REJECTED 条目
8. 运行质量评估或质量回归
9. 后续如果该误报再次出现，评估报告记录 known_false_positive_regression_count
```

第一版可以先使用默认原因：

```text
manual_false_positive
```

后续再扩展为原因下拉或自由输入。

## 前端设计

### ActualDiffList 扩展

在 `ActualDiffList` 中每条 actual diff 增加按钮：

```text
标为负向误报
```

点击后调用父组件传入的回调：

```ts
onCreateNegativeExpectedDiff(diff: QualityActualDiffSummary)
```

按钮文案应避免暗示系统自动判断，强调这是人工标注操作。

### Payload 映射

从 actual diff 生成 expected diff payload：

```ts
{
  review_status: "REJECTED",
  should_not_match_again: true,
  false_positive_reason: "manual_false_positive",
  source_actual_diff_id: diff.diff_id,
  diff_type: diff.diff_type,
  source_type: diff.source_type,
  title_contains: diff.title,
  original_contains: diff.original_snippet || "",
  compare_contains: diff.compare_snippet || "",
}
```

字段策略：

- `source_actual_diff_id` 用于追溯来源。
- `title_contains` 优先使用 actual diff 的标题。
- `original_contains` 和 `compare_contains` 使用片段字段；如果片段为空，保留空字符串。
- `false_positive_reason` 第一版固定为 `manual_false_positive`。

如果当前 `QualityActualDiffSummary` 类型缺少 `original_snippet` 或 `compare_snippet`，第一版可以只写 `title_contains`、`source_actual_diff_id`、`diff_type` 和 `source_type`。不要为了这个阶段扩大 actual diff 摘要字段，除非现有后端已经返回这些字段。

### 状态与错误处理

新增状态：

- `negativeExpectedDiffRequestIdRef`
- `negativeExpectedDiffInFlightRef`
- `isSavingNegativeExpectedDiff`

行为：

1. 如果当前没有打开 case，不允许创建。
2. 点击后禁用当前保存动作，避免重复提交。
3. 调用 `createQualityExpectedDiff(case_id, payload)`。
4. 成功后调用现有 `applyCaseDetail()` 刷新当前 case。
5. 失败时显示现有全局错误。
6. 切换 case 时取消旧请求结果，避免 stale response 覆盖当前 case。

## 后端设计

第一版不新增后端接口。

复用现有：

```python
QualityWorkbenchService.create_expected_diff(case_id, payload)
POST /api/quality/cases/{case_id}/expected-diffs
```

现有 `EXPECTED_DIFF_ALLOWED_FIELDS` 已允许：

- `review_status`
- `false_positive_reason`
- `should_not_match_again`
- `source_actual_diff_id`
- `diff_type`
- `source_type`
- `title_contains`
- `original_contains`
- `compare_contains`

如果实现时发现允许字段缺失，再补服务层测试；否则不改后端。

## 数据流

```text
用户确认 actual diff 是误报
  -> 点击“标为负向误报”
  -> 前端构造 rejected expected diff payload
  -> POST /api/quality/cases/{case_id}/expected-diffs
  -> QualityWorkbenchService.create_expected_diff()
  -> 写入 expected.json
  -> 返回最新 case detail
  -> 前端刷新 expected diff 列表
  -> 后续质量回归检查 known false positive regression
```

## 测试方案

### 前端测试

扩展 `frontend/src/pages/QualityWorkbenchPage.test.tsx`：

1. 用户点击 actual diff 的“标为负向误报”后，调用：

```ts
createQualityExpectedDiff("case-001", {
  review_status: "REJECTED",
  should_not_match_again: true,
  false_positive_reason: "manual_false_positive",
  source_actual_diff_id: "diff-001",
  diff_type: "MODIFY",
  source_type: "clause",
  title_contains: "实际日期变更",
})
```

2. 成功后 expected diff 列表出现 `REJECTED`。
3. 切换 case 后，旧请求结果不能覆盖新 case。
4. 请求失败时显示错误。

### 后端测试

如果现有测试已经覆盖 `create_expected_diff()` 写入 allowed fields，可以不新增后端测试。

如果覆盖不足，补充：

- `create_expected_diff` 允许写入 `source_actual_diff_id`。
- `create_expected_diff` 允许写入 `should_not_match_again`。
- `create_expected_diff` 允许写入 `false_positive_reason`。

### 回归测试

已有 `test_evaluate_case_reports_known_false_positive_regressions` 覆盖 rejected expected diff 命中 actual diff 后计数。

本阶段不改评估逻辑，因此只需要确认相关测试仍通过。

## 文档

更新 `docs/golden_set_regression_sop.md`：

- 解释负向 golden set 的用途。
- 增加“从 actual diff 标为负向误报”的 UI 流程。
- 强调该操作必须基于人工确认。
- 说明 `known_false_positive_regression_count` 的意义。
- 提醒不要把真实差异误标为负向样本。

## 成功标准

完成后应满足：

1. 用户可以从 actual diff 一键创建 rejected expected diff。
2. 创建的 expected diff 包含 `REJECTED + should_not_match_again + false_positive_reason`。
3. 创建成功后 expected diff 列表刷新。
4. 切换 case 或请求失败时没有 stale update。
5. 不新增后端接口。
6. 不自动判断误报。
7. 质量回归仍能统计 known false positive regression。

## 后续方向

Phase 4C-3 完成后，再考虑：

- 从任务复盘 retained diff 直接生成负向样本。
- 支持 false positive reason 下拉，例如 `ocr_noise`、`header_footer`、`signature_page`、`layout_punctuation`、`cross_page_boundary`。
- 支持批量负向标注。
- 支持导出前敏感信息检查清单。
