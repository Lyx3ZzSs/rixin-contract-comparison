# Quality Workbench Frontend Evidence Gold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first usable visual quality workbench so reviewers can create, inspect, mark, and regression-test golden set cases without manually editing `expected.json`.

**Architecture:** Reuse the existing `/api/quality` backend service for case list/detail/export/create/update/delete. Add missing evaluate/regression backend endpoints as thin adapters over existing quality scripts, then add typed frontend API clients and a React workbench page for case metadata, actual diffs, expected diffs, negative gold marking, manual false-negative entry, and evidence editing.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, React 19, TypeScript, Vite, Vitest, Testing Library, existing CSS in `frontend/src/styles.css`.

---

## Scope

This phase builds **Golden Set Phase 3: 可视化标注工作台与 Evidence-level Gold 初版**.

In scope:

- List gold cases visually.
- Export draft gold case from `task_id`.
- Inspect one case's metadata, actual diffs, expected diffs, and evidence snippets.
- Edit expected diff review fields.
- Mark false positives as negative gold.
- Add manual false-negative expected diffs.
- Edit `expected_evidence` JSON at first, without building a PDF canvas annotator.
- Run quality evaluation from UI.
- Run regression from UI with `dataset_split=regression`.
- Show core metrics and gate failures.

Out of scope:

- Full PDF side-by-side bbox drawing.
- Multi-user auth/permission/audit workflow.
- Label Studio integration.
- Historical trend charts.
- Changing matcher/OCR/diff algorithms.

---

## File Structure

- Modify `backend/app/services/quality_workbench.py`
  - Add methods to run evaluation and regression through existing script functions.
- Modify `backend/app/api_quality_schemas.py`
  - Add evaluate/regression request/response models.
- Modify `backend/app/api_quality.py`
  - Add `/api/quality/evaluate` and `/api/quality/regression`.
- Modify `backend/tests/test_quality_workbench.py`
  - Unit coverage for evaluate/regression service wrappers.
- Modify `backend/tests/test_api_quality.py`
  - API coverage for evaluate/regression endpoints.
- Modify `frontend/src/types.ts`
  - Add quality workbench request/response types.
- Modify `frontend/src/lib/api.ts`
  - Add quality workbench API client functions.
- Create `frontend/src/pages/QualityWorkbenchPage.tsx`
  - Main visual workflow page.
- Create `frontend/src/pages/QualityWorkbenchPage.test.tsx`
  - Frontend behavior tests for loading, marking, adding, and running evaluation.
- Modify `frontend/src/App.tsx`
  - Add route rendering and sidebar entry.
- Modify `frontend/src/lib/routes.ts`
  - Add quality workbench route helpers.
- Modify `frontend/src/lib/state.ts`
  - Add route state variant if routes are locally modeled there.
- Modify `frontend/src/styles.css`
  - Add workbench-specific styles.
- Modify `docs/golden_set_regression_sop.md`
  - Add a visual workbench workflow section.

If `frontend/src/lib/routes.ts` or `frontend/src/lib/state.ts` has different names in the current branch, inspect those files first and follow their existing route pattern.

---

## API Contract

### Evaluate

```http
POST /api/quality/evaluate
```

Request:

```json
{
  "dataset_splits": ["regression"],
  "run_id": "local-eval"
}
```

Response:

```json
{
  "run_id": "local-eval",
  "status": "COMPLETED",
  "report": {
    "case_count": 2,
    "dataset_splits": ["regression"],
    "aggregate": {
      "precision": 1.0,
      "recall": 1.0,
      "false_positive_count": 0,
      "false_negative_count": 0,
      "known_false_positive_regression_count": 0,
      "evidence_hit_rate": 1.0
    },
    "threshold_failures": []
  }
}
```

### Regression

```http
POST /api/quality/regression
```

Request:

```json
{
  "dataset_splits": ["regression"],
  "baseline_name": "current",
  "run_id": "local-regression"
}
```

Response:

```json
{
  "run_id": "local-regression",
  "status": "PASSED",
  "report": {},
  "comparison": {
    "baseline_available": true,
    "failed_gates": []
  }
}
```

The backend may return `status: "FAILED"` when gates fail. HTTP 200 is acceptable for completed regression with failed gates; transport errors should still use HTTP errors.

---

### Task 1: Backend Evaluate and Regression Endpoints

**Files:**
- Modify: `backend/app/services/quality_workbench.py`
- Modify: `backend/app/api_quality_schemas.py`
- Modify: `backend/app/api_quality.py`
- Test: `backend/tests/test_quality_workbench.py`
- Test: `backend/tests/test_api_quality.py`

- [ ] **Step 1: Write service tests for quality evaluation**

Add this to `backend/tests/test_quality_workbench.py`:

```python
def test_evaluate_cases_runs_quality_report_with_dataset_split(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    case_dir = _make_case(case_root)
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    expected["dataset_split"] = "regression"
    expected["expected_diffs"] = [
        {
            "review_status": "APPROVED",
            "diff_type": "MODIFY",
            "source_type": "metadata",
            "title_contains": "date",
        }
    ]
    _write_json(case_dir / "expected.json", expected)

    result = service.evaluate_cases(dataset_splits={"regression"}, run_id="unit-eval")

    assert result["run_id"] == "unit-eval"
    assert result["status"] == "COMPLETED"
    assert result["report"]["case_count"] == 1
    assert result["report"]["dataset_splits"] == ["regression"]
    assert result["report"]["aggregate"]["recall"] == 1.0
```

