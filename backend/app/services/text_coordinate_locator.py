from __future__ import annotations

from pathlib import Path

import fitz

from app.models import BBox, DiffItem, EvidenceBox


class TextCoordinateLocator:
    """Refine OCR evidence with native PDF text coordinates when available."""

    def refine(
        self,
        original_pdf: str | Path,
        compare_pdf: str | Path,
        diffs: list[DiffItem],
    ) -> list[DiffItem]:
        self._refine_side(Path(original_pdf), diffs, side="original")
        self._refine_side(Path(compare_pdf), diffs, side="compare")
        return diffs

    def _refine_side(self, pdf_path: Path, diffs: list[DiffItem], side: str) -> None:
        if not pdf_path.exists():
            return
        try:
            pdf = fitz.open(pdf_path)
        except Exception:
            return
        try:
            for diff in diffs:
                snippet = diff.original_snippet if side == "original" else diff.compare_snippet
                highlight_type = self._side_highlight_type(diff, side)
                existing = diff.original_evidence if side == "original" else diff.compare_evidence
                if not self._should_refine(existing):
                    continue
                refined = self._locate_snippet(pdf, snippet, existing, highlight_type)
                if not refined:
                    continue
                if side == "original":
                    diff.original_evidence = refined
                else:
                    diff.compare_evidence = refined
        finally:
            pdf.close()

    def _side_highlight_type(self, diff: DiffItem, side: str) -> str:
        if diff.diff_type == "MODIFY":
            return "MODIFY"
        if side == "original":
            return "DELETE"
        return "ADD"

    def _should_refine(self, existing: list[EvidenceBox]) -> bool:
        if not existing:
            return True
        precise_methods = {"char_exact", "table_cell", "cover_metadata", "text_exact"}
        return not any((evidence.method or "").lower() in precise_methods for evidence in existing)

    def _locate_snippet(
        self,
        pdf: fitz.Document,
        snippet: str,
        existing: list[EvidenceBox],
        highlight_type: str,
    ) -> list[EvidenceBox]:
        query = " ".join((snippet or "").split())
        if not query:
            return []
        preferred_pages = sorted({evidence.page_no for evidence in existing if evidence.page_no})
        page_numbers = preferred_pages + [page_no for page_no in range(1, len(pdf) + 1) if page_no not in set(preferred_pages)]
        candidates: list[EvidenceBox] = []
        for page_no in page_numbers:
            if page_no < 1 or page_no > len(pdf):
                continue
            page = pdf[page_no - 1]
            for rect in page.search_for(query):
                if rect.is_empty or rect.is_infinite:
                    continue
                candidates.append(
                    EvidenceBox(
                        page_no=page_no,
                        bbox=BBox(x0=float(rect.x0), y0=float(rect.y0), x1=float(rect.x1), y1=float(rect.y1)),
                        method="text_exact",
                        text=query,
                        highlight_type=highlight_type,
                    )
                )
            if candidates:
                break
        if not candidates:
            return []
        return [self._best_candidate(candidates, existing)]

    def _best_candidate(self, candidates: list[EvidenceBox], existing: list[EvidenceBox]) -> EvidenceBox:
        if not existing:
            return candidates[0]
        return max(candidates, key=lambda candidate: self._candidate_score(candidate, existing))

    def _candidate_score(self, candidate: EvidenceBox, existing: list[EvidenceBox]) -> float:
        same_page = [evidence for evidence in existing if evidence.page_no == candidate.page_no]
        if not same_page:
            return 0.0
        return max(self._overlap(candidate.bbox, evidence.bbox) for evidence in same_page)

    def _overlap(self, left: BBox, right: BBox) -> float:
        width = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
        height = max(0.0, min(left.y1, right.y1) - max(left.y0, right.y0))
        return width * height
