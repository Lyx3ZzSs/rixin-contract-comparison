from __future__ import annotations

from app.models import BBox, CharBox, EvidenceBox, TextBlock, TextRange

from .geometry import mid_y, pad_bbox, union_bboxes
from .patterns import find_text_index
from .types import CoverField


def mark_evidences(evidences: list[EvidenceBox], highlight_type: str) -> list[EvidenceBox]:
    return [evidence.model_copy(update={"highlight_type": highlight_type, "method": "cover_metadata"}) for evidence in evidences]


def mark_extra_evidences(evidences: list[EvidenceBox], highlight_type: str) -> list[EvidenceBox]:
    return [evidence.model_copy(update={"highlight_type": highlight_type, "method": "cover_extra"}) for evidence in evidences]


def build_evidence(block: TextBlock, text: str, bbox_override: BBox | None = None) -> EvidenceBox:
    return EvidenceBox(page_no=block.page_no, bbox=bbox_override or block.bbox, method="cover_metadata", text=text[:300])


def field_evidence(
    field: CoverField,
    ranges: list[TextRange],
    fallback_highlight_type: str | None = None,
) -> list[EvidenceBox]:
    evidences: list[EvidenceBox] = []
    for text_range in ranges:
        evidences.extend(evidence_for_field_range(field, text_range, fallback_highlight_type))
    return evidences


def evidence_for_field_range(
    field: CoverField,
    text_range: TextRange,
    fallback_highlight_type: str | None = None,
) -> list[EvidenceBox]:
    start = max(0, text_range.start)
    end = min(len(field.value), text_range.end)
    if start >= end:
        return []
    precise: list[EvidenceBox] = []
    for part in field.value_parts:
        overlap_start = max(start, part.start)
        overlap_end = min(end, part.end)
        if overlap_start >= overlap_end:
            continue
        local_start = overlap_start - part.start
        local_end = overlap_end - part.start
        precise.extend(
            evidence_for_block_text_range(
                part.block,
                part.text,
                local_start,
                local_end,
                text_range.highlight_type,
                fallback_highlight_type,
                block_start=part.block_start,
                fallback_bbox=part.fallback_bbox,
            )
        )
    if precise:
        return precise
    fragment = field.value[start:end]
    highlight_type = fallback_highlight_type or text_range.highlight_type
    return [
        evidence.model_copy(update={"text": fragment, "highlight_type": highlight_type, "method": "cover_metadata"})
        for evidence in field.evidences
        if fragment and fragment in evidence.text
    ] or [
        evidence.model_copy(update={"highlight_type": highlight_type, "method": "cover_metadata"})
        for evidence in field.evidences
    ]


def evidence_for_block_text_range(
    block: TextBlock,
    text: str,
    start: int,
    end: int,
    highlight_type: str,
    fallback_highlight_type: str | None = None,
    block_start: int | None = None,
    fallback_bbox: BBox | None = None,
) -> list[EvidenceBox]:
    effective_highlight_type = fallback_highlight_type or highlight_type
    fragment = text[start:end]
    if not block.char_boxes:
        return [build_evidence(block, fragment, bbox_override=fallback_bbox).model_copy(update={"highlight_type": effective_highlight_type})]
    if block_start is None:
        block_start = find_text_index(block.text, text)
    if block_start is None:
        return [build_evidence(block, fragment, bbox_override=fallback_bbox).model_copy(update={"highlight_type": effective_highlight_type})]
    wanted_start = block_start + start
    wanted_end = block_start + end
    char_boxes = [
        char_box
        for char_box in block.char_boxes
        if char_box.text_index is not None and wanted_start <= char_box.text_index < wanted_end
    ]
    if not char_boxes:
        return [build_evidence(block, fragment, bbox_override=fallback_bbox).model_copy(update={"highlight_type": effective_highlight_type})]
    return [
        EvidenceBox(
            page_no=page_no,
            bbox=bbox,
            method="cover_metadata",
            text=fragment,
            highlight_type=highlight_type,
        )
        for page_no, bbox, fragment in merge_char_boxes(char_boxes)
    ]


def merge_char_boxes(char_boxes: list[CharBox]) -> list[tuple[int, BBox, str]]:
    if not char_boxes:
        return []
    ordered = sorted(char_boxes, key=lambda item: (item.page_no, item.bbox.y0, item.bbox.x0))
    merged: list[tuple[int, BBox, str]] = []
    current_page = ordered[0].page_no
    current_bbox = ordered[0].bbox
    current_text = ordered[0].char
    for char_box in ordered[1:]:
        if char_box.page_no == current_page and abs(mid_y(char_box.bbox) - mid_y(current_bbox)) <= 4.0:
            current_bbox = union_bboxes(current_bbox, char_box.bbox)
            current_text += char_box.char
            continue
        merged.append((current_page, pad_bbox(current_bbox), current_text))
        current_page = char_box.page_no
        current_bbox = char_box.bbox
        current_text = char_box.char
    merged.append((current_page, pad_bbox(current_bbox), current_text))
    return merged
