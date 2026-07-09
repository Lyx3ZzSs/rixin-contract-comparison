# Task Review Draft Golden Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a quality workbench workflow that exports a loaded task review into a draft golden set case and opens that case for manual review.

**Architecture:** Reuse the existing `POST /api/quality/cases/export` API and `exportQualityCase()` frontend client. Extend only the `QualityWorkbenchPage` task review panel with draft case export state, controls, success/error handling, list refresh, and automatic case opening. Update the Chinese golden set SOP to document the workflow and its review requirements.

**Tech Stack:** React 19, TypeScript, Vite, Vitest, Testing Library, existing FastAPI quality API, existing CSS in `frontend/src/styles.css`.

---

## Scope

In scope:

- Add export controls inside the existing “任务复盘” panel.
- Default `case_id` to the loaded task id after a successful task review load.
- Disable export until a task review exists and `case_id` is non-empty.
- Call the existing `exportQualityCase({ task_id, case_id, force: false })`.
- Refresh quality cases after successful export.
- Open the exported case automatically when it appears in the refreshed case list.
- Show export success and failure messages.
- Keep task review results visible when export fails.
- Document the workflow in Chinese.

Out of scope:

- New backend endpoints.
- Automatic `APPROVED` / `REJECTED` labeling.
- Automatic negative golden set generation from suppressed diffs.
- Automatic redaction.
- Force overwrite UI in the first implementation.
- Re-running OCR, matcher, or full comparison.

## File Structure

- Modify `frontend/src/pages/QualityWorkbenchPage.tsx`
  - Import `exportQualityCase`.
  - Add draft export state and request id ref.
  - Add `refreshQualityCases()` helper.
  - Add `exportDraftGoldenCase()` handler.
  - Extend `TaskReviewPanel` props and UI.
- Modify `frontend/src/pages/QualityWorkbenchPage.test.tsx`
  - Import and mock `exportQualityCase`.
  - Add tests for default case id, disabled export, success auto-open, and failure.
- Modify `frontend/src/styles.css`
  - Add compact export form/message styles under existing task review styles.
- Modify `docs/golden_set_regression_sop.md`
  - Add “从任务复盘导出 Draft Golden Set” section.

No backend files should be changed unless existing tests reveal that `export_case` behavior is broken.

---

### Task 1: Frontend Tests For Draft Export

**Files:**
- Modify: `frontend/src/pages/QualityWorkbenchPage.test.tsx`

- [ ] **Step 1: Import and mock `exportQualityCase`**

Add `exportQualityCase` to the API imports:

```ts
import {
  createQualityExpectedDiff,
  evaluateQuality,
  exportQualityCase,
  getQualityCase,
  getQualityTaskReview,
  listQualityCases,
  runQualityRegression,
  updateQualityExpectedDiff,
} from "../lib/api";
```

The existing API mock block already contains this entry:

```ts
exportQualityCase: vi.fn(),
```

- [ ] **Step 2: Add exported case fixtures**

After `secondCaseDetail`, add these fixtures:

```ts
const exportedCaseSummary: QualityCaseSummary = {
  case_id: "task-001",
  schema_version: "1.1",
  dataset_split: "dev",
  case_tags: ["exported", "requires_human_review"],
  baseline_required: false,
  source_task_id: "task-001",
  original_filename: "原合同.pdf",
  compare_filename: "新合同.pdf",
  approved_expected_count: 0,
  draft_expected_count: 10,
  rejected_expected_count: 0,
  actual_diff_count: 10,
  has_actual_json: true,
  has_source_pdfs: false,
};

const exportedCaseDetail: QualityCaseDetail = {
  summary: exportedCaseSummary,
  readme: "exported draft case readme",
  expected: {
    case_id: "task-001",
    source_task_id: "task-001",
    expected_diffs: [
      {
        diff_type: "ADD",
        source_type: "metadata",
        title_contains: "封面字段：合同编号",
        review_status: "DRAFT",
      },
    ],
  },
  actual_diffs: [
    {
      diff_id: "D001",
      diff_type: "ADD",
      source_type: "metadata",
      title: "封面字段：合同编号",
      quality_status: "NEEDS_REVIEW",
      review_flags: ["PAGE_UNRELIABLE"],
    },
  ],
};
```

