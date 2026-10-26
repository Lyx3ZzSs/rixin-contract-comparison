"""Verify 任/仟 only inside a money value using that document's own pixels."""
from decimal import Decimal
import logging
from pathlib import Path
import re
import tempfile

import fitz

from app.models import BBox, TextSourceEvidence
from app.services.extractors.ppocrv5 import PPOCRV5Extractor
from app.services.single_character_ocr import normalized

logger = logging.getLogger(__name__)
SOURCE = 'financial_amount_roi_ppocr'
_DIGITS = dict(zip('零壹贰叁肆伍陆柒捌玖', range(10)))
_UNITS = {'拾': 10, '佰': 100, '仟': 1000, '万': 10000, '亿': 100000000}
_TOKEN = re.compile(r'[零壹贰叁肆伍陆柒捌玖拾佰仟任万亿]+元(?:整|[零壹贰叁肆伍陆柒捌玖角分]+)')


def amount_value(token):
    """Parse an uppercase monetary value without normalizing disputed glyphs."""
    integer, fraction = token.split('元')
    total = section = number = 0
    for char in integer:
        if char in _DIGITS:
            number = _DIGITS[char]
        else:
            unit = _UNITS[char]
            if unit < 10000:
                section += number * unit
            else:
                section = (section + number) * unit
                total += section
                section = 0
            number = 0
    cents = 0
    if fraction != '整':
        if not re.fullmatch(r'零?(?:[壹贰叁肆伍陆柒捌玖]角)?(?:[零壹贰叁肆伍陆柒捌玖]分)?', fraction):
            raise ValueError('Invalid fractional amount')
        for digit, unit in re.findall(r'([零壹贰叁肆伍陆柒捌玖])([角分])', fraction):
            cents += _DIGITS[digit] * (10 if unit == '角' else 1)
    return Decimal(total + section + number) + Decimal(cents) / 100


def candidates(document):
    for page in document.pages:
        blocks = sorted(page.blocks, key=lambda b: (b.bbox.y0, b.bbox.x0))
        for pos, block in enumerate(blocks):
            if (block.source != 'PP-OCRv6' or block.block_type != 'text' or not block.char_boxes
                    or block.block_role in {'revision_artifact', 'page_footer', 'page_header'}):
                continue
            text, indices = normalized(block.text)
            for match in _TOKEN.finditer(text):
                token = match.group()
                if token.count('任') != 1 or not re.search(r'[壹贰叁肆伍陆柒捌玖]任', token):
                    continue
                tail = text[match.end():]
                if not tail and pos + 1 < len(blocks):
                    following = blocks[pos + 1]
                    height = block.bbox.y1 - block.bbox.y0
                    if (following.block_type == 'text' and 0 <= following.bbox.y0 - block.bbox.y1 <= height
                            and min(block.bbox.x1, following.bbox.x1) > max(block.bbox.x0, following.bbox.x0)):
                        tail = normalized(following.text)[0]
                numeric = re.match(r'\([¥￥]([0-9]+(?:\.[0-9]{1,2})?)元\)', tail)
                if numeric is None:
                    continue
                try:
                    if amount_value(token.replace('任', '仟')) != Decimal(numeric[1]):
                        continue
                except (ValueError, KeyError):
                    continue
                start, end = indices[match.start()], indices[match.end() - 1] + 1
                label = re.search(r'(?:[（(]大写[）)]|大写[:：])\s*$', block.text[:start])
                if label is not None:
                    yield page, block, start, end, label.start(), indices[match.start() + token.index('任')]


def _read_amount(extractor, page, block, start, end, index):
    chars = [c for c in block.char_boxes if c.text_index is not None and start <= c.text_index < end]
    required = {i for i in range(start, end) if not block.text[i].isspace()}
    if (len(chars) != len(required) or {c.text_index for c in chars} != required
            or any(c.char != block.text[c.text_index] for c in chars)):
        return None
    rect = fitz.Rect(min(c.bbox.x0 for c in chars) - 2, min(c.bbox.y0 for c in chars) - 3,
                     max(c.bbox.x1 for c in chars) + 2, max(c.bbox.y1 for c in chars) + 3) & page.rect
    if rect.is_empty:
        return None
    scale = 3
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
    with tempfile.NamedTemporaryFile(suffix='.png') as image:
        pix.save(image.name)
        result = extractor.payload_to_document(extractor.predict(image.name, file_type=1), image.name)
    expected = normalized(block.text[start:index] + '仟' + block.text[index + 1:end])[0]
    target_index = len(normalized(block.text[start:index])[0])
    hits = []
    for p in result.pages:
        for reading in p.blocks:
            text, positions = normalized(reading.text)
            # Whole-value equality checks every other numeral as well as the label.
            if text != expected or (reading.confidence or 0) < .94:
                continue
            target = [c for c in reading.char_boxes if c.text_index == positions[target_index] and c.char == '仟']
            if len(target) != 1 or (target[0].confidence is not None and target[0].confidence < .94):
                continue
            def mapped(box):
                return BBox(x0=(pix.x + box.x0 / p.width * pix.width) / scale,
                            y0=(pix.y + box.y0 / p.height * pix.height) / scale,
                            x1=(pix.x + box.x1 / p.width * pix.width) / scale,
                            y1=(pix.y + box.y1 / p.height * pix.height) / scale)
            hits.append((mapped(target[0].bbox), reading.text, mapped(reading.bbox), reading.confidence))
    return hits[0] if len(hits) == 1 else None


def reconcile_financial_amounts(original, compare, extractor=None):
    pending = [(side, doc, *c) for side, doc in (('original', original), ('compare', compare))
               for c in candidates(doc)][:12]
    if not pending:
        return []
    extractor = extractor or PPOCRV5Extractor()
    changes = []
    for side, document, page, block, start, end, label_start, index in pending:
        if not Path(document.path).is_file():
            continue
        try:
            with fitz.open(document.path) as pdf:
                if not 1 <= page.page_no <= len(pdf):
                    continue
                native = pdf[page.page_no - 1]
                if abs(native.rect.width - page.width) > 1 or abs(native.rect.height - page.height) > 1:
                    continue
                readings = [_read_amount(extractor, native, block, lo, end, index)
                            for lo in (start, label_start)]
        except Exception as exc:
            logger.warning('Financial amount verification unavailable: %s', type(exc).__name__)
            continue
        # The isolated full value must be very clear. The longer label context
        # tolerates punctuation uncertainty but must agree on every amount glyph.
        if any(r is None for r in readings) or readings[0][3] < .98:
            continue
        source = next(c for c in block.char_boxes if c.text_index == index)
        box = source.bbox
        if any(not (box.x0 - 2 <= (r[0].x0 + r[0].x1) / 2 <= box.x1 + 2
                    and box.y0 - 2 <= (r[0].y0 + r[0].y1) / 2 <= box.y1 + 2) for r in readings):
            continue
        block.source_evidence.append(TextSourceEvidence(source='financial_amount_before', raw_text=block.text,
            page_no=page.page_no, bbox=block.bbox, confidence=block.confidence))
        for reading in readings:
            block.source_evidence.append(TextSourceEvidence(source=SOURCE, raw_text=reading[1],
                page_no=page.page_no, bbox=reading[2], confidence=reading[3]))
        block.text = block.text[:index] + '仟' + block.text[index + 1:]
        source.char = '仟'
        block.semantic_reasons.append('own_financial_amount_two_context_verified')
        changes.append((side, block))
    return changes
