from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import fitz

from app.models import BBox, DiffItem, EvidenceBox

RelocationStatus = Literal["SUCCEEDED", "FAILED", "SKIPPED"]
RelocationSide = Literal["original", "compare"]

QualitySnapshot = dict[str, int | float | list[str] | list[int]]

HIGH_CONFIDENCE_THRESHOLD = 0.85
RELOCATED_CONFIDENCE = 0.98
MAX_RELOCATED_EVIDENCE = 3
LOW_INFORMATION_SYMBOL_PATTERN = re.compile(r"^[/\\∠_.,，。·•\-—~～…\s|]+$")


@dataclass(frozen=True)
class EvidenceRelocationResult:
    status: RelocationStatus
    reason: str
    evidence: list[EvidenceBox] = field(default_factory=list)
    changed_evidence: bool = False
    before_quality: QualitySnapshot = field(default_factory=dict)
    after_quality: QualitySnapshot = field(default_factory=dict)


class EvidenceRelocator:
    def relocate(
        self,
        diff: DiffItem,
        *,
        side: RelocationSide,
        page_no: int | None,
        original_pdf: str | Path,
        compare_pdf: str | Path,
    ) -> EvidenceRelocationResult:
        original_pdf = Path(original_pdf)
        compare_pdf = Path(compare_pdf)
        current_evidence = self._side_evidence(diff, side)
        before_quality = self._quality_snapshot(current_evidence)

        if self._has_high_confidence_evidence(current_evidence):
            return EvidenceRelocationResult(
                status="SKIPPED",
                reason="EXISTING_EVIDENCE_HIGH_CONFIDENCE",
                before_quality=before_quality,
                after_quality=dict(before_quality),
            )

        query = self._side_query(diff, side)
        if not query:
            return EvidenceRelocationResult(
                status="FAILED",
                reason="NO_SIDE_TEXT_SIGNAL",
                before_quality=before_quality,
                after_quality=dict(before_quality),
            )
        if self._is_low_information_query(query) and not self._has_existing_page_anchor(current_evidence, page_no):
            return EvidenceRelocationResult(
                status="FAILED",
                reason="LOW_INFORMATION_QUERY_WITHOUT_PAGE_ANCHOR",
                before_quality=before_quality,
                after_quality=dict(before_quality),
            )

        pdf_path = original_pdf if side == "original" else compare_pdf
        current_max_confidence = self._max_confidence(current_evidence)
        relocated = [
            evidence
            for evidence in self._search_pdf(pdf_path, query, page_no, diff.diff_type, side)
            if evidence.confidence > current_max_confidence
        ][:MAX_RELOCATED_EVIDENCE]

        if not relocated:
            return EvidenceRelocationResult(
                status="FAILED",
                reason="NO_ACCEPTED_CANDIDATE",
                before_quality=before_quality,
                after_quality=dict(before_quality),
            )

        return EvidenceRelocationResult(
            status="SUCCEEDED",
            reason="EVIDENCE_RELOCATED",
            evidence=relocated,
            changed_evidence=True,
            before_quality=before_quality,
            after_quality=self._quality_snapshot(relocated),
        )

    def _side_evidence(self, diff: DiffItem, side: RelocationSide) -> list[EvidenceBox]:
        if side == "original":
            return diff.original_evidence
        return diff.compare_evidence

    def _side_query(self, diff: DiffItem, side: RelocationSide) -> str:
        if side == "original":
            text = diff.original_snippet or diff.original_text
        else:
            text = diff.compare_snippet or diff.compare_text
        return self._normalize_query(text)

    def _search_pdf(
        self,
        pdf_path: Path,
        query: str,
        page_no: int | None,
        diff_type: str,
        side: RelocationSide,
    ) -> list[EvidenceBox]:
        doc = fitz.open(pdf_path)
        try:
            pages = self._pages_to_search(doc, page_no)
            evidences: list[EvidenceBox] = []
            for page_index in pages:
                page = doc[page_index]
                for rect in page.search_for(query):
                    evidences.append(
                        EvidenceBox(
                            page_no=page_index + 1,
                            bbox=BBox(
                                x0=float(rect.x0),
                                y0=float(rect.y0),
                                x1=float(rect.x1),
                                y1=float(rect.y1),
                            ),
                            method="text_exact",
                            text=query,
                            highlight_type=self._highlight_type(diff_type, side),
                            confidence=RELOCATED_CONFIDENCE,
                            evidence_quality="HIGH",
                        )
                    )
            return evidences
        finally:
            doc.close()

    def _pages_to_search(self, doc: fitz.Document, page_no: int | None) -> list[int]:
        if page_no is None:
            return list(range(doc.page_count))
        page_index = page_no - 1
        if page_index < 0 or page_index >= doc.page_count:
            return []
        return [page_index]

    @staticmethod
    def _has_high_confidence_evidence(evidences: list[EvidenceBox]) -> bool:
        return any(
            evidence.confidence >= HIGH_CONFIDENCE_THRESHOLD or evidence.evidence_quality == "HIGH"
            for evidence in evidences
        )

    @staticmethod
    def _highlight_type(diff_type: str, side: RelocationSide) -> str:
        if diff_type == "MODIFY":
            return "MODIFY"
        if side == "original":
            return "DELETE"
        return "ADD"

    @staticmethod
    def _normalize_query(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _is_low_information_query(query: str) -> bool:
        compact = re.sub(r"\s+", "", query or "")
        return 0 < len(compact) <= 2 and bool(LOW_INFORMATION_SYMBOL_PATTERN.fullmatch(compact))

    @staticmethod
    def _has_existing_page_anchor(evidences: list[EvidenceBox], page_no: int | None) -> bool:
        if page_no is None:
            return False
        return any(evidence.page_no == page_no for evidence in evidences)

    @staticmethod
    def _quality_snapshot(evidences: list[EvidenceBox]) -> QualitySnapshot:
        if not evidences:
            return {
                "evidence_count": 0,
                "max_confidence": 0.0,
                "methods": [],
                "pages": [],
            }

        return {
            "evidence_count": len(evidences),
            "max_confidence": EvidenceRelocator._max_confidence(evidences),
            "methods": sorted({evidence.method for evidence in evidences}),
            "pages": sorted({evidence.page_no for evidence in evidences}),
        }

    @staticmethod
    def _max_confidence(evidences: list[EvidenceBox]) -> float:
        if not evidences:
            return 0.0
        return max(evidence.confidence for evidence in evidences)
