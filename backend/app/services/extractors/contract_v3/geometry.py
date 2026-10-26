"""Page coordinate and overlap helpers."""

import unicodedata

TITLE_FAMILY = frozenset({"doc_title", "paragraph_title", "title"})


def is_suspicious_char(char: str) -> bool:
    return char == "�" or unicodedata.category(char) in {"Cc", "Cf", "Co", "Cs", "Cn"}


def union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def overlap(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def area(b):
    return max(1e-9, (b[2] - b[0]) * (b[3] - b[1]))


def iou(a, b):
    hit = overlap(a, b)
    return hit / (area(a) + area(b) - hit)
