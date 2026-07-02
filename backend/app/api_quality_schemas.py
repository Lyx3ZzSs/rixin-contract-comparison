from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


GoldReviewStatus = Literal["DRAFT", "APPROVED", "REJECTED"]
DatasetSplit = Literal["dev", "regression", "holdout", "adversarial", "legacy"]


class QualityCaseSummaryResponse(BaseModel):
    case_id: str
    schema_version: str = "1.0"
    dataset_split: str = "legacy"
    case_tags: list[str] = Field(default_factory=list)
    baseline_required: bool = False
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
    cases: list[QualityCaseSummaryResponse] = Field(default_factory=list)


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
    expected_diff_count: int = 0
    actual_diff_count: int = 0


class QualityTaskReviewDiffResponse(BaseModel):
    diff_id: str
    diff_type: str
    source_type: str = ""
    title: str = ""
    quality_status: str = "NORMAL"
    review_flags: list[str] = Field(default_factory=list)
    match_score: float | None = None
    original_snippet: str = ""
    compare_snippet: str = ""


class QualityTaskSuppressedDiffResponse(QualityTaskReviewDiffResponse):
    suppression_reason: str = ""
    quality_decisions: list[str] = Field(default_factory=list)


class QualityDebugArtifactSummaryResponse(BaseModel):
    has_diff_quality: bool = False
    has_diff_decisions: bool = False
    has_ocr_quality: bool = False
    has_clause_matches: bool = False


class QualityDecisionSummaryResponse(BaseModel):
    action: str
    diff_id: str
    detail: dict[str, Any] = Field(default_factory=dict)


class QualityTaskReviewResponse(BaseModel):
    task_id: str
    status: str = ""
    original_filename: str = ""
    compare_filename: str = ""
    historical_diff_count: int = 0
    retained_diff_count: int = 0
    suppressed_diff_count: int = 0
    ocr_quality_summary: dict[str, Any] = Field(default_factory=dict)
    retained_diffs: list[QualityTaskReviewDiffResponse] = Field(default_factory=list)
    suppressed_diffs: list[QualityTaskSuppressedDiffResponse] = Field(
        default_factory=list
    )
    quality_decisions: list[QualityDecisionSummaryResponse] = Field(
        default_factory=list
    )
    debug_artifacts: QualityDebugArtifactSummaryResponse


class QualityRunRequest(BaseModel):
    dataset_splits: list[DatasetSplit] | None = None
    run_id: str = "local-eval"


class QualityRegressionRequest(BaseModel):
    dataset_splits: list[DatasetSplit] | None = None
    baseline_name: str = "current"
    run_id: str = "local-regression"


class QualityRunResponse(BaseModel):
    run_id: str
    status: str
    report: dict[str, Any]
    comparison: dict[str, Any] | None = None


class ExpectedDiffPatchRequest(BaseModel):
    diff_type: str | None = None
    source_type: str | None = None
    title_contains: str | None = None
    original_contains: str | None = None
    compare_contains: str | None = None
    review_status: GoldReviewStatus | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None
    severity: str | None = None
    notes: str | None = None
    false_positive_reason: str | None = None
    false_negative_reason: str | None = None
    should_not_match_again: bool | None = None
    source_actual_diff_id: str | None = None
    expected_evidence: list[dict[str, Any]] | None = None
