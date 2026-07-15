from __future__ import annotations

import re
import tempfile
import unicodedata
from pathlib import Path

import fitz

from app.models import BBox, DiffItem
from app.services.extractors.ppocrv5 import PPOCRV5Extractor


class CounterpartTextRecovery:
    """Re-read a counterpart page crop when full-page OCR omitted a clause fragment."""

    def __init__(self, extractor: PPOCRV5Extractor | None = None) -> None:
        self.extractor = extractor or PPOCRV5Extractor()

    def recover(
        self,
        *,
        diff: DiffItem,
        side: str | None,
        page_no: int | None,
        original_pdf: str | Path,
        compare_pdf: str | Path,
    ) -> str:
        if side not in {"original", "compare"} or page_no is None:
            return ""
        expected, anchors = self._expected_and_anchors(diff, side, page_no)
        if not expected or not anchors:
            return ""

        source_pdf = Path(compare_pdf if side == "original" else original_pdf)
        target_pdf = Path(original_pdf if side == "original" else compare_pdf)
        try:
            with fitz.open(source_pdf) as source, fitz.open(target_pdf) as target:
                if page_no > len(source) or page_no > len(target):
                    return ""
                source_page = source[page_no - 1]
                target_page = target[page_no - 1]
                clip = self._target_clip(anchors, source_page.rect, target_page.rect)
                pixmap = target_page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False)
                with tempfile.NamedTemporaryFile(suffix=".png") as image:
                    pixmap.save(image.name)
                    payload = self.extractor.predict(image.name, file_type=1)
                    document = self.extractor.payload_to_document(payload, image.name)
        except Exception:
            return ""

        recovered = "\n".join(block.text for page in document.pages for block in page.blocks if block.text.strip())
        return recovered if self._compact(expected) in self._compact(recovered) else ""

    @staticmethod
    def _expected_and_anchors(diff: DiffItem, side: str, page_no: int) -> tuple[str, list[BBox]]:
        if side == "compare":
            return diff.original_snippet or diff.original_text, [
                item.bbox for item in diff.original_evidence if item.page_no == page_no
            ]
        return diff.compare_snippet or diff.compare_text, [
            item.bbox for item in diff.compare_evidence if item.page_no == page_no
        ]

    @staticmethod
    def _target_clip(anchors: list[BBox], source_rect: fitz.Rect, target_rect: fitz.Rect) -> fitz.Rect:
        x0 = min(item.x0 for item in anchors)
        y0 = min(item.y0 for item in anchors)
        x1 = max(item.x1 for item in anchors)
        y1 = max(item.y1 for item in anchors)
        scale_x = target_rect.width / source_rect.width
        scale_y = target_rect.height / source_rect.height
        padding = 28.0
        return fitz.Rect(
            max(target_rect.x0, target_rect.x0 + x0 * scale_x - padding),
            max(target_rect.y0, target_rect.y0 + y0 * scale_y - padding),
            min(target_rect.x1, target_rect.x0 + x1 * scale_x + padding),
            min(target_rect.y1, target_rect.y0 + y1 * scale_y + padding),
        )

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
