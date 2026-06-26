# OCR Model Routing And Retry Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a conservative model-routing and page-level retry evaluation layer that recommends OCR handling strategies without changing production comparison output.

**Architecture:** Create a pure `ModelRoutingAnalyzer` service that consumes existing OCR quality profiles, diffs, and warnings to produce route recommendations. Add a no-op retry adapter contract, write route summaries to debug artifacts through a lightweight pipeline stage, and extend the OCR comparison evaluator JSON/HTML reports with route metrics.

**Tech Stack:** FastAPI backend domain models, Pydantic models, existing comparison pipeline stages, `CompareDebugWriter`, pytest, existing OCR comparison evaluator.

---

## Scope

Included:

- Deterministic page classification and route recommendation service.
- No-op OCR retry adapter interface.
- Debug artifact `ocr_model_routing.json`.
- Pipeline stage that writes routing recommendations and does not mutate diffs.
- OCR compare evaluator route records and aggregate route metrics.
- HTML report route section.

Deferred:

- Real OCR retry execution.
- Alternate OCR engine integration.
- Public API fields or endpoints for routing.
- Frontend route display.
- Reviewer feedback endpoints.

## File Structure

- Create `backend/app/services/model_routing.py`
  - Owns page classification, route recommendation, and summary aggregation.
- Create `backend/tests/test_model_routing.py`
  - Unit tests for classifications, recommendations, and no mutation.
- Create `backend/app/services/ocr_retry.py`
  - Defines retry request/result models and default no-op adapter.
- Create `backend/tests/test_ocr_retry.py`
  - Unit tests for no-op retry behavior.
- Modify `backend/app/services/compare_debug.py`
  - Add `write_model_routing()`.
- Modify `backend/app/services/pipeline_stages.py`
  - Add `ModelRoutingStage`.
- Modify `backend/app/services/pipeline.py`
  - Include `ModelRoutingStage` in default stage order.
- Modify `backend/app/services/compare_service.py`
  - Include `ModelRoutingStage` in service-built pipeline.
- Modify `backend/tests/test_pipeline.py`
  - Cover stage order and debug artifact without diff mutation.
- Modify `backend/scripts/evaluate_ocr_compare_quality.py`
  - Add route records and route metrics to JSON output and HTML report.
- Modify `backend/tests/test_evaluate_ocr_compare_quality.py`
  - Cover route metrics and HTML route rendering.

---

### Task 1: Add Model Routing Analyzer

**Files:**
- Create: `backend/app/services/model_routing.py`
- Create: `backend/tests/test_model_routing.py`

- [ ] **Step 1: Write failing analyzer tests**

Create `backend/tests/test_model_routing.py`:

