"""Re-read a missing printed signing line from its own red-suppressed pixels."""
import re
import tempfile
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import fitz
import numpy as np

from app.models import BBox, TextSourceEvidence
from app.services.extractors.ppocrv5 import PPOCRV5Extractor


def key(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def _partial_field_candidates(left, right):
    """Use a peer label and geometry to locate a split line, never its value."""
    for block in left.blocks:
        text = key(block.text)
        label = re.match(r'^[^:<>]{2,16}:', text)
        if block.block_type not in {'text','paragraph_title'} or '\n' in block.text or not label or not 12 <= len(text) <= 120:
            continue
        parts = sorted([b for b in right.blocks if b.block_type in {'text','paragraph_title'} and key(b.text)
                        and abs(b.bbox.y0-block.bbox.y0) <= 12
                        and b.bbox.x0 >= block.bbox.x0-18 and b.bbox.x1 <= block.bbox.x1+18],
                       key=lambda b:b.bbox.x0)
        if len(parts) < 2 or not key(parts[0].text).startswith(label.group()):
            continue
        suffix = key(parts[-1].text)
        joined = ''.join(key(b.text) for b in parts)
        if len(suffix) < 4 or not text.endswith(suffix) or not len(joined)+3 <= len(text):
            continue
        yield block, parts, label.group(), suffix


def _read_occluded_field(extractor, page, rect, scale, label, suffix, parts):
    pix = page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=rect,alpha=False)
    rgb = np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3)
    red = rgb[:,:,0].astype(int)-np.minimum(rgb[:,:,1],rgb[:,:,2])
    if np.count_nonzero(red > 35) < 40:
        return None
    cleaned = np.ascontiguousarray(rgb[:,:,0])
    with tempfile.NamedTemporaryFile(suffix='.png') as image:
        fitz.Pixmap(fitz.csGRAY,pix.width,pix.height,cleaned.tobytes(),False).save(image.name)
        parsed = extractor.payload_to_document(extractor.predict(image.name,file_type=1),image.name)
    found = []
    for p in parsed.pages:
        for reading in p.blocks:
            text = key(reading.text)
            if ((reading.confidence or 0) < .95 or not text.startswith(label) or not text.endswith(suffix)
                    or len(text) < sum(len(key(b.text)) for b in parts)+3):
                continue
            # Every existing fragment must still occur in order. Conflicting
            # new readings remain unresolved instead of replacing known text.
            cursor = 0
            for part in parts:
                pos = text.find(key(part.text),cursor)
                if pos < 0:
                    break
                cursor = pos+len(key(part.text))
            else:
                def mapped(box):
                    return BBox(x0=(pix.x+box.x0/p.width*pix.width)/scale,
                                y0=(pix.y+box.y0/p.height*pix.height)/scale,
                                x1=(pix.x+box.x1/p.width*pix.width)/scale,
                                y1=(pix.y+box.y1/p.height*pix.height)/scale)
                recovered = reading.model_copy(deep=True)
                recovered.bbox = mapped(reading.bbox)
                recovered.char_boxes = [c.model_copy(update={'bbox':mapped(c.bbox)}) for c in reading.char_boxes]
                if abs(recovered.bbox.y0-parts[0].bbox.y0) <= 12:
                    found.append(recovered)
    return found[0] if len(found) == 1 else None


def recover_occluded_field_lines(reference, target, extractor=None):
    """Recover an occluded labeled line using two agreeing same-side readings.

    Reference text only selects a bounded crop. A differing project/company
    name read from that crop is retained, so real edits still reach comparison.
    """
    if not Path(target.path).is_file():
        return []
    extractor = extractor or PPOCRV5Extractor()
    peers = {p.page_no:p for p in reference.pages}
    changes = []
    with fitz.open(target.path) as pdf:
        for page in target.pages:
            left = peers.get(page.page_no)
            if left is None or not 1 <= page.page_no <= len(pdf) or not pdf[page.page_no-1].get_images():
                continue
            for block,parts,label,suffix in list(_partial_field_candidates(left,page))[:16]:
                if any(not any(b is part for b in page.blocks) for part in parts):
                    continue
                box = block.bbox
                rect = fitz.Rect(min(box.x0,parts[0].bbox.x0)-12,min(box.y0,parts[0].bbox.y0)-18,
                                 max(box.x1,parts[-1].bbox.x1)+12,max(box.y1,parts[-1].bbox.y1)+18)
                try:
                    readings = [_read_occluded_field(extractor,pdf[page.page_no-1],rect,scale,label,suffix,parts)
                                for scale in (3,4)]
                except Exception:
                    continue
                if any(r is None for r in readings) or key(readings[0].text) != key(readings[1].text):
                    continue
                recovered = max(readings,key=lambda b:b.confidence).model_copy(deep=True)
                recovered.block_id = parts[0].block_id
                recovered.page_no = page.page_no
                recovered.block_type = 'text'
                recovered.source = 'occluded_field_roi_ppocr'
                recovered.char_boxes = [c.model_copy(update={'page_no':page.page_no}) for c in recovered.char_boxes]
                recovered.source_evidence = [TextSourceEvidence(source=recovered.source,raw_text=r.text,
                    page_no=page.page_no,bbox=r.bbox,confidence=r.confidence) for r in readings]
                recovered.source_evidence.extend(TextSourceEvidence(source='occluded_field_before',raw_text=b.text,
                    page_no=page.page_no,bbox=b.bbox,confidence=b.confidence) for b in parts)
                recovered.semantic_reasons.append('own_occluded_field_two_scale_verified')
                consumed = {id(b) for b in parts}
                page.blocks = [b for b in page.blocks if id(b) not in consumed]+[recovered]
                page.blocks.sort(key=lambda b:(b.bbox.y0,b.bbox.x0))
                changes.append(recovered)
    return changes


