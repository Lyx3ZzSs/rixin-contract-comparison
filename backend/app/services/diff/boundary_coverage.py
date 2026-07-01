from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.models import Clause, DiffItem, Document

STRUCTURAL_RISK_FLAGS = {
    "POSSIBLE_SPLIT_DRIFT",
    "LOW_CONFIDENCE_MATCH",
    "LOW_COVERAGE_CLAUSE_KEY_MATCH",
}

_APPENDIX_HEADING_PATTERN = re.compile(r"^\s*(附件\s*[一二三四五六七八九十百\d]+)\s*[:：.．、]?\s*$")
_APPENDIX_HEADING_LINE_PATTERN = re.compile(r"^\s*(附件\s*[一二三四五六七八九十百\d]+)\s*(?:[:：.．、]|$)")
_COMPACT_PUNCTUATION_PATTERN = re.compile(r"[\s，。；：、”“‘’（）()\[\]【】《》!?:;\"'.,．]+")


@dataclass
class BoundaryCoverageContext:
    original_clauses: list[Clause] = field(default_factory=list)
    compare_clauses: list[Clause] = field(default_factory=list)
    original_document: Document | None = None
    compare_document: Document | None = None


@dataclass
class BoundaryCoverageDecision:
    action: str
    diff_id: str
    detail: dict[str, Any] = field(default_factory=dict)


class ClauseBoundaryCoverageFilter:
    def filter(
        self,
        diffs: list[DiffItem],
        context: BoundaryCoverageContext,
    ) -> tuple[list[DiffItem], list[BoundaryCoverageDecision]]:
        kept: list[DiffItem] = []
        decisions: list[BoundaryCoverageDecision] = []
        for diff in diffs:
            if self._short_appendix_heading_covered(diff, context):
                decisions.append(
                    BoundaryCoverageDecision(
                        action="suppressed_by_neighbor_clause_coverage",
                        diff_id=diff.diff_id,
                        detail={"reason": "short_appendix_heading_covered"},
                    )
                )
                continue
            kept.append(diff)
        return kept, decisions

    def _short_appendix_heading_covered(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
        if diff.source_type != "clause" or diff.diff_type not in {"ADD", "DELETE"}:
            return False
        if diff.section_type != "appendix":
            return False

        changed = (
            (diff.original_text or diff.original_snippet)
            if diff.diff_type == "DELETE"
            else (diff.compare_text or diff.compare_snippet)
        )
        heading_key = appendix_heading_key(changed)
        if not heading_key:
            return False

        opposite_document = context.compare_document if diff.diff_type == "DELETE" else context.original_document
        if opposite_document is None:
            return False

        evidence_pages = self._evidence_pages(diff)
        if not evidence_pages:
            return False

        candidate_pages = {page_no + offset for page_no in evidence_pages for offset in (-1, 0, 1)}
        for page in opposite_document.pages:
            if page.page_no not in candidate_pages:
                continue
            page_text = "\n".join(block.text for block in page.blocks)
            if contains_appendix_heading(page_text, heading_key):
                return True
        return False

    @staticmethod
    def _evidence_pages(diff: DiffItem) -> set[int]:
        if diff.diff_type == "DELETE":
            return {evidence.page_no for evidence in diff.original_evidence}
        return {evidence.page_no for evidence in diff.compare_evidence}


def compact_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    return _COMPACT_PUNCTUATION_PATTERN.sub("", normalized).lower()


def appendix_heading_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    if len(compact_text(normalized)) > 8:
        return ""
    match = _APPENDIX_HEADING_PATTERN.fullmatch(normalized)
    if not match:
        return ""
    return compact_text(match.group(1))


def contains_appendix_heading(text: str, heading_key: str) -> bool:
    if not heading_key:
        return False
    normalized = unicodedata.normalize("NFKC", text or "").replace("\f", "\n")
    for line in normalized.splitlines():
        match = _APPENDIX_HEADING_LINE_PATTERN.match(line)
        if match and compact_text(match.group(1)) == heading_key:
            return True
    return False
