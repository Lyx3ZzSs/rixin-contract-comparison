from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


DiffType = Literal["ADD", "DELETE", "MODIFY"]
EvidenceQuality = Literal["LOW", "MEDIUM", "HIGH"]
DiffQualityStatus = Literal["NORMAL", "NEEDS_REVIEW"]
TaskStatus = Literal["PROCESSING", "COMPLETED", "FAILED"]
ReviewStatus = Literal["UNREVIEWED", "CONFIRMED", "FALSE_POSITIVE", "NEEDS_REVIEW", "IGNORED"]
DiffSourceType = Literal["clause", "header_footer", "table", "metadata", "seal", "page"]
LayoutMatchStatus = Literal[
    "matched",
    "ambiguous",
    "meaningful_unmatched",
    "noise_unmatched",
    "structure_only",
    "not_applicable",
]
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


class NormalizedBBox(BaseModel):
    """Bounding box in 0-1000 normalized coordinates (MinerU convention)."""

    x0: float
    y0: float
    x1: float
    y1: float


class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float
    normalized: NormalizedBBox | None = None

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
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class EvidenceBox(BaseModel):
    page_no: int
    bbox: BBox
    method: str = "clause_fallback"
    text: str = ""
    highlight_type: DiffType | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_quality: EvidenceQuality = "MEDIUM"
    text_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class ParseWarningDetail(BaseModel):
    code: str
    message: str
    severity: Literal["INFO", "WARNING", "ERROR"] = "WARNING"
    page_no: int | None = None
    source: str = ""


class PageLayoutQualityReport(BaseModel):
    page_no: int
    region_count: int = 0
    ocr_block_count: int = 0
    matched_ocr_block_count: int = 0
    ambiguous_match_count: int = 0
    meaningful_unmatched_count: int = 0
    noise_unmatched_count: int = 0
    structure_only_count: int = 0
    reading_order_conflict_count: int = 0
    issues: list[str] = Field(default_factory=list)


class LayoutQualityReport(BaseModel):
    parser_version: str = "v2"
    mode: str = "v2"
    page_count: int = 0
    region_count: int = 0
    label_counts: dict[str, int] = Field(default_factory=dict)
    invalid_bbox_count: int = 0
    empty_region_count: int = 0
    table_region_count: int = 0
    table_cell_matched_count: int = 0
    table_cell_unmatched_count: int = 0
    ocr_block_count: int = 0
    matched_ocr_block_count: int = 0
    unmatched_ocr_block_count: int = 0
    ambiguous_match_count: int = 0
    meaningful_unmatched_count: int = 0
    noise_unmatched_count: int = 0
    structure_only_count: int = 0
    reading_order_count: int = 0
    reading_order_conflict_count: int = 0
    page_quality: list[PageLayoutQualityReport] = Field(default_factory=list)
    warnings: list[ParseWarningDetail] = Field(default_factory=list)


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
    flow_role: str = ""
    semantic_role: str = ""
    semantic_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    semantic_reasons: list[str] = Field(default_factory=list)
    enter_clause_compare: bool | None = None
    layout_match_score: float | None = Field(default=None, ge=0.0, le=1.0)
    layout_match_status: LayoutMatchStatus = "not_applicable"
    layout_match_reason: str = ""
    char_boxes: list[CharBox] = Field(default_factory=list)
    raw_html: str = ""
    table_cell_bboxes: list[list[float]] = Field(default_factory=list)


class Page(BaseModel):
    page_no: int
    width: float
    height: float
    blocks: list[TextBlock] = Field(default_factory=list)
    semantic_role: str = "unknown"
    semantic_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    semantic_reasons: list[str] = Field(default_factory=list)


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
    match_text: str = ""
    page_numbers: list[int] = Field(default_factory=list)
    bboxes: list[EvidenceBox] = Field(default_factory=list)
    source_block_ids: list[str] = Field(default_factory=list)
    char_boxes: list[CharBox | None] = Field(default_factory=list)
    segmentation_reason: str = ""
    segmentation_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    section_type: str = "main_contract"
    section_path: list[str] = Field(default_factory=list)
    clause_key: str = ""
    order_index: int = 0
    split_flags: list[str] = Field(default_factory=list)


class TextRange(BaseModel):
    start: int
    end: int
    highlight_type: DiffType = "MODIFY"