```python
from app.models import (
    BBox,
    DiffItem,
    EvidenceBox,
    PageOcrQualityProfile,
    ParseWarningDetail,
    TaskOcrQualitySummary,
)
from app.services.model_routing import ModelRoutingAnalyzer


def _summary(*profiles: PageOcrQualityProfile) -> TaskOcrQualitySummary:
    return TaskOcrQualitySummary(
        status=profiles[0].status if profiles else "OK",
        requires_review=any(profile.status != "OK" for profile in profiles),
        profiles=list(profiles),
    )


def _diff(diff_id: str = "D001", source_type: str = "clause", text: str = "付款金额为100元") -> DiffItem:
    return DiffItem(
        diff_id=diff_id,
        diff_type="MODIFY",
        source_type=source_type,
        original_text=text,
        compare_text=text.replace("100", "120"),
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=10, y0=10, x1=100, y1=30),
                confidence=0.46,
                evidence_quality="LOW",
            )
        ],
        review_flags=["OCR_LOW_CONFIDENCE"],
        quality_status="NEEDS_REVIEW",
    )


def test_analyzer_recommends_high_dpi_retry_for_low_confidence_critical_text_page() -> None:
    profile = PageOcrQualityProfile(
        side="original",
        page_no=1,
        status="LOW_TEXT_CONFIDENCE",
        reasons=["LOW_AVG_CONFIDENCE"],
        affected_diff_ids=["D001"],
    )

    summary = ModelRoutingAnalyzer().analyze(_summary(profile), [_diff()])

    route = summary.routes[0]
    assert route.page_type == "scan_low_quality"
    assert route.recommended_route == "HIGH_DPI_PAGE_RETRY"
    assert route.should_execute is False
    assert "LOW_AVG_CONFIDENCE" in route.reason_codes
    assert summary.retry_recommended_count == 1


def test_analyzer_recommends_table_region_retry_for_table_risk() -> None:
    profile = PageOcrQualityProfile(
        side="compare",
        page_no=1,
        status="TABLE_RISK",
        reasons=["TABLE_CELL_UNMATCHED"],
        affected_diff_ids=["D001"],
    )

    summary = ModelRoutingAnalyzer().analyze(_summary(profile), [_diff(source_type="table")])

    route = summary.routes[0]
    assert route.page_type == "table_heavy"
    assert route.recommended_route == "TABLE_REGION_RETRY"
    assert summary.page_count_by_type == {"table_heavy": 1}
    assert summary.route_count_by_recommendation == {"TABLE_REGION_RETRY": 1}


def test_analyzer_recommends_manual_review_for_unreliable_mixed_page() -> None:
    profile = PageOcrQualityProfile(
        side="original",
        page_no=1,
        status="UNRELIABLE",
        reasons=["LOW_AVG_CONFIDENCE", "TABLE_CELL_UNMATCHED"],
        affected_diff_ids=["D001"],
    )

    summary = ModelRoutingAnalyzer().analyze(_summary(profile), [_diff(source_type="table")])

    route = summary.routes[0]
    assert route.page_type == "mixed"
    assert route.recommended_route == "MANUAL_REVIEW"
    assert summary.manual_review_recommended_count == 1


def test_analyzer_recommends_keep_current_for_ok_page_without_affected_diffs() -> None:
    profile = PageOcrQualityProfile(side="original", page_no=1, status="OK")

    summary = ModelRoutingAnalyzer().analyze(_summary(profile), [])

    route = summary.routes[0]
    assert route.page_type == "text_heavy"
    assert route.recommended_route == "KEEP_CURRENT"
    assert summary.retry_recommended_count == 0


def test_analyzer_uses_warnings_for_seal_signature_routing() -> None:
    profile = PageOcrQualityProfile(
        side="compare",
        page_no=2,
        status="SEAL_OR_SIGNATURE_RISK",
        reasons=["SEAL_OR_SIGNATURE_INCOMPLETE"],
        affected_diff_ids=["D009"],
    )
    warning = ParseWarningDetail(
        code="SEAL_REGION_INCOMPLETE",
        message="seal recognition incomplete",
        page_no=2,
        source="ocr_quality:compare",
    )

    summary = ModelRoutingAnalyzer().analyze(_summary(profile), [_diff("D009", source_type="seal")], [warning])

    route = summary.routes[0]
    assert route.page_type == "seal_signature"
    assert route.recommended_route == "MANUAL_REVIEW"
    assert "SEAL_REGION_INCOMPLETE" in route.reason_codes


def test_analyzer_does_not_mutate_diffs() -> None:
    diff = _diff()
    original_flags = list(diff.review_flags)
    original_evidence = list(diff.original_evidence)
    profile = PageOcrQualityProfile(
        side="original",
        page_no=1,
        status="LOW_TEXT_CONFIDENCE",
        reasons=["LOW_AVG_CONFIDENCE"],
        affected_diff_ids=["D001"],
    )

    ModelRoutingAnalyzer().analyze(_summary(profile), [diff])

    assert diff.review_flags == original_flags
    assert diff.original_evidence == original_evidence
    assert diff.quality_status == "NEEDS_REVIEW"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend && python -m pytest tests/test_model_routing.py -v
```

Expected: import error for `app.services.model_routing`.

- [ ] **Step 3: Implement analyzer service**

Create `backend/app/services/model_routing.py`:

