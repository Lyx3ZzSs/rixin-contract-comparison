from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from app.models import BBox, Clause, TextRange
from app.services.diff.range_refiner import line_ranges
from app.services.diff.text_utils import merge_ranges

MAX_FRAGMENT_LENGTH = 4
MAX_VERTICAL_GAP_ABSOLUTE = 18.0
MAX_VERTICAL_GAP_MULTIPLIER = 2.5
MIN_CROSS_COLUMN_GAP = 60.0
SAME_COLUMN_GAP = 40.0

PUNCT_TRANSLATION = str.maketrans({
    "（": "(",
    "）": ")",
    "：": ":",
    "；": ";",
    "，": ",",
    "。": ".",
    "【": "[",
    "】": "]",
})


@dataclass(frozen=True)
class LineCandidate:
    index: int
    text: str
    normalized: str
    start: int
    end: int
    bbox: BBox
    center_x: float
    center_y: float
    height: float


def repair_cross_column_prefix_fragments(
    left: Clause,
    right: Clause,
    left_ranges: list[TextRange],
    right_ranges: list[TextRange],
) -> tuple[list[TextRange], list[TextRange], list[str]]:
    """Expand short suffix fragments caused by cross-column prefix matching.

    In two-column form-like layouts, OCR may miss a right-column line while a
    left-column line is partially recognized with the same prefix. Text-only
    diff then treats the left-column prefix as equal and leaves only a short
    suffix such as "字:" or "号:" as deleted. This repair does not know field
    names; it uses only line text prefixes and visual column separation.
    """

    if not _has_usable_char_boxes(left) or not _has_usable_char_boxes(right):
        return left_ranges, right_ranges, []

    left_lines = _line_candidates(left)
    right_lines = _line_candidates(right)
    if not left_lines or not right_lines:
        return left_ranges, right_ranges, []

    repaired_left, left_reasons = _repair_side(
        source=left,
        target=right,
        source_lines=left_lines,
        target_lines=right_lines,
        source_ranges=left_ranges,
        highlight_type="DELETE",
    )
    repaired_right, right_reasons = _repair_side(
        source=right,
        target=left,
        source_lines=right_lines,
        target_lines=left_lines,
        source_ranges=right_ranges,
        highlight_type="ADD",
    )
    reasons = [*left_reasons, *right_reasons]
    if not reasons:
        return left_ranges, right_ranges, []
    return merge_ranges(repaired_left), merge_ranges(repaired_right), reasons


def _repair_side(
    source: Clause,
    target: Clause,
    source_lines: list[LineCandidate],
    target_lines: list[LineCandidate],
    source_ranges: list[TextRange],
    highlight_type: str,
) -> tuple[list[TextRange], list[str]]:
    repaired: list[TextRange] = []
    reasons: list[str] = []
    for text_range in source_ranges:
        if text_range.highlight_type != highlight_type:
            repaired.append(text_range)
            continue
        source_line = _line_for_range(source_lines, text_range)
        if source_line is None or not _is_suffix_fragment(source, source_line, text_range):
            repaired.append(text_range)
            continue
        target_line = _cross_column_prefix_match(source_line, target_lines)
        if target_line is None:
            repaired.append(text_range)
            continue
        if _same_column_prefix_exists(source_line, target_lines):
            repaired.append(text_range)
            continue
        repaired.append(TextRange(start=source_line.start, end=source_line.end, highlight_type=highlight_type))
        reasons.append(f"cross_column_prefix:{source_line.index}->{target_line.index}")
    return repaired, reasons


def _line_for_range(lines: list[LineCandidate], text_range: TextRange) -> LineCandidate | None:
    for line in lines:
        if line.start <= text_range.start and text_range.end <= line.end:
            return line
    return None


def _is_suffix_fragment(clause: Clause, line: LineCandidate, text_range: TextRange) -> bool:
    if text_range.start <= line.start or text_range.end != line.end:
        return False
    fragment = _normalize(clause.text[text_range.start:text_range.end])
    if not fragment or len(fragment) > MAX_FRAGMENT_LENGTH:
        return False
    if not any(_is_meaningful_char(char) for char in fragment):
        return False
    prefix = _normalize(clause.text[line.start:text_range.start])
    return bool(prefix and line.normalized.startswith(prefix))


