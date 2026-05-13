from __future__ import annotations

from pathlib import Path

import fitz

from app.models import BBox, DiffItem, EvidenceBox


class ScreenshotService:
    def create_screenshots(
        self,
        original_highlight_pdf: str | Path,
        compare_highlight_pdf: str | Path,
        diffs: list[DiffItem],
        output_dir: str | Path,
    ) -> list[DiffItem]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        for diff in diffs:
            if diff.original_evidence:
                path = output_dir / f"{diff.diff_id}_original.png"
                if self._capture(original_highlight_pdf, diff.original_evidence[0], path):
                    diff.original_screenshot = str(path)
            if diff.compare_evidence:
                path = output_dir / f"{diff.diff_id}_compare.png"
                if self._capture(compare_highlight_pdf, diff.compare_evidence[0], path):
                    diff.compare_screenshot = str(path)
        return diffs

    def _capture(self, pdf_path: str | Path, evidence: EvidenceBox, output_path: Path) -> bool:
        pdf = fitz.open(pdf_path)
        try:
            if evidence.page_no < 1 or evidence.page_no > len(pdf):
                return False
            page = pdf[evidence.page_no - 1]
            bbox = evidence.bbox.expanded(40, page.rect.width, page.rect.height)
            rect = fitz.Rect(bbox.x0, bbox.y0, bbox.x1, bbox.y1)
            if rect.is_empty:
                rect = self._fallback_rect(evidence.bbox, page.rect.width, page.rect.height)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, alpha=False)
            pix.save(output_path)
            return True
        finally:
            pdf.close()

    def _fallback_rect(self, bbox: BBox, width: float, height: float) -> fitz.Rect:
        y0 = max(0, bbox.y0 - 80)
        y1 = min(height, max(y0 + 120, bbox.y1 + 80))
        return fitz.Rect(0, y0, width, y1)