class ClausePair(BaseModel):
    original: Clause | None = None
    compare: Clause | None = None
    score: float = 0
    match_method: str = "unmatched"
    score_details: dict[str, Any] = Field(default_factory=dict)
    match_candidates: list[dict[str, Any]] = Field(default_factory=list)
    match_confidence: str = "NORMAL"


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
    source_type: DiffSourceType = "clause"
    section_type: str = ""
    section_path: list[str] = Field(default_factory=list)
    match_score: float | None = None
    match_method: str = ""
    match_score_details: dict[str, Any] = Field(default_factory=dict)
    match_candidates: list[dict[str, Any]] = Field(default_factory=list)
    match_confidence: str = ""
    structural_flags: list[str] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)
    quality_status: DiffQualityStatus = "NORMAL"
    text_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    merged_sources: list[str] = Field(default_factory=list)
    review_status: ReviewStatus = "UNREVIEWED"
    review_comment: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""
    original_evidence: list[EvidenceBox] = Field(default_factory=list)
    compare_evidence: list[EvidenceBox] = Field(default_factory=list)
    original_change_ranges: list[TextRange] = Field(default_factory=list)
    compare_change_ranges: list[TextRange] = Field(default_factory=list)


class AuditItemReview(BaseModel):
    review_status: ReviewStatus = "UNREVIEWED"
    review_comment: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""


class CompareOptions(BaseModel):
    ignore_punctuation: bool = False
    ignore_headers_footers: bool = False
    ignore_stamps: bool = False


class OcrRawResultSidePaths(BaseModel):
    ppstructure: str | None = None
    ppocrv5: str | None = None
    hybrid: str | None = None

    def is_empty(self) -> bool:
        return not any([self.ppstructure, self.ppocrv5, self.hybrid])


class OcrRawResultPaths(BaseModel):
    original: OcrRawResultSidePaths = Field(default_factory=OcrRawResultSidePaths)
    compare: OcrRawResultSidePaths = Field(default_factory=OcrRawResultSidePaths)

    @classmethod
    def from_legacy_value(cls, value: Any) -> "OcrRawResultPaths":
        paths: list[str] = []
        if isinstance(value, str):
            paths = [line.strip() for line in value.splitlines() if line.strip()]
        elif isinstance(value, list):
            paths = [str(item).strip() for item in value if str(item).strip()]
        elif isinstance(value, dict):
            return cls(**value)

        parsed = cls()
        for raw_path in paths:
            side = _ocr_raw_path_side(raw_path)
            kind = _ocr_raw_path_kind(raw_path)
            if side and kind:
                setattr(getattr(parsed, side), kind, raw_path)
        return parsed

    def is_empty(self) -> bool:
        return self.original.is_empty() and self.compare.is_empty()


def _ocr_raw_path_side(raw_path: str) -> Literal["original", "compare"] | None:
    name = Path(raw_path).name.lower()
    if name.startswith("original_"):
        return "original"
    if name.startswith("compare_"):
        return "compare"
    return None


def _ocr_raw_path_kind(raw_path: str) -> Literal["ppstructure", "ppocrv5", "hybrid"] | None:
    name = Path(raw_path).name.lower()
    if "ppstructure_ocr_hybrid_raw" in name:
        return "hybrid"
    if "ppstructure_raw" in name:
        return "ppstructure"
    if "ppocrv5_raw" in name:
        return "ppocrv5"
    return None


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
    original_highlight_pdf_path: str | None = None
    compare_highlight_pdf_path: str | None = None
    report_pdf_path: str | None = None
    extractor_used: str = ""
    ocr_raw_result_path: str = ""
    ocr_raw_result_paths: OcrRawResultPaths = Field(default_factory=OcrRawResultPaths)
    parse_warnings: list[str] = Field(default_factory=list)
    parse_warning_details: list[ParseWarningDetail] = Field(default_factory=list)
    document_profiles: dict[str, DocumentProfile] = Field(default_factory=dict)
    ocr_quality_summary: TaskOcrQualitySummary | None = None
    debug_artifact_paths: dict[str, str] = Field(default_factory=dict)
    diff_count: int = 0
    reviewed_count: int = 0
    confirmed_count: int = 0
    false_positive_count: int = 0
    manual_review_count: int = 0
    ignored_count: int = 0
    audit_item_reviews: dict[str, AuditItemReview] = Field(default_factory=dict)
    compare_options: CompareOptions = Field(default_factory=CompareOptions)
    diffs: list[DiffItem] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_task_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        for key in [
            "original_highlight_pdf_path",
            "compare_highlight_pdf_path",
            "report_pdf_path",
        ]:
            if normalized.get(key) == "":
                normalized[key] = None
        if not normalized.get("ocr_raw_result_paths") and normalized.get("ocr_raw_result_path"):
            normalized["ocr_raw_result_paths"] = OcrRawResultPaths.from_legacy_value(
                normalized.get("ocr_raw_result_path")
            ).model_dump(mode="json")
        return normalized