def _cross_column_prefix_match(
    source_line: LineCandidate,
    target_lines: list[LineCandidate],
) -> LineCandidate | None:
    matches = [
        target_line
        for target_line in target_lines
        if _is_prefix_line(source_line, target_line)
        and _same_visual_row(source_line, target_line)
        and _is_cross_column(source_line, target_line)
    ]
    if not matches:
        return None
    return min(matches, key=lambda item: abs(source_line.center_x - item.center_x))


def _same_column_prefix_exists(
    source_line: LineCandidate,
    target_lines: list[LineCandidate],
) -> bool:
    return any(
        _is_prefix_line(source_line, target_line)
        and _same_visual_row(source_line, target_line)
        and abs(source_line.center_x - target_line.center_x) <= SAME_COLUMN_GAP
        for target_line in target_lines
    )


def _is_prefix_line(source_line: LineCandidate, target_line: LineCandidate) -> bool:
    if not source_line.normalized or not target_line.normalized:
        return False
    if len(target_line.normalized) >= len(source_line.normalized):
        return False
    if not source_line.normalized.startswith(target_line.normalized):
        return False
    return sum(1 for char in target_line.normalized if _is_meaningful_char(char)) >= 2


def _same_visual_row(left: LineCandidate, right: LineCandidate) -> bool:
    vertical_gap = abs(left.center_y - right.center_y)
    tolerance = max(
        MAX_VERTICAL_GAP_ABSOLUTE,
        left.height * MAX_VERTICAL_GAP_MULTIPLIER,
        right.height * MAX_VERTICAL_GAP_MULTIPLIER,
    )
    return vertical_gap <= tolerance


def _is_cross_column(left: LineCandidate, right: LineCandidate) -> bool:
    gap = abs(left.center_x - right.center_x)
    return gap >= max(MIN_CROSS_COLUMN_GAP, min(_width(left.bbox), _width(right.bbox)) * 0.8)


def _line_candidates(clause: Clause) -> list[LineCandidate]:
    candidates: list[LineCandidate] = []
    for index, (text, start, end) in enumerate(line_ranges(clause.text, 0, len(clause.text))):
        boxes = [
            char_box
            for char_box in clause.char_boxes[start:end]
            if char_box is not None and char_box.char.strip()
        ]
        if not boxes:
            return []
        bbox = _union_boxes([char_box.bbox for char_box in boxes])
        height = max(1.0, _median([char_box.bbox.y1 - char_box.bbox.y0 for char_box in boxes]))
        candidates.append(
            LineCandidate(
                index=index,
                text=text,
                normalized=_normalize(text),
                start=start,
                end=end,
                bbox=bbox,
                center_x=(bbox.x0 + bbox.x1) / 2,
                center_y=(bbox.y0 + bbox.y1) / 2,
                height=height,
            )
        )
    return candidates


def _normalize(text: str) -> str:
    chars: list[str] = []
    for char in text or "":
        for normalized in unicodedata.normalize("NFKC", char.translate(PUNCT_TRANSLATION)):
            if normalized.isspace():
                continue
            chars.append(normalized.lower())
    return "".join(chars)


def _is_meaningful_char(char: str) -> bool:
    return char.isalnum() or "一" <= char <= "鿿"


def _has_usable_char_boxes(clause: Clause) -> bool:
    return bool(clause.char_boxes) and len(clause.char_boxes) == len(clause.text)


def _width(bbox: BBox) -> float:
    return max(1.0, bbox.x1 - bbox.x0)


def _union_boxes(boxes: list[BBox]) -> BBox:
    return BBox(
        x0=min(box.x0 for box in boxes),
        y0=min(box.y0 for box in boxes),
        x1=max(box.x1 for box in boxes),
        y1=max(box.y1 for box in boxes),
    )


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2