def recover_printed_signing_lines(reference, target, extractor=None):
    if not Path(target.path).is_file():
        return []
    extractor = extractor or PPOCRV5Extractor()
    changes = []
    with fitz.open(target.path) as pdf:
        for left, right in zip(reference.pages, target.pages):
            if left.page_no != right.page_no or right.page_no > len(pdf):
                continue
            page = pdf[right.page_no-1]
            if not page.get_images():
                continue
            # A signing area and a red impression are both necessary. This is
            # not document-wide text completion from the other contract.
            if not any(re.search(r'甲方|乙方|法定代表', b.text) for b in right.blocks):
                continue
            candidates = [b for b in left.blocks if
                (re.fullmatch(r'.{2,12}分公司', key(b.text)) or re.fullmatch(r'法定代表人?[（(]负责人[）)]或?', key(b.text)))
                and not any(key(b.text) == key(t.text) for t in right.blocks)]
            if not candidates:
                continue
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
            rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
            for block in candidates:
                box = block.bbox
                x0, y0 = max(0, int((box.x0-80)*3)), max(0, int((box.y0-45)*3))
                x1, y1 = min(pix.width, int((box.x1+80)*3)), min(pix.height, int((box.y1+45)*3))
                roi = rgb[y0:y1, x0:x1]
                if roi.size == 0:
                    continue
                red = roi[:, :, 0].astype(int)-np.minimum(roi[:, :, 1], roi[:, :, 2])
                if np.count_nonzero(red > 35) < 40:
                    continue
                cleaned = np.ascontiguousarray(roi[:, :, 0])
                try:
                    with tempfile.NamedTemporaryFile(suffix='.png') as image:
                        fitz.Pixmap(fitz.csGRAY, cleaned.shape[1], cleaned.shape[0], cleaned.tobytes(), False).save(image.name)
                        parsed = extractor.payload_to_document(extractor.predict(image.name, file_type=1), image.name)
                except Exception:
                    continue
                found = [(p, b) for p in parsed.pages for b in p.blocks
                         if key(b.text) == key(block.text) and (b.confidence or 0) >= .95]
                if len(found) != 1:
                    continue
                p, reading = found[0]
                def mapped(b):
                    return BBox(x0=(x0+b.x0/p.width*cleaned.shape[1])/3,
                                y0=(y0+b.y0/p.height*cleaned.shape[0])/3,
                                x1=(x0+b.x1/p.width*cleaned.shape[1])/3,
                                y1=(y0+b.y1/p.height*cleaned.shape[0])/3)
                bbox = mapped(reading.bbox)
                if abs((bbox.y0+bbox.y1)-(box.y0+box.y1)) > 80:
                    continue
                same_line = [b for b in right.blocks if b.text.strip()
                             and (key(b.text) in key(reading.text) or (
                                 key(b.text).startswith('法定代表') and key(reading.text).startswith('法定代表')
                                 and SequenceMatcher(None, key(b.text), key(reading.text)).ratio() >= .8))
                             and abs(b.bbox.y0-bbox.y0) <= 12 and abs(b.bbox.x0-bbox.x0) <= 35]
                if len(same_line) > 1:
                    continue
                recovered = reading.model_copy(deep=True)
                recovered.block_id = same_line[0].block_id if same_line else f'{block.block_id}:own_red_channel_recall'
                recovered.page_no = right.page_no
                recovered.bbox = bbox
                recovered.block_type = 'text'
                recovered.source = 'red_suppressed_printed_roi_ppocrv5'
                recovered.char_boxes = [c.model_copy(update={'page_no':right.page_no,'bbox':mapped(c.bbox)}) for c in reading.char_boxes]
                recovered.source_evidence = [TextSourceEvidence(source=recovered.source, raw_text=reading.text,
                    page_no=right.page_no,bbox=bbox,confidence=reading.confidence)]
                if same_line:
                    right.blocks[right.blocks.index(same_line[0])] = recovered
                else:
                    right.blocks.append(recovered)
                right.blocks.sort(key=lambda b:(b.bbox.y0,b.bbox.x0))
                changes.append(recovered)
    return changes
