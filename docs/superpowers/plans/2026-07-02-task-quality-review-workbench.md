# Task Quality Review Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only task review panel to the quality workbench so an existing `storage/tasks/<task_id>/task.json` can be replayed through the current quality filter and inspected for retained versus suppressed historical diffs.

**Architecture:** Extend `QualityWorkbenchService` with `review_task()`, expose it through `GET /api/quality/tasks/{task_id}/review`, then add a React panel inside the existing quality workbench page. The backend only reads the task snapshot and runs `DiffQualityProcessor`; it must not rerun OCR, matcher, full comparison, or write task/golden files.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, React 19, TypeScript, Vite, Vitest, Testing Library.

---

## Scope

In scope:

- Read `storage/tasks/<task_id>/task.json`.
- Deserialize historical `diffs` into `DiffItem`.
- Replay only `DiffQualityProcessor`.
- Return historical, retained, and suppressed diff counts.
- Return retained and suppressed diff summaries.
- Return suppression reasons from quality decisions.
- Return debug artifact existence flags.
- Add a “任务复盘” panel in `QualityWorkbenchPage`.
- Document the workflow in Chinese.

Out of scope:

- Re-running OCR, document preparation, matcher, diff builder, or full comparison.
- Writing `task.json`, `expected.json`, `actual.json`, or golden set files.
- PDF side-by-side rendering or bbox annotation.
- Cross-task trend statistics.

## Files

- Modify `backend/app/services/quality_workbench.py`
- Modify `backend/app/api_quality_schemas.py`
- Modify `backend/app/api_quality.py`
- Modify `backend/tests/test_quality_workbench.py`
- Modify `backend/tests/test_api_quality.py`
- Modify `frontend/src/types.ts`
- Modify `frontend/src/lib/api.ts`
- Modify `frontend/src/lib/api.quality.test.ts`
- Modify `frontend/src/pages/QualityWorkbenchPage.tsx`
- Modify `frontend/src/pages/QualityWorkbenchPage.test.tsx`
- Modify `frontend/src/styles.css`
- Modify `docs/golden_set_regression_sop.md`

---

### Task 1: Backend Service And API

**Files:**
- Modify: `backend/app/services/quality_workbench.py`
- Modify: `backend/app/api_quality_schemas.py`
- Modify: `backend/app/api_quality.py`
- Modify: `backend/tests/test_quality_workbench.py`
- Modify: `backend/tests/test_api_quality.py`

- [ ] **Step 1: Add failing service tests**

Add service tests that create a temporary task with two diffs: one short OCR noise diff that should be suppressed and one real metadata diff that should be retained.

Expected assertions:

- `historical_diff_count == 2`
- `retained_diff_count == 1`
- `suppressed_diff_count == 1`
- suppressed diff is `D001`
- suppression reason is `clause_ocr_noise`
- unsafe task id raises `InvalidQualityWorkbenchIdError`
- missing task raises `QualityTaskNotFoundError`

- [ ] **Step 2: Implement `QualityWorkbenchService.review_task()`**

Implementation requirements:

- Reuse `_task_dir(task_id)` so safe id validation is identical to export.
- Read only `task.json`.
- Validate each historical diff with `DiffItem.model_validate(diff)`.
- Process with `DiffQualityProcessor().process(historical_diffs)`.
- Build retained diffs from replay result.
- Build suppressed diffs by comparing historical ids against retained ids.
- Include `quality_decisions` from `replay.to_debug_payload()`.
- Include debug flags for:
  - `debug/diff_quality.json`
  - `debug/diff_decisions.json`
  - `debug/ocr_quality.json`
  - `debug/clause_matches.json`
- Do not write any files.

- [ ] **Step 3: Add response schemas**

Add these Pydantic models in `backend/app/api_quality_schemas.py`:

- `QualityTaskReviewDiffResponse`
- `QualityTaskSuppressedDiffResponse`
- `QualityDebugArtifactSummaryResponse`
- `QualityDecisionSummaryResponse`
- `QualityTaskReviewResponse`

- [ ] **Step 4: Add API route**

Add:

```python
@router.get("/tasks/{task_id:path}/review", response_model=QualityTaskReviewResponse)
def review_quality_task(...):
    ...
```

Route behavior:

- success returns service result.
- `InvalidQualityWorkbenchIdError` maps to 400.
- `QualityTaskNotFoundError` maps to 404.

- [ ] **Step 5: Add API tests**

Add tests for:

- success response includes suppressed diff and reason.
- unsafe id returns 400.
- missing task returns 404.

