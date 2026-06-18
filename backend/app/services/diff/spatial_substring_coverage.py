from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from app.models import BBox, CharBox, Clause, TextRange
from app.services.diff.text_utils import is_whitespace_only, merge_ranges, trim_range_whitespace

MIN_TOKEN_LENGTH = 2
MAX_TOKEN_LENGTH = 30
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
class NormalizedChar:
    char: str
    source_index: int


@dataclass(frozen=True)
class SpatialCandidate:
    range_index: int
    start: int
    end: int
    token: str
    page_no: int
    bbox: BBox
    height: float
    width: float


def repair_spatial_substring_coverage(
    left: Clause,
    right: Clause,
    left_ranges: list[TextRange],
    right_ranges: list[TextRange],
) -> tuple[list[TextRange], list[TextRange], list[str]]:
    """Remove ADD/DELETE text that is already covered as a positioned substring."""

    if not _has_usable_char_boxes(left) or not _has_usable_char_boxes(right):
        return left_ranges, right_ranges, []

    repaired_left, repaired_right, left_reasons = _repair_direction(
        source=left,
        target=right,
        source_ranges=left_ranges,
        target_ranges=right_ranges,
        source_highlight_type="DELETE",
        target_highlight_type="ADD",
    )
    repaired_right, repaired_left, right_reasons = _repair_direction(
        source=right,
        target=left,
        source_ranges=repaired_right,
        target_ranges=repaired_left,
        source_highlight_type="ADD",
        target_highlight_type="DELETE",
    )
    reasons = [*left_reasons, *right_reasons]
    if not reasons:
        return left_ranges, right_ranges, []
    return merge_ranges(repaired_left), merge_ranges(repaired_right), reasons


def _repair_direction(
    source: Clause,
    target: Clause,
    source_ranges: list[TextRange],
    target_ranges: list[TextRange],
    source_highlight_type: str,
    target_highlight_type: str,
) -> tuple[list[TextRange], list[TextRange], list[str]]:
    cuts_by_source: dict[int, list[tuple[int, int]]] = {}
    ignored_target: set[int] = set()
    reasons: list[str] = []

    for target_index, target_range in enumerate(target_ranges):
        if target_range.highlight_type != target_highlight_type:
            continue
        target_candidate = _candidate_for_range(target, target_range, target_index)
        if target_candidate is None:
            continue

        source_match = _matching_source_substring(
            source,
            source_ranges,
            source_highlight_type,
            target_candidate,
        )
        if source_match is None:
            continue

        source_index, source_candidate = source_match
        cuts_by_source.setdefault(source_index, []).append((source_candidate.start, source_candidate.end))
        ignored_target.add(target_index)
        reasons.append(
            "spatial_substring_coverage:"
            f"{target_candidate.token}@p{source_candidate.page_no}->{target_candidate.page_no}"
        )

    if not cuts_by_source and not ignored_target:
        return source_ranges, target_ranges, []

    repaired_source = _remove_source_cuts(source, source_ranges, cuts_by_source)
    repaired_target = [item for index, item in enumerate(target_ranges) if index not in ignored_target]
    return repaired_source, repaired_target, reasons


def _matching_source_substring(
    source: Clause,
    source_ranges: list[TextRange],
    source_highlight_type: str,
    target_candidate: SpatialCandidate,
) -> tuple[int, SpatialCandidate] | None:
    matches: list[tuple[int, SpatialCandidate]] = []
    for source_index, source_range in enumerate(source_ranges):
        if source_range.highlight_type != source_highlight_type:
            continue
        source_text = source.text[source_range.start:source_range.end]
        if _normalize_token(source_text) == target_candidate.token:
            continue
        for source_candidate in _occurrences_in_range(source, source_range, target_candidate.token):
            if _same_visual_position(source_candidate, target_candidate):
                matches.append((source_index, source_candidate))
    if not matches:
        return None
    return min(matches, key=lambda item: _spatial_distance(item[1], target_candidate))


def _remove_source_cuts(
    source: Clause,
    source_ranges: list[TextRange],
    cuts_by_source: dict[int, list[tuple[int, int]]],
) -> list[TextRange]:
    repaired: list[TextRange] = []
    for index, source_range in enumerate(source_ranges):
        cuts = sorted(cuts_by_source.get(index, []))
        if not cuts:
            repaired.append(source_range)
            continue

        cursor = source_range.start
        for cut_start, cut_end in cuts:
            if cursor < cut_start:
                _append_trimmed_range(repaired, source, cursor, cut_start, source_range.highlight_type)
            cursor = max(cursor, cut_end)
        if cursor < source_range.end:
            _append_trimmed_range(repaired, source, cursor, source_range.end, source_range.highlight_type)
    return repaired


def _append_trimmed_range(
    ranges: list[TextRange],
    clause: Clause,
    start: int,
    end: int,
    highlight_type: str,
) -> None:
    start, end = trim_range_whitespace(clause.text, start, end)
    if start >= end or is_whitespace_only(clause.text, start, end):
        return
    ranges.append(TextRange(start=start, end=end, highlight_type=highlight_type))


def _candidate_for_range(clause: Clause, text_range: TextRange, range_index: int) -> SpatialCandidate | None:
    start = max(0, min(text_range.start, len(clause.text)))
    end = max(start, min(text_range.end, len(clause.text)))
    token = _normalize_token(clause.text[start:end])
    if not _is_repairable_token(token):
        return None
    return _candidate_from_char_range(clause, start, end, token, range_index)


def _occurrences_in_range(
    clause: Clause,
    text_range: TextRange,
    token: str,
) -> list[SpatialCandidate]:
    normalized = [
        item
        for item in _normalized_chars(clause.text[text_range.start:text_range.end], offset=text_range.start)
        if text_range.start <= item.source_index < text_range.end
    ]
    text = "".join(item.char for item in normalized)
    if not text or not token:
        return []

    candidates: list[SpatialCandidate] = []
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
) -> SpatialCandidate | None:
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
    return SpatialCandidate(
        range_index=range_index,
        start=start,
        end=end,
        token=token,
        page_no=page_no,
        bbox=bbox,
        height=height,
        width=max(1.0, bbox.x1 - bbox.x0),
    )


def _same_visual_position(left: SpatialCandidate, right: SpatialCandidate) -> bool:
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


def _spatial_distance(left: SpatialCandidate, right: SpatialCandidate) -> tuple[float, float]:
    return (abs(_center_y(left.bbox) - _center_y(right.bbox)), abs(_center_x(left.bbox) - _center_x(right.bbox)))


def _normalize_token(text: str) -> str:
    return "".join(item.char for item in _normalized_chars(text))


def _normalized_chars(text: str, offset: int = 0) -> list[NormalizedChar]:
    result: list[NormalizedChar] = []
    for index, char in enumerate(text or ""):
        for normalized in unicodedata.normalize("NFKC", char.translate(PUNCT_TRANSLATION)):
            if normalized.isspace():
                continue
            result.append(NormalizedChar(normalized.lower(), offset + index))
    return result


def _is_repairable_token(token: str) -> bool:
    if not (MIN_TOKEN_LENGTH <= len(token) <= MAX_TOKEN_LENGTH):
        return False
    return sum(1 for char in token if char.isalnum() or "一" <= char <= "鿿") >= 2


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
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _center_x(bbox: BBox) -> float:
    return (bbox.x0 + bbox.x1) / 2


def _center_y(bbox: BBox) -> float:
    return (bbox.y0 + bbox.y1) / 2
