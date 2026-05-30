from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


DiffType = Literal["ADD", "DELETE", "MODIFY"]
EvidenceQuality = Literal["LOW", "MEDIUM", "HIGH"]
TaskStatus = Literal["PROCESSING", "COMPLETED", "FAILED"]
ReviewStatus = Literal["UNREVIEWED", "CONFIRMED", "FALSE_POSITIVE", "NEEDS_REVIEW", "IGNORED"]


class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float

    def expanded(self, amount: float, width: float, height: float) -> "BBox":
        return BBox(
            x0=max(0, self.x0 - amount),
            y0=max(0, self.y0 - amount),
            x1=min(width, self.x1 + amount),
            y1=min(height, self.y1 + amount),
        )


class CharBox(BaseModel):
    char: str
    page_no: int
    bbox: BBox
    text_index: int | None = None


class EvidenceBox(BaseModel):
    page_no: int
    bbox: BBox
    method: str = "clause_fallback"
    text: str = ""
    highlight_type: DiffType | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_quality: EvidenceQuality = "MEDIUM"


class ParseWarningDetail(BaseModel):
    code: str
    message: str
    severity: Literal["INFO", "WARNING", "ERROR"] = "WARNING"
    page_no: int | None = None
    source: str = ""


class PageProfile(BaseModel):
    page_no: int
    width: float = 0
    height: float = 0
    text_block_count: int = 0
    table_block_count: int = 0
    image_block_count: int = 0
    char_count: int = 0
    avg_confidence: float | None = None
    table_area_ratio: float = 0
    image_area_ratio: float = 0
    page_role: str = "body"
    extraction_strategy: str = "text"
    low_text: bool = False
    table_heavy: bool = False


class DocumentProfile(BaseModel):
    filename: str = ""
    page_count: int = 0
    extractor_used: str = ""
    total_text_chars: int = 0
    table_block_count: int = 0
    image_block_count: int = 0
    scanned_page_count: int = 0
    table_heavy_page_count: int = 0
    page_profiles: list[PageProfile] = Field(default_factory=list)
    recommended_strategy: str = "text"
    warnings: list[ParseWarningDetail] = Field(default_factory=list)


class TextBlock(BaseModel):
    block_id: str
    page_no: int
    text: str
    bbox: BBox
    block_type: str = "text"
    confidence: float | None = None
    layout_block_id: str = ""
    layout_order: int | None = None
    layout_bbox: BBox | None = None
    table_id: str = ""
    row_index: int | None = None
    column_index: int | None = None
    source: str = ""
    reading_order: int | None = None
    block_role: str = ""
    char_boxes: list[CharBox] = Field(default_factory=list)
    raw_html: str = ""
    table_cell_bboxes: list[list[float]] = Field(default_factory=list)


class Page(BaseModel):
    page_no: int
    width: float
    height: float
    blocks: list[TextBlock] = Field(default_factory=list)


class Document(BaseModel):
    filename: str
    path: str
    page_count: int
    pages: list[Page] = Field(default_factory=list)
    profile: DocumentProfile | None = None


class Clause(BaseModel):
    clause_id: str
    clause_no: str = ""
    title: str = ""
    text: str
    normalized_text: str
    page_numbers: list[int] = Field(default_factory=list)
    bboxes: list[EvidenceBox] = Field(default_factory=list)
    source_block_ids: list[str] = Field(default_factory=list)
    char_boxes: list[CharBox | None] = Field(default_factory=list)
    segmentation_reason: str = ""
    segmentation_confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class TextRange(BaseModel):
    start: int
    end: int
    highlight_type: DiffType = "MODIFY"


class ClausePair(BaseModel):
    original: Clause | None = None
    compare: Clause | None = None
    score: float = 0
    match_method: str = "unmatched"
    score_details: dict[str, float] = Field(default_factory=dict)
    match_candidates: list[dict[str, Any]] = Field(default_factory=list)


class DiffItem(BaseModel):
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
    match_score_details: dict[str, float] = Field(default_factory=dict)
    match_candidates: list[dict[str, Any]] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)
    review_status: ReviewStatus = "UNREVIEWED"
    review_comment: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""
    original_evidence: list[EvidenceBox] = Field(default_factory=list)
    compare_evidence: list[EvidenceBox] = Field(default_factory=list)
    original_change_ranges: list[TextRange] = Field(default_factory=list)
    compare_change_ranges: list[TextRange] = Field(default_factory=list)


class CompareTask(BaseModel):
    task_id: str
    schema_version: int = 1
    revision: int = 0
    status: TaskStatus = "PROCESSING"
    stage: str = "已创建"
    progress_percent: int = Field(default=0, ge=0, le=100)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    original_filename: str = ""
    compare_filename: str = ""
    original_pdf_path: str = ""
    compare_pdf_path: str = ""
    original_highlight_pdf_path: str = ""
    compare_highlight_pdf_path: str = ""
    report_pdf_path: str = ""
    extractor_used: str = ""
    ocr_raw_result_path: str = ""
    parse_warnings: list[str] = Field(default_factory=list)
    parse_warning_details: list[ParseWarningDetail] = Field(default_factory=list)
    document_profiles: dict[str, DocumentProfile] = Field(default_factory=dict)
    debug_artifact_paths: dict[str, str] = Field(default_factory=dict)
    diff_count: int = 0
    reviewed_count: int = 0
    confirmed_count: int = 0
    false_positive_count: int = 0
    manual_review_count: int = 0
    ignored_count: int = 0
    diffs: list[DiffItem] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
