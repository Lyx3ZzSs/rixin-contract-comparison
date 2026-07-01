# 质量回归工作台设计方案

## 背景

当前合同差异比对项目已经具备命令行质量体系：

- `backend/scripts/export_ocr_compare_gold_case.py`：从已完成任务导出 draft golden set。
- `backend/scripts/evaluate_ocr_compare_quality.py`：基于 approved gold case 做单次质量评估。
- `backend/scripts/run_quality_regression.py`：基于 baseline 做质量回归门禁。
- `backend/scripts/analyze_quality_attribution.py`：对回归结果做质量归因。
- `backend/scripts/inspect_task_quality.py`：排查单个任务的质量风险和可疑差异。

这些脚本适合工程使用，但对日常标注和排查不够直观。目标是新增一个可视化质量回归工作台，把“导出 golden set、人工标注、运行评估、运行回归、查看误报漏报”变成可点击流程。

## 目标

第一期目标：

1. 在前端新增“质量回归”页面。
2. 支持查看本地 gold case 列表。
3. 支持从 `task_id` 导出 draft gold case。
4. 支持查看并编辑 `expected.json` 中的 `expected_diffs`。
5. 支持把 expected diff 标为 `APPROVED`、`DRAFT`、`REJECTED`。
6. 支持运行单次质量评估。
7. 支持运行 baseline 回归。
8. 支持查看核心指标、误报、漏检、证据漂移和回归门禁失败项。

非目标：

- 第一期不做完整 PDF 双栏标注器。
- 第一期不做多人协同审核、权限、审计流。
- 第一期不做趋势图和历史指标仓库。
- 第一期不直接替代命令行脚本；脚本仍作为自动化和 CI 入口保留。

## 总体方案

采用“内部 API + 前端工作台 + 复用现有脚本核心函数”的结构。

```text
frontend/src/pages/QualityWorkbenchPage.tsx
        |
        v
frontend/src/lib/api.ts 新增 quality API client
        |
        v
backend/app/api_quality.py
        |
        v
backend/app/services/quality_workbench.py
        |
        v
backend/scripts/export_ocr_compare_gold_case.py
backend/scripts/evaluate_ocr_compare_quality.py
backend/scripts/run_quality_regression.py
backend/scripts/analyze_quality_attribution.py
```

后端 API 不通过 shell 调脚本，而是优先 import 脚本中的函数。这样可以避免命令拼接风险，也方便测试。

## 后端设计

### 新增模块

新增：

```text
backend/app/api_quality.py
backend/app/services/quality_workbench.py
```

`api_quality.py` 只负责 HTTP 参数、响应模型、错误转换。

`quality_workbench.py` 负责：

- 定位 `storage/tasks`。
- 定位 `backend/tests/fixtures/ocr_compare_cases`。
- 读取/写入 `expected.json`。
- 调用 export/evaluate/regression 核心函数。
- 做路径安全校验。
- 生成工作台可消费的摘要结构。

### API 路由

新增路由前缀：

```text
/api/quality
```

#### 列出 gold cases

```text
GET /api/quality/cases
```

响应字段：

```json
{
  "cases": [
    {
      "case_id": "da0e1282-e239-42dd-92dd-447b8f7ec136",
      "source_task_id": "da0e1282-e239-42dd-92dd-447b8f7ec136",
      "original_filename": "...",
      "compare_filename": "...",
      "approved_expected_count": 0,
      "draft_expected_count": 9,
      "rejected_expected_count": 0,
      "actual_diff_count": 9,
      "has_actual_json": true,
      "has_source_pdfs": false
    }
  ]
}
```

#### 导出 draft gold case

```text
POST /api/quality/cases/export
```

请求：

```json
{
  "task_id": "da0e1282-e239-42dd-92dd-447b8f7ec136",
  "case_id": "da0e1282-e239-42dd-92dd-447b8f7ec136",
  "force": false
}
```

行为：

- 从 `storage/tasks/<task_id>/task.json` 读取任务。
- 导出到 `backend/tests/fixtures/ocr_compare_cases/<case_id>`。
- 如果目标 `expected.json` 已包含非 `DRAFT` 标注且 `force=false`，返回 409。

#### 获取单个 gold case

```text
GET /api/quality/cases/{case_id}
```

响应包含：

- `README.md` 摘要。
- `expected.json` 的结构化内容。
- `actual.json` 的差异摘要。
- 每条 expected diff 的可编辑字段。
- 每条 actual diff 的 `diff_id`、标题、类型、来源、质量状态、review flags。

#### 更新 expected diff 标注

```text
PATCH /api/quality/cases/{case_id}/expected-diffs/{index}
```

请求：

