from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz

from app.models import BBox, Document, Page


@dataclass(frozen=True)
class RedPixelMetrics:
    red_pixels: int = 0
    total_pixels: int = 0

    @property
    def ratio(self) -> float:
        return self.red_pixels / self.total_pixels if self.total_pixels else 0.0


class RedSealVisualInspector:
    """Verifies red seal pixels in a PDF region without relying on OCR labels."""

    def __init__(self, *, scale: float = 2.0) -> None:
        self.scale = scale

    def inspect(
        self,
        document: Document,
        page_no: int,
        bbox: BBox,
        *,
        padding: float = 0.0,
    ) -> RedPixelMetrics:
        page = self._page(document, page_no)
        pdf_path = Path(document.path)
        if page is None or not pdf_path.is_file():
            return RedPixelMetrics()

        pdf: fitz.Document | None = None
        try:
            pdf = fitz.open(pdf_path)
            if page_no < 1 or page_no > len(pdf):
                return RedPixelMetrics()
            pdf_page = pdf[page_no - 1]
            clip = self._pdf_bbox(bbox, page, pdf_page.rect, padding)
            if clip.is_empty or clip.is_infinite:
                return RedPixelMetrics()
            pixmap = pdf_page.get_pixmap(
                matrix=fitz.Matrix(self.scale, self.scale),
                clip=clip,
                colorspace=fitz.csRGB,
                alpha=False,
            )
            return self._metrics(pixmap)
        except Exception:
            return RedPixelMetrics()
        finally:
            if pdf is not None:
                pdf.close()

    @staticmethod
    def normalized_bbox(bbox: BBox, source_page: Page, target_page: Page) -> BBox:
        if source_page.width <= 0 or source_page.height <= 0:
            return bbox
        return BBox(
            x0=bbox.x0 / source_page.width * target_page.width,
            y0=bbox.y0 / source_page.height * target_page.height,
            x1=bbox.x1 / source_page.width * target_page.width,
            y1=bbox.y1 / source_page.height * target_page.height,
        )

    @staticmethod
    def _page(document: Document, page_no: int) -> Page | None:
        return next((page for page in document.pages if page.page_no == page_no), None)

    @staticmethod
    def _pdf_bbox(bbox: BBox, page: Page, pdf_rect: fitz.Rect, padding: float) -> fitz.Rect:
        x_scale = pdf_rect.width / page.width if page.width > 0 else 1.0
        y_scale = pdf_rect.height / page.height if page.height > 0 else 1.0
        expanded = BBox(
            x0=max(0.0, bbox.x0 - padding),
            y0=max(0.0, bbox.y0 - padding),
            x1=min(page.width, bbox.x1 + padding),
            y1=min(page.height, bbox.y1 + padding),
        )
        return (
            fitz.Rect(
                pdf_rect.x0 + expanded.x0 * x_scale,
                pdf_rect.y0 + expanded.y0 * y_scale,
                pdf_rect.x0 + expanded.x1 * x_scale,
                pdf_rect.y0 + expanded.y1 * y_scale,
            )
            & pdf_rect
        )

    @staticmethod
    def _metrics(pixmap: fitz.Pixmap) -> RedPixelMetrics:
        if pixmap.width <= 0 or pixmap.height <= 0 or pixmap.n < 3:
            return RedPixelMetrics()
        samples = pixmap.samples
        red_pixels = 0
        for index in range(0, len(samples), pixmap.n):
            red, green, blue = samples[index : index + 3]
            if red >= 100 and red >= green * 1.25 and red >= blue * 1.25 and red - min(green, blue) >= 25:
                red_pixels += 1
        return RedPixelMetrics(
            red_pixels=red_pixels,
            total_pixels=pixmap.width * pixmap.height,
        )
