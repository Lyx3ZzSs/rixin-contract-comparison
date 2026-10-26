from __future__ import annotations

import difflib
from dataclasses import dataclass

from app.models import BBox, Clause, TextRange
from app.services.diff.range_refiner import (
    expand_token_range,
    line_ranges,
    refine_inline_changed_ranges,
)
from app.services.diff.text_utils import merge_ranges


MIN_PAIR_SCORE = 0.48
TEXT_WEIGHT = 0.52
SPATIAL_WEIGHT = 0.36
ORDER_WEIGHT = 0.12
SHORT_LINE_MAX_LEN = 16


@dataclass(frozen=True)
class LineCandidate:
    index: int
    text: str
    start: int
    end: int
    bbox: BBox
    center_x: float
    center_y: float
    height: float


@dataclass(frozen=True)
class PairCandidate:
    score: float
    text_score: float
    spatial_score: float
    order_score: float
    left_index: int
    right_index: int


def repair_spatial_line_pairing(
    left: Clause,
    right: Clause,
    left_ranges: list[TextRange],
    right_ranges: list[TextRange],
) -> tuple[list[TextRange], list[TextRange], list[str]]:
    """Re-pair short form lines by visual columns before marking ADD/DELETE.

    OCR often returns two-column signing/form labels as a flat sequence of lines.
    When a repeated source label is partially recognized on the compare side,
    text-only greedy matching can consume the wrong column and create a false
    ADD for a shared prefix. This repair keeps clause matching unchanged and
    only rebuilds text ranges when line bboxes provide a clearer pairing.
    """

    if not _has_suspicious_short_add_delete(left, right, left_ranges, right_ranges):
        return left_ranges, right_ranges, []

    left_lines = _line_candidates(left)
    right_lines = _line_candidates(right)
    if not left_lines or not right_lines:
        return left_ranges, right_ranges, []
    if not _looks_like_multicolumn_form(left_lines) and not _looks_like_multicolumn_form(right_lines):
        return left_ranges, right_ranges, []

    pairs, reasons = _match_lines_spatially(left_lines, right_lines)
    if not reasons:
        return left_ranges, right_ranges, []

    repaired_left, repaired_right = _ranges_from_line_pairs(left, right, left_lines, right_lines, pairs)
    if not _meaningfully_changed(left_ranges, right_ranges, repaired_left, repaired_right):
        return left_ranges, right_ranges, []

    return repaired_left, repaired_right, reasons


def _has_suspicious_short_add_delete(
    left: Clause,
    right: Clause,
    left_ranges: list[TextRange],
    right_ranges: list[TextRange],
) -> bool:
    deleted = [
        left.text[item.start : item.end]
        for item in left_ranges
        if item.highlight_type == "DELETE" and item.start < item.end
    ]
    added = [
        right.text[item.start : item.end]
        for item in right_ranges
        if item.highlight_type == "ADD" and item.start < item.end
    ]
    if not deleted or not added:
        return False
    return any(_is_short_form_text(text) for text in deleted + added)


def _is_short_form_text(text: str) -> bool:
    compact = "".join(char for char in text or "" if not char.isspace())
    if not compact or len(compact) > SHORT_LINE_MAX_LEN:
        return False
    meaningful_count = sum(1 for char in compact if _is_meaningful_char(char))
    if meaningful_count < 2:
        return False
    if all(char.isdigit() or char in ".,，:：-_/ " for char in compact):
        return False
    return True


def _line_candidates(clause: Clause) -> list[LineCandidate]:
    if len(clause.char_boxes) != len(clause.text):
        return []
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
                start=start,
                end=end,
                bbox=bbox,
                center_x=(bbox.x0 + bbox.x1) / 2,
                center_y=(bbox.y0 + bbox.y1) / 2,
                height=height,
            )
        )
    return candidates


def _looks_like_multicolumn_form(lines: list[LineCandidate]) -> bool:
    short_form_lines = [line for line in lines if _is_short_form_text(line.text)]
    if len(short_form_lines) < 2:
        return False
    centers = sorted(line.center_x for line in short_form_lines)
    span = max(centers) - min(centers)
    if span < _median([line.height for line in short_form_lines]) * 4:
        return False
    return any((right - left) > max(24.0, span * 0.25) for left, right in zip(centers, centers[1:], strict=False))