```python
from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from app.models import DiffItem, PageOcrQualityProfile, ParseWarningDetail, TaskOcrQualitySummary


PageRouteType = Literal["text_heavy", "table_heavy", "scan_low_quality", "seal_signature", "mixed"]
RouteRecommendation = Literal[
    "KEEP_CURRENT",
    "HIGH_DPI_PAGE_RETRY",
    "TABLE_REGION_RETRY",
    "CRITICAL_FIELD_RETRY",
    "MANUAL_REVIEW",
    "NO_ROUTE",
]
RouteSummaryStatus = Literal["OK", "RETRY_RECOMMENDED", "MANUAL_REVIEW_RECOMMENDED"]
CostLevel = Literal["none", "low", "medium", "high"]

CRITICAL_TOKENS = (
    "金额",
    "价款",
    "付款",
    "支付",
    "日期",
    "期限",
    "交付",
    "验收",
    "违约",
    "责任",
    "终止",
    "解除",
    "数量",
    "单价",
    "总价",
    "party",
    "payment",
    "delivery",
    "liability",
    "termination",
    "breach",
)
RETRY_RECOMMENDATIONS = {"HIGH_DPI_PAGE_RETRY", "TABLE_REGION_RETRY", "CRITICAL_FIELD_RETRY"}


class PageModelRoute(BaseModel):
    side: Literal["original", "compare"]
    page_no: int
    page_type: PageRouteType
    recommended_route: RouteRecommendation
    reason_codes: list[str] = Field(default_factory=list)
    affected_diff_ids: list[str] = Field(default_factory=list)
    quality_status: str = "OK"
    estimated_cost_level: CostLevel = "none"
    should_execute: bool = False
    notes: list[str] = Field(default_factory=list)


class TaskModelRoutingSummary(BaseModel):
    status: RouteSummaryStatus = "OK"
    route_count: int = 0
    retry_recommended_count: int = 0
    manual_review_recommended_count: int = 0
    page_count_by_type: dict[str, int] = Field(default_factory=dict)
    route_count_by_recommendation: dict[str, int] = Field(default_factory=dict)
    routes: list[PageModelRoute] = Field(default_factory=list)


class ModelRoutingAnalyzer:
    """Recommend OCR routing strategies from existing quality signals without mutating diffs."""

    def analyze(
        self,
        ocr_quality_summary: TaskOcrQualitySummary | None,
        diffs: list[DiffItem],
        warnings: list[ParseWarningDetail] | None = None,
    ) -> TaskModelRoutingSummary:
        profiles = list(ocr_quality_summary.profiles) if ocr_quality_summary is not None else []
        routes = [
            self._route_for_profile(profile, diffs, warnings or [])
            for profile in sorted(profiles, key=lambda item: (item.side, item.page_no))
        ]
        return self._summary(routes)

    def _route_for_profile(
        self,
        profile: PageOcrQualityProfile,
        diffs: list[DiffItem],
        warnings: list[ParseWarningDetail],
    ) -> PageModelRoute:
        affected_diffs = [diff for diff in diffs if diff.diff_id in set(profile.affected_diff_ids)]
        warning_codes = self._warning_codes(profile, warnings)
        reason_codes = sorted({*profile.reasons, *warning_codes})
        page_type = self._page_type(profile, affected_diffs, reason_codes)
        recommendation = self._recommendation(profile, affected_diffs, page_type)
        return PageModelRoute(
            side=profile.side,
            page_no=profile.page_no,
            page_type=page_type,
            recommended_route=recommendation,
            reason_codes=reason_codes,
            affected_diff_ids=list(profile.affected_diff_ids),
            quality_status=profile.status,
            estimated_cost_level=self._cost_level(recommendation),
            should_execute=False,
            notes=[f"score={profile.score:.2f}"],
        )

    def _page_type(
        self,
        profile: PageOcrQualityProfile,
        affected_diffs: list[DiffItem],
        reason_codes: list[str],
    ) -> PageRouteType:
        categories: set[PageRouteType] = set()
        if profile.status in {"LOW_TEXT_CONFIDENCE", "UNRELIABLE"} or any(
            reason in reason_codes
            for reason in {"LOW_AVG_CONFIDENCE", "LOW_CONFIDENCE_BLOCK_RATIO", "MISSING_CHAR_CONFIDENCE"}
        ):
            categories.add("scan_low_quality")
        if profile.status == "TABLE_RISK" or "TABLE_CELL_UNMATCHED" in reason_codes or any(
            diff.source_type == "table" for diff in affected_diffs
        ):
            categories.add("table_heavy")
        if profile.status == "SEAL_OR_SIGNATURE_RISK" or any(
            "SEAL" in reason or "SIGNATURE" in reason or "STAMP" in reason for reason in reason_codes
        ) or any(diff.source_type == "seal" for diff in affected_diffs):
            categories.add("seal_signature")
        if len(categories) > 1:
            return "mixed"
        if categories:
            return next(iter(categories))
        return "text_heavy"

    def _recommendation(
        self,
        profile: PageOcrQualityProfile,
        affected_diffs: list[DiffItem],
        page_type: PageRouteType,
    ) -> RouteRecommendation:
        if profile.status == "OK" and not affected_diffs:
            return "KEEP_CURRENT"
        if profile.status == "UNRELIABLE" or page_type in {"mixed", "seal_signature"}:
            return "MANUAL_REVIEW"
        if page_type == "table_heavy":
            return "TABLE_REGION_RETRY"
        if page_type == "scan_low_quality" and self._has_critical_text(affected_diffs):
            return "HIGH_DPI_PAGE_RETRY"
        if page_type == "text_heavy" and self._has_critical_text(affected_diffs):
            return "CRITICAL_FIELD_RETRY"
        if affected_diffs:
            return "MANUAL_REVIEW"
        return "NO_ROUTE"

    @staticmethod
    def _has_critical_text(diffs: list[DiffItem]) -> bool:
        text = " ".join(
            f"{diff.title} {diff.original_text} {diff.compare_text} {diff.original_snippet} {diff.compare_snippet}"
            for diff in diffs
        ).lower()
        return any(token.lower() in text for token in CRITICAL_TOKENS)

    @staticmethod
    def _warning_codes(profile: PageOcrQualityProfile, warnings: list[ParseWarningDetail]) -> list[str]:
        return [
            warning.code
            for warning in warnings
            if warning.page_no == profile.page_no and profile.side in warning.source
        ]

    @staticmethod
    def _cost_level(recommendation: RouteRecommendation) -> CostLevel:
        if recommendation in {"KEEP_CURRENT", "NO_ROUTE"}:
            return "none"
        if recommendation == "CRITICAL_FIELD_RETRY":
            return "low"
        if recommendation in {"TABLE_REGION_RETRY", "HIGH_DPI_PAGE_RETRY"}:
            return "medium"
        return "high"

    @staticmethod
    def _summary(routes: list[PageModelRoute]) -> TaskModelRoutingSummary:
        type_counts = Counter(route.page_type for route in routes)
        recommendation_counts = Counter(route.recommended_route for route in routes)
        retry_count = sum(1 for route in routes if route.recommended_route in RETRY_RECOMMENDATIONS)
        manual_count = recommendation_counts["MANUAL_REVIEW"]
        status: RouteSummaryStatus = "OK"
        if manual_count:
            status = "MANUAL_REVIEW_RECOMMENDED"
        elif retry_count:
            status = "RETRY_RECOMMENDED"
        return TaskModelRoutingSummary(
            status=status,
            route_count=len(routes),
            retry_recommended_count=retry_count,
            manual_review_recommended_count=manual_count,
            page_count_by_type=dict(type_counts),
            route_count_by_recommendation=dict(recommendation_counts),
            routes=routes,
        )
```

