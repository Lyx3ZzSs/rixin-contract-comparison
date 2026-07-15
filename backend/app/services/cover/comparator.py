from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
import unicodedata

from app.models import DiffItem, Document, TextRange
from app.services.diff_engine import DiffEngine
from app.utils.id_utils import generate_diff_id

from .constants import FIELD_LABELS, FIELD_ORDER
from .evidence import field_evidence, mark_extra_evidences
from .extra_text import extra_texts
from .patterns import field_readable_change, is_title_line, normalize_extra, normalize_value, parse_labeled_line
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
        if key == "project_title" and _project_title_covered_by_opposite_document(left, right, original, compare):
            continue
        diffs.append(_build_diff(key, left, right, next_index))
        next_index += 1
    preamble_diffs = _build_preamble_title_diffs(
        original_extraction,
        compare_extraction,
        next_index,
    )
    diffs.extend(preamble_diffs)
    next_index += len(preamble_diffs)
    preamble_field_diffs = _build_preamble_field_diffs(
        normalizer,
        original_extraction,
        compare_extraction,
        next_index,
    )
    diffs.extend(preamble_field_diffs)
    next_index += len(preamble_field_diffs)
    extra_diffs = _build_extra_text_diffs(original, compare, original_extraction, compare_extraction, next_index)
    diffs.extend(extra_diffs)
    return diffs


def _build_preamble_field_diffs(
    normalizer,
    original_extraction: CoverExtraction,
    compare_extraction: CoverExtraction,
    start_index: int,
) -> list[DiffItem]:
    diffs: list[DiffItem] = []
    page_numbers = sorted(
        set(original_extraction.preamble_fields) | set(compare_extraction.preamble_fields)
    )
    for page_no in page_numbers:
        original_fields = original_extraction.preamble_fields.get(page_no, {})
        compare_fields = compare_extraction.preamble_fields.get(page_no, {})
        for key in ("buyer", "seller"):
            left = original_fields.get(key)
            right = compare_fields.get(key)
            if left is None and right is None:
                continue
            if normalize_value(
                normalizer,
                key,
                left.value if left else "",
            ) == normalize_value(normalizer, key, right.value if right else ""):
                continue
            diffs.append(
                _build_preamble_field_diff(
                    page_no,
                    key,
                    left,
                    right,
                    start_index + len(diffs),
                )
            )
    return diffs


def _build_preamble_field_diff(
    page_no: int,
    key: str,
    left: CoverField | None,
    right: CoverField | None,
    index: int,
) -> DiffItem:
    label = FIELD_LABELS[key]
    title = f"前置字段（第{page_no}页）：{label}"
    original_text = f"{label}：{left.value}" if left else ""
    compare_text = f"{label}：{right.value}" if right else ""
    if left is not None and right is not None:
        diff_type = "MODIFY"
    elif right is not None:
        diff_type = "ADD"
    else:
        diff_type = "DELETE"
    original_ranges = (
        [TextRange(start=0, end=len(original_text), highlight_type=diff_type)]
        if original_text
        else []
    )
    compare_ranges = (
        [TextRange(start=0, end=len(compare_text), highlight_type=diff_type)]
        if compare_text
        else []
    )
    return DiffItem(
        diff_id=generate_diff_id(index),
        diff_type=diff_type,
        title=title,
        original_text=original_text,
        compare_text=compare_text,
        original_snippet=original_text,
        compare_snippet=compare_text,
        readable_change=f"{title}变更：{original_text} -> {compare_text}",
        source_type="metadata",
        review_flags=["CRITICAL_VALUE_CHANGE"],
        original_evidence=_preamble_field_evidence(left, original_text, diff_type),
        compare_evidence=_preamble_field_evidence(right, compare_text, diff_type),
        original_change_ranges=original_ranges,
        compare_change_ranges=compare_ranges,
    )


def _preamble_field_evidence(
    field: CoverField | None,
    text: str,
    highlight_type: str,
):
    if field is None:
        return []
    evidences = [
        evidence.model_copy(update={"highlight_type": highlight_type, "method": "cover_metadata"})
        for evidence in field.evidences
    ]
    if evidences:
        evidences[0] = evidences[0].model_copy(update={"text": text})
    return evidences