- [ ] **Step 2: Write service tests for regression wrapper**

Add:

```python
def test_run_regression_returns_gate_status(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    case_dir = _make_case(case_root)
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    expected["dataset_split"] = "regression"
    expected["expected_diffs"] = [
        {
            "review_status": "APPROVED",
            "diff_type": "MODIFY",
            "source_type": "metadata",
            "title_contains": "date",
        }
    ]
    _write_json(case_dir / "expected.json", expected)

    result = service.run_regression(
        dataset_splits={"regression"},
        baseline_name="missing-baseline",
        run_id="unit-regression",
    )

    assert result["run_id"] == "unit-regression"
    assert result["status"] in {"PASSED", "FAILED"}
    assert result["report"]["case_count"] == 1
    assert "comparison" in result
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py::test_evaluate_cases_runs_quality_report_with_dataset_split tests/test_quality_workbench.py::test_run_regression_returns_gate_status -v
```

Expected: FAIL because `QualityWorkbenchService.evaluate_cases` and `run_regression` do not exist.

- [ ] **Step 4: Implement service methods**

Modify `backend/app/services/quality_workbench.py` imports:

```python
from scripts.evaluate_ocr_compare_quality import evaluate_case_root
from scripts.run_quality_regression import (
    apply_gates,
    compare_reports,
    load_baseline,
    load_thresholds,
)
```

If `load_baseline` does not exist in `run_quality_regression.py`, inspect that script and use its existing baseline-loading helper. Do not shell out to the script.

Add methods to `QualityWorkbenchService`:

```python
    def evaluate_cases(
        self,
        *,
        dataset_splits: set[str] | None = None,
        run_id: str = "local-eval",
    ) -> dict[str, Any]:
        report = evaluate_case_root(self.case_root, dataset_splits=dataset_splits)
        return {
            "run_id": run_id,
            "status": "COMPLETED",
            "report": report,
        }

    def run_regression(
        self,
        *,
        dataset_splits: set[str] | None = None,
        baseline_name: str = "current",
        run_id: str = "local-regression",
    ) -> dict[str, Any]:
        report = evaluate_case_root(self.case_root, dataset_splits=dataset_splits)
        baseline_path = self.output_root / "baselines" / f"{baseline_name}.json"
        baseline_report = _read_json(baseline_path) if baseline_path.exists() else None
        comparison = compare_reports(report, baseline_report)
        thresholds = load_thresholds(None)
        failed_gates = apply_gates(report, comparison, thresholds)
        return {
            "run_id": run_id,
            "status": "FAILED" if failed_gates else "PASSED",
            "report": report,
            "comparison": comparison,
        }
```

If the existing script has a canonical baseline file naming convention, use it and adjust the test to match.

- [ ] **Step 5: Add Pydantic schemas**

Modify `backend/app/api_quality_schemas.py`:

```python
class QualityRunRequest(BaseModel):
    dataset_splits: list[Literal["dev", "regression", "holdout", "adversarial", "legacy"]] | None = None
    run_id: str = "local-eval"


class QualityRegressionRequest(BaseModel):
    dataset_splits: list[Literal["dev", "regression", "holdout", "adversarial", "legacy"]] | None = None
    baseline_name: str = "current"
    run_id: str = "local-regression"


class QualityRunResponse(BaseModel):
    run_id: str
    status: str
    report: dict[str, Any] = Field(default_factory=dict)
    comparison: dict[str, Any] | None = None
```

- [ ] **Step 6: Add API endpoints**

Modify `backend/app/api_quality.py` imports and add:

```python
@router.post("/evaluate", response_model=QualityRunResponse)
def evaluate_quality(
    request: QualityRunRequest,
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> dict:
    return service.evaluate_cases(
        dataset_splits=set(request.dataset_splits) if request.dataset_splits else None,
        run_id=request.run_id,
    )


@router.post("/regression", response_model=QualityRunResponse)
def run_quality_regression(
    request: QualityRegressionRequest,
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> dict:
    return service.run_regression(
        dataset_splits=set(request.dataset_splits) if request.dataset_splits else None,
        baseline_name=request.baseline_name,
        run_id=request.run_id,
    )
```

- [ ] **Step 7: Add API tests**

Add to `backend/tests/test_api_quality.py`:

```python
def test_evaluate_quality_endpoint(
    quality_service: QualityWorkbenchService,
) -> None:
    _make_case(quality_service.case_root)
    client = TestClient(app)

    response = client.post(
        "/api/quality/evaluate",
        json={"dataset_splits": ["legacy"], "run_id": "api-eval"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["run_id"] == "api-eval"
    assert payload["status"] == "COMPLETED"
    assert payload["report"]["dataset_splits"] == ["legacy"]


def test_quality_regression_endpoint(
    quality_service: QualityWorkbenchService,
) -> None:
    _make_case(quality_service.case_root)
    client = TestClient(app)

    response = client.post(
        "/api/quality/regression",
        json={
            "dataset_splits": ["legacy"],
            "baseline_name": "missing-baseline",
            "run_id": "api-regression",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["run_id"] == "api-regression"
    assert payload["status"] in {"PASSED", "FAILED"}
    assert "comparison" in payload
```

