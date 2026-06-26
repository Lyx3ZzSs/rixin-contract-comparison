# OCR Evidence Relocation Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute planned `RELOCATE_EVIDENCE` OCR remediation actions deterministically, improving evidence confidence without changing diff text or running OCR retry.

**Architecture:** Add a focused `EvidenceRelocator` service that returns immutable relocation results and does not mutate diffs directly. Extend `OcrRemediationStage` to execute only eligible `RELOCATE_EVIDENCE` actions, apply accepted evidence replacements, update action status/quality snapshots, and keep existing DiffQuality/SummaryStage remap and dedupe preservation behavior.

**Tech Stack:** FastAPI backend, Pydantic domain models, PyMuPDF-backed text coordinate lookup, existing comparison pipeline, pytest, React/Vite frontend, Vitest.

---

## Scope

This plan implements Phase 3B only.

Included:

- `EvidenceRelocator` service.
- Deterministic relocation for planned `RELOCATE_EVIDENCE` actions.
- Action status updates: `SUCCEEDED`, `FAILED`, `SKIPPED`.
- Evidence before/after quality snapshots.
- Diff-side evidence replacement only on accepted improvements.
- Summary count recomputation after execution.
- API compatibility tests for updated action state.
- Frontend test coverage for `SUCCEEDED` rendering as `已自动处置`.

Deferred:

- OCR retry.
- Table repair.
- Manual review feedback endpoints.
- Model routing experiments.
- New public API endpoints.

## File Structure

- Create `backend/app/services/evidence_relocator.py`
  - Owns deterministic evidence relocation and candidate acceptance.
- Create `backend/tests/test_evidence_relocator.py`
  - Unit tests for success, skip, failure, wrong-page rejection, and no mutation.
- Modify `backend/app/services/pipeline_stages.py`
  - Extend `OcrRemediationStage` to execute eligible relocation actions.
  - Recompute remediation summary counts after execution.
- Modify `backend/tests/test_pipeline.py`
  - Pipeline coverage for successful and failed relocation action state.
- Modify `backend/tests/test_api.py`
  - API coverage that action status and before/after quality serialize correctly.
- Modify `frontend/src/pages/ResultPage.test.tsx`
  - Cover `SUCCEEDED` remediation badge rendering.

---

### Task 1: Add Evidence Relocator Service

**Files:**
- Create: `backend/app/services/evidence_relocator.py`
- Create: `backend/tests/test_evidence_relocator.py`

- [ ] **Step 1: Write failing service tests**

Create `backend/tests/test_evidence_relocator.py`:

