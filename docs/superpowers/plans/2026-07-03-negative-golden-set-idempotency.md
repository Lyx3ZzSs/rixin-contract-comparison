# Negative Golden Set Idempotency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 防止同一条 actual diff 被重复创建为多个负向 Golden Set expected diff。

**Architecture:** 前端根据当前 case 的 rejected expected diff 计算已标注的 `source_actual_diff_id` 集合，并禁用对应 actual diff 的按钮。后端在 `QualityWorkbenchService.create_expected_diff()` 中对 `REJECTED + should_not_match_again + source_actual_diff_id` 做幂等保护，重复请求返回当前 case detail，不追加条目。

**Tech Stack:** React、TypeScript、Vitest、Testing Library、FastAPI、pytest、Markdown。

---

## Scope Check

本计划实现 `docs/superpowers/specs/2026-07-03-negative-golden-set-idempotency-design.md` 的推荐方案 B：前端提示 + 后端幂等兜底。

本计划不实现：

- 历史重复负向样本扫描或清洗。
- 批量负向标注。
- 409 冲突错误。
- 自动误报判断。
- 质量评估算法调整。
- 新增后端 API。
- 扩展 `QualityActualDiffSummary` 字段。

## File Structure

修改文件：

- `frontend/src/pages/QualityWorkbenchPage.test.tsx`
  - 增加前端行为测试：已存在负向样本时按钮显示 `已标为负向误报` 且禁用；创建成功后按钮变为已标注。

- `frontend/src/pages/QualityWorkbenchPage.tsx`
  - 增加 `useMemo`。
  - 从 `expectedDiffs` 计算 `negativeExpectedActualDiffIds`。
  - 将集合传给 `ActualDiffList`。
  - `ActualDiffList` 根据已标注状态调整按钮文案、disabled 状态和点击行为。

- `backend/tests/test_quality_workbench.py`
  - 增加服务层幂等测试：重复负向样本不 append，非负向样本仍 append。

- `backend/app/services/quality_workbench.py`
  - 增加负向 expected diff helper。
  - 在 `create_expected_diff()` append 前做幂等判断。

- `backend/tests/test_api_quality.py`
  - 增加 API 层重复 POST 测试：返回 200 且 expected diff 数量不增加。

- `docs/golden_set_regression_sop.md`
  - 补充同一 actual diff 只需标注一次、重复请求会被幂等保护、历史重复不自动清理。

不修改文件：

- `frontend/src/types.ts`
- `frontend/src/lib/api.ts`
- `backend/app/api_quality.py`
- 质量评估脚本和 matcher 逻辑

## Current Context Notes

执行前运行：

```bash
git status --short
```

当前工作区可能已有与本阶段无关的未提交改动，例如：

```text
 M backend/app/services/clause_splitter.py
 M backend/app/services/diff_quality.py
 M backend/app/services/document_preparation.py
 M backend/app/services/header_footer_compare.py
 M backend/tests/test_header_footer_compare.py
 M backend/tests/test_text_cleaning_quality.py
 M frontend/src/pages/ResultPage.test.tsx
 M frontend/src/pages/ResultPage.tsx
 M frontend/src/styles.css
?? docs/superpowers/plans/2026-07-03-task-review-draft-golden-export.md
?? storage/
```

不要回滚这些既有改动。提交时只暂存本计划列出的文件。

## Task 1: Add Frontend RED Tests For Already-Negative Actual Diff

**Files:**
- Modify: `frontend/src/pages/QualityWorkbenchPage.test.tsx`

- [ ] **Step 1: Add a test where an actual diff is already marked negative**

在现有 `creates a negative expected diff from an actual diff` 测试之后新增：

