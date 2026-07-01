# 任务级误报复盘工作台设计方案

## 背景

当前质量工作台已经支持 golden set case 列表、expected diff 标注、证据编辑、质量评估和回归运行。但在真实任务质量排查时，仍需要工程人员手动读取：

- `storage/tasks/<task_id>/task.json`
- `storage/tasks/<task_id>/debug/diff_quality.json`
- `storage/tasks/<task_id>/debug/diff_decisions.json`
- `storage/tasks/<task_id>/debug/ocr_quality.json`
- `storage/tasks/<task_id>/debug/clause_matches.json`

例如任务 `fb67bf36-73bd-48c3-a7b3-59696ed4fb12` 中，历史输出包含 `D005`、`D007`、`D010`、`D013` 等误报。修复后需要快速确认：这些历史 diff 用当前质量过滤逻辑复放后是否会被抑制。

Phase 4C-1 的目标是把这类排查过程产品化，作为质量工作台中的“任务复盘”入口。

## 目标

第一版只做任务快照复盘，不重新执行完整合同比对。

目标：

1. 在质量工作台中输入 `task_id`，读取已存在的任务目录。
2. 展示历史任务输出 diff 总数、质量状态、OCR 风险摘要。
3. 使用当前代码对历史 `task.json.diffs` 重新运行 `DiffQualityProcessor`。
4. 展示当前质量过滤复放后的保留 diff 与被抑制 diff。
5. 对被抑制 diff 展示抑制原因，例如：
   - `clause_ocr_noise`
   - `single_latin_layout_glyph_noise`
   - `layout_punctuation_equivalent`
   - `short_symbol_noise`
6. 对保留 diff 展示质量状态、review flags、matcher 分数、标题和片段，帮助人工继续判断。
7. 不改写 `task.json`。
8. 不自动重新执行 OCR、条款切分、matcher 或完整对比流程。

非目标：

- 第一版不做 PDF 双栏渲染和 bbox 高亮。
- 第一版不做一键标注 golden set。
- 第一版不导出或写入 `expected.json`。
- 第一版不跑完整合同对比。
- 第一版不做跨任务趋势统计。

## 用户流程

```text
1. 打开质量工作台
2. 进入“任务复盘”区域
3. 输入 task_id
4. 点击“加载任务复盘”
5. 查看任务摘要
6. 查看历史 diff 列表
7. 查看当前质量过滤复放结果
8. 对比“历史输出”和“当前会保留/会抑制”
9. 根据抑制原因判断修复是否命中误报模式
```

第一版以工程排查为主，不要求审核员完成标注闭环。

## 后端设计

### 新增服务能力

在 `QualityWorkbenchService` 中新增方法：

```python
review_task(task_id: str) -> dict[str, Any]
```

职责：

- 校验 `task_id` 只允许安全路径片段。
- 定位 `settings.tasks_dir / task_id / task.json`。
- 读取历史任务 JSON。
- 将 `task["diffs"]` 反序列化为 `DiffItem`。
- 调用 `DiffQualityProcessor().process(diffs)`，只做质量过滤复放。
- 比较历史 diff id 与复放后保留 diff id，生成：
  - `retained_diffs`
  - `suppressed_diffs`
  - `quality_decisions`
- 尽量读取 debug artifacts 摘要，但缺失时不失败。

### API

新增：

```http
GET /api/quality/tasks/{task_id}/review
```

响应示例：

```json
{
  "task_id": "fb67bf36-73bd-48c3-a7b3-59696ed4fb12",
  "status": "COMPLETED",
  "original_filename": "...pdf",
  "compare_filename": "...pdf",
  "historical_diff_count": 10,
  "retained_diff_count": 6,
  "suppressed_diff_count": 4,
  "ocr_quality_summary": {
    "status": "UNRELIABLE",
    "requires_review": true,
    "risk_page_count": 11,
    "affected_diff_count": 9
  },
  "retained_diffs": [
    {
      "diff_id": "D001",
      "diff_type": "ADD",
      "source_type": "metadata",
      "title": "封面字段：合同编号（乙方）",
      "quality_status": "NEEDS_REVIEW",
      "review_flags": ["PAGE_UNRELIABLE"],
      "match_score": null,
      "original_snippet": "",
      "compare_snippet": "GNXNYN-20140604-000024"
    }
  ],
  "suppressed_diffs": [
    {
      "diff_id": "D007",
      "diff_type": "MODIFY",
      "source_type": "clause",
      "title": "可行性论证报告:;",
      "suppression_reason": "clause_ocr_noise",
      "quality_decisions": ["possible_ocr_noise", "suppressed_low_value_noise"],
      "original_snippet": "/",
      "compare_snippet": ""
    }
  ],
  "quality_decisions": [
    {
      "diff_id": "D007",
      "action": "suppressed_low_value_noise",
      "detail": {
        "reason": "clause_ocr_noise"
      }
    }
  ],
  "debug_artifacts": {
    "has_diff_quality": true,
    "has_diff_decisions": true,
    "has_ocr_quality": true,
    "has_clause_matches": true
  }
}
```