```python
from pathlib import Path

import fitz

from app.models import BBox, DiffItem, EvidenceBox
from app.services.evidence_relocator import EvidenceRelocator


def _write_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 96), text)
    doc.save(path)
    doc.close()


def _low_original_evidence() -> EvidenceBox:
    return EvidenceBox(
        page_no=1,
        bbox=BBox(x0=10, y0=10, x1=80, y1=30),
        method="block_fallback",
        text="付款金额为100元",
        highlight_type="MODIFY",
        confidence=0.46,
        evidence_quality="LOW",
    )


def test_relocator_upgrades_low_confidence_original_evidence(tmp_path: Path):
    original_pdf = tmp_path / "original.pdf"
    compare_pdf = tmp_path / "compare.pdf"
    _write_pdf(original_pdf, "付款金额为100元")
    _write_pdf(compare_pdf, "付款金额为120元")
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        original_snippet="付款金额为100元",
        compare_snippet="付款金额为120元",
        original_evidence=[_low_original_evidence()],
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )

    result = EvidenceRelocator().relocate(
        diff,
        side="original",
        page_no=1,
        original_pdf=original_pdf,
        compare_pdf=compare_pdf,
    )

    assert result.status == "SUCCEEDED"
    assert result.evidence
    assert result.evidence[0].method == "text_exact"
    assert result.evidence[0].confidence > diff.original_evidence[0].confidence
    assert result.before_quality["max_confidence"] == 0.46
    assert result.after_quality["max_confidence"] > 0.46
    assert diff.original_evidence[0].method == "block_fallback"


def test_relocator_skips_existing_high_confidence_evidence(tmp_path: Path):
    original_pdf = tmp_path / "original.pdf"
    compare_pdf = tmp_path / "compare.pdf"
    _write_pdf(original_pdf, "付款金额为100元")
    _write_pdf(compare_pdf, "付款金额为120元")
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        original_snippet="付款金额为100元",
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=10, y0=10, x1=80, y1=30),
                method="text_exact",
                text="付款金额为100元",
                highlight_type="MODIFY",
                confidence=0.98,
                evidence_quality="HIGH",
            )
        ],
    )

    result = EvidenceRelocator().relocate(
        diff,
        side="original",
        page_no=1,
        original_pdf=original_pdf,
        compare_pdf=compare_pdf,
    )

    assert result.status == "SKIPPED"
    assert result.reason == "EXISTING_EVIDENCE_HIGH_CONFIDENCE"
    assert result.evidence == []


def test_relocator_rejects_wrong_page_candidate(tmp_path: Path):
    original_pdf = tmp_path / "original.pdf"
    compare_pdf = tmp_path / "compare.pdf"
    _write_pdf(original_pdf, "付款金额为100元")
    _write_pdf(compare_pdf, "付款金额为120元")
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        original_snippet="付款金额为100元",
        original_evidence=[_low_original_evidence()],
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )

    result = EvidenceRelocator().relocate(
        diff,
        side="original",
        page_no=2,
        original_pdf=original_pdf,
        compare_pdf=compare_pdf,
    )

    assert result.status == "FAILED"
    assert result.reason == "NO_ACCEPTED_CANDIDATE"
    assert result.evidence == []


def test_relocator_fails_without_side_text_signal(tmp_path: Path):
    original_pdf = tmp_path / "original.pdf"
    compare_pdf = tmp_path / "compare.pdf"
    _write_pdf(original_pdf, "付款金额为100元")
    _write_pdf(compare_pdf, "付款金额为120元")
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        original_evidence=[_low_original_evidence()],
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )

    result = EvidenceRelocator().relocate(
        diff,
        side="original",
        page_no=1,
        original_pdf=original_pdf,
        compare_pdf=compare_pdf,
    )

    assert result.status == "FAILED"
    assert result.reason == "NO_SIDE_TEXT_SIGNAL"
    assert result.changed_evidence is False
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend && python -m pytest tests/test_evidence_relocator.py -v
```

Expected: import error for `app.services.evidence_relocator`.

- [ ] **Step 3: Implement service**

