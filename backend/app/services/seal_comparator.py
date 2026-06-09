"""Seal / stamp region comparison.

Compares seal blocks between two documents and produces diffs with
``source_type="seal"`` and region-level evidence boxes.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import BBox, DiffItem, Document, EvidenceBox, TextBlock, TextRange
from app.utils.id_utils import generate_diff_id


SEAL_BLOCK_TYPES = {"seal", "stamp"}


@dataclass(frozen=True)
class SealEntry:
    page_no: int
    text: str
    bbox: BBox


def build_seal_diffs(
    original: Document,
    compare: Document,
    start_index: int = 1,
) -> list[DiffItem]:
    """Compare seal blocks between two documents.

    Returns diffs with ``source_type="seal"``. Evidence uses the seal
    region's full bbox as a single box (``method="seal_region"``).
    """
    original_seals = _collect_seals(original)
    compare_seals = _collect_seals(compare)

    if not original_seals and not compare_seals:
        return []

    pairs = _match_seals(original_seals, compare_seals)

    diffs: list[DiffItem] = []
    next_index = start_index
    for orig, comp in pairs:
        if orig is not None and comp is not None:
            diff = _build_modify(orig, comp, next_index)
            if diff is not None:
                diffs.append(diff)
                next_index += 1
        elif comp is not None:
            diffs.append(_build_add(comp, next_index))
            next_index += 1
        elif orig is not None:
            diffs.append(_build_delete(orig, next_index))
            next_index += 1
    return diffs


def _collect_seals(document: Document) -> list[SealEntry]:
    """Collect seal regions, merging OCR fragments that share one layout region."""
    grouped: dict[tuple[int, tuple[float, float, float, float] | str], list[TextBlock]] = {}
    for page in document.pages:
        for block in page.blocks:
            if (block.block_type or "").lower() not in SEAL_BLOCK_TYPES:
                continue
            key: tuple[int, tuple[float, float, float, float] | str]
            if block.layout_bbox is not None:
                key = (block.page_no, _bbox_key(block.layout_bbox))
            else:
                key = (block.page_no, block.block_id)
            grouped.setdefault(key, []).append(block)

    entries = [_seal_entry(blocks) for blocks in grouped.values()]
    entries.sort(key=lambda entry: (entry.page_no, entry.bbox.y0, entry.bbox.x0))
    return entries


def _match_seals(
    original: list[SealEntry],
    compare: list[SealEntry],
) -> list[tuple[SealEntry | None, SealEntry | None]]:
    """Match seal blocks by page position.

    Seals on the same page are paired by positional order (left-to-right,
    top-to-bottom). Unmatched seals produce ADD / DELETE.
    """
    from collections import defaultdict

    orig_by_page: dict[int, list[SealEntry]] = defaultdict(list)
    comp_by_page: dict[int, list[SealEntry]] = defaultdict(list)
    for s in original:
        orig_by_page[s.page_no].append(s)
    for s in compare:
        comp_by_page[s.page_no].append(s)

    all_pages = sorted(set(orig_by_page) | set(comp_by_page))
    pairs: list[tuple[SealEntry | None, SealEntry | None]] = []
    for page_no in all_pages:
        o_list = orig_by_page.get(page_no, [])
        c_list = comp_by_page.get(page_no, [])
        max_len = max(len(o_list), len(c_list))
        for i in range(max_len):
            o = o_list[i] if i < len(o_list) else None
            c = c_list[i] if i < len(c_list) else None
            pairs.append((o, c))
    return pairs


def _seal_entry(blocks: list[TextBlock]) -> SealEntry:
    blocks = sorted(blocks, key=lambda block: (block.page_no, block.bbox.y0, block.bbox.x0, block.block_id))
    first = blocks[0]
    bbox = first.layout_bbox or _union_bbox([block.bbox for block in blocks])
    return SealEntry(
        page_no=first.page_no,
        text=_merge_text(blocks),
        bbox=bbox,
    )


def _bbox_key(bbox: BBox) -> tuple[float, float, float, float]:
    return round(bbox.x0, 2), round(bbox.y0, 2), round(bbox.x1, 2), round(bbox.y1, 2)


def _union_bbox(bboxes: list[BBox]) -> BBox:
    return BBox(
        x0=min(bbox.x0 for bbox in bboxes),
        y0=min(bbox.y0 for bbox in bboxes),
        x1=max(bbox.x1 for bbox in bboxes),
        y1=max(bbox.y1 for bbox in bboxes),
    )


def _merge_text(blocks: list[TextBlock]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        text = block.text.strip()
        if not text or text in seen:
            continue
        parts.append(text)
        seen.add(text)
    return " ".join(parts)


def _build_add(entry: SealEntry, index: int) -> DiffItem:
    text = entry.text
    return DiffItem(
        diff_id=generate_diff_id(index),
        diff_type="ADD",
        title=f"印章区域（第{entry.page_no}页）",
        compare_text=text,
        compare_snippet=text,
        readable_change=f"新增印章：{text}" if text else f"新增印章区域（第{entry.page_no}页）",
        source_type="seal",
        compare_evidence=[_region_evidence(entry, "ADD")],
        compare_change_ranges=[TextRange(start=0, end=len(text), highlight_type="ADD")] if text else [],
    )


def _build_delete(entry: SealEntry, index: int) -> DiffItem:
    text = entry.text
    return DiffItem(
        diff_id=generate_diff_id(index),
        diff_type="DELETE",
        title=f"印章区域（第{entry.page_no}页）",
        original_text=text,
        original_snippet=text,
        readable_change=f"删除印章：{text}" if text else f"删除印章区域（第{entry.page_no}页）",
        source_type="seal",
        original_evidence=[_region_evidence(entry, "DELETE")],
        original_change_ranges=[TextRange(start=0, end=len(text), highlight_type="DELETE")] if text else [],
    )


def _build_modify(orig: SealEntry, comp: SealEntry, index: int) -> DiffItem | None:
    orig_text = orig.text
    comp_text = comp.text
    if orig_text == comp_text:
        return None

    return DiffItem(
        diff_id=generate_diff_id(index),
        diff_type="MODIFY",
        title=f"印章区域（第{orig.page_no}页）",
        original_text=orig_text,
        compare_text=comp_text,
        original_snippet=orig_text,
        compare_snippet=comp_text,
        readable_change=f"印章变更：{orig_text} → {comp_text}",
        source_type="seal",
        original_evidence=[_region_evidence(orig, "MODIFY")],
        compare_evidence=[_region_evidence(comp, "MODIFY")],
        original_change_ranges=[TextRange(start=0, end=len(orig_text), highlight_type="MODIFY")] if orig_text else [],
        compare_change_ranges=[TextRange(start=0, end=len(comp_text), highlight_type="MODIFY")] if comp_text else [],
    )


def _region_evidence(entry: SealEntry, highlight_type: str) -> EvidenceBox:
    """Create a single region-level evidence box for a seal region."""
    return EvidenceBox(
        page_no=entry.page_no,
        bbox=entry.bbox,
        method="seal_region",
        text=entry.text[:300],
        highlight_type=highlight_type,
        confidence=0.9,
        evidence_quality="HIGH",
    )