- [ ] **Step 4: Run analyzer tests**

Run:

```bash
cd backend && python -m pytest tests/test_model_routing.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Run lint**

Run:

```bash
cd backend && python -m ruff check app/services/model_routing.py tests/test_model_routing.py
```

Expected: all checks pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/model_routing.py backend/tests/test_model_routing.py
git commit -m "feat: add OCR model routing analyzer"
```

---

### Task 2: Add No-Op OCR Retry Adapter Contract

**Files:**
- Create: `backend/app/services/ocr_retry.py`
- Create: `backend/tests/test_ocr_retry.py`

- [ ] **Step 1: Write failing retry adapter tests**

Create `backend/tests/test_ocr_retry.py`:

```python
from pathlib import Path

from app.services.ocr_retry import NoopOcrRetryAdapter, OcrRetryRequest


def test_noop_retry_adapter_reports_retry_disabled() -> None:
    request = OcrRetryRequest(
        task_id="task-retry",
        side="original",
        page_no=2,
        pdf_path=Path("original.pdf"),
        route="HIGH_DPI_PAGE_RETRY",
        reason_codes=["LOW_AVG_CONFIDENCE"],
    )

    result = NoopOcrRetryAdapter().retry_page(request)

    assert result.status == "SKIPPED"
    assert result.reason == "RETRY_DISABLED"
    assert result.changed_output is False
    assert result.route == "HIGH_DPI_PAGE_RETRY"
    assert result.side == "original"
    assert result.page_no == 2


def test_noop_retry_adapter_does_not_require_existing_pdf() -> None:
    request = OcrRetryRequest(
        task_id="task-retry",
        side="compare",
        page_no=1,
        pdf_path=Path("missing.pdf"),
        route="TABLE_REGION_RETRY",
        reason_codes=[],
    )

    result = NoopOcrRetryAdapter().retry_page(request)

    assert result.status == "SKIPPED"
    assert result.metrics == {}
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend && python -m pytest tests/test_ocr_retry.py -v
```

Expected: import error for `app.services.ocr_retry`.

- [ ] **Step 3: Implement retry adapter**

Create `backend/app/services/ocr_retry.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Protocol, Literal

from pydantic import BaseModel, Field

from app.services.model_routing import RouteRecommendation


OcrRetryStatus = Literal["SKIPPED", "SUCCEEDED", "FAILED"]


class OcrRetryRequest(BaseModel):
    task_id: str
    side: Literal["original", "compare"]
    page_no: int
    pdf_path: Path
    route: RouteRecommendation
    reason_codes: list[str] = Field(default_factory=list)


class OcrRetryResult(BaseModel):
    status: OcrRetryStatus
    reason: str
    side: Literal["original", "compare"]
    page_no: int
    route: RouteRecommendation
    changed_output: bool = False
    metrics: dict[str, object] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class OcrRetryAdapter(Protocol):
    def retry_page(self, request: OcrRetryRequest) -> OcrRetryResult:
        """Retry OCR for one page or region."""


class NoopOcrRetryAdapter:
    def retry_page(self, request: OcrRetryRequest) -> OcrRetryResult:
        return OcrRetryResult(
            status="SKIPPED",
            reason="RETRY_DISABLED",
            side=request.side,
            page_no=request.page_no,
            route=request.route,
            changed_output=False,
            notes=["OCR retry execution is disabled in Phase 4A."],
        )
```

- [ ] **Step 4: Run retry adapter tests**

Run:

```bash
cd backend && python -m pytest tests/test_ocr_retry.py -v
```

Expected: both tests pass.

- [ ] **Step 5: Run lint**

Run:

```bash
cd backend && python -m ruff check app/services/ocr_retry.py tests/test_ocr_retry.py
```

