# Quality Workbench Backend Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backend foundation for a visual quality workbench: list gold cases, export draft cases, inspect case detail, and edit `expected.json` safely through `/api/quality`.

**Architecture:** Add a focused `quality_workbench` service that wraps existing script functions and owns path safety, JSON read/write, and summary shaping. Add a separate FastAPI router and Pydantic schemas for `/api/quality`, then register the router in `app.main`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, existing `backend/scripts/export_ocr_compare_gold_case.py`, existing fixture format under `backend/tests/fixtures/ocr_compare_cases`.

---

## File Structure

- Create `backend/app/api_quality_schemas.py`
  - Pydantic request/response models for quality workbench endpoints.
- Create `backend/app/services/quality_workbench.py`
  - Path-safe service for case discovery, export, case detail, expected diff create/update/delete.
- Create `backend/app/api_quality.py`
  - FastAPI HTTP adapter only.
- Modify `backend/app/main.py`
  - Include the new quality router.
- Create `backend/tests/test_quality_workbench.py`
  - Unit tests for service behavior and path safety.
- Create `backend/tests/test_api_quality.py`
  - API tests through `TestClient`.

Do not modify existing command-line scripts except if a test exposes a real bug in reusable function behavior.

---

### Task 1: Quality Workbench Service Read Model

**Files:**
- Create: `backend/app/services/quality_workbench.py`
- Test: `backend/tests/test_quality_workbench.py`

- [ ] **Step 1: Write failing tests for case listing and invalid IDs**

Add this to `backend/tests/test_quality_workbench.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.quality_workbench import (
    InvalidQualityWorkbenchIdError,
    QualityWorkbenchService,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_case(case_root: Path, case_id: str = "case-001") -> Path:
    case_dir = case_root / case_id
    _write_json(
        case_dir / "expected.json",
        {
            "case_id": case_id,
            "source_task_id": "task-001",
            "source_files": {
                "original_filename": "original.pdf",
                "compare_filename": "compare.pdf",
            },
            "expected_diffs": [
                {"review_status": "APPROVED", "title_contains": "date"},
                {"review_status": "DRAFT", "title_contains": "amount"},
                {"review_status": "REJECTED", "title_contains": "footer"},
            ],
        },
    )
    _write_json(
        case_dir / "actual.json",
        {
            "task_id": "task-001",
            "status": "COMPLETED",
            "stage": "已完成",
            "progress_percent": 100,
            "created_at": "2026-06-30T00:00:00+00:00",
            "updated_at": "2026-06-30T00:01:00+00:00",
            "original_filename": "original.pdf",
            "compare_filename": "compare.pdf",
            "diffs": [
                {"diff_id": "D001", "title": "date", "diff_type": "MODIFY", "source_type": "metadata"},
                {"diff_id": "D002", "title": "amount", "diff_type": "ADD", "source_type": "clause"},
            ],
        },
    )
    (case_dir / "README.md").write_text("# OCR Compare Gold Case: case-001\n", encoding="utf-8")
    return case_dir


def test_list_cases_summarizes_annotation_counts(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    _make_case(case_root)

    cases = service.list_cases()

    assert len(cases) == 1
    assert cases[0]["case_id"] == "case-001"
    assert cases[0]["source_task_id"] == "task-001"
    assert cases[0]["approved_expected_count"] == 1
    assert cases[0]["draft_expected_count"] == 1
    assert cases[0]["rejected_expected_count"] == 1
    assert cases[0]["actual_diff_count"] == 2
    assert cases[0]["has_actual_json"] is True
    assert cases[0]["has_source_pdfs"] is False


def test_quality_workbench_rejects_path_traversal_ids(tmp_path: Path) -> None:
    service = QualityWorkbenchService(
        case_root=tmp_path / "cases",
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )

    with pytest.raises(InvalidQualityWorkbenchIdError):
        service.get_case("../secret")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py::test_list_cases_summarizes_annotation_counts tests/test_quality_workbench.py::test_quality_workbench_rejects_path_traversal_ids -v
```

Expected: FAIL with `ModuleNotFoundError` or import error for `app.services.quality_workbench`.

- [ ] **Step 3: Implement minimal read service**

Create `backend/app/services/quality_workbench.py`:

