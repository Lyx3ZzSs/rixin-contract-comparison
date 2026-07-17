from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

TaskStatus = Literal["PROCESSING", "COMPLETED", "FAILED"]
TaskTerminalReason = Literal["NONE", "EXECUTION_FAILED", "SUBMISSION_FAILED", "CANCELLED"]
TaskExecutionStatus = Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCEL_REQUESTED", "CANCELLED"]
TaskExecutionType = Literal["compare"]
DiffType = Literal["ADD", "DELETE", "MODIFY"]
EvidenceQuality = Literal["LOW", "MEDIUM", "HIGH"]
DiffQualityStatus = Literal["NORMAL", "NEEDS_REVIEW"]
ReviewStatus = Literal["UNREVIEWED", "CONFIRMED", "FALSE_POSITIVE", "NEEDS_REVIEW", "IGNORED"]
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


class TextRangeResponse(BaseModel):
    start: int
    end: int
    highlight_type: DiffType = "MODIFY"


class AuditItemResponse(BaseModel):
    audit_item_id: str
    diff_id: str
    diff_type: DiffType
    source_type: str = "clause"
    section_type: str = ""
    section_path: list[str] = Field(default_factory=list)
    title: str = ""
    summary: str = ""
    original_text: str = ""
    compare_text: str = ""
    original_evidence: list[EvidenceBoxResponse] = Field(default_factory=list)
    compare_evidence: list[EvidenceBoxResponse] = Field(default_factory=list)
    evidence_state: Literal["LOCATED", "UNLOCATED"] = "UNLOCATED"
    quality_status: DiffQualityStatus = "NORMAL"
    review_flags: list[str] = Field(default_factory=list)
    text_confidence: float | None = None
    match_confidence: str = ""
    review_status: ReviewStatus = "UNREVIEWED"
    review_comment: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""


class CompareTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus
    terminal_reason: TaskTerminalReason = "NONE"
    revision: int = 0
    report_revision: int = 0
    retry_eligible: bool = False
    stage: str
    progress_percent: int
    diff_count: int
    reviewed_count: int = 0
    confirmed_count: int = 0
    false_positive_count: int = 0
    manual_review_count: int = 0
    ignored_count: int = 0
    audit_item_reviews: dict[str, dict[str, Any]] = Field(default_factory=dict)
    audit_items: list[AuditItemResponse] = Field(default_factory=list)
    extractor_used: str = ""
    parse_warnings: list[str] = Field(default_factory=list)
    parse_warning_details: list[dict[str, Any]] = Field(default_factory=list)
    document_profiles: dict[str, Any] = Field(default_factory=dict)
    ocr_quality_summary: TaskOcrQualitySummaryResponse | None = None
    ocr_remediation_summary: TaskOcrRemediationSummaryResponse | None = None
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
    terminal_reason: TaskTerminalReason = "NONE"
    revision: int = 0
    report_revision: int = 0
    retry_eligible: bool = False
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
    total: int
    page: int
    page_size: int
    total_pages: int


class TaskExecutionResponse(BaseModel):
    job_id: str
    task_id: str
    task_type: TaskExecutionType
    status: TaskExecutionStatus
    execution_no: int = 1
    attempt: int
    max_attempts: int
    queued_at: str
    started_at: str = ""
    finished_at: str = ""
    updated_at: str
    error_code: str = ""
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


class ReviewStatsResponse(BaseModel):
    total_count: int
    reviewed_count: int
    confirmed_count: int
    false_positive_count: int
    manual_review_count: int
    ignored_count: int
    review_unit: Literal["audit_item"] = "audit_item"


class DiffReviewResponse(BaseModel):
    task_id: str
    diff: CompareDiffResponse
    review_stats: ReviewStatsResponse


class AuditItemReviewUpdateResponse(BaseModel):
    task_id: str
    audit_item: AuditItemResponse
    review_stats: ReviewStatsResponse
    report_revision: int