```json
{
  "review_status": "APPROVED",
  "diff_type": "MODIFY",
  "source_type": "metadata",
  "title_contains": "签订日期",
  "original_contains": "2026年4月 日",
  "compare_contains": "2026年4月21日",
  "severity": "critical",
  "notes": "人工确认真实差异"
}
```

行为：

- 按 index 更新 `expected.json` 中对应条目。
- 只允许更新白名单字段。
- 写入前校验 JSON 结构。
- 使用原子写入避免写坏文件。

#### 新增 expected diff

```text
POST /api/quality/cases/{case_id}/expected-diffs
```

用于补充系统漏检的 gold 差异。

#### 删除 expected diff

```text
DELETE /api/quality/cases/{case_id}/expected-diffs/{index}
```

用于删除明显无价值的草稿条目。对 `APPROVED` 条目建议前端二次确认。

#### 运行单次质量评估

```text
POST /api/quality/evaluate
```

请求：

```json
{
  "case_root": "tests/fixtures/ocr_compare_cases",
  "run_id": "local-eval"
}
```

第一期固定只允许默认 case root，避免任意路径读写。

响应：

```json
{
  "run_id": "local-eval",
  "status": "COMPLETED",
  "quality_report_path": ".ocr-compare-quality/runs/local-eval/quality.json",
  "html_report_path": ".ocr-compare-quality/runs/local-eval/html/index.html",
  "aggregate": {
    "precision": 0.0,
    "recall": 0.0,
    "false_positive_count": 9,
    "false_negative_count": 0,
    "evidence_hit_rate": 0.0
  }
}
```

#### 运行质量回归

```text
POST /api/quality/regression
```

请求：

```json
{
  "baseline": ".ocr-compare-quality/baselines/v0.0.2.json",
  "run_id": "local-check",
  "fail_on_regression": true
}
```

第一期 baseline 只允许从 `.ocr-compare-quality/baselines` 下选择。

#### 读取 run 结果

```text
GET /api/quality/runs/{run_id}
```

返回：

- `run_summary.json`
- `quality.json` 摘要
- `baseline_comparison.json` 摘要
- failed gates
- case ranking
- missed expected diffs
- unexpected actual diffs
- evidence drift

#### 运行质量归因

```text
POST /api/quality/runs/{run_id}/attribution
```

调用 `analyze_quality_attribution.py` 的核心函数，生成 `quality_attribution.json`。

### 路径安全

后端必须限制所有文件读写根目录：

- gold case root：`backend/tests/fixtures/ocr_compare_cases`
- task root：`storage/tasks`
- quality output root：`backend/.ocr-compare-quality`

所有 API 入参中的 `case_id`、`task_id`、`run_id` 只能是安全 ID：

```text
字母、数字、下划线、短横线、点号
```

禁止：

- `..`
- 绝对路径
- 路径分隔符
- 任意用户传入输出路径

## 前端设计

### 路由

新增路由：

```text
/quality
```

修改：

```text
frontend/src/lib/routes.ts
frontend/src/App.tsx
```

侧边栏新增入口：

```text
质量回归
```

### 页面结构

新增：

```text
frontend/src/pages/QualityWorkbenchPage.tsx
frontend/src/pages/QualityWorkbenchPage.test.tsx
```

页面采用三栏或上下分区：

```text
顶部：操作区
  - 输入 task_id
  - 输入/自动生成 case_id
  - 导出 draft gold case
  - 运行质量评估
  - 运行回归

左侧：gold case 列表
  - case_id
  - approved/draft/rejected 数量
  - actual diff 数量
  - 是否有 source PDFs

中间：expected diff 标注表
  - diff_type
  - source_type
  - title_contains
  - original_contains
  - compare_contains
  - review_status
  - severity
  - 保存按钮

右侧/下方：质量报告摘要
  - precision
  - recall
  - false_positive_count
  - false_negative_count
  - evidence_hit_rate
  - failed gates
  - missed expected diffs
  - unexpected actual diffs
  - evidence drift
```

### 交互原则

- `APPROVED`、`DRAFT`、`REJECTED` 使用分段控件。
- `diff_type`、`source_type`、`severity` 使用下拉选择。
- `title_contains`、`original_contains`、`compare_contains` 使用可编辑文本框。
- 保存单条 diff，不做整页隐式保存。
- 对删除 `APPROVED` 条目做确认。
- 对 `approved_expected_count = 0` 的 case 显示明确提示：当前不能作为有效回归依据。
- 运行评估/回归时展示 loading 和结果摘要。

### API client

修改：

```text
frontend/src/lib/api.ts
frontend/src/types.ts
```

新增函数：

```ts
listQualityCases()
exportQualityCase(payload)
getQualityCase(caseId)
updateExpectedDiff(caseId, index, payload)
createExpectedDiff(caseId, payload)
deleteExpectedDiff(caseId, index)
runQualityEvaluation(payload)
runQualityRegression(payload)
getQualityRun(runId)
runQualityAttribution(runId)
```