Create `backend/app/services/evidence_relocator.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import fitz

from app.models import BBox, DiffItem, EvidenceBox


RelocationStatus = Literal["SUCCEEDED", "FAILED", "SKIPPED"]
RelocationSide = Literal["original", "compare"]


@dataclass(frozen=True)
class EvidenceRelocationResult:
    status: RelocationStatus
    reason: str
    before_quality: dict[str, object] = field(default_factory=dict)
    after_quality: dict[str, object] = field(default_factory=dict)
    evidence: list[EvidenceBox] = field(default_factory=list)
    changed_evidence: bool = False
    notes: list[str] = field(default_factory=list)


class EvidenceRelocator:
    """Deterministically relocate low-confidence diff evidence without mutating diffs."""

    high_confidence_threshold = 0.85

    def relocate(
        self,
        diff: DiffItem,
        *,
        side: RelocationSide,
        page_no: int | None,
        original_pdf: str | Path,
        compare_pdf: str | Path,
    ) -> EvidenceRelocationResult:
        existing = list(diff.original_evidence if side == "original" else diff.compare_evidence)
        before_quality = self._quality_snapshot(existing)
        if self._has_high_confidence(existing):
            return EvidenceRelocationResult(
                status="SKIPPED",
                reason="EXISTING_EVIDENCE_HIGH_CONFIDENCE",
                before_quality=before_quality,
                notes=["Existing evidence is already high confidence."],
            )

        query = self._side_query(diff, side)
        if not query:
            return EvidenceRelocationResult(
                status="FAILED",
                reason="NO_SIDE_TEXT_SIGNAL",
                before_quality=before_quality,
                notes=["No side-specific snippet or text was available for relocation."],
            )

        pdf_path = Path(original_pdf if side == "original" else compare_pdf)
        candidates = self._locate_pdf_text(pdf_path, query, self._highlight_type(diff, side), page_no)
        accepted = self._accepted_candidates(candidates, existing, page_no)
        if not accepted:
            return EvidenceRelocationResult(
                status="FAILED",
                reason="NO_ACCEPTED_CANDIDATE",
                before_quality=before_quality,
                notes=["No higher-confidence evidence candidate was found."],
            )

        after_quality = self._quality_snapshot(accepted)
        return EvidenceRelocationResult(
            status="SUCCEEDED",
            reason="EVIDENCE_RELOCATED",
            before_quality=before_quality,
            after_quality=after_quality,
            evidence=accepted,
            changed_evidence=True,
            notes=["Evidence was relocated with deterministic PDF text search."],
        )

    def _side_query(self, diff: DiffItem, side: RelocationSide) -> str:
        if side == "original":
            return " ".join((diff.original_snippet or diff.original_text or "").split())
        return " ".join((diff.compare_snippet or diff.compare_text or "").split())

    def _highlight_type(self, diff: DiffItem, side: RelocationSide) -> str:
        if diff.diff_type == "MODIFY":
            return "MODIFY"
        if side == "original":
            return "DELETE"
        return "ADD"

    def _locate_pdf_text(
        self,
        pdf_path: Path,
        query: str,
        highlight_type: str,
        page_no: int | None,
    ) -> list[EvidenceBox]:
        if not pdf_path.exists() or not query:
            return []
        try:
            pdf = fitz.open(pdf_path)
        except Exception:
            return []
        try:
            page_numbers = [page_no] if page_no is not None else list(range(1, len(pdf) + 1))
            candidates: list[EvidenceBox] = []
            for candidate_page_no in page_numbers:
                if candidate_page_no is None or candidate_page_no < 1 or candidate_page_no > len(pdf):
                    continue
                page = pdf[candidate_page_no - 1]
                for rect in page.search_for(query):
                    if rect.is_empty or rect.is_infinite:
                        continue
                    candidates.append(
                        EvidenceBox(
                            page_no=candidate_page_no,
                            bbox=BBox(x0=float(rect.x0), y0=float(rect.y0), x1=float(rect.x1), y1=float(rect.y1)),
                            method="text_exact",
                            text=query,
                            highlight_type=highlight_type,
                            confidence=0.98,
                            evidence_quality="HIGH",
                        )
                    )
                if candidates:
                    break
            return candidates
        finally:
            pdf.close()

    def _accepted_candidates(
        self,
        candidates: list[EvidenceBox],
        existing: list[EvidenceBox],
        page_no: int | None,
    ) -> list[EvidenceBox]:
        if not candidates:
            return []
        current_confidence = max((evidence.confidence for evidence in existing), default=0.0)
        accepted = [
            candidate
            for candidate in candidates
            if candidate.confidence > current_confidence and (page_no is None or candidate.page_no == page_no)
        ]
        return accepted[:3]

    def _quality_snapshot(self, evidence: list[EvidenceBox]) -> dict[str, object]:
        if not evidence:
            return {
                "evidence_count": 0,
                "max_confidence": 0.0,
                "methods": [],
                "pages": [],
            }
        return {
            "evidence_count": len(evidence),
            "max_confidence": max(item.confidence for item in evidence),
            "methods": sorted({item.method for item in evidence}),
            "pages": sorted({item.page_no for item in evidence}),
        }

    def _has_high_confidence(self, evidence: list[EvidenceBox]) -> bool:
        return any(item.confidence >= self.high_confidence_threshold or item.evidence_quality == "HIGH" for item in evidence)
```

- [ ] **Step 4: Run service tests**

Run:

```bash
cd backend && python -m pytest tests/test_evidence_relocator.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run lint**

Run:

```bash
cd backend && python -m ruff check app/services/evidence_relocator.py tests/test_evidence_relocator.py
```

Expected: all checks pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/evidence_relocator.py backend/tests/test_evidence_relocator.py
git commit -m "feat: add OCR evidence relocator"
```

---

### Task 2: Execute Relocation Actions in Pipeline