Expected: all checks pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ocr_retry.py backend/tests/test_ocr_retry.py
git commit -m "feat: add OCR retry adapter contract"
```

---

### Task 3: Write Model Routing Debug Artifact In Pipeline

**Files:**
- Modify: `backend/app/services/compare_debug.py`
- Modify: `backend/app/services/pipeline_stages.py`
- Modify: `backend/app/services/pipeline.py`
- Modify: `backend/app/services/compare_service.py`
- Modify: `backend/tests/test_pipeline.py`

- [ ] **Step 1: Add failing pipeline tests**

Modify imports in `backend/tests/test_pipeline.py` to include `ModelRoutingStage` from `app.services.pipeline_stages`.

Add these tests near existing OCR remediation pipeline tests:

```python
def test_model_routing_stage_writes_debug_artifact_without_mutating_diffs(tmp_path: Path) -> None:
    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    task = CompareTask(
        task_id="task-model-routing",
        ocr_quality_summary=TaskOcrQualitySummary(
            status="LOW_TEXT_CONFIDENCE",
            requires_review=True,
            profiles=[
                PageOcrQualityProfile(
                    side="original",
                    page_no=1,
                    status="LOW_TEXT_CONFIDENCE",
                    reasons=["LOW_AVG_CONFIDENCE"],
                    affected_diff_ids=["D001"],
                )
            ],
        ),
    )
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="clause",
        original_text="付款金额为100元",
        compare_text="付款金额为120元",
        review_flags=["OCR_LOW_CONFIDENCE"],
        quality_status="NEEDS_REVIEW",
    )
    ctx = PipelineContext(task=task, original_pdf=tmp_path / "o.pdf", compare_pdf=tmp_path / "c.pdf")
    ctx.diffs = [diff]

    ModelRoutingStage(artifact_store=artifact_store).execute(ctx)

    assert ctx.diffs == [diff]
    artifact_path = Path(task.debug_artifact_paths["ocr_model_routing"])
    assert artifact_path == tmp_path / "storage" / "tasks" / "task-model-routing" / "debug" / "ocr_model_routing.json"
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["status"] == "RETRY_RECOMMENDED"
    assert payload["routes"][0]["recommended_route"] == "HIGH_DPI_PAGE_RETRY"
    assert payload["routes"][0]["should_execute"] is False
```

Update existing order tests:

```python
def test_default_stages_include_ocr_quality_before_diff_quality(self) -> None:
    stage_names = [type(stage).__name__ for stage in ComparePipeline().stages]

    assert stage_names[stage_names.index("EvidenceStage") : stage_names.index("DiffQualityStage") + 1] == [
        "EvidenceStage",
        "OcrQualityStage",
        "OcrRemediationStage",
        "ModelRoutingStage",
        "DiffQualityStage",
    ]
    progress_values = {
        type(stage).__name__: (stage.start_progress, stage.progress)
        for stage in ComparePipeline().stages
    }
    assert progress_values["OcrQualityStage"] == (83, 84)
    assert progress_values["OcrRemediationStage"] == (84, 85)
    assert progress_values["ModelRoutingStage"] == (85, 85)
    assert progress_values["DiffQualityStage"] == (85, 86)
    assert progress_values["VisualizationStage"] == (86, 87)


def test_compare_service_pipeline_includes_model_routing_before_diff_quality(self) -> None:
    stage_names = [type(stage).__name__ for stage in CompareService()._build_pipeline().stages]

    assert stage_names[stage_names.index("OcrQualityStage") : stage_names.index("DiffQualityStage") + 1] == [
        "OcrQualityStage",
        "OcrRemediationStage",
        "ModelRoutingStage",
        "DiffQualityStage",
    ]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend && python -m pytest tests/test_pipeline.py::test_model_routing_stage_writes_debug_artifact_without_mutating_diffs tests/test_pipeline.py::TestComparePipeline::test_default_stages_include_ocr_quality_before_diff_quality tests/test_pipeline.py::TestComparePipeline::test_compare_service_pipeline_includes_model_routing_before_diff_quality -v
```

Expected: import error for `ModelRoutingStage` or assertion failure because the stage is not present.

- [ ] **Step 3: Add debug writer method**

In `backend/app/services/compare_debug.py`, add after `write_ocr_remediation()`:

```python
    def write_model_routing(self, task_id: str, summary) -> str:
        return str(self._write_json(task_id, "ocr_model_routing.json", to_jsonable(summary)))
```

- [ ] **Step 4: Add pipeline stage**

In `backend/app/services/pipeline_stages.py`, import:

```python
from app.services.model_routing import ModelRoutingAnalyzer
```

Add this class after `OcrRemediationStage`:

```python
class ModelRoutingStage:
    name = "OCR模型路由评估中"
    start_progress = 85
    progress = 85

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.analyzer = ModelRoutingAnalyzer()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        summary = self.analyzer.analyze(
            ctx.task.ocr_quality_summary,
            ctx.diffs,
            ctx.task.parse_warning_details,
        )
        _write_debug_artifact(
            ctx.task,
            "ocr_model_routing",
            lambda: self.debug_writer.write_model_routing(ctx.task.task_id, summary),
        )
        _emit_progress(ctx, 85, self.name, "model_routing_evaluated")
