"""Printed party text changes, with offsets in the unmodified source text."""
from difflib import SequenceMatcher
import unicodedata

from app.models import TextRange


def normalized_with_offsets(text: str) -> tuple[str, list[int]]:
    chars, offsets = [], []
    for index, char in enumerate(text):
        for normalized in unicodedata.normalize("NFKC", char):
            if not normalized.isspace():
                chars.append(normalized)
                offsets.append(index)
    return "".join(chars), offsets


def party_change_ranges(original: str, compare: str) -> tuple[list[TextRange], list[TextRange]]:
    left, left_offsets = normalized_with_offsets(original)
    right, right_offsets = normalized_with_offsets(compare)
    ranges = ([], [])
    for tag, i, j, k, l in SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        for dest, offsets, start, end in ((ranges[0], left_offsets, i, j), (ranges[1], right_offsets, k, l)):
            if start < end:
                dest.append(TextRange(start=offsets[start], end=offsets[end - 1] + 1,
                                      highlight_type="MODIFY" if tag == "replace" else "DELETE" if tag == "delete" else "ADD"))
    return ranges