- [ ] **Step 6: Verify backend task**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py -k "review_task" -v
python -m pytest tests/test_api_quality.py -k "review_quality_task" -v
python -m ruff check app/api_quality.py app/api_quality_schemas.py app/services/quality_workbench.py tests/test_quality_workbench.py tests/test_api_quality.py
```

Expected:

- selected pytest tests pass.
- ruff reports no issues.

---

### Task 2: Frontend Task Review Panel

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.quality.test.ts`
- Modify: `frontend/src/pages/QualityWorkbenchPage.tsx`
- Modify: `frontend/src/pages/QualityWorkbenchPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Add frontend API client test**

Add a test that verifies:

```ts
await getQualityTaskReview("task/with space");
expect(fetchMock).toHaveBeenCalledWith(toApiUrl("/api/quality/tasks/task%2Fwith%20space/review"));
```

- [ ] **Step 2: Add frontend types and API function**

Add types matching backend schema:

- `QualityTaskReviewDiff`
- `QualityTaskSuppressedDiff`
- `QualityDecisionSummary`
- `QualityDebugArtifactSummary`
- `QualityTaskReviewResponse`

Add:

```ts
export async function getQualityTaskReview(taskId: string): Promise<QualityTaskReviewResponse>
```

- [ ] **Step 3: Add UI tests**

Add tests that verify:

- task id input labelled `任务 ID`
- clicking `加载任务复盘` calls `getQualityTaskReview("task-001")`
- success renders `历史 10`, `保留 6`, `抑制 4`, `D007`, and `clause_ocr_noise`
- failure renders backend error text

- [ ] **Step 4: Implement UI panel**

Add a “任务复盘” section inside `QualityWorkbenchPage`, independent from selected golden case state.

State:

- `taskReviewId`
- `taskReview`
- `taskReviewError`
- `isLoadingTaskReview`
- request id ref to ignore stale responses

Render:

- input and load button
- loading disabled state
- error alert
- summary metrics
- suppressed diff list
- retained diff list

- [ ] **Step 5: Add CSS**

Add compact styles for:

- `.quality-task-review-form`
- `.quality-task-review-body`
- `.quality-task-review-metrics`
- `.quality-task-review-list`

Keep the styling consistent with existing quality workbench panels.

- [ ] **Step 6: Verify frontend task**

Run:

```bash
cd frontend
npm test -- api.quality QualityWorkbenchPage
npm run build
```

Expected:

- tests pass.
- TypeScript build passes.

---

### Task 3: Documentation And End-To-End Verification

**Files:**
- Modify: `docs/golden_set_regression_sop.md`

- [ ] **Step 1: Document Chinese workflow**

Add a section named `任务级误报复盘` explaining:

- open `/quality/workbench`
- input the `<task_id>` part from `storage/tasks/<task_id>`
- the workbench reads `task.json`
- only current quality filtering is replayed
- OCR, matcher, full comparison, and file writes are not executed
- users should inspect historical, retained, and suppressed counts
- users can confirm whether expected false positives are now suppressed before deciding whether to export a golden set

- [ ] **Step 2: Run focused backend verification**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py tests/test_api_quality.py -v
python -m compileall app tests scripts
```

Expected:

- tests pass.
- compileall exits 0.

- [ ] **Step 3: Run focused frontend verification**

Run:

```bash
cd frontend
npm test -- api.quality QualityWorkbenchPage
npm run build
```

Expected:

- tests pass.
- build passes.

- [ ] **Step 4: Replay acceptance task through service**

Run a Python snippet that calls:

```python
QualityWorkbenchService(
    case_root=Path("tests/fixtures/ocr_compare_cases"),
    task_root=Path("../storage/tasks"),
    output_root=Path(".ocr-compare-quality"),
).review_task("fb67bf36-73bd-48c3-a7b3-59696ed4fb12")
```

Expected:

- `historical_diff_count == 10`
- `retained_diff_count == 6`
- `suppressed_diff_count == 4`
- `D005`, `D007`, `D010`, and `D013` appear in `suppressed_diffs`

- [ ] **Step 5: Review diff**

Run:

```bash
git diff -- backend/app/services/quality_workbench.py backend/app/api_quality_schemas.py backend/app/api_quality.py backend/tests/test_quality_workbench.py backend/tests/test_api_quality.py frontend/src/types.ts frontend/src/lib/api.ts frontend/src/lib/api.quality.test.ts frontend/src/pages/QualityWorkbenchPage.tsx frontend/src/pages/QualityWorkbenchPage.test.tsx frontend/src/styles.css docs/golden_set_regression_sop.md
```

Expected:

- no unrelated file rewrites.
- no changes to `storage/tasks`.
- endpoint remains read-only.

---

## Completion Criteria

- `GET /api/quality/tasks/{task_id}/review` returns read-only task review.
- Quality workbench can load a task id and show retained/suppressed diff counts.
- Acceptance task `fb67bf36-73bd-48c3-a7b3-59696ed4fb12` reports `10 -> 6 retained + 4 suppressed`.
- `D005`, `D007`, `D010`, and `D013` are suppressed.
- No full comparison rerun is introduced.
- No task or golden set files are written by task review.
