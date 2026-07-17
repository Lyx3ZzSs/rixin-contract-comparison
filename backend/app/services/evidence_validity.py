from __future__ import annotations

import math
from numbers import Real

from app.models import EvidenceBox


def is_located_evidence(evidence: EvidenceBox) -> bool:
    page_no = evidence.page_no
    if isinstance(page_no, bool) or not isinstance(page_no, int) or page_no <= 0:
        return False
    coordinates = (evidence.bbox.x0, evidence.bbox.y0, evidence.bbox.x1, evidence.bbox.y1)
    if any(
        isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value))
        for value in coordinates
    ):
        return False
    x0, y0, x1, y1 = coordinates
    return x1 > x0 and y1 > y0
