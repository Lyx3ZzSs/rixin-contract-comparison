from __future__ import annotations

from app.models import BBox, NormalizedBBox

# MinerU uses 0-1000 range for normalized coordinates.
NORM_SCALE = 1000.0


def normalize_bbox(bbox: BBox, page_w: float, page_h: float) -> NormalizedBBox:
    """Convert pixel-space BBox to 0-1000 NormalizedBBox."""
    if page_w <= 0 or page_h <= 0:
        return NormalizedBBox(x0=0, y0=0, x1=0, y1=0)
    return NormalizedBBox(
        x0=bbox.x0 / page_w * NORM_SCALE,
        y0=bbox.y0 / page_h * NORM_SCALE,
        x1=bbox.x1 / page_w * NORM_SCALE,
        y1=bbox.y1 / page_h * NORM_SCALE,
    )


def denormalize_bbox(nbbox: NormalizedBBox, page_w: float, page_h: float) -> BBox:
    """Convert 0-1000 NormalizedBBox back to pixel-space BBox."""
    return BBox(
        x0=nbbox.x0 / NORM_SCALE * page_w,
        y0=nbbox.y0 / NORM_SCALE * page_h,
        x1=nbbox.x1 / NORM_SCALE * page_w,
        y1=nbbox.y1 / NORM_SCALE * page_h,
    )


def normalize_document(document) -> None:
    """Add normalized coordinates to all BBoxes in a Document.

    Mutates the document in-place by setting ``bbox.normalized`` on
    every ``TextBlock`` and ``EvidenceBox``.
    """
    for page in document.pages:
        w, h = page.width, page.height
        for block in page.blocks:
            if block.bbox.normalized is None:
                block.bbox.normalized = normalize_bbox(block.bbox, w, h)
            for char_box in block.char_boxes:
                if char_box.bbox.normalized is None:
                    char_box.bbox.normalized = normalize_bbox(char_box.bbox, w, h)