def _build_preamble_title_diffs(
    original_extraction: CoverExtraction,
    compare_extraction: CoverExtraction,
    start_index: int,
) -> list[DiffItem]:
    diffs: list[DiffItem] = []
    page_numbers = sorted(
        set(original_extraction.preamble_titles) | set(compare_extraction.preamble_titles)
    )
    for page_no in page_numbers:
        left = original_extraction.preamble_titles.get(page_no)
        right = compare_extraction.preamble_titles.get(page_no)
        if left is None or right is None:
            continue
        if normalize_extra(left.value) == normalize_extra(right.value):
            continue
        diffs.append(_build_preamble_title_diff(page_no, left, right, start_index + len(diffs)))
    return diffs


def _build_preamble_title_diff(
    page_no: int,
    left: CoverField,
    right: CoverField,
    index: int,
) -> DiffItem:
    title = f"前置标题（第{page_no}页）"
    original_ranges = [TextRange(start=0, end=len(left.value), highlight_type="MODIFY")]
    compare_ranges = [TextRange(start=0, end=len(right.value), highlight_type="MODIFY")]
    return DiffItem(
        diff_id=generate_diff_id(index),
        diff_type="MODIFY",
        title=title,
        original_text=left.value,
        compare_text=right.value,
        original_snippet=left.value,
        compare_snippet=right.value,
        readable_change=f"{title}变更：{left.value} -> {right.value}",
        source_type="metadata",
        original_evidence=field_evidence(left, original_ranges),
        compare_evidence=field_evidence(right, compare_ranges),
        original_change_ranges=original_ranges,
        compare_change_ranges=compare_ranges,
    )


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
    original_has_filled_date = any(_looks_like_filled_cover_date(extra.value) for extra in original_extras)
    compare_has_filled_date = any(_looks_like_filled_cover_date(extra.value) for extra in compare_extras)
    diffs: list[DiffItem] = []
    next_index = start_index

    for extra in original_extras:
        if normalize_extra(extra.value) in compare_keys:
            continue
        if _is_blank_cover_date_placeholder(extra.value) and compare_has_filled_date:
            continue
        if _title_extra_covered_by_document(extra, compare):
            continue
        diffs.append(_build_extra_delete(extra, next_index))
        next_index += 1

    for extra in compare_extras:
        if normalize_extra(extra.value) in original_keys:
            continue
        if _is_blank_cover_date_placeholder(extra.value) and original_has_filled_date:
            continue
        if _title_extra_covered_by_document(extra, original):
            continue
        diffs.append(_build_extra_add(extra, next_index))
        next_index += 1
    return diffs


def _has_cover_metadata(extraction: CoverExtraction) -> bool:
    return any(key in extraction.fields for key in FIELD_ORDER)


def _project_title_covered_by_opposite_document(
    left: CoverField | None,
    right: CoverField | None,
    original: Document,
    compare: Document,
) -> bool:
    if left and right:
        return False
    present = left or right
    if present is None or not _looks_like_title_text(present.value):
        return False
    opposite = compare if left else original
    return _document_contains_text(opposite, present.value)


def _title_extra_covered_by_document(extra: CoverExtraText, document: Document) -> bool:
    return _looks_like_title_text(extra.value) and _document_contains_text(document, extra.value)


def _looks_like_title_text(text: str) -> bool:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return False
    if any(is_title_line(line) for line in lines):
        return True
    compact = normalize_extra(text)
    return len(compact) >= 12 and all(parse_labeled_line(line) is None for line in lines)


def _document_contains_text(document: Document, value: str) -> bool:
    needle = normalize_extra(value)
    if not needle or len(needle) < 8:
        return False
    haystack = normalize_extra("\n".join(block.text for page in document.pages for block in page.blocks))
    if needle in haystack:
        return True
    return _native_pdf_contains_text(document.path, needle)


def _is_blank_cover_date_placeholder(value: str) -> bool:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))
    return compact in {"年月", "年月日"}


def _looks_like_filled_cover_date(value: str) -> bool:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))
    return bool(re.fullmatch(r"\d{4}年\d{1,2}月(?:\d{1,2}日)?", compact))


def _native_pdf_contains_text(path: str, normalized_needle: str) -> bool:
    pdf_path = _existing_pdf_path(path)
    if pdf_path is None:
        return False
    return normalized_needle in _native_pdf_text_key(str(pdf_path))


def _existing_pdf_path(path: str) -> Path | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if not candidate.exists() or candidate.suffix.lower() != ".pdf":
        return None
    return candidate


@lru_cache(maxsize=64)
def _native_pdf_text_key(path: str) -> str:
    try:
        import fitz

        with fitz.open(path) as pdf:
            text = "\n".join(page.get_text() for page in pdf)
    except Exception:
        return ""
    return normalize_extra(text)


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