- [ ] **Step 3: Add test for default draft case id and disabled export before review**

Add this test near existing task review tests:

```tsx
  it("defaults draft case id from the loaded task review and disables export before review", async () => {
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(getQualityTaskReview).mockResolvedValueOnce(taskReviewResponse);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导出 Draft Golden Set" })).toBeDisabled();

    fireEvent.change(screen.getByLabelText("任务 ID"), { target: { value: "task-001" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "加载任务复盘" }));
    });

    expect(await screen.findByText("历史 10")).toBeInTheDocument();
    expect(screen.getByLabelText("case_id")).toHaveValue("task-001");
    expect(screen.getByRole("button", { name: "导出 Draft Golden Set" })).toBeEnabled();
  });
```

- [ ] **Step 4: Add test for successful export, list refresh, and auto-open**

Add this test after the default case id test:

```tsx
  it("exports a loaded task review as a draft golden set and opens the exported case", async () => {
    vi.mocked(listQualityCases)
      .mockResolvedValueOnce({ cases: [caseSummary] })
      .mockResolvedValueOnce({ cases: [caseSummary, exportedCaseSummary] });
    vi.mocked(getQualityCase)
      .mockResolvedValueOnce(caseDetail)
      .mockResolvedValueOnce(exportedCaseDetail);
    vi.mocked(getQualityTaskReview).mockResolvedValueOnce(taskReviewResponse);
    vi.mocked(exportQualityCase).mockResolvedValueOnce({
      case_id: "task-001",
      task_id: "task-001",
      expected_diff_count: 10,
      actual_diff_count: 10,
    });

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("任务 ID"), { target: { value: "task-001" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "加载任务复盘" }));
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "导出 Draft Golden Set" }));
    });

    expect(exportQualityCase).toHaveBeenCalledWith({
      task_id: "task-001",
      case_id: "task-001",
      force: false,
    });
    expect(listQualityCases).toHaveBeenCalledTimes(2);
    expect(getQualityCase).toHaveBeenLastCalledWith("task-001");
    expect(await screen.findByText("已导出 draft golden set：task-001")).toBeInTheDocument();
    expect(await screen.findByText("封面字段：合同编号")).toBeInTheDocument();
  });
```

- [ ] **Step 5: Add test for export failure preserving task review**

Add this test after the success test:

```tsx
  it("shows draft export errors without clearing the loaded task review", async () => {
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
    vi.mocked(getQualityTaskReview).mockResolvedValueOnce(taskReviewResponse);
    vi.mocked(exportQualityCase).mockRejectedValueOnce(new Error("Quality case already exists: task-001"));

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("任务 ID"), { target: { value: "task-001" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "加载任务复盘" }));
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "导出 Draft Golden Set" }));
    });

    expect(await screen.findByText("Quality case already exists: task-001")).toBeInTheDocument();
    expect(screen.getByText("历史 10")).toBeInTheDocument();
    expect(screen.getByText("D007")).toBeInTheDocument();
  });
```

- [ ] **Step 6: Run tests and verify they fail**

Run:

```bash
cd frontend
npm test -- QualityWorkbenchPage
```

Expected: failures because `exportQualityCase` is not imported in the page and the draft export UI does not exist.

---

### Task 2: Frontend Draft Export Implementation

**Files:**
- Modify: `frontend/src/pages/QualityWorkbenchPage.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Import `exportQualityCase`**

In `frontend/src/pages/QualityWorkbenchPage.tsx`, add `exportQualityCase` to the API import list:

```ts
import {
  createQualityExpectedDiff,
  evaluateQuality,
  exportQualityCase,
  getQualityCase,
  getQualityTaskReview,
  listQualityCases,
  runQualityRegression,
  updateQualityExpectedDiff,
} from "../lib/api";
```

- [ ] **Step 2: Add draft export state**

Inside `QualityWorkbenchPage`, add these states near the task review state:

```ts
  const [draftCaseId, setDraftCaseId] = useState("");
  const [draftExportError, setDraftExportError] = useState("");
  const [draftExportMessage, setDraftExportMessage] = useState("");
  const [isExportingDraftCase, setIsExportingDraftCase] = useState(false);