```python
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class QualityWorkbenchError(Exception):
    """Base error for quality workbench operations."""


class InvalidQualityWorkbenchIdError(QualityWorkbenchError):
    """Raised when a case, task, or run id is unsafe."""


class QualityCaseNotFoundError(QualityWorkbenchError):
    """Raised when a gold case does not exist."""


class QualityWorkbenchService:
    def __init__(self, *, case_root: Path, task_root: Path, output_root: Path) -> None:
        self.case_root = case_root
        self.task_root = task_root
        self.output_root = output_root

    def list_cases(self) -> list[dict[str, Any]]:
        if not self.case_root.exists():
            return []
        cases = []
        for case_dir in sorted(self.case_root.iterdir()):
            if case_dir.is_dir() and (case_dir / "expected.json").exists():
                cases.append(self._case_summary(case_dir))
        return cases

    def get_case(self, case_id: str) -> dict[str, Any]:
        case_dir = self._case_dir(case_id)
        if not case_dir.exists():
            raise QualityCaseNotFoundError(f"gold case not found: {case_id}")
        expected = _read_json(case_dir / "expected.json")
        actual = _read_json(case_dir / "actual.json") if (case_dir / "actual.json").exists() else {}
        readme = (case_dir / "README.md").read_text(encoding="utf-8") if (case_dir / "README.md").exists() else ""
        return {
            "summary": self._case_summary(case_dir),
            "readme": readme,
            "expected": expected,
            "actual_diffs": [_actual_diff_summary(item) for item in actual.get("diffs", [])],
        }

    def _case_summary(self, case_dir: Path) -> dict[str, Any]:
        expected = _read_json(case_dir / "expected.json")
        actual = _read_json(case_dir / "actual.json") if (case_dir / "actual.json").exists() else {}
        expected_diffs = expected.get("expected_diffs", [])
        return {
            "case_id": case_dir.name,
            "source_task_id": str(expected.get("source_task_id") or actual.get("task_id") or ""),
            "original_filename": str((expected.get("source_files") or {}).get("original_filename") or actual.get("original_filename") or ""),
            "compare_filename": str((expected.get("source_files") or {}).get("compare_filename") or actual.get("compare_filename") or ""),
            "approved_expected_count": _count_status(expected_diffs, {"", "APPROVED"}),
            "draft_expected_count": _count_status(expected_diffs, {"DRAFT"}),
            "rejected_expected_count": _count_status(expected_diffs, {"REJECTED"}),
            "actual_diff_count": len(actual.get("diffs", [])) if isinstance(actual.get("diffs", []), list) else 0,
            "has_actual_json": (case_dir / "actual.json").exists(),
            "has_source_pdfs": (case_dir / "original.pdf").exists() and (case_dir / "compare.pdf").exists(),
        }

    def _case_dir(self, case_id: str) -> Path:
        safe_id = _validate_safe_id(case_id)
        path = (self.case_root / safe_id).resolve()
        root = self.case_root.resolve()
        if root != path and root not in path.parents:
            raise InvalidQualityWorkbenchIdError(f"unsafe id: {case_id}")
        return path


def _validate_safe_id(value: str) -> str:
    if not value or not SAFE_ID_RE.fullmatch(value):
        raise InvalidQualityWorkbenchIdError(f"unsafe id: {value}")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _count_status(items: list[dict[str, Any]], statuses: set[str]) -> int:
    count = 0
    for item in items:
        status = str(item.get("review_status") or "")
        if status in statuses:
            count += 1
    return count


def _actual_diff_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "diff_id": str(item.get("diff_id", "")),
        "diff_type": str(item.get("diff_type", "")),
        "source_type": str(item.get("source_type", "")),
        "title": str(item.get("title", "")),
        "quality_status": str(item.get("quality_status", "")),
        "review_flags": list(item.get("review_flags", [])) if isinstance(item.get("review_flags"), list) else [],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py::test_list_cases_summarizes_annotation_counts tests/test_quality_workbench.py::test_quality_workbench_rejects_path_traversal_ids -v
```

Expected: PASS.

---

### Task 2: Export and Expected Diff Mutation Service

**Files:**
- Modify: `backend/app/services/quality_workbench.py`
- Modify: `backend/tests/test_quality_workbench.py`

- [ ] **Step 1: Add failing service tests for export and expected diff updates**

Append to `backend/tests/test_quality_workbench.py`:

```python
def _make_task(task_root: Path, task_id: str = "task-001") -> Path:
    task_dir = task_root / task_id
    _write_json(
        task_dir / "task.json",
        {
            "task_id": task_id,
            "status": "COMPLETED",
            "stage": "已完成",
            "progress_percent": 100,
            "created_at": "2026-06-30T00:00:00+00:00",
            "updated_at": "2026-06-30T00:01:00+00:00",
            "original_filename": "original.pdf",
            "compare_filename": "compare.pdf",
            "diffs": [
                {
                    "diff_id": "D001",
                    "diff_type": "MODIFY",
                    "source_type": "metadata",
                    "title": "签订日期",
                    "original_text": "2026年4月 日",
                    "compare_text": "2026年4月21日",
                    "review_flags": ["CRITICAL_VALUE_CHANGE"],
                }
            ],
        },
    )
    return task_dir


def test_export_case_creates_draft_case_from_task(tmp_path: Path) -> None:
    task_root = tmp_path / "tasks"
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=task_root,
        output_root=tmp_path / ".ocr-compare-quality",
    )
    _make_task(task_root)

    summary = service.export_case(task_id="task-001", case_id="case-001", force=False)

    assert summary["case_id"] == "case-001"
    assert summary["expected_diff_count"] == 1
    expected = json.loads((case_root / "case-001" / "expected.json").read_text(encoding="utf-8"))
    assert expected["expected_diffs"][0]["review_status"] == "DRAFT"


def test_update_expected_diff_writes_allowed_fields(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    _make_case(case_root)

    detail = service.update_expected_diff(
        "case-001",
        0,
        {
            "review_status": "APPROVED",
            "title_contains": "签订日期",
            "original_contains": "2026年4月 日",
            "compare_contains": "2026年4月21日",
            "ignored": "not persisted",
        },
    )

    first = detail["expected"]["expected_diffs"][0]
    assert first["review_status"] == "APPROVED"
    assert first["title_contains"] == "签订日期"
    assert "ignored" not in first


def test_create_and_delete_expected_diff(tmp_path: Path) -> None:
    case_root = tmp_path / "cases"
    service = QualityWorkbenchService(
        case_root=case_root,
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )
    _make_case(case_root)

    service.create_expected_diff(
        "case-001",
        {
            "review_status": "APPROVED",
            "diff_type": "MODIFY",
            "source_type": "metadata",
            "title_contains": "合同编号",
            "compare_contains": "XX-C-260520",
        },
    )
    detail = service.delete_expected_diff("case-001", 3)

    assert len(detail["expected"]["expected_diffs"]) == 3
    assert all(item.get("title_contains") != "合同编号" for item in detail["expected"]["expected_diffs"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py -v
```

Expected: FAIL because `export_case`, `update_expected_diff`, `create_expected_diff`, and `delete_expected_diff` do not exist.

- [ ] **Step 3: Implement export and mutation methods**

Modify `backend/app/services/quality_workbench.py`:

```python
import tempfile

from scripts.export_ocr_compare_gold_case import export_gold_case
```

Add classes:

```python
class QualityTaskNotFoundError(QualityWorkbenchError):
    """Raised when a source compare task does not exist."""


class QualityExpectedDiffNotFoundError(QualityWorkbenchError):
    """Raised when an expected diff index does not exist."""
```

Add methods inside `QualityWorkbenchService`:

```python
    def export_case(self, *, task_id: str, case_id: str, force: bool = False) -> dict[str, Any]:
        safe_task_id = _validate_safe_id(task_id)
        task_dir = self.task_root / safe_task_id
        if not (task_dir / "task.json").exists():
            raise QualityTaskNotFoundError(f"task not found: {task_id}")
        return export_gold_case(task_dir, self._case_dir(case_id), force=force)

    def update_expected_diff(self, case_id: str, index: int, patch: dict[str, Any]) -> dict[str, Any]:
        expected = self._expected_payload(case_id)
        expected_diffs = _expected_diffs(expected)
        if index < 0 or index >= len(expected_diffs):
            raise QualityExpectedDiffNotFoundError(f"expected diff not found: {index}")
        updated = dict(expected_diffs[index])
        for key, value in patch.items():
            if key in ALLOWED_EXPECTED_DIFF_FIELDS:
                updated[key] = value
        expected_diffs[index] = _clean_expected_diff(updated)
        self._write_expected(case_id, expected)
        return self.get_case(case_id)

    def create_expected_diff(self, case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        expected = self._expected_payload(case_id)
        expected_diffs = _expected_diffs(expected)
        expected_diffs.append(_clean_expected_diff({key: value for key, value in payload.items() if key in ALLOWED_EXPECTED_DIFF_FIELDS}))
        self._write_expected(case_id, expected)
        return self.get_case(case_id)

    def delete_expected_diff(self, case_id: str, index: int) -> dict[str, Any]:
        expected = self._expected_payload(case_id)
        expected_diffs = _expected_diffs(expected)
        if index < 0 or index >= len(expected_diffs):
            raise QualityExpectedDiffNotFoundError(f"expected diff not found: {index}")
        del expected_diffs[index]
        self._write_expected(case_id, expected)
        return self.get_case(case_id)

    def _expected_payload(self, case_id: str) -> dict[str, Any]:
        case_dir = self._case_dir(case_id)
        expected_path = case_dir / "expected.json"
        if not expected_path.exists():
            raise QualityCaseNotFoundError(f"gold case not found: {case_id}")
        return _read_json(expected_path)

    def _write_expected(self, case_id: str, payload: dict[str, Any]) -> None:
        expected_path = self._case_dir(case_id) / "expected.json"
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(expected_path, payload)
```

