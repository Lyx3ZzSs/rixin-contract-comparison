# Negative Golden Set Assist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在质量工作台中支持从 actual diff 一键创建人工确认的负向 Golden Set 条目，并把误报样本纳入已知误报回归闭环。

**Architecture:** 前端复用已有 `createQualityExpectedDiff()` API，把当前打开 case 的 `actual_diffs` 转成 `REJECTED + should_not_match_again` 的 expected diff。后端不新增接口，质量评估逻辑不改动，只依赖已有 known false positive regression 统计能力。

**Tech Stack:** React、TypeScript、Vitest、Testing Library、FastAPI 现有质量接口、Markdown 文档。

---

## Scope Check

本计划覆盖 Phase 4C-3 设计文档中的方案 A：从 `ActualDiffList` 一键生成负向 expected diff。

本计划不实现以下能力：

- 从任务复盘 retained diff 直接生成负向样本。
- 批量标注误报。
- false positive reason 下拉选择。
- 自动判断误报。
- 后端新接口。
- 质量评估算法调整。

这些能力没有进入本阶段成功标准。

## File Structure

修改文件：

- `frontend/src/pages/QualityWorkbenchPage.test.tsx`
  - 新增前端行为测试，覆盖 actual diff 创建负向 expected diff、成功刷新、重复点击保护、切换 case stale response、失败错误展示。

- `frontend/src/pages/QualityWorkbenchPage.tsx`
  - 增加 `createNegativeExpectedDiff` handler。
  - 为 `ActualDiffList` 增加 `onCreateNegativeExpectedDiff` 和 `isSavingNegativeExpectedDiff` props。
  - 在 actual diff 行增加“标为负向误报”按钮。
  - 在 `openCase()` 中取消未完成的负向标注请求结果。

- `docs/golden_set_regression_sop.md`
  - 增加“从 actual diff 标为负向误报”的可视化流程。
  - 强调该操作必须基于人工确认。
  - 说明 `known_false_positive_regression_count` 的回归含义。

不修改文件：

- `frontend/src/types.ts`
  - 当前 `QualityActualDiffSummary` 不包含 `original_snippet` 或 `compare_snippet`。本阶段只使用现有字段：`diff_id`、`diff_type`、`source_type`、`title`。

- `frontend/src/lib/api.ts`
  - 已有 `createQualityExpectedDiff(caseId, payload)` 满足需求。

- 后端服务与评估代码
  - 已有 allowed fields 和 known false positive regression 测试覆盖本阶段需求。

## Current Context Notes

开始执行前先运行：

```bash
git status --short
```

预期当前工作区可能包含 Phase 4C-2 遗留的未提交改动，例如：

```text
 M docs/golden_set_regression_sop.md
 M frontend/src/pages/QualityWorkbenchPage.test.tsx
 M frontend/src/pages/QualityWorkbenchPage.tsx
 M frontend/src/styles.css
?? docs/superpowers/plans/2026-07-03-task-review-draft-golden-export.md
?? storage/
```

执行本计划时不要回滚这些改动。只在本计划涉及的文件中做增量修改，并在提交前用 `git diff` 核对变更范围。

## Task 1: Add Failing Tests For Negative Expected Diff Creation

**Files:**
- Modify: `frontend/src/pages/QualityWorkbenchPage.test.tsx`

- [ ] **Step 1: Add test for creating rejected expected diff from actual diff**

在 `describe("QualityWorkbenchPage", () => {` 测试套件内，放在 `"marks an expected diff as negative gold"` 测试之后，新增测试：

```tsx
  it("creates a negative expected diff from an actual diff", async () => {
    const rejectedDetail: QualityCaseDetail = {
      ...caseDetail,
      summary: {
        ...caseSummary,
        approved_expected_count: 1,
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
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(createQualityExpectedDiff).mockResolvedValueOnce(rejectedDetail);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("实际日期变更")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "标为负向误报" }));
    });

    expect(createQualityExpectedDiff).toHaveBeenCalledWith("case-001", {
      review_status: "REJECTED",
      should_not_match_again: true,
      false_positive_reason: "manual_false_positive",
      source_actual_diff_id: "diff-001",
      diff_type: "MODIFY",
      source_type: "clause",
      title_contains: "实际日期变更",
    });
    expect(await screen.findByText("REJECTED")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Add test for duplicate submit protection**

在同一测试文件中继续新增：

```tsx
  it("does not create a negative expected diff twice while save is pending", async () => {
    const createResult = createDeferred<QualityCaseDetail>();
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(createQualityExpectedDiff).mockReturnValueOnce(createResult.promise);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("实际日期变更")).toBeInTheDocument();

    const button = screen.getByRole("button", { name: "标为负向误报" });
    fireEvent.click(button);
    fireEvent.click(button);

    expect(createQualityExpectedDiff).toHaveBeenCalledTimes(1);

    await act(async () => {
      createResult.resolve(caseDetail);
      await createResult.promise;
    });
  });