### 错误处理

- 非安全 `task_id` 返回 400。
- 找不到 `task.json` 返回 404。
- `task.json` 结构无法解析时第一版统一返回 500，并在 detail 中说明解析失败；后续如需细分客户端输入错误，再引入 422。
- debug artifact 缺失不阻塞，只在 `debug_artifacts` 标记为 false。

### 安全边界

- 只允许从配置的 `settings.tasks_dir` 下读取任务。
- `task_id` 复用质量工作台已有 safe id 校验。
- 不接受任意路径参数。
- 不写入任务目录。
- 不执行 shell 命令。

## 前端设计

在 `QualityWorkbenchPage` 增加一个“任务复盘”区域，不新建独立主路由。

第一版 UI 结构：

```text
质量回归工作台

[任务复盘]
task_id 输入框  [加载任务复盘]

任务摘要
- 状态
- 历史 diff 数
- 当前保留 diff 数
- 当前抑制 diff 数
- OCR 状态

被抑制 diff
- diff_id
- 标题
- 原片段 / 新片段
- 抑制原因
- quality decisions

保留 diff
- diff_id
- 类型 / 来源
- 标题
- quality_status
- review flags
- matcher 分数
```

交互要求：

- 加载中显示按钮禁用状态。
- 加载失败显示错误提示。
- 如果没有被抑制 diff，显示“当前质量过滤未抑制历史 diff”。
- 历史任务复盘结果不影响当前 selected gold case。
- 切换 gold case 不清空任务复盘结果，除非用户重新加载。

## 前端 API 与类型

在 `frontend/src/types.ts` 增加：

- `QualityTaskReviewResponse`
- `QualityTaskReviewDiff`
- `QualityTaskSuppressedDiff`
- `QualityDecisionSummary`
- `QualityDebugArtifactSummary`

在 `frontend/src/lib/api.ts` 增加：

```ts
export async function getQualityTaskReview(taskId: string): Promise<QualityTaskReviewResponse>
```

## 数据流

```text
用户输入 task_id
  -> frontend getQualityTaskReview(taskId)
  -> GET /api/quality/tasks/{task_id}/review
  -> QualityWorkbenchService.review_task()
  -> read task.json
  -> DiffQualityProcessor.process(historical_diffs)
  -> compare retained/suppressed ids
  -> return summary
  -> UI render retained/suppressed result
```

## 测试方案

### 后端测试

新增或扩展 `backend/tests/test_quality_workbench.py`：

- `test_review_task_replays_quality_filter_and_reports_suppressed_diffs`
  - 构造 task.json，包含 `/ -> ""` 的短符号 OCR 噪声。
  - 断言 suppressed diff 出现在 `suppressed_diffs`。
  - 断言 suppression reason 为 `clause_ocr_noise`。
- `test_review_task_rejects_unsafe_task_id`
  - 传入 `../bad` 返回业务异常。
- `test_review_task_missing_task_returns_not_found`
  - task 目录不存在时抛 `QualityTaskNotFoundError`。

新增或扩展 `backend/tests/test_api_quality.py`：

- `test_review_quality_task_endpoint`
- `test_review_quality_task_rejects_unsafe_task_id`
- `test_review_quality_task_missing_task_returns_404`

### 前端测试

扩展 `frontend/src/lib/api.quality.test.ts`：

- API client 正确请求 `/api/quality/tasks/<task_id>/review`。

扩展 `frontend/src/pages/QualityWorkbenchPage.test.tsx`：

- 输入 task id 并加载任务复盘。
- 展示历史 diff 数、保留 diff 数、抑制 diff 数。
- 展示 `D007` 和 `clause_ocr_noise`。
- 加载失败时显示错误。

## 实施顺序

1. 后端 service 方法和单元测试。
2. 后端 API schema 与 endpoint。
3. 前端类型与 API client。
4. 前端任务复盘 UI。
5. 文档更新。
6. 回归验证。

## 验收标准

以任务 `fb67bf36-73bd-48c3-a7b3-59696ed4fb12` 为例，第一版完成后应能在 UI 中看到：

- 历史 diff 数：`10`
- 当前质量过滤保留 diff 数：`6`
- 当前质量过滤抑制 diff 数：`4`
- 被抑制 diff 包含：
  - `D005`
  - `D007`
  - `D010`
  - `D013`
- `D007` 和 `D010` 的原因显示为 `clause_ocr_noise`。
- `D013` 的原因显示为 `single_latin_layout_glyph_noise`。

## 后续阶段

Phase 4C-1 完成后，再考虑：

- Phase 4C-2：从任务复盘页一键导出 draft gold case。
- Phase 4C-3：在任务复盘页直接标注误报/真实差异。
- Phase 4C-4：PDF 双栏证据查看。
- Phase 4C-5：跨任务误报模式统计。
