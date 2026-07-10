from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median

from app.models import Document, TextBlock
from app.services.clause_numbering import ClauseNumberParser


PROTECTED_LABEL_RE = re.compile(
    r"^[\u4e00-\u9fff]{1,12}(?:方|人|名|号|码|话|真|箱|址|字|章|期|日)[:：]?$"
)
FIELD_VALUE_RE = re.compile(r"^[^:：\n]{1,24}[:：][^:：\n]{1,80}$")
VALUE_RE = re.compile(
    r"^(?:"
    r"[+-]?\d+(?:\.\d+)?(?:元|万元|亿元|%|天|月|年|份|项|台|套|号)?"
    r"|(?:19|20)\d{2}(?:年|[-/.]\d{1,2}(?:[-/.]\d{1,2})?)"
    r"|1\d{10}"
    r"|[^@\s]+@[^@\s]+\.[^@\s]+"
    r")$"
)

MEANINGFUL_BLOCK_TYPES = frozenset(
    {"paragraph_title", "doc_title", "title", "table", "table_title", "table_cell"}
)
MEANINGFUL_ROLES = frozenset(
    {
        "heading",
        "main_clause",
        "appendix",
        "appendix_section",
        "quote",
        "quote_section",
        "quote_metadata",
        "safety_agreement",
        "safety_section",
        "table",
        "table_body",
        "table_title",
        "table_cell",
    }
)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


@dataclass
class RepeatedOverlayFilterResult:
    decisions: list[dict[str, object]] = field(default_factory=list)

    @property
    def filtered_block_count(self) -> int:
        return sum(
            int(item.get("filtered_block_count", 0))
            for item in self.decisions
            if item.get("action") == "filtered"
        )

    def to_debug_payload(self) -> dict[str, object]:
        return {"filtered_block_count": self.filtered_block_count, "decisions": self.decisions}


class RepeatedOverlayFilter:
    min_pages = 5
    min_page_ratio = 0.80
    min_cluster_ratio = 0.80
    position_tolerance = 0.08

    def __init__(self) -> None:
        self.number_parser = ClauseNumberParser()

    def apply(self, document: Document) -> RepeatedOverlayFilterResult:
        result = RepeatedOverlayFilterResult()
        groups: dict[str, list[tuple[TextBlock, float, float]]] = defaultdict(list)
        for page in document.pages:
            for block in page.blocks:
                key = _compact(block.text)
                if not 2 <= len(key) <= 12 or block.enter_clause_compare is False:
                    continue
                groups[key].append(
                    (
                        block,
                        (block.bbox.x0 + block.bbox.x1) / (2 * max(page.width, 1)),
                        (block.bbox.y0 + block.bbox.y1) / (2 * max(page.height, 1)),
                    )
                )
        for key, occurrences in groups.items():
            page_counts: dict[int, int] = defaultdict(int)
            for block, _, _ in occurrences:
                page_counts[block.page_no] += 1
            page_ratio = len(page_counts) / max(document.page_count, 1)
            cluster_ratio = self._cluster_ratio(occurrences)
            reason = self._rejection_reason(key, occurrences, page_counts, page_ratio, cluster_ratio)
            if reason:
                if len(page_counts) >= self.min_pages:
                    result.decisions.append(
                        {
                            "action": "kept",
                            "text": key,
                            "page_ratio": round(page_ratio, 4),
                            "cluster_ratio": round(cluster_ratio, 4),
                            "reason": reason,
                        }
                    )
                continue
            for block, _, _ in occurrences:
                block.enter_clause_compare = False
                block.flow_role = "noise"
                block.source = "+".join(
                    part for part in [block.source, "repeated_overlay_filter"] if part
                )
                block.semantic_reasons = [*block.semantic_reasons, "repeated_overlay_filter"]
            result.decisions.append(
                {
                    "action": "filtered",
                    "text": key,
                    "page_ratio": round(page_ratio, 4),
                    "cluster_ratio": round(cluster_ratio, 4),
                    "filtered_block_count": len(occurrences),
                    "block_ids": [block.block_id for block, _, _ in occurrences],
                }
            )
        return result

    def _rejection_reason(
        self,
        key: str,
        occurrences: list[tuple[TextBlock, float, float]],
        page_counts: dict[int, int],
        page_ratio: float,
        cluster_ratio: float,
    ) -> str:
        if len(page_counts) < self.min_pages or page_ratio < self.min_page_ratio:
            return "insufficient_page_coverage"
        if max(page_counts.values()) > 1:
            return "too_many_occurrences_per_page"
        if cluster_ratio < self.min_cluster_ratio:
            return "unstable_position"
        if any(self._has_meaningful_metadata(block) for block, _, _ in occurrences):
            return "meaningful_metadata"
        if (
            PROTECTED_LABEL_RE.fullmatch(key)
            or FIELD_VALUE_RE.fullmatch(key)
            or VALUE_RE.fullmatch(key)
        ):
            return "protected_field_or_value"
        if self.number_parser.parse_line(key) is not None:
            return "numbered_heading_or_clause"
        return ""

    def _has_meaningful_metadata(self, block: TextBlock) -> bool:
        block_type = (block.block_type or "").lower()
        roles = {
            (block.block_role or "").lower(),
            (block.semantic_role or "").lower(),
            (block.flow_role or "").lower(),
        }
        return block_type in MEANINGFUL_BLOCK_TYPES or bool(roles & MEANINGFUL_ROLES)

    def _cluster_ratio(self, occurrences: list[tuple[TextBlock, float, float]]) -> float:
        xs = [x for _, x, _ in occurrences]
        ys = [y for _, _, y in occurrences]
        center_x = median(xs)
        center_y = median(ys)
        clustered = sum(
            1
            for _, x, y in occurrences
            if abs(x - center_x) <= self.position_tolerance
            and abs(y - center_y) <= self.position_tolerance
        )
        return clustered / max(len(occurrences), 1)
