"""Read disputed address lines from their own pixels; recover print under seals.

Peer text selects a candidate only. Neither geographical dictionaries nor peer
values are passed to OCR, and house numbers cannot be changed by this repair.
"""
from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

import fitz
import numpy as np

from app.models import BBox, TextSourceEvidence
from app.services.extractors.ppocrv5 import PPOCRV5Extractor
from app.services.single_character_ocr import normalized, repairable

logger = logging.getLogger(__name__)
SOURCE = "address_line_roi_ppocr"
LABEL = re.compile(r"(?:通讯地址|通信地址|联系地址|注册地址|住所地|地址)\s*[:：]")


def compact(text):
    return normalized(text)[0]


def same_address_reading(old, new):
    """Allow one Han glyph disagreement, preserving every number and suffix."""
    a, b = compact(old), compact(new)
    if len(a) != len(b):
        return False
    changed = [(x, y) for x, y in zip(a, b) if x != y]
    return len(changed) <= 1 and all(repairable(x) and repairable(y) for x, y in changed)


def address_tail_candidates(source, field_bbox, blocks):
    """Find nearby address-shaped print without granting it text ownership."""
    height = max(1., field_bbox.y1-field_bbox.y0)
    for b in blocks:
        text = compact(b.text)
        if (b.block_id == source.block_id or b.page_no != source.page_no
                or not 0 <= b.bbox.y0-field_bbox.y1 <= min(40., height*2.2)
                or abs(b.bbox.x0-field_bbox.x0) > max(18., height*1.5)
                or LABEL.search(text) or re.search(r"公司|专用章|签字|电话|账号|税号|联系人", text)
                or not 8 <= len(text) <= 80
                or not re.search(r"(?:路|街|巷|弄|道).*\d+号|^\d+号", text)
                or b.bbox.x1-b.bbox.x0 < (b.bbox.y1-b.bbox.y0)*5):
            continue
        yield b


def address_lines(page):
    result = {}
    for b in page.blocks:
        labels = list(LABEL.finditer(b.text))
        if not labels:
            continue
        if len(labels) == 1 and labels[0].start() == 0 and len(compact(b.text)) >= 20:
            result[b.block_id] = b
        # A source row may contain both parties. Each label has its own x anchor.
        for label in labels:
            chars = [c for c in b.char_boxes if c.text_index == label.start()]
            if label.start() and not chars:
                continue
            box = b.bbox.model_copy(update={"x0": chars[0].bbox.x0 if chars else b.bbox.x0})
            for tail in address_tail_candidates(b, box, page.blocks):
                result[tail.block_id] = tail
    return list(result.values())


def read_address_line(extractor, page, block, scale):
    box = block.bbox
    rect = fitz.Rect(box.x0-2, box.y0-2, box.x1+2, box.y1+2) & page.rect
    if rect.is_empty:
        return None
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    # Red ink is suppressed; black printed strokes remain. This does not invent ink.
    gray = np.ascontiguousarray(rgb[:, :, 0])
    with tempfile.NamedTemporaryFile(suffix=".png") as image:
        fitz.Pixmap(fitz.csGRAY, pix.width, pix.height, gray.tobytes(), False).save(image.name)
        parsed = extractor.payload_to_document(extractor.predict(image.name, file_type=1), image.name)
    found = []
    for p in parsed.pages:
        for reading in p.blocks:
            if (reading.confidence or 0) < .97 or not same_address_reading(block.text, reading.text):
                continue
            def mapped(b):
                return BBox(x0=(pix.x+b.x0/p.width*pix.width)/scale,
                            y0=(pix.y+b.y0/p.height*pix.height)/scale,
                            x1=(pix.x+b.x1/p.width*pix.width)/scale,
                            y1=(pix.y+b.y1/p.height*pix.height)/scale)
            mapped_box = mapped(reading.bbox)
            if (abs(mapped_box.x0-box.x0) > 8 or abs(mapped_box.y0-box.y0) > 8
                    or abs(mapped_box.x1-box.x1) > 10 or abs(mapped_box.y1-box.y1) > 8):
                continue
            result = reading.model_copy(deep=True)
            result.bbox = mapped_box
            result.char_boxes = [c.model_copy(update={"page_no":block.page_no,"bbox":mapped(c.bbox)})
                                 for c in reading.char_boxes]
            found.append(result)
    return found[0] if len(found) == 1 else None


def reconcile_addresses(original, compare, extractor=None):
    pending = []
    for side, document, peer in (("original", original, compare), ("compare", compare, original)):
        peers = {p.page_no: address_lines(p) for p in peer.pages}
        for page in document.pages:
            for block in address_lines(page):
                if block.source != "PP-OCRv6" or any(e.source.startswith("address_line_roi_") for e in block.source_evidence):
                    continue
                under_seal = block.block_type in {"seal", "stamp"}
                disputed = any(compact(block.text) != compact(b.text) and same_address_reading(block.text,b.text)
                               for b in peers.get(page.page_no, []))
                if under_seal or disputed:
                    pending.append((side, document, page, block))
    if not pending:
        return []
    extractor = extractor or PPOCRV5Extractor()
    changes = []
    for side, document, page, block in pending[:12]:
        if not Path(document.path).is_file():
            continue
        try:
            with fitz.open(document.path) as pdf:
                if not 1 <= page.page_no <= len(pdf):
                    continue
                pdf_page = pdf[page.page_no-1]
                if abs(pdf_page.rect.width-page.width) > 1 or abs(pdf_page.rect.height-page.height) > 1:
                    continue
                readings = [read_address_line(extractor, pdf_page, block, scale) for scale in (3, 5)]
        except Exception as exc:
            logger.warning("Address line verification unavailable: %s", type(exc).__name__)
            continue
        if any(r is None for r in readings):
            continue
        if compact(readings[0].text) != compact(readings[1].text):
            block.source_evidence.extend(TextSourceEvidence(source=SOURCE+"_disputed",raw_text=r.text,
                page_no=page.page_no,bbox=r.bbox,confidence=r.confidence) for r in readings)
            block.semantic_reasons.append("address_line_readings_disagree")
            changes.append((side, block))
            continue
        best = max(readings, key=lambda r:r.confidence)
        old = block.text
        block.source_evidence.append(TextSourceEvidence(source="address_line_before",raw_text=old,
            page_no=page.page_no,bbox=block.bbox,confidence=block.confidence))
        block.source_evidence.extend(TextSourceEvidence(source=SOURCE,raw_text=r.text,
            page_no=page.page_no,bbox=r.bbox,confidence=r.confidence) for r in readings)
        block.text = best.text
        block.char_boxes = best.char_boxes
        block.block_type = "text"
        block.block_role = "text"
        block.flow_role = "body"
        block.enter_clause_compare = True
        block.semantic_reasons.append("own_address_line_two_scale_verified")
        changes.append((side, block))
    return changes
