# Ignore Header Footer Diffs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a default-off upload option that excludes dedicated header/footer/page-number diff items from comparison results and reports.

**Architecture:** Reuse the existing `CompareOptions` flow that already carries stamp exclusion from frontend form state through FastAPI, queued jobs, persisted tasks, and pipeline stages. The pipeline skips `HeaderFooterComparator.build_diffs()` when `ignore_headers_footers` is enabled, and `SummaryStage` defensively removes any remaining `source_type="header_footer"` diffs before final dedupe and stats.

**Tech Stack:** FastAPI multipart forms, Pydantic models, pytest, React, TypeScript, Vitest, Testing Library.

---

## File Structure

- Modify `backend/app/api.py`: accept the `ignore_headers_footers` form field and include it in `CompareOptions`.
- Modify `backend/app/services/pipeline_stages.py`: skip header/footer diff generation and extend final compare-option filtering.
- Modify `backend/tests/test_api.py`: update the compare creation option persistence regression.
- Modify `backend/tests/test_pipeline.py`: add pipeline tests for skipping and final guard filtering.
- Modify `frontend/src/types.ts`: add `ignoreHeadersFooters` to `CompareContractOptions`.
- Modify `frontend/src/lib/api.ts`: submit `ignore_headers_footers=true` only when enabled.
- Modify `frontend/src/lib/api.test.ts`: cover default omission and enabled submission.
- Modify `frontend/src/pages/UploadPage.tsx`: add checkbox state and submit option.
- Modify `frontend/src/pages/UploadPage.test.tsx`: cover default unchecked state and selected submission payload.
- Modify `frontend/src/styles.css`: reuse/adjust the existing compare option row so two checkboxes lay out cleanly.

## Task 1: Backend API Option Persistence

**Files:**
- Modify: `backend/tests/test_api.py`
- Modify: `backend/app/api.py`

- [ ] **Step 1: Update the failing API test**

In `backend/tests/test_api.py`, rename `test_api_compare_persists_stamp_exclusion_option_only` to `test_api_compare_persists_enabled_exclusion_options`, and change its assertions to expect header/footer and stamp options to persist while punctuation remains disabled.

```python
def test_api_compare_persists_enabled_exclusion_options(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    default_task_runner.stop(wait=True)
    original_autostart = default_task_runner.autostart
    default_task_runner.autostart = False
    original = tmp_path / "original.pdf"
    compare = tmp_path / "compare.pdf"
    make_pdf(original, ["1. Payment", "Buyer shall pay within 30 days."])
    make_pdf(compare, ["1. Payment", "Buyer shall pay within 45 days."])

    try:
        client = TestClient(app)
        with original.open("rb") as original_file, compare.open("rb") as compare_file:
            response = client.post(
                "/api/compare",
                data={
                    "ignore_punctuation": "true",
                    "ignore_headers_footers": "true",
                    "ignore_stamps": "true",
                },
                files={
                    "original_file": ("original.pdf", original_file, "application/pdf"),
                    "compare_file": ("compare.pdf", compare_file, "application/pdf"),
                },
            )
    finally:
        default_task_runner.autostart = original_autostart

    assert response.status_code == 200, response.text
    payload = response.json()
    task_id = payload["task_id"]
    assert "compare_options" not in payload
    task = load_task(task_id)
    assert task.compare_options.ignore_punctuation is False
    assert task.compare_options.ignore_headers_footers is True
    assert task.compare_options.ignore_stamps is True
    job = default_task_runner.latest_job(task_id, task_type="compare")
    assert job.payload["compare_options"] == {
        "ignore_punctuation": False,
        "ignore_headers_footers": True,
        "ignore_stamps": True,
    }
```

- [ ] **Step 2: Run the API test and verify it fails**

Run:

```bash
python -m pytest backend/tests/test_api.py::test_api_compare_persists_enabled_exclusion_options -q
```

Expected: the test fails because `ignore_headers_footers` is still `False` in the saved task or job payload.

- [ ] **Step 3: Implement the FastAPI form option**

In `backend/app/api.py`, update `compare_contracts()` so the signature and options construction match this shape:

```python
@router.post("", response_model=CompareTaskResponse)
async def compare_contracts(
    original_file: UploadFile = File(...),
    compare_file: UploadFile = File(...),
    ignore_stamps: bool = Form(False),
    ignore_headers_footers: bool = Form(False),
) -> CompareTaskResponse:
    task_id = generate_task_id()
    compare_options = CompareOptions(
        ignore_stamps=ignore_stamps,
        ignore_headers_footers=ignore_headers_footers,
    )
```

