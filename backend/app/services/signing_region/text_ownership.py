"""Resolve recalled seal inscriptions before the final clause document is consumed.

Geometry proposes an owner, but cannot erase prose under a stamp. Only orphan
OCR spans with independently verified red ink in every character box qualify.
"""

from pathlib import Path
import math

import fitz
import numpy as np

from app.services.page_render_cache import PageRenderCache
from app.services.signing_region.models import SigningVisualObjectType

SEAL_TYPES = {
    SigningVisualObjectType.COMPANY_ROUND_SEAL,
    SigningVisualObjectType.PERSONAL_NAME_SEAL,
    SigningVisualObjectType.OTHER_STAMP,
    SigningVisualObjectType.EDGE_SEAM_SEAL,
}


def _overlap(inner, outer):
    area = max(1, (inner.x1 - inner.x0) * (inner.y1 - inner.y0))
    return (
        max(0, min(inner.x1, outer.x1) - max(inner.x0, outer.x0))
        * max(0, min(inner.y1, outer.y1) - max(inner.y0, outer.y0))
        / area
    )


def _ink_evidence(image, boxes, sx, sy):
    evidence = []
    for char in boxes:
        b = char.bbox
        crop = image[
            max(0, math.floor(b.y0 * sy)) : math.ceil(b.y1 * sy),
            max(0, math.floor(b.x0 * sx)) : math.ceil(b.x1 * sx),
            :3,
        ].astype(np.int16)
        if not crop.size:
            return None
        red, green, blue = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
        ink = crop.min(axis=2) < 190
        colored = ink & (red - green > 45) & (red - blue > 35) & (red > 90)
        dark = crop.max(axis=2) < 150
        count = int(ink.sum())
        red_ratio = float(colored.sum()) / max(1, count)
        dark_ratio = float(dark.sum()) / max(1, count)
        # Any black/mixed/empty character keeps the complete OCR span in prose.
        if count < 8 or red_ratio < 0.9 or dark_ratio > 0.03:
            return None
        evidence.append(
            {"text_index": char.text_index, "red_ratio": round(red_ratio, 4), "dark_ratio": round(dark_ratio, 4)}
        )
    return evidence


def reconcile_visual_seal_text(result, objects, pdf_path, render_cache=None):
    owners = [o for o in objects if o.object_type in SEAL_TYPES and o.visual_detection_confidence >= 0.75]
    if not owners or not Path(pdf_path).is_file():
        return
    cache = render_cache or PageRenderCache()
    try:
        pdf = fitz.open(pdf_path)
    except (RuntimeError, ValueError, OSError):
        return
    with pdf:
        for page in result.document.pages:
            local = [o for o in owners if o.page_no == page.page_no]
            if not local or not 1 <= page.page_no <= len(pdf):
                continue
            kept = []
            image = None
            for block in page.blocks:
                chars = [c for c in block.char_boxes if c.char.strip()]
                # Existing layout-backed text and native characters are independent
                # prose evidence. Never override them merely because ink overlaps.
                orphan_ocr = (
                    "ocr" in block.source.lower()
                    and not block.layout_block_id
                    and not block.raw_html
                    and block.block_type in {"text", "ocr_line", "seal", "stamp"}
                    and len(chars) == len("".join(block.text.split()))
                    and 0 < len(chars) <= 32
                )
                owner = next(
                    (
                        o
                        for o in local
                        if orphan_ocr
                        and _overlap(block.bbox, o.bbox) >= 0.75
                        and all(_overlap(c.bbox, o.bbox) >= 0.75 for c in chars)
                    ),
                    None,
                )
                if owner is None:
                    kept.append(block)
                    continue
                if image is None:
                    image = cache.render(pdf[page.page_no - 1], scale=2)
                proof = _ink_evidence(image, chars, image.shape[1] / page.width, image.shape[0] / page.height)
                if proof is None:
                    kept.append(block)
                    continue
                result.excluded_block_ids.append(block.block_id)
                result.entries.append(
                    {
                        "page_no": page.page_no,
                        "block_id": block.block_id,
                        "signing_block_id": owner.object_id,
                        "reason": "visual_seal_owned_ocr",
                        "text_preview": block.text,
                        "character_ink_evidence": proof,
                    }
                )
            page.blocks = kept
