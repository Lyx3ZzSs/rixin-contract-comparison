from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models import BBox, Document, Page, TextBlock
from app.services.signing_region.models import SigningBlock


@dataclass
class SigningClauseDocumentResult:
    document: Document
    excluded_block_ids: list[str] = field(default_factory=list)
    entries: list[dict[str, Any]] = field(default_factory=list)


class SigningClauseDocumentBuilder:
    overlap_threshold = 0.55

    def build(
        self,
        document: Document,
        signing_blocks: list[SigningBlock],
    ) -> SigningClauseDocumentResult:
        blocks_by_page: dict[int, list[SigningBlock]] = {}
        for block in signing_blocks:
            if block.exclude_from_clause_diff:
                blocks_by_page.setdefault(block.page_no, []).append(block)

        excluded: list[str] = []
        entries: list[dict[str, Any]] = []
        new_pages: list[Page] = []
        for page in document.pages:
            signing_page_blocks = blocks_by_page.get(page.page_no, [])
            kept_blocks: list[TextBlock] = []
            for text_block in page.blocks:
                matched = self._matching_signing_block(text_block, signing_page_blocks)
                if matched is None:
                    kept_blocks.append(text_block)
                    continue

                excluded.append(text_block.block_id)
                entries.append(
                    {
                        "page_no": page.page_no,
                        "block_id": text_block.block_id,
                        "signing_block_id": matched.block_id,
                        "reason": "high_confidence_signing_block",
                    }
                )
            new_pages.append(page.model_copy(update={"blocks": kept_blocks}))

        return SigningClauseDocumentResult(
            document=document.model_copy(update={"pages": new_pages}),
            excluded_block_ids=excluded,
            entries=entries,
        )

    def _matching_signing_block(
        self,
        text_block: TextBlock,
        signing_blocks: list[SigningBlock],
    ) -> SigningBlock | None:
        for signing_block in signing_blocks:
            if text_block.block_id in signing_block.source_block_ids:
                return signing_block

        for signing_block in signing_blocks:
            if self._overlap_ratio(text_block.bbox, signing_block.bbox) >= self.overlap_threshold:
                return signing_block
        return None

    @staticmethod
    def _overlap_ratio(a: BBox, b: BBox) -> float:
        x0 = max(a.x0, b.x0)
        y0 = max(a.y0, b.y0)
        x1 = min(a.x1, b.x1)
        y1 = min(a.y1, b.y1)
        inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        base = max(1.0, (a.x1 - a.x0) * (a.y1 - a.y0))
        return inter / base