```

- [ ] **Step 3: Add test for stale success after switching cases**

在同一测试文件中继续新增：

```tsx
  it("does not apply a stale negative expected diff result after switching cases", async () => {
    const createResult = createDeferred<QualityCaseDetail>();
    const rejectedDetail: QualityCaseDetail = {
      ...caseDetail,
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
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary, secondCaseSummary] });
    vi.mocked(getQualityCase).mockImplementation((caseId) => {
      if (caseId === "case-001") {
        return Promise.resolve(caseDetail);
      }
      if (caseId === "case-002") {
        return Promise.resolve(secondCaseDetail);
      }
      return Promise.reject(new Error(`Unexpected case id: ${caseId}`));
    });
    vi.mocked(createQualityExpectedDiff).mockReturnValueOnce(createResult.promise);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("实际日期变更")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "标为负向误报" }));
    fireEvent.click(screen.getByRole("button", { name: /case-002/ }));

    expect(await screen.findByText("付款金额")).toBeInTheDocument();

    await act(async () => {
      createResult.resolve(rejectedDetail);
      await createResult.promise;
    });

    expect(screen.getByText("付款金额")).toBeInTheDocument();
    expect(screen.queryByText("实际日期变更")).not.toBeInTheDocument();
  });
```

- [ ] **Step 4: Add test for create failure**

在同一测试文件中继续新增：

```tsx
  it("shows an error when creating a negative expected diff fails", async () => {
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(createQualityExpectedDiff).mockRejectedValueOnce(new Error("negative create failed"));

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("实际日期变更")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "标为负向误报" }));
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("negative create failed");
  });
```

- [ ] **Step 5: Run the focused failing tests**

Run:

```bash
cd frontend
npm test -- QualityWorkbenchPage
```

Expected: tests referencing `"标为负向误报"` fail because the button and handler do not exist. Existing tests should still compile until the new assertions run.

## Task 2: Implement Actual Diff Negative Marking In Workbench

**Files:**
- Modify: `frontend/src/pages/QualityWorkbenchPage.tsx`

- [ ] **Step 1: Add negative expected diff state and refs**

In `QualityWorkbenchPage()`, after existing missed diff state:

```tsx
  const [isSavingNegativeExpectedDiff, setIsSavingNegativeExpectedDiff] = useState(false);
```

After `missedDiffRequestIdRef`:

```tsx
  const negativeExpectedDiffRequestInFlightRef = useRef(false);
  const negativeExpectedDiffRequestIdRef = useRef(0);
```

- [ ] **Step 2: Cancel negative creation when opening a different case**

Inside `openCase`, in the reset block near `missedDiffRequestInFlightRef.current = false;`, add:

```tsx
    negativeExpectedDiffRequestInFlightRef.current = false;
    negativeExpectedDiffRequestIdRef.current += 1;
    setIsSavingNegativeExpectedDiff(false);
```

- [ ] **Step 3: Add the createNegativeExpectedDiff handler**

Add this callback after `markFalsePositive` and before `saveMissedDiff`:

```tsx
  const createNegativeExpectedDiff = useCallback(
    async (diff: QualityActualDiffSummary) => {
      if (!detail) return;
      if (negativeExpectedDiffRequestInFlightRef.current) return;

      const requestCaseId = detail.summary.case_id;
      const saveRequestId = negativeExpectedDiffRequestIdRef.current + 1;
      negativeExpectedDiffRequestIdRef.current = saveRequestId;
      negativeExpectedDiffRequestInFlightRef.current = true;
      setIsSavingNegativeExpectedDiff(true);
      setError("");

      try {
        const nextDetail = await createQualityExpectedDiff(requestCaseId, {
          review_status: "REJECTED",
          should_not_match_again: true,
          false_positive_reason: "manual_false_positive",
          source_actual_diff_id: diff.diff_id,
          diff_type: diff.diff_type,
          source_type: diff.source_type,
          title_contains: diff.title || diff.diff_id,
        });
        if (
          negativeExpectedDiffRequestIdRef.current === saveRequestId &&
          selectedCaseIdRef.current === requestCaseId &&
          nextDetail.summary.case_id === requestCaseId
        ) {
          applyCaseDetail(nextDetail);
        }
      } catch (err) {
        if (
          negativeExpectedDiffRequestIdRef.current === saveRequestId &&
          selectedCaseIdRef.current === requestCaseId
        ) {
          setError(err instanceof Error ? err.message : "负向误报标注失败。");
        }
      } finally {
        if (
          negativeExpectedDiffRequestIdRef.current === saveRequestId &&
          selectedCaseIdRef.current === requestCaseId
        ) {
          negativeExpectedDiffRequestInFlightRef.current = false;
          setIsSavingNegativeExpectedDiff(false);
        }
      }
    },
    [applyCaseDetail, detail],
  );
