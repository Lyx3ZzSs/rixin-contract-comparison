from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path
import re
import tempfile

import fitz

from app.models import Document, TextSourceEvidence
from app.services.extractors.ppocrv5 import PPOCRV5Extractor

_DATE = re.compile(r"(?P<year>\d{3,4})年(?P<month>\d{1,2})月(?:(?P<day>\d{1,2})日?)?")


def date_precision(text: str) -> str | None:
    match = _DATE.fullmatch(re.sub(r"\s+", "", text))
    if not match:
        return None
    year, month, day = int(match["year"]), int(match["month"]), int(match["day"] or 1)
    try:
        date(year, month, day)
    except ValueError:
        return None
    if len(match["year"]) != 4:
        return None
    return "day" if match["day"] else "month"


class CoverDateOCR:
    """Re-read anomalous standalone cover dates from this side's PDF only."""

    def __init__(self, extractor=None):
        self.extractor = extractor or PPOCRV5Extractor()

    method = "COVER_DATE_LOCAL_OCR"
    unresolved = "UNRESOLVED_COVER_DATE"
    consistent = "CONSISTENT_LOCAL_COVER_DATE"
    source_prefix = "cover_date_local"
    precision = staticmethod(date_precision)

    def _candidates(self, document: Document):
        from .extractor import _cover_blocks

        cover_blocks = _cover_blocks(document)
        first_page = min((b.page_no for b in cover_blocks), default=1)
        return [
            b
            for b in cover_blocks
            if b.page_no == first_page
            and _DATE.fullmatch(re.sub(r"\s+", "", b.text))
            and (
                date_precision(b.text) is None
                or (
                    "ocr" in b.source.lower()
                    and (
                        b.confidence is None
                        or b.confidence < 0.98
                        or bool(re.search(r"月\d日?$", re.sub(r"\s+", "", b.text)))
                    )
                )
            )
        ][:2]

    def enrich(self, document: Document) -> list[dict]:
        blocks = self._candidates(document)
        if not blocks:
            return []
        diagnostics = []
        for block in blocks:
            diagnostic = {
                "page": block.page_no,
                "block_id": block.block_id,
                "before": block.text,
                "method": self.method,
                "status": "NEEDS_REVIEW",
                "readings": [],
            }
            diagnostics.append(diagnostic)
            block.semantic_reasons.append(self.unresolved)
            if not Path(document.path).is_file():
                diagnostic["error"] = "PDF_UNAVAILABLE"
                continue
            try:
                with fitz.open(document.path) as pdf:
                    page = pdf[block.page_no - 1]
                    model_page = next(p for p in document.pages if p.page_no == block.page_no)
                    sx, sy = page.rect.width / model_page.width, page.rect.height / model_page.height
                    clip = (
                        fitz.Rect(
                            (block.bbox.x0 - 10) * sx,
                            (block.bbox.y0 - 10) * sy,
                            (block.bbox.x1 + 10) * sx,
                            (block.bbox.y1 + 10) * sy,
                        )
                        & page.rect
                    )
                    for scale in (3.0, 4.0, 5.0):
                        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False, annots=False)
                        with tempfile.NamedTemporaryFile(suffix=".png") as image:
                            pix.save(image.name)
                            digest = hashlib.sha256(Path(image.name).read_bytes()).hexdigest()
                            parsed = self.extractor.payload_to_document(
                                self.extractor.predict(image.name, file_type=1), image.name
                            )
                        found = [b for p in parsed.pages for b in p.blocks if self.precision(b.text)]
                        reading = {
                            "scale": scale,
                            "input_sha256": digest,
                            "text": "",
                            "confidence": None,
                            "raw_readings": [
                                {"text": b.text, "confidence": b.confidence} for p in parsed.pages for b in p.blocks
                            ],
                        }
                        if len(found) == 1:
                            reading.update(text=re.sub(r"\s+", "", found[0].text), confidence=found[0].confidence)
                        diagnostic["readings"].append(reading)
                readings = [
                    r
                    for r in diagnostic["readings"]
                    if r["text"] and r["confidence"] is not None and r["confidence"] >= 0.85
                ]
                valid_values = {r["text"] for r in diagnostic["readings"] if r["text"]}
                if len(readings) >= 2 and len(valid_values) == 1:
                    # Keep the full original ROI: character boxes refer to the old reading.
                    if not block.source_evidence:
                        block.source_evidence.append(
                            TextSourceEvidence(
                                source=block.source or "initial_ocr",
                                raw_text=block.text,
                                page_no=block.page_no,
                                bbox=block.bbox,
                                confidence=block.confidence,
                            )
                        )
                    block.text = readings[0]["text"]
                    block.char_boxes = []
                    block.confidence = min(r["confidence"] for r in readings)
                    for reading in readings:
                        block.source_evidence.append(
                            TextSourceEvidence(
                                source=f"{self.source_prefix}_{reading['scale']}",
                                raw_text=reading["text"],
                                page_no=block.page_no,
                                bbox=block.bbox,
                                confidence=reading["confidence"],
                            )
                        )
                    block.semantic_reasons.remove(self.unresolved)
                    block.semantic_reasons.append(self.consistent)
                    diagnostic.update(status="RECOVERED", after=block.text, precision=self.precision(block.text))
            except Exception as exc:
                diagnostic["error"] = type(exc).__name__
        return diagnostics


class CoverIdentifierOCR(CoverDateOCR):
    """Bounded rereading of OCR contract identifiers; never consults the other PDF."""

    method = "COVER_IDENTIFIER_LOCAL_OCR"
    unresolved = "UNRESOLVED_COVER_IDENTIFIER"
    consistent = "CONSISTENT_LOCAL_COVER_IDENTIFIER"
    source_prefix = "cover_identifier_local"

    @staticmethod
    def precision(text: str) -> str | None:
        value = re.sub(r"\s+", "", text)
        return (
            "identifier" if re.fullmatch(r"[A-Za-z(（][A-Za-z0-9/()（）.-]{1,30}-\d{6,8}-\d{3,10}[.]?", value) else None
        )

    def _candidates(self, document: Document):
        return [
            block
            for page in document.pages[:1]
            for block in page.blocks
            if "ocr" in block.source.lower() and self.precision(block.text) and block.bbox.y1 <= page.height * 0.45
        ][:2]
