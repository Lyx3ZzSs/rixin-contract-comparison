"""Recover heading ownership from located evidence, never from peer text alone."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import tempfile

import fitz

from app.models import BBox, TextSourceEvidence
from app.services.clause_numbering import ClauseNumberParser
from app.services.normalizer import TextNormalizer

_PARSER = ClauseNumberParser()
_NORMALIZER = TextNormalizer()
_TITLE_TYPES = {"paragraph_title", "doc_title", "title"}


def numbered_heading(text):
    if len(text.splitlines()) != 1:
        return None
    parsed = _PARSER.parse_line(text)
    if (
        parsed is None
        or parsed.non_clause
        or not 4 <= len(parsed.title) <= 32
        or re.search(r"[。；;：:！？!?]", parsed.title)
    ):
        return None
    return parsed


def _located(page, block, other_page, other):
    return (
        abs(page.page_no - other_page.page_no) <= 1
        and abs(block.bbox.y0 / page.height - other.bbox.y0 / other_page.height) <= 0.035
        and abs(block.bbox.x0 / page.width - other.bbox.x0 / other_page.width) <= 0.075
    )


def _peers(document, page, block, title):
    key = _NORMALIZER.normalize_for_match(title)
    return [
        (p, b)
        for p in document.pages
        for b in p.blocks
        if b.block_type in _TITLE_TYPES
        and (b.confidence or 0) >= 0.9
        and (parsed := numbered_heading(b.text)) is not None
        and _NORMALIZER.normalize_for_match(parsed.title) == key
        and _located(page, block, p, b)
    ]


def reconcile_numbered_heading_roles(original, compare):
    """A unique counterpart corroborates the role, but each side keeps its number."""
    changes = []
    for side, own, peer in (("original", original, compare), ("compare", compare, original)):
        for page in own.pages:
            for block in page.blocks:
                parsed = numbered_heading(block.text)
                if (
                    block.block_type not in {"text", "ocr_line"}
                    or parsed is None
                    or (block.confidence or 0) < 0.9
                    or block.enter_clause_compare is False
                ):
                    continue
                candidates = _peers(peer, page, block, parsed.title)
                if len(candidates) != 1:
                    continue
                p, b = candidates[0]
                owners = [
                    x
                    for q in own.pages
                    for x in q.blocks
                    if (h := numbered_heading(x.text)) is not None
                    and _NORMALIZER.normalize_for_match(h.title) == _NORMALIZER.normalize_for_match(parsed.title)
                    and _located(q, x, p, b)
                ]
                if len(owners) != 1:
                    continue
                changes.append((side, block))
    for _, block in changes:
        block.block_type = "paragraph_title"
        block.flow_role = "heading"
        block.semantic_reasons.append("paired_located_numbered_heading")
    return changes


class HeadingPrefixOCR:
    """Bounded local OCR for a missing number; two scales must agree on own ink."""

    def __init__(self, extractor=None):
        from app.services.extractors.ppocrv5 import PPOCRV5Extractor

        self.extractor = extractor or PPOCRV5Extractor()

    def enrich_pair(self, original, compare):
        diagnostics = []
        for side, own, peer in (("original", original, compare), ("compare", compare, original)):
            if not Path(own.path).is_file():
                continue
            for page in own.pages:
                for block in page.blocks:
                    title = _NORMALIZER.normalize_for_match(block.text)
                    if (
                        len(diagnostics) >= 4
                        or block.block_type not in {"text", "ocr_line", *_TITLE_TYPES}
                        or "ocr" not in block.source.lower()
                        or numbered_heading(block.text)
                        or len(block.text.splitlines()) != 1
                        or not 4 <= len(title) <= 32
                        or block.enter_clause_compare is False
                    ):
                        continue
                    peers = _peers(peer, page, block, block.text)
                    if len(peers) != 1:
                        continue
                    p, other = peers[0]
                    roi = BBox(
                        x0=max(0, min(block.bbox.x0 - 40, other.bbox.x0 / p.width * page.width - 12)),
                        y0=max(0, block.bbox.y0 - 12),
                        x1=min(page.width, block.bbox.x1 + 20),
                        y1=min(page.height, block.bbox.y1 + 8),
                    )
                    item = dict(
                        side=side,
                        page_no=page.page_no,
                        block_id=block.block_id,
                        status="UNRESOLVED",
                        before=block.text,
                        roi=roi.model_dump(),
                        readings=[],
                    )
                    diagnostics.append(item)
                    try:
                        readings = self._read(own, page, roi, item)
                        parsed = [numbered_heading(b.text) for b in readings]
                        if (
                            len(readings) != 2
                            or any(h is None for h in parsed)
                            or any((b.confidence or 0) < 0.9 for b in readings)
                            or _NORMALIZER.normalize_for_match(readings[0].text)
                            != _NORMALIZER.normalize_for_match(readings[1].text)
                            or any(_NORMALIZER.normalize_for_match(h.title) != title for h in parsed)
                        ):
                            continue
                        block.source_evidence.append(
                            TextSourceEvidence(
                                source=block.source,
                                raw_text=block.text,
                                bbox=block.bbox,
                                page_no=page.page_no,
                                confidence=block.confidence,
                            )
                        )
                        for scale, reading in zip((3, 5), readings):
                            block.source_evidence.append(
                                TextSourceEvidence(
                                    source=f"own_heading_local_ocr_{scale}",
                                    raw_text=reading.text,
                                    bbox=reading.bbox,
                                    page_no=page.page_no,
                                    confidence=reading.confidence,
                                )
                            )
                        reading = readings[0]
                        block.text, block.bbox, block.char_boxes = reading.text, reading.bbox, reading.char_boxes
                        block.confidence = min(b.confidence for b in readings)
                        block.source = "heading_local_ocr"
                        block.block_type, block.flow_role = "paragraph_title", "heading"
                        block.semantic_reasons.append("consistent_own_heading_prefix_readings")
                        item.update(status="RECOVERED", after=block.text)
                    except Exception as exc:
                        item["error"] = type(exc).__name__
        return diagnostics

    def _read(self, document, page, roi, diagnostic):
        import numpy as np

        readings = []
        with fitz.open(document.path) as pdf:
            source = pdf[page.page_no - 1]
            sx, sy = source.rect.width / page.width, source.rect.height / page.height
            clip = fitz.Rect(roi.x0 * sx, roi.y0 * sy, roi.x1 * sx, roi.y1 * sy) & source.rect
            if clip.is_empty:
                return []
            for scale in (3, 5):
                pix = source.get_pixmap(
                    matrix=fitz.Matrix(scale, scale), clip=clip, colorspace=fitz.csRGB, alpha=False, annots=False
                )
                rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
                clean = fitz.Pixmap(
                    fitz.csGRAY, pix.width, pix.height, np.ascontiguousarray(rgb[:, :, 0]).tobytes(), False
                )
                with tempfile.NamedTemporaryFile(suffix=".png") as image:
                    clean.save(image.name)
                    digest = hashlib.sha256(Path(image.name).read_bytes()).hexdigest()
                    parsed = self.extractor.payload_to_document(
                        self.extractor.predict(image.name, file_type=1), image.name
                    )
                candidates = [(p, b) for p in parsed.pages for b in p.blocks if numbered_heading(b.text)]
                diagnostic["readings"].append(
                    dict(scale=scale, input_sha256=digest, raw_texts=[b.text for p in parsed.pages for b in p.blocks])
                )
                if len(candidates) != 1:
                    continue
                crop, b = candidates[0]

                def convert(box):
                    return BBox(
                        x0=(clip.x0 + box.x0 / crop.width * clip.width) / sx,
                        y0=(clip.y0 + box.y0 / crop.height * clip.height) / sy,
                        x1=(clip.x0 + box.x1 / crop.width * clip.width) / sx,
                        y1=(clip.y0 + box.y1 / crop.height * clip.height) / sy,
                    )

                readings.append(
                    b.model_copy(
                        update={
                            "page_no": page.page_no,
                            "bbox": convert(b.bbox),
                            "char_boxes": [
                                c.model_copy(update={"page_no": page.page_no, "bbox": convert(c.bbox)})
                                for c in b.char_boxes
                            ],
                        }
                    )
                )
        return readings


def reconcile_heading_residuals(diffs, original, compare):
    """Clear only located fragments whose entire numbered source line is unchanged."""
    from app.services.diff.overlap_dedup import rebuild_snippet

    if original is None or compare is None:
        return diffs, []
    result, decisions = [], []
    for diff in diffs:
        if diff.source_type != "clause" or diff.section_type not in {"appendix", "safety_agreement"}:
            result.append(diff)
            continue
        removed = []
        for side, own, peer, direction in (
            ("original", original, compare, "DELETE"),
            ("compare", compare, original, "ADD"),
        ):
            ranges, evidences = getattr(diff, f"{side}_change_ranges"), getattr(diff, f"{side}_evidence")
            keep, removed_evidence = [], []
            for r in ranges:
                text = getattr(diff, f"{side}_text")[r.start : r.end]
                boxes = [
                    e
                    for e in evidences
                    if e.highlight_type == direction
                    and _NORMALIZER.normalize_for_match(e.text) == _NORMALIZER.normalize_for_match(text)
                ]
                owners = [
                    (p, b)
                    for p in own.pages
                    for b in p.blocks
                    if numbered_heading(b.text)
                    and (b.confidence or 0) >= 0.9
                    and any(
                        e.page_no == p.page_no
                        and b.bbox.x0 - 2 <= e.bbox.x0
                        and e.bbox.x1 <= b.bbox.x1 + 2
                        and b.bbox.y0 - 2 <= e.bbox.y0
                        and e.bbox.y1 <= b.bbox.y1 + 2
                        for e in boxes
                    )
                ]
                covered = False
                if r.highlight_type == direction and len(owners) == 1:
                    p, b = owners[0]
                    peers = _peers(peer, p, b, numbered_heading(b.text).title)
                    covered = (
                        len(peers) == 1
                        and _NORMALIZER.normalize_for_match(b.text) == _NORMALIZER.normalize_for_match(peers[0][1].text)
                        and _NORMALIZER.normalize_for_match(text) in _NORMALIZER.normalize_for_match(b.text)
                    )
                if covered:
                    removed.append(dict(side=side, text=text, start=r.start, end=r.end))
                    removed_evidence.extend(boxes)
                else:
                    keep.append(r)
            if removed_evidence:
                setattr(diff, f"{side}_change_ranges", keep)
                setattr(diff, f"{side}_evidence", [e for e in evidences if e not in removed_evidence])
                setattr(diff, f"{side}_snippet", rebuild_snippet(getattr(diff, f"{side}_text"), keep))
        if removed:
            decisions.append(dict(diff_id=diff.diff_id, fragments=removed))
            diff.readable_change = f"原文：{diff.original_snippet}\n修改后：{diff.compare_snippet}"
        if not removed or diff.original_change_ranges or diff.compare_change_ranges:
            result.append(diff)
    return result, decisions