```

- [ ] **Step 4: Pass handler and save state to ActualDiffList**

Replace:

```tsx
              <ActualDiffList diffs={detail.actual_diffs} />
```

with:

```tsx
              <ActualDiffList
                diffs={detail.actual_diffs}
                isSavingNegativeExpectedDiff={isSavingNegativeExpectedDiff}
                onCreateNegativeExpectedDiff={createNegativeExpectedDiff}
              />
```

- [ ] **Step 5: Extend ActualDiffList props and UI**

Replace the current `ActualDiffList` function with:

```tsx
function ActualDiffList({
  diffs,
  isSavingNegativeExpectedDiff,
  onCreateNegativeExpectedDiff,
}: {
  diffs: QualityActualDiffSummary[];
  isSavingNegativeExpectedDiff: boolean;
  onCreateNegativeExpectedDiff: (diff: QualityActualDiffSummary) => void;
}) {
  return (
    <section className="quality-section" aria-labelledby="quality-actual-title">
      <h2 id="quality-actual-title">ActualDiffList</h2>
      {diffs.length === 0 ? (
        <div className="quality-state">暂无实际差异</div>
      ) : (
        diffs.map((diff) => (
          <article className="quality-diff-row" key={diff.diff_id}>
            <div>
              <strong>{diff.title || diff.diff_id}</strong>
              <span>{diff.diff_id}</span>
            </div>
            <dl>
              <dt>Type</dt>
              <dd>{diff.diff_type}</dd>
              <dt>Source</dt>
              <dd>{diff.source_type}</dd>
              <dt>Status</dt>
              <dd>{diff.quality_status}</dd>
            </dl>
            {diff.review_flags.length > 0 && <small>{diff.review_flags.join(" / ")}</small>}
            <div className="quality-diff-actions">
              <button
                type="button"
                onClick={() => onCreateNegativeExpectedDiff(diff)}
                disabled={isSavingNegativeExpectedDiff}
              >
                {isSavingNegativeExpectedDiff ? "标注中..." : "标为负向误报"}
              </button>
            </div>
          </article>
        ))
      )}
    </section>
  );
}
```

- [ ] **Step 6: Run the focused tests**

Run:

```bash
cd frontend
npm test -- QualityWorkbenchPage
```

Expected: the new negative marking tests pass. If existing unrelated tests fail, inspect whether the failure comes from this file's current dirty state before changing broader code.

## Task 3: Document The Negative Golden Set UI Workflow

**Files:**
- Modify: `docs/golden_set_regression_sop.md`

- [ ] **Step 1: Add a subsection after “从任务复盘导出 Draft Golden Set”**

Insert this section before `## 1. 从对比任务导出 draft gold case`:

````markdown
## 从 actual diff 标为负向误报

当 draft golden set 已导出并打开后，可以在质量工作台中直接把人工确认的 actual diff 沉淀为负向 Golden Set。

操作步骤：

```text
1. 打开 /quality/workbench
2. 打开一个已有 gold case，或从任务复盘导出 draft golden set
3. 在 ActualDiffList 中逐条查看系统实际输出
4. 人工确认某条 actual diff 是误报
5. 点击“标为负向误报”
6. 在 ExpectedDiffList 中确认新增的 REJECTED 条目
7. 运行“运行评估”或“运行回归”
8. 如果该误报再次出现，报告会记录 known_false_positive_regression_count
```

该操作会创建一条 expected diff，并默认写入：

```json
{
  "review_status": "REJECTED",
  "should_not_match_again": true,
  "false_positive_reason": "manual_false_positive",
  "source_actual_diff_id": "actual diff id",
  "diff_type": "actual diff type",
  "source_type": "actual source type",
  "title_contains": "actual diff title"
}
```

注意事项：

- “标为负向误报”只表示人工已经确认该 actual diff 是误报。
- 系统不会自动判断某条 diff 是否为误报。
- 不要把真实合同差异标成负向样本，否则后续回归会倾向于压掉真实差异。
- 包含敏感合同内容的 case 仍需先脱敏，再提交到仓库。
````

- [ ] **Step 2: Verify Markdown structure**

Run:

```bash
rg "从 actual diff 标为负向误报|known_false_positive_regression_count|标为负向误报" docs/golden_set_regression_sop.md -n
```

