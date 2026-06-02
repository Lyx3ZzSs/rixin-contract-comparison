from __future__ import annotations

import unicodedata

from app.models import DiffItem, EvidenceBox, TextRange
from app.services.diff.text_utils import shorten


def apply_punctuation_filter(diffs: list[DiffItem]) -> list[DiffItem]:
    filtered: list[DiffItem] = []
    for diff in diffs:
        cleaned = clean_punctuation_diff(diff)
        if cleaned is not None:
            filtered.append(cleaned)
    return filtered


def clean_punctuation_diff(diff: DiffItem) -> DiffItem | None:
    if diff.diff_type == "MODIFY":
        return _clean_modify_diff(diff)
    if diff.diff_type == "ADD":
        return _clean_one_sided_diff(diff, side="compare", highlight_type="ADD")
    if diff.diff_type == "DELETE":
        return _clean_one_sided_diff(diff, side="original", highlight_type="DELETE")
    return diff


def _clean_modify_diff(diff: DiffItem) -> DiffItem | None:
    if _normalize_without_punctuation(diff.original_text) == _normalize_without_punctuation(diff.compare_text):
        return None

    original_ranges = _clean_ranges(
        diff.original_text,
        diff.original_change_ranges,
        fallback_type="MODIFY",
        use_fallback=False,
    )
    compare_ranges = _clean_ranges(
        diff.compare_text,
        diff.compare_change_ranges,
        fallback_type="MODIFY",
        use_fallback=False,
    )
    if not original_ranges and not compare_ranges:
        return None

    original_snippet = _snippet_from_ranges(diff.original_text, original_ranges)
    compare_snippet = _snippet_from_ranges(diff.compare_text, compare_ranges)
    return diff.model_copy(update={
        "original_snippet": original_snippet,
        "compare_snippet": compare_snippet,
        "readable_change": _readable_change(original_snippet, compare_snippet),
        "original_change_ranges": original_ranges,
        "compare_change_ranges": compare_ranges,
        "original_evidence": _clean_evidence(diff.original_evidence),
        "compare_evidence": _clean_evidence(diff.compare_evidence),
    })


def _clean_one_sided_diff(diff: DiffItem, *, side: str, highlight_type: str) -> DiffItem | None:
    text = diff.compare_text if side == "compare" else diff.original_text
    ranges = diff.compare_change_ranges if side == "compare" else diff.original_change_ranges
    cleaned_ranges = _clean_ranges(text, ranges, fallback_type=highlight_type, use_fallback=True)
    if not cleaned_ranges:
        return None
    snippet = _snippet_from_ranges(text, cleaned_ranges)
    if not snippet:
        return None
    updates = {
        "readable_change": f"{'新增' if side == 'compare' else '删除'}内容：{snippet}",
        "compare_evidence": _clean_evidence(diff.compare_evidence),
        "original_evidence": _clean_evidence(diff.original_evidence),
    }
    if side == "compare":
        updates["compare_snippet"] = snippet
        updates["compare_change_ranges"] = cleaned_ranges
    else:
        updates["original_snippet"] = snippet
        updates["original_change_ranges"] = cleaned_ranges
    return diff.model_copy(update=updates)


def _clean_ranges(
    text: str,
    ranges: list[TextRange],
    *,
    fallback_type: str,
    use_fallback: bool,
) -> list[TextRange]:
    source_ranges = ranges or (
        [TextRange(start=0, end=len(text), highlight_type=fallback_type)]
        if use_fallback and text
        else []
    )
    cleaned: list[TextRange] = []
    for range_ in source_ranges:
        start = max(0, min(len(text), range_.start))
        end = max(start, min(len(text), range_.end))
        for run_start, run_end in _non_punctuation_runs(text, start, end):
            cleaned.append(TextRange(start=run_start, end=run_end, highlight_type=range_.highlight_type))
    return _merge_ranges_preserving_highlight(cleaned)


def _non_punctuation_runs(text: str, start: int, end: int) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for index in range(start, end):
        char = text[index]
        if char.isspace() or _is_punctuation(char):
            if run_start is not None:
                runs.append((run_start, index))
                run_start = None
            continue
        if run_start is None:
            run_start = index
    if run_start is not None:
        runs.append((run_start, end))
    return runs


def _merge_ranges_preserving_highlight(ranges: list[TextRange]) -> list[TextRange]:
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda item: (item.start, item.end, item.highlight_type))
    merged = [ordered[0]]
    for item in ordered[1:]:
        previous = merged[-1]
        if item.highlight_type == previous.highlight_type and item.start <= previous.end:
            previous.end = max(previous.end, item.end)
        else:
            merged.append(item)
    return merged


def _snippet_from_ranges(text: str, ranges: list[TextRange]) -> str:
    return shorten("".join(text[range_.start:range_.end] for range_ in ranges))


def _readable_change(original_snippet: str, compare_snippet: str) -> str:
    return f"原文：{original_snippet}\n修改后：{compare_snippet}"


def _clean_evidence(evidences: list[EvidenceBox]) -> list[EvidenceBox]:
    cleaned: list[EvidenceBox] = []
    for evidence in evidences:
        text = _remove_punctuation(evidence.text)
        if evidence.text and not text:
            continue
        cleaned.append(evidence.model_copy(update={"text": text or evidence.text}))
    return cleaned


def _normalize_without_punctuation(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    return "".join(
        char
        for char in normalized
        if not char.isspace() and not _is_punctuation(char)
    )


def _remove_punctuation(text: str) -> str:
    return "".join(char for char in text or "" if not _is_punctuation(char)).strip()


def _is_punctuation(char: str) -> bool:
    return unicodedata.category(char).startswith("P")
