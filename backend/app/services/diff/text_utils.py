from __future__ import annotations

import re

from app.models import TextRange


def build_diff_text(text: str) -> tuple[str, list[tuple[int, int]]]:
    result_chars: list[str] = []
    segments: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        normalized = per_mille_ocr_token(text, index)
        if normalized is not None:
            result_chars.append("‰")
            segments.append(normalized)
            index = normalized[1]
            continue
        char = text[index]
        if char.isspace():
            index += 1
            continue
        result_chars.append(char)
        segments.append((index, index + 1))
        index += 1
    return "".join(result_chars), segments


def build_compacted_text(text: str) -> tuple[str, list[tuple[int, int]]]:
    result_chars: list[str] = []
    segments: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        if text[i] in " \t\n\r":
            ws_start = i
            result_chars.append(" ")
            while i < len(text) and text[i] in " \t\n\r":
                i += 1
            segments.append((ws_start, i))
        else:
            normalized = per_mille_ocr_token(text, i)
            if normalized is not None:
                result_chars.append("‰")
                segments.append(normalized)
                i = normalized[1]
                continue
            result_chars.append(text[i])
            segments.append((i, i + 1))
            i += 1
    return "".join(result_chars), segments


def per_mille_ocr_token(text: str, percent_index: int) -> tuple[int, int] | None:
    if percent_index <= 0 or percent_index + 1 >= len(text):
        return None
    if text[percent_index] != "%" or not text[percent_index - 1].isdigit():
        return None
    suffix = text[percent_index + 1]
    if suffix in "oO0":
        return percent_index, percent_index + 2
    if suffix not in "。.．":
        return None
    if percent_index + 2 >= len(text):
        return None
    next_char = text[percent_index + 2]
    if next_char.isspace():
        return None
    return percent_index, percent_index + 2


def compacted_range_to_original(
    start: int, end: int, segments: list[tuple[int, int]]
) -> tuple[int, int]:
    if start >= end or not segments:
        return 0, 0
    start = max(0, min(start, len(segments) - 1))
    end = max(start + 1, min(end, len(segments)))
    return segments[start][0], segments[end - 1][1]


def is_whitespace_only(text: str, start: int, end: int) -> bool:
    return all(c in " \t\n\r" for c in text[start:end])


def trim_range_whitespace(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start] in " \t\n\r":
        start += 1
    while end > start and text[end - 1] in " \t\n\r":
        end -= 1
    return start, end


def merge_ranges(ranges: list[TextRange]) -> list[TextRange]:
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda item: (item.start, item.end))
    merged = [ordered[0]]
    for item in ordered[1:]:
        previous = merged[-1]
        if item.start <= previous.end:
            previous.end = max(previous.end, item.end)
            if previous.highlight_type != item.highlight_type:
                previous.highlight_type = "MODIFY"
        else:
            merged.append(item)
    return merged


def shorten(text: str, max_len: int = 220) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}..."
