from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import BBox, DiffType
from app.services.field_observation import ObservationState


class SigningElementType(str, Enum):
    SEAL = "seal"
    SIGNATURE = "signature"
    LABEL = "label"
    PARTY_FIELD = "party_field"
    FIELD = "field"
    DATE_FIELD = "date_field"
    SIGNING_TABLE = "signing_table"
    VISUAL_AREA = "visual_area"


class SigningVisualObjectType(str, Enum):
    COMPANY_ROUND_SEAL = "COMPANY_ROUND_SEAL"
    PERSONAL_NAME_SEAL = "PERSONAL_NAME_SEAL"
    HANDWRITTEN_SIGNATURE = "HANDWRITTEN_SIGNATURE"
    HANDWRITTEN_DATE = "HANDWRITTEN_DATE"
    EDGE_SEAM_SEAL = "EDGE_SEAM_SEAL"
    OTHER_STAMP = "OTHER_STAMP"
    UNKNOWN_SIGNING_MARK = "UNKNOWN_SIGNING_MARK"


class SigningRegionRole(str, Enum):
    PARTY_A = "party_a"
    PARTY_B = "party_b"
    BOTH_PARTIES = "both_parties"
    SIGNATURE_PAGE = "signature_page"
    UNKNOWN = "unknown"


class SigningPageType(str, Enum):
    FULL_PAGE = "full_page"
    MIXED_PAGE = "mixed_page"
    CONTINUATION_PAGE = "continuation_page"
    UNKNOWN = "unknown"


class SigningBlockRole(str, Enum):
    PARTY_A = "party_a"
    PARTY_B = "party_b"
    BOTH_PARTIES = "both_parties"
    CONTINUATION = "continuation"
    UNKNOWN = "unknown"


class SigningBlockConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


SigningElementSource = Literal["layout", "ocr", "visual_model", "visual_fingerprint", "inferred"]


class SigningElement(BaseModel):
    element_id: str
    element_type: SigningElementType
    page_no: int
    bbox: BBox
    text: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: SigningElementSource = "layout"
    visual_hash: str = ""
    model_name: str = ""
    observation_state: ObservationState | None = None
    observation_reason: str = ""
    raw_ref: dict[str, Any] = Field(default_factory=dict)


class SigningRegion(BaseModel):
    region_id: str
    group_id: str = ""
    page_no: int
    bbox: BBox
    signing_block_id: str = ""
    region_role: SigningRegionRole = SigningRegionRole.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_reasons: list[str] = Field(default_factory=list)
    elements: list[SigningElement] = Field(default_factory=list)


class SigningVisualFeatures(BaseModel):
    status: str = "unavailable"
    has_red_seal: bool = False
    has_handwriting: bool = False
    visual_hash: str = ""
    detected_bboxes: list[BBox] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


class SigningPage(BaseModel):
    page_no: int
    bbox: BBox
    page_role: str = "body"
    signing_page_type: SigningPageType = SigningPageType.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_reasons: list[str] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    exclude_full_page_from_clause_diff: bool = False


class SigningBlock(BaseModel):
    block_id: str
    group_id: str = ""
    page_no: int
    bbox: BBox
    block_role: SigningBlockRole = SigningBlockRole.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_level: SigningBlockConfidenceLevel = SigningBlockConfidenceLevel.LOW
    confidence_reasons: list[str] = Field(default_factory=list)
    source_block_ids: list[str] = Field(default_factory=list)
    text: str = ""
    elements: list[SigningElement] = Field(default_factory=list)
    visual_features: SigningVisualFeatures = Field(default_factory=SigningVisualFeatures)
    exclude_from_clause_diff: bool = False


class VisualDetection(BaseModel):
    page_no: int
    bbox: BBox
    label: str = "signature"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    model_name: str = ""
    raw_data: dict[str, Any] = Field(default_factory=dict)


class VisualDetectionResult(BaseModel):
    available: bool = True
    model_name: str = ""
    detections: list[VisualDetection] = Field(default_factory=list)
    error: str = ""


class SigningVisualObject(BaseModel):
    object_id: str
    object_type: SigningVisualObjectType
    page_no: int
    bbox: BBox
    visual_detection_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    text: str = ""
    text_recognition_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    party_role: str = "unknown"
    party_attribution_reason: str = "UNATTRIBUTED"
    party_attribution_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    supporting_pages: list[int] = Field(default_factory=list)
    fragment_bboxes: list[BBox] = Field(default_factory=list)
    raw_ref: dict[str, Any] = Field(default_factory=dict)


class SigningRegionComparison(BaseModel):
    comparison_id: str
    diff_type: DiffType | None = None
    original_region: SigningRegion | None = None
    compare_region: SigningRegion | None = None
    match_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    region_changes: list[dict[str, Any]] = Field(default_factory=list)
    column_changes: list[dict[str, Any]] = Field(default_factory=list)
    seal_changes: list[dict[str, Any]] = Field(default_factory=list)
    signature_changes: list[dict[str, Any]] = Field(default_factory=list)
    date_changes: list[dict[str, Any]] = Field(default_factory=list)
    label_changes: list[dict[str, Any]] = Field(default_factory=list)
    party_changes: list[dict[str, Any]] = Field(default_factory=list)
    field_changes: list[dict[str, Any]] = Field(default_factory=list)
    template_changes: list[dict[str, Any]] = Field(default_factory=list)
    table_changes: list[dict[str, Any]] = Field(default_factory=list)
    visual_changes: list[dict[str, Any]] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)


class SigningCoverageEntry(BaseModel):
    signing_region_diff_id: str
    covered_diff_ids: list[str] = Field(default_factory=list)
    reasons: dict[str, str] = Field(default_factory=dict)
