"""Seal / stamp region comparison.

Compares seal blocks between two documents and produces diffs with
``source_type="seal"`` and region-level evidence boxes.
"""
from __future__ import annotations

from app.models import DiffItem, Document, EvidenceBox, TextBlock, TextRange
from app.utils.id_utils import generate_diff_id


SEAL_BLOCK_TYPES = {"seal", "stamp", "signature"}


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


def _collect_seals(document: Document) -> list[TextBlock]:
    """Collect all seal blocks, grouped by page, sorted by y then x."""
    seals = [
        block
        for page in document.pages
        for block in page.blocks
        if (block.block_type or "").lower() in SEAL_BLOCK_TYPES
    ]
    seals.sort(key=lambda b: (b.page_no, b.bbox.y0, b.bbox.x0))
    return seals


def _match_seals(
    original: list[TextBlock],
    compare: list[TextBlock],
) -> list[tuple[TextBlock | None, TextBlock | None]]:
    """Match seal blocks by page position.

    Seals on the same page are paired by positional order (left-to-right,
    top-to-bottom). Unmatched seals produce ADD / DELETE.
    """
    from collections import defaultdict

    orig_by_page: dict[int, list[TextBlock]] = defaultdict(list)
    comp_by_page: dict[int, list[TextBlock]] = defaultdict(list)
    for s in original:
        orig_by_page[s.page_no].append(s)
    for s in compare:
        comp_by_page[s.page_no].append(s)

    all_pages = sorted(set(orig_by_page) | set(comp_by_page))
    pairs: list[tuple[TextBlock | None, TextBlock | None]] = []
    for page_no in all_pages:
        o_list = orig_by_page.get(page_no, [])
        c_list = comp_by_page.get(page_no, [])
        max_len = max(len(o_list), len(c_list))
        for i in range(max_len):
            o = o_list[i] if i < len(o_list) else None
            c = c_list[i] if i < len(c_list) else None
            pairs.append((o, c))
    return pairs


def _build_add(block: TextBlock, index: int) -> DiffItem:
    text = block.text.strip()
    return DiffItem(
        diff_id=generate_diff_id(index),
        diff_type="ADD",
        title=f"印章区域（第{block.page_no}页）",
        compare_text=text,
        compare_snippet=text,
        readable_change=f"新增印章：{text}" if text else f"新增印章区域（第{block.page_no}页）",
        source_type="seal",
        compare_evidence=[_region_evidence(block, "ADD")],
        compare_change_ranges=[TextRange(start=0, end=len(text), highlight_type="ADD")] if text else [],
    )


def _build_delete(block: TextBlock, index: int) -> DiffItem:
    text = block.text.strip()
    return DiffItem(
        diff_id=generate_diff_id(index),
        diff_type="DELETE",
        title=f"印章区域（第{block.page_no}页）",
        original_text=text,
        original_snippet=text,
        readable_change=f"删除印章：{text}" if text else f"删除印章区域（第{block.page_no}页）",
        source_type="seal",
        original_evidence=[_region_evidence(block, "DELETE")],
        original_change_ranges=[TextRange(start=0, end=len(text), highlight_type="DELETE")] if text else [],
    )


def _build_modify(orig: TextBlock, comp: TextBlock, index: int) -> DiffItem | None:
    orig_text = orig.text.strip()
    comp_text = comp.text.strip()
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


def _region_evidence(block: TextBlock, highlight_type: str) -> EvidenceBox:
    """Create a single region-level evidence box for a seal block."""
    return EvidenceBox(
        page_no=block.page_no,
        bbox=block.bbox,
        method="seal_region",
        text=block.text.strip()[:300],
        highlight_type=highlight_type,
        confidence=0.9,
        evidence_quality="HIGH",
    )
