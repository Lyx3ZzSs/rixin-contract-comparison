# 负向 Golden Set 去重与幂等保护设计方案

## 背景

Phase 4C-3 已经支持在质量工作台中从 `ActualDiffList` 将人工确认的误报标为负向 Golden Set。该操作会创建一条 expected diff，并写入：

- `review_status: "REJECTED"`
- `should_not_match_again: true`
- `false_positive_reason: "manual_false_positive"`
- `source_actual_diff_id`
- `diff_type`
- `source_type`
- `title_contains`

当前剩余风险是：同一条 actual diff 在非 pending 状态下可以被重复点击，或者绕过前端重复调用 `POST /api/quality/cases/{case_id}/expected-diffs`。这会在 `expected.json` 中写入多个语义相同的负向样本，增加人工维护成本，也会让后续质量统计和回归报告变得难以解释。

Phase 4C-4 的目标是保护 Golden Set 数据质量：同一 case 内，同一个 actual diff 只应沉淀一次负向 Golden Set。

## 目标

1. 前端识别已经标为负向误报的 actual diff。
2. 已标注的 actual diff 显示 `已标为负向误报`，按钮禁用。
3. 未标注的 actual diff 仍显示 `标为负向误报`。
4. 正在提交时保持现有 `标注中...` pending 状态。
5. 后端对重复负向 expected diff 创建请求做幂等保护。
6. 重复请求不追加 expected diff，不报错，直接返回当前 case detail。
7. 非负向 expected diff 创建保持原有 append 行为。
8. 文档说明同一 actual diff 只需标注一次，重复请求会被幂等保护。

## 非目标

本阶段不做以下能力：

- 不清洗历史 `expected.json` 中已经存在的重复负向样本。
- 不删除或合并既有 expected diff。
- 不返回 409 冲突错误。
- 不做批量负向标注。
- 不做自动误报判断。
- 不改质量评估算法。
- 不扩大 `QualityActualDiffSummary` 字段。
- 不新增后端 API。

原因：本阶段要解决的是“从现在开始不要继续写入重复负向样本”。历史重复样本如果存在，应通过单独的数据清理任务处理，避免本阶段实现产生隐式数据迁移风险。

## 方案选择

### 方案 A：仅前端禁用重复标注

前端根据当前 case 的 `expected.expected_diffs` 计算哪些 `source_actual_diff_id` 已经存在负向样本，并禁用对应 actual diff 的按钮。

优点：

- 实现最小。
- 用户体验直接。
- 不改后端服务。

缺点：

- 绕过 UI 重复 POST 仍会写入重复样本。
- 并发点击或多端操作仍可能产生重复。
- 不能保证 `expected.json` 数据层安全。

### 方案 B：前端提示 + 后端幂等兜底

前端先禁用已标注 actual diff，后端在 `QualityWorkbenchService.create_expected_diff()` 中对负向样本做幂等判断。

优点：

- UI 层减少误操作。
- 服务层保证数据不重复。
- 重复请求返回 200，用户体验平稳。
- 不新增 API，不改变现有前端调用方式。

缺点：

- 需要补充后端服务测试和 API 测试。

### 方案 C：后端重复创建返回冲突错误

后端发现重复负向样本时返回错误，例如 409。

优点：

- 冲突语义严格。

缺点：

- 当前 API 错误映射没有 409 语义，需要新增异常和 HTTP 映射。
- 用户重复点击会看到错误，不如幂等返回自然。
- 对质量工作台这种人工标注流程来说，重复请求更像“已完成”，不是业务失败。

## 推荐方案

推荐方案 B：前端提示 + 后端幂等兜底。

理由：

- 它同时解决用户体验和数据一致性。
- 它不需要新接口。
- 它不会把正常重复点击变成错误。
- 它能保留 `create_expected_diff()` 当前作为创建入口的简单调用方式。

## 去重判定

本阶段只对负向 Golden Set 做幂等保护。

一个 payload 被认为是负向 Golden Set，当且仅当满足：

```text
review_status == "REJECTED"
should_not_match_again == true
source_actual_diff_id 是非空字符串
```

一个 existing expected diff 被认为是同一负向样本，当且仅当满足：

```text
existing.review_status == "REJECTED"
existing.should_not_match_again == true
existing.source_actual_diff_id == payload.source_actual_diff_id
```

不把 `title_contains`、`diff_type`、`source_type` 纳入幂等 key。

原因：

- `source_actual_diff_id` 是从 actual diff 到 expected diff 的追溯主键。
- 标题或类型后续可能被人工修订，不应导致同一 actual diff 产生第二条负向样本。
- 幂等保护的目标是“同一 actual diff 只沉淀一次负向样本”。

## 前端设计

### 数据来源

`QualityWorkbenchPage` 已经同时持有：

- `detail.actual_diffs`
- `detail.expected.expected_diffs`

前端可以从 expected diff 中计算已标注的负向 actual diff id 集合：

```ts
const negativeExpectedActualDiffIds = new Set(
  expectedDiffs
    .filter(
      (diff) =>
        diff.review_status === "REJECTED" &&
        diff.should_not_match_again === true &&
        typeof diff.source_actual_diff_id === "string" &&
        diff.source_actual_diff_id.length > 0,
    )
    .map((diff) => diff.source_actual_diff_id),
);
```

实现时可以用 `useMemo`，也可以在 render 中直接计算。当前数据量较小，两者都可接受。为了可读性，推荐使用 `useMemo`。

### ActualDiffList props

扩展 `ActualDiffList`：

```ts
negativeExpectedActualDiffIds: Set<string>
```

渲染每条 actual diff 时：

```ts
const isAlreadyNegativeExpected = negativeExpectedActualDiffIds.has(diff.diff_id);
```