```

Add this ref near `taskReviewRequestIdRef`:

```ts
  const draftExportRequestIdRef = useRef(0);
```

- [ ] **Step 3: Add `refreshQualityCases` helper**

Add this callback before `openCase`:

```ts
  const refreshQualityCases = useCallback(async () => {
    const payload = await listQualityCases();
    return payload.cases;
  }, []);
```

Then update the existing `useEffect` list loading block from:

```ts
    void listQualityCases()
      .then((payload) => {
        if (!isCurrent) return;
        setCases(payload.cases);
        if (payload.cases.length > 0) {
          openCase(payload.cases[0].case_id);
        }
      })
```

to:

```ts
    void refreshQualityCases()
      .then((nextCases) => {
        if (!isCurrent) return;
        setCases(nextCases);
        if (nextCases.length > 0) {
          openCase(nextCases[0].case_id);
        }
      })
```

Update the `useEffect` dependency list:

```ts
  }, [openCase, refreshQualityCases]);
```

- [ ] **Step 4: Populate draft case id when task review loads**

Inside `loadTaskReview()`, after `setTaskReview(result);`, add:

```ts
        setDraftExportError("");
        setDraftExportMessage("");
        setDraftCaseId((current) => current || result.task_id);
```

At the start of `loadTaskReview()`, after `setTaskReview(null);`, add:

```ts
    setDraftExportError("");
    setDraftExportMessage("");
```

- [ ] **Step 5: Add draft export handler**

Add this callback after `loadTaskReview`:

```ts
  const exportDraftGoldenCase = useCallback(async () => {
    if (!taskReview) return;
    const caseId = draftCaseId.trim();
    if (!caseId) return;

    const requestId = draftExportRequestIdRef.current + 1;
    draftExportRequestIdRef.current = requestId;
    setIsExportingDraftCase(true);
    setDraftExportError("");
    setDraftExportMessage("");
    try {
      const exported = await exportQualityCase({
        task_id: taskReview.task_id,
        case_id: caseId,
        force: false,
      });
      if (draftExportRequestIdRef.current !== requestId) return;

      const nextCases = await refreshQualityCases();
      if (draftExportRequestIdRef.current !== requestId) return;

      const exportedCaseId = exported.case_id || caseId;
      setCases(nextCases);
      setDraftExportMessage(`已导出 draft golden set：${exportedCaseId}`);
      if (nextCases.some((qualityCase) => qualityCase.case_id === exportedCaseId)) {
        openCase(exportedCaseId);
      }
    } catch (err) {
      if (draftExportRequestIdRef.current === requestId) {
        setDraftExportError(err instanceof Error ? err.message : "Draft golden set 导出失败。");
      }
    } finally {
      if (draftExportRequestIdRef.current === requestId) {
        setIsExportingDraftCase(false);
      }
    }
  }, [draftCaseId, openCase, refreshQualityCases, taskReview]);
```

- [ ] **Step 6: Pass draft export props into `TaskReviewPanel`**

Update the `TaskReviewPanel` usage:

```tsx
          <TaskReviewPanel
            taskReviewId={taskReviewId}
            taskReview={taskReview}
            taskReviewError={taskReviewError}
            isLoadingTaskReview={isLoadingTaskReview}
            draftCaseId={draftCaseId}
            draftExportError={draftExportError}
            draftExportMessage={draftExportMessage}
            isExportingDraftCase={isExportingDraftCase}
            onChangeDraftCaseId={setDraftCaseId}
            onChangeTaskReviewId={setTaskReviewId}
            onExportDraftGoldenCase={exportDraftGoldenCase}
            onLoadTaskReview={loadTaskReview}
          />
