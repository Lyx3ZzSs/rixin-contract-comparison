from __future__ import annotations

import math
from numbers import Real

from app.models import BBox, EvidenceBox

BBoxCoordinates = tuple[float, float, float, float]


def valid_bbox_coordinates(bbox: BBox, *, normalized: bool = False) -> BBoxCoordinates | None:
    source = bbox.normalized if normalized else bbox
    if source is None:
        return None
    coordinates = (source.x0, source.y0, source.x1, source.y1)
    if any(
        isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value))
        for value in coordinates
    ):
        return None
    x0, y0, x1, y1 = coordinates
    if x1 <= x0 or y1 <= y0:
        return None
    return tuple(float(value) for value in coordinates)


def comparable_bbox_coordinates(
    left: BBox,
    right: BBox,
) -> tuple[BBoxCoordinates, BBoxCoordinates] | None:
    left_normalized = valid_bbox_coordinates(left, normalized=True)
    right_normalized = valid_bbox_coordinates(right, normalized=True)
    if left_normalized is not None and right_normalized is not None:
        return left_normalized, right_normalized
    left_raw = valid_bbox_coordinates(left)
    right_raw = valid_bbox_coordinates(right)
    if left_raw is not None and right_raw is not None:
        return left_raw, right_raw
    return None


def is_located_evidence(evidence: EvidenceBox) -> bool:
    page_no = evidence.page_no
    if isinstance(page_no, bool) or not isinstance(page_no, int) or page_no <= 0:
        return False
    return valid_bbox_coordinates(evidence.bbox) is not None


def has_dedupe_location(evidence: EvidenceBox) -> bool:
    page_no = evidence.page_no
    if isinstance(page_no, bool) or not isinstance(page_no, int) or page_no <= 0:
        return False
    return (
        valid_bbox_coordinates(evidence.bbox, normalized=True) is not None
        or valid_bbox_coordinates(evidence.bbox) is not None
    )


def valid_evidence_copies(evidence: list[EvidenceBox]) -> list[EvidenceBox]:
    return [item.model_copy(deep=True) for item in evidence if is_located_evidence(item)]