按钮状态：

```text
如果 isSavingNegativeExpectedDiff: disabled, 文案 标注中...
否则如果 isAlreadyNegativeExpected: disabled, 文案 已标为负向误报
否则 enabled, 文案 标为负向误报
```

点击行为：

- 已标注时按钮禁用，不会调用 `onCreateNegativeExpectedDiff`。
- 未标注时沿用 Phase 4C-3 的创建流程。

### 成功后的状态

创建成功后，后端返回最新 case detail。前端调用 `applyCaseDetail(nextDetail)` 后，`negativeExpectedActualDiffIds` 会基于新的 expected diff 自动重算，对应 actual diff 按钮变为 `已标为负向误报`。

## 后端设计

### 服务层入口

修改：

```python
QualityWorkbenchService.create_expected_diff(case_id, payload)
```

保留现有流程：

1. 读取 case 的 `expected.json`。
2. 确保 `expected_diffs` 是 list。
3. 过滤 allowed fields。
4. 写入 `expected.json`。
5. 返回 `get_case(case_id)`。

新增逻辑放在 append 之前：

```python
next_diff = _allowed_expected_diff(payload)
if _is_negative_expected_diff(next_diff) and _has_same_negative_expected_diff(
    expected_diffs,
    next_diff["source_actual_diff_id"],
):
    return self.get_case(case_id)
```

如果重复，不写文件也可以接受；如果为了保持一致写回规范化结构则会增加无意义 churn。推荐重复时直接返回 `get_case(case_id)`，不写文件。

### Helper 函数

新增私有 helper：

```python
def _is_negative_expected_diff(diff: dict[str, Any]) -> bool:
    return (
        diff.get("review_status") == "REJECTED"
        and diff.get("should_not_match_again") is True
        and isinstance(diff.get("source_actual_diff_id"), str)
        and bool(diff.get("source_actual_diff_id"))
    )
```

新增：

```python
def _has_same_negative_expected_diff(
    expected_diffs: list[Any],
    source_actual_diff_id: str,
) -> bool:
    for diff in expected_diffs:
        if not isinstance(diff, dict):
            continue
        if (
            diff.get("review_status") == "REJECTED"
            and diff.get("should_not_match_again") is True
            and diff.get("source_actual_diff_id") == source_actual_diff_id
        ):
            return True
    return False
```

保持 helper 私有，不新增公开服务接口。

## API 行为

`POST /api/quality/cases/{case_id}/expected-diffs` 不新增参数，不改 response model。

重复负向样本请求：

- HTTP 200
- response 是当前 case detail
- `expected.expected_diffs` 数量不增加

非重复负向样本请求：

- HTTP 200
- 追加 expected diff
- response 是更新后的 case detail

非负向样本请求：

- 沿用现有 append 行为

## 数据流

```text
用户打开质量工作台
  -> 前端读取 case detail
  -> 从 expected_diffs 计算已负向标注的 source_actual_diff_id 集合
  -> ActualDiffList 禁用已标注 actual diff 的按钮
  -> 用户点击未标注 actual diff
  -> POST create expected diff
  -> 后端判断是否为重复负向样本
      -> 重复：不 append，返回当前 case detail
      -> 不重复：append，写 expected.json，返回最新 case detail
  -> 前端刷新 detail
  -> 按钮状态变为已标注
```

## 测试方案

### 前端测试

扩展 `frontend/src/pages/QualityWorkbenchPage.test.tsx`：

1. 已存在负向 expected diff 时，actual diff 按钮显示 `已标为负向误报`，并且 disabled。
2. 点击或尝试操作已禁用按钮不会调用 `createQualityExpectedDiff`。
3. 从未标注状态创建成功后，对应按钮变为 `已标为负向误报`。

第三点可以复用已有成功创建测试，在 `rejectedDetail` 返回后额外断言按钮文案变化。

### 后端服务测试

扩展 `backend/tests/test_quality_workbench.py`：

1. 第一次创建负向 expected diff 会 append。
2. 第二次使用相同 `source_actual_diff_id`、`REJECTED`、`should_not_match_again: true` 创建时，不增加 `expected_diffs` 数量。
3. 相同 `source_actual_diff_id` 但不是负向样本，例如 `review_status: "APPROVED"` 或没有 `should_not_match_again`，仍按原逻辑 append。

### API 测试

扩展 `backend/tests/test_api_quality.py`：

1. 重复 POST 同一个负向 payload 返回 200。
2. 第二次 response 中 `expected.expected_diffs` 数量不增加。
3. 已存在的负向 expected diff 字段保持不变。

## 文档

更新 `docs/golden_set_regression_sop.md` 的负向 Golden Set 章节：

- 同一 actual diff 只需要标注一次。
- 工作台会对已标注 actual diff 显示 `已标为负向误报`。
- 后端会对重复负向创建请求做幂等保护。
- 如果历史 case 已经存在重复负向样本，本阶段不会自动删除，需要人工清理。

## 成功标准

完成后应满足：

1. 已有负向样本的 actual diff 不再允许通过 UI 重复创建。
2. 重复 POST 相同负向样本不会追加 expected diff。
3. 非负向 expected diff 创建不受影响。
4. 不新增 API。
5. 不改质量评估算法。
6. 前端、后端服务、API 都有回归测试。
7. SOP 说明幂等保护和历史重复样本处理边界。

## 后续方向

Phase 4C-4 完成后，可以再考虑：

- 历史重复负向样本扫描脚本。
- 基于 `source_actual_diff_id` 的 expected diff 快速定位。
- 批量负向标注前的重复检查。
- 人工审核工作台中的 duplicate badge 或 filter。
