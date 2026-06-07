from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.models import Document, TextBlock


@dataclass
class DocumentPreparationDecision:
    side: str
    page_no: int
    block_id: str
    reason: str
    text_preview: str


@dataclass
class DocumentPreparationResult:
    decisions: list[DocumentPreparationDecision] = field(default_factory=list)

    def to_debug_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "side": decision.side,
                "page_no": decision.page_no,
                "block_id": decision.block_id,
                "reason": decision.reason,
                "text_preview": decision.text_preview,
            }
            for decision in self.decisions
        ]


class DocumentPreparer:
    signature_anchor_pattern = re.compile(
        r"(签字页|此页无正文|甲方|乙方|盖章|公章|法定代表人|法人代表|授权委托人|签字|日期)"
    )
    explicit_signature_roles = {"signature_area", "signature", "sign", "seal_area"}

    def prepare_pair(self, original: Document, compare: Document) -> DocumentPreparationResult:
        result = DocumentPreparationResult()
        self.prepare(original, "original", result)
        self.prepare(compare, "compare", result)
        return result

    def prepare(self, document: Document, side: str, result: DocumentPreparationResult | None = None) -> DocumentPreparationResult:
        result = result or DocumentPreparationResult()
        if not document.pages:
            return result

        final_pages = {page.page_no for page in sorted(document.pages, key=lambda item: item.page_no)[-2:]}
        for page in document.pages:
            if page.page_no not in final_pages:
                continue
            explicit_blocks = [block for block in page.blocks if self._is_explicit_signature_block(block)]
            for block in explicit_blocks:
                self._mark(block, side, "explicit_signature_role", result)

            if explicit_blocks:
                continue

            anchor_blocks = [block for block in page.blocks if self._has_signature_anchor(block.text)]
            distinct_anchors = self._distinct_anchor_count(anchor_blocks)
            if distinct_anchors < 3:
                continue
            mark_from = min((self._block_order(block) for block in anchor_blocks), default=0)
            for block in page.blocks:
                if self._block_order(block) >= mark_from or self._has_signature_anchor(block.text):
                    self._mark(block, side, "final_page_signature_anchors", result)
        return result

    def _is_explicit_signature_block(self, block: TextBlock) -> bool:
        return (block.block_role or "").lower() in self.explicit_signature_roles or (
            block.block_type or ""
        ).lower() in self.explicit_signature_roles

    def _has_signature_anchor(self, text: str) -> bool:
        return bool(self.signature_anchor_pattern.search(text or ""))

    def _distinct_anchor_count(self, blocks: list[TextBlock]) -> int:
        anchors: set[str] = set()
        for block in blocks:
            anchors.update(self.signature_anchor_pattern.findall(block.text or ""))
        return len(anchors)

    def _mark(
        self,
        block: TextBlock,
        side: str,
        reason: str,
        result: DocumentPreparationResult,
    ) -> None:
        if (block.block_role or "").lower() != "signature_area":
            block.block_role = "signature_area"
        result.decisions.append(
            DocumentPreparationDecision(
                side=side,
                page_no=block.page_no,
                block_id=block.block_id,
                reason=reason,
                text_preview=(block.text or "")[:160],
            )
        )

    def _block_order(self, block: TextBlock) -> tuple[int, float, float, str]:
        return (
            block.reading_order if block.reading_order is not None else block.layout_order or 0,
            block.bbox.y0,
            block.bbox.x0,
            block.block_id,
        )
