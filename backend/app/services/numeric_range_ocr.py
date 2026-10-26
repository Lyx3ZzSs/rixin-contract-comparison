"""Verify OCR's ambiguous 一 inside numeric ranges from the same PDF crop.

Both readings must independently supply a dash and the existing endpoints.
The peer document supplies neither replacement characters nor numeric values.
"""
from pathlib import Path
import re
import tempfile

import fitz

from app.models import BBox, TextSourceEvidence
from app.services.extractors.ppocrv5 import PPOCRV5Extractor
from app.services.payment_symbol_ocr import compact

SOURCE = 'numeric_range_roi_ppocr'
RANGE = re.compile(r'(?<![\d.])\d+(?:\.\d+)?一\d+(?:\.\d+)?(?![\d.])')


def read_range(extractor, page, rect, scale, expected):
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
    with tempfile.NamedTemporaryFile(suffix='.png') as image:
        pix.save(image.name)
        doc = extractor.payload_to_document(extractor.predict(image.name, file_type=1), image.name)
    found = []
    for p in doc.pages:
        for block in p.blocks:
            # Dash typography is equivalent here; Chinese 一 is not accepted
            # as confirmation of itself.
            text = compact(block.text)
            normalized = re.sub(r'[—–−-]', '—', text)
            if normalized != expected or (block.confidence or 0) < .95:
                continue
            box = block.bbox
            mapped = BBox(x0=(pix.x+box.x0/p.width*pix.width)/scale,
                          y0=(pix.y+box.y0/p.height*pix.height)/scale,
                          x1=(pix.x+box.x1/p.width*pix.width)/scale,
                          y1=(pix.y+box.y1/p.height*pix.height)/scale)
            found.append((block.text, mapped, block.confidence))
    return found[0] if len(found) == 1 else None


def reconcile_numeric_ranges(original, compare, extractor=None):
    extractor = extractor or PPOCRV5Extractor()
    changes = []
    for side, doc in (('original', original), ('compare', compare)):
        candidates = [(page, block, match) for page in doc.pages for block in page.blocks
                      if block.block_type == 'text' and block.source == 'PP-OCRv6'
                      for match in reversed(list(RANGE.finditer(block.text)))][:32]
        if not candidates or not Path(doc.path).is_file():
            continue
        with fitz.open(doc.path) as pdf:
            for page, block, match in candidates:
                chars = [c for c in block.char_boxes if c.text_index is not None
                         and match.start() <= c.text_index < match.end()]
                if {c.text_index for c in chars} != set(range(match.start(), match.end())):
                    continue
                if not 1 <= page.page_no <= len(pdf):
                    continue
                rect = fitz.Rect(min(c.bbox.x0 for c in chars)-4, min(c.bbox.y0 for c in chars)-6,
                                 max(c.bbox.x1 for c in chars)+4, max(c.bbox.y1 for c in chars)+6)
                expected = match.group().replace('一', '—')
                try:
                    readings = [read_range(extractor, pdf[page.page_no-1], rect, scale, expected) for scale in (3,4)]
                except Exception:
                    continue
                if any(r is None for r in readings):
                    continue
                old = block.text
                index = match.start()+match.group().index('一')
                block.text = old[:index]+'—'+old[index+1:]
                block.char_boxes = [c.model_copy(update={'char':'—'}) if c.text_index == index else c
                                    for c in block.char_boxes]
                block.source_evidence.append(TextSourceEvidence(source='numeric_range_before',raw_text=old,
                    page_no=page.page_no,bbox=block.bbox,confidence=block.confidence))
                for text, box, confidence in readings:
                    block.source_evidence.append(TextSourceEvidence(source=SOURCE,raw_text=text,
                        page_no=page.page_no,bbox=box,confidence=confidence))
                block.semantic_reasons.append('own_numeric_range_two_scale_verified')
                if not any(b is block for _, b in changes):
                    changes.append((side,block))
    return changes