```tsx
  it("disables negative marking when the actual diff already has a negative expected diff", async () => {
    const negativeDetail: QualityCaseDetail = {
      ...caseDetail,
      summary: {
        ...caseSummary,
        rejected_expected_count: 1,
      },
      expected: {
        ...caseDetail.expected,
        expected_diffs: [
          ...caseExpectedDiffs,
          {
            review_status: "REJECTED",
            should_not_match_again: true,
            false_positive_reason: "manual_false_positive",
            source_actual_diff_id: "diff-001",
            diff_type: "MODIFY",
            source_type: "clause",
            title_contains: "实际日期变更",
          },
        ],
      },
    };
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(negativeDetail);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    const negativeButton = screen.getByRole("button", { name: "已标为负向误报" });

    expect(negativeButton).toBeDisabled();
    fireEvent.click(negativeButton);
    expect(createQualityExpectedDiff).not.toHaveBeenCalled();
  });
```

- [ ] **Step 2: Extend the existing success test to assert post-create button state**

在现有 `creates a negative expected diff from an actual diff` 测试末尾，把：

```tsx
    expect(await screen.findByText("REJECTED")).toBeInTheDocument();
```

替换为：

```tsx
    expect(await screen.findByText("REJECTED")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "已标为负向误报" })).toBeDisabled();
```

- [ ] **Step 3: Run focused frontend tests and verify RED**

Run:

```bash
cd frontend
npm test -- QualityWorkbenchPage
```

Expected: the new or updated tests fail because the UI still renders `标为负向误报` after a negative expected diff exists. The failure should mention that button name `已标为负向误报` cannot be found.

## Task 2: Implement Frontend Duplicate State

**Files:**
- Modify: `frontend/src/pages/QualityWorkbenchPage.tsx`

- [ ] **Step 1: Import useMemo**

Replace:

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
```

with:

```tsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
```

- [ ] **Step 2: Compute negative expected actual diff ids**

After:

```tsx
  const expectedDiffs = detail?.expected.expected_diffs ?? [];
```

add:

```tsx
  const negativeExpectedActualDiffIds = useMemo(() => {
    return new Set(
      expectedDiffs
        .filter(
          (diff) =>
            diff.review_status === "REJECTED" &&
            diff.should_not_match_again === true &&
            typeof diff.source_actual_diff_id === "string" &&
            diff.source_actual_diff_id.length > 0,
        )
        .map((diff) => diff.source_actual_diff_id as string),
    );
  }, [expectedDiffs]);
```

- [ ] **Step 3: Pass the set to ActualDiffList**

Replace:

```tsx
              <ActualDiffList
                diffs={detail.actual_diffs}
                isSavingNegativeExpectedDiff={isSavingNegativeExpectedDiff}
                onCreateNegativeExpectedDiff={createNegativeExpectedDiff}
              />
```

with:

```tsx
              <ActualDiffList
                diffs={detail.actual_diffs}
                isSavingNegativeExpectedDiff={isSavingNegativeExpectedDiff}
                negativeExpectedActualDiffIds={negativeExpectedActualDiffIds}
                onCreateNegativeExpectedDiff={createNegativeExpectedDiff}
              />