**Files:**
- Modify: `backend/app/services/pipeline_stages.py`
- Modify: `backend/tests/test_pipeline.py`

- [ ] **Step 1: Add failing pipeline tests**

Add these tests to `backend/tests/test_pipeline.py` near existing OCR remediation tests:

```python
def test_ocr_remediation_stage_executes_evidence_relocation(tmp_path: Path) -> None:
    original_pdf = tmp_path / "original.pdf"
    compare_pdf = tmp_path / "compare.pdf"
    _write_text_pdf(original_pdf, "付款金额为100元")
    _write_text_pdf(compare_pdf, "付款金额为120元")
    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    task = CompareTask(
        task_id="task-relocate",
        ocr_quality_summary=TaskOcrQualitySummary(
            status="LAYOUT_MISMATCH",
            requires_review=True,
            risk_page_count=1,
            affected_diff_count=1,
            profiles=[
                PageOcrQualityProfile(
                    side="original",
                    page_no=1,
                    status="LAYOUT_MISMATCH",
                    affected_diff_ids=["D001"],
                )
            ],
        ),
    )
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="clause",
        original_snippet="付款金额为100元",
        compare_snippet="付款金额为120元",
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=10, y0=10, x1=80, y1=30),
                method="block_fallback",
                text="付款金额为100元",
                highlight_type="MODIFY",
                confidence=0.46,
                evidence_quality="LOW",
            )
        ],
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )
    ctx = PipelineContext(task=task, original_pdf=original_pdf, compare_pdf=compare_pdf)
    ctx.diffs = [diff]

    OcrRemediationStage(artifact_store=artifact_store).execute(ctx)

    action = task.ocr_remediation_summary.actions[0]
    assert action.status == "SUCCEEDED"
    assert action.changed_evidence is True
    assert action.changed_diff_text is False
    assert action.before_quality["max_confidence"] == 0.46
    assert action.after_quality["max_confidence"] > 0.46
    assert ctx.diffs[0].original_text == ""
    assert ctx.diffs[0].original_evidence[0].method == "text_exact"
    assert "OCR_REMEDIATION_EVIDENCE_RELOCATED" in ctx.diffs[0].review_flags
    assert task.ocr_remediation_summary.successful_action_count == 1
    assert task.ocr_remediation_summary.unresolved_action_count == 0
    assert task.ocr_remediation_summary.risk_reduced_diff_count == 1


def test_ocr_remediation_stage_keeps_unresolved_state_when_relocation_fails(tmp_path: Path) -> None:
    original_pdf = tmp_path / "original.pdf"
    compare_pdf = tmp_path / "compare.pdf"
    _write_text_pdf(original_pdf, "其他内容")
    _write_text_pdf(compare_pdf, "付款金额为120元")
    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    task = CompareTask(
        task_id="task-relocate-failed",
        ocr_quality_summary=TaskOcrQualitySummary(
            status="LAYOUT_MISMATCH",
            requires_review=True,
            risk_page_count=1,
            affected_diff_count=1,
            profiles=[
                PageOcrQualityProfile(
                    side="original",
                    page_no=1,
                    status="LAYOUT_MISMATCH",
                    affected_diff_ids=["D001"],
                )
            ],
        ),
    )
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="clause",
        original_snippet="付款金额为100元",
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=10, y0=10, x1=80, y1=30),
                method="block_fallback",
                text="付款金额为100元",
                highlight_type="MODIFY",
                confidence=0.46,
                evidence_quality="LOW",
            )
        ],
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )
    ctx = PipelineContext(task=task, original_pdf=original_pdf, compare_pdf=compare_pdf)
    ctx.diffs = [diff]

    OcrRemediationStage(artifact_store=artifact_store).execute(ctx)

    action = task.ocr_remediation_summary.actions[0]
    assert action.status == "FAILED"
    assert action.changed_evidence is False
    assert action.changed_diff_text is False
    assert ctx.diffs[0].original_evidence[0].method == "block_fallback"
    assert "OCR_REMEDIATION_UNRESOLVED" in ctx.diffs[0].review_flags
    assert ctx.diffs[0].quality_status == "NEEDS_REVIEW"
    assert task.ocr_remediation_summary.successful_action_count == 0
    assert task.ocr_remediation_summary.unresolved_action_count == 1
```