Add module constants and helpers:

```python
ALLOWED_EXPECTED_DIFF_FIELDS = {
    "diff_type",
    "source_type",
    "title_contains",
    "original_contains",
    "compare_contains",
    "review_status",
    "severity",
    "notes",
    "source_actual_diff_id",
    "expected_evidence",
}


def _expected_diffs(expected: dict[str, Any]) -> list[dict[str, Any]]:
    value = expected.setdefault("expected_diffs", [])
    if not isinstance(value, list):
        raise ValueError("expected_diffs must be a list")
    return value


def _clean_expected_diff(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key in ALLOWED_EXPECTED_DIFF_FIELDS and value not in (None, "")}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    temp_path.replace(path)
```

- [ ] **Step 4: Run service tests to verify they pass**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py -v
```

Expected: PASS.

---

### Task 3: Quality API Schemas and Router

**Files:**
- Create: `backend/app/api_quality_schemas.py`
- Create: `backend/app/api_quality.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_api_quality.py`

- [ ] **Step 1: Write failing API tests**

Create `backend/tests/test_api_quality.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api_quality import get_quality_workbench_service
from app.main import app
from app.services.quality_workbench import QualityWorkbenchService


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _override_service(tmp_path: Path) -> QualityWorkbenchService:
    return QualityWorkbenchService(
        case_root=tmp_path / "cases",
        task_root=tmp_path / "tasks",
        output_root=tmp_path / ".ocr-compare-quality",
    )


def _make_case(case_root: Path) -> None:
    _write_json(
        case_root / "case-001" / "expected.json",
        {
            "case_id": "case-001",
            "source_task_id": "task-001",
            "source_files": {"original_filename": "original.pdf", "compare_filename": "compare.pdf"},
            "expected_diffs": [{"review_status": "DRAFT", "title_contains": "date"}],
        },
    )
    _write_json(
        case_root / "case-001" / "actual.json",
        {
            "task_id": "task-001",
            "status": "COMPLETED",
            "diffs": [{"diff_id": "D001", "title": "date", "diff_type": "MODIFY", "source_type": "metadata"}],
        },
    )


def test_quality_api_lists_cases(tmp_path: Path) -> None:
    service = _override_service(tmp_path)
    _make_case(service.case_root)
    app.dependency_overrides[get_quality_workbench_service] = lambda: service
    client = TestClient(app)

    response = client.get("/api/quality/cases")

    app.dependency_overrides.clear()
    assert response.status_code == 200
    payload = response.json()
    assert payload["cases"][0]["case_id"] == "case-001"
    assert payload["cases"][0]["draft_expected_count"] == 1


def test_quality_api_updates_expected_diff(tmp_path: Path) -> None:
    service = _override_service(tmp_path)
    _make_case(service.case_root)
    app.dependency_overrides[get_quality_workbench_service] = lambda: service
    client = TestClient(app)

    response = client.patch(
        "/api/quality/cases/case-001/expected-diffs/0",
        json={"review_status": "APPROVED", "title_contains": "签订日期"},
    )

    app.dependency_overrides.clear()
    assert response.status_code == 200
    first = response.json()["expected"]["expected_diffs"][0]
    assert first["review_status"] == "APPROVED"
    assert first["title_contains"] == "签订日期"


