"""Recover untranscribed cover ink beside an otherwise matched printed line.

OCR detection proves a local text-like object, not a reliable transcription.
The paired printed line supplies geometry only; a blank counterpart is required.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import statistics
import tempfile

import fitz
import numpy as np

from app.models import BBox, DiffItem, EvidenceBox
from app.config import settings
from app.services.extractors.ppocrv5 import PPOCRV5Extractor


def _compact(value):
    return re.sub(r"\s+", "", value)


def _char_bounds(block):
    boxes = [c.bbox for c in block.char_boxes if c.char.strip()]
    if not boxes or len(boxes) < len(_compact(block.text)):
        return None
    return fitz.Rect(
        min(b.x0 for b in boxes), min(b.y0 for b in boxes), max(b.x1 for b in boxes), max(b.y1 for b in boxes)
    )


def _overlap(a, b):
    return (a & b).get_area() / max(1, a.get_area())


class CoverResidualOCR:
    def __init__(self, extractor=None):
        # Retain low-score readings for diagnostics, never display them as facts.
        self.extractor = extractor or PPOCRV5Extractor(
            settings.model_copy(update={"ppocrv5_text_rec_score_thresh": 0.0})
        )
        self.last_diagnostics = []

    def _candidates(self, own, peer):
        page, other = own.pages[0], peer.pages[0]
        for block in page.blocks:
            if (
                "ocr" not in block.source.lower()
                or block.block_type != "doc_title"
                or block.bbox.y1 > page.height * 0.5
                or not 6 <= len(_compact(block.text)) <= 80
                or (block.confidence or 0) < 0.9
            ):
                continue
            matches = [
                b
                for b in other.blocks
                if _compact(b.text) == _compact(block.text)
                and abs(b.bbox.y0 / other.height - block.bbox.y0 / page.height) < 0.08
            ]
            if len(matches) != 1:
                continue
            chars, matched = _char_bounds(block), _char_bounds(matches[0])
            if chars is None or matched is None or min(chars.width, chars.height, matched.width, matched.height) <= 0:
                continue
            char_width = statistics.median(c.bbox.x1 - c.bbox.x0 for c in block.char_boxes if c.char.strip())
            margin = max(3, char_width * 0.35)
            # A box/character mismatch is only a proposal. Other OCR objects
            # retain ownership, and both pixels and model detection must agree.
            for edge, roi in (
                ("right", fitz.Rect(chars.x1 + margin, block.bbox.y0, block.bbox.x1, block.bbox.y1)),
                ("left", fitz.Rect(block.bbox.x0, block.bbox.y0, chars.x0 - margin, block.bbox.y1)),
            ):
                if roi.is_empty or not max(12, char_width * 0.65) <= roi.width <= page.width * 0.2:
                    continue
                if any(
                    b is not block and _overlap(roi, fitz.Rect(b.bbox.x0, b.bbox.y0, b.bbox.x1, b.bbox.y1)) > 0.2
                    for b in page.blocks
                ):
                    continue
                sx, sy = matched.width / chars.width, matched.height / chars.height
                counterpart = fitz.Rect(
                    matched.x0 + (roi.x0 - chars.x0) * sx,
                    matched.y0 + (roi.y0 - chars.y0) * sy,
                    matched.x0 + (roi.x1 - chars.x0) * sx,
                    matched.y0 + (roi.y1 - chars.y0) * sy,
                )
                if not fitz.Rect(0, 0, other.width, other.height).contains(counterpart):
                    continue
                if any(
                    b is not matches[0]
                    and _overlap(counterpart, fitz.Rect(b.bbox.x0, b.bbox.y0, b.bbox.x1, b.bbox.y1)) > 0.1
                    for b in other.blocks
                ):
                    # Native text can be invisible to a local renderer when a
                    # PDF font is missing. Its known geometry still owns ink.
                    continue
                yield block, edge, roi, counterpart

    @staticmethod
    def _render(page, model, roi):
        clip = fitz.Rect(
            roi.x0 * page.rect.width / model.width,
            roi.y0 * page.rect.height / model.height,
            roi.x1 * page.rect.width / model.width,
            roi.y1 * page.rect.height / model.height,
        )
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=clip, colorspace=fitz.csRGB, alpha=False, annots=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).astype(np.int16)
        # Reject the coloured scanner background; this only verifies ink in a
        # model-proposed residual, it is not a page-wide handwriting detector.
        neutral = (rgb.max(axis=2) < 150) & ((rgb.max(axis=2) - rgb.min(axis=2)) < 65)
        return pix, neutral

    def build_diffs(self, original, compare):
        self.last_diagnostics = []
        diffs = []
        if not original.pages or not compare.pages:
            return diffs
        for side, own, peer in (("compare", compare, original), ("original", original, compare)):
            if not Path(own.path).is_file() or not Path(peer.path).is_file():
                continue
            candidates = list(self._candidates(own, peer))[:4]
            if not candidates:
                continue
            with fitz.open(own.path) as pdf, fitz.open(peer.path) as other:
                for block, edge, roi, counterpart in candidates:
                    diagnostic = dict(
                        side=side, block_id=block.block_id, roi=list(roi), counterpart_roi=list(counterpart)
                    )
                    self.last_diagnostics.append(diagnostic)
                    pix, ink = self._render(pdf[0], own.pages[0], roi)
                    for owned in own.pages[0].blocks:
                        boxes = [c.bbox for c in owned.char_boxes] or [owned.bbox]
                        if owned is block:
                            boxes = [c.bbox for c in owned.char_boxes]
                        for b in boxes:
                            r = fitz.Rect(b.x0, b.y0, b.x1, b.y1) & roi
                            if r.is_empty:
                                continue
                            x0 = max(0, int((r.x0 - roi.x0) / roi.width * pix.width) - 2)
                            y0 = max(0, int((r.y0 - roi.y0) / roi.height * pix.height) - 2)
                            x1 = min(pix.width, int((r.x1 - roi.x0) / roi.width * pix.width) + 3)
                            y1 = min(pix.height, int((r.y1 - roi.y0) / roi.height * pix.height) + 3)
                            ink[y0:y1, x0:x1] = False
                    _, peer_ink = self._render(other[0], peer.pages[0], counterpart)
                    count, peer_count = int(ink.sum()), int(peer_ink.sum())
                    density, peer_density = float(ink.mean()), float(peer_ink.mean())
                    diagnostic.update(
                        ink_pixels=count,
                        counterpart_ink_pixels=peer_count,
                        ink_density=density,
                        counterpart_density=peer_density,
                    )
                    if count < 90 or not 0.015 <= density <= 0.4 or peer_density > 0.003:
                        diagnostic["status"] = "NO_UNIQUE_INK"
                        continue
                    try:
                        with tempfile.NamedTemporaryFile(suffix=".png") as image:
                            pix.save(image.name)
                            payload = self.extractor.predict(image.name, file_type=1)
                        raw = payload[0] if len(payload) == 1 else {}
                        diagnostic["readings"] = list(zip(raw.get("rec_texts", []), raw.get("rec_scores", [])))
                        detected = []
                        for poly in raw.get("dt_polys", []):
                            r = fitz.Rect(
                                min(p[0] for p in poly),
                                min(p[1] for p in poly),
                                max(p[0] for p in poly),
                                max(p[1] for p in poly),
                            )
                            x0, y0 = max(0, int(r.x0)), max(0, int(r.y0))
                            x1, y1 = min(pix.width, int(r.x1)), min(pix.height, int(r.y1))
                            if x1 > x0 and y1 > y0 and int(ink[y0:y1, x0:x1].sum()) >= count * 0.7:
                                detected.append(r)
                        if len(detected) != 1:
                            diagnostic["status"] = "NO_UNIQUE_MODEL_DETECTION"
                            continue
                    except Exception as exc:
                        diagnostic.update(status="OCR_UNAVAILABLE", error=type(exc).__name__)
                        continue
                    # Tighten to supported ink, keeping the recognizer's guess
                    # separate from the existence and location of the change.
                    y, x = np.nonzero(ink)
                    box = BBox(
                        x0=roi.x0 + max(0, int(x.min()) - 3) / pix.width * roi.width,
                        y0=roi.y0 + max(0, int(y.min()) - 3) / pix.height * roi.height,
                        x1=roi.x0 + min(pix.width, int(x.max()) + 4) / pix.width * roi.width,
                        y1=roi.y0 + min(pix.height, int(y.max()) + 4) / pix.height * roi.height,
                    )
                    kind = "ADD" if side == "compare" else "DELETE"
                    text = "检测到封面额外笔迹（文字待识别）"
                    evidence = EvidenceBox(
                        page_no=own.pages[0].page_no,
                        bbox=box,
                        method="cover_residual_ocr",
                        text=text,
                        highlight_type=kind,
                        evidence_quality="HIGH",
                        confidence=0.9,
                    )
                    diagnostic.update(status="RECOVERED", bbox=box.model_dump())
                    identity = hashlib.sha256(f"{side}:{block.block_id}:{edge}".encode()).hexdigest()[:12]
                    diffs.append(
                        DiffItem(
                            diff_id=f"cover-ink-{identity}",
                            diff_type=kind,
                            source_type="metadata",
                            title="封面额外笔迹",
                            **{f"{side}_text": text, f"{side}_snippet": text, f"{side}_evidence": [evidence]},
                            readable_change="该处相对另一侧存在额外笔迹，具体文字需复核。",
                            quality_status="NEEDS_REVIEW",
                            content_recognition_status="NEEDS_REVIEW",
                            content_recognition_reason="COVER_INK_TEXT_UNREAD",
                            review_flags=["CONTENT_RECOGNITION_REVIEW"],
                            match_score_details={"recognition_scope": "ink_presence", "residual_evidence": diagnostic},
                            content_candidates=[
                                {"text": t, "confidence": s, "side": side} for t, s in diagnostic["readings"]
                            ],
                        )
                    )
        return diffs