If `backend/tests/test_pipeline.py` does not have `_write_text_pdf`, add:

```python
def _write_text_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 96), text)
    doc.save(path)
    doc.close()
```

Add `import fitz` at the top if missing.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend && python -m pytest tests/test_pipeline.py::test_ocr_remediation_stage_executes_evidence_relocation tests/test_pipeline.py::test_ocr_remediation_stage_keeps_unresolved_state_when_relocation_fails -v
```

Expected: first test fails because action remains `PLANNED` and evidence is not relocated.

- [ ] **Step 3: Extend `OcrRemediationStage`**

In `backend/app/services/pipeline_stages.py`, import:

```python
from app.services.evidence_relocator import EvidenceRelocator, EvidenceRelocationResult
```

Update `OcrRemediationStage.__init__`:

```python
        self.relocator = EvidenceRelocator()
```

Update `execute()` to execute relocation before applying flags and writing debug:

```python
    def execute(self, ctx: PipelineContext) -> None:
        summary = self.planner.plan(ctx.task.ocr_quality_summary, ctx.diffs)
        ctx.task.ocr_remediation_summary = summary
        self._execute_relocation_actions(ctx, summary.actions)
        self._refresh_summary_counts(summary)
        self._apply_planning_flags(ctx.diffs, summary.actions)
        _write_debug_artifact(
            ctx.task,
            "ocr_remediation",
            lambda: self.debug_writer.write_ocr_remediation(ctx.task.task_id, summary),
        )
        _emit_progress(ctx, 85, self.name, "ocr_remediation_planned")
```

Add methods to `OcrRemediationStage`:

```python
    def _execute_relocation_actions(self, ctx: PipelineContext, actions: list[OcrRemediationAction]) -> None:
        diffs_by_id = {diff.diff_id: diff for diff in ctx.diffs}
        for action in actions:
            if action.action_type != "RELOCATE_EVIDENCE" or action.status != "PLANNED":
                continue
            if action.side not in {"original", "compare"} or not action.diff_id:
                self._mark_action_skipped(action, "ACTION_NOT_ELIGIBLE")
                continue
            diff = diffs_by_id.get(action.diff_id)
            if diff is None:
                self._mark_action_skipped(action, "DIFF_NOT_FOUND")
                continue
            result = self.relocator.relocate(
                diff,
                side=action.side,
                page_no=action.page_no,
                original_pdf=ctx.original_pdf,
                compare_pdf=ctx.compare_pdf,
            )
            self._apply_relocation_result(diff, action, result)

    @staticmethod
    def _apply_relocation_result(
        diff: DiffItem,
        action: OcrRemediationAction,
        result: EvidenceRelocationResult,
    ) -> None:
        action.status = result.status
        action.before_quality = result.before_quality
        action.after_quality = result.after_quality
        action.changed_evidence = result.changed_evidence
        action.changed_diff_text = False
        action.notes = [*action.notes, *result.notes]
        if result.status == "SUCCEEDED" and result.evidence:
            if action.side == "original":
                diff.original_evidence = result.evidence
            elif action.side == "compare":
                diff.compare_evidence = result.evidence
            OcrRemediationStage._add_flag(diff, "OCR_REMEDIATION_EVIDENCE_RELOCATED")
            if "OCR_REMEDIATION_EVIDENCE_RELOCATED" not in action.review_flags_added:
                action.review_flags_added.append("OCR_REMEDIATION_EVIDENCE_RELOCATED")
            return
        if result.status == "FAILED":
            OcrRemediationStage._add_flag(diff, "OCR_REMEDIATION_UNRESOLVED")
            diff.quality_status = "NEEDS_REVIEW"

    @staticmethod
    def _mark_action_skipped(action: OcrRemediationAction, reason: str) -> None:
        action.status = "SKIPPED"
        action.changed_evidence = False
        action.changed_diff_text = False
        action.notes.append(reason)

    @staticmethod
    def _add_flag(diff: DiffItem, flag: str) -> None:
        if flag not in diff.review_flags:
            diff.review_flags.append(flag)

    @staticmethod
    def _refresh_summary_counts(summary: TaskOcrRemediationSummary) -> None:
        summary.attempted_action_count = len(summary.actions)
        summary.successful_action_count = sum(1 for action in summary.actions if action.status == "SUCCEEDED")
        summary.unresolved_action_count = sum(
            1 for action in summary.actions if action.status in {"PLANNED", "FAILED", "MANUAL_REVIEW_REQUIRED"}
        )
        summary.risk_reduced_diff_count = len(
            {action.diff_id for action in summary.actions if action.status == "SUCCEEDED" and action.diff_id}
        )
        summary.manual_review_required_count = sum(
            1 for action in summary.actions if action.status == "MANUAL_REVIEW_REQUIRED"
        )
        if summary.manual_review_required_count:
            summary.status = "MANUAL_REVIEW_REQUIRED"
        elif summary.unresolved_action_count:
            summary.status = "ACTIONS_PLANNED"
        elif summary.actions:
            summary.status = "OK"
        else:
            summary.status = "OK"
        summary.requires_manual_review = bool(summary.manual_review_required_count or summary.unresolved_action_count)
