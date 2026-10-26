"""Recover missing cover labels from the affected PDF's own printed ink."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import statistics
import tempfile

import fitz

from app.models import BBox, TextBlock, TextSourceEvidence
from app.services.extractors.ppocrv5 import PPOCRV5Extractor

_PARTY = re.compile(r"^(业主方|设计方|买方|卖方|甲方|乙方)[:：]?(.{2,80}(?:有限公司|股份公司|设计院|研究院))$")


def _party_blocks(page):
    result = {}
    for block in page.blocks:
        match = _PARTY.fullmatch(re.sub(r"\s+", "", block.text))
        if match:
            result.setdefault(match[1], []).append(block)
    return {key: blocks[0] for key, blocks in result.items() if len(blocks) == 1}


def reconcile_cover_title_roles(original, compare):
    """Reconcile layout roles for identical, localized cover titles only."""
    changed = []
    if not original.pages or not compare.pages:
        return changed
    for side, own, peer in (("original", original.pages[0], compare.pages[0]),
                             ("compare", compare.pages[0], original.pages[0])):
        if min(len(_party_blocks(own)), len(_party_blocks(peer))) < 2:
            continue
        for block in own.blocks:
            text = re.sub(r"\s+", "", block.text)
            if block.block_type not in {"text", "ocr_line"} or not 6 <= len(text) <= 80:
                continue
            if not re.search(r"系统|设备|合同|协议|项目", text):
                continue
            if any(b.block_type == "doc_title" and re.sub(r"\s+", "", b.text) == text
                   and abs(b.bbox.y0 / peer.height - block.bbox.y0 / own.height) < .05
                   and abs(b.bbox.x0 / peer.width - block.bbox.x0 / own.width) < .05 for b in peer.blocks):
                block.block_type = "doc_title"
                block.semantic_reasons.append("MATCHED_COVER_TITLE_ROLE")
                changed.append((side, block))
    return changed


class CoverPartyOCR:
    def __init__(self, extractor=None):
        self.extractor = extractor or PPOCRV5Extractor()

    def enrich_pair(self, original, compare):
        diagnostics = []
        if not original.pages or not compare.pages:
            return diagnostics
        for side, own, peer in (("original", original, compare), ("compare", compare, original)):
            page, other = own.pages[0], peer.pages[0]
            own_roles, peer_roles = _party_blocks(page), _party_blocks(other)
            common = own_roles.keys() & peer_roles.keys()
            if len(common) < 2 or not Path(own.path).is_file():
                continue
            dy = statistics.median(own_roles[k].bbox.y0/page.height - peer_roles[k].bbox.y0/other.height for k in common)
            dx = statistics.median(own_roles[k].bbox.x0/page.width - peer_roles[k].bbox.x0/other.width for k in common)
            for role in sorted(peer_roles.keys() - own_roles.keys()):
                b = peer_roles[role].bbox
                # Peer geometry proposes a search area, never a field value.
                roi = BBox(x0=max(0, (b.x0/other.width+dx)*page.width-15),
                           y0=max(0, (b.y0/other.height+dy)*page.height-10),
                           x1=min(page.width, max(x.bbox.x1 for x in own_roles.values())+15),
                           y1=min(page.height, (b.y1/other.height+dy)*page.height+10))
                item = {"side": side, "page": page.page_no, "role": role, "roi": roi.model_dump(),
                        "method": "OWN_COVER_PARTY_LOCAL_OCR", "status": "NEEDS_REVIEW", "readings": []}
                diagnostics.append(item)
                try:
                    readings = self._read(own, roi, role, item)
                    if len(readings) != 2 or readings[0][0] != readings[1][0]:
                        continue
                    text, bbox, _ = readings[0]
                    confidence = min(r[2] for r in readings)
                    block = TextBlock(block_id=f"p{page.page_no}_local_cover_{role}", page_no=page.page_no,
                                      text=text, bbox=bbox, confidence=confidence, block_type="text", source="cover_party_local_ocr")
                    block.source_evidence = [TextSourceEvidence(source=f"own_cover_local_{r['scale']}", raw_text=r['text'],
                        page_no=page.page_no, bbox=BBox.model_validate(r['bbox']), confidence=r['confidence']) for r in item['readings']]
                    block.semantic_reasons.append("CONSISTENT_OWN_COVER_PARTY_READINGS")
                    page.blocks.append(block)
                    page.blocks.sort(key=lambda x: (x.bbox.y0, x.bbox.x0))
                    item.update(status="RECOVERED", text=text, block_id=block.block_id)
                except Exception as exc:
                    item['error'] = type(exc).__name__
        return diagnostics

    def _read(self, document, roi, role, diagnostic):
        import numpy as np
        readings = []
        with fitz.open(document.path) as pdf:
            model = document.pages[0]
            page = pdf[model.page_no-1]
            sx, sy = page.rect.width/model.width, page.rect.height/model.height
            clip = fitz.Rect(roi.x0*sx, roi.y0*sy, roi.x1*sx, roi.y1*sy) & page.rect
            if clip.is_empty:
                return readings
            for scale in (3.0, 5.0):
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, colorspace=fitz.csRGB, alpha=False, annots=False)
                rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
                cleaned = fitz.Pixmap(fitz.csGRAY, pix.width, pix.height, np.ascontiguousarray(rgb[:,:,0]).tobytes(), False)
                with tempfile.NamedTemporaryFile(suffix=".png") as image:
                    cleaned.save(image.name)
                    digest = hashlib.sha256(Path(image.name).read_bytes()).hexdigest()
                    diagnostic.setdefault('attempted_inputs', []).append(digest)
                    parsed = self.extractor.payload_to_document(self.extractor.predict(image.name, file_type=1), image.name)
                found = []
                for p in parsed.pages:
                    for block in p.blocks:
                        match = _PARTY.fullmatch(re.sub(r"\s+", "", block.text))
                        if match and match[1] == role and block.confidence is not None and block.confidence >= .85:
                            found.append((match[1]+"："+match[2], block, p))
                if len(found) != 1:
                    diagnostic['readings'].append({'scale':scale, 'input_sha256':digest,
                        'raw_texts':[b.text for p in parsed.pages for b in p.blocks]})
                    continue
                text, block, crop = found[0]
                b = block.bbox
                box = BBox(x0=(clip.x0+b.x0/crop.width*clip.width)/sx, y0=(clip.y0+b.y0/crop.height*clip.height)/sy,
                           x1=(clip.x0+b.x1/crop.width*clip.width)/sx, y1=(clip.y0+b.y1/crop.height*clip.height)/sy)
                readings.append((text, box, block.confidence))
                diagnostic['readings'].append({'scale':scale,'input_sha256':digest,'text':text,'bbox':box.model_dump(),'confidence':block.confidence})
        return readings