def test_quality_api_rejects_unsafe_case_id(tmp_path: Path) -> None:
    service = _override_service(tmp_path)
    app.dependency_overrides[get_quality_workbench_service] = lambda: service
    client = TestClient(app)

    response = client.get("/api/quality/cases/..%2Fsecret")

    app.dependency_overrides.clear()
    assert response.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_api_quality.py -v
```

Expected: FAIL because `app.api_quality` does not exist.

- [ ] **Step 3: Add schemas**

Create `backend/app/api_quality_schemas.py`:

```python
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


GoldReviewStatus = Literal["DRAFT", "APPROVED", "REJECTED"]


class QualityCaseSummaryResponse(BaseModel):
    case_id: str
    source_task_id: str = ""
    original_filename: str = ""
    compare_filename: str = ""
    approved_expected_count: int = 0
    draft_expected_count: int = 0
    rejected_expected_count: int = 0
    actual_diff_count: int = 0
    has_actual_json: bool = False
    has_source_pdfs: bool = False


class QualityCaseListResponse(BaseModel):
    cases: list[QualityCaseSummaryResponse]


class QualityActualDiffSummaryResponse(BaseModel):
    diff_id: str = ""
    diff_type: str = ""
    source_type: str = ""
    title: str = ""
    quality_status: str = ""
    review_flags: list[str] = Field(default_factory=list)


class QualityCaseDetailResponse(BaseModel):
    summary: QualityCaseSummaryResponse
    readme: str = ""
    expected: dict[str, Any] = Field(default_factory=dict)
    actual_diffs: list[QualityActualDiffSummaryResponse] = Field(default_factory=list)


class QualityCaseExportRequest(BaseModel):
    task_id: str
    case_id: str
    force: bool = False


class QualityCaseExportResponse(BaseModel):
    case_id: str
    task_id: str
    expected_diff_count: int
    actual_diff_count: int


class ExpectedDiffPatchRequest(BaseModel):
    diff_type: str | None = None
    source_type: str | None = None
    title_contains: str | None = None
    original_contains: str | None = None
    compare_contains: str | None = None
    review_status: GoldReviewStatus | None = None
    severity: str | None = None
    notes: str | None = None
    source_actual_diff_id: str | None = None
    expected_evidence: list[dict[str, Any]] | None = None
```

- [ ] **Step 4: Add router**

Create `backend/app/api_quality.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api_quality_schemas import (
    ExpectedDiffPatchRequest,
    QualityCaseDetailResponse,
    QualityCaseExportRequest,
    QualityCaseExportResponse,
    QualityCaseListResponse,
)
from app.services.quality_workbench import (
    InvalidQualityWorkbenchIdError,
    QualityCaseNotFoundError,
    QualityExpectedDiffNotFoundError,
    QualityTaskNotFoundError,
    QualityWorkbenchService,
)

router = APIRouter(prefix="/api/quality", tags=["quality"])


def get_quality_workbench_service() -> QualityWorkbenchService:
    backend_root = Path(__file__).resolve().parents[1]
    repo_root = backend_root.parent
    return QualityWorkbenchService(
        case_root=backend_root / "tests" / "fixtures" / "ocr_compare_cases",
        task_root=repo_root / "storage" / "tasks",
        output_root=backend_root / ".ocr-compare-quality",
    )