## 数据模型

### 后端 Pydantic schema

可放入：

```text
backend/app/api_schemas.py
```

或拆分为：

```text
backend/app/api_quality_schemas.py
```

推荐拆分，避免 `api_schemas.py` 继续膨胀。

核心模型：

```text
QualityCaseSummaryResponse
QualityCaseDetailResponse
ExpectedDiffUpdateRequest
ExpectedDiffCreateRequest
QualityCaseExportRequest
QualityEvaluationRequest
QualityRegressionRequest
QualityRunSummaryResponse
```

### 前端类型

放入：

```text
frontend/src/types.ts
```

或新增：

```text
frontend/src/types-quality.ts
```

推荐第一期放入 `types.ts`，如果增长明显再拆分。

## 错误处理

后端错误映射：

- 400：非法 `case_id`、`task_id`、`run_id`。
- 404：任务不存在、case 不存在、run 不存在、baseline 不存在。
- 409：导出目标已存在且包含人工标注。
- 422：expected diff 字段不合法。
- 500：评估或回归执行失败。

前端展示：

- 操作失败统一显示错误条。
- 字段校验失败显示在表单附近。
- 回归失败不是系统错误，应显示为“质量门禁未通过”，并展示 failed gates。

## 测试方案

### 后端测试

新增：

```text
backend/tests/test_quality_workbench.py
backend/tests/test_api_quality.py
```

覆盖：

- list cases 能统计 approved/draft/rejected。
- export case 会生成三个文件。
- export 已有 reviewed case 且 `force=false` 返回冲突。
- update expected diff 只写允许字段。
- 非法 case_id/task_id/run_id 被拒绝。
- evaluate 调用现有 evaluator 并返回摘要。
- regression 调用现有 runner 并返回 failed gates。

### 前端测试

新增：

```text
frontend/src/pages/QualityWorkbenchPage.test.tsx
```

覆盖：

- 页面加载 case 列表。
- 选择 case 后展示 expected diff。
- 修改 review status 并保存。
- approved count 为 0 时显示不可作为有效回归依据。
- 运行评估后展示指标。
- 回归失败时展示 failed gates。

### 命令验证

实现后建议运行：

```bash
cd backend
python -m compileall app tests
python -m pytest tests/test_quality_workbench.py tests/test_api_quality.py
python -m ruff check app tests
```

```bash
cd frontend
npm test -- QualityWorkbenchPage
npm run build
```

## 分阶段实施

### Phase 1：后端能力封装

- 新增 `quality_workbench.py`。
- 新增 `/api/quality/cases`。
- 新增 `/api/quality/cases/export`。
- 新增 expected diff 更新能力。
- 加后端测试。

### Phase 2：前端标注工作台

- 新增 `/quality` 路由。
- 新增侧边栏入口。
- 新增 case 列表和 expected diff 编辑表。
- 接入保存接口。
- 加前端测试。

### Phase 3：评估与回归运行

- 新增 evaluate/regression/run 相关 API。
- 前端展示指标、failed gates、误报、漏检、证据漂移。
- 接入 attribution。

### Phase 4：增强体验

- 增加 baseline 选择器。
- 增加 report HTML 链接。
- 增加按 source_type、review_status、severity 过滤。
- 增加从 actual diff 一键生成 expected diff。

### Phase 5：PDF 证据可视化

- 在工作台内嵌现有 PDF viewer。
- 点击 diff 定位证据页。
- 展示 original/compare evidence box。
- 支持人工录入 expected evidence。

## 风险与约束

1. 真实合同数据敏感。第一期必须限制路径，并避免把真实报告默认提交到仓库。
2. 现有 `actual.json` 快照不会重新执行算法。页面需要明确显示 case 是“快照评估”还是“源 PDF 重跑评估”。
3. 回归运行可能耗时。第一期可以同步调用，后续如果运行变慢，再改为后台任务。
4. 当前工作区已有大量未提交改动。实施时需要避免混入无关文件。
5. 如果 `backend/tests/fixtures/ocr_compare_cases` 包含敏感内容，不应直接暴露给非内部用户。

## 验收标准

第一期完成后，用户可以：

1. 打开 `/quality`。
2. 输入一个已完成的 `task_id` 并导出 draft gold case。
3. 在页面中把真实差异标为 `APPROVED`。
4. 保存 `expected.json`。
5. 运行单次质量评估并看到指标。
6. 基于已有 baseline 运行质量回归。
7. 在页面中看到 failed gates、误报、漏检和证据漂移。

通过标准：

- 不需要手写命令也能完成 golden set 标注和回归检查。
- 命令行脚本仍可独立运行。
- 非法路径不能通过 API 读取或写入。
- 后端和前端关键测试通过。
