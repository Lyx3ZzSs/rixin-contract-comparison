# 任务复盘到 Draft Golden Set 可视化导出设计方案

## 背景

当前系统已经具备三段能力：

1. 合同对比任务会在 `storage/tasks/<task_id>/task.json` 中保存实际输出。
2. 质量工作台可以查看 golden set case、编辑 `expected.json`、运行质量评估和质量回归。
3. 任务级误报复盘可以读取历史任务，并用当前质量过滤逻辑展示“保留 diff”和“被抑制 diff”。

但从真实任务沉淀 golden set 仍然需要工程人员手动执行脚本：

```bash
cd backend
python scripts/export_ocr_compare_gold_case.py \
  ../storage/tasks/<task_id> \
  tests/fixtures/ocr_compare_cases/<case_id>
```

这一步容易出现路径、case id、force 覆盖、导出后未刷新 UI 等操作问题。下一阶段目标是把“任务复盘 -> 导出 draft golden set -> 人工标注 -> 回归验证”接成质量工作台中的可视化闭环。

## 目标

Phase 4C-2 的目标是：在质量工作台中从一个已加载的任务复盘结果快速导出 draft golden set。

具体目标：

1. 用户在“任务复盘”中输入 `task_id` 并加载复盘结果。
2. 用户输入目标 `case_id`。
3. 用户点击“导出 Draft Golden Set”。
4. 后端复用现有 `QualityWorkbenchService.export_case()` 和导出脚本能力，生成：
   - `actual.json`
   - `expected.json`
   - `README.md`
5. 导出成功后，前端刷新 quality case 列表。
6. 如果导出的 `case_id` 出现在列表中，自动打开该 case。
7. UI 明确提示：导出的 expected diff 默认是 `DRAFT`，必须人工审核后才是可信 golden set。

## 非目标

本阶段不做以下能力：

- 不自动把 diff 标为 `APPROVED` 或 `REJECTED`。
- 不根据任务复盘的 suppressed diff 自动写入负向 golden set。
- 不对合同内容做自动脱敏。
- 不提交或上传真实合同文件。
- 不重新执行 OCR、matcher 或完整合同对比。
- 不设计新的 golden set schema。
- 不做批量任务导出。

原因：golden set 的可信性来自人工确认。系统可以加速生成草稿和减少操作错误，但不能把系统输出直接当作 gold 标准。

## 用户流程

推荐用户流程：

```text
1. 打开质量工作台
2. 在“任务复盘”区域输入 task_id
3. 点击“加载任务复盘”
4. 查看历史/保留/抑制 diff 统计
5. 判断该任务是否值得沉淀为样本
6. 输入 case_id
7. 点击“导出 Draft Golden Set”
8. 导出成功后自动打开该 case
9. 在 expected diff 列表中人工标注 APPROVED / REJECTED / DRAFT
10. 运行质量评估或回归
```

失败场景：

- `task_id` 未加载：导出按钮禁用。
- `case_id` 为空：导出按钮禁用。
- `case_id` 不安全：后端返回 400，UI 显示错误。
- 目标 case 已存在且未启用覆盖：后端返回 409，UI 提示已存在。
- task 不存在：后端返回 404，UI 显示错误。

## 后端设计

### 服务层

复用已有服务方法：

```python
QualityWorkbenchService.export_case(task_id: str, case_id: str, force: bool = False) -> dict[str, Any]
```

该方法已经负责：

- 校验 `task_id` 和 `case_id` 的安全路径。
- 检查 `task.json` 是否存在。
- 调用 `export_gold_case()` 生成 draft case。
- 默认不覆盖已有 case。

本阶段不新增新的导出业务逻辑，避免脚本和 API 的导出行为分叉。

### API

继续使用现有接口：

```http
POST /api/quality/cases/export
```

请求：

```json
{
  "task_id": "508ffeef-803a-4273-a628-403aaff8ecb7",
  "case_id": "508ffeef-803a-4273-a628-403aaff8ecb7",
  "force": false
}
```

响应继续使用 `QualityCaseExportResponse`。

错误映射保持现状：

- unsafe id -> 400
- missing task -> 404
- case already exists -> 409

## 前端设计

在现有 `QualityWorkbenchPage` 的“任务复盘”区域中增加导出控件。

### UI 结构