- [ ] **Step 8: Run backend tests**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py tests/test_api_quality.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/quality_workbench.py backend/app/api_quality_schemas.py backend/app/api_quality.py backend/tests/test_quality_workbench.py backend/tests/test_api_quality.py
git commit -m "feat: add quality workbench run endpoints"
```

---

### Task 2: Frontend Quality Workbench Types and API Client

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Test: `frontend/src/lib/api.test.ts` if it exists, otherwise create `frontend/src/lib/api.quality.test.ts`

- [ ] **Step 1: Add frontend types**

Append to `frontend/src/types.ts`:

```ts
export type GoldReviewStatus = "DRAFT" | "APPROVED" | "REJECTED";
export type GoldDatasetSplit = "dev" | "regression" | "holdout" | "adversarial" | "legacy";

export interface QualityCaseSummary {
  case_id: string;
  schema_version: string;
  dataset_split: GoldDatasetSplit | string;
  case_tags: string[];
  baseline_required: boolean;
  source_task_id: string;
  original_filename: string;
  compare_filename: string;
  approved_expected_count: number;
  draft_expected_count: number;
  rejected_expected_count: number;
  actual_diff_count: number;
  has_actual_json: boolean;
  has_source_pdfs: boolean;
}

export interface QualityActualDiffSummary {
  diff_id: string;
  diff_type: DiffType | string;
  source_type: string;
  title: string;
  quality_status: DiffQualityStatus | string;
  review_flags: string[];
}

export interface ExpectedEvidenceItem {
  side?: "original" | "compare" | string;
  page_no?: number;
  page?: number;
  text?: string;
  text_contains?: string;
  bbox?: BBox;
}

export interface ExpectedDiff {
  diff_type?: DiffType | string;
  source_type?: string;
  title_contains?: string;
  original_contains?: string;
  compare_contains?: string;
  review_status?: GoldReviewStatus;
  reviewer?: string;
  reviewed_at?: string;
  severity?: string;
  notes?: string;
  false_positive_reason?: string;
  false_negative_reason?: string;
  should_not_match_again?: boolean;
  source_actual_diff_id?: string;
  expected_evidence?: ExpectedEvidenceItem[];
}

export interface QualityCaseDetail {
  summary: QualityCaseSummary;
  readme: string;
  expected: {
    case_id?: string;
    expected_diffs?: ExpectedDiff[];
    [key: string]: unknown;
  };
  actual_diffs: QualityActualDiffSummary[];
}

export interface QualityCaseListResponse {
  cases: QualityCaseSummary[];
}

export interface QualityCaseExportRequest {
  task_id: string;
  case_id: string;
  force?: boolean;
}

export interface QualityRunRequest {
  dataset_splits?: GoldDatasetSplit[];
  run_id?: string;
}

export interface QualityRegressionRequest extends QualityRunRequest {
  baseline_name?: string;
}

export interface QualityRunResponse {
  run_id: string;
  status: string;
  report: {
    case_count?: number;
    dataset_splits?: string[];
    aggregate?: Record<string, number | string | boolean | Record<string, unknown>>;
    threshold_failures?: string[];
    cases?: Record<string, unknown>[];
    [key: string]: unknown;
  };
  comparison?: {
    baseline_available?: boolean;
    failed_gates?: Array<Record<string, unknown>>;
    [key: string]: unknown;
  } | null;
}
```

- [ ] **Step 2: Add API client functions**

Modify `frontend/src/lib/api.ts` imports and add:

```ts
export async function listQualityCases(): Promise<QualityCaseListResponse> {
  const response = await fetch(toApiUrl("/api/quality/cases"));
  return parseJsonResponse<QualityCaseListResponse>(response);
}