```

- [ ] **Step 4: Extend ActualDiffList props**

Replace the `ActualDiffList` signature with:

```tsx
function ActualDiffList({
  diffs,
  isSavingNegativeExpectedDiff,
  negativeExpectedActualDiffIds,
  onCreateNegativeExpectedDiff,
}: {
  diffs: QualityActualDiffSummary[];
  isSavingNegativeExpectedDiff: boolean;
  negativeExpectedActualDiffIds: Set<string>;
  onCreateNegativeExpectedDiff: (diff: QualityActualDiffSummary) => void;
}) {
```

- [ ] **Step 5: Update the actual diff button state**

Inside `diffs.map((diff) => (` replace the current button block:

```tsx
            <div className="quality-diff-actions">
              <button
                type="button"
                onClick={() => onCreateNegativeExpectedDiff(diff)}
                disabled={isSavingNegativeExpectedDiff}
              >
                {isSavingNegativeExpectedDiff ? "标注中..." : "标为负向误报"}
              </button>
            </div>
```

with:

```tsx
            <div className="quality-diff-actions">
              {(() => {
                const isAlreadyNegativeExpected = negativeExpectedActualDiffIds.has(diff.diff_id);
                const isDisabled = isSavingNegativeExpectedDiff || isAlreadyNegativeExpected;
                const label = isSavingNegativeExpectedDiff
                  ? "标注中..."
                  : isAlreadyNegativeExpected
                    ? "已标为负向误报"
                    : "标为负向误报";

                return (
                  <button
                    type="button"
                    onClick={() => onCreateNegativeExpectedDiff(diff)}
                    disabled={isDisabled}
                  >
                    {label}
                  </button>
                );
              })()}
            </div>
```

- [ ] **Step 6: Run frontend tests and verify GREEN**

Run:

```bash
cd frontend
npm test -- QualityWorkbenchPage
```

Expected: `QualityWorkbenchPage.test.tsx` passes.

## Task 3: Add Backend RED Tests For Negative Idempotency

**Files:**
- Modify: `backend/tests/test_quality_workbench.py`
- Modify: `backend/tests/test_api_quality.py`

- [ ] **Step 1: Add service test for repeated negative expected diff**

Append this test after `test_create_and_delete_expected_diff` in `backend/tests/test_quality_workbench.py`:

```python
def test_create_expected_diff_is_idempotent_for_same_negative_actual_diff(
    tmp_path: Path,
) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    _make_case(case_root)

    payload = {
        "review_status": "REJECTED",
        "should_not_match_again": True,
        "false_positive_reason": "manual_false_positive",
        "source_actual_diff_id": "D001",
        "diff_type": "MODIFY",
        "source_type": "metadata",
        "title_contains": "date",
    }

    first = service.create_expected_diff("case-001", payload)
    second = service.create_expected_diff("case-001", payload)

    first_diffs = first["expected"]["expected_diffs"]
    second_diffs = second["expected"]["expected_diffs"]
    negative_matches = [
        diff
        for diff in second_diffs
        if diff.get("source_actual_diff_id") == "D001"
        and diff.get("review_status") == "REJECTED"
        and diff.get("should_not_match_again") is True
    ]

    assert len(first_diffs) == 4
    assert len(second_diffs) == 4
    assert len(negative_matches) == 1
    assert negative_matches[0]["false_positive_reason"] == "manual_false_positive"
```

- [ ] **Step 2: Add service test proving non-negative creates still append**

Append after the previous test:

```python
def test_create_expected_diff_appends_same_actual_diff_when_not_negative(
    tmp_path: Path,
) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    _make_case(case_root)

    payload = {
        "review_status": "APPROVED",
        "source_actual_diff_id": "D001",
        "diff_type": "MODIFY",
        "source_type": "metadata",
        "title_contains": "date",
    }

    first = service.create_expected_diff("case-001", payload)
    second = service.create_expected_diff("case-001", payload)

    assert len(first["expected"]["expected_diffs"]) == 4
    assert len(second["expected"]["expected_diffs"]) == 5
```

- [ ] **Step 3: Add API test for repeated negative POST**

Append this test after `test_create_and_delete_quality_expected_diff` in `backend/tests/test_api_quality.py`:

```python
def test_create_quality_expected_diff_is_idempotent_for_same_negative_actual_diff(
    quality_service: QualityWorkbenchService,
) -> None:
    _make_case(quality_service.case_root)
    client = TestClient(app)
    payload = {
        "review_status": "REJECTED",
        "should_not_match_again": True,
        "false_positive_reason": "manual_false_positive",
        "source_actual_diff_id": "D001",
        "diff_type": "MODIFY",
        "source_type": "metadata",
        "title_contains": "date",
    }

    first_response = client.post(
        "/api/quality/cases/case-001/expected-diffs",
        json=payload,
    )
    second_response = client.post(
        "/api/quality/cases/case-001/expected-diffs",
        json=payload,
    )

    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 200, second_response.text
    first = first_response.json()
    second = second_response.json()
    negative_matches = [
        diff
        for diff in second["expected"]["expected_diffs"]
        if diff.get("source_actual_diff_id") == "D001"
        and diff.get("review_status") == "REJECTED"
        and diff.get("should_not_match_again") is True
    ]

    assert len(first["expected"]["expected_diffs"]) == 3
    assert len(second["expected"]["expected_diffs"]) == 3
    assert negative_matches == [
        {
            "review_status": "REJECTED",
            "should_not_match_again": True,
            "false_positive_reason": "manual_false_positive",
            "source_actual_diff_id": "D001",
            "diff_type": "MODIFY",
            "source_type": "metadata",
            "title_contains": "date",
        }
    ]
```

- [ ] **Step 4: Run backend focused tests and verify RED**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py -k "idempotent_for_same_negative_actual_diff or appends_same_actual_diff_when_not_negative" -v
```

Expected: the idempotency test fails because duplicate negative payload currently appends. The non-negative append test should pass.

Run:

```bash
cd backend
python -m pytest tests/test_api_quality.py -k "idempotent_for_same_negative_actual_diff" -v
```

Expected: API idempotency test fails because duplicate negative POST currently appends.

## Task 4: Implement Backend Idempotency

**Files:**
- Modify: `backend/app/services/quality_workbench.py`

- [ ] **Step 1: Update create_expected_diff to use the normalized payload once**

Replace:

```python
        expected_diffs.append(_allowed_expected_diff(payload))
        _write_json_atomic(case_dir / "expected.json", expected)
        return self.get_case(case_id)
```

with:

```python
        next_diff = _allowed_expected_diff(payload)
        if _is_negative_expected_diff(next_diff) and _has_same_negative_expected_diff(
            expected_diffs,
            next_diff["source_actual_diff_id"],
        ):
            return self.get_case(case_id)

        expected_diffs.append(next_diff)
        _write_json_atomic(case_dir / "expected.json", expected)
        return self.get_case(case_id)
```

- [ ] **Step 2: Add helper functions near other private helpers**

Add these functions near `_allowed_expected_diff` in `backend/app/services/quality_workbench.py`:

```python
def _is_negative_expected_diff(diff: dict[str, Any]) -> bool:
    source_actual_diff_id = diff.get("source_actual_diff_id")
    return (
        diff.get("review_status") == "REJECTED"
        and diff.get("should_not_match_again") is True
        and isinstance(source_actual_diff_id, str)
        and len(source_actual_diff_id) > 0
    )


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

- [ ] **Step 3: Run backend focused tests and verify GREEN**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py -k "idempotent_for_same_negative_actual_diff or appends_same_actual_diff_when_not_negative" -v
```

Expected: selected service tests pass.

Run:

```bash
cd backend
python -m pytest tests/test_api_quality.py -k "idempotent_for_same_negative_actual_diff" -v
```

Expected: selected API test passes.

## Task 5: Update SOP And Run Full Verification

**Files:**
- Modify: `docs/golden_set_regression_sop.md`

- [ ] **Step 1: Update the negative Golden Set UI workflow docs**

In `docs/golden_set_regression_sop.md`, inside section `## 从 actual diff 标为负向误报`, after the JSON example and before `注意事项：`, insert:

```markdown
重复保护：

- 同一条 actual diff 只需要标注一次。
- 工作台会对已标注的 actual diff 显示 `已标为负向误报`，并禁用按钮。
- 后端会对相同 `source_actual_diff_id` 的负向创建请求做幂等保护；重复请求不会追加第二条 expected diff。
- 如果历史 case 已经存在重复负向样本，本阶段不会自动删除，需要人工清理。
```

- [ ] **Step 2: Verify SOP text**

Run:

```bash
rg "已标为负向误报|幂等保护|source_actual_diff_id|历史 case 已经存在重复负向样本" docs/golden_set_regression_sop.md -n
```

Expected: output includes the new duplicate protection text.

- [ ] **Step 3: Run frontend focused tests**

Run:

```bash
cd frontend
npm test -- QualityWorkbenchPage
```

Expected: `QualityWorkbenchPage.test.tsx` passes.

- [ ] **Step 4: Run frontend API/workbench tests**

Run:

```bash
cd frontend
npm test -- api.quality QualityWorkbenchPage
```

Expected: `api.quality.test.ts` and `QualityWorkbenchPage.test.tsx` pass.

- [ ] **Step 5: Run frontend build**

Run:

```bash
cd frontend
npm run build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 6: Run backend quality workbench tests**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py -v
```

Expected: all quality workbench service tests pass.

- [ ] **Step 7: Run backend quality API tests**

Run:

```bash
cd backend
python -m pytest tests/test_api_quality.py -v
```

Expected: all quality API tests pass.

## Task 6: Review, Commit, And Handoff

**Files:**
- Modify: `frontend/src/pages/QualityWorkbenchPage.test.tsx`
- Modify: `frontend/src/pages/QualityWorkbenchPage.tsx`
- Modify: `backend/tests/test_quality_workbench.py`
- Modify: `backend/app/services/quality_workbench.py`
- Modify: `backend/tests/test_api_quality.py`
- Modify: `docs/golden_set_regression_sop.md`

- [ ] **Step 1: Review changed files**

Run:

```bash
git diff -- frontend/src/pages/QualityWorkbenchPage.test.tsx frontend/src/pages/QualityWorkbenchPage.tsx backend/tests/test_quality_workbench.py backend/app/services/quality_workbench.py backend/tests/test_api_quality.py docs/golden_set_regression_sop.md
```

Expected: diff contains only Phase 4C-4 idempotency changes plus any existing changes in these same files if they were already dirty before execution. Do not revert unrelated existing changes.

- [ ] **Step 2: Check worktree status**

Run:

```bash
git status --short
```

Expected: touched files include the six files in this plan. Existing unrelated dirty files may remain.

- [ ] **Step 3: Stage only Phase 4C-4 implementation files**

Run:

```bash
git add frontend/src/pages/QualityWorkbenchPage.test.tsx frontend/src/pages/QualityWorkbenchPage.tsx backend/tests/test_quality_workbench.py backend/app/services/quality_workbench.py backend/tests/test_api_quality.py docs/golden_set_regression_sop.md
```

Expected: no `storage/` files are staged.

- [ ] **Step 4: Verify staged files**

Run:

```bash
git diff --cached --name-only
```

Expected:

```text
backend/app/services/quality_workbench.py
backend/tests/test_api_quality.py
backend/tests/test_quality_workbench.py
docs/golden_set_regression_sop.md
frontend/src/pages/QualityWorkbenchPage.test.tsx
frontend/src/pages/QualityWorkbenchPage.tsx
```

- [ ] **Step 5: Commit implementation**

Run:

```bash
git commit -m "Add negative golden set idempotency"
```

Expected: commit succeeds on the current branch.

- [ ] **Step 6: Final handoff**

Final response should include:

```text
已完成 Phase 4C-4：负向 Golden Set 去重与幂等保护。

主要变化：
- ActualDiffList 对已负向标注的 actual diff 显示“已标为负向误报”并禁用。
- 后端 create_expected_diff 对重复负向样本做幂等保护。
- 重复 POST 返回 200 且不追加 expected diff。
- SOP 补充重复保护说明。

验证：
- npm test -- QualityWorkbenchPage
- npm test -- api.quality QualityWorkbenchPage
- npm run build
- python -m pytest tests/test_quality_workbench.py -v
- python -m pytest tests/test_api_quality.py -v
```

## Self-Review Checklist

- Spec coverage:
  - 前端识别已负向标注 actual diff：Task 1、Task 2。
  - 已标注按钮显示 `已标为负向误报` 并禁用：Task 1、Task 2。
  - pending 状态保持 `标注中...`：Task 2。
  - 后端重复负向请求幂等：Task 3、Task 4。
  - 非负向创建仍 append：Task 3、Task 4。
  - API 重复 POST 返回 200 且不追加：Task 3、Task 4。
  - SOP 说明重复保护和历史重复边界：Task 5。

- Placeholder scan:
  - The plan contains no unresolved placeholder markers.
  - The `标注中...` text is an intentional UI label, not an unfinished placeholder.

- Type consistency:
  - Frontend still uses existing `ExpectedDiff` and `QualityActualDiffSummary` fields.
  - Backend helper names match call sites: `_is_negative_expected_diff` and `_has_same_negative_expected_diff`。
  - No new API request or response type is introduced.