```

This stage must not set new task fields or mutate `ctx.diffs`.

- [ ] **Step 5: Add stage to default pipelines**

In `backend/app/services/pipeline.py`, import `ModelRoutingStage` and add it between `OcrRemediationStage()` and `DiffQualityStage()` in `_default_stages()`.

In `backend/app/services/compare_service.py`, import `ModelRoutingStage` and add `ModelRoutingStage(artifact_store=self.artifact_store)` between `OcrRemediationStage(...)` and `DiffQualityStage(...)`.

- [ ] **Step 6: Run pipeline tests**

Run:

```bash
cd backend && python -m pytest tests/test_pipeline.py::test_model_routing_stage_writes_debug_artifact_without_mutating_diffs tests/test_pipeline.py::TestComparePipeline::test_default_stages_include_ocr_quality_before_diff_quality tests/test_pipeline.py::TestComparePipeline::test_compare_service_pipeline_includes_model_routing_before_diff_quality -v
```

Expected: all tests pass.

- [ ] **Step 7: Run related pipeline suite and lint**

Run:

```bash
cd backend && python -m pytest tests/test_model_routing.py tests/test_pipeline.py -v
cd backend && python -m ruff check app/services/model_routing.py app/services/compare_debug.py app/services/pipeline_stages.py app/services/pipeline.py app/services/compare_service.py tests/test_model_routing.py tests/test_pipeline.py
```

Expected: all tests and lint pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/compare_debug.py backend/app/services/pipeline_stages.py backend/app/services/pipeline.py backend/app/services/compare_service.py backend/tests/test_pipeline.py
git commit -m "feat: write OCR model routing debug artifact"
```

---

### Task 4: Add Route Metrics To OCR Compare Evaluator JSON

**Files:**
- Modify: `backend/scripts/evaluate_ocr_compare_quality.py`
- Modify: `backend/tests/test_evaluate_ocr_compare_quality.py`

This task adds authoritative count-level route metrics. It also emits empty quality-by-recommendation maps for `precision_by_recommendation`, `recall_by_recommendation`, `evidence_hit_rate_by_recommendation`, and `low_confidence_ratio_by_recommendation` so the report shape is stable; those maps must stay empty until a later phase implements reviewed diff-to-route attribution instead of inferring quality from page-level recommendations.

- [ ] **Step 1: Add failing evaluator tests**

In `backend/tests/test_evaluate_ocr_compare_quality.py`, add imports:

```python
from app.models import PageOcrQualityProfile, TaskOcrQualitySummary
```

Add these tests:

```python
def test_evaluate_case_includes_model_route_records(tmp_path: Path) -> None:
    case_dir = tmp_path / "route_case"
    case_dir.mkdir()
    (case_dir / "expected.json").write_text(
        json.dumps(
            {
                "case_id": "route_case",
                "expected_diffs": [
                    {
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title_contains": "付款",
                        "original_contains": "100元",
                        "compare_contains": "120元",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "actual.json").write_text(
        json.dumps(
            {
                "task_id": "EVAL_ROUTE_CASE",
                "status": "COMPLETED",
                "parse_warning_details": [],
                "ocr_quality_summary": TaskOcrQualitySummary(
                    status="LOW_TEXT_CONFIDENCE",
                    requires_review=True,
                    profiles=[
                        PageOcrQualityProfile(
                            side="original",
                            page_no=1,
                            status="LOW_TEXT_CONFIDENCE",
                            reasons=["LOW_AVG_CONFIDENCE"],
                            affected_diff_ids=["D001"],
                        )
                    ],
                ).model_dump(mode="json"),
                "diffs": [
                    {
                        "diff_id": "D001",
                        "diff_type": "MODIFY",
                        "source_type": "clause",
                        "title": "付款",
                        "original_text": "付款金额为100元",
                        "compare_text": "付款金额为120元",
                        "review_flags": ["OCR_LOW_CONFIDENCE"],
                        "quality_status": "NEEDS_REVIEW",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_case(discover_cases(tmp_path)[0]).to_dict()

    assert result["model_routing"]["route_count"] == 1
    assert result["model_routing"]["routes"][0]["recommended_route"] == "HIGH_DPI_PAGE_RETRY"
    assert result["route_metrics"]["route_count_by_recommendation"] == {"HIGH_DPI_PAGE_RETRY": 1}
    assert result["route_metrics"]["page_count_by_type"] == {"scan_low_quality": 1}
    assert result["route_metrics"]["precision_by_recommendation"] == {}


def test_evaluate_case_root_aggregates_route_metrics() -> None:
    report = evaluate_case_root(Path("tests/fixtures/ocr_compare_cases"))

    assert "route_metrics" in report["aggregate"]
    assert isinstance(report["aggregate"]["route_metrics"]["route_count_by_recommendation"], dict)
    assert "route_metrics" in report["cases"][0]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd backend && python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_includes_model_route_records tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_root_aggregates_route_metrics -v
```

Expected: missing `model_routing` or `route_metrics` keys.

- [ ] **Step 3: Extend evaluator dataclass**

In `backend/scripts/evaluate_ocr_compare_quality.py`, import:

```python
from app.services.model_routing import ModelRoutingAnalyzer
from app.utils.json_utils import to_jsonable
```

Add fields to `OcrCompareCaseResult`:

```python
    model_routing: dict[str, Any]
    route_metrics: dict[str, Any]
```

