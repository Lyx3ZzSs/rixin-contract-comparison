"""Verify disputed payment symbols from the affected PDF's own pixels.

A unique peer line only identifies a candidate. Two local OCR readings must
supply the symbol and unchanged surrounding text. Underlines may be removed
only inside a bounded blank between characters, never across printed text.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import logging
from pathlib import Path
import re
import tempfile
import unicodedata

import fitz
import numpy as np
from PIL import Image

from app.models import BBox, TextSourceEvidence
from app.services.extractors.ppocrv5 import PPOCRV5Extractor

logger = logging.getLogger(__name__)
SOURCE = "payment_symbol_roi_ppocr"
MAX_SYMBOLS = 12


def indexed_text(text):
    chars, indices = [], []
    for index, char in enumerate(text):
        for c in unicodedata.normalize("NFKC", char):
            if not c.isspace():
                chars.append(c)
                indices.append(index)
    return "".join(chars), indices


def compact(text):
    return indexed_text(text)[0]


def signature(text):
    return re.sub(r"Y(?=[/\d.,]+元)", "¥", compact(text)).replace("/", "")


def candidates(original, compare):
    for side, target, peer in (("original", original, compare), ("compare", compare, original)):
        pages = {p.page_no: p for p in peer.pages}
        for page in target.pages:
            other = pages.get(page.page_no)
            if other is None:
                continue
            for block in page.blocks:
                if (block.block_type != "text" or block.source != "PP-OCRv6" or not block.char_boxes
                        or any(e.source == SOURCE for e in block.source_evidence)
                        or not re.search(r"款|支付|发票", block.text)):
                    continue
                text, indices = indexed_text(block.text)
                if sum("一" <= c <= "鿿" for c in text) < 6:
                    continue
                peers = [b for b in other.blocks if b.block_type == "text"
                         and signature(b.text) == signature(text)
                         and abs(b.bbox.y0 / other.height - block.bbox.y0 / page.height) < .04]
                if len(peers) != 1:
                    continue
                expected = compact(peers[0].text)
                for op, a, z, c, d in reversed(SequenceMatcher(None, text, expected, autojunk=False).get_opcodes()):
                    if op == "replace" and text[a:z] == "Y" and expected[c:d] == "¥":
                        if re.match(r"[/\d.,]+元", text[z:]):
                            yield side, target, page, block, indices[a], indices[z-1]+1, "currency"
                    elif op == "insert" and expected[c:d] == "/" and 0 < a < len(text):
                        if re.match(r"(?:个工作日|工作日|天|日|个月|月)", text[a:]):
                            yield side, target, page, block, indices[a], indices[a], "slash"


def context(block, start, end, kind):
    text, indices = indexed_text(block.text)
    a = indices.index(start)
    z = a + (end > start)
    left, right = max(0, a-2), min(len(text), z+(3 if kind == "slash" else 2))
    lo, hi = indices[left], indices[right-1]+1
    chars = [c for c in block.char_boxes if c.text_index is not None and lo <= c.text_index < hi]
    if {c.text_index for c in chars} != {i for i in range(lo, hi) if not block.text[i].isspace()}:
        return None
    rect = fitz.Rect(min(c.bbox.x0 for c in chars)-3, min(c.bbox.y0 for c in chars)-4,
                     max(c.bbox.x1 for c in chars)+3, max(c.bbox.y1 for c in chars)+4)
    gap = None
    if kind == "slash":
        before = next(c for c in chars if c.text_index == indices[a-1])
        after = next(c for c in chars if c.text_index == indices[a])
        height = min(before.bbox.y1-before.bbox.y0, after.bbox.y1-after.bbox.y0)
        gap = fitz.Rect(before.bbox.x1+.5, max(before.bbox.y0, after.bbox.y0),
                        after.bbox.x0-.5, min(before.bbox.y1, after.bbox.y1))
        if height <= 0 or not 3 <= gap.width <= height*2.5 or gap.height < height*.7:
            return None
    return block.text[lo:start], block.text[end:hi], rect, gap


def remove_gap_underline(pix, scale, gap):
    """Remove only a thin, long horizontal stroke at the foot of the gap."""
    pixels = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).copy()
    x0, x1 = max(0, round(gap.x0*scale-pix.x)), min(pix.width, round(gap.x1*scale-pix.x))
    y0, y1 = max(0, round(gap.y0*scale-pix.y)), min(pix.height, round(gap.y1*scale-pix.y))
    if x1 <= x0 or y1 <= y0:
        return None
    dark = pixels[y0:y1, x0:x1, :3].mean(axis=2) < 190
    rows = np.flatnonzero(dark.mean(axis=1) >= .75)
    if (len(rows) == 0 or rows[0] < .65*(y1-y0) or rows[-1]-rows[0]+1 > .22*(y1-y0)
            or np.any(np.diff(rows) != 1)):
        return None
    # Require ink above the rule: a blank underline is not a missing symbol.
    above = dark[:max(0, rows[0]-1)]
    if above.sum() < 2*scale or np.count_nonzero(above.any(axis=1)) < .25*(y1-y0):
        return None
    pixels[max(y0, y0+rows[0]-1):min(y1+1, y0+rows[-1]+2), x0:x1] = 255
    return Image.fromarray(pixels[:, :, :3])


def read_crop(extractor, page, rect, scale, prefix, suffix, kind, gap):
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
    cleaned = remove_gap_underline(pix, scale, gap) if gap is not None else None
    if gap is not None and cleaned is None:
        return None
    with tempfile.NamedTemporaryFile(suffix=".png") as image:
        if cleaned is None:
            pix.save(image.name)
        else:
            cleaned.save(image.name)
        doc = extractor.payload_to_document(extractor.predict(image.name, file_type=1), image.name)
    found = []
    for p in doc.pages:
        for b in p.blocks:
            if (b.confidence or 0) < .85:
                continue
            text, indices = indexed_text(b.text)
            token = "¥" if kind == "currency" else "/"
            if text != compact(prefix)+token+compact(suffix):
                continue
            index = indices[len(compact(prefix))]
            chars = [c for c in b.char_boxes if c.text_index == index]
            if len(chars) != 1 or compact(chars[0].char) != token:
                continue

            def mapped(box):
                return BBox(x0=(pix.x+box.x0/p.width*pix.width)/scale,
                            y0=(pix.y+box.y0/p.height*pix.height)/scale,
                            x1=(pix.x+box.x1/p.width*pix.width)/scale,
                            y1=(pix.y+box.y1/p.height*pix.height)/scale)

            char = chars[0].model_copy(update={"char": token, "bbox": mapped(chars[0].bbox)})
            if gap is not None and not (gap.x0 <= (char.bbox.x0+char.bbox.x1)/2 <= gap.x1):
                continue
            found.append((token, char, b.text, mapped(b.bbox), b.confidence))
    return found[0] if len(found) == 1 else None


def reconcile_payment_symbols(original, compare, extractor=None):
    pending = list(candidates(original, compare))[:MAX_SYMBOLS]
    if not pending:
        return []
    extractor = extractor or PPOCRV5Extractor()
    changes = []
    for side, doc, page, block, start, end, kind in pending:
        crop = context(block, start, end, kind)
        if crop is None or not Path(doc.path).is_file():
            continue
        prefix, suffix, rect, gap = crop
        try:
            with fitz.open(doc.path) as pdf:
                if not 1 <= page.page_no <= len(pdf):
                    continue
                pdf_page = pdf[page.page_no-1]
                rect &= pdf_page.rect
                if rect.is_empty:
                    continue
                readings = [read_crop(extractor, pdf_page, rect, scale, prefix, suffix, kind, gap) for scale in (3, 4)]
        except Exception as exc:
            logger.warning("Payment symbol verification unavailable: %s", type(exc).__name__)
            continue
        if any(r is None for r in readings) or readings[0][0] != readings[1][0]:
            continue
        old = block.text
        token = readings[0][0]
        delta = len(token)-(end-start)
        kept = [c.model_copy(update={"text_index": c.text_index+delta}) if c.text_index is not None and c.text_index >= end
                else c for c in block.char_boxes if c.text_index is None or not start <= c.text_index < end]
        best = max(readings, key=lambda r: r[4])
        kept.append(best[1].model_copy(update={"page_no": page.page_no, "text_index": start}))
        block.char_boxes = sorted(kept, key=lambda c: c.text_index if c.text_index is not None else -1)
        block.text = old[:start]+token+old[end:]
        block.source_evidence.append(TextSourceEvidence(source="payment_symbol_before", raw_text=old,
                                    page_no=page.page_no, bbox=block.bbox, confidence=block.confidence))
        for r in readings:
            block.source_evidence.append(TextSourceEvidence(source=SOURCE, raw_text=r[2], page_no=page.page_no,
                                        bbox=r[3], confidence=r[4]))
        block.semantic_reasons.append("own_payment_symbol_two_scale_verified")
        changes.append((side, block))
    return changes
