from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from app.services.clause_items import ClauseItem


Marker = tuple[str, str]


@dataclass(frozen=True)
class HeadingCandidate:
    marker: str
    title: str
    level: int
    score: float
    signals: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()


class ClauseHeadingDetector:
    weak_numeric_marker_pattern = re.compile(r"^\d+$")

    def __init__(
        self,
        *,
        table_block_types: Collection[str],
        heading_business_terms: Collection[str],
        heading_accept_score: float,
        weak_heading_review_score: float,
        parse_marker: Callable[[str], Marker | None],
        title_from_text: Callable[[str], str],
        is_quantity_or_amount_marker: Callable[[str, Marker], bool],
        is_non_contract_numeric_marker: Callable[[Marker], bool],
        is_weak_numeric_continuation: Callable[[str, Marker], bool],
        is_formal_clause_marker: Callable[[str], bool],
        marker_level: Callable[[str], int],
        is_weak_numeric_marker: Callable[[str, Marker], bool],
        is_cover_noise_text: Callable[[str], bool],
    ) -> None:
        self.table_block_types = set(table_block_types)
        self.heading_business_terms = set(heading_business_terms)
        self.heading_accept_score = heading_accept_score
        self.weak_heading_review_score = weak_heading_review_score
        self.parse_marker = parse_marker
        self.title_from_text = title_from_text
        self.is_quantity_or_amount_marker = is_quantity_or_amount_marker
        self.is_non_contract_numeric_marker = is_non_contract_numeric_marker
        self.is_weak_numeric_continuation = is_weak_numeric_continuation
        self.is_formal_clause_marker = is_formal_clause_marker
        self.marker_level = marker_level
        self.is_weak_numeric_marker = is_weak_numeric_marker
        self.is_cover_noise_text = is_cover_noise_text

    def candidate(
        self,
        unit: Any,
        marker: Marker | None,
        current: ClauseItem | None,
    ) -> HeadingCandidate | None:
        text = str(getattr(unit, "text", "") or "")
        block_type = str(getattr(unit, "block_type", "") or "")
        if marker is None:
            if not self.is_unnumbered_section_title(unit, marker) or self.current_is_bare_marker(current):
                return None
            title = self.title_from_text(text)
            score = 0.74
            signals = ["paragraph_title", "unnumbered_title"]
            if self.has_business_term(title):
                score += 0.08
                signals.append("heading_business_term")
            return HeadingCandidate(
                marker="",
                title=title,
                level=1,
                score=min(0.95, score),
                signals=tuple(signals),
            )

        clause_no, title = marker
        score = 0.42
        signals: list[str] = ["marker"]
        risk_flags: list[str] = []

        if block_type in self.table_block_types:
            return None
        if self.is_quantity_or_amount_marker(text, marker):
            return None
        if self._should_suppress_non_contract_numeric_heading(unit, marker):
            return None
        if self.is_weak_numeric_continuation(text, marker):
            return None

        business_heading = self.has_business_term(title)
        if self.is_formal_clause_marker(clause_no):
            score += 0.34
            signals.append("formal_clause_marker")
        elif re.fullmatch(r"\d+(?:\.\d+)+", clause_no or ""):
            score += 0.34
            signals.append("decimal_marker")
        elif re.fullmatch(r"[一二三四五六七八九十]+", clause_no or ""):
            score += 0.30
            signals.append("chinese_list_marker")
        elif re.fullmatch(r"[（(][一二三四五六七八九十0-9]+[)）]", clause_no or ""):
            score += 0.20
            signals.append("parenthesized_marker")
        elif self.weak_numeric_marker_pattern.fullmatch(clause_no or ""):
            score += 0.16
            signals.append("single_numeric_marker")

        compact_title = re.sub(r"\s+", "", title or "")
        strong_title_block = (
            block_type in {"paragraph_title", "doc_title", "title"}
            and bool(self.weak_numeric_marker_pattern.fullmatch(clause_no or ""))
            and 2 <= len(compact_title) <= 12
        )
        if strong_title_block:
            signals.append("strong_title_block")
        if 2 <= len(compact_title) <= 36:
            score += 0.12
            signals.append("title_length")
        elif 36 < len(compact_title) <= 100 and re.fullmatch(r"\d+(?:\.\d+)+", clause_no or ""):
            score += 0.04
            signals.append("decimal_heading_with_body")
        elif not compact_title:
            score -= 0.14
            risk_flags.append("WEAK_HEADING")
        elif len(compact_title) > 80:
            score -= 0.18
            risk_flags.append("LONG_HEADING")

        if block_type in {"paragraph_title", "doc_title", "title"}:
            score += 0.08
            signals.append("title_block")
        if business_heading:
            score += 0.06
            signals.append("heading_business_term")
        if self.weak_numeric_marker_pattern.fullmatch(clause_no or "") and business_heading:
            score += 0.08
            signals.append("inline_numeric_business_heading")
        if re.search(r"[。；;]$", compact_title):
            score -= 0.10
            risk_flags.append("PUNCTUATED_HEADING")
        if self.is_date_like_heading(text, clause_no):
            score -= 0.30
            risk_flags.append("DATE_LIKE_HEADING")
        if self.is_weak_numeric_marker(text, marker) and not strong_title_block:
            score = min(score, self.weak_heading_review_score)
            risk_flags.append("WEAK_NUMERIC_MARKER")

        return HeadingCandidate(
            marker=clause_no,
            title=title,
            level=self.marker_level(clause_no),
            score=max(0.0, min(1.0, score)),
            signals=tuple(dict.fromkeys(signals)),
            risk_flags=tuple(dict.fromkeys(risk_flags)),
        )

    @staticmethod
    def reason(candidate: HeadingCandidate | None, fallback: str) -> str:
        if candidate is None:
            return fallback
        marker = f"marker:{candidate.marker}" if candidate.marker else "unnumbered_heading"
        signals = ",".join(candidate.signals)
        reason = f"{marker}|heading_score:{candidate.score:.2f}"
        if signals:
            reason += f"|signals:{signals}"
        if candidate.risk_flags:
            reason += f"|risks:{','.join(candidate.risk_flags)}"
        return reason

    def has_business_term(self, title: str) -> bool:
        compact = re.sub(r"\s+", "", title or "")
        return any(term in compact for term in self.heading_business_terms)

    @staticmethod
    def is_date_like_heading(text: str, clause_no: str) -> bool:
        first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        compact = re.sub(r"\s+", "", first_line)
        return bool(
            re.fullmatch(r"(?:19|20)\d{2}[./年-]\d{1,2}(?:[./月-]\d{1,2}日?)?", compact)
            or re.fullmatch(r"(?:19|20)\d{2}(?:\.\d{1,2}){1,2}", clause_no or "")
        )

    def is_unnumbered_section_title(self, unit: Any, marker: Marker | None) -> bool:
        if marker is not None or getattr(unit, "block_type", "") != "paragraph_title":
            return False
        compact = re.sub(r"\s+", "", str(getattr(unit, "text", "") or ""))
        cross_page_title_boundary = "CROSS_PAGE_TITLE_BOUNDARY" in getattr(unit, "split_flags", ())
        if not compact:
            return False
        if len(compact) < 4 and not cross_page_title_boundary:
            return False
        if self.is_cover_noise_text(compact) or self.is_attachment_title(compact):
            return False
        return not bool(re.search(r"[:：。；;，,]$", compact))

    def current_is_bare_marker(self, current: ClauseItem | None) -> bool:
        if current is None or len(current.texts) != 1:
            return False
        marker = self.parse_marker(str(current.texts[0]).strip())
        if marker is None:
            return False
        _, title = marker
        return not title

    @staticmethod
    def is_attachment_title(compact: str) -> bool:
        return bool(re.fullmatch(r"附件[一二三四五六七八九十0-9]+.*", compact))

    def _should_suppress_non_contract_numeric_heading(
        self,
        unit: Any,
        marker: Marker,
    ) -> bool:
        section_type = str(getattr(unit, "section_type", "main_contract") or "main_contract")
        if section_type == "main_contract":
            return False
        if not self.is_non_contract_numeric_marker(marker):
            return False
        return not self._allow_numbered_non_contract_heading(unit, marker)

    @staticmethod
    def _allow_numbered_non_contract_heading(unit: Any, marker: Marker) -> bool:
        section_type = str(getattr(unit, "section_type", "main_contract") or "main_contract")
        if section_type not in {"appendix", "safety_agreement"}:
            return False
        block_type = str(getattr(unit, "block_type", "") or "")
        if block_type not in {"paragraph_title", "doc_title", "title"}:
            return False
        _, title = marker
        compact_title = re.sub(r"\s+", "", title or "")
        return len(compact_title) >= 4
