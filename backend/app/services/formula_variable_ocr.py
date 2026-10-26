"""Verify disputed definition variables against two crops of their own pixels.

The peer locates a disagreement; it is never used as replacement text. Keep
unresolved readings and real letter substitutions in the ordinary comparison.
"""
from __future__ import annotations

import logging
import re
import tempfile
import unicodedata
from pathlib import Path

import fitz

from app.models import BBox, TextSourceEvidence
from app.services.extractors.ppocrv5 import PPOCRV5Extractor

logger = logging.getLogger(__name__)
VARIABLE = re.compile(r"[A-Za-z]{1,6}(?=为)")
SOURCE = "formula_variable_roi_ppocr"
MAX_VARIABLES = 12


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def signature(text):
    return VARIABLE.sub("<VAR>", compact(text))


def repairable(old, new):
    if old == new:
        return False
    if old.lower() == new.lower():
        return sum(a != b for a, b in zip(old, new)) == 1
    return len(new) == len(old) + 1 and any(new[:i] + new[i+1:] == old for i in range(len(new)))


def eligible(page, block):
    return (block.block_type == "text" and block.source == "PP-OCRv6" and bool(block.char_boxes)
            and not any(e.source == SOURCE for e in block.source_evidence)
            and sum("一" <= c <= "鿿" for c in block.text) >= 6
            and any(b.block_type == "formula" and 0 <= block.bbox.y0-b.bbox.y1 <= 85
                    and min(block.bbox.x1, b.bbox.x1) > max(block.bbox.x0, b.bbox.x0)
                    for b in page.blocks))


def candidates(original, compare):
    for side, target, peer in (("original", original, compare), ("compare", compare, original)):
        pages = {p.page_no: p for p in peer.pages}
        for page in target.pages:
            other = pages.get(page.page_no)
            if other is None:
                continue
            for block in page.blocks:
                if not eligible(page, block):
                    continue
                matches = [b for b in other.blocks if b.block_type == "text"
                           and signature(b.text) == signature(block.text)
                           and abs(b.bbox.y0 / other.height - block.bbox.y0 / page.height) < .04]
                if len(matches) != 1:
                    continue
                own, theirs = list(VARIABLE.finditer(block.text)), list(VARIABLE.finditer(matches[0].text))
                if len(own) != len(theirs):
                    continue
                # Work from right to left so inserting a subscript cannot
                # invalidate a pending variable's source character indices.
                for a, b in reversed(list(zip(own, theirs))):
                    if repairable(a.group(), b.group()):
                        yield side, target, page, block, a


def context_box(block, variable):
    start, end = max(0, variable.start()-1), min(len(block.text), variable.end()+3)
    chars = [c for c in block.char_boxes if c.text_index is not None and start <= c.text_index < end]
    if {c.text_index for c in chars} != {i for i in range(start, end) if not block.text[i].isspace()}:
        return None
    rect = fitz.Rect(min(c.bbox.x0 for c in chars)-3, min(c.bbox.y0 for c in chars)-4,
                     max(c.bbox.x1 for c in chars)+3, max(c.bbox.y1 for c in chars)+4)
    return start, end, rect


def read_crop(extractor, pdf_page, rect, scale, prefix, suffix):
    pix = pdf_page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
    with tempfile.NamedTemporaryFile(suffix=".png") as image:
        pix.save(image.name)
        doc = extractor.payload_to_document(extractor.predict(image.name, file_type=1), image.name)
    found = []
    for page in doc.pages:
        for block in page.blocks:
            if (block.confidence or 0) < .85:
                continue
            pattern = re.escape(compact(prefix)) + r"([A-Za-z]{1,6})" + re.escape(compact(suffix))
            match = re.fullmatch(pattern, compact(block.text))
            variables = list(re.finditer(r"[A-Za-z]{1,6}(?=\s*为)", block.text))
            if match is None or len(variables) != 1 or variables[0].group() != match[1]:
                continue
            variable = variables[0]
            chars = [c for c in block.char_boxes if c.text_index is not None
                     and variable.start() <= c.text_index < variable.end()]
            if {c.text_index for c in chars} != set(range(variable.start(), variable.end())):
                continue

            def mapped(box):
                # Pixmap origin accounts for clipping and raster rounding.
                return BBox(x0=(pix.x + box.x0 / page.width * pix.width) / scale,
                            y0=(pix.y + box.y0 / page.height * pix.height) / scale,
                            x1=(pix.x + box.x1 / page.width * pix.width) / scale,
                            y1=(pix.y + box.y1 / page.height * pix.height) / scale)

            found.append((match[1], [c.model_copy(update={"bbox": mapped(c.bbox),
                          "text_index": c.text_index-variable.start()}) for c in chars],
                          block.text, mapped(block.bbox), block.confidence))
    return found[0] if len(found) == 1 else None


def reconcile_formula_variables(original, compare, extractor=None):
    pending = list(candidates(original, compare))[:MAX_VARIABLES]
    if not pending:
        return []
    extractor = extractor or PPOCRV5Extractor()
    changes = []
    for side, doc, page, block, variable in pending:
        if not Path(doc.path).is_file():
            continue
        context = context_box(block, variable)
        if context is None:
            continue
        start, end, rect = context
        prefix, suffix = block.text[start:variable.start()], block.text[variable.end():end]
        try:
            with fitz.open(doc.path) as pdf:
                if not 1 <= page.page_no <= len(pdf):
                    continue
                pdf_page = pdf[page.page_no-1]
                rect &= pdf_page.rect
                if rect.is_empty:
                    continue
                readings = [read_crop(extractor, pdf_page, rect, scale, prefix, suffix) for scale in (3, 4)]
        except Exception as exc:
            logger.warning("Formula variable verification unavailable: %s", type(exc).__name__)
            continue
        if any(r is None for r in readings) or readings[0][0] != readings[1][0]:
            continue
        token = readings[0][0]
        if not repairable(variable.group(), token):
            continue
        old_text = block.text
        delta = len(token) - len(variable.group())
        kept = []
        for char in block.char_boxes:
            if char.text_index is None or char.text_index < variable.start():
                kept.append(char)
            elif char.text_index >= variable.end():
                kept.append(char.model_copy(update={"text_index": char.text_index+delta}))
        best = max(readings, key=lambda r: r[4])
        kept.extend(c.model_copy(update={"page_no": page.page_no, "text_index": variable.start()+c.text_index})
                    for c in best[1])
        block.char_boxes = sorted(kept, key=lambda c: c.text_index if c.text_index is not None else -1)
        block.text = old_text[:variable.start()] + token + old_text[variable.end():]
        block.source_evidence.append(TextSourceEvidence(source="formula_variable_before", raw_text=old_text,
                                    page_no=page.page_no, bbox=block.bbox, confidence=block.confidence))
        for reading in readings:
            block.source_evidence.append(TextSourceEvidence(source=SOURCE, raw_text=reading[2],
                                        page_no=page.page_no, bbox=reading[3], confidence=reading[4]))
        block.semantic_reasons.append("own_formula_variable_two_scale_verified")
        changes.append((side, block))
    return changes
