# OCR Runtime Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add runtime OCR quality profiling that marks OCR-risk pages, propagates risk to affected diffs, exposes the summary through APIs, and renders lightweight review badges.

**Architecture:** Add typed OCR quality models to the existing compare task payload, then build a deterministic `OcrQualityProfiler` from existing extraction diagnostics. Insert an `OcrQualityStage` after evidence location so page risk can be mapped to diff evidence before the existing diff quality stage. Extend existing API presenters, quality summary, frontend types, and audit badges without changing public API paths or OCR extraction behavior.

**Tech Stack:** Python 3.12, Pydantic models, FastAPI schemas/presenters, existing compare pipeline stages, pytest, React/TypeScript, Vitest.

---

## Scope

This plan implements Phase 2 from `docs/superpowers/specs/2026-06-25-ocr-runtime-quality-design.md`.

In scope:

- OCR quality data models.
- Deterministic profiler using existing extraction signals.
- Pipeline stage that writes `ocr_quality.json`.
- Diff review flag propagation.
- Task and quality API output.
- Frontend types and audit badges.
- Backend and frontend tests.

Out of scope:

- OCR retry or repair.
- PP-OCRv6 / PP-StructureV3 routing.
- LLM or VLM correction.
- Rewriting diff text.
- Page heatmaps or a new OCR diagnostics screen.

## File Structure

- Modify `backend/app/models.py`
  - Add `OcrQualityStatus`, `OcrQualitySide`, `PageOcrQualityProfile`, and `TaskOcrQualitySummary`.
  - Add `ocr_quality_summary` to `CompareTask`.
- Create `backend/app/services/ocr_quality.py`
  - Owns profile generation and diff risk propagation.
  - No HTTP, repository, or filesystem writes.
- Modify `backend/app/services/compare_debug.py`
  - Add `write_ocr_quality(task_id, summary)` helper.
- Modify `backend/app/services/pipeline_stages.py`
  - Add `OcrQualityStage`.
  - Insert it after `EvidenceStage()` and before `DiffQualityStage(...)`.
- Modify `backend/app/api_schemas.py`
  - Add response schemas for OCR quality.
  - Add `ocr_quality_summary` to `CompareTaskResponse`.
- Modify `backend/app/api_presenters.py`
  - Include OCR quality summary in task responses.
- Modify `backend/app/services/review_service.py`
  - Include OCR quality summary and counts in `/quality`.
- Create `backend/tests/test_ocr_quality.py`
  - Unit tests for profiler and propagation.
- Modify `backend/tests/test_pipeline.py`
  - Stage integration and artifact tests.
- Modify `backend/tests/test_api.py`
  - API serialization and `/quality` tests.
- Modify `frontend/src/types.ts`
  - Add OCR quality types and summary fields.
- Modify `frontend/src/pages/ResultPage.tsx`
  - Render OCR review badges through `auditQualityBadges`.
- Modify `frontend/src/pages/ResultPage.test.tsx`
  - Test new badges and summary parsing.

---

### Task 1: Backend OCR Quality Models

**Files:**
- Modify: `backend/app/models.py`
- Test: `backend/tests/test_ocr_quality.py`

- [ ] **Step 1: Write failing model tests**

Create `backend/tests/test_ocr_quality.py`:

```python
from app.models import PageOcrQualityProfile, TaskOcrQualitySummary


def test_task_ocr_quality_summary_defaults_and_counts() -> None:
    profile = PageOcrQualityProfile(
        side="original",
        page_no=1,
        status="LOW_TEXT_CONFIDENCE",
        score=0.75,
        reasons=["LOW_AVG_CONFIDENCE"],
        metrics={"avg_confidence": 0.7},
        affected_diff_ids=["D001"],
    )

    summary = TaskOcrQualitySummary(
        status="LOW_TEXT_CONFIDENCE",
        requires_review=True,
        page_count_by_status={"LOW_TEXT_CONFIDENCE": 1},
        risk_page_count=1,
        affected_diff_count=1,
        profiles=[profile],
    )

    assert summary.status == "LOW_TEXT_CONFIDENCE"
    assert summary.profiles[0].side == "original"
    assert summary.profiles[0].metrics["avg_confidence"] == 0.7
```

- [ ] **Step 2: Run model test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_ocr_quality.py::test_task_ocr_quality_summary_defaults_and_counts -v
```

Expected: FAIL with `ImportError` for missing `PageOcrQualityProfile` or `TaskOcrQualitySummary`.

- [ ] **Step 3: Add OCR quality models**

In `backend/app/models.py`, add these aliases near existing literal aliases:

```python
OcrQualityStatus = Literal[
    "OK",
    "LOW_TEXT_CONFIDENCE",
    "LAYOUT_MISMATCH",
    "READING_ORDER_RISK",
    "TABLE_RISK",
    "SEAL_OR_SIGNATURE_RISK",
    "UNRELIABLE",
]
OcrQualitySide = Literal["original", "compare"]
```

Add these models after `LayoutQualityReport`:

```python
class PageOcrQualityProfile(BaseModel):
    side: OcrQualitySide
    page_no: int
    status: OcrQualityStatus = "OK"
    score: float = Field(default=1.0, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    affected_diff_ids: list[str] = Field(default_factory=list)


class TaskOcrQualitySummary(BaseModel):
    status: OcrQualityStatus = "OK"
    requires_review: bool = False
    page_count_by_status: dict[str, int] = Field(default_factory=dict)
    risk_page_count: int = 0
    affected_diff_count: int = 0
    profiles: list[PageOcrQualityProfile] = Field(default_factory=list)
```

Add this field to `CompareTask` after `document_profiles`:

```python
    ocr_quality_summary: TaskOcrQualitySummary | None = None
```

- [ ] **Step 4: Run model test to verify it passes**

Run:

```bash
cd backend
python -m pytest tests/test_ocr_quality.py::test_task_ocr_quality_summary_defaults_and_counts -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models.py backend/tests/test_ocr_quality.py
git commit -m "feat: add OCR quality models"
```

---

### Task 2: Deterministic OCR Quality Profiler

**Files:**
- Create: `backend/app/services/ocr_quality.py`
- Modify: `backend/tests/test_ocr_quality.py`

- [ ] **Step 1: Add profiler tests**

Append to `backend/tests/test_ocr_quality.py`:

```python
from app.models import (
    BBox,
    Document,
    DocumentProfile,
    LayoutQualityReport,
    Page,
    PageLayoutQualityReport,
    PageProfile,
    ParseWarningDetail,
    TextBlock,
)
from app.services.ocr_quality import OcrQualityProfiler


def _document_with_blocks(blocks: list[TextBlock]) -> Document:
    return Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[Page(page_no=1, width=600, height=800, blocks=blocks)],
    )


