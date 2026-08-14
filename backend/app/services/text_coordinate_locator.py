from __future__ import annotations

from pathlib import Path
import unicodedata

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
        precise_methods = {
            "char_exact",
            "table_cell",
            "cover_metadata",
            "header_footer",
            "seal_region",
            "signing_region",
            "page_region",
            "text_exact",
        }
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
        if len(existing) > 1:
            segment_queries = [" ".join((evidence.text or "").split()) for evidence in existing]
            if all(segment_queries) and any(
                self._compact_text(segment_query) != self._compact_text(query) for segment_query in segment_queries
            ):
                segmented: list[EvidenceBox] = []
                for segment_query, evidence in zip(segment_queries, existing, strict=True):
                    segmented.extend(self._locate_snippet(pdf, segment_query, [evidence], highlight_type) or [evidence])
                return segmented
        preferred_pages = sorted({evidence.page_no for evidence in existing if evidence.page_no})
        page_numbers = preferred_pages + [
            page_no for page_no in range(1, len(pdf) + 1) if page_no not in set(preferred_pages)
        ]
        candidates: list[EvidenceBox] = []
        for page_no in page_numbers:
            if page_no < 1 or page_no > len(pdf):
                continue
            page = pdf[page_no - 1]
            for rect in page.search_for(query):
                if rect.is_empty or rect.is_infinite:
                    continue
                if not self._plausible_text_extent(query, rect):
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
            candidates.extend(self._word_sequence_candidates(page, query, page_no, highlight_type))
            if candidates:
                break
        if not candidates:
            return []
        return [self._best_candidate(candidates, existing)]

    def _word_sequence_candidates(
        self,
        page: fitz.Page,
        query: str,
        page_no: int,
        highlight_type: str,
    ) -> list[EvidenceBox]:
        """Locate labels whose glyph spacing prevents PyMuPDF phrase search."""
        target = self._compact_text(query)
        if not target:
            return []
        words = page.get_text("words", sort=True)
        candidates: list[EvidenceBox] = []
        for start, word in enumerate(words):
            text = self._compact_text(str(word[4]))
            if not text or not target.startswith(text):
                continue
            matched = text
            rect = fitz.Rect(word[:4])
            if matched == target:
                candidates.append(
                    EvidenceBox(
                        page_no=page_no,
                        bbox=BBox(
                            x0=float(rect.x0),
                            y0=float(rect.y0),
                            x1=float(rect.x1),
                            y1=float(rect.y1),
                        ),
                        method="text_exact",
                        text=query,
                        highlight_type=highlight_type,
                    )
                )
                continue
            for next_word in words[start + 1 :]:
                next_rect = fitz.Rect(next_word[:4])
                if not self._same_visual_line(rect, next_rect):
                    break
                next_text = self._compact_text(str(next_word[4]))
                if not next_text or not target.startswith(matched + next_text):
                    break
                matched += next_text
                rect.include_rect(next_rect)
                if matched == target:
                    candidates.append(
                        EvidenceBox(
                            page_no=page_no,
                            bbox=BBox(
                                x0=float(rect.x0),
                                y0=float(rect.y0),
                                x1=float(rect.x1),
                                y1=float(rect.y1),
                            ),
                            method="text_exact",
                            text=query,
                            highlight_type=highlight_type,
                        )
                    )
                    break
        return candidates

    @staticmethod
    def _same_visual_line(left: fitz.Rect, right: fitz.Rect) -> bool:
        left_midpoint = (left.y0 + left.y1) / 2
        right_midpoint = (right.y0 + right.y1) / 2
        tolerance = max(left.height, right.height) * 0.4
        return abs(left_midpoint - right_midpoint) <= tolerance and right.x0 >= left.x0

    @staticmethod
    def _compact_text(text: str) -> str:
        return "".join(unicodedata.normalize("NFKC", text).split())

    @staticmethod
    def _plausible_text_extent(query: str, rect: fitz.Rect) -> bool:
        cjk_count = sum("\u4e00" <= char <= "\u9fff" for char in query)
        if cjk_count < 2:
            return True
        height = max(1.0, float(rect.y1 - rect.y0))
        return float(rect.x1 - rect.x0) >= height * cjk_count * 0.4

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
