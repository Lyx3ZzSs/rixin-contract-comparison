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
                if self._capture(original_highlight_pdf, diff.original_snippet, diff.original_evidence[0], path):
                    diff.original_screenshot = str(path)
            if diff.compare_evidence:
                path = output_dir / f"{diff.diff_id}_compare.png"
                if self._capture(compare_highlight_pdf, diff.compare_snippet, diff.compare_evidence[0], path):
                    diff.compare_screenshot = str(path)
        return diffs

    def create_page_screenshots(
        self,
        pdf_path: str | Path,
        output_dir: str | Path,
        prefix: str,
        max_pages: int,
    ) -> list[str]:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        pdf = fitz.open(pdf_path)
        try:
            page_count = min(len(pdf), max_pages)
            for page_index in range(page_count):
                page = pdf[page_index]
                output_path = output_dir / f"{prefix}_page_{page_index + 1:03d}.png"
                pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
                pix.save(output_path)
                paths.append(str(output_path))
        finally:
            pdf.close()
        return paths

    def _capture(self, pdf_path: str | Path, snippet: str, evidence: EvidenceBox, output_path: Path) -> bool:
        pdf = fitz.open(pdf_path)
        try:
            if evidence.page_no < 1 or evidence.page_no > len(pdf):
                return False
            page = pdf[evidence.page_no - 1]
            rect = self._find_crop_rect(page, snippet, evidence, page.rect.width, page.rect.height)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, alpha=False)
            pix.save(output_path)
            return True
        finally:
            pdf.close()

    def _find_crop_rect(
        self,
        page: fitz.Page,
        snippet: str,
        evidence: EvidenceBox,
        page_width: float,
        page_height: float,
    ) -> fitz.Rect:
        if evidence.method in {"char_exact", "estimated_char"}:
            return self._bbox_rect(evidence.bbox, page_width, page_height)
        if snippet and snippet.strip():
            results = page.search_for(snippet.strip())
            if results:
                union = results[0]
                for r in results[1:]:
                    union |= r
                padded = union + (-20, -20, 20, 20)
                return fitz.Rect(
                    max(0, padded.x0),
                    max(0, padded.y0),
                    min(page_width, padded.x1),
                    min(page_height, padded.y1),
                )
        return self._bbox_rect(evidence.bbox, page_width, page_height)

    def _bbox_rect(self, bbox: BBox, width: float, height: float) -> fitz.Rect:
        expanded = bbox.expanded(40, width, height)
        rect = fitz.Rect(expanded.x0, expanded.y0, expanded.x1, expanded.y1)
        if rect.is_empty:
            y0 = max(0, bbox.y0 - 80)
            y1 = min(height, max(y0 + 120, bbox.y1 + 80))
            rect = fitz.Rect(0, y0, width, y1)
        return rect
