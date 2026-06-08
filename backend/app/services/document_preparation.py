from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.models import BBox, Document, Page, TextBlock
from app.services.cover.patterns import parse_labeled_line
from app.services.table_compare.constants import TABLE_HEADERS
from app.services.table_compare.utils import strip_html


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
    page_number_pattern = re.compile(r"^(?:第?\s*\d+\s*页?|共\s*\d+\s*页\s*第\s*\d+\s*页)$")
    clause_heading_pattern = re.compile(
        r"^\s*(?:第[一二三四五六七八九十百千万0-9]+[章节条]|[一二三四五六七八九十百千万0-9]+[、.．])"
    )
    table_block_types = {"table", "table_cell"}
    table_note_label_pattern = re.compile(r"^(?:单位|币种|金额单位|计量单位|报价单位|含税|不含税)[:：]")
    generic_table_terms = TABLE_HEADERS | {
        "名称",
        "项目",
        "内容",
        "规格",
        "型号",
        "品牌",
        "单位",
        "数量",
        "单价",
        "总价",
        "报价",
        "金额",
        "合计",
        "税率",
        "期限",
        "时间",
        "日期",
        "编号",
        "联系人",
        "电话",
        "地址",
        "服务内容",
        "服务期限",
        "配置",
        "参数",
        "指标",
        "标准",
        "备注",
    }
    clause_prefix_pattern = re.compile(
        r"^\s*(?:第[一二三四五六七八九十百千万0-9]+[章节条]|[一二三四五六七八九十百千万0-9]+[、.．]|[（(][一二三四五六七八九十百千万0-9]+[)）])\s*"
    )

    def prepare_pair(self, original: Document, compare: Document) -> DocumentPreparationResult:
        result = DocumentPreparationResult()
        self.prepare(original, "original", result)
        self.prepare(compare, "compare", result)
        return result

    def prepare(self, document: Document, side: str, result: DocumentPreparationResult | None = None) -> DocumentPreparationResult:
        result = result or DocumentPreparationResult()
        if not document.pages:
            return result

        for page in document.pages:
            self._classify_page_structure(page, side, result)

        return result

    def _classify_page_structure(
        self,
        page: Page,
        side: str,
        result: DocumentPreparationResult,
    ) -> None:
        body_start_y = min(
            (
                block.bbox.y0
                for block in page.blocks
                if self.clause_heading_pattern.match(block.text or "")
            ),
            default=page.height,
        )
        table_blocks = [
            block
            for block in page.blocks
            if (block.block_type or "").lower() in self.table_block_types
        ]

        for block in page.blocks:
            compact = re.sub(r"\s+", "", block.text or "")
            if not compact:
                continue
            if page.page_no == 1 and block.bbox.y0 <= body_start_y and self._is_cover_metadata(block):
                self._mark_role(block, side, "cover_metadata", "cover_metadata_parser", result)
                continue
            table_role = self._table_context_role(block, table_blocks, compact)
            if table_role:
                self._mark_role(block, side, table_role, "nearby_table_header_context", result)
                continue
            block_type = (block.block_type or "").lower()
            if block_type in {"footnote", "vision_footnote"}:
                near_bottom = page.height > 0 and block.bbox.y0 >= page.height * 0.90
                role = "page_footer" if near_bottom or self.page_number_pattern.fullmatch(compact) else "body_footnote"
                self._mark_role(block, side, role, "footnote_secondary_classification", result)

    def _is_cover_metadata(self, block: TextBlock) -> bool:
        for line in (block.text or "").splitlines():
            if parse_labeled_line(line.strip()):
                return True
        return False

    def _table_context_role(
        self,
        block: TextBlock,
        tables: list[TextBlock],
        compact: str,
    ) -> str | None:
        if (block.block_type or "").lower() in self.table_block_types:
            return None

        nearby_tables = [table for table in tables if self._near_table(block.bbox, table.bbox)]
        if not nearby_tables:
            return None

        if self.table_note_label_pattern.match(compact):
            return "table_note"

        candidate_terms = self._candidate_table_terms(block.text)
        if not candidate_terms or not self._looks_like_table_caption(block.text, candidate_terms):
            return None

        table_terms: set[str] = set()
        for table in nearby_tables:
            table_terms.update(self._table_header_terms(table))
        overlap = candidate_terms & table_terms
        if len(overlap) >= 2:
            return "table_caption"
        if len(overlap) == 1 and len(candidate_terms) <= 2:
            return "table_caption"
        return None

    def _candidate_table_terms(self, text: str) -> set[str]:
        without_prefix = self.clause_prefix_pattern.sub("", text or "")
        return self._tokenize_table_text(without_prefix)

    def _table_header_terms(self, table: TextBlock) -> set[str]:
        source = "\n".join(part for part in (table.raw_html and strip_html(table.raw_html), table.text) if part)
        return self._tokenize_table_text(source)

    def _tokenize_table_text(self, text: str) -> set[str]:
        normalized = strip_html(text or "")
        tokens = {
            token
            for token in re.split(r"[\s,，、;；:：|/\\()\[\]（）【】<>《》]+", normalized)
            if 1 < len(token) <= 16
        }
        compact = re.sub(r"\s+", "", normalized)
        tokens.update(term for term in self.generic_table_terms if term in compact)
        return tokens

    def _looks_like_table_caption(self, text: str, candidate_terms: set[str]) -> bool:
        without_prefix = self.clause_prefix_pattern.sub("", text or "").strip()
        compact = re.sub(r"\s+", "", without_prefix)
        if not compact or len(compact) > 100:
            return False
        if re.search(r"[。；;]", compact):
            return False
        if re.search(r"(应当|应|须|负责|确认|约定|履行|符合|承担|支付|交付|提供)", compact):
            return False
        separators = len(re.findall(r"[、,，/\s]+", without_prefix))
        return compact.endswith((":", "：")) or separators >= 2 or len(candidate_terms) >= 3

    def _near_table(self, block: BBox, table: BBox) -> bool:
        vertical_gap = table.y0 - block.y1
        horizontal_overlap = max(0.0, min(block.x1, table.x1) - max(block.x0, table.x0))
        block_width = max(1.0, block.x1 - block.x0)
        return -20.0 <= vertical_gap <= 140.0 and horizontal_overlap / block_width >= 0.2

    def _mark_role(
        self,
        block: TextBlock,
        side: str,
        role: str,
        reason: str,
        result: DocumentPreparationResult,
    ) -> None:
        if (block.block_role or "").lower() == role:
            return
        block.block_role = role
        result.decisions.append(
            DocumentPreparationDecision(
                side=side,
                page_no=block.page_no,
                block_id=block.block_id,
                reason=reason,
                text_preview=(block.text or "")[:160],
            )
        )