```

Make sure `TaskOcrRemediationSummary` is imported from `app.models`.

- [ ] **Step 4: Run pipeline tests**

Run:

```bash
cd backend && python -m pytest tests/test_pipeline.py::test_ocr_remediation_stage_executes_evidence_relocation tests/test_pipeline.py::test_ocr_remediation_stage_keeps_unresolved_state_when_relocation_fails -v
```

Expected: both tests pass.

- [ ] **Step 5: Run lint**

Run:

```bash
cd backend && python -m ruff check app/services/pipeline_stages.py tests/test_pipeline.py
```

Expected: all checks pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/pipeline_stages.py backend/tests/test_pipeline.py
git commit -m "feat: execute OCR evidence relocation actions"
```

---

### Task 3: Expose Execution State Through API Tests

**Files:**
- Modify: `backend/tests/test_api.py`

- [ ] **Step 1: Add failing API serialization test**

Add this test to `backend/tests/test_api.py` near existing remediation API tests:

```python
def test_compare_task_response_serializes_executed_ocr_remediation_summary():
    task = CompareTask(task_id="task-api", status="COMPLETED")
    task.ocr_remediation_summary = TaskOcrRemediationSummary(
        status="OK",
        attempted_action_count=1,
        successful_action_count=1,
        unresolved_action_count=0,
        risk_reduced_diff_count=1,
        actions=[
            OcrRemediationAction(
                action_id="original:1:diff-1:RELOCATE_EVIDENCE",
                action_type="RELOCATE_EVIDENCE",
                reason="EVIDENCE_UNRELIABLE",
                status="SUCCEEDED",
                side="original",
                page_no=1,
                diff_id="diff-1",
                before_quality={"max_confidence": 0.46, "methods": ["block_fallback"]},
                after_quality={"max_confidence": 0.98, "methods": ["text_exact"]},
                changed_evidence=True,
                changed_diff_text=False,
                review_flags_added=["OCR_REMEDIATION_PLANNED", "OCR_REMEDIATION_EVIDENCE_RELOCATED"],
            )
        ],
    )

    response = compare_task_response(task)
    action = response.ocr_remediation_summary.actions[0]

    assert action.status == "SUCCEEDED"
    assert action.before_quality["max_confidence"] == 0.46
    assert action.after_quality["max_confidence"] == 0.98
    assert action.changed_evidence is True
    assert action.changed_diff_text is False
```

- [ ] **Step 2: Run test**

Run:

```bash
cd backend && python -m pytest tests/test_api.py::test_compare_task_response_serializes_executed_ocr_remediation_summary -v
```

Expected: pass if schemas already support the fields. If it fails, update only response schemas enough to serialize existing model fields.

- [ ] **Step 3: Run lint**

Run:

```bash
cd backend && python -m ruff check tests/test_api.py app/api_schemas.py app/api_presenters.py
```