def _block(block_id: str, text: str, confidence: float | None, block_type: str = "text") -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=1,
        text=text,
        bbox=BBox(x0=10, y0=10, x1=100, y1=30),
        block_type=block_type,
        confidence=confidence,
    )


def test_profiler_creates_low_text_confidence_profile() -> None:
    document = _document_with_blocks([
        _block("b1", "付款30日", 0.6),
        _block("b2", "交付设备", 0.7),
    ])
    profile = DocumentProfile(
        filename="sample.pdf",
        page_count=1,
        page_profiles=[PageProfile(page_no=1, avg_confidence=0.65, text_block_count=2)],
    )

    result = OcrQualityProfiler().profile_side(
        side="original",
        document=document,
        document_profile=profile,
        layout_quality=None,
        warnings=[],
    )

    assert result[0].status == "LOW_TEXT_CONFIDENCE"
    assert "LOW_AVG_CONFIDENCE" in result[0].reasons
    assert result[0].score < 1.0


def test_profiler_creates_layout_and_reading_order_risks() -> None:
    document = _document_with_blocks([_block("b1", "第一条", 0.95)])
    layout_quality = LayoutQualityReport(
        page_count=1,
        ocr_block_count=4,
        matched_ocr_block_count=2,
        meaningful_unmatched_count=1,
        reading_order_conflict_count=1,
        page_quality=[
            PageLayoutQualityReport(
                page_no=1,
                ocr_block_count=4,
                matched_ocr_block_count=2,
                meaningful_unmatched_count=1,
                reading_order_conflict_count=1,
            )
        ],
    )

    result = OcrQualityProfiler().profile_side(
        side="compare",
        document=document,
        document_profile=None,
        layout_quality=layout_quality,
        warnings=[],
    )

    assert result[0].status == "UNRELIABLE"
    assert "MEANINGFUL_UNMATCHED_OCR" in result[0].reasons
    assert "LOW_LAYOUT_MATCH_RATE" in result[0].reasons
    assert "READING_ORDER_CONFLICT" in result[0].reasons


def test_profiler_creates_table_risk_from_unmatched_cells() -> None:
    document = _document_with_blocks([_block("t1", "金额", 0.92, block_type="table")])
    profile = DocumentProfile(
        filename="sample.pdf",
        page_count=1,
        page_profiles=[PageProfile(page_no=1, table_heavy=True, table_block_count=1)],
    )
    layout_quality = LayoutQualityReport(
        page_count=1,
        table_cell_unmatched_count=2,
        page_quality=[PageLayoutQualityReport(page_no=1, table_cell_unmatched_count=2)],
    )

    result = OcrQualityProfiler().profile_side(
        side="original",
        document=document,
        document_profile=profile,
        layout_quality=layout_quality,
        warnings=[],
    )

    assert result[0].status == "TABLE_RISK"
    assert "TABLE_CELL_UNMATCHED" in result[0].reasons


def test_profiler_marks_error_warning_unreliable() -> None:
    document = _document_with_blocks([_block("b1", "签署页", 0.9)])
    warning = ParseWarningDetail(
        code="OCR_ENGINE_ERROR",
        message="OCR engine returned partial page result",
        severity="ERROR",
        page_no=1,
        source="ocr",
    )

    result = OcrQualityProfiler().profile_side(
        side="compare",
        document=document,
        document_profile=None,
        layout_quality=None,
        warnings=[warning],
    )

    assert result[0].status == "UNRELIABLE"
    assert "EXTRACTION_ERROR_WARNING" in result[0].reasons
```

- [ ] **Step 2: Run profiler tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_ocr_quality.py -v
```

Expected: FAIL with `ModuleNotFoundError` for `app.services.ocr_quality`.

- [ ] **Step 3: Implement profiler**

Create `backend/app/services/ocr_quality.py`:

```python
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.models import (
    Document,
    DocumentProfile,
    LayoutQualityReport,
    OcrQualitySide,
    OcrQualityStatus,
    PageOcrQualityProfile,
    ParseWarningDetail,
    TaskOcrQualitySummary,
)


STATUS_PRIORITY: dict[OcrQualityStatus, int] = {
    "OK": 0,
    "SEAL_OR_SIGNATURE_RISK": 1,
    "LOW_TEXT_CONFIDENCE": 2,
    "LAYOUT_MISMATCH": 3,
    "READING_ORDER_RISK": 4,
    "TABLE_RISK": 5,
    "UNRELIABLE": 6,
}


@dataclass(frozen=True)
class OcrQualityThresholds:
    low_avg_confidence: float = 0.75
    low_block_confidence: float = 0.75
    low_confidence_block_ratio: float = 0.20
    min_layout_match_rate: float = 0.85


class OcrQualityProfiler:
    def __init__(self, thresholds: OcrQualityThresholds | None = None) -> None:
        self.thresholds = thresholds or OcrQualityThresholds()

    def profile_side(
        self,
        *,
        side: OcrQualitySide,
        document: Document,
        document_profile: DocumentProfile | None,
        layout_quality: LayoutQualityReport | None,
        warnings: list[ParseWarningDetail] | list[str],
    ) -> list[PageOcrQualityProfile]:
        layout_by_page = {
            item.page_no: item
            for item in (layout_quality.page_quality if layout_quality else [])
        }
        profile_by_page = {
            item.page_no: item
            for item in (document_profile.page_profiles if document_profile else [])
        }
        warning_details = self._coerce_warnings(warnings)
        warnings_by_page: dict[int, list[ParseWarningDetail]] = {}
        for warning in warning_details:
            if warning.page_no is not None:
                warnings_by_page.setdefault(warning.page_no, []).append(warning)

        profiles: list[PageOcrQualityProfile] = []
        for page in sorted(document.pages, key=lambda item: item.page_no):
            reasons: list[str] = []
            metrics: dict[str, Any] = {
                "text_block_count": len(page.blocks),
                "ocr_block_count": len(page.blocks),
            }
            page_profile = profile_by_page.get(page.page_no)
            page_layout = layout_by_page.get(page.page_no)

            confidences = [
                block.confidence
                for block in page.blocks
                if block.confidence is not None
            ]
            avg_confidence = (
                page_profile.avg_confidence
                if page_profile and page_profile.avg_confidence is not None
                else (sum(confidences) / len(confidences) if confidences else None)
            )
            if avg_confidence is not None:
                metrics["avg_confidence"] = round(avg_confidence, 4)
                if avg_confidence < self.thresholds.low_avg_confidence:
                    reasons.append("LOW_AVG_CONFIDENCE")
            text_blocks = [block for block in page.blocks if block.text.strip()]
            low_blocks = [
                block
                for block in text_blocks
                if block.confidence is not None and block.confidence < self.thresholds.low_block_confidence
            ]
            if text_blocks:
                low_ratio = len(low_blocks) / len(text_blocks)
                metrics["low_confidence_block_ratio"] = round(low_ratio, 4)
                if low_ratio > self.thresholds.low_confidence_block_ratio:
                    reasons.append("LOW_CONFIDENCE_BLOCK_RATIO")
            if text_blocks and not any(block.char_boxes for block in text_blocks):
                reasons.append("MISSING_CHAR_CONFIDENCE")

            if page_layout is not None:
                metrics.update(
                    {
                        "matched_ocr_block_count": page_layout.matched_ocr_block_count,
                        "meaningful_unmatched_count": page_layout.meaningful_unmatched_count,
                        "reading_order_conflict_count": page_layout.reading_order_conflict_count,
                        "table_cell_unmatched_count": page_layout.table_cell_unmatched_count,
                    }
                )
                if page_layout.meaningful_unmatched_count > 0:
                    reasons.append("MEANINGFUL_UNMATCHED_OCR")
                if page_layout.ocr_block_count > 0:
                    match_rate = page_layout.matched_ocr_block_count / page_layout.ocr_block_count
                    metrics["layout_match_rate"] = round(match_rate, 4)
                    if match_rate < self.thresholds.min_layout_match_rate:
                        reasons.append("LOW_LAYOUT_MATCH_RATE")
                if page_layout.reading_order_conflict_count > 0:
                    reasons.append("READING_ORDER_CONFLICT")
                if page_layout.table_cell_unmatched_count > 0:
                    reasons.append("TABLE_CELL_UNMATCHED")

            if page_profile is not None and page_profile.table_heavy and not self._has_table_structure(page):
                reasons.append("TABLE_HEAVY_WITHOUT_STRUCTURE")

            for warning in warnings_by_page.get(page.page_no, []):
                if warning.severity == "ERROR":
                    reasons.append("EXTRACTION_ERROR_WARNING")
                if warning.code == "LAYOUT_LOW_MATCH_RATE":
                    reasons.append("LAYOUT_LOW_MATCH_RATE")

            reasons = sorted(set(reasons))
            status = self._status_for_reasons(reasons)
            score = self._score_for_reasons(reasons)
            profiles.append(
                PageOcrQualityProfile(
                    side=side,
                    page_no=page.page_no,
                    status=status,
                    score=score,
                    reasons=reasons,
                    metrics=metrics,
                )
            )
        return profiles

    def build_summary(self, profiles: list[PageOcrQualityProfile]) -> TaskOcrQualitySummary:
        ordered = sorted(profiles, key=lambda item: (item.side, item.page_no))
        counts = Counter(item.status for item in ordered)
        status = max((item.status for item in ordered), key=lambda item: STATUS_PRIORITY[item], default="OK")
        risk_page_count = sum(1 for item in ordered if item.status != "OK")
        affected = {diff_id for item in ordered for diff_id in item.affected_diff_ids}
        return TaskOcrQualitySummary(
            status=status,
            requires_review=risk_page_count > 0 or bool(affected),
            page_count_by_status=dict(counts),
            risk_page_count=risk_page_count,
            affected_diff_count=len(affected),
            profiles=ordered,
        )

    def _status_for_reasons(self, reasons: list[str]) -> OcrQualityStatus:
        if "EXTRACTION_ERROR_WARNING" in reasons or len(self._risk_categories(reasons)) >= 2:
            return "UNRELIABLE"
        if any(reason.startswith("TABLE_") for reason in reasons):
            return "TABLE_RISK"
        if "READING_ORDER_CONFLICT" in reasons:
            return "READING_ORDER_RISK"
        if any(reason in {"MEANINGFUL_UNMATCHED_OCR", "LOW_LAYOUT_MATCH_RATE", "LAYOUT_LOW_MATCH_RATE"} for reason in reasons):
            return "LAYOUT_MISMATCH"
        if any(reason in {"LOW_AVG_CONFIDENCE", "LOW_CONFIDENCE_BLOCK_RATIO", "MISSING_CHAR_CONFIDENCE"} for reason in reasons):
            return "LOW_TEXT_CONFIDENCE"
        if "SEAL_OR_SIGNATURE_INCOMPLETE" in reasons:
            return "SEAL_OR_SIGNATURE_RISK"
        return "OK"

    def _score_for_reasons(self, reasons: list[str]) -> float:
        score = 1.0
        if any(reason in {"LOW_AVG_CONFIDENCE", "LOW_CONFIDENCE_BLOCK_RATIO", "MISSING_CHAR_CONFIDENCE"} for reason in reasons):
            score -= 0.25
        if any(reason in {"MEANINGFUL_UNMATCHED_OCR", "LOW_LAYOUT_MATCH_RATE", "LAYOUT_LOW_MATCH_RATE"} for reason in reasons):
            score -= 0.25
        if "READING_ORDER_CONFLICT" in reasons:
            score -= 0.20
        if any(reason.startswith("TABLE_") for reason in reasons):
            score -= 0.20
        if "SEAL_OR_SIGNATURE_INCOMPLETE" in reasons:
            score -= 0.15
        return round(max(0.0, score), 4)

    def _risk_categories(self, reasons: list[str]) -> set[str]:
        categories: set[str] = set()
        if any(reason in {"LOW_AVG_CONFIDENCE", "LOW_CONFIDENCE_BLOCK_RATIO", "MISSING_CHAR_CONFIDENCE"} for reason in reasons):
            categories.add("text")
        if any(reason in {"MEANINGFUL_UNMATCHED_OCR", "LOW_LAYOUT_MATCH_RATE", "LAYOUT_LOW_MATCH_RATE"} for reason in reasons):
            categories.add("layout")
        if "READING_ORDER_CONFLICT" in reasons:
            categories.add("reading_order")
        if any(reason.startswith("TABLE_") for reason in reasons):
            categories.add("table")
        if "SEAL_OR_SIGNATURE_INCOMPLETE" in reasons:
            categories.add("seal")
        return categories

    @staticmethod
    def _has_table_structure(page) -> bool:
        return any(block.block_type == "table" or block.table_id or block.raw_html for block in page.blocks)

    @staticmethod
    def _coerce_warnings(warnings: list[ParseWarningDetail] | list[str]) -> list[ParseWarningDetail]:
        result: list[ParseWarningDetail] = []
        for warning in warnings:
            if isinstance(warning, ParseWarningDetail):
                result.append(warning)
            elif isinstance(warning, str) and warning:
                result.append(ParseWarningDetail(code="OCR_WARNING", message=warning, source="ocr"))
        return result
```

