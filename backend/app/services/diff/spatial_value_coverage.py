from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.models import BBox, CharBox, Clause, TextRange
from app.services.diff.text_utils import merge_ranges

MIN_TOKEN_LENGTH = 4
MAX_TOKEN_LENGTH = 40
MAX_VERTICAL_GAP_ABSOLUTE = 18.0
MAX_VERTICAL_GAP_MULTIPLIER = 2.5
MAX_HORIZONTAL_GAP_ABSOLUTE = 70.0
MAX_HORIZONTAL_GAP_WIDTH_RATIO = 0.8

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
class ValueCandidate:
    range_index: int
    start: int
    end: int
    token: str
    page_no: int
    bbox: BBox
    height: float
    width: float


@dataclass(frozen=True)
class NormalizedChar:
    char: str
    source_index: int


def repair_spatial_value_coverage(
    left: Clause,
    right: Clause,
    left_ranges: list[TextRange],
    right_ranges: list[TextRange],
) -> tuple[list[TextRange], list[TextRange], list[str]]:
    """Drop one-sided value ranges when the same value is visually present opposite.

    Text diff can pair repeated values with the wrong occurrence when signing or
    form-like content has repaired reading order. This pass does not infer any
    domain fields; it only removes a one-sided range when the same normalized
    text exists on the other side at a close visual position and is not itself
    marked changed.
    """

    if not _has_usable_char_boxes(left) or not _has_usable_char_boxes(right):
        return left_ranges, right_ranges, []

    ignored_left, left_reasons = _covered_range_indexes(
        source=left,
        target=right,
        source_ranges=left_ranges,
        target_ranges=right_ranges,
        source_highlight_type="DELETE",
    )
    ignored_right, right_reasons = _covered_range_indexes(
        source=right,
        target=left,
        source_ranges=right_ranges,
        target_ranges=left_ranges,
        source_highlight_type="ADD",
    )

    if not ignored_left and not ignored_right:
        return left_ranges, right_ranges, []

    repaired_left = [item for index, item in enumerate(left_ranges) if index not in ignored_left]
    repaired_right = [item for index, item in enumerate(right_ranges) if index not in ignored_right]
    return merge_ranges(repaired_left), merge_ranges(repaired_right), [*left_reasons, *right_reasons]


def _covered_range_indexes(
    source: Clause,
    target: Clause,
    source_ranges: list[TextRange],
    target_ranges: list[TextRange],
    source_highlight_type: str,
) -> tuple[set[int], list[str]]:
    ignored: set[int] = set()
    reasons: list[str] = []
    for index, text_range in enumerate(source_ranges):
        if text_range.highlight_type != source_highlight_type:
            continue
        source_candidate = _candidate_for_range(source, text_range, index)
        if source_candidate is None:
            continue
        target_candidates = [
            candidate
            for candidate in _occurrences_for_token(target, source_candidate.token)
            if not _range_intersects_changed_target(candidate, target_ranges)
            and _same_visual_position(source_candidate, candidate)
        ]
        if not target_candidates:
            continue
        ignored.add(index)
        matched = min(target_candidates, key=lambda item: _spatial_distance(source_candidate, item))
        reasons.append(
            "spatial_value_coverage:"
            f"{source_candidate.token}@p{source_candidate.page_no}->{matched.page_no}"
        )
    return ignored, reasons


def _candidate_for_range(clause: Clause, text_range: TextRange, range_index: int) -> ValueCandidate | None:
    start = max(0, min(text_range.start, len(clause.text)))
    end = max(start, min(text_range.end, len(clause.text)))
    token = _normalize_token(clause.text[start:end])
    if not _is_repairable_token(token):
        return None
    return _candidate_from_char_range(clause, start, end, token, range_index)


def _occurrences_for_token(clause: Clause, token: str) -> list[ValueCandidate]:
    normalized = _normalized_chars(clause.text)
    text = "".join(item.char for item in normalized)
    if not text or not token:
        return []

    candidates: list[ValueCandidate] = []
    cursor = 0
    while True:
        position = text.find(token, cursor)
        if position < 0:
            break
        source_start = normalized[position].source_index
        source_end = normalized[position + len(token) - 1].source_index + 1
        candidate = _candidate_from_char_range(clause, source_start, source_end, token, source_start)
        if candidate is not None:
            candidates.append(candidate)
        cursor = position + 1
    return candidates


def _candidate_from_char_range(
    clause: Clause,
    start: int,
    end: int,
    token: str,
    range_index: int,
) -> ValueCandidate | None:
    boxes = [
        char_box
        for char_box in clause.char_boxes[start:end]
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
    return ValueCandidate(
        range_index=range_index,
        start=start,
        end=end,
        token=token,
        page_no=page_no,
        bbox=bbox,
        height=height,
        width=max(1.0, bbox.x1 - bbox.x0),
    )


def _range_intersects_changed_target(candidate: ValueCandidate, ranges: list[TextRange]) -> bool:
    return any(candidate.start < item.end and item.start < candidate.end for item in ranges)


def _same_visual_position(left: ValueCandidate, right: ValueCandidate) -> bool:
    if left.page_no != right.page_no:
        return False
    vertical_gap = abs(_center_y(left.bbox) - _center_y(right.bbox))
    vertical_tolerance = max(
        MAX_VERTICAL_GAP_ABSOLUTE,
        left.height * MAX_VERTICAL_GAP_MULTIPLIER,
        right.height * MAX_VERTICAL_GAP_MULTIPLIER,
    )
    if vertical_gap > vertical_tolerance:
        return False

    horizontal_gap = abs(_center_x(left.bbox) - _center_x(right.bbox))
    horizontal_tolerance = max(
        MAX_HORIZONTAL_GAP_ABSOLUTE,
        min(left.width, right.width) * MAX_HORIZONTAL_GAP_WIDTH_RATIO,
    )
    return horizontal_gap <= horizontal_tolerance


def _spatial_distance(left: ValueCandidate, right: ValueCandidate) -> tuple[float, float]:
    return (abs(_center_y(left.bbox) - _center_y(right.bbox)), abs(_center_x(left.bbox) - _center_x(right.bbox)))


def _normalize_token(text: str) -> str:
    return "".join(item.char for item in _normalized_chars(text))


def _normalized_chars(text: str) -> list[NormalizedChar]:
    result: list[NormalizedChar] = []
    for index, char in enumerate(text or ""):
        for normalized in unicodedata.normalize("NFKC", char.translate(PUNCT_TRANSLATION)):
            if normalized.isspace():
                continue
            result.append(NormalizedChar(normalized.lower(), index))
    return result


def _is_repairable_token(token: str) -> bool:
    if not (MIN_TOKEN_LENGTH <= len(token) <= MAX_TOKEN_LENGTH):
        return False
    if not any(char.isalnum() or "一" <= char <= "鿿" for char in token):
        return False
    return bool(re.search(r"[0-9a-zA-Z]", token) or sum(1 for char in token if "一" <= char <= "鿿") >= 2)


def _has_usable_char_boxes(clause: Clause) -> bool:
    return bool(clause.char_boxes) and len(clause.char_boxes) == len(clause.text)


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
