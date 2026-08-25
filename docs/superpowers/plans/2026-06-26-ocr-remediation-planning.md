# OCR Remediation Planning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Phase 3A: a planning-only OCR remediation layer that converts runtime OCR quality risks into auditable remediation actions and exposes them through backend APIs and the result page.

**Architecture:** Add additive Pydantic models to `backend/app/models.py`, implement a deterministic `OcrRemediationPlanner`, insert an `OcrRemediationStage` after `OcrQualityStage`, persist/debug the summary, and expose it through existing presenter/quality responses. The first implementation only plans actions and marks unresolved risks; it does not rewrite diff text, retry OCR, or perform table/evidence repair.

**Tech Stack:** FastAPI backend, Pydantic models, existing comparison pipeline, pytest, React/Vite frontend, Vitest.

---

## Scope

This plan implements Phase 3A only.

Included:

- Remediation action and summary models.
- Deterministic action planning from OCR quality profiles and diff flags.
- Pipeline stage in planning-only mode.
- Debug artifact `ocr_remediation.json`.
- Additive API/quality response fields.
- Frontend type updates and compact badges/status display.
- Unit, API, pipeline, and frontend tests.

Deferred to later plans:

- Deterministic evidence re-location execution.
- Table repair execution.
- OCR retry adapter execution.
- Model routing experiments.
- Manual review feedback endpoints.

## File Structure

- Modify `backend/app/models.py`
  - Add remediation literal types, `OcrRemediationAction`, and `TaskOcrRemediationSummary`.
  - Add `CompareTask.ocr_remediation_summary`.
- Create `backend/app/services/ocr_remediation.py`
  - Own deterministic planning and summary aggregation.
- Modify `backend/app/services/compare_debug.py`
  - Add `write_ocr_remediation()`.
- Modify `backend/app/services/pipeline_stages.py`
  - Add `OcrRemediationStage`.
- Modify `backend/app/services/pipeline.py`
  - Add the stage to `_default_stages()`.
  - Persist `ocr_remediation_summary` in `_copy_processing_result()`.
- Modify `backend/app/api_schemas.py`
  - Add response schemas for remediation action and summary.
  - Add optional fields to `CompareTaskResponse`.
- Modify `backend/app/api_presenters.py`
  - Include `ocr_remediation_summary`.
- Modify `backend/app/services/review_service.py`
  - Include remediation summary and counts in quality response.
- Modify `frontend/src/types.ts`
  - Add remediation types and optional response fields.
- Modify `frontend/src/pages/ResultPage.tsx`
  - Show compact remediation badges/status for diff cards.
- Modify `frontend/src/pages/ResultPage.test.tsx`
  - Cover rendering and legacy response behavior.
- Create `backend/tests/test_ocr_remediation.py`
  - Unit tests for planner behavior.
- Modify `backend/tests/test_pipeline.py`
  - Stage and persistence coverage.
- Modify `backend/tests/test_api.py`
  - Response compatibility and new fields.

---

### Task 1: Add Remediation Models

**Files:**
- Modify: `backend/app/models.py`
- Test: `backend/tests/test_ocr_remediation.py`

- [ ] **Step 1: Write failing model tests**

Add `backend/tests/test_ocr_remediation.py`:

```python
from app.models import (
    OcrRemediationAction,
    TaskOcrRemediationSummary,
    CompareTask,
)


def test_ocr_remediation_action_defaults_are_planning_safe():
    action = OcrRemediationAction(
        action_id="original:1:diff-1:RELOCATE_EVIDENCE",
        action_type="RELOCATE_EVIDENCE",
        reason="EVIDENCE_UNRELIABLE",
        side="original",
        page_no=1,
        diff_id="diff-1",
    )

    assert action.status == "PLANNED"
    assert action.changed_evidence is False
    assert action.changed_diff_text is False
    assert action.review_flags_added == []


def test_task_ocr_remediation_summary_defaults_are_legacy_safe():
    summary = TaskOcrRemediationSummary()

    assert summary.status == "OK"
    assert summary.requires_manual_review is False
    assert summary.attempted_action_count == 0
    assert summary.successful_action_count == 0
    assert summary.unresolved_action_count == 0
    assert summary.manual_review_required_count == 0
    assert summary.actions == []


def test_compare_task_accepts_missing_ocr_remediation_summary():
    task = CompareTask(task_id="task-1")

    assert task.ocr_remediation_summary is None
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend && python -m pytest tests/test_ocr_remediation.py -v
```

Expected: import error for `OcrRemediationAction` or `TaskOcrRemediationSummary`.

- [ ] **Step 3: Add models**

In `backend/app/models.py`, after `OcrQualitySide`, add:

```python
OcrRemediationActionType = Literal[
    "NO_ACTION",
    "MARK_REVIEW",
    "RELOCATE_EVIDENCE",
    "REPAIR_TABLE",
    "RETRY_OCR_PAGE",
    "ESCALATE_MANUAL_REVIEW",
]
OcrRemediationStatus = Literal[
    "PLANNED",
    "SKIPPED",
    "SUCCEEDED",
    "FAILED",
    "MANUAL_REVIEW_REQUIRED",
]
OcrRemediationSummaryStatus = Literal[
    "OK",
    "ACTIONS_PLANNED",
    "MANUAL_REVIEW_REQUIRED",
]
```

After `TaskOcrQualitySummary`, add:

```python
class OcrRemediationAction(BaseModel):
    action_id: str
    action_type: OcrRemediationActionType
    reason: str
    status: OcrRemediationStatus = "PLANNED"
    side: OcrQualitySide | None = None
    page_no: int | None = None
    diff_id: str | None = None
    before_quality: dict[str, Any] = Field(default_factory=dict)
    after_quality: dict[str, Any] = Field(default_factory=dict)
    changed_evidence: bool = False
    changed_diff_text: bool = False
    review_flags_added: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TaskOcrRemediationSummary(BaseModel):
    status: OcrRemediationSummaryStatus = "OK"
    requires_manual_review: bool = False
    attempted_action_count: int = 0
    successful_action_count: int = 0
    unresolved_action_count: int = 0
    risk_reduced_page_count: int = 0
    risk_reduced_diff_count: int = 0
    manual_review_required_count: int = 0
    actions: list[OcrRemediationAction] = Field(default_factory=list)
```

In `CompareTask`, after `ocr_quality_summary`, add:

```python
    ocr_remediation_summary: TaskOcrRemediationSummary | None = None
```

- [ ] **Step 4: Run model tests**

Run:

```bash
cd backend && python -m pytest tests/test_ocr_remediation.py -v
```

Expected: all tests in `test_ocr_remediation.py` pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/tests/test_ocr_remediation.py
git commit -m "feat: add OCR remediation models"
```

---

### Task 2: Implement the Remediation Planner

**Files:**
- Create: `backend/app/services/ocr_remediation.py`
- Modify: `backend/tests/test_ocr_remediation.py`

- [ ] **Step 1: Add failing planner tests**

Append these tests to `backend/tests/test_ocr_remediation.py`:

```python
from app.models import DiffItem, PageOcrQualityProfile, TaskOcrQualitySummary
from app.services.ocr_remediation import OcrRemediationPlanner


def test_planner_creates_relocate_action_for_unreliable_evidence():
    diff = DiffItem(
        diff_id="diff-1",
        diff_type="MODIFY",
        source_type="clause",
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )
    summary = TaskOcrQualitySummary(
        status="LAYOUT_MISMATCH",
        requires_review=True,
        risk_page_count=1,
        affected_diff_count=1,
        profiles=[
            PageOcrQualityProfile(
                side="original",
                page_no=2,
                status="LAYOUT_MISMATCH",
                reasons=["LOW_LAYOUT_MATCH_RATE"],
                affected_diff_ids=["diff-1"],
                metrics={"layout_match_rate": 0.5},
            )
        ],
    )

    remediation = OcrRemediationPlanner().plan(summary, [diff])

    assert remediation.status == "ACTIONS_PLANNED"
    assert remediation.attempted_action_count == 1
    assert remediation.unresolved_action_count == 1
    assert remediation.actions[0].action_type == "RELOCATE_EVIDENCE"
    assert remediation.actions[0].side == "original"
    assert remediation.actions[0].page_no == 2
    assert remediation.actions[0].diff_id == "diff-1"
    assert remediation.actions[0].before_quality["ocr_status"] == "LAYOUT_MISMATCH"