- [ ] **Step 4: Run profiler tests**

Run:

```bash
cd backend
python -m pytest tests/test_ocr_quality.py -v
```

Expected: all tests in `tests/test_ocr_quality.py` pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ocr_quality.py backend/tests/test_ocr_quality.py
git commit -m "feat: profile OCR quality pages"
```

---

### Task 3: Diff Risk Propagation

**Files:**
- Modify: `backend/app/services/ocr_quality.py`
- Modify: `backend/tests/test_ocr_quality.py`

- [ ] **Step 1: Add propagation tests**

Append to `backend/tests/test_ocr_quality.py`:

```python
from app.models import DiffItem, EvidenceBox, PageOcrQualityProfile


def _evidence(page_no: int) -> EvidenceBox:
    return EvidenceBox(page_no=page_no, bbox=BBox(x0=1, y0=2, x1=3, y1=4), confidence=0.9)


def test_propagates_unreliable_page_to_diff_review_status() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        title="付款",
        original_text="30日付款",
        compare_text="45日付款",
        original_evidence=[_evidence(1)],
    )
    profiles = [
        PageOcrQualityProfile(
            side="original",
            page_no=1,
            status="UNRELIABLE",
            score=0.3,
            reasons=["LOW_AVG_CONFIDENCE", "READING_ORDER_CONFLICT"],
        )
    ]

    summary = OcrQualityProfiler().apply_to_diffs(diffs=[diff], profiles=profiles)

    assert diff.quality_status == "NEEDS_REVIEW"
    assert "PAGE_UNRELIABLE" in diff.review_flags
    assert "OCR_LOW_CONFIDENCE" in diff.review_flags
    assert "READING_ORDER_RISK" in diff.review_flags
    assert profiles[0].affected_diff_ids == ["D001"]
    assert summary.affected_diff_count == 1


def test_low_confidence_business_diff_needs_review() -> None:
    diff = DiffItem(
        diff_id="D002",
        diff_type="MODIFY",
        title="付款金额",
        original_text="付款金额为100万元",
        compare_text="付款金额为120万元",
        compare_evidence=[_evidence(2)],
    )
    profiles = [
        PageOcrQualityProfile(
            side="compare",
            page_no=2,
            status="LOW_TEXT_CONFIDENCE",
            score=0.75,
            reasons=["LOW_AVG_CONFIDENCE"],
        )
    ]

    OcrQualityProfiler().apply_to_diffs(diffs=[diff], profiles=profiles)

    assert "OCR_LOW_CONFIDENCE" in diff.review_flags
    assert diff.quality_status == "NEEDS_REVIEW"


def test_missing_evidence_with_risk_marks_evidence_unreliable() -> None:
    diff = DiffItem(
        diff_id="D003",
        diff_type="ADD",
        title="新增条款",
        compare_text="新增付款条款",
        source_type="clause",
    )
    profiles = [
        PageOcrQualityProfile(
            side="compare",
            page_no=1,
            status="TABLE_RISK",
            score=0.8,
            reasons=["TABLE_CELL_UNMATCHED"],
        )
    ]

    OcrQualityProfiler().apply_to_diffs(diffs=[diff], profiles=profiles)

    assert "EVIDENCE_UNRELIABLE" in diff.review_flags
    assert diff.quality_status == "NEEDS_REVIEW"