export async function exportQualityCase(payload: QualityCaseExportRequest): Promise<QualityCaseSummary> {
  const response = await fetch(toApiUrl("/api/quality/cases/export"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<QualityCaseSummary>(response);
}

export async function getQualityCase(caseId: string): Promise<QualityCaseDetail> {
  const response = await fetch(toApiUrl(`/api/quality/cases/${encodeURIComponent(caseId)}`));
  return parseJsonResponse<QualityCaseDetail>(response);
}

export async function updateQualityExpectedDiff(
  caseId: string,
  index: number,
  payload: Partial<ExpectedDiff>,
): Promise<QualityCaseDetail> {
  const response = await fetch(toApiUrl(`/api/quality/cases/${encodeURIComponent(caseId)}/expected-diffs/${index}`), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<QualityCaseDetail>(response);
}

export async function createQualityExpectedDiff(
  caseId: string,
  payload: Partial<ExpectedDiff>,
): Promise<QualityCaseDetail> {
  const response = await fetch(toApiUrl(`/api/quality/cases/${encodeURIComponent(caseId)}/expected-diffs`), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<QualityCaseDetail>(response);
}

export async function deleteQualityExpectedDiff(caseId: string, index: number): Promise<QualityCaseDetail> {
  const response = await fetch(toApiUrl(`/api/quality/cases/${encodeURIComponent(caseId)}/expected-diffs/${index}`), {
    method: "DELETE",
  });
  return parseJsonResponse<QualityCaseDetail>(response);
}

export async function evaluateQuality(payload: QualityRunRequest): Promise<QualityRunResponse> {
  const response = await fetch(toApiUrl("/api/quality/evaluate"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<QualityRunResponse>(response);
}

export async function runQualityRegression(payload: QualityRegressionRequest): Promise<QualityRunResponse> {
  const response = await fetch(toApiUrl("/api/quality/regression"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<QualityRunResponse>(response);
}
```

If `exportQualityCase` response type is `QualityCaseExportResponse` from backend, add that type and return it instead of `QualityCaseSummary`.

- [ ] **Step 3: Add API tests**

Create `frontend/src/lib/api.quality.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createQualityExpectedDiff,
  evaluateQuality,
  getQualityCase,
  listQualityCases,
  runQualityRegression,
  updateQualityExpectedDiff,
} from "./api";

describe("quality API client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads quality cases", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ cases: [] }), { status: 200 }),
    );

    await expect(listQualityCases()).resolves.toEqual({ cases: [] });
    expect(fetchMock).toHaveBeenCalledWith("http://127.0.0.1:8000/api/quality/cases");
  });

  it("updates expected diff review fields", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ summary: {}, expected: {}, actual_diffs: [] }), { status: 200 }),
    );

    await updateQualityExpectedDiff("case-1", 0, {
      review_status: "REJECTED",
      should_not_match_again: true,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/quality/cases/case-1/expected-diffs/0",
      expect.objectContaining({ method: "PATCH" }),
    );
  });

  it("creates manual expected diff and runs evaluation", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ summary: {}, expected: {}, actual_diffs: [] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ run_id: "eval", status: "COMPLETED", report: {} }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ run_id: "reg", status: "PASSED", report: {}, comparison: {} }), { status: 200 }));

    await createQualityExpectedDiff("case-1", { review_status: "APPROVED", title_contains: "签订日期" });
    await evaluateQuality({ dataset_splits: ["regression"], run_id: "eval" });
    await runQualityRegression({ dataset_splits: ["regression"], baseline_name: "current", run_id: "reg" });

    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("loads case detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ summary: {}, expected: {}, actual_diffs: [] }), { status: 200 }),
    );

    await expect(getQualityCase("case-1")).resolves.toEqual({
      summary: {},
      expected: {},
      actual_diffs: [],
    });
  });
});
```

- [ ] **Step 4: Run frontend tests**

Run:

```bash
cd frontend
npm test -- api.quality
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/lib/api.ts frontend/src/lib/api.quality.test.ts
git commit -m "feat: add quality workbench frontend API"
```

---

### Task 3: Quality Workbench Page Read Model

**Files:**
- Create: `frontend/src/pages/QualityWorkbenchPage.tsx`
- Create: `frontend/src/pages/QualityWorkbenchPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write page load test**

Create `frontend/src/pages/QualityWorkbenchPage.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getQualityCase, listQualityCases } from "../lib/api";
import { QualityWorkbenchPage } from "./QualityWorkbenchPage";

vi.mock("../lib/api", () => ({
  listQualityCases: vi.fn(),
  getQualityCase: vi.fn(),
  updateQualityExpectedDiff: vi.fn(),
  createQualityExpectedDiff: vi.fn(),
  deleteQualityExpectedDiff: vi.fn(),
  exportQualityCase: vi.fn(),
  evaluateQuality: vi.fn(),
  runQualityRegression: vi.fn(),
}));

const caseSummary = {
  case_id: "case-001",
  schema_version: "1.1",
  dataset_split: "regression",
  case_tags: ["metadata"],
  baseline_required: true,
  source_task_id: "task-001",
  original_filename: "original.pdf",
  compare_filename: "compare.pdf",
  approved_expected_count: 1,
  draft_expected_count: 1,
  rejected_expected_count: 0,
  actual_diff_count: 2,
  has_actual_json: true,
  has_source_pdfs: false,
};

const caseDetail = {
  summary: caseSummary,
  readme: "# Case",
  expected: {
    expected_diffs: [
      {
        review_status: "APPROVED",
        title_contains: "签订日期",
        original_contains: "2026年4月 日",
        compare_contains: "2026年4月21日",
      },
    ],
  },
  actual_diffs: [
    {
      diff_id: "D001",
      diff_type: "MODIFY",
      source_type: "metadata",
      title: "签订日期",
      quality_status: "NORMAL",
      review_flags: [],
    },
  ],
};

describe("QualityWorkbenchPage", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("loads cases and opens the first case", async () => {
    vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
    vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);

    render(<QualityWorkbenchPage />);

    expect(await screen.findByText("质量回归工作台")).toBeInTheDocument();
    expect(await screen.findByText("case-001")).toBeInTheDocument();
    expect(await screen.findByText("签订日期")).toBeInTheDocument();
    expect(screen.getByText("regression")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd frontend
npm test -- QualityWorkbenchPage
```

Expected: FAIL because `QualityWorkbenchPage.tsx` does not exist.

- [ ] **Step 3: Implement read-only page**

Create `frontend/src/pages/QualityWorkbenchPage.tsx`:

```tsx
import { useEffect, useMemo, useState } from "react";

import { getQualityCase, listQualityCases } from "../lib/api";
import type { ExpectedDiff, QualityCaseDetail, QualityCaseSummary } from "../types";

export function QualityWorkbenchPage() {
  const [cases, setCases] = useState<QualityCaseSummary[]>([]);
  const [selectedCaseId, setSelectedCaseId] = useState("");
  const [caseDetail, setCaseDetail] = useState<QualityCaseDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let isCurrent = true;
    setIsLoading(true);
    setError("");
    void listQualityCases()
      .then((payload) => {
        if (!isCurrent) return;
        setCases(payload.cases);
        const first = payload.cases[0]?.case_id ?? "";
        setSelectedCaseId(first);
        if (first) {
          return getQualityCase(first).then((detail) => {
            if (isCurrent) setCaseDetail(detail);
          });
        }
      })
      .catch((err) => {
        if (isCurrent) setError(err instanceof Error ? err.message : "质量工作台加载失败。");
      })
      .finally(() => {
        if (isCurrent) setIsLoading(false);
      });
    return () => {
      isCurrent = false;
    };
  }, []);

  const expectedDiffs = useMemo(
    () => caseDetail?.expected.expected_diffs ?? [],
    [caseDetail],
  );

  function openCase(caseId: string) {
    setSelectedCaseId(caseId);
    setError("");
    void getQualityCase(caseId)
      .then(setCaseDetail)
      .catch((err) => setError(err instanceof Error ? err.message : "gold case 加载失败。"));
  }

  return (
    <section className="quality-workbench" aria-labelledby="quality-workbench-title">
      <header className="quality-workbench-header">
        <div>
          <span>Golden Set</span>
          <h1 id="quality-workbench-title">质量回归工作台</h1>
        </div>
      </header>

      {isLoading ? (
        <div className="quality-state" role="status">正在加载质量用例...</div>
      ) : error ? (
        <div className="quality-state error" role="alert">{error}</div>
      ) : (
        <div className="quality-workbench-layout">
          <aside className="quality-case-list" aria-label="Gold case 列表">
            {cases.map((item) => (
              <button
                key={item.case_id}
                type="button"
                className={item.case_id === selectedCaseId ? "active" : ""}
                onClick={() => openCase(item.case_id)}
              >
                <strong>{item.case_id}</strong>
                <span>{item.dataset_split}</span>
                <small>{item.approved_expected_count} approved / {item.rejected_expected_count} rejected</small>
              </button>
            ))}
          </aside>

          <div className="quality-case-detail">
            {caseDetail ? (
              <>
                <CaseSummary summary={caseDetail.summary} />
                <ActualDiffList diffs={caseDetail.actual_diffs} />
                <ExpectedDiffList diffs={expectedDiffs} />
              </>
            ) : (
              <div className="quality-state">暂无 gold case。</div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function CaseSummary({ summary }: { summary: QualityCaseSummary }) {
  return (
    <section className="quality-section" aria-label="Case 元数据">
      <h2>{summary.case_id}</h2>
      <div className="quality-meta-grid">
        <span>{summary.schema_version}</span>
        <span>{summary.dataset_split}</span>
        <span>{summary.baseline_required ? "baseline required" : "dev only"}</span>
        <span>{summary.actual_diff_count} actual diffs</span>
      </div>
    </section>
  );
}

function ActualDiffList({ diffs }: { diffs: QualityCaseDetail["actual_diffs"] }) {
  return (
    <section className="quality-section" aria-label="Actual diffs">
      <h2>Actual Diffs</h2>
      {diffs.map((diff) => (
        <article className="quality-diff-row" key={diff.diff_id}>
          <strong>{diff.title || diff.diff_id}</strong>
          <span>{diff.diff_type}</span>
          <span>{diff.source_type}</span>
        </article>
      ))}
    </section>
  );
}

function ExpectedDiffList({ diffs }: { diffs: ExpectedDiff[] }) {
  return (
    <section className="quality-section" aria-label="Expected diffs">
      <h2>Expected Diffs</h2>
      {diffs.map((diff, index) => (
        <article className="quality-diff-row" key={`${diff.title_contains ?? "expected"}-${index}`}>
          <strong>{diff.title_contains || diff.source_type || diff.diff_type || "未命名差异"}</strong>
          <span>{diff.review_status ?? "APPROVED"}</span>
          <span>{diff.false_positive_reason || diff.false_negative_reason || diff.severity || ""}</span>
        </article>
      ))}
    </section>
  );
}
```

- [ ] **Step 4: Add minimal styles**

Append to `frontend/src/styles.css`:

```css
.quality-workbench {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.quality-workbench-header,
.quality-section {
  border: 1px solid #d7dde8;
  border-radius: 8px;
  background: #ffffff;
  padding: 16px;
}

.quality-workbench-layout {
  display: grid;
  grid-template-columns: minmax(220px, 300px) minmax(0, 1fr);
  gap: 16px;
}

.quality-case-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.quality-case-list button {
  text-align: left;
  border: 1px solid #d7dde8;
  border-radius: 8px;
  background: #ffffff;
  padding: 12px;
}

.quality-case-list button.active {
  border-color: #2563eb;
  box-shadow: 0 0 0 1px #2563eb;
}

.quality-case-detail {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 12px;
}

.quality-meta-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.quality-diff-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 120px 160px;
  gap: 8px;
  border-top: 1px solid #edf0f5;
  padding: 10px 0;
}

.quality-state {
  border: 1px solid #d7dde8;
  border-radius: 8px;
  background: #ffffff;
  padding: 24px;
}

@media (max-width: 860px) {
  .quality-workbench-layout,
  .quality-meta-grid,
  .quality-diff-row {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 5: Run page test**

Run:

```bash
cd frontend
npm test -- QualityWorkbenchPage
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/QualityWorkbenchPage.tsx frontend/src/pages/QualityWorkbenchPage.test.tsx frontend/src/styles.css
git commit -m "feat: add quality workbench page"
```

---

### Task 4: Expected Diff Review Actions and Manual False Negatives

**Files:**
- Modify: `frontend/src/pages/QualityWorkbenchPage.tsx`
- Modify: `frontend/src/pages/QualityWorkbenchPage.test.tsx`

- [ ] **Step 1: Add tests for marking negative gold**

Add to `QualityWorkbenchPage.test.tsx`:

```tsx
import userEvent from "@testing-library/user-event";
import { updateQualityExpectedDiff } from "../lib/api";