```text
任务复盘

[任务 ID 输入框] [加载任务复盘]

历史 diff 数 / 保留 diff 数 / 抑制 diff 数 / OCR 状态

导出 Draft Golden Set
[case_id 输入框] [导出 Draft Golden Set]
[可选 force 覆盖 checkbox]

提示：
导出的 expected diff 默认为 DRAFT，需要人工审核后才进入可信回归。
```

### 状态

新增状态：

- `draftCaseId`
- `isExportingDraftCase`
- `draftExportError`
- `draftExportMessage`
- `forceExportDraftCase`

复用已有：

- `taskReviewId`
- `taskReview`
- `listQualityCases()`
- `getQualityCase()`
- `exportQualityCase()`

### 行为

1. 加载任务复盘成功后，如果 `draftCaseId` 为空，默认填入当前 `task_id`。
2. “导出 Draft Golden Set”按钮启用条件：
   - 已有 `taskReview`
   - `taskReview.task_id` 非空
   - `draftCaseId.trim()` 非空
   - 当前没有导出请求进行中
3. 点击导出后：
   - 调用 `exportQualityCase({ task_id: taskReview.task_id, case_id: draftCaseId.trim(), force })`
   - 成功后刷新 case 列表
   - 如果刷新后存在该 case，自动打开该 case
   - 展示成功提示
4. 失败后：
   - 保留任务复盘结果
   - 显示错误
   - 不清空当前 selected case

### force 覆盖

第一版可以保留 `force` checkbox，但默认关闭。

UI 文案需要明确：

```text
覆盖已有 case 会重写该 case 目录，请只在确认草稿可替换时使用。
```

如果实现时认为覆盖操作风险过高，可以先不暴露 checkbox，只处理 409 错误并提示用户换一个 `case_id`。推荐第一版不暴露覆盖，降低误操作风险。

## 数据流

```text
用户加载 task review
  -> GET /api/quality/tasks/{task_id}/review
  -> 用户输入 case_id
  -> POST /api/quality/cases/export
  -> 后端复用 export_case()
  -> 生成 draft case 目录
  -> 前端刷新 listQualityCases()
  -> 前端打开新 case
  -> 用户人工审核 expected_diffs
```

## 测试方案

### 后端

后端已有 `export_case` 服务测试和 API 测试。本阶段后端以复用为主，只需要在前端实现不要求新增后端接口。

如果实现过程中发现现有测试缺口，补充：

- API 成功导出后返回 `case_id` 和 `expected_diff_count`。
- case 已存在且 `force=false` 返回 409。
- unsafe `case_id` 返回 400。

### 前端

新增或扩展 `QualityWorkbenchPage.test.tsx`：

- 任务复盘成功后默认填充 `case_id`。
- 点击“导出 Draft Golden Set”调用 `exportQualityCase()`。
- 导出成功后调用 `listQualityCases()` 并打开新 case。
- 导出失败时显示错误，任务复盘结果仍保留。
- 未加载任务复盘时导出按钮禁用。

扩展 `api.quality.test.ts` 如现有覆盖不足：

- `exportQualityCase()` 的 endpoint、method、body 正确。

## 文档

更新 `docs/golden_set_regression_sop.md`：

- 在“任务级误报复盘”后增加“从任务复盘导出 Draft Golden Set”。
- 说明 UI 操作步骤。
- 强调导出结果是 draft，不是可信 gold。
- 强调真实合同内容需要脱敏后才能提交。
- 说明 case 已存在时的处理方式。

## 成功标准

完成后应满足：

1. 用户可以在质量工作台中从已加载任务复盘导出 draft golden set。
2. 导出成功后可以直接在 UI 中打开新 case。
3. 导出失败时错误清晰，不破坏当前页面状态。
4. 导出过程不重跑 OCR、matcher 或完整比对。
5. 不自动标注任何 diff 为 `APPROVED` 或 `REJECTED`。
6. 文档明确 draft golden set 仍需人工审核。

## 后续方向

Phase 4C-2 完成后，再考虑：

- Phase 4C-3：从任务复盘中选择误报 diff，辅助生成 `REJECTED + should_not_match_again` 草稿。
- Phase 4C-4：导出前敏感信息检查清单。
- Phase 5：基于已沉淀 golden set 的质量回归门禁和模型路由调优。