```

- [ ] **Step 2: Run propagation tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_ocr_quality.py::test_propagates_unreliable_page_to_diff_review_status tests/test_ocr_quality.py::test_low_confidence_business_diff_needs_review tests/test_ocr_quality.py::test_missing_evidence_with_risk_marks_evidence_unreliable -v
```

Expected: FAIL with missing `apply_to_diffs`.

- [ ] **Step 3: Implement propagation**

Add these imports to `backend/app/services/ocr_quality.py`:

```python
import re

from app.models import DiffItem
```

Add this class attribute and methods inside `OcrQualityProfiler`:

```python
    business_token_pattern = re.compile(
        r"(\d|%|‰|元|万元|亿元|付款|支付|金额|日期|期限|甲方|乙方|公司|交付|违约|责任|终止|解除)"
    )

    def apply_to_diffs(
        self,
        *,
        diffs: list[DiffItem],
        profiles: list[PageOcrQualityProfile],
    ) -> TaskOcrQualitySummary:
        profile_by_key = {(profile.side, profile.page_no): profile for profile in profiles if profile.status != "OK"}
        affected: set[str] = set()
        for diff in diffs:
            matched_profiles = self._profiles_for_diff(diff, profile_by_key)
            if not matched_profiles and self._diff_without_evidence_needs_ocr_review(diff, profile_by_key):
                self._add_flag(diff, "EVIDENCE_UNRELIABLE")
                diff.quality_status = "NEEDS_REVIEW"
                affected.add(diff.diff_id)
                continue
            for profile in matched_profiles:
                self._apply_profile_to_diff(diff, profile)
                profile.affected_diff_ids = sorted(set(profile.affected_diff_ids) | {diff.diff_id})
                affected.add(diff.diff_id)
        summary = self.build_summary(profiles)
        summary.affected_diff_count = len(affected)
        summary.requires_review = summary.requires_review or bool(affected)
        return summary

    def _profiles_for_diff(
        self,
        diff: DiffItem,
        profile_by_key: dict[tuple[str, int], PageOcrQualityProfile],
    ) -> list[PageOcrQualityProfile]:
        profiles: list[PageOcrQualityProfile] = []
        for evidence in diff.original_evidence:
            profile = profile_by_key.get(("original", evidence.page_no))
            if profile is not None:
                profiles.append(profile)
        for evidence in diff.compare_evidence:
            profile = profile_by_key.get(("compare", evidence.page_no))
            if profile is not None:
                profiles.append(profile)
        unique: dict[tuple[str, int], PageOcrQualityProfile] = {
            (profile.side, profile.page_no): profile
            for profile in profiles
        }
        return [unique[key] for key in sorted(unique)]

    def _apply_profile_to_diff(self, diff: DiffItem, profile: PageOcrQualityProfile) -> None:
        if profile.status == "UNRELIABLE":
            self._add_flag(diff, "PAGE_UNRELIABLE")
            diff.quality_status = "NEEDS_REVIEW"
        if profile.status in {"LOW_TEXT_CONFIDENCE", "UNRELIABLE"}:
            self._add_flag(diff, "OCR_LOW_CONFIDENCE")
            if self._has_business_token(diff):
                diff.quality_status = "NEEDS_REVIEW"
        if profile.status in {"LAYOUT_MISMATCH", "UNRELIABLE"}:
            self._add_flag(diff, "LAYOUT_MISMATCH_RISK")
            diff.quality_status = "NEEDS_REVIEW"
        if profile.status in {"READING_ORDER_RISK", "UNRELIABLE"}:
            self._add_flag(diff, "READING_ORDER_RISK")
            diff.quality_status = "NEEDS_REVIEW"
        if profile.status in {"TABLE_RISK", "UNRELIABLE"}:
            self._add_flag(diff, "TABLE_STRUCTURE_UNRELIABLE")
            diff.quality_status = "NEEDS_REVIEW"
        if profile.status in {"SEAL_OR_SIGNATURE_RISK", "UNRELIABLE"}:
            self._add_flag(diff, "SEAL_OR_SIGNATURE_RISK")

    def _diff_without_evidence_needs_ocr_review(
        self,
        diff: DiffItem,
        profile_by_key: dict[tuple[str, int], PageOcrQualityProfile],
    ) -> bool:
        has_evidence = bool(diff.original_evidence or diff.compare_evidence)
        return (
            not has_evidence
            and bool(profile_by_key)
            and diff.source_type in {"clause", "page", "table", "metadata"}
        )

    def _has_business_token(self, diff: DiffItem) -> bool:
        text = " ".join([diff.title, diff.original_text, diff.compare_text, diff.original_snippet, diff.compare_snippet])
        return bool(self.business_token_pattern.search(text))

    @staticmethod
    def _add_flag(diff: DiffItem, flag: str) -> None:
        if flag not in diff.review_flags:
            diff.review_flags.append(flag)
```

- [ ] **Step 4: Run propagation tests**

Run:

```bash
cd backend
python -m pytest tests/test_ocr_quality.py -v
```

Expected: all tests in `tests/test_ocr_quality.py` pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ocr_quality.py backend/tests/test_ocr_quality.py
git commit -m "feat: propagate OCR quality to diffs"
```

---

### Task 4: Pipeline Stage And Debug Artifact

**Files:**
- Modify: `backend/app/services/compare_debug.py`
- Modify: `backend/app/services/pipeline_stages.py`
- Modify: `backend/tests/test_pipeline.py`

- [ ] **Step 1: Add pipeline stage test**

Append to `backend/tests/test_pipeline.py`:

```python
from app.config import Settings
from app.infrastructure.artifact_store import LocalArtifactStore
from app.models import EvidenceBox, LayoutQualityReport, PageLayoutQualityReport
from app.services.extractors.base import ExtractionResult
from app.services.pipeline import PipelineContext
from app.services.pipeline_stages import OcrQualityStage