@router.get("/cases", response_model=QualityCaseListResponse)
def list_quality_cases(
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> QualityCaseListResponse:
    return QualityCaseListResponse(cases=service.list_cases())


@router.post("/cases/export", response_model=QualityCaseExportResponse)
def export_quality_case(
    payload: QualityCaseExportRequest,
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> QualityCaseExportResponse:
    try:
        return QualityCaseExportResponse(**service.export_case(task_id=payload.task_id, case_id=payload.case_id, force=payload.force))
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except QualityTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidQualityWorkbenchIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/cases/{case_id}", response_model=QualityCaseDetailResponse)
def get_quality_case(
    case_id: str,
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> QualityCaseDetailResponse:
    try:
        return QualityCaseDetailResponse(**service.get_case(case_id))
    except QualityCaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidQualityWorkbenchIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/cases/{case_id}/expected-diffs/{index}", response_model=QualityCaseDetailResponse)
def update_expected_diff(
    case_id: str,
    index: int,
    payload: ExpectedDiffPatchRequest,
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> QualityCaseDetailResponse:
    try:
        patch = payload.model_dump(exclude_unset=True)
        return QualityCaseDetailResponse(**service.update_expected_diff(case_id, index, patch))
    except QualityCaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QualityExpectedDiffNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidQualityWorkbenchIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/cases/{case_id}/expected-diffs", response_model=QualityCaseDetailResponse)
def create_expected_diff(
    case_id: str,
    payload: ExpectedDiffPatchRequest,
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> QualityCaseDetailResponse:
    try:
        return QualityCaseDetailResponse(**service.create_expected_diff(case_id, payload.model_dump(exclude_unset=True)))
    except QualityCaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidQualityWorkbenchIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/cases/{case_id}/expected-diffs/{index}", response_model=QualityCaseDetailResponse)
def delete_expected_diff(
    case_id: str,
    index: int,
    service: QualityWorkbenchService = Depends(get_quality_workbench_service),
) -> QualityCaseDetailResponse:
    try:
        return QualityCaseDetailResponse(**service.delete_expected_diff(case_id, index))
    except QualityCaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QualityExpectedDiffNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidQualityWorkbenchIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 5: Register router**

Modify `backend/app/main.py`:

```python
from app.api import router as compare_router
from app.api_quality import router as quality_router
```

Then include:

```python
app.include_router(compare_router)
app.include_router(quality_router)
```

- [ ] **Step 6: Run API tests**

Run:

```bash
cd backend
python -m pytest tests/test_api_quality.py -v
```

Expected: PASS.

---

### Task 4: Verification and Documentation Link

**Files:**
- Modify: `docs/golden_set_regression_sop.md`
- Verify: backend test and compile commands

- [ ] **Step 1: Add API workbench note to SOP**

Append this section before `## 相关文档` in `docs/golden_set_regression_sop.md`:

```markdown
## 可视化工作台接口

后端质量工作台接口以 `/api/quality` 为前缀。第一期接口用于支持后续前端可视化页面：

- `GET /api/quality/cases`：列出 golden cases。
- `POST /api/quality/cases/export`：从已完成任务导出 draft golden case。
- `GET /api/quality/cases/{case_id}`：查看单个 case 的 expected/actual 摘要。
- `PATCH /api/quality/cases/{case_id}/expected-diffs/{index}`：更新一条 expected diff 标注。
- `POST /api/quality/cases/{case_id}/expected-diffs`：新增一条 expected diff。
- `DELETE /api/quality/cases/{case_id}/expected-diffs/{index}`：删除一条 expected diff。

这些接口仍然复用现有 golden set 文件格式；命令行脚本和可视化接口可以并行使用。
```

- [ ] **Step 2: Run focused backend tests**

Run:

```bash
cd backend
python -m pytest tests/test_quality_workbench.py tests/test_api_quality.py tests/test_export_ocr_compare_gold_case.py -v
```

Expected: PASS.

- [ ] **Step 3: Run backend syntax check**

Run:

```bash
cd backend
python -m compileall app tests
```

Expected: command exits 0.

- [ ] **Step 4: Run lint on touched backend files if ruff is available**

Run:

```bash
cd backend
python -m ruff check app/api_quality.py app/api_quality_schemas.py app/services/quality_workbench.py tests/test_quality_workbench.py tests/test_api_quality.py
```

Expected: PASS. If `ruff` is not installed, record that lint could not run.

- [ ] **Step 5: Commit only Phase 1 files**

Run:

```bash
git add backend/app/api_quality.py backend/app/api_quality_schemas.py backend/app/services/quality_workbench.py backend/app/main.py backend/tests/test_quality_workbench.py backend/tests/test_api_quality.py docs/golden_set_regression_sop.md docs/superpowers/plans/2026-06-30-quality-workbench-backend-phase1.md
git commit -m "feat: add quality workbench backend"
```

Expected: commit succeeds. Do not stage unrelated existing changes or fixture deletions.

---

## Self-Review

- Spec coverage: This plan implements Phase 1 from `docs/superpowers/specs/2026-06-30-quality-workbench-design.md`: backend service, `/api/quality/cases`, export, case detail, expected diff mutation, path safety, and tests. Evaluation/regression execution endpoints are deliberately deferred to a later phase.
- Placeholder scan: The plan contains no unfinished markers or unspecified implementation steps.
- Type consistency: Service methods return dictionaries shaped for `api_quality_schemas.py`; API tests use dependency override on `get_quality_workbench_service`; route prefix is `/api/quality`.