In `failure()`, set both to empty values:

```python
            model_routing={"status": "OK", "route_count": 0, "routes": []},
            route_metrics=_empty_route_metrics(),
```

In `evaluate_case()`, before returning:

```python
    routing_summary = ModelRoutingAnalyzer().analyze(
        actual_task.ocr_quality_summary,
        actual_task.diffs,
        actual_task.parse_warning_details,
    )
    routing_payload = to_jsonable(routing_summary)
    route_metrics = _route_metrics(routing_payload)
```

Then include:

```python
        model_routing=routing_payload,
        route_metrics=route_metrics,
```

- [ ] **Step 4: Add route metric helpers**

Add these helpers near `_aggregate()`:

```python
def _empty_route_metrics() -> dict[str, Any]:
    return {
        "route_count_by_recommendation": {},
        "page_count_by_type": {},
        "retry_recommended_count": 0,
        "manual_review_recommended_count": 0,
        "precision_by_recommendation": {},
        "recall_by_recommendation": {},
        "evidence_hit_rate_by_recommendation": {},
        "low_confidence_ratio_by_recommendation": {},
    }


def _route_metrics(model_routing: dict[str, Any]) -> dict[str, Any]:
    return {
        "route_count_by_recommendation": dict(model_routing.get("route_count_by_recommendation", {})),
        "page_count_by_type": dict(model_routing.get("page_count_by_type", {})),
        "retry_recommended_count": int(model_routing.get("retry_recommended_count", 0)),
        "manual_review_recommended_count": int(model_routing.get("manual_review_recommended_count", 0)),
        "precision_by_recommendation": {},
        "recall_by_recommendation": {},
        "evidence_hit_rate_by_recommendation": {},
        "low_confidence_ratio_by_recommendation": {},
    }


def _aggregate_route_metrics(results: list[OcrCompareCaseResult]) -> dict[str, Any]:
    route_counts: Counter[str] = Counter()
    page_counts: Counter[str] = Counter()
    retry_count = 0
    manual_count = 0
    for result in results:
        route_metrics = result.route_metrics
        route_counts.update(route_metrics.get("route_count_by_recommendation", {}))
        page_counts.update(route_metrics.get("page_count_by_type", {}))
        retry_count += int(route_metrics.get("retry_recommended_count", 0))
        manual_count += int(route_metrics.get("manual_review_recommended_count", 0))
    return {
        "route_count_by_recommendation": dict(route_counts),
        "page_count_by_type": dict(page_counts),
        "retry_recommended_count": retry_count,
        "manual_review_recommended_count": manual_count,
        "precision_by_recommendation": {},
        "recall_by_recommendation": {},
        "evidence_hit_rate_by_recommendation": {},
        "low_confidence_ratio_by_recommendation": {},
    }
```

Add `from collections import Counter` at the top.

In `_aggregate()`, after `aggregate.to_dict()`:

```python
    payload = aggregate.to_dict()
    payload["route_metrics"] = _aggregate_route_metrics(results)
    return payload
```

- [ ] **Step 5: Run evaluator JSON tests**

Run:

```bash
cd backend && python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_includes_model_route_records tests/test_evaluate_ocr_compare_quality.py::test_evaluate_case_root_aggregates_route_metrics -v
```

Expected: both tests pass.

- [ ] **Step 6: Run evaluator suite and lint**

Run:

```bash
cd backend && python -m pytest tests/test_model_routing.py tests/test_evaluate_ocr_compare_quality.py -v
cd backend && python -m ruff check app/services/model_routing.py scripts/evaluate_ocr_compare_quality.py tests/test_model_routing.py tests/test_evaluate_ocr_compare_quality.py
```

Expected: all tests and lint pass.

- [ ] **Step 7: Commit**

```bash
git add backend/scripts/evaluate_ocr_compare_quality.py backend/tests/test_evaluate_ocr_compare_quality.py
git commit -m "feat: add OCR route metrics to evaluator"
```

---

### Task 5: Add Route Recommendations To HTML Report

**Files:**
- Modify: `backend/scripts/evaluate_ocr_compare_quality.py`
- Modify: `backend/tests/test_evaluate_ocr_compare_quality.py`

- [ ] **Step 1: Add failing HTML test**

Modify `test_write_html_report_creates_index_and_case_pages()` in `backend/tests/test_evaluate_ocr_compare_quality.py`:

```python
def test_write_html_report_creates_index_and_case_pages(tmp_path: Path) -> None:
    report = evaluate_case_root(Path("tests/fixtures/ocr_compare_cases"))

    write_html_report(tmp_path, report)

    index = tmp_path / "index.html"
    case_page = tmp_path / "simple_scanned.html"
    assert index.exists()
    assert case_page.exists()
    index_html = index.read_text(encoding="utf-8")
    case_html = case_page.read_text(encoding="utf-8")
    assert "OCR comparison quality report" in index_html
    assert "Route recommendations" in index_html
    assert "simple_scanned" in case_html
    assert "OCR_LOW_CONFIDENCE" in case_html
    assert "Model routing" in case_html
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
cd backend && python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_write_html_report_creates_index_and_case_pages -v
```