def test_ocr_quality_stage_writes_artifact_and_flags_diff(tmp_path: Path) -> None:
    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    original_doc = Document(
        filename="original.pdf",
        path="original.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=600,
                height=800,
                blocks=[
                    TextBlock(
                        block_id="O1",
                        page_no=1,
                        text="付款30日",
                        bbox=BBox(x0=10, y0=10, x1=100, y1=40),
                        confidence=0.6,
                    )
                ],
            )
        ],
    )
    compare_doc = Document(
        filename="compare.pdf",
        path="compare.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=600,
                height=800,
                blocks=[
                    TextBlock(
                        block_id="C1",
                        page_no=1,
                        text="付款45日",
                        bbox=BBox(x0=10, y0=10, x1=100, y1=40),
                        confidence=0.95,
                    )
                ],
            )
        ],
    )
    task = CompareTask(task_id="TOCRQUALITY")
    ctx = PipelineContext(
        task=task,
        original_pdf=tmp_path / "original.pdf",
        compare_pdf=tmp_path / "compare.pdf",
    )
    ctx.set_extractions(
        ExtractionResult(
            document=original_doc,
            extractor_used="ppstructure_ocr_hybrid",
            layout_quality=LayoutQualityReport(
                page_count=1,
                page_quality=[
                    PageLayoutQualityReport(
                        page_no=1,
                        ocr_block_count=2,
                        matched_ocr_block_count=1,
                        meaningful_unmatched_count=1,
                    )
                ],
            ),
        ),
        ExtractionResult(document=compare_doc, extractor_used="ppstructure_ocr_hybrid"),
    )
    ctx.diffs = [
        DiffItem(
            diff_id="D001",
            diff_type="MODIFY",
            title="付款",
            original_text="付款30日",
            compare_text="付款45日",
            original_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=10, y0=10, x1=100, y1=40))],
        )
    ]

    OcrQualityStage(artifact_store=artifact_store).execute(ctx)

    assert task.ocr_quality_summary is not None
    assert task.ocr_quality_summary.risk_page_count == 1
    assert task.ocr_quality_summary.affected_diff_count == 1
    assert "ocr_quality" in task.debug_artifact_paths
    assert Path(task.debug_artifact_paths["ocr_quality"]).exists()
    assert ctx.diffs[0].quality_status == "NEEDS_REVIEW"
    assert "OCR_LOW_CONFIDENCE" in ctx.diffs[0].review_flags
```

Add the imports shown above to the existing import blocks, deduplicating names already imported.

- [ ] **Step 2: Run stage test to verify it fails**

Run:

```bash
cd backend
python -m pytest tests/test_pipeline.py::test_ocr_quality_stage_writes_artifact_and_flags_diff -v
```

Expected: FAIL with missing `OcrQualityStage` or missing `write_ocr_quality`.

- [ ] **Step 3: Add debug writer method**

In `backend/app/services/compare_debug.py`, add this method to `CompareDebugWriter`:

```python
    def write_ocr_quality(self, task_id: str, summary) -> str:
        return str(self._write_json(task_id, "ocr_quality.json", to_jsonable(summary)))
```

Use the existing private `_write_json` helper in `CompareDebugWriter`; do not call a non-existent artifact-store debug JSON method.

- [ ] **Step 4: Add `OcrQualityStage`**

In `backend/app/services/pipeline_stages.py`, import the profiler:

```python
from app.services.ocr_quality import OcrQualityProfiler
```

Add `OcrQualityStage` after `EvidenceStage`:

```python
class OcrQualityStage:
    name = "OCR质量评估中"
    start_progress = 83
    progress = 84

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.profiler = OcrQualityProfiler()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        original_extraction, compare_extraction = ctx.require_extractions()
        original_profiles = self.profiler.profile_side(
            side="original",
            document=original_extraction.document,
            document_profile=original_extraction.profile,
            layout_quality=original_extraction.layout_quality,
            warnings=original_extraction.warnings,
        )
        compare_profiles = self.profiler.profile_side(
            side="compare",
            document=compare_extraction.document,
            document_profile=compare_extraction.profile,
            layout_quality=compare_extraction.layout_quality,
            warnings=compare_extraction.warnings,
        )
        profiles = [*original_profiles, *compare_profiles]
        summary = self.profiler.apply_to_diffs(diffs=ctx.diffs, profiles=profiles)
        ctx.task.ocr_quality_summary = summary
        for profile in summary.profiles:
            if profile.status == "OK":
                continue
            _append_warning_details(
                ctx.task,
                [
                    ParseWarningDetail(
                        code=f"OCR_QUALITY_{profile.status}",
                        message=f"{profile.side} 第 {profile.page_no} 页 OCR 质量风险: {', '.join(profile.reasons)}",
                        severity="WARNING" if profile.status != "UNRELIABLE" else "ERROR",
                        page_no=profile.page_no,
                        source=f"ocr_quality:{profile.side}",
                    )
                ],
            )
        _write_debug_artifact(
            ctx.task,
            "ocr_quality",
            lambda: self.debug_writer.write_ocr_quality(ctx.task.task_id, summary),
        )
        _emit_progress(ctx, self.progress, self.name, "ocr_quality_done")
```

Insert the stage in `CompareService._build_pipeline()` after `EvidenceStage()` and before `DiffQualityStage(...)`:

```python
                EvidenceStage(),
                OcrQualityStage(artifact_store=self.artifact_store),
                DiffQualityStage(artifact_store=self.artifact_store),
