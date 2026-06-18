from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TaskStatus = Literal["PROCESSING", "COMPLETED", "FAILED"]
TaskExecutionStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCEL_REQUESTED", "CANCELLED"]
TaskExecutionType = Literal["compare", "extraction"]
DiffType = Literal["ADD", "DELETE", "MODIFY"]
EvidenceQuality = Literal["LOW", "MEDIUM", "HIGH"]
DiffQualityStatus = Literal["NORMAL", "NEEDS_REVIEW"]
ReviewStatus = Literal["UNREVIEWED", "CONFIRMED", "FALSE_POSITIVE", "NEEDS_REVIEW", "IGNORED"]
ExtractionFieldStatus = Literal["found", "not_found", "error"]
ExtractionMethod = Literal["explicit", "semantic"]


class BBoxResponse(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class EvidenceBoxResponse(BaseModel):
    page_no: int
    bbox: BBoxResponse
    method: str = "clause_fallback"
    text: str = ""
    highlight_type: DiffType | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_quality: EvidenceQuality = "MEDIUM"
    text_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class TextRangeResponse(BaseModel):
    start: int
    end: int
    highlight_type: DiffType = "MODIFY"


class CompareTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    stage: str
    progress_percent: int
    diff_count: int
    reviewed_count: int = 0
    confirmed_count: int = 0
    false_positive_count: int = 0
    manual_review_count: int = 0
    ignored_count: int = 0
    audit_item_reviews: dict[str, dict[str, Any]] = Field(default_factory=dict)
    extractor_used: str = ""
    parse_warnings: list[str] = Field(default_factory=list)
    parse_warning_details: list[dict[str, Any]] = Field(default_factory=list)
    document_profiles: dict[str, Any] = Field(default_factory=dict)
    debug_artifact_paths: dict[str, str] = Field(default_factory=dict)
    report_url: str
    report_filename: str
    original_pdf_url: str
    compare_pdf_url: str
    original_highlight_pdf_url: str
    compare_highlight_pdf_url: str
    errors: list[str] = Field(default_factory=list)


class CompareTaskDetailResponse(CompareTaskResponse):
    created_at: str
    updated_at: str
    original_filename: str
    compare_filename: str


class CompareRecordResponse(BaseModel):
    task_id: str
    status: TaskStatus
    stage: str
    progress_percent: int
    created_at: str
    updated_at: str
    original_filename: str
    compare_filename: str
    diff_count: int
    report_url: str


class CompareRecordListResponse(BaseModel):
    records: list[CompareRecordResponse]


class TaskExecutionResponse(BaseModel):
    job_id: str
    task_id: str
    task_type: TaskExecutionType
    status: TaskExecutionStatus
    attempt: int
    max_attempts: int
    queued_at: str
    started_at: str = ""
    finished_at: str = ""
    updated_at: str
    last_error: str = ""


class CompareDiffResponse(BaseModel):
    diff_id: str
    diff_type: DiffType
    original_clause_id: str | None = None
    compare_clause_id: str | None = None
    clause_no: str = ""
    title: str = ""
    original_text: str = ""
    compare_text: str = ""
    original_snippet: str = ""
    compare_snippet: str = ""
    readable_change: str = ""
    source_type: str = "clause"
    match_score: float | None = None
    match_method: str = ""
    match_score_details: dict[str, Any] = Field(default_factory=dict)
    match_candidates: list[dict[str, Any]] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)
    quality_status: DiffQualityStatus = "NORMAL"
    text_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    merged_sources: list[str] = Field(default_factory=list)
    review_status: ReviewStatus = "UNREVIEWED"
    review_comment: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""
    original_evidence: list[EvidenceBoxResponse] = Field(default_factory=list)
    compare_evidence: list[EvidenceBoxResponse] = Field(default_factory=list)
    original_change_ranges: list[TextRangeResponse] = Field(default_factory=list)
    compare_change_ranges: list[TextRangeResponse] = Field(default_factory=list)


class CompareDiffListResponse(BaseModel):
    task_id: str
    diffs: list[CompareDiffResponse]


class DiffReviewRequest(BaseModel):
    review_status: ReviewStatus
    review_comment: str = ""
    reviewed_by: str = ""


class AuditItemReviewResponse(BaseModel):
    audit_item_id: str
    review_status: ReviewStatus = "UNREVIEWED"
    review_comment: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""


class ReviewStatsResponse(BaseModel):
    reviewed_count: int
    confirmed_count: int
    false_positive_count: int
    manual_review_count: int
    ignored_count: int


class DiffReviewResponse(BaseModel):
    task_id: str
    diff: CompareDiffResponse
    review_stats: ReviewStatsResponse


class AuditItemReviewUpdateResponse(BaseModel):
    task_id: str
    audit_item_id: str
    audit_item_review: AuditItemReviewResponse
    review_stats: ReviewStatsResponse


class ExtractionFieldRequest(BaseModel):
    id: str
    name: str
    type: str = "文本"
    description: str = ""
    semantic_extraction: bool = True


class ExtractionFieldResponse(ExtractionFieldRequest):
    pass


class ExtractionFieldValueResponse(BaseModel):
    field_id: str
    field_name: str
    value: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_snippet: str = ""
    status: ExtractionFieldStatus = "not_found"
    extraction_method: ExtractionMethod | None = None


class ExtractionTaskResponse(BaseModel):
    task_id: str
    task_type: str = "extraction"
    status: TaskStatus
    stage: str = ""
    created_at: str
    updated_at: str
    filename: str = ""
    file_url: str = ""
    extractor_used: str = ""
    fields: list[ExtractionFieldResponse] = Field(default_factory=list)
    results: list[ExtractionFieldValueResponse] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ExtractionRecordResponse(BaseModel):
    task_id: str
    task_type: str
    status: TaskStatus
    created_at: str
    updated_at: str
    filename: str
    file_url: str
    extractor_used: str
    field_count: int
    found_count: int
    not_found_count: int
    error_count: int


class ExtractionRecordListResponse(BaseModel):
    records: list[ExtractionRecordResponse]
