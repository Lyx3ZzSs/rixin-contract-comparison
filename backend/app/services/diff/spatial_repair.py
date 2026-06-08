from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import BBox, CharBox, Clause, TextRange

from app.services.diff.text_utils import shorten

MIN_TOKEN_LENGTH = 1
MAX_TOKEN_LENGTH = 12
MAX_VERTICAL_GAP_MULTIPLIER = 2.5
MAX_VERTICAL_GAP_ABSOLUTE = 14.0
BUSINESS_VALUE_PATTERN = re.compile(r"[\d¥￥$%‰]")
PUNCT_TRANSLATION = str.maketrans({
    "（": "(",
    "）": ")",
    "：": ":",
    "；": ";",
    "，": ",",
    "。": ".",
})


@dataclass(frozen=True)
class RangeCandidate:
    range_index: int
    text_range: TextRange
    token: str
    text: str
    page_no: int
    bbox: BBox
    height: float


def repair_spatial_duplicate_ranges(
    left: Clause,
    right: Clause,
    left_ranges: list[TextRange],
    right_ranges: list[TextRange],
) -> tuple[list[TextRange], list[TextRange], list[str]]:
    """Remove ADD/DELETE pairs caused by reading-order drift of repeated short labels."""

    left_candidates = [
        candidate
        for index, text_range in enumerate(left_ranges)
        if text_range.highlight_type == "DELETE"
        if (candidate := _candidate_for_range(left, text_range, index)) is not None
    ]
    right_candidates = [
        candidate
        for index, text_range in enumerate(right_ranges)
        if text_range.highlight_type == "ADD"
        if (candidate := _candidate_for_range(right, text_range, index)) is not None
    ]
    if not left_candidates or not right_candidates:
        return left_ranges, right_ranges, []

    ignored_left: set[int] = set()
    ignored_right: set[int] = set()
    reasons: list[str] = []
    tokens = sorted({candidate.token for candidate in left_candidates} & {candidate.token for candidate in right_candidates})
    for token in tokens:
        if _token_count(left.text, token) != _token_count(right.text, token):
            continue
        token_left = [candidate for candidate in left_candidates if candidate.token == token]
        token_right = [candidate for candidate in right_candidates if candidate.token == token]
        for left_candidate, right_candidate in _match_spatial_candidates(token_left, token_right):
            ignored_left.add(left_candidate.range_index)
            ignored_right.add(right_candidate.range_index)
            reasons.append(
                "spatial_duplicate_token_repair:"
                f"{token}@p{left_candidate.page_no}->{right_candidate.page_no}"
            )

    if not ignored_left and not ignored_right:
        return left_ranges, right_ranges, []

    repaired_left = [item for index, item in enumerate(left_ranges) if index not in ignored_left]
    repaired_right = [item for index, item in enumerate(right_ranges) if index not in ignored_right]
    return repaired_left, repaired_right, reasons


def rebuild_change_text(
    original_text: str,
    compare_text: str,
    original_ranges: list[TextRange],
    compare_ranges: list[TextRange],
) -> tuple[str, str, str]:
    original_snippet = shorten("".join(original_text[item.start : item.end] for item in original_ranges))
    compare_snippet = shorten("".join(compare_text[item.start : item.end] for item in compare_ranges))
    return original_snippet, compare_snippet, f"原文：{original_snippet}\n修改后：{compare_snippet}"


def _candidate_for_range(clause: Clause, text_range: TextRange, range_index: int) -> RangeCandidate | None:
    text = clause.text[max(0, text_range.start) : min(len(clause.text), text_range.end)]
    token = _normalize_token(text)
    if not _is_repairable_token(token):
        return None
    boxes = [
        char_box
        for char_box in clause.char_boxes[max(0, text_range.start) : min(len(clause.char_boxes), text_range.end)]
        if char_box is not None and char_box.char.strip()
    ]
    if not boxes:
        return None
    page_no = _dominant_page(boxes)
    page_boxes = [char_box for char_box in boxes if char_box.page_no == page_no]
    if not page_boxes:
        return None
    bbox = _union_boxes([char_box.bbox for char_box in page_boxes])
    height = max(1.0, _median([char_box.bbox.y1 - char_box.bbox.y0 for char_box in page_boxes]))
    return RangeCandidate(
        range_index=range_index,
        text_range=text_range,
        token=token,
        text=text,
        page_no=page_no,
        bbox=bbox,
        height=height,
    )


def _is_repairable_token(token: str) -> bool:
    if not (MIN_TOKEN_LENGTH <= len(token) <= MAX_TOKEN_LENGTH):
        return False
    if BUSINESS_VALUE_PATTERN.search(token):
        return False
    return any(char.isalnum() or "一" <= char <= "鿿" for char in token)


def _normalize_token(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").translate(PUNCT_TRANSLATION))


def _token_count(text: str, token: str) -> int:
    normalized = _normalize_token(text)
    if not token:
        return 0
    return normalized.count(token)


def _match_spatial_candidates(
    left: list[RangeCandidate],
    right: list[RangeCandidate],
) -> list[tuple[RangeCandidate, RangeCandidate]]:
    pairs: list[tuple[RangeCandidate, RangeCandidate]] = []
    used_right: set[int] = set()
    for left_candidate in sorted(left, key=_candidate_order):
        candidates = [
            right_candidate
            for right_candidate in right
            if right_candidate.range_index not in used_right
            and _same_spatial_row(left_candidate, right_candidate)
        ]
        if not candidates:
            continue
        right_candidate = min(candidates, key=lambda item: _spatial_distance(left_candidate, item))
        used_right.add(right_candidate.range_index)
        pairs.append((left_candidate, right_candidate))
    return pairs


def _same_spatial_row(left: RangeCandidate, right: RangeCandidate) -> bool:
    if left.page_no != right.page_no:
        return False
    vertical_gap = abs(_center_y(left.bbox) - _center_y(right.bbox))
    tolerance = max(MAX_VERTICAL_GAP_ABSOLUTE, left.height * MAX_VERTICAL_GAP_MULTIPLIER, right.height * MAX_VERTICAL_GAP_MULTIPLIER)
    return vertical_gap <= tolerance


def _spatial_distance(left: RangeCandidate, right: RangeCandidate) -> tuple[float, float]:
    return (abs(_center_y(left.bbox) - _center_y(right.bbox)), abs(_center_x(left.bbox) - _center_x(right.bbox)))


def _candidate_order(candidate: RangeCandidate) -> tuple[int, float, float]:
    return (candidate.page_no, _center_y(candidate.bbox), _center_x(candidate.bbox))


def _dominant_page(char_boxes: list[CharBox]) -> int:
    page_counts: dict[int, int] = {}
    for char_box in char_boxes:
        page_counts[char_box.page_no] = page_counts.get(char_box.page_no, 0) + 1
    return max(page_counts, key=lambda page_no: page_counts[page_no])


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
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _center_x(bbox: BBox) -> float:
    return (bbox.x0 + bbox.x1) / 2


def _center_y(bbox: BBox) -> float:
    return (bbox.y0 + bbox.y1) / 2