```

Ensure `CompareService._build_pipeline()` imports `OcrQualityStage` from `pipeline_stages`.

- [ ] **Step 5: Run stage test**

Run:

```bash
cd backend
python -m pytest tests/test_pipeline.py::test_ocr_quality_stage_writes_artifact_and_flags_diff -v
```

Expected: PASS.

- [ ] **Step 6: Run OCR quality tests**

Run:

```bash
cd backend
python -m pytest tests/test_ocr_quality.py tests/test_pipeline.py::test_ocr_quality_stage_writes_artifact_and_flags_diff -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/compare_debug.py backend/app/services/pipeline_stages.py backend/app/services/compare_service.py backend/tests/test_pipeline.py
git commit -m "feat: add OCR quality pipeline stage"
```

---

### Task 5: API And Quality Summary Output

**Files:**
- Modify: `backend/app/api_schemas.py`
- Modify: `backend/app/api_presenters.py`
- Modify: `backend/app/services/review_service.py`
- Modify: `backend/tests/test_api.py`

- [ ] **Step 1: Add API tests**

Append to `backend/tests/test_api.py`:

```python
from app.models import PageOcrQualityProfile, TaskOcrQualitySummary


def test_api_exposes_ocr_quality_summary(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    task = CompareTask(
        task_id="TOCRAPI",
        status="COMPLETED",
        diff_count=1,
        ocr_quality_summary=TaskOcrQualitySummary(
            status="LOW_TEXT_CONFIDENCE",
            requires_review=True,
            page_count_by_status={"LOW_TEXT_CONFIDENCE": 1},
            risk_page_count=1,
            affected_diff_count=1,
            profiles=[
                PageOcrQualityProfile(
                    side="original",
                    page_no=1,
                    status="LOW_TEXT_CONFIDENCE",
                    score=0.75,
                    reasons=["LOW_AVG_CONFIDENCE"],
                    affected_diff_ids=["D001"],
                )
            ],
        ),
    )
    save_task(task)

    client = TestClient(app)
    response = client.get("/api/compare/TOCRAPI")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ocr_quality_summary"]["status"] == "LOW_TEXT_CONFIDENCE"
    assert payload["ocr_quality_summary"]["risk_page_count"] == 1
    assert payload["ocr_quality_summary"]["profiles"][0]["affected_diff_ids"] == ["D001"]


def test_quality_summary_includes_ocr_quality_counts(tmp_path: Path) -> None:
    configure_storage(tmp_path)
    save_task(
        CompareTask(
            task_id="TOCRQUALITYAPI",
            status="COMPLETED",
            ocr_quality_summary=TaskOcrQualitySummary(
                status="TABLE_RISK",
                requires_review=True,
                page_count_by_status={"TABLE_RISK": 1},
                risk_page_count=1,
                affected_diff_count=2,
                profiles=[
                    PageOcrQualityProfile(
                        side="compare",
                        page_no=2,
                        status="TABLE_RISK",
                        score=0.8,
                        reasons=["TABLE_CELL_UNMATCHED"],
                    )
                ],
            ),
        )
    )

    client = TestClient(app)
    response = client.get("/api/compare/TOCRQUALITYAPI/quality")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ocr_quality_summary"]["status"] == "TABLE_RISK"
    assert payload["ocr_risk_page_count"] == 1
    assert payload["ocr_affected_diff_count"] == 2
```

- [ ] **Step 2: Run API tests to verify they fail**

Run:

```bash
cd backend
python -m pytest tests/test_api.py::test_api_exposes_ocr_quality_summary tests/test_api.py::test_quality_summary_includes_ocr_quality_counts -v
```

Expected: FAIL because response schemas do not expose `ocr_quality_summary`.

- [ ] **Step 3: Add API schemas**

In `backend/app/api_schemas.py`, add `OcrQualityStatus` and `OcrQualitySide` aliases near other aliases:

```python
OcrQualityStatus = Literal[
    "OK",
    "LOW_TEXT_CONFIDENCE",
    "LAYOUT_MISMATCH",
    "READING_ORDER_RISK",
    "TABLE_RISK",
    "SEAL_OR_SIGNATURE_RISK",
    "UNRELIABLE",
]
OcrQualitySide = Literal["original", "compare"]
```

Add response models after `EvidenceBoxResponse`:

```python
class PageOcrQualityProfileResponse(BaseModel):
    side: OcrQualitySide
    page_no: int
    status: OcrQualityStatus = "OK"
    score: float = Field(default=1.0, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    affected_diff_ids: list[str] = Field(default_factory=list)


class TaskOcrQualitySummaryResponse(BaseModel):
    status: OcrQualityStatus = "OK"
    requires_review: bool = False
    page_count_by_status: dict[str, int] = Field(default_factory=dict)
    risk_page_count: int = 0
    affected_diff_count: int = 0
    profiles: list[PageOcrQualityProfileResponse] = Field(default_factory=list)
```

Add to `CompareTaskResponse`:

```python
    ocr_quality_summary: TaskOcrQualitySummaryResponse | None = None
```

- [ ] **Step 4: Update presenter and quality service**

In `backend/app/api_presenters.py`, add this to `compare_task_response` data:

```python
        "ocr_quality_summary": to_jsonable(task.ocr_quality_summary) if task.ocr_quality_summary else None,
```

In `backend/app/services/review_service.py`, add to `CompareQualityService.build_summary()` return dict:

```python
            "ocr_quality_summary": to_jsonable(task.ocr_quality_summary) if task.ocr_quality_summary else None,
            "ocr_risk_page_count": task.ocr_quality_summary.risk_page_count if task.ocr_quality_summary else 0,
            "ocr_affected_diff_count": task.ocr_quality_summary.affected_diff_count if task.ocr_quality_summary else 0,
```

- [ ] **Step 5: Run API tests**

Run:

```bash
cd backend
python -m pytest tests/test_api.py::test_api_exposes_ocr_quality_summary tests/test_api.py::test_quality_summary_includes_ocr_quality_counts -v
```

Expected: PASS.

- [ ] **Step 6: Run related backend tests**

Run:

```bash
cd backend
python -m pytest tests/test_ocr_quality.py tests/test_api.py::test_api_exposes_ocr_quality_summary tests/test_api.py::test_quality_summary_includes_ocr_quality_counts -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api_schemas.py backend/app/api_presenters.py backend/app/services/review_service.py backend/tests/test_api.py
git commit -m "feat: expose OCR quality summaries"
```

---

### Task 6: Frontend Types And Audit Badges

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/pages/ResultPage.tsx`
- Modify: `frontend/src/pages/ResultPage.test.tsx`

- [ ] **Step 1: Add frontend test expectations**

In `frontend/src/pages/ResultPage.test.tsx`, find an existing diff fixture with `review_flags`. Add OCR flags to one fixture:

```ts
review_flags: ["OCR_LOW_CONFIDENCE", "PAGE_UNRELIABLE", "TABLE_STRUCTURE_UNRELIABLE"],
quality_status: "NEEDS_REVIEW",
```

Add assertions to the result-page test that renders audit cards:

```ts
expect(await screen.findByText("低置信 OCR")).toBeInTheDocument();
expect(screen.getByText("页面不可靠")).toBeInTheDocument();
expect(screen.getByText("表格识别风险")).toBeInTheDocument();
```

Use the existing mocked ResultPage task/diff setup in `ResultPage.test.tsx`; add OCR flags to a diff fixture rendered by the audit list and assert those labels.

- [ ] **Step 2: Run frontend test to verify it fails**

Run:

```bash
cd frontend
npm test -- ResultPage.test.tsx
```

Expected: FAIL because OCR labels are not rendered.

- [ ] **Step 3: Add OCR quality types**

In `frontend/src/types.ts`, add:

```ts
export type OcrQualityStatus =
  | "OK"
  | "LOW_TEXT_CONFIDENCE"
  | "LAYOUT_MISMATCH"
  | "READING_ORDER_RISK"
  | "TABLE_RISK"
  | "SEAL_OR_SIGNATURE_RISK"
  | "UNRELIABLE";

export type OcrQualitySide = "original" | "compare";

export interface PageOcrQualityProfile {
  side: OcrQualitySide;
  page_no: number;
  status: OcrQualityStatus;
  score: number;
  reasons: string[];
  metrics: Record<string, number | string | boolean>;
  affected_diff_ids: string[];
}

export interface TaskOcrQualitySummary {
  status: OcrQualityStatus;
  requires_review: boolean;
  page_count_by_status: Record<string, number>;
  risk_page_count: number;
  affected_diff_count: number;
  profiles: PageOcrQualityProfile[];
}
```

Add to `CompareResponse`:

```ts
  ocr_quality_summary?: TaskOcrQualitySummary | null;
```

Add to `CompareQualitySummary`:

```ts
  ocr_quality_summary?: TaskOcrQualitySummary | null;
  ocr_risk_page_count?: number;
  ocr_affected_diff_count?: number;
```

- [ ] **Step 4: Render OCR badges**

In `frontend/src/pages/ResultPage.tsx`, update `auditQualityBadges`:

```tsx
  if (item.reviewFlags.includes("OCR_LOW_CONFIDENCE")) {
    badges.push({ className: "needs-review", label: "低置信 OCR" });
  }
  if (item.reviewFlags.includes("PAGE_UNRELIABLE")) {
    badges.push({ className: "needs-review", label: "页面不可靠" });
  }
  if (item.reviewFlags.includes("TABLE_STRUCTURE_UNRELIABLE")) {
    badges.push({ className: "needs-review", label: "表格识别风险" });
  }
```

Place these after the existing `NEEDS_REVIEW` badge so the general status appears first.

- [ ] **Step 5: Run frontend tests**

Run:

```bash
cd frontend
npm test -- ResultPage.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Run TypeScript build**

Run:

```bash
cd frontend
npm run build
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types.ts frontend/src/pages/ResultPage.tsx frontend/src/pages/ResultPage.test.tsx
git commit -m "feat: show OCR quality review badges"
```

---

### Task 7: Full Verification And Smoke Gate

**Files:**
- No source changes expected.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
cd backend
python -m pytest tests/test_ocr_quality.py tests/test_api.py tests/test_pipeline.py -v
```

Expected: PASS.

- [ ] **Step 2: Run backend syntax and lint**

Run:

```bash
cd backend
python -m compileall app tests scripts
python -m ruff check .
```

Expected: both commands exit 0.

- [ ] **Step 3: Run full backend suite**

Run:

```bash
cd backend
python -m pytest
```

Expected: PASS.

- [ ] **Step 4: Run frontend suite and build**

Run:

```bash
cd frontend
npm test
npm run build
```

Expected: both commands exit 0.

- [ ] **Step 6: Commit verification fixes only if needed**

If any command fails and the fix is in scope for Phase 2, fix it and commit:

```bash
git add backend/app backend/tests frontend/src
git commit -m "fix: stabilize OCR runtime quality"
```

If all commands pass without file changes, skip this commit.

---

## Self-Review Notes

Spec coverage:

- Data model is covered by Task 1.
- Profiling rules are covered by Task 2.
- Diff risk propagation is covered by Task 3.
- Pipeline stage and debug artifact are covered by Task 4.
- API output and quality summary are covered by Task 5.
- Frontend types and badges are covered by Task 6.
- Verification and Phase 1 smoke gate are covered by Task 7.

Intentional exclusions:

- OCR retry and repair are excluded.
- PP-OCRv6 and PP-StructureV3 shadow routing are excluded.
- LLM/VLM correction is excluded.
- Page heatmap and new diagnostics UI are excluded.
