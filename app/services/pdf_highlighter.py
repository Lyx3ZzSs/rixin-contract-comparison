from __future__ import annotations

from pathlib import Path

import fitz

from app.models import DiffItem, EvidenceBox


class PdfHighlighter:
    original_colors = {
        "DELETE": (1, 0.25, 0.25),
        "MODIFY": (1, 0.85, 0.1),
    }
    compare_colors = {
        "ADD": (0.25, 0.8, 0.35),
        "MODIFY": (1, 0.85, 0.1),
    }

    def highlight_original(self, pdf_path: str | Path, diffs: list[DiffItem], output_path: str | Path) -> Path:
        return self._highlight(pdf_path, diffs, output_path, side="original")

    def highlight_compare(self, pdf_path: str | Path, diffs: list[DiffItem], output_path: str | Path) -> Path:
        return self._highlight(pdf_path, diffs, output_path, side="compare")

    def _highlight(self, pdf_path: str | Path, diffs: list[DiffItem], output_path: str | Path, side: str) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pdf = fitz.open(pdf_path)
        try:
            for diff in diffs:
                analysis = diff.ai_analysis
                risk = analysis.risk_level if analysis else "LOW"
                summary = analysis.change_summary if analysis else diff.readable_change[:120]
                if side == "original":
                    evidences = diff.original_evidence
                    color = self.original_colors.get(diff.diff_type)
                else:
                    evidences = diff.compare_evidence
                    color = self.compare_colors.get(diff.diff_type)
                if not color:
                    continue
                for evidence in evidences:
                    self._add_annotation(pdf, evidence, color, diff.diff_id, risk, summary)
            pdf.save(output_path)
        finally:
            pdf.close()
        return output_path

    def _add_annotation(
        self,
        pdf: fitz.Document,
        evidence: EvidenceBox,
        color: tuple[float, float, float],
        diff_id: str,
        risk_level: str,
        summary: str,
    ) -> None:
        if evidence.page_no < 1 or evidence.page_no > len(pdf):
            return
        page = pdf[evidence.page_no - 1]
        rect = fitz.Rect(evidence.bbox.x0, evidence.bbox.y0, evidence.bbox.x1, evidence.bbox.y1)
        if rect.is_empty or rect.is_infinite:
            return
        annot = page.add_highlight_annot(rect)
        annot.set_colors(stroke=color)
        annot.set_opacity(0.35)
        annot.set_info(content=f"{diff_id} | {risk_level} | {summary[:200]}")
        annot.update()
        if risk_level == "HIGH":
            box = page.add_rect_annot(rect)
            box.set_colors(stroke=(1, 0, 0))
            box.set_border(width=1.2)
            box.set_opacity(0.9)
            box.update()

