"""Verify isolated OCR disagreements using two contexts from the target PDF.

A peer line selects the location only. Neither its disputed character nor a
language-based correction dictionary is supplied to the image reader.
"""
from __future__ import annotations

import logging
import tempfile
import unicodedata
from pathlib import Path

import fitz

from app.models import BBox, TextSourceEvidence
from app.services.extractors.ppocrv5 import PPOCRV5Extractor

logger = logging.getLogger(__name__)
SOURCE = 'single_character_roi_ppocr'
MAX_CANDIDATES = 12
# Numbers, negations and obligation operators require their own critical-value
# validation. They must not enter this narrow orthographic repair path.
PROTECTED = set('零〇一二三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟两不未无非否勿莫禁须应可免')


def normalized(text):
    positions, chars = [], []
    for index, char in enumerate(text):
        for value in unicodedata.normalize('NFKC', char):
            if not value.isspace():
                positions.append(index)
                chars.append(value)
    return ''.join(chars), positions


def repairable(char):
    return len(char) == 1 and '\u4e00' <= char <= '\u9fff' and char not in PROTECTED


def candidates(original, compare):
    for side, document, peer in (('original',original,compare), ('compare',compare,original)):
        peer_pages = {p.page_no:p for p in peer.pages}
        for page in document.pages:
            other_page = peer_pages.get(page.page_no)
            if other_page is None:
                continue
            others = [(b,normalized(b.text)[0]) for b in other_page.blocks
                      if b.block_type == 'text' and b.block_role not in {'revision_artifact','page_footer','page_header'}]
            for block in page.blocks:
                if (block.source != 'PP-OCRv6' or block.block_type != 'text'
                        or block.block_role in {'revision_artifact','page_footer','page_header'}
                        or not block.char_boxes or any(e.source == SOURCE for e in block.source_evidence)):
                    continue
                own, indices = normalized(block.text)
                if not 24 <= len(own) <= 200:
                    continue
                nearby = [text for b,text in others if len(text) == len(own)
                          and abs(b.bbox.y0/other_page.height - block.bbox.y0/page.height) < .12]
                if own in nearby:
                    continue
                matches = []
                for text in nearby:
                    changed = [i for i,(a,b) in enumerate(zip(own,text)) if a != b]
                    if len(changed) == 1:
                        index = changed[0]
                        if repairable(own[index]) and repairable(text[index]):
                            matches.append(index)
                if len(matches) == 1:
                    yield side,document,page,block,indices[matches[0]]


def crop_context(block, index, radius):
    start,end = max(0,index-radius),min(len(block.text),index+radius+1)
    # Both sides must anchor the reread, with exact source character coverage.
    if index-start < 2 or end-index-1 < 2:
        return None
    chars = [c for c in block.char_boxes if c.text_index is not None and start <= c.text_index < end]
    required = {i for i in range(start,end) if not block.text[i].isspace()}
    if {c.text_index for c in chars} != required or len(chars) != len(required):
        return None
    if any(c.char != block.text[c.text_index] for c in chars):
        return None
    rect = fitz.Rect(min(c.bbox.x0 for c in chars)-3,min(c.bbox.y0 for c in chars)-4,
                     max(c.bbox.x1 for c in chars)+3,max(c.bbox.y1 for c in chars)+4)
    return start,end,rect


def read_crop(extractor, page, rect, scale, prefix, suffix):
    pix = page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=rect,alpha=False)
    with tempfile.NamedTemporaryFile(suffix='.png') as image:
        pix.save(image.name)
        result = extractor.payload_to_document(extractor.predict(image.name,file_type=1),image.name)
    prefix,suffix = normalized(prefix)[0],normalized(suffix)[0]
    found = []
    for p in result.pages:
        for block in p.blocks:
            text,indices = normalized(block.text)
            if ((block.confidence or 0) < .95 or len(text) != len(prefix)+1+len(suffix)
                    or not text.startswith(prefix) or not text.endswith(suffix)):
                continue
            token = text[len(prefix)]
            if not repairable(token):
                continue
            chars = [c for c in block.char_boxes if c.text_index == indices[len(prefix)] and c.char == token]
            if len(chars) != 1 or (chars[0].confidence is not None and chars[0].confidence < .95):
                continue
            def mapped(box):
                return BBox(x0=(pix.x+box.x0/p.width*pix.width)/scale,
                            y0=(pix.y+box.y0/p.height*pix.height)/scale,
                            x1=(pix.x+box.x1/p.width*pix.width)/scale,
                            y1=(pix.y+box.y1/p.height*pix.height)/scale)
            found.append((token, chars[0].model_copy(update={'bbox':mapped(chars[0].bbox)}),
                          block.text,mapped(block.bbox),block.confidence))
    return found[0] if len(found) == 1 else None


def reconcile_single_characters(original, compare, extractor=None):
    pending = list(candidates(original,compare))[:MAX_CANDIDATES]
    if not pending:
        return []
    extractor = extractor or PPOCRV5Extractor()
    changes = []
    for side,document,page,block,index in pending:
        contexts = [crop_context(block,index,radius) for radius in (3,5)]
        if any(c is None for c in contexts) or not Path(document.path).is_file():
            continue
        try:
            with fitz.open(document.path) as pdf:
                if not 1 <= page.page_no <= len(pdf):
                    continue
                pdf_page = pdf[page.page_no-1]
                if abs(pdf_page.rect.width-page.width) > 1 or abs(pdf_page.rect.height-page.height) > 1:
                    continue
                readings = []
                for (start,end,rect),scale in zip(contexts,(3,4)):
                    rect &= pdf_page.rect
                    if rect.is_empty:
                        break
                    readings.append(read_crop(extractor,pdf_page,rect,scale,
                                              block.text[start:index],block.text[index+1:end]))
        except Exception as exc:
            logger.warning('Single character verification unavailable: %s',type(exc).__name__)
            continue
        if len(readings) != 2 or any(r is None for r in readings) or readings[0][0] != readings[1][0]:
            continue
        token = readings[0][0]
        if token == block.text[index]:
            continue
        # Require both image reads to locate the new reading at the disputed
        # source glyph, not merely elsewhere inside the surrounding crop.
        source_char = next(c for c in block.char_boxes if c.text_index == index)
        box = source_char.bbox
        if any(not (box.x0-2 <= (r[1].bbox.x0+r[1].bbox.x1)/2 <= box.x1+2
                       and box.y0-2 <= (r[1].bbox.y0+r[1].bbox.y1)/2 <= box.y1+2) for r in readings):
            continue
        old_text = block.text
        block.source_evidence.append(TextSourceEvidence(source='single_character_before',raw_text=old_text,
            page_no=page.page_no,bbox=block.bbox,confidence=block.confidence))
        for reading in readings:
            block.source_evidence.append(TextSourceEvidence(source=SOURCE,raw_text=reading[2],
                page_no=page.page_no,bbox=reading[3],confidence=reading[4]))
        best = max(readings,key=lambda r:r[4])
        block.text = old_text[:index]+token+old_text[index+1:]
        block.char_boxes = [best[1].model_copy(update={'page_no':page.page_no,'text_index':index})
                            if c.text_index == index else c for c in block.char_boxes]
        block.semantic_reasons.append('own_single_character_two_context_verified')
        changes.append((side,block))
    return changes