def test_planner_escalates_page_unreliable_once_per_diff():
    diff = DiffItem(
        diff_id="diff-1",
        diff_type="MODIFY",
        source_type="table",
        review_flags=["PAGE_UNRELIABLE", "TABLE_STRUCTURE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )
    summary = TaskOcrQualitySummary(
        status="UNRELIABLE",
        requires_review=True,
        risk_page_count=1,
        affected_diff_count=1,
        profiles=[
            PageOcrQualityProfile(
                side="compare",
                page_no=3,
                status="UNRELIABLE",
                reasons=["TABLE_CELL_UNMATCHED", "LOW_AVG_CONFIDENCE"],
                affected_diff_ids=["diff-1"],
                metrics={"avg_confidence": 0.52},
            )
        ],
    )

    remediation = OcrRemediationPlanner().plan(summary, [diff])
    action_types = [action.action_type for action in remediation.actions]

    assert action_types == ["ESCALATE_MANUAL_REVIEW"]
    assert remediation.status == "MANUAL_REVIEW_REQUIRED"
    assert remediation.requires_manual_review is True
    assert remediation.manual_review_required_count == 1


def test_planner_returns_ok_summary_without_ocr_risk():
    remediation = OcrRemediationPlanner().plan(None, [])

    assert remediation.status == "OK"
    assert remediation.actions == []
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend && python -m pytest tests/test_ocr_remediation.py -v
```

Expected: import error for `app.services.ocr_remediation`.

- [ ] **Step 3: Implement planner**

Create `backend/app/services/ocr_remediation.py`:

```python
from __future__ import annotations

from app.models import (
    DiffItem,
    OcrRemediationAction,
    OcrRemediationActionType,
    PageOcrQualityProfile,
    TaskOcrQualitySummary,
    TaskOcrRemediationSummary,
)


_FLAG_ACTION_PRIORITY: list[tuple[str, OcrRemediationActionType, str]] = [
    ("PAGE_UNRELIABLE", "ESCALATE_MANUAL_REVIEW", "PAGE_UNRELIABLE"),
    ("EVIDENCE_UNRELIABLE", "RELOCATE_EVIDENCE", "EVIDENCE_UNRELIABLE"),
    ("TABLE_STRUCTURE_UNRELIABLE", "REPAIR_TABLE", "TABLE_STRUCTURE_UNRELIABLE"),
    ("OCR_LOW_CONFIDENCE", "RETRY_OCR_PAGE", "OCR_LOW_CONFIDENCE"),
    ("LAYOUT_MISMATCH_RISK", "MARK_REVIEW", "LAYOUT_MISMATCH_RISK"),
    ("READING_ORDER_RISK", "MARK_REVIEW", "READING_ORDER_RISK"),
    ("SEAL_OR_SIGNATURE_RISK", "MARK_REVIEW", "SEAL_OR_SIGNATURE_RISK"),
]


class OcrRemediationPlanner:
    """Create deterministic remediation actions from OCR quality risk signals."""

    def plan(
        self,
        ocr_quality_summary: TaskOcrQualitySummary | None,
        diffs: list[DiffItem],
    ) -> TaskOcrRemediationSummary:
        if ocr_quality_summary is None or not ocr_quality_summary.requires_review:
            return TaskOcrRemediationSummary()

        profiles_by_diff = self._profiles_by_diff(ocr_quality_summary.profiles)
        actions: list[OcrRemediationAction] = []
        seen: set[str] = set()

        for diff in sorted(diffs, key=lambda item: item.diff_id):
            profiles = profiles_by_diff.get(diff.diff_id, [])
            action_type, reason = self._action_for_diff(diff)
            if action_type is None:
                continue
            if action_type == "ESCALATE_MANUAL_REVIEW":
                profiles = profiles[:1] or [None]
            elif not profiles:
                profiles = [None]

            for profile in profiles:
                action = self._build_action(diff, profile, action_type, reason)
                if action.action_id in seen:
                    continue
                seen.add(action.action_id)
                actions.append(action)

        return self._summary(actions)

    @staticmethod
    def _profiles_by_diff(
        profiles: list[PageOcrQualityProfile],
    ) -> dict[str, list[PageOcrQualityProfile]]:
        result: dict[str, list[PageOcrQualityProfile]] = {}
        for profile in sorted(profiles, key=lambda item: (item.side, item.page_no)):
            if profile.status == "OK":
                continue
            for diff_id in profile.affected_diff_ids:
                result.setdefault(diff_id, []).append(profile)
        return result

    @staticmethod
    def _action_for_diff(diff: DiffItem) -> tuple[OcrRemediationActionType | None, str]:
        flags = set(diff.review_flags)
        for flag, action_type, reason in _FLAG_ACTION_PRIORITY:
            if flag in flags:
                return action_type, reason
        if diff.quality_status == "NEEDS_REVIEW" and any(flag.startswith("OCR_") for flag in flags):
            return "MARK_REVIEW", "OCR_NEEDS_REVIEW"
        return None, ""

    @staticmethod
    def _build_action(
        diff: DiffItem,
        profile: PageOcrQualityProfile | None,
        action_type: OcrRemediationActionType,
        reason: str,
    ) -> OcrRemediationAction:
        side = profile.side if profile is not None else None
        page_no = profile.page_no if profile is not None else None
        status = "MANUAL_REVIEW_REQUIRED" if action_type == "ESCALATE_MANUAL_REVIEW" else "PLANNED"
        action_id = ":".join(
            [
                side or "unknown",
                str(page_no) if page_no is not None else "unknown",
                diff.diff_id,
                action_type,
            ]
        )
        before_quality = {}
        if profile is not None:
            before_quality = {
                "ocr_status": profile.status,
                "ocr_score": profile.score,
                "ocr_reasons": profile.reasons,
                "ocr_metrics": profile.metrics,
            }
        return OcrRemediationAction(
            action_id=action_id,
            action_type=action_type,
            reason=reason,
            status=status,
            side=side,
            page_no=page_no,
            diff_id=diff.diff_id,
            before_quality=before_quality,
            changed_diff_text=False,
            review_flags_added=["OCR_REMEDIATION_PLANNED"],
            notes=[f"Planning-only action for {reason}."],
        )

    @staticmethod
    def _summary(actions: list[OcrRemediationAction]) -> TaskOcrRemediationSummary:
        manual_count = sum(1 for action in actions if action.status == "MANUAL_REVIEW_REQUIRED")
        unresolved_count = sum(1 for action in actions if action.status in {"PLANNED", "MANUAL_REVIEW_REQUIRED"})
        status = "OK"
        if manual_count:
            status = "MANUAL_REVIEW_REQUIRED"
        elif actions:
            status = "ACTIONS_PLANNED"
        return TaskOcrRemediationSummary(
            status=status,
            requires_manual_review=bool(manual_count),
            attempted_action_count=len(actions),
            successful_action_count=sum(1 for action in actions if action.status == "SUCCEEDED"),
            unresolved_action_count=unresolved_count,
            manual_review_required_count=manual_count,
            actions=actions,
        )
```

- [ ] **Step 4: Run planner tests**

Run:

```bash
cd backend && python -m pytest tests/test_ocr_remediation.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ocr_remediation.py backend/tests/test_ocr_remediation.py
git commit -m "feat: plan OCR remediation actions"
```

---

### Task 3: Add Pipeline Stage and Debug Artifact

**Files:**
- Modify: `backend/app/services/compare_debug.py`
- Modify: `backend/app/services/pipeline_stages.py`
- Modify: `backend/app/services/pipeline.py`
- Modify: `backend/tests/test_pipeline.py`

- [ ] **Step 1: Add failing pipeline tests**

Add these tests to `backend/tests/test_pipeline.py` near existing OCR quality pipeline tests:

```python
from app.models import CompareTask, DiffItem, PageOcrQualityProfile, TaskOcrQualitySummary
from app.services.pipeline import PipelineContext, _copy_processing_result
from app.services.pipeline_stages import OcrRemediationStage


def test_ocr_remediation_stage_plans_actions_and_marks_diffs(tmp_path):
    task = CompareTask(task_id="task-remediation")
    task.ocr_quality_summary = TaskOcrQualitySummary(
        status="LAYOUT_MISMATCH",
        requires_review=True,
        risk_page_count=1,
        affected_diff_count=1,
        profiles=[
            PageOcrQualityProfile(
                side="original",
                page_no=1,
                status="LAYOUT_MISMATCH",
                reasons=["LOW_LAYOUT_MATCH_RATE"],
                affected_diff_ids=["diff-1"],
            )
        ],
    )
    diff = DiffItem(
        diff_id="diff-1",
        diff_type="MODIFY",
        source_type="clause",
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )
    ctx = PipelineContext(task=task, original_pdf=tmp_path / "o.pdf", compare_pdf=tmp_path / "c.pdf")
    ctx.diffs = [diff]

    OcrRemediationStage().execute(ctx)

    assert task.ocr_remediation_summary is not None
    assert task.ocr_remediation_summary.attempted_action_count == 1
    assert task.debug_artifact_paths["ocr_remediation"].endswith("ocr_remediation.json")
    assert "OCR_REMEDIATION_PLANNED" in ctx.diffs[0].review_flags


def test_copy_processing_result_persists_ocr_remediation_summary():
    target = CompareTask(task_id="task-copy")
    source = CompareTask(task_id="task-copy")
    source.ocr_remediation_summary = TaskOcrRemediationSummary(
        status="ACTIONS_PLANNED",
        attempted_action_count=1,
        unresolved_action_count=1,
    )

    _copy_processing_result(target, source)

    assert target.ocr_remediation_summary is not None
    assert target.ocr_remediation_summary.attempted_action_count == 1
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend && python -m pytest tests/test_pipeline.py::test_ocr_remediation_stage_plans_actions_and_marks_diffs tests/test_pipeline.py::test_copy_processing_result_persists_ocr_remediation_summary -v
```

Expected: import error for `OcrRemediationStage` or missing `write_ocr_remediation`.

- [ ] **Step 3: Add debug writer method**

In `backend/app/services/compare_debug.py`, after `write_ocr_quality`, add:

```python
    def write_ocr_remediation(self, task_id: str, summary) -> str:
        return str(self._write_json(task_id, "ocr_remediation.json", to_jsonable(summary)))
```

- [ ] **Step 4: Add pipeline stage**

In `backend/app/services/pipeline_stages.py`, add import:

```python
from app.services.ocr_remediation import OcrRemediationPlanner
```

After `OcrQualityStage`, add:

```python
class OcrRemediationStage:
    name = "OCR风险处置规划中"
    start_progress = 84
    progress = 85

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.planner = OcrRemediationPlanner()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        summary = self.planner.plan(ctx.task.ocr_quality_summary, ctx.diffs)
        ctx.task.ocr_remediation_summary = summary
        self._apply_planning_flags(ctx.diffs, summary.actions)
        _write_debug_artifact(
            ctx.task,
            "ocr_remediation",
            lambda: self.debug_writer.write_ocr_remediation(ctx.task.task_id, summary),
        )
        _emit_progress(ctx, 85, self.name, "ocr_remediation_planned")

    @staticmethod
    def _apply_planning_flags(diffs: list[DiffItem], actions) -> None:
        flags_by_diff: dict[str, set[str]] = {}
        for action in actions:
            if not action.diff_id:
                continue
            flags_by_diff.setdefault(action.diff_id, set()).update(action.review_flags_added)
            if action.status == "MANUAL_REVIEW_REQUIRED":
                flags_by_diff[action.diff_id].add("OCR_REMEDIATION_MANUAL_REVIEW")

        for diff in diffs:
            flags = flags_by_diff.get(diff.diff_id)
            if not flags:
                continue
            for flag in sorted(flags):
                if flag not in diff.review_flags:
                    diff.review_flags.append(flag)
            diff.quality_status = "NEEDS_REVIEW"
```

- [ ] **Step 5: Add stage and persistence**

In `backend/app/services/pipeline.py`, add `target.ocr_remediation_summary = source.ocr_remediation_summary` after the OCR quality copy:

```python
    target.ocr_quality_summary = source.ocr_quality_summary
    target.ocr_remediation_summary = source.ocr_remediation_summary
```

In `_default_stages()`, import and insert `OcrRemediationStage` after `OcrQualityStage()`:

```python
        OcrQualityStage(),
        OcrRemediationStage(),
        DiffQualityStage(),
```

- [ ] **Step 6: Run pipeline tests**

Run:

```bash
cd backend && python -m pytest tests/test_pipeline.py::test_ocr_remediation_stage_plans_actions_and_marks_diffs tests/test_pipeline.py::test_copy_processing_result_persists_ocr_remediation_summary -v
```

Expected: both tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/compare_debug.py backend/app/services/pipeline_stages.py backend/app/services/pipeline.py backend/tests/test_pipeline.py
git commit -m "feat: add OCR remediation pipeline stage"
```

---

### Task 4: Expose Remediation Through API and Quality Summary

**Files:**
- Modify: `backend/app/api_schemas.py`
- Modify: `backend/app/api_presenters.py`
- Modify: `backend/app/services/review_service.py`
- Modify: `backend/tests/test_api.py`

- [ ] **Step 1: Add failing API tests**

Add tests to `backend/tests/test_api.py`:

```python
from app.api_presenters import compare_task_response
from app.models import CompareTask, OcrRemediationAction, TaskOcrRemediationSummary
from app.services.review_service import CompareQualityService


def test_compare_task_response_includes_ocr_remediation_summary():
    task = CompareTask(task_id="task-api", status="COMPLETED")
    task.ocr_remediation_summary = TaskOcrRemediationSummary(
        status="ACTIONS_PLANNED",
        attempted_action_count=1,
        unresolved_action_count=1,
        actions=[
            OcrRemediationAction(
                action_id="original:1:diff-1:RELOCATE_EVIDENCE",
                action_type="RELOCATE_EVIDENCE",
                reason="EVIDENCE_UNRELIABLE",
                side="original",
                page_no=1,
                diff_id="diff-1",
            )
        ],
    )

    response = compare_task_response(task)

    assert response.ocr_remediation_summary is not None
    assert response.ocr_remediation_summary.attempted_action_count == 1
    assert response.ocr_remediation_summary.actions[0].action_type == "RELOCATE_EVIDENCE"


def test_compare_quality_summary_includes_ocr_remediation_counts():
    task = CompareTask(task_id="task-quality", status="COMPLETED")
    task.ocr_remediation_summary = TaskOcrRemediationSummary(
        status="MANUAL_REVIEW_REQUIRED",
        attempted_action_count=2,
        unresolved_action_count=2,
        manual_review_required_count=1,
    )

    summary = CompareQualityService().build_summary(task)

    assert summary["ocr_remediation_summary"]["status"] == "MANUAL_REVIEW_REQUIRED"
    assert summary["ocr_remediation_action_count"] == 2
    assert summary["ocr_remediation_unresolved_count"] == 2
    assert summary["manual_review_required_count"] == 1
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend && python -m pytest tests/test_api.py::test_compare_task_response_includes_ocr_remediation_summary tests/test_api.py::test_compare_quality_summary_includes_ocr_remediation_counts -v
```

Expected: missing response field errors.

- [ ] **Step 3: Add API schemas**

In `backend/app/api_schemas.py`, add remediation literals near OCR quality types:

```python
OcrRemediationActionType = Literal[
    "NO_ACTION",
    "MARK_REVIEW",
    "RELOCATE_EVIDENCE",
    "REPAIR_TABLE",
    "RETRY_OCR_PAGE",
    "ESCALATE_MANUAL_REVIEW",
]
OcrRemediationStatus = Literal[
    "PLANNED",
    "SKIPPED",
    "SUCCEEDED",
    "FAILED",
    "MANUAL_REVIEW_REQUIRED",
]
OcrRemediationSummaryStatus = Literal[
    "OK",
    "ACTIONS_PLANNED",
    "MANUAL_REVIEW_REQUIRED",
]
```

After `TaskOcrQualitySummaryResponse`, add:

```python
class OcrRemediationActionResponse(BaseModel):
    action_id: str
    action_type: OcrRemediationActionType
    reason: str
    status: OcrRemediationStatus = "PLANNED"
    side: OcrQualitySide | None = None
    page_no: int | None = None
    diff_id: str | None = None
    before_quality: dict[str, Any] = Field(default_factory=dict)
    after_quality: dict[str, Any] = Field(default_factory=dict)
    changed_evidence: bool = False
    changed_diff_text: bool = False
    review_flags_added: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TaskOcrRemediationSummaryResponse(BaseModel):
    status: OcrRemediationSummaryStatus = "OK"
    requires_manual_review: bool = False
    attempted_action_count: int = 0
    successful_action_count: int = 0
    unresolved_action_count: int = 0
    risk_reduced_page_count: int = 0
    risk_reduced_diff_count: int = 0
    manual_review_required_count: int = 0
    actions: list[OcrRemediationActionResponse] = Field(default_factory=list)
```

In `CompareTaskResponse`, after `ocr_quality_summary`, add:

```python
    ocr_remediation_summary: TaskOcrRemediationSummaryResponse | None = None
```

- [ ] **Step 4: Add presenter and quality response fields**

In `backend/app/api_presenters.py`, add to `compare_task_response()` data:

```python
        "ocr_remediation_summary": (
            to_jsonable(task.ocr_remediation_summary) if task.ocr_remediation_summary else None
        ),
```

In `backend/app/services/review_service.py`, add to `CompareQualityService.build_summary()` return payload:

```python
            "ocr_remediation_summary": (
                to_jsonable(task.ocr_remediation_summary) if task.ocr_remediation_summary else None
            ),
            "ocr_remediation_action_count": (
                task.ocr_remediation_summary.attempted_action_count if task.ocr_remediation_summary else 0
            ),
            "ocr_remediation_unresolved_count": (
                task.ocr_remediation_summary.unresolved_action_count if task.ocr_remediation_summary else 0
            ),
            "manual_review_required_count": (
                task.ocr_remediation_summary.manual_review_required_count if task.ocr_remediation_summary else 0
            ),
```

- [ ] **Step 5: Run API tests**

Run:

```bash
cd backend && python -m pytest tests/test_api.py::test_compare_task_response_includes_ocr_remediation_summary tests/test_api.py::test_compare_quality_summary_includes_ocr_remediation_counts -v
```

Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api_schemas.py backend/app/api_presenters.py backend/app/services/review_service.py backend/tests/test_api.py
git commit -m "feat: expose OCR remediation summaries"
```

---

### Task 5: Add Frontend Types and Result Badges

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/pages/ResultPage.tsx`
- Modify: `frontend/src/pages/ResultPage.test.tsx`

- [ ] **Step 1: Add failing frontend test**

In `frontend/src/pages/ResultPage.test.tsx`, extend the existing OCR-risk fixture with:

```ts
ocr_remediation_summary: {
  status: "ACTIONS_PLANNED",
  requires_manual_review: false,
  attempted_action_count: 1,
  successful_action_count: 0,
  unresolved_action_count: 1,
  risk_reduced_page_count: 0,
  risk_reduced_diff_count: 0,
  manual_review_required_count: 0,
  actions: [
    {
      action_id: "original:1:diff-1:RELOCATE_EVIDENCE",
      action_type: "RELOCATE_EVIDENCE",
      reason: "EVIDENCE_UNRELIABLE",
      status: "PLANNED",
      side: "original",
      page_no: 1,
      diff_id: "diff-1",
      before_quality: {},
      after_quality: {},
      changed_evidence: false,
      changed_diff_text: false,
      review_flags_added: ["OCR_REMEDIATION_PLANNED"],
      notes: ["Planning-only action for EVIDENCE_UNRELIABLE."],
    },
  ],
},
```

Add assertion:

```ts
expect(await screen.findByText("处置规划")).toBeInTheDocument();
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd frontend && npm test -- src/pages/ResultPage.test.tsx
```

Expected: assertion fails because remediation badge is not rendered.

- [ ] **Step 3: Add TypeScript types**

In `frontend/src/types.ts`, after OCR quality types, add:

```ts
export type OcrRemediationActionType =
  | "NO_ACTION"
  | "MARK_REVIEW"
  | "RELOCATE_EVIDENCE"
  | "REPAIR_TABLE"
  | "RETRY_OCR_PAGE"
  | "ESCALATE_MANUAL_REVIEW";

export type OcrRemediationStatus =
  | "PLANNED"
  | "SKIPPED"
  | "SUCCEEDED"
  | "FAILED"
  | "MANUAL_REVIEW_REQUIRED";

export type OcrRemediationSummaryStatus =
  | "OK"
  | "ACTIONS_PLANNED"
  | "MANUAL_REVIEW_REQUIRED";

export interface OcrRemediationAction {
  action_id: string;
  action_type: OcrRemediationActionType;
  reason: string;
  status: OcrRemediationStatus;
  side?: OcrQualitySide | null;
  page_no?: number | null;
  diff_id?: string | null;
  before_quality: Record<string, unknown>;
  after_quality: Record<string, unknown>;
  changed_evidence: boolean;
  changed_diff_text: boolean;
  review_flags_added: string[];
  notes: string[];
}

export interface TaskOcrRemediationSummary {
  status: OcrRemediationSummaryStatus;
  requires_manual_review: boolean;
  attempted_action_count: number;
  successful_action_count: number;
  unresolved_action_count: number;
  risk_reduced_page_count: number;
  risk_reduced_diff_count: number;
  manual_review_required_count: number;
  actions: OcrRemediationAction[];
}
```

In `CompareResponse`, after `ocr_quality_summary`, add:

```ts
  ocr_remediation_summary?: TaskOcrRemediationSummary | null;
```

In `CompareQualitySummary`, add:

```ts
  ocr_remediation_summary?: TaskOcrRemediationSummary | null;
  ocr_remediation_action_count?: number;
  ocr_remediation_unresolved_count?: number;
  manual_review_required_count?: number;
```

- [ ] **Step 4: Render compact remediation badge**

In `frontend/src/pages/ResultPage.tsx`, update the type import:

```tsx
import type { CompareTask, DiffItem, DiffType, ReviewStatus, TaskOcrRemediationSummary } from "../types";
```

Update the memoized audit item builder:

```tsx
  const auditItems = useMemo(
    () => buildAuditItems(diffs, task?.audit_item_reviews ?? {}, task?.ocr_remediation_summary ?? null),
    [diffs, task?.audit_item_reviews, task?.ocr_remediation_summary],
  );
```

Add a remediation badge field to `AuditChangeItem`:

```tsx
interface AuditChangeItem {
  id: string;
  diffId: string;
  type: DiffType;
  group: AuditGroup;
  title: string;
  summary: string;
  pageNo: number | null;
  y0: number | null;
  reviewStatus: ReviewStatus;
  reviewComment: string;
  qualityStatus: DiffItem["quality_status"];
  reviewFlags: string[];
  remediationBadge: { className: string; label: string } | null;
}
```

Update `buildAuditItems()`:

```tsx
function buildAuditItems(
  diffs: DiffItem[],
  auditItemReviews: NonNullable<CompareTask["audit_item_reviews"]>,
  remediationSummary: TaskOcrRemediationSummary | null,
): AuditChangeItem[] {
  return diffs.flatMap((diff) => auditItemsForDiff(diff, auditItemReviews, remediationSummary));
}
```

Update `auditItemsForDiff()`:

```tsx
function auditItemsForDiff(
  diff: DiffItem,
  auditItemReviews: NonNullable<CompareTask["audit_item_reviews"]>,
  remediationSummary: TaskOcrRemediationSummary | null,
): AuditChangeItem[] {
  const originalEvidence = diff.original_evidence ?? [];
  const compareEvidence = diff.compare_evidence ?? [];
  const hasTypedEvidence = [...originalEvidence, ...compareEvidence].some((evidence) => Boolean(evidence.highlight_type));

  if (!hasTypedEvidence) {
    return [];
  }

  const items: AuditChangeItem[] = [];
  const addEvidence = typedEvidence(compareEvidence, "ADD");
  const deleteEvidence = typedEvidence(originalEvidence, "DELETE");
  const originalModifyEvidence = typedEvidence(originalEvidence, "MODIFY");
  const compareModifyEvidence = typedEvidence(compareEvidence, "MODIFY");
  const addText = evidenceText(addEvidence);
  const deleteText = evidenceText(deleteEvidence);
  const originalModifyText = evidenceText(originalModifyEvidence);
  const compareModifyText = evidenceText(compareModifyEvidence);
  if (addEvidence.length > 0) {
    items.push(auditItem(diff, "ADD", addText, addEvidence, auditItemReviews, remediationSummary));
  }
  if (deleteEvidence.length > 0) {
    items.push(auditItem(diff, "DELETE", deleteText, deleteEvidence, auditItemReviews, remediationSummary));
  }
  if (originalModifyEvidence.length > 0 || compareModifyEvidence.length > 0) {
    items.push(
      auditItem(
        diff,
        "MODIFY",
        modifySummary(originalModifyText, compareModifyText),
        [...originalModifyEvidence, ...compareModifyEvidence],
        auditItemReviews,
        remediationSummary,
      ),
    );
  }
  return items.length > 0
    ? items
    : [
        auditItem(
          diff,
          diff.diff_type,
          diffSummary(diff),
          [...originalEvidence, ...compareEvidence],
          auditItemReviews,
          remediationSummary,
        ),
      ];
}
```

Update `auditItem()`:

```tsx
function auditItem(
  diff: DiffItem,
  type: DiffType,
  summary: string,
  evidenceList: NonNullable<DiffItem["compare_evidence"]>,
  auditItemReviews: NonNullable<CompareTask["audit_item_reviews"]>,
  remediationSummary: TaskOcrRemediationSummary | null,
): AuditChangeItem {
  const location = evidenceLocation(evidenceList);
  const id = `${diff.diff_id}:${type}`;
  const review = auditItemReviews[id];
  return {
    id,
    diffId: diff.diff_id,
    type,
    group: auditGroup(diff),
    title: diff.title || diff.clause_no || diff.diff_id,
    summary: compactText(summary || diffSummary(diff)),
    pageNo: location?.pageNo ?? null,
    y0: location?.y0 ?? null,
    reviewStatus: review?.review_status ?? "UNREVIEWED",
    reviewComment: review?.review_comment ?? "",
    qualityStatus: diff.quality_status ?? "NORMAL",
    reviewFlags: diff.review_flags ?? [],
    remediationBadge: remediationBadgeForDiff(diff.diff_id, remediationSummary),
  };
}
```

Use this helper near `auditQualityBadges()`:

```tsx
function remediationBadgeForDiff(
  diffId: string,
  summary?: TaskOcrRemediationSummary | null,
): { className: string; label: string } | null {
  const action = summary?.actions.find((item) => item.diff_id === diffId);
  if (!action) {
    return null;
  }
  if (action.status === "MANUAL_REVIEW_REQUIRED") {
    return { className: "needs-review", label: "需人工处置" };
  }
  if (action.status === "PLANNED") {
    return { className: "needs-review", label: "处置规划" };
  }
  if (action.status === "SUCCEEDED") {
    return { className: "merged", label: "已自动处置" };
  }
  return { className: "needs-review", label: "处置未完成" };
}
```

Render the badge inside `AuditDiffCard`, after `qualityBadges.map(...)`:

```tsx
          {item.remediationBadge ? (
            <span className={`audit-quality-badge ${item.remediationBadge.className}`}>
              {item.remediationBadge.label}
            </span>
          ) : null}
```

Keep existing card layout and badge CSS classes; Phase 3A does not add a new dashboard panel.

- [ ] **Step 5: Run frontend test**

Run:

```bash
cd frontend && npm test -- src/pages/ResultPage.test.tsx
```

Expected: test passes and renders `处置规划`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/pages/ResultPage.tsx frontend/src/pages/ResultPage.test.tsx
git commit -m "feat: show OCR remediation planning badges"
```

---

### Task 6: Run Focused and Full Verification

**Files:**
- No new source files.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
cd backend && python -m pytest tests/test_ocr_remediation.py tests/test_pipeline.py tests/test_api.py -v
```

Expected: all selected tests pass.

- [ ] **Step 2: Run backend syntax and lint**

Run:

```bash
cd backend && python -m compileall app tests scripts
cd backend && python -m ruff check .
```

Expected: compileall exits 0 and ruff prints `All checks passed!`.

- [ ] **Step 3: Run frontend verification**

Run:

```bash
cd frontend && npm test
cd frontend && npm run build
```

Expected: all Vitest tests pass and Vite build exits 0.

- [ ] **Step 4: Run OCR quality gate**

Run:


- [ ] **Step 5: Run full backend tests**

Run:

```bash
cd backend && python -m pytest
```

Expected: all backend tests pass.

- [ ] **Step 6: Commit verification-only test fixture updates if any exist**

Run:

```bash
git status --short
```


---

### Task 7: Final Review and Branch Completion

**Files:**
- No source edits expected.

- [ ] **Step 1: Request final code review**

Use a subagent reviewer or an inline review pass over the implementation range. Review must check:

- API compatibility.
- No diff text rewrite.
- Planning-only stage does not suppress or delete diffs.
- OCR remediation flags survive `DiffQualityStage`.
- Persistence copies `ocr_remediation_summary`.
- Frontend handles missing remediation fields.

- [ ] **Step 2: Fix review findings**

If review finds Critical or Important issues, create focused commits for the fixes and rerun the verification commands from Task 6.

- [ ] **Step 3: Finish branch**

Use `superpowers:finishing-a-development-branch` after verification passes. Present the standard options:

```text
1. Merge back to main locally
2. Push and create a Pull Request
3. Keep the branch as-is
4. Discard this work
```