Expected: all checks pass.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_api.py backend/app/api_schemas.py backend/app/api_presenters.py
git commit -m "test: cover executed OCR remediation API state"
```

If only the test file changed, commit only `backend/tests/test_api.py`.

---

### Task 4: Confirm Frontend Successful Remediation Badge

**Files:**
- Modify: `frontend/src/pages/ResultPage.test.tsx`
- Modify: `frontend/src/pages/ResultPage.tsx` only if the test fails

- [ ] **Step 1: Add frontend test for `SUCCEEDED` status**

Add a ResultPage test or extend the existing remediation badge test so the mocked task has:

```ts
ocr_remediation_summary: {
  status: "OK",
  requires_manual_review: false,
  attempted_action_count: 1,
  successful_action_count: 1,
  unresolved_action_count: 0,
  risk_reduced_page_count: 0,
  risk_reduced_diff_count: 1,
  manual_review_required_count: 0,
  actions: [
    {
      action_id: "original:1:diff-1:RELOCATE_EVIDENCE",
      action_type: "RELOCATE_EVIDENCE",
      reason: "EVIDENCE_UNRELIABLE",
      status: "SUCCEEDED",
      side: "original",
      page_no: 1,
      diff_id: "diff-1",
      before_quality: { max_confidence: 0.46 },
      after_quality: { max_confidence: 0.98 },
      changed_evidence: true,
      changed_diff_text: false,
      review_flags_added: ["OCR_REMEDIATION_EVIDENCE_RELOCATED"],
      notes: ["Evidence was relocated with deterministic PDF text search."],
    },
  ],
},
```

Assert:

```ts
expect(await screen.findAllByText("已自动处置")).not.toHaveLength(0);
```

- [ ] **Step 2: Run frontend test**

Run:

```bash
cd frontend && npm test -- src/pages/ResultPage.test.tsx
```

Expected: pass if current badge mapping already supports `SUCCEEDED`.

- [ ] **Step 3: Fix ResultPage only if needed**

If the test fails because `SUCCEEDED` is not mapped, update `remediationBadgeForDiff()` in `frontend/src/pages/ResultPage.tsx` to return:

```tsx
if (action.status === "SUCCEEDED") {
  return { className: "merged", label: "已自动处置" };
}
```

- [ ] **Step 4: Run frontend build**

Run:

```bash
cd frontend && npm run build
```

Expected: build passes.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/ResultPage.test.tsx frontend/src/pages/ResultPage.tsx
git commit -m "test: cover successful OCR remediation badge"
```

If only the test file changed, commit only `frontend/src/pages/ResultPage.test.tsx`.

---

### Task 5: Full Verification and Final Review

**Files:**
- No source edits expected.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
cd backend && python -m pytest tests/test_evidence_relocator.py tests/test_ocr_remediation.py tests/test_pipeline.py tests/test_api.py -v
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

Expected: all Vitest tests pass and production build exits 0.

- [ ] **Step 4: Run OCR quality gate**

Run:

```bash
cd backend && python scripts/evaluate_ocr_compare_quality.py tests/fixtures/ocr_compare_cases --output .ocr-compare-quality/ocr_compare_quality.json --html-output .ocr-compare-quality/html --fail-on-threshold
cd backend && jq '{threshold_failures, aggregate: .aggregate}' .ocr-compare-quality/ocr_compare_quality.json
```

Expected: `threshold_failures` is `[]`; precision, recall, and evidence_hit_rate remain at the current fixture baseline.

- [ ] **Step 5: Run full backend tests**

Run:

```bash
cd backend && python -m pytest
```

Expected: all backend tests pass.

- [ ] **Step 6: Request final review**

Ask a reviewer to inspect the full Phase 3B implementation range. Review focus:

- `RELOCATE_EVIDENCE` execution is deterministic.
- No diff text is changed.
- No OCR retry, table repair, or model call was added.
- Evidence replacement happens only on confidence improvement.
- Failed relocation keeps manual review state.
- API/frontend compatibility remains additive.
- Remediation state still survives DiffQuality merge, SummaryStage dedupe, and persistence.

- [ ] **Step 7: Finish branch options**

After final review passes, use `superpowers:finishing-a-development-branch` and present:

```text
1. Merge back to main locally
2. Push and create a Pull Request
3. Keep the branch as-is
4. Discard this work
```