```

- [ ] **Step 7: Extend `TaskReviewPanel` props and render export form**

Update the component signature:

```tsx
function TaskReviewPanel({
  taskReviewId,
  taskReview,
  taskReviewError,
  isLoadingTaskReview,
  draftCaseId,
  draftExportError,
  draftExportMessage,
  isExportingDraftCase,
  onChangeDraftCaseId,
  onChangeTaskReviewId,
  onExportDraftGoldenCase,
  onLoadTaskReview,
}: {
  taskReviewId: string;
  taskReview: QualityTaskReviewResponse | null;
  taskReviewError: string;
  isLoadingTaskReview: boolean;
  draftCaseId: string;
  draftExportError: string;
  draftExportMessage: string;
  isExportingDraftCase: boolean;
  onChangeDraftCaseId: (value: string) => void;
  onChangeTaskReviewId: (value: string) => void;
  onExportDraftGoldenCase: () => void;
  onLoadTaskReview: () => void;
}) {
```

Render `DraftGoldenExportPanel` after task review errors and before the `taskReview` result block, so the export button is visible but disabled before a task review is loaded:

```tsx
          <DraftGoldenExportPanel
            draftCaseId={draftCaseId}
            draftExportError={draftExportError}
            draftExportMessage={draftExportMessage}
            isExportingDraftCase={isExportingDraftCase}
            taskReview={taskReview}
            onChangeDraftCaseId={onChangeDraftCaseId}
            onExportDraftGoldenCase={onExportDraftGoldenCase}
          />
```

Add this component before `TaskSuppressedDiffList`:

```tsx
function DraftGoldenExportPanel({
  draftCaseId,
  draftExportError,
  draftExportMessage,
  isExportingDraftCase,
  taskReview,
  onChangeDraftCaseId,
  onExportDraftGoldenCase,
}: {
  draftCaseId: string;
  draftExportError: string;
  draftExportMessage: string;
  isExportingDraftCase: boolean;
  taskReview: QualityTaskReviewResponse | null;
  onChangeDraftCaseId: (value: string) => void;
  onExportDraftGoldenCase: () => void;
}) {
  return (
    <section className="quality-task-export" aria-labelledby="quality-task-export-title">
      <div>
        <h3 id="quality-task-export-title">导出 Draft Golden Set</h3>
        <p>导出的 expected diff 默认为 DRAFT，需要人工审核后才进入可信回归。</p>
      </div>
      <form
        className="quality-task-export-form"
        onSubmit={(event) => {
          event.preventDefault();
          onExportDraftGoldenCase();
        }}
      >
        <label htmlFor="quality-draft-case-id">case_id</label>
        <input
          id="quality-draft-case-id"
          type="text"
          value={draftCaseId}
          onChange={(event) => onChangeDraftCaseId(event.target.value)}
        />
        <button type="submit" disabled={isExportingDraftCase || !taskReview?.task_id || !draftCaseId.trim()}>
          {isExportingDraftCase ? "导出中" : "导出 Draft Golden Set"}
        </button>
      </form>
      {draftExportMessage && <div className="quality-inline-success">{draftExportMessage}</div>}
      {draftExportError && (
        <div className="quality-inline-error" role="alert">
          {draftExportError}
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 8: Add CSS**

Append these styles near existing `.quality-task-review-*` styles in `frontend/src/styles.css`:

```css
.quality-task-export {
  display: grid;
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--surface-soft);
}

.quality-task-export h3 {
  margin: 0;
  color: #263638;
  font-size: 0.8rem;
  font-weight: 900;
}

.quality-task-export p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 0.74rem;
  font-weight: 800;
}

.quality-task-export-form {
  display: grid;
  grid-template-columns: auto minmax(220px, 1fr) auto;
  gap: 8px;
  align-items: center;
}

.quality-task-export-form label {
  color: var(--muted);
  font-size: 0.74rem;
  font-weight: 900;
}

.quality-task-export-form input {
  width: 100%;
  min-width: 0;
  height: 30px;
  padding: 0 9px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--surface);
  color: var(--text);
  font: inherit;
  font-size: 0.82rem;
  font-weight: 800;
}

.quality-task-export-form button {
  min-height: 30px;
  padding: 0 10px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--teal);
  color: #fff;
  font-size: 0.76rem;
  font-weight: 900;
  cursor: pointer;
}

.quality-task-export-form button:disabled {
  cursor: not-allowed;
  opacity: 0.62;
}

.quality-inline-success {
  padding: 8px 10px;
  border: 1px solid rgba(25, 135, 84, 0.28);
  border-radius: 6px;
  background: rgba(25, 135, 84, 0.08);
  color: #166534;
  font-size: 0.78rem;
  font-weight: 900;
}
```

Inside the existing `@media (max-width: 900px)` block, update the grid selector:

```css
  .quality-task-review-form,
  .quality-task-review-metrics,
  .quality-task-export-form {
    grid-template-columns: 1fr;
  }
```

- [ ] **Step 9: Run UI tests**

Run:

```bash
cd frontend
npm test -- QualityWorkbenchPage
```

Expected: all `QualityWorkbenchPage` tests pass.

- [ ] **Step 10: Run frontend build**

Run:

```bash
cd frontend
npm run build
```

Expected: TypeScript and Vite build pass.

---

### Task 3: Documentation And Final Verification

**Files:**
- Modify: `docs/golden_set_regression_sop.md`

- [ ] **Step 1: Document draft export workflow**

In `docs/golden_set_regression_sop.md`, add this section after “任务级误报复盘” and before “1. 从对比任务导出 draft gold case”:

````markdown
## 从任务复盘导出 Draft Golden Set

当某个历史任务值得沉淀为回归样本时，可以直接在质量工作台中从任务复盘导出 draft golden set。

操作步骤：

```text
1. 打开 /quality/workbench
2. 在“任务复盘”区域输入 task_id
3. 点击“加载任务复盘”
4. 检查历史 diff、保留 diff、抑制 diff
5. 在“导出 Draft Golden Set”区域确认或修改 case_id
6. 点击“导出 Draft Golden Set”
7. 导出成功后，工作台会刷新 case 列表并打开新 case
8. 在 expected diff 列表中继续人工标注
```

导出的 case 仍然只是草稿：

- `actual.json` 是任务实际输出快照。
- `expected.json` 中的 diff 默认是 `DRAFT`。
- `DRAFT` 不进入可信质量指标。
- 必须人工把真实差异标为 `APPROVED`，把误报标为 `REJECTED`。
- 如果希望误报以后不再出现，再设置 `should_not_match_again: true`。

注意事项：

- 导出不会重新执行 OCR、matcher 或完整合同对比。
- 导出不会自动判断差异真假。
- 如果 `case_id` 已存在，工作台会提示冲突；推荐换一个新的 `case_id`，不要直接覆盖已审核 case。
- 包含真实合同内容的 draft case 不能直接提交到仓库；必须先完成脱敏和人工审核。
````

- [ ] **Step 2: Run focused frontend verification**

Run:

```bash
cd frontend
npm test -- api.quality QualityWorkbenchPage
npm run build
```

Expected:

- Quality API and page tests pass.
- Build passes.

- [ ] **Step 3: Run backend export API regression tests**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py -k "export_case" -v
python -m pytest tests/test_api_quality.py -k "export_quality_case" -v
```

Expected:

- Existing export service/API tests pass.

- [ ] **Step 4: Review changed files**

Run:

```bash
git diff -- frontend/src/pages/QualityWorkbenchPage.tsx frontend/src/pages/QualityWorkbenchPage.test.tsx frontend/src/styles.css docs/golden_set_regression_sop.md
```

Expected:

- No backend implementation changes.
- No `force: true` export path in the UI.
- No automatic `APPROVED` / `REJECTED` labeling.
- No writes to `storage/tasks` from frontend code.

---

## Completion Criteria

- A loaded task review can be exported as draft golden set from the quality workbench.
- Draft export uses `exportQualityCase({ task_id, case_id, force: false })`.
- Export success refreshes quality case list and opens the exported case.
- Export failure shows an error and keeps the loaded task review visible.
- UI states clearly that exported expected diffs are `DRAFT` and require manual review.
- Existing backend export tests still pass.
- Frontend tests and build pass.