Keep the existing `compare_options=compare_options` arguments in `create_queued_task()` and `submit_compare()`.

- [ ] **Step 4: Run the API test and verify it passes**

Run:

```bash
python -m pytest backend/tests/test_api.py::test_api_compare_persists_enabled_exclusion_options -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit backend API propagation**

Stage only these files:

```bash
git add backend/app/api.py backend/tests/test_api.py
git commit -m "feat: persist header footer exclusion option"
```

## Task 2: Pipeline Skip and Final Guard

**Files:**
- Modify: `backend/tests/test_pipeline.py`
- Modify: `backend/app/services/pipeline_stages.py`

- [ ] **Step 1: Add the pre-clause skip test**

In `backend/tests/test_pipeline.py`, inside `class TestPreClauseDiffStage`, add this test after `test_keeps_header_footer_diffs`:

```python
    def test_filters_header_footer_diffs_when_option_enabled(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.task.compare_options = CompareOptions(ignore_headers_footers=True)
        original = make_document("正文条款一致。")
        compare = make_document("正文条款一致。")
        original.pages[0].blocks.insert(
            0,
            TextBlock(
                block_id="o_header",
                page_no=1,
                text="合同编号：A-001",
                bbox=BBox(x0=40, y0=20, x1=180, y1=36),
                block_type="header",
            ),
        )
        compare.pages[0].blocks.insert(
            0,
            TextBlock(
                block_id="c_header",
                page_no=1,
                text="合同编号：B-002",
                bbox=BBox(x0=40, y0=20, x1=180, y1=36),
                block_type="header",
            ),
        )
        ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
        ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")

        PreClauseDiffStage().execute(ctx)

        assert ctx.header_footer_diffs == []
```

- [ ] **Step 2: Add the summary guard test**

In `backend/tests/test_pipeline.py`, inside `class TestSummaryStage`, add this test after `test_refreshes_diff_count_and_writes_debug_artifact`:

```python
    def test_filters_header_footer_diffs_when_option_enabled(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.task.compare_options = CompareOptions(ignore_headers_footers=True)
        header_diff = DiffItem(
            diff_id="D001",
            diff_type="MODIFY",
            title="页眉",
            source_type="header_footer",
            original_text="合同编号：A-001",
            compare_text="合同编号：B-002",
        )
        clause_diff = DiffItem(
            diff_id="D002",
            diff_type="MODIFY",
            title="正文",
            source_type="clause",
            original_text="30日",
            compare_text="45日",
        )
        ctx.diffs = [header_diff, clause_diff]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D002"]
        assert ctx.task.diff_count == 1
```

- [ ] **Step 3: Run the pipeline tests and verify they fail**

Run:

```bash
python -m pytest backend/tests/test_pipeline.py::TestPreClauseDiffStage::test_filters_header_footer_diffs_when_option_enabled backend/tests/test_pipeline.py::TestSummaryStage::test_filters_header_footer_diffs_when_option_enabled -q
```

Expected: the pre-clause test fails because header/footer diffs are still generated, and the summary test fails because the final filter only handles `seal`.

- [ ] **Step 4: Implement the pre-clause skip**

In `backend/app/services/pipeline_stages.py`, replace the unconditional header/footer diff generation in `PreClauseDiffStage.execute()` with:

```python
        if task.compare_options.ignore_headers_footers:
            header_footer_diffs: list[DiffItem] = []
            _emit_progress(ctx, 38, self.name, "header_footer_diff_skipped")
        else:
            header_footer_diffs = self.header_footer.build_diffs(original_doc, compare_doc)
            _emit_progress(ctx, 38, self.name, "header_footer_diff_done")
```

Leave the metadata and table `start_index` calculations based on `len(header_footer_diffs)`.

- [ ] **Step 5: Implement the final source-type guard**

In `backend/app/services/pipeline_stages.py`, replace `_filter_compare_option_diffs()` with:

```python
def _filter_compare_option_diffs(task: CompareTask, diffs: list[DiffItem]) -> list[DiffItem]:
    excluded_source_types: set[str] = set()
    if task.compare_options.ignore_stamps:
        excluded_source_types.add("seal")
    if task.compare_options.ignore_headers_footers:
        excluded_source_types.add("header_footer")
    if not excluded_source_types:
        return diffs
    return [diff for diff in diffs if diff.source_type not in excluded_source_types]
```

- [ ] **Step 6: Run the pipeline tests and verify they pass**

Run:

```bash
python -m pytest backend/tests/test_pipeline.py::TestPreClauseDiffStage::test_filters_header_footer_diffs_when_option_enabled backend/tests/test_pipeline.py::TestSummaryStage::test_filters_header_footer_diffs_when_option_enabled -q
```

Expected: `2 passed`.

- [ ] **Step 7: Re-run the existing stamp skip test**

Run:

```bash
python -m pytest backend/tests/test_pipeline.py::TestPreClauseDiffStage::test_filters_stamp_diffs_when_option_enabled -q
```

Expected: `1 passed`.

- [ ] **Step 8: Commit pipeline behavior**

Stage only these files:

```bash
git add backend/app/services/pipeline_stages.py backend/tests/test_pipeline.py
git commit -m "feat: skip header footer diffs when requested"
```

## Task 3: Frontend API Client

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`

- [ ] **Step 1: Update frontend API tests first**

In `frontend/src/lib/api.test.ts`, keep the existing default request test asserting `ignore_headers_footers` is absent. Add this test after the stamp exclusion test:

```ts
  it("sends the header footer exclusion option only when enabled", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ task_id: "task-1", status: "PROCESSING" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await compareContracts(
      new File(["original"], "original.pdf", { type: "application/pdf" }),
      new File(["compare"], "compare.pdf", { type: "application/pdf" }),
      { ignoreStamps: false, ignoreHeadersFooters: true },
    );

    const body = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(body.get("ignore_headers_footers")).toBe("true");
    expect(body.has("ignore_punctuation")).toBe(false);
    expect(body.has("ignore_stamps")).toBe(false);

    vi.unstubAllGlobals();
  });
```

- [ ] **Step 2: Run the frontend API test and verify it fails**

Run:

```bash
cd frontend && npm test -- src/lib/api.test.ts
```

Expected: the new test fails because `ignoreHeadersFooters` is not in the option type and `ignore_headers_footers` is not appended.

- [ ] **Step 3: Extend the frontend option type**

In `frontend/src/types.ts`, replace `CompareContractOptions` with:

```ts
export interface CompareContractOptions {
  ignoreStamps: boolean;
  ignoreHeadersFooters: boolean;
}
```

- [ ] **Step 4: Append the multipart field when enabled**

In `frontend/src/lib/api.ts`, update `compareContracts()`:

```ts
  if (options?.ignoreStamps) {
    formData.append("ignore_stamps", "true");
  }
  if (options?.ignoreHeadersFooters) {
    formData.append("ignore_headers_footers", "true");
  }
```

- [ ] **Step 5: Update the existing stamp API test call**

In `frontend/src/lib/api.test.ts`, update the stamp-only options object to include the new field as false:

```ts
      { ignoreStamps: true, ignoreHeadersFooters: false },
```

- [ ] **Step 6: Run the frontend API test and verify it passes**

Run:

```bash
cd frontend && npm test -- src/lib/api.test.ts
```

Expected: all tests in `src/lib/api.test.ts` pass.

- [ ] **Step 7: Commit frontend API client changes**

Stage only these files:

```bash
git add frontend/src/types.ts frontend/src/lib/api.ts frontend/src/lib/api.test.ts
git commit -m "feat: send header footer exclusion option"
```

## Task 4: Upload Page Option

**Files:**
- Modify: `frontend/src/pages/UploadPage.tsx`
- Modify: `frontend/src/pages/UploadPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Update upload page tests first**

In `frontend/src/pages/UploadPage.test.tsx`, update the render test to assert both options are unchecked:

```ts
    expect(screen.getByLabelText("排除签章区域")).not.toBeChecked();
    expect(screen.getByLabelText("排除页眉页脚差异项")).not.toBeChecked();
```

Update the basic submit assertion:

```ts
    expect(compareContracts).toHaveBeenCalledWith(expect.any(File), expect.any(File), {
      ignoreStamps: false,
      ignoreHeadersFooters: false,
    });
```

Update the stamp selected assertion:

```ts
    expect(compareContracts).toHaveBeenCalledWith(expect.any(File), expect.any(File), {
      ignoreStamps: true,
      ignoreHeadersFooters: false,
    });
```

Add this test after the stamp selected test:

```ts
  it("submits the header footer exclusion option when selected", async () => {
    const user = userEvent.setup();
    render(<UploadPage onTaskCreated={vi.fn()} onOpenRecords={vi.fn()} />);

    await user.upload(screen.getByLabelText("原版文件"), new File(["original"], "original.pdf", { type: "application/pdf" }));
    await user.upload(screen.getByLabelText("新版文件"), new File(["compare"], "compare.pdf", { type: "application/pdf" }));
    await user.click(screen.getByLabelText("排除页眉页脚差异项"));
    await user.click(screen.getByRole("button", { name: "开始对比" }));

    await waitFor(() => expect(compareContracts).toHaveBeenCalled());
    expect(compareContracts).toHaveBeenCalledWith(expect.any(File), expect.any(File), {
      ignoreStamps: false,
      ignoreHeadersFooters: true,
    });
  });
```

- [ ] **Step 2: Run the upload page test and verify it fails**

Run:

```bash
cd frontend && npm test -- src/pages/UploadPage.test.tsx
```

Expected: tests fail because the page does not render `排除页眉页脚差异项` and the submit payload does not include `ignoreHeadersFooters`.

- [ ] **Step 3: Add upload page state and submit payload**

In `frontend/src/pages/UploadPage.tsx`, add state next to `ignoreStamps`:

```ts
  const [ignoreHeadersFooters, setIgnoreHeadersFooters] = useState(false);
```

Update the submit call:

```ts
      const payload = await compareContracts(originalFile, compareFile, { ignoreStamps, ignoreHeadersFooters });
```

- [ ] **Step 4: Render the second checkbox**

In `frontend/src/pages/UploadPage.tsx`, inside `<div className="compare-options" aria-label="排除对比项">`, keep the existing stamp label and add this sibling label:

```tsx
          <label className="compare-option">
            <input
              type="checkbox"
              aria-label="排除页眉页脚差异项"
              checked={ignoreHeadersFooters}
              onChange={(event) => setIgnoreHeadersFooters(event.target.checked)}
            />
            <span>
              <strong>排除页眉页脚差异项</strong>
              <small>不生成页眉、页脚、页码等差异</small>
            </span>
          </label>
```

- [ ] **Step 5: Adjust option row layout for two controls**

In `frontend/src/styles.css`, update `.compare-options`:

```css
.compare-options {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
  margin: 18px 0 0;
}
```

Keep the existing mobile rules that stretch `.compare-option` to full width.

- [ ] **Step 6: Run upload page tests and verify they pass**

Run:

```bash
cd frontend && npm test -- src/pages/UploadPage.test.tsx
```

Expected: all upload page tests pass.

- [ ] **Step 7: Commit upload page changes**

Stage only these files:

```bash
git add frontend/src/pages/UploadPage.tsx frontend/src/pages/UploadPage.test.tsx frontend/src/styles.css
git commit -m "feat: add header footer exclusion control"
```

## Task 5: End-to-End Verification

**Files:**
- No source changes expected unless verification exposes a defect.

- [ ] **Step 1: Run targeted backend tests**

Run:

```bash
python -m pytest backend/tests/test_api.py::test_api_compare_persists_enabled_exclusion_options backend/tests/test_pipeline.py::TestPreClauseDiffStage::test_filters_header_footer_diffs_when_option_enabled backend/tests/test_pipeline.py::TestSummaryStage::test_filters_header_footer_diffs_when_option_enabled backend/tests/test_pipeline.py::TestPreClauseDiffStage::test_filters_stamp_diffs_when_option_enabled -q
```

Expected: all targeted backend tests pass.

- [ ] **Step 2: Run targeted frontend tests**

Run:

```bash
cd frontend && npm test -- src/lib/api.test.ts src/pages/UploadPage.test.tsx
```

Expected: all targeted frontend tests pass.

- [ ] **Step 3: Run backend syntax verification**

Run:

```bash
python -m compileall backend/app backend/tests
```

Expected: command exits with status 0.

- [ ] **Step 4: Run frontend production build**

Run:

```bash
cd frontend && npm run build
```

Expected: TypeScript compile and Vite build complete successfully.

- [ ] **Step 5: Inspect final changed files**

Run:

```bash
git status --short
```

Expected: only intended task files are modified or committed. Existing unrelated dirty files may still appear; do not stage or revert unrelated files.

- [ ] **Step 6: Commit any verification fixes**

If a verification defect required a source change, stage only the file changed for this feature and commit with:

```bash
git add <exact feature file paths>
git commit -m "fix: verify header footer exclusion"
```

If no verification fixes were needed, do not create an empty commit.

## Self-Review

- Spec coverage: the plan covers API form input, task/job persistence, pre-clause skip, final guard filter, frontend request submission, upload UI, tests, and build verification.
- Scope control: the plan does not implement pixel masking, OCR region deletion, comparison-time cropping, punctuation exclusion, or new report schema fields.
- Type consistency: backend uses `ignore_headers_footers`; frontend uses `ignoreHeadersFooters`; final filter excludes `source_type="header_footer"`.
