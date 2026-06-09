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
    table_note_label_pattern = re.compile(r"^(?:单位|币种|金额单位|计量单位|含税|不含税)[:：]")
    quote_metadata_label_pattern = re.compile(
        r"^(?:项目名称|项目名|项图名称|报价单位|报价日期|联系方式|项目经理|邮箱|网址|地址|"
        r"24小时服务热线|服务热线|联系人|电话)[:：]"
    )
    quote_title_pattern = re.compile(r"(?:报价明细|报价单|报价表|报价清单)$")
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
    signature_field_terms = (
        "甲方",
        "乙方",
        "买方",
        "卖方",
        "供方",
        "需方",
        "授权代表",
        "法定代表人",
        "法人代表或授权委托人",
        "法人代表",
        "纳税人识别",
        "地址",
        "电话",
        "开户行",
        "账号",
        "银行行号",
        "邮编",
        "日期",
    )
    signature_identity_terms = {
        "甲方",
        "乙方",
        "买方",
        "卖方",
        "供方",
        "需方",
        "授权代表",
        "法定代表人",
        "法人代表或授权委托人",
        "法人代表",
    }
    signature_partial_cue_pattern = re.compile(r"(签字页|此页无正文|盖章|签字|地址)")
    signature_noise_texts = {"站股", "心", "萧"}
    signature_seal_noise_pattern = re.compile(r"(?:未购合同章|合同章|公章|业务章)")
    signature_excluded_block_types = {
        "table",
        "table_cell",
        "header",
        "footer",
        "page_header",
        "page_footer",
        "footnote",
        "vision_footnote",
        "image",
        "figure",
        "seal",
        "chart",
        "formula",
        "vertical_text",
        "number",
    }
    signature_preserved_roles = {
        "cover_metadata",
        "table_caption",
        "table_note",
        "quote_metadata",
        "page_footer",
        "body_footnote",
        "noise",
    }
    formal_clause_heading_pattern = re.compile(
        r"^\s*(?:第[一二三四五六七八九十百千万0-9]+[章节条]|[一二三四五六七八九十百千万]+、)"
    )
    decimal_clause_heading_pattern = re.compile(r"^\s*(\d+(?:\.\d+){1,3})[、.．]?\s*(.+)$")
    integer_clause_heading_pattern = re.compile(r"^\s*\d+[、.．]\s*(.+)$")
    date_like_pattern = re.compile(r"^\s*(?:19|20)\d{2}[./年-]\d{1,2}(?:[./月-]\d{1,2}日?)?\s*$")

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
            if self._is_quote_page_metadata(block, table_blocks, compact):
                self._mark_role(block, side, "quote_metadata", "nearby_quote_page_metadata", result)
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

        self._classify_signature_region(page, side, result)

    def _classify_signature_region(
        self,
        page: Page,
        side: str,
        result: DocumentPreparationResult,
    ) -> None:
        text_blocks = [block for block in page.blocks if self._is_signature_region_text_block(block)]
        labeled_blocks = [
            (block, label)
            for block in text_blocks
            if (label := self._signature_field_label(block.text)) is not None
        ]
        if len(labeled_blocks) < 6:
            self._classify_partial_signature_region(page, side, result, text_blocks, labeled_blocks)
            return

        distinct_labels = {label for _, label in labeled_blocks}
        if len(distinct_labels) < 4 or not (distinct_labels & self.signature_identity_terms):
            return

        labeled_only = [block for block, _ in labeled_blocks]
        page_midpoint = self._signature_column_split(labeled_only, page.width)
        left_labels = [block for block, _ in labeled_blocks if block.bbox.x0 < page_midpoint]
        right_labels = [block for block, _ in labeled_blocks if block.bbox.x0 >= page_midpoint]
        if min(len(left_labels), len(right_labels)) < 2:
            return

        median_height = self._median_block_height(labeled_only)
        vertical_span = max(block.bbox.y1 for block in labeled_only) - min(block.bbox.y0 for block in labeled_only)
        paired_rows = self._signature_paired_row_count(labeled_only, page_midpoint, median_height)
        if vertical_span < max(50.0, median_height * 3.0):
            return
        if paired_rows < 2 and min(len(left_labels), len(right_labels)) < 3:
            return

        first_label_y = min(block.bbox.y0 for block in labeled_only)
        preceding_headings = [
            block
            for block in text_blocks
            if block.bbox.y0 < first_label_y and self._looks_like_real_clause_heading(block.text)
        ]
        preceding_heading = max(preceding_headings, key=lambda block: block.bbox.y0, default=None)
        region_start = first_label_y - max(45.0, median_height * 3.0)
        if preceding_heading is not None:
            region_start = max(region_start, preceding_heading.bbox.y1 + 1.0)

        for block in sorted(text_blocks, key=lambda item: (item.bbox.y0, item.bbox.x0)):
            if block.bbox.y0 < region_start:
                continue
            if block.bbox.y0 > first_label_y and self._looks_like_real_clause_heading(block.text):
                break
            self._mark_role(
                block,
                side,
                "signature_field",
                "signature_region_spatial_cluster",
                result,
            )

    def _classify_partial_signature_region(
        self,
        page: Page,
        side: str,
        result: DocumentPreparationResult,
        text_blocks: list[TextBlock],
        labeled_blocks: list[tuple[TextBlock, str]],
    ) -> None:
        if len(labeled_blocks) < 2:
            return

        identity_blocks = [
            block
            for block, label in labeled_blocks
            if label in self.signature_identity_terms
        ]
        if len(identity_blocks) < 2:
            return
        if len({label for _, label in labeled_blocks if label in self.signature_identity_terms}) < 2:
            return
        if not self._has_partial_signature_cue(text_blocks, labeled_blocks):
            return

        page_midpoint = self._partial_signature_column_split(identity_blocks, page.width)
        if not any(block.bbox.x0 < page_midpoint for block in identity_blocks):
            return
        if not any(block.bbox.x0 >= page_midpoint for block in identity_blocks):
            return

        median_height = self._median_block_height([block for block, _ in labeled_blocks])
        first_label_y = min(block.bbox.y0 for block, _ in labeled_blocks)
        last_relevant_y = max(block.bbox.y1 for block, _ in labeled_blocks)
        region_start = first_label_y - max(8.0, median_height * 0.6)
        region_end = min(page.height, last_relevant_y + max(90.0, median_height * 6.0))
        columns = self._partial_signature_columns(identity_blocks, page_midpoint)

        for block in sorted(text_blocks, key=lambda item: (item.bbox.y0, item.bbox.x0)):
            if block.bbox.y0 < region_start or block.bbox.y0 > region_end:
                continue
            if self._looks_like_real_clause_heading(block.text):
                continue
            if not self._is_partial_signature_block(block, columns, page_midpoint):
                continue
            self._mark_role(
                block,
                side,
                "signature_field",
                "partial_signature_region_spatial_cluster",
                result,
            )

    def _has_partial_signature_cue(
        self,
        text_blocks: list[TextBlock],
        labeled_blocks: list[tuple[TextBlock, str]],
    ) -> bool:
        if any(label == "地址" for _, label in labeled_blocks):
            return True
        first_label_y = min(block.bbox.y0 for block, _ in labeled_blocks)
        cue_blocks = [
            block
            for block in text_blocks
            if block.bbox.y0 <= first_label_y + 90.0
            and self.signature_partial_cue_pattern.search(block.text or "")
        ]
        return bool(cue_blocks)

    def _partial_signature_columns(
        self,
        identity_blocks: list[TextBlock],
        page_midpoint: float,
    ) -> dict[str, tuple[float, float]]:
        left_blocks = [block for block in identity_blocks if block.bbox.x0 < page_midpoint]
        right_blocks = [block for block in identity_blocks if block.bbox.x0 >= page_midpoint]
        columns: dict[str, tuple[float, float]] = {}
        if left_blocks:
            columns["left"] = (
                min(block.bbox.x0 for block in left_blocks),
                max(block.bbox.x1 for block in left_blocks),
            )
        if right_blocks:
            columns["right"] = (
                min(block.bbox.x0 for block in right_blocks),
                max(block.bbox.x1 for block in right_blocks),
            )
        return columns

    def _partial_signature_column_split(self, blocks: list[TextBlock], page_width: float) -> float:
        x_positions = sorted(block.bbox.x0 for block in blocks)
        if len(x_positions) >= 2:
            gap, left, right = max(
                ((right - left, left, right) for left, right in zip(x_positions, x_positions[1:], strict=False)),
                key=lambda item: item[0],
            )
            if gap >= 35.0:
                split = (left + right) / 2
                if page_width <= 0 or page_width * 0.10 <= split <= page_width * 0.90:
                    return split
        return self._signature_column_split(blocks, page_width)

    def _is_partial_signature_block(
        self,
        block: TextBlock,
        columns: dict[str, tuple[float, float]],
        page_midpoint: float,
    ) -> bool:
        if self._signature_field_label(block.text) is not None:
            return True
        compact = re.sub(r"\s+", "", block.text or "")
        if not compact:
            return False
        if self._is_signature_noise_block(block, compact):
            return False
        if self.signature_partial_cue_pattern.search(compact):
            return True
        column = "left" if block.bbox.x0 < page_midpoint else "right"
        if column not in columns:
            return False
        col_x0, col_x1 = columns[column]
        horizontal_overlap = max(0.0, min(block.bbox.x1, col_x1 + 80.0) - max(block.bbox.x0, col_x0 - 20.0))
        block_width = max(1.0, block.bbox.x1 - block.bbox.x0)
        return horizontal_overlap / block_width >= 0.35

    def _is_signature_noise_block(self, block: TextBlock, compact: str) -> bool:
        if compact in self.signature_noise_texts:
            return True
        if block.confidence is not None and block.confidence < 0.3 and len(compact) <= 2:
            return True
        if self.signature_seal_noise_pattern.search(compact):
            return True
        if len(compact) <= 1:
            return True
        return False

    def _is_signature_region_text_block(self, block: TextBlock) -> bool:
        if not (block.text or "").strip():
            return False
        if (block.block_type or "").lower() in self.signature_excluded_block_types:
            return False
        return (block.block_role or "").lower() not in self.signature_preserved_roles

    def _signature_field_label(self, text: str) -> str | None:
        compact = re.sub(r"\s+", "", text or "")
        for term in self.signature_field_terms:
            if compact.startswith(term):
                return term
        return None

    def _signature_paired_row_count(
        self,
        blocks: list[TextBlock],
        page_midpoint: float,
        median_height: float,
    ) -> int:
        threshold = max(8.0, median_height * 0.8)
        rows: list[list[TextBlock]] = []
        for block in sorted(blocks, key=lambda item: (item.bbox.y0, item.bbox.x0)):
            if not rows or block.bbox.y0 - min(item.bbox.y0 for item in rows[-1]) > threshold:
                rows.append([block])
            else:
                rows[-1].append(block)
        return sum(
            1
            for row in rows
            if any(block.bbox.x0 < page_midpoint for block in row)
            and any(block.bbox.x0 >= page_midpoint for block in row)
        )

    def _looks_like_real_clause_heading(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact or self.date_like_pattern.fullmatch(compact):
            return False
        if self.formal_clause_heading_pattern.match(compact):
            return True
        decimal_match = self.decimal_clause_heading_pattern.match(compact)
        if decimal_match:
            return len(decimal_match.group(2).strip("、.．:：")) >= 4
        integer_match = self.integer_clause_heading_pattern.match(compact)
        return bool(integer_match and len(integer_match.group(1).strip("、.．:：")) >= 4)

    def _signature_column_split(self, blocks: list[TextBlock], page_width: float) -> float:
        x_positions = sorted(block.bbox.x0 for block in blocks)
        if len(x_positions) >= 4:
            gaps = [
                (right - left, left, right)
                for left, right in zip(x_positions, x_positions[1:], strict=False)
            ]
            gap, left, right = max(gaps, key=lambda item: item[0])
            if gap >= 35.0:
                split = (left + right) / 2
                if page_width <= 0 or page_width * 0.15 <= split <= page_width * 0.85:
                    return split
        if page_width > 0:
            return page_width / 2
        return self._horizontal_midpoint(blocks)

    def _horizontal_midpoint(self, blocks: list[TextBlock]) -> float:
        if not blocks:
            return 0.0
        return (min(block.bbox.x0 for block in blocks) + max(block.bbox.x1 for block in blocks)) / 2

    def _median_block_height(self, blocks: list[TextBlock]) -> float:
        heights = sorted(max(0.0, block.bbox.y1 - block.bbox.y0) for block in blocks)
        return heights[len(heights) // 2] if heights else 0.0

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

    def _is_quote_page_metadata(
        self,
        block: TextBlock,
        tables: list[TextBlock],
        compact: str,
    ) -> bool:
        if not tables or not any(self._near_table(block.bbox, table.bbox) for table in tables):
            return False
        if self.quote_metadata_label_pattern.match(compact):
            return True
        return bool(len(compact) <= 80 and self.quote_title_pattern.search(compact))

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
