"""Locate equal cover content using independent text anchors, not paper margins."""
from __future__ import annotations

from itertools import combinations
from statistics import median

from app.models import BBox, TextBlock
from .patterns import normalize_extra


def aligned_cover_slot(
    target: BBox, peer: BBox, key: str,
    source_blocks: list[TextBlock], peer_blocks: list[TextBlock],
    source_size: tuple[float, float], peer_size: tuple[float, float],
) -> bool:
    def unique(blocks):
        groups = {}
        for block in blocks:
            text = normalize_extra(block.text)
            if (len(text) < 4 or text == key or block.block_role == "revision_artifact"
                    or block.block_type in {"seal", "seal_region", "watermark", "footer", "page_footer"}
                    or (block.confidence is not None and block.confidence < .85)):
                continue
            groups.setdefault(text, []).append(block)
        return {text: values[0].bbox for text, values in groups.items() if len(values) == 1}

    left, right = unique(source_blocks), unique(peer_blocks)
    pairs = [(left[text], right[text]) for text in sorted(left.keys() & right.keys())]
    if len(pairs) < 4:
        return False
    models = []
    witnesses = []
    for axis, dimension in enumerate(("x", "y")):
        def center(box):
            return (getattr(box, dimension + "0") + getattr(box, dimension + "1")) / 2
        points = [(center(a), center(b)) for a, b in pairs]
        tolerance = max(6.0, peer_size[axis] * .015)
        candidates = []
        for (x1, y1), (x2, y2) in combinations(points, 2):
            if abs(x2 - x1) < source_size[axis] * .15:
                continue
            scale = (y2 - y1) / (x2 - x1)
            if not .5 <= scale <= 2:
                continue
            offset = median(y - scale * x for x, y in points)
            residuals = [abs(scale * x + offset - y) for x, y in points]
            inliers = {i for i, residual in enumerate(residuals) if residual <= tolerance}
            candidates.append((len(inliers), -median(residuals), scale, offset, inliers))
        if not candidates:
            return False
        best = max(candidates, key=lambda item: item[:2])
        if best[0] < max(3, len(points) * .7):
            return False
        models.append((best[2], best[3], tolerance))
        witnesses.append(best[4])
    if len(witnesses[0] & witnesses[1]) < 3:
        return False
    for dimension, (scale, offset, tolerance) in zip(("x", "y"), models):
        for edge in ("0", "1"):
            if abs(scale * getattr(target, dimension + edge) + offset - getattr(peer, dimension + edge)) > tolerance:
                return False
    return True
