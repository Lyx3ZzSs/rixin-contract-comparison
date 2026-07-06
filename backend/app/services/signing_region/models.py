from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import BBox, DiffType


class SigningElementType(str, Enum):
    SEAL = "seal"
    SIGNATURE = "signature"
    LABEL = "label"
    DATE_FIELD = "date_field"
    SIGNING_TABLE = "signing_table"
    VISUAL_AREA = "visual_area"


class SigningRegionRole(str, Enum):
    PARTY_A = "party_a"
    PARTY_B = "party_b"
    BOTH_PARTIES = "both_parties"
    SIGNATURE_PAGE = "signature_page"
    UNKNOWN = "unknown"


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
    raw_ref: dict[str, Any] = Field(default_factory=dict)


class SigningRegion(BaseModel):
    region_id: str
    page_no: int
    bbox: BBox
    region_role: SigningRegionRole = SigningRegionRole.UNKNOWN
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence_reasons: list[str] = Field(default_factory=list)
    elements: list[SigningElement] = Field(default_factory=list)


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


class SigningRegionComparison(BaseModel):
    comparison_id: str
    diff_type: DiffType | None = None
    original_region: SigningRegion | None = None
    compare_region: SigningRegion | None = None
    match_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    seal_changes: list[dict[str, Any]] = Field(default_factory=list)
    signature_changes: list[dict[str, Any]] = Field(default_factory=list)
    date_changes: list[dict[str, Any]] = Field(default_factory=list)
    label_changes: list[dict[str, Any]] = Field(default_factory=list)
    table_changes: list[dict[str, Any]] = Field(default_factory=list)
    visual_changes: list[dict[str, Any]] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)


class SigningCoverageEntry(BaseModel):
    signing_region_diff_id: str
    covered_diff_ids: list[str] = Field(default_factory=list)
    reasons: dict[str, str] = Field(default_factory=dict)
