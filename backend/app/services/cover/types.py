from __future__ import annotations

from dataclasses import dataclass, field

from app.models import BBox, EvidenceBox, TextBlock


@dataclass
class CoverValuePart:
    block: TextBlock
    text: str
    start: int
    end: int
    block_start: int | None = None
    fallback_bbox: BBox | None = None


@dataclass
class CoverField:
    key: str
    label: str
    value: str
    evidences: list[EvidenceBox] = field(default_factory=list)
    value_parts: list[CoverValuePart] = field(default_factory=list)


@dataclass
class CoverExtraText:
    value: str
    evidence: EvidenceBox
    block_id: str


@dataclass
class CoverExtraFragment:
    value: str
    evidence: EvidenceBox
    block_id: str
    layout_block_id: str = ""


@dataclass
class CoverExtraction:
    fields: dict[str, CoverField] = field(default_factory=dict)
    consumed_block_ids: set[str] = field(default_factory=set)
