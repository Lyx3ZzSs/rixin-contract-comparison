from __future__ import annotations

import re
import unicodedata

from app.models import BBox, Document, EvidenceBox

from .constants import EXTRA_FIELD_LABELS
from .evidence import build_evidence
from .geometry import mid_y, union_bboxes
from .patterns import clean_text, normalize_extra, skip_extra_block, skip_extra_line
from .types import CoverExtraFragment, CoverExtraText, CoverExtraction


def extra_texts(document: Document, extraction: CoverExtraction, cover_blocks_fn) -> list[CoverExtraText]:
    width_by_page = {page.page_no: page.width for page in document.pages}
    fragments: list[CoverExtraFragment] = []
    for block in cover_blocks_fn(document):
        if block.block_id in extraction.consumed_block_ids or skip_extra_block(block):
            continue
        from .patterns import lines
        for line in lines(block.text):
            if skip_extra_line(line, block, width_by_page.get(block.page_no, 0.0)):
                continue
            fragments.append(
                CoverExtraFragment(
                    value=line,
                    evidence=build_evidence(block, line),
                    block_id=block.block_id,
                    layout_block_id=block.layout_block_id,
                )
            )

    extras: list[CoverExtraText] = []
    seen: set[str] = set()
    for extra in merge_extra_fragments(fragments):
        key = normalize_extra(extra.value)
        if not key or key in seen:
            continue
        extras.append(extra)
        seen.add(key)
    return extras


def merge_extra_fragments(fragments: list[CoverExtraFragment]) -> list[CoverExtraText]:
    rows = group_extra_fragments_by_row(fragments)
    extras: list[CoverExtraText] = []
    for row in rows:
        extras.extend(merge_extra_row(row))
    return extras


def group_extra_fragments_by_row(fragments: list[CoverExtraFragment]) -> list[list[CoverExtraFragment]]:
    ordered = sorted(
        fragments,
        key=lambda item: (item.evidence.page_no, mid_y(item.evidence.bbox), item.evidence.bbox.x0),
    )
    rows: list[list[CoverExtraFragment]] = []
    current: list[CoverExtraFragment] = []
    current_mid_y = 0.0
    current_height = 0.0
    current_page = 0

    for fragment in ordered:
        bbox = fragment.evidence.bbox
        fragment_mid_y = mid_y(bbox)
        height = max(1.0, bbox.y1 - bbox.y0)
        threshold = max(4.0, min(current_height or height, height) * 0.8)
        if current and (fragment.evidence.page_no != current_page or abs(fragment_mid_y - current_mid_y) > threshold):
            rows.append(current)
            current = []
        if not current:
            current_page = fragment.evidence.page_no
            current_mid_y = fragment_mid_y
            current_height = height
        else:
            count = len(current)
            current_mid_y = (current_mid_y * count + fragment_mid_y) / (count + 1)
            current_height = max(current_height, height)
        current.append(fragment)

    if current:
        rows.append(current)
    return rows


def merge_extra_row(row: list[CoverExtraFragment]) -> list[CoverExtraText]:
    ordered = sorted(row, key=lambda item: (item.evidence.bbox.x0, item.evidence.bbox.y0, item.block_id))
    merged: list[CoverExtraText] = []
    current_value = ""
    current_bbox: BBox | None = None
    current_page = 0
    current_block_ids: list[str] = []
    previous: CoverExtraFragment | None = None

    def flush() -> None:
        nonlocal current_value, current_bbox, current_page, current_block_ids, previous
        value = clean_text(current_value)
        if value and current_bbox is not None:
            merged.append(
                CoverExtraText(
                    value=value,
                    evidence=EvidenceBox(page_no=current_page, bbox=current_bbox, method="cover_extra", text=value),
                    block_id="+".join(current_block_ids),
                )
            )
        current_value = ""
        current_bbox = None
        current_page = 0
        current_block_ids = []
        previous = None

    for fragment in ordered:
        value = clean_text(fragment.value)
        if not value:
            continue
        if current_value and starts_extra_field(value):
            flush()
        elif current_value and previous is not None and not should_join_extra_fragments(previous, fragment, current_value):
            flush()

        if not current_value:
            current_value = value
            current_bbox = fragment.evidence.bbox
            current_page = fragment.evidence.page_no
            current_block_ids = [fragment.block_id]
        else:
            current_value += value
            assert current_bbox is not None
            current_bbox = union_bboxes(current_bbox, fragment.evidence.bbox)
            current_block_ids.append(fragment.block_id)
        previous = fragment

    flush()
    return merged


def starts_extra_field(text: str) -> bool:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
    return bool(re.match(rf"^({'|'.join(map(re.escape, EXTRA_FIELD_LABELS))})[:：]", compact))


def should_join_extra_fragments(
    previous: CoverExtraFragment,
    current: CoverExtraFragment,
    current_value: str,
) -> bool:
    if previous.evidence.page_no != current.evidence.page_no:
        return False
    if previous.layout_block_id and previous.layout_block_id == current.layout_block_id:
        return True
    previous_bbox = previous.evidence.bbox
    current_bbox = current.evidence.bbox
    height = max(previous_bbox.y1 - previous_bbox.y0, current_bbox.y1 - current_bbox.y0, 1.0)
    gap = current_bbox.x0 - previous_bbox.x1
    if current_value.rstrip().endswith((':', '：')):
        return gap <= max(80.0, height * 5)
    return gap <= max(24.0, height * 1.5)