it("marks an expected diff as negative gold", async () => {
  const user = userEvent.setup();
  vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
  vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
  vi.mocked(updateQualityExpectedDiff).mockResolvedValueOnce({
    ...caseDetail,
    expected: {
      expected_diffs: [
        {
          ...caseDetail.expected.expected_diffs[0],
          review_status: "REJECTED",
          should_not_match_again: true,
          false_positive_reason: "header_footer",
        },
      ],
    },
  });

  render(<QualityWorkbenchPage />);

  await screen.findByText("签订日期");
  await user.click(screen.getByRole("button", { name: "标为误报" }));

  expect(updateQualityExpectedDiff).toHaveBeenCalledWith("case-001", 0, {
    review_status: "REJECTED",
    should_not_match_again: true,
    false_positive_reason: "manual_false_positive",
  });
});
```

- [ ] **Step 2: Add tests for manual false-negative creation**

Add:

```tsx
import { createQualityExpectedDiff } from "../lib/api";

it("adds a manual missed expected diff", async () => {
  const user = userEvent.setup();
  vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
  vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
  vi.mocked(createQualityExpectedDiff).mockResolvedValueOnce({
    ...caseDetail,
    expected: {
      expected_diffs: [
        ...caseDetail.expected.expected_diffs,
        {
          review_status: "APPROVED",
          diff_type: "MODIFY",
          source_type: "metadata",
          title_contains: "合同金额",
          false_negative_reason: "manual_missing_diff",
        },
      ],
    },
  });

  render(<QualityWorkbenchPage />);

  await screen.findByText("签订日期");
  await user.click(screen.getByRole("button", { name: "补录漏报" }));
  await user.type(screen.getByLabelText("漏报标题"), "合同金额");
  await user.click(screen.getByRole("button", { name: "保存漏报" }));

  expect(createQualityExpectedDiff).toHaveBeenCalledWith("case-001", {
    review_status: "APPROVED",
    diff_type: "MODIFY",
    source_type: "metadata",
    title_contains: "合同金额",
    false_negative_reason: "manual_missing_diff",
  });
});
```

- [ ] **Step 3: Implement action handlers**

In `QualityWorkbenchPage.tsx`, import:

```ts
import { createQualityExpectedDiff, updateQualityExpectedDiff } from "../lib/api";
```

Add state:

```tsx
const [missedTitle, setMissedTitle] = useState("");
const [isAddingMissedDiff, setIsAddingMissedDiff] = useState(false);
```

Add handlers:

```tsx
function applyCaseDetail(detail: QualityCaseDetail) {
  setCaseDetail(detail);
  setCases((current) =>
    current.map((item) => (item.case_id === detail.summary.case_id ? detail.summary : item)),
  );
}

function markExpectedDiff(index: number, payload: Partial<ExpectedDiff>) {
  if (!selectedCaseId) return;
  void updateQualityExpectedDiff(selectedCaseId, index, payload).then(applyCaseDetail);
}

function markFalsePositive(index: number) {
  markExpectedDiff(index, {
    review_status: "REJECTED",
    should_not_match_again: true,
    false_positive_reason: "manual_false_positive",
  });
}

function addMissedDiff() {
  if (!selectedCaseId || !missedTitle.trim()) return;
  void createQualityExpectedDiff(selectedCaseId, {
    review_status: "APPROVED",
    diff_type: "MODIFY",
    source_type: "metadata",
    title_contains: missedTitle.trim(),
    false_negative_reason: "manual_missing_diff",
  }).then((detail) => {
    applyCaseDetail(detail);
    setMissedTitle("");
    setIsAddingMissedDiff(false);
  });
}
```

- [ ] **Step 4: Add buttons/forms**

Pass handlers to `ExpectedDiffList` and render per diff:

```tsx
<button type="button" onClick={() => onApprove(index)}>标为真实差异</button>
<button type="button" onClick={() => onReject(index)}>标为误报</button>
<button type="button" onClick={() => onDraft(index)}>待复核</button>
```

Render manual missed diff form:

```tsx
<button type="button" onClick={() => setIsAddingMissedDiff(true)}>补录漏报</button>
{isAddingMissedDiff && (
  <div className="quality-inline-form">
    <label>
      <span>漏报标题</span>
      <input value={missedTitle} onChange={(event) => setMissedTitle(event.target.value)} />
    </label>
    <button type="button" onClick={addMissedDiff}>保存漏报</button>
  </div>
)}
```

- [ ] **Step 5: Run tests**

Run:

```bash
cd frontend
npm test -- QualityWorkbenchPage
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/QualityWorkbenchPage.tsx frontend/src/pages/QualityWorkbenchPage.test.tsx frontend/src/styles.css
git commit -m "feat: edit quality expected diffs"
```

---

### Task 5: Evidence Editing and Run Results

**Files:**
- Modify: `frontend/src/pages/QualityWorkbenchPage.tsx`
- Modify: `frontend/src/pages/QualityWorkbenchPage.test.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Add tests for evidence editing**

Add:

```tsx
it("updates expected evidence from JSON", async () => {
  const user = userEvent.setup();
  vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
  vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
  vi.mocked(updateQualityExpectedDiff).mockResolvedValueOnce(caseDetail);

  render(<QualityWorkbenchPage />);

  await screen.findByText("签订日期");
  await user.click(screen.getByRole("button", { name: "编辑证据" }));
  await user.clear(screen.getByLabelText("证据 JSON"));
  await user.type(
    screen.getByLabelText("证据 JSON"),
    JSON.stringify([{ side: "compare", page_no: 1, text_contains: "2026年4月21日" }]),
  );
  await user.click(screen.getByRole("button", { name: "保存证据" }));

  expect(updateQualityExpectedDiff).toHaveBeenCalledWith("case-001", 0, {
    expected_evidence: [{ side: "compare", page_no: 1, text_contains: "2026年4月21日" }],
  });
});
```

- [ ] **Step 2: Add tests for evaluate/regression buttons**

Add:

```tsx
import { evaluateQuality, runQualityRegression } from "../lib/api";

it("runs evaluation and regression from the workbench", async () => {
  const user = userEvent.setup();
  vi.mocked(listQualityCases).mockResolvedValueOnce({ cases: [caseSummary] });
  vi.mocked(getQualityCase).mockResolvedValueOnce(caseDetail);
  vi.mocked(evaluateQuality).mockResolvedValueOnce({
    run_id: "ui-eval",
    status: "COMPLETED",
    report: {
      aggregate: {
        precision: 1,
        recall: 1,
        known_false_positive_regression_count: 0,
      },
      threshold_failures: [],
    },
  });
  vi.mocked(runQualityRegression).mockResolvedValueOnce({
    run_id: "ui-regression",
    status: "PASSED",
    report: {},
    comparison: { failed_gates: [] },
  });

  render(<QualityWorkbenchPage />);

  await screen.findByText("签订日期");
  await user.click(screen.getByRole("button", { name: "运行评估" }));
  expect(await screen.findByText("Precision 1")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "运行回归" }));
  expect(await screen.findByText("PASSED")).toBeInTheDocument();
});
```

- [ ] **Step 3: Implement evidence editor**

Add state:

```tsx
const [editingEvidenceIndex, setEditingEvidenceIndex] = useState<number | null>(null);
const [evidenceJson, setEvidenceJson] = useState("");
```

Add handlers:

```tsx
function openEvidenceEditor(index: number, diff: ExpectedDiff) {
  setEditingEvidenceIndex(index);
  setEvidenceJson(JSON.stringify(diff.expected_evidence ?? [], null, 2));
}

function saveEvidence() {
  if (editingEvidenceIndex === null || !selectedCaseId) return;
  const parsed = JSON.parse(evidenceJson) as ExpectedDiff["expected_evidence"];
  void updateQualityExpectedDiff(selectedCaseId, editingEvidenceIndex, {
    expected_evidence: parsed,
  }).then((detail) => {
    applyCaseDetail(detail);
    setEditingEvidenceIndex(null);
    setEvidenceJson("");
  });
}
```

Render:

```tsx
{editingEvidenceIndex !== null && (
  <div className="quality-evidence-editor">
    <label>
      <span>证据 JSON</span>
      <textarea value={evidenceJson} onChange={(event) => setEvidenceJson(event.target.value)} />
    </label>
    <button type="button" onClick={saveEvidence}>保存证据</button>
  </div>
)}
```

This first phase intentionally uses JSON editing for evidence to avoid pretending a full PDF bbox annotator exists.

- [ ] **Step 4: Implement run buttons**

Import:

```ts
import { evaluateQuality, runQualityRegression } from "../lib/api";
import type { QualityRunResponse } from "../types";
```

Add state:

```tsx
const [runResult, setRunResult] = useState<QualityRunResponse | null>(null);
```

Add handlers:

```tsx
function runEvaluation() {
  void evaluateQuality({ dataset_splits: ["regression"], run_id: "ui-eval" }).then(setRunResult);
}

function runRegression() {
  void runQualityRegression({
    dataset_splits: ["regression"],
    baseline_name: "current",
    run_id: "ui-regression",
  }).then(setRunResult);
}
```

Render:

```tsx
<div className="quality-runbar">
  <button type="button" onClick={runEvaluation}>运行评估</button>
  <button type="button" onClick={runRegression}>运行回归</button>
</div>
{runResult && (
  <section className="quality-section" aria-label="运行结果">
    <h2>{runResult.status}</h2>
    <span>Precision {String(runResult.report.aggregate?.precision ?? "-")}</span>
    <span>Recall {String(runResult.report.aggregate?.recall ?? "-")}</span>
    <span>Known FP {String(runResult.report.aggregate?.known_false_positive_regression_count ?? "-")}</span>
  </section>
)}
```

- [ ] **Step 5: Run tests**

Run:

```bash
cd frontend
npm test -- QualityWorkbenchPage
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/QualityWorkbenchPage.tsx frontend/src/pages/QualityWorkbenchPage.test.tsx frontend/src/styles.css
git commit -m "feat: add evidence and run controls"
```

---