Expected: assertion failure because route labels are not rendered yet.

- [ ] **Step 3: Update index HTML**

In `write_html_report()`, add route columns to each row:

```python
            f"<td>{case.get('route_metrics', {}).get('retry_recommended_count', 0)}</td>"
            f"<td>{case.get('route_metrics', {}).get('manual_review_recommended_count', 0)}</td>"
```

Update table header:

```python
        "<th>False positives</th><th>Missed diffs</th><th>Low confidence</th><th>OCR warnings</th>"
        "<th>Retry routes</th><th>Manual routes</th></tr>"
```

Add a compact aggregate route section before the table:

```python
        "<h2>Route recommendations</h2>"
        f"<pre>{html.escape(json.dumps(report.get('aggregate', {}).get('route_metrics', {}), ensure_ascii=False, indent=2))}</pre>"
```

- [ ] **Step 4: Update case HTML**

In `_case_html()`, add route rendering before issues:

```python
    routes = case.get("model_routing", {}).get("routes", [])
    route_items = "".join(
        "<li>"
        f"{html.escape(str(route.get('side')))} page {html.escape(str(route.get('page_no')))}: "
        f"{html.escape(str(route.get('page_type')))} → {html.escape(str(route.get('recommended_route')))}"
        "</li>"
        for route in routes
    ) or "<li>None</li>"
```

Then include:

```python
        f"<h2>Model routing</h2><ul>{route_items}</ul>"
```

- [ ] **Step 5: Run HTML tests**

Run:

```bash
cd backend && python -m pytest tests/test_evaluate_ocr_compare_quality.py::test_write_html_report_creates_index_and_case_pages tests/test_evaluate_ocr_compare_quality.py::test_write_html_report_sanitizes_filename_and_escapes_html -v
```

Expected: both tests pass.

- [ ] **Step 6: Run evaluator CLI smoke**

Run:

```bash
cd backend && python scripts/evaluate_ocr_compare_quality.py tests/fixtures/ocr_compare_cases --output .ocr-compare-quality/ocr_compare_quality.json --html-output .ocr-compare-quality/html --fail-on-threshold
cd backend && jq '{threshold_failures, aggregate: .aggregate.route_metrics}' .ocr-compare-quality/ocr_compare_quality.json
```

Expected: command exits 0; `threshold_failures` is `[]`; route metrics are present.

- [ ] **Step 7: Run evaluator suite and lint**

Run:

```bash
cd backend && python -m pytest tests/test_evaluate_ocr_compare_quality.py -v
cd backend && python -m ruff check scripts/evaluate_ocr_compare_quality.py tests/test_evaluate_ocr_compare_quality.py
```

Expected: all tests and lint pass.

- [ ] **Step 8: Commit**

```bash
git add backend/scripts/evaluate_ocr_compare_quality.py backend/tests/test_evaluate_ocr_compare_quality.py
git commit -m "feat: show OCR route recommendations in quality report"
```

---

### Task 6: Full Verification And Final Review

**Files:**
- No expected code changes unless verification reveals a bug.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
cd backend && python -m pytest tests/test_model_routing.py tests/test_ocr_retry.py tests/test_evaluate_ocr_compare_quality.py tests/test_pipeline.py tests/test_api.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Run backend syntax and lint checks**

Run:

```bash
cd backend && python -m compileall app tests scripts
cd backend && python -m ruff check .
```

Expected: compileall exits 0 and ruff reports all checks passed.

- [ ] **Step 3: Run frontend checks**

Run:

```bash
cd frontend && npm test
cd frontend && npm run build
```

Expected: all frontend tests pass and production build succeeds.

- [ ] **Step 4: Run OCR quality evaluation**

Run:

```bash
cd backend && python scripts/evaluate_ocr_compare_quality.py tests/fixtures/ocr_compare_cases --output .ocr-compare-quality/ocr_compare_quality.json --html-output .ocr-compare-quality/html --fail-on-threshold
cd backend && jq '{threshold_failures, route_metrics: .aggregate.route_metrics}' .ocr-compare-quality/ocr_compare_quality.json
```

Expected: `threshold_failures` is `[]`; `route_metrics` is present.

- [ ] **Step 5: Run full backend suite**

Run:

```bash
cd backend && python -m pytest
```

Expected: all backend tests pass.

- [ ] **Step 6: Inspect git status**

Run:

```bash
git status --short --branch
git log --oneline -n 12
```

Expected: implementation commits are present; only known local untracked artifacts remain.

- [ ] **Step 7: Final code review**

Perform final review against the design:

- Routing recommendations do not mutate diffs, evidence, snippets, or task status.
- Retry adapter does not execute real OCR.
- Pipeline debug artifact is additive and internal.
- Evaluator JSON and HTML include route metrics without weakening existing thresholds.
- Existing APIs remain compatible.

- [ ] **Step 8: Finish branch**

Use `superpowers:finishing-a-development-branch` after all verification passes.
