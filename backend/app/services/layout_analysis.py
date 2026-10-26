"""Generic layout regions and flow roles used by downstream services."""

from dataclasses import dataclass, field
from typing import Any
from app.models import BBox, LayoutQualityReport

LABEL_ALIASES = {
    "page_header": "header", "page_footer": "footer", "vision_footnote": "footnote",
    "stamp": "seal", "paragraph": "text", "content": "text",
}
NON_TEXT_REGION_TYPES = {"abandon", "chart", "figure", "formula", "image", "seal"}

@dataclass
class LayoutRegion:
    region_type: str
    bbox: BBox
    page_number: int
    confidence: float = 1.0
    text: str = ""
    raw_html: str = ""
    table_cell_bboxes: list[list[float]] = field(default_factory=list)
    raw_data: dict[str, Any] = field(default_factory=dict)
    original_label: str = ""
    layout_order: int | None = None


@dataclass
class LayoutResult:
    regions: list[LayoutRegion] = field(default_factory=list)
    page_dimensions: dict[int, tuple[float, float]] = field(default_factory=dict)
    page_count: int = 0
    quality: LayoutQualityReport = field(default_factory=LayoutQualityReport)


def canonical_region_type(label: str) -> str:
    value = (label or "text").strip().lower()
    return LABEL_ALIASES.get(value, value)


def flow_role_for_region(region_type: str) -> str:
    value = canonical_region_type(region_type)
    if value in {"doc_title", "title", "paragraph_title"}:
        return "heading"
    if value in {"header", "footer"}:
        return "margin"
    if value == "footnote":
        return "note"
    if value == "aside_text":
        return "aside"
    if value in {"table", "table_cell"}:
        return "table"
    if value in {"table_title", "table_caption", "table_footnote", "figure_title"}:
        return "caption"
    if value in {"list", "reference", "number"}:
        return "list"
    if value == "abandon":
        return "noise"
    if value in NON_TEXT_REGION_TYPES:
        return "non_text"
    return "body"


def bbox_area(bbox: BBox) -> float:
    return max(0.0, bbox.x1 - bbox.x0) * max(0.0, bbox.y1 - bbox.y0)
