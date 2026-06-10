from __future__ import annotations

from app.models import DiffItem, Document, TextRange
from app.services.diff_engine import DiffEngine
from app.utils.id_utils import generate_diff_id

from .constants import FIELD_LABELS, FIELD_ORDER
from .evidence import field_evidence, mark_extra_evidences
from .extra_text import extra_texts
from .patterns import field_readable_change, normalize_extra, normalize_value
from .types import CoverExtraction, CoverExtraText, CoverField


def build_diffs(normalizer, original: Document, compare: Document, start_index: int = 1) -> list[DiffItem]:
    from .extractor import _extract_cover

    original_extraction = _extract_cover(normalizer, original)
    compare_extraction = _extract_cover(normalizer, compare)
    original_fields = original_extraction.fields
    compare_fields = compare_extraction.fields
    diffs: list[DiffItem] = []
    next_index = start_index
    for key in FIELD_ORDER:
        left = original_fields.get(key)
        right = compare_fields.get(key)
        if left is None and right is None:
            continue
        left_value = left.value if left else ""
        right_value = right.value if right else ""
        if normalize_value(normalizer, key, left_value) == normalize_value(normalizer, key, right_value):
            continue
        diffs.append(_build_diff(key, left, right, next_index))
        next_index += 1
    extra_diffs = _build_extra_text_diffs(original, compare, original_extraction, compare_extraction, next_index)
    diffs.extend(extra_diffs)
    return diffs


def _build_diff(key: str, left: CoverField | None, right: CoverField | None, index: int) -> DiffItem:
    label = FIELD_LABELS.get(key, key)
    if left and right:
        original_snippet, compare_snippet, original_ranges, compare_ranges = DiffEngine()._changed_snippets(
            left.value,
            right.value,
        )
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="MODIFY",
            title=f"封面字段：{label}",
            original_text=left.value,
            compare_text=right.value,
            original_snippet=original_snippet,
            compare_snippet=compare_snippet,
            readable_change=field_readable_change(label, left.value, right.value, original_snippet, compare_snippet),
            source_type="metadata",
            original_evidence=field_evidence(left, original_ranges),
            compare_evidence=field_evidence(right, compare_ranges),
            original_change_ranges=original_ranges,
            compare_change_ranges=compare_ranges,
        )
    if right:
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="ADD",
            title=f"封面字段：{label}",
            compare_text=right.value,
            compare_snippet=right.value,
            readable_change=f"新增封面字段【{label}】：{right.value}",
            source_type="metadata",
            compare_evidence=field_evidence(right, [TextRange(start=0, end=len(right.value), highlight_type="ADD")], "ADD"),
            compare_change_ranges=[TextRange(start=0, end=len(right.value), highlight_type="ADD")],
        )
    assert left is not None
    return DiffItem(
        diff_id=generate_diff_id(index),
        diff_type="DELETE",
        title=f"封面字段：{label}",
        original_text=left.value,
        original_snippet=left.value,
        readable_change=f"删除封面字段【{label}】：{left.value}",
        source_type="metadata",
        original_evidence=field_evidence(left, [TextRange(start=0, end=len(left.value), highlight_type="DELETE")], "DELETE"),
        original_change_ranges=[TextRange(start=0, end=len(left.value), highlight_type="DELETE")],
    )


def _build_extra_text_diffs(
    original: Document,
    compare: Document,
    original_extraction: CoverExtraction,
    compare_extraction: CoverExtraction,
    start_index: int,
) -> list[DiffItem]:
    if not _has_cover_metadata(original_extraction) and not _has_cover_metadata(compare_extraction):
        return []
    from .extractor import _cover_blocks as get_cover_blocks
    original_extras = extra_texts(original, original_extraction, get_cover_blocks)
    compare_extras = extra_texts(compare, compare_extraction, get_cover_blocks)
    compare_keys = {normalize_extra(extra.value) for extra in compare_extras}
    original_keys = {normalize_extra(extra.value) for extra in original_extras}
    diffs: list[DiffItem] = []
    next_index = start_index

    for extra in original_extras:
        if normalize_extra(extra.value) in compare_keys:
            continue
        diffs.append(_build_extra_delete(extra, next_index))
        next_index += 1

    for extra in compare_extras:
        if normalize_extra(extra.value) in original_keys:
            continue
        diffs.append(_build_extra_add(extra, next_index))
        next_index += 1
    return diffs


def _has_cover_metadata(extraction: CoverExtraction) -> bool:
    return any(key in extraction.fields for key in FIELD_ORDER)


def _build_extra_delete(extra: CoverExtraText, index: int) -> DiffItem:
    return DiffItem(
        diff_id=generate_diff_id(index),
        diff_type="DELETE",
        title="封面额外文本",
        original_text=extra.value,
        original_snippet=extra.value,
        readable_change=f"删除封面额外文本：{extra.value}",
        source_type="metadata",
        original_evidence=mark_extra_evidences([extra.evidence], "DELETE"),
        original_change_ranges=[TextRange(start=0, end=len(extra.value), highlight_type="DELETE")],
    )


def _build_extra_add(extra: CoverExtraText, index: int) -> DiffItem:
    return DiffItem(
        diff_id=generate_diff_id(index),
        diff_type="ADD",
        title="封面额外文本",
        compare_text=extra.value,
        compare_snippet=extra.value,
        readable_change=f"新增封面额外文本：{extra.value}",
        source_type="metadata",
        compare_evidence=mark_extra_evidences([extra.evidence], "ADD"),
        compare_change_ranges=[TextRange(start=0, end=len(extra.value), highlight_type="ADD")],
    )