Expected: output includes the new section heading, the UI button text, and `known_false_positive_regression_count`.

## Task 4: Run Regression Checks

**Files:**
- No source edits.

- [ ] **Step 1: Run focused frontend tests**

Run:

```bash
cd frontend
npm test -- QualityWorkbenchPage
```

Expected: all `QualityWorkbenchPage` tests pass.

- [ ] **Step 2: Run quality API and workbench tests together**

Run:

```bash
cd frontend
npm test -- api.quality QualityWorkbenchPage
```

Expected: API quality tests and workbench tests pass.

- [ ] **Step 3: Run frontend production build**

Run:

```bash
cd frontend
npm run build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 4: Run backend known false positive tests**

Run:

```bash
cd backend
python -m pytest tests/test_evaluate_ocr_compare_quality.py -k "known_false_positive" -v
```

Expected: tests covering known false positive regression pass.

- [ ] **Step 5: Run backend quality regression gate tests**

Run:

```bash
cd backend
python -m pytest tests/test_run_quality_regression.py -k "known_false_positive" -v
```

Expected: tests covering `max_known_false_positive_regression_count` and `max_known_false_positive_regression_increase` pass.

## Task 5: Review, Commit, And Handoff

**Files:**
- Modify: `frontend/src/pages/QualityWorkbenchPage.test.tsx`
- Modify: `frontend/src/pages/QualityWorkbenchPage.tsx`
- Modify: `docs/golden_set_regression_sop.md`

- [ ] **Step 1: Review changed files**

Run:

```bash
git diff -- frontend/src/pages/QualityWorkbenchPage.test.tsx frontend/src/pages/QualityWorkbenchPage.tsx docs/golden_set_regression_sop.md
```

Expected: diff only contains Phase 4C-3 negative Golden Set assist changes plus any existing Phase 4C-2 changes already present before execution. Do not revert unrelated existing changes.

- [ ] **Step 2: Check worktree status**

Run:

```bash
git status --short
```

Expected: touched files include the three files in this plan. Untracked `storage/` may still exist and must not be staged.

- [ ] **Step 3: Stage only plan-related files**

Run:

```bash
git add frontend/src/pages/QualityWorkbenchPage.test.tsx frontend/src/pages/QualityWorkbenchPage.tsx docs/golden_set_regression_sop.md
```

Expected: only these files are staged.

- [ ] **Step 4: Verify staged files**

Run:

```bash
git diff --cached --name-only
```

Expected:

```text
docs/golden_set_regression_sop.md
frontend/src/pages/QualityWorkbenchPage.test.tsx
frontend/src/pages/QualityWorkbenchPage.tsx
```

- [ ] **Step 5: Commit implementation**

Run:

```bash
git commit -m "Add negative golden set assist workflow"
```

Expected: commit succeeds on the current branch.

- [ ] **Step 6: Final handoff**

Final response should include:

```text
已完成 Phase 4C-3：负向 Golden Set 辅助标注与误报回归闭环。

主要变化：
- ActualDiffList 支持“标为负向误报”。
- 创建 REJECTED + should_not_match_again 的 expected diff。
- 成功后刷新当前 case。
- 文档补充可视化操作流程。

验证：
- npm test -- QualityWorkbenchPage
- npm test -- api.quality QualityWorkbenchPage
- npm run build
- python -m pytest tests/test_evaluate_ocr_compare_quality.py -k "known_false_positive" -v
- python -m pytest tests/test_run_quality_regression.py -k "known_false_positive" -v
```

## Self-Review Checklist

- Spec coverage:
  - `ActualDiffList` 一键创建负向 expected diff：Task 1、Task 2。
  - `REJECTED + should_not_match_again + false_positive_reason`：Task 1、Task 2。
  - `source_actual_diff_id`、`diff_type`、`source_type`、`title_contains`：Task 1、Task 2。
  - 成功后刷新当前 case：Task 1、Task 2。
  - stale response 保护：Task 1、Task 2。
  - 不新增后端接口：Task 2 复用 `createQualityExpectedDiff()`。
  - 不自动判断误报：Task 3 文档明确人工确认。
  - known false positive regression 验证：Task 4。

- Placeholder scan:
  - The plan contains no unresolved placeholder markers.
  - The remaining `...` tokens are TypeScript object spread syntax in executable snippets.
  - No undefined function names.
  - No omitted test cases for core success and failure paths.

- Type consistency:
  - `QualityActualDiffSummary` uses existing fields only: `diff_id`、`diff_type`、`source_type`、`title`。
  - Payload type remains `Partial<ExpectedDiff>` via existing `createQualityExpectedDiff()`。
  - Button text in tests exactly matches implementation: `标为负向误报`。