### Task 6: Navigation, Documentation, and Final Verification

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/lib/routes.ts`
- Modify: `frontend/src/lib/state.ts`
- Modify: `frontend/src/App.test.tsx` if present
- Modify: `docs/golden_set_regression_sop.md`

- [ ] **Step 1: Inspect route model**

Run:

```bash
cd frontend
sed -n '1,220p' src/lib/routes.ts
sed -n '1,220p' src/lib/state.ts
```

Expected: identify the existing route union and navigation event pattern.

- [ ] **Step 2: Add route state**

If route union is in `frontend/src/lib/state.ts`, add:

```ts
| { name: "quality" }
```

Ensure the default route is unchanged.

- [ ] **Step 3: Add route helper**

In `frontend/src/lib/routes.ts`, add:

```ts
export function navigateToQualityWorkbench() {
  window.history.pushState({}, "", "#quality");
  window.dispatchEvent(new PopStateEvent("popstate"));
}
```

If existing route helpers use a different hash/path format, follow the existing format and add `quality` consistently.

- [ ] **Step 4: Render page in App**

Modify `frontend/src/App.tsx`:

```tsx
import { QualityWorkbenchPage } from "./pages/QualityWorkbenchPage";
import { navigateToQualityWorkbench } from "./lib/routes";
```

In route rendering:

```tsx
if (route.name === "quality") {
  return <QualityWorkbenchPage />;
}
```

Add sidebar subnav button:

```tsx
<button
  className={route.name === "quality" ? "active" : ""}
  type="button"
  onClick={navigateToQualityWorkbench}
  aria-current={route.name === "quality" ? "page" : undefined}
>
  <span className="oa-subnav-dot" aria-hidden="true" />
  <span>质量回归</span>
</button>
```

Also include `route.name === "quality"` in the parent active condition.

- [ ] **Step 5: Add navigation test**

If `frontend/src/App.test.tsx` exists, add a test that logs in and clicks `质量回归`. If it does not exist, create a small route parsing test in the route/state test file if present.

Example:

```tsx
it("opens quality workbench from sidebar", async () => {
  const user = userEvent.setup();
  render(<App />);

  await user.type(screen.getByLabelText("用户名"), "admin");
  await user.type(screen.getByLabelText("密码"), "password");
  await user.click(screen.getByRole("button", { name: "登录" }));
  await user.click(screen.getByRole("button", { name: /合同智能对比/ }));
  await user.click(screen.getByRole("button", { name: "质量回归" }));

  expect(await screen.findByText("质量回归工作台")).toBeInTheDocument();
});
```

Adapt labels to the actual login page if needed.

- [ ] **Step 6: Update SOP**

Append to `docs/golden_set_regression_sop.md`:

```markdown
## 可视化质量工作台

完成 Phase 3 后，可以通过前端“质量回归”页面完成常用 golden set 操作：

1. 从 `task_id` 导出 draft gold case。
2. 打开 gold case 查看 actual diffs 和 expected diffs。
3. 将真实差异标为 `APPROVED`。
4. 将误报标为 `REJECTED`，并勾选 `should_not_match_again`。
5. 人工补录系统漏检的 `APPROVED` expected diff。
6. 编辑 `expected_evidence`。
7. 运行 `dataset_split=regression` 的质量评估和回归门禁。

Evidence-level gold 的第一期采用 JSON 编辑方式，适合标注页码、文本片段和 bbox；完整 PDF 双栏框选标注器后续再做。
```

- [ ] **Step 7: Run final verification**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py tests/test_api_quality.py -v
python -m ruff check app/api_quality.py app/api_quality_schemas.py app/services/quality_workbench.py tests/test_quality_workbench.py tests/test_api_quality.py
python -m compileall app tests scripts
```

Expected: PASS.

Run:

```bash
cd frontend
npm test -- QualityWorkbenchPage api.quality
npm run build
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/App.tsx frontend/src/lib/routes.ts frontend/src/lib/state.ts frontend/src/pages/QualityWorkbenchPage.tsx frontend/src/pages/QualityWorkbenchPage.test.tsx frontend/src/styles.css docs/golden_set_regression_sop.md
git commit -m "feat: wire quality workbench navigation"
```

---

## Acceptance Criteria

- Reviewer can open a front-end quality workbench page.
- Reviewer can see gold case list and case metadata.
- Reviewer can inspect actual diffs and expected diffs.
- Reviewer can mark an expected diff as `APPROVED`, `DRAFT`, or `REJECTED`.
- Reviewer can mark a false positive as `REJECTED + should_not_match_again`.
- Reviewer can add a manual false-negative expected diff.
- Reviewer can edit `expected_evidence` in JSON form.
- Reviewer can run quality evaluation from UI.
- Reviewer can run regression from UI using `dataset_split=regression`.
- Backend has API tests for evaluate/regression endpoints.
- Frontend has tests for loading, marking, adding, evidence editing, and run buttons.
- SOP documents the visual workbench flow.

---

## Final Verification Commands

```bash
cd backend
python -m pytest tests/test_quality_workbench.py tests/test_api_quality.py -v
python -m ruff check app/api_quality.py app/api_quality_schemas.py app/services/quality_workbench.py tests/test_quality_workbench.py tests/test_api_quality.py
python -m compileall app tests scripts
```

```bash
cd frontend
npm test -- QualityWorkbenchPage api.quality
npm run build
```

---

## Execution Notes

- Keep existing backend script entry points; API should import functions, not shell out.
- Keep the first frontend version utilitarian and dense; this is an internal review tool, not a landing page.
- Use icons only where they improve scanning; do not add decorative cards or marketing hero sections.
- Do not build a fake PDF annotator. Evidence editing is JSON-first in this phase.
- Do not introduce Label Studio or a new database.
- If the worktree has unrelated staged or modified files, only stage files touched by this plan.