def _match_lines_spatially(
    left_lines: list[LineCandidate],
    right_lines: list[LineCandidate],
) -> tuple[list[tuple[int, int]], list[str]]:
    left_span = _x_span(left_lines)
    right_span = _x_span(right_lines)
    candidates: list[PairCandidate] = []
    for left in left_lines:
        for right in right_lines:
            text_score = _line_similarity(left.text, right.text)
            if text_score <= 0:
                continue
            spatial_score = _spatial_score(left, right, left_span, right_span)
            order_score = _order_score(left.index, right.index, len(left_lines), len(right_lines))
            score = text_score * TEXT_WEIGHT + spatial_score * SPATIAL_WEIGHT + order_score * ORDER_WEIGHT
            if score >= MIN_PAIR_SCORE:
                candidates.append(
                    PairCandidate(
                        score=score,
                        text_score=text_score,
                        spatial_score=spatial_score,
                        order_score=order_score,
                        left_index=left.index,
                        right_index=right.index,
                    )
                )

    candidates.sort(key=lambda item: (-item.score, -item.spatial_score, -item.text_score, item.left_index, item.right_index))
    used_left: set[int] = set()
    used_right: set[int] = set()
    pairs: list[tuple[int, int]] = []
    reasons: list[str] = []
    for candidate in candidates:
        if candidate.left_index in used_left or candidate.right_index in used_right:
            continue
        used_left.add(candidate.left_index)
        used_right.add(candidate.right_index)
        pairs.append((candidate.left_index, candidate.right_index))
        if candidate.text_score < 0.5 and candidate.spatial_score >= 0.60:
            reasons.append(
                "spatial_line_pairing:"
                f"{candidate.left_index}->{candidate.right_index}"
            )
    pairs.sort()
    return pairs, reasons


def _ranges_from_line_pairs(
    left: Clause,
    right: Clause,
    left_lines: list[LineCandidate],
    right_lines: list[LineCandidate],
    pairs: list[tuple[int, int]],
) -> tuple[list[TextRange], list[TextRange]]:
    matched_left = {left_index for left_index, _ in pairs}
    matched_right = {right_index for _, right_index in pairs}
    right_by_index = {line.index: line for line in right_lines}

    left_ranges: list[TextRange] = []
    right_ranges: list[TextRange] = []
    for left_index, right_index in pairs:
        left_line = left_lines[left_index]
        right_line = right_by_index[right_index]
        if left_line.text == right_line.text:
            continue
        line_left_ranges, line_right_ranges = refine_inline_changed_ranges(
            left.text,
            left_line.start,
            left_line.end,
            right.text,
            right_line.start,
            right_line.end,
        )
        left_ranges.extend(line_left_ranges)
        right_ranges.extend(line_right_ranges)

    for line in left_lines:
        if line.index not in matched_left:
            left_ranges.append(expand_token_range(left.text, line.start, line.end, "DELETE"))
    for line in right_lines:
        if line.index not in matched_right:
            right_ranges.append(expand_token_range(right.text, line.start, line.end, "ADD"))

    return merge_ranges(left_ranges), merge_ranges(right_ranges)


def _line_similarity(left: str, right: str) -> float:
    left_compact = "".join(char for char in left or "" if not char.isspace())
    right_compact = "".join(char for char in right or "" if not char.isspace())
    if not left_compact or not right_compact:
        return 0.0
    if len({char for char in set(left_compact) & set(right_compact) if _is_meaningful_char(char)}) < 2:
        return 0.0
    return difflib.SequenceMatcher(None, left_compact, right_compact).ratio()


def _is_meaningful_char(char: str) -> bool:
    return char.isalnum() or "一" <= char <= "鿿"


def _spatial_score(left: LineCandidate, right: LineCandidate, left_span: tuple[float, float], right_span: tuple[float, float]) -> float:
    left_pos = _normalized_x(left.center_x, left_span)
    right_pos = _normalized_x(right.center_x, right_span)
    distance = abs(left_pos - right_pos)
    return max(0.0, 1.0 - distance / 0.35)


def _normalized_x(center_x: float, span: tuple[float, float]) -> float:
    x0, x1 = span
    width = max(1.0, x1 - x0)
    return (center_x - x0) / width


def _order_score(left_index: int, right_index: int, left_count: int, right_count: int) -> float:
    left_pos = left_index / max(1, left_count - 1)
    right_pos = right_index / max(1, right_count - 1)
    return max(0.0, 1.0 - abs(left_pos - right_pos) / 0.25)


def _x_span(lines: list[LineCandidate]) -> tuple[float, float]:
    return min(line.bbox.x0 for line in lines), max(line.bbox.x1 for line in lines)


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


def _meaningfully_changed(
    old_left: list[TextRange],
    old_right: list[TextRange],
    new_left: list[TextRange],
    new_right: list[TextRange],
) -> bool:
    return _range_signature(old_left) != _range_signature(new_left) or _range_signature(old_right) != _range_signature(new_right)


def _range_signature(ranges: list[TextRange]) -> list[tuple[int, int, str]]:
    return [(item.start, item.end, item.highlight_type) for item in ranges]
