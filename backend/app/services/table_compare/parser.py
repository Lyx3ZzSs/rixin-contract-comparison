"""Table parsing and logical table construction."""

from __future__ import annotations

import logging
import re
import unicodedata

from app.models import BBox, TextBlock
from app.models_table import StructuredTable
from app.services.table_compare.constants import (
    TABLE_BLOCK_TYPES,
    TABLE_HEADERS,
)
from app.services.table_compare.types import (
    _LogicalCell,
    _LogicalRow,
    _LogicalTable,
    _SummaryPair,
    _SummaryTransition,
)
from app.services.table_compare.html_parser import parse_html_tables
from app.services.table_compare import utils

logger = logging.getLogger(__name__)


class LogicalTableParser:
    """Extracts, parses, and builds logical tables from document blocks."""

    table_block_types = TABLE_BLOCK_TYPES

    def table_blocks(self, document) -> list[tuple[TextBlock, str]]:
        result = []
        for page in document.pages:
            for block in page.blocks:
                if self.is_table_block(block):
                    text = block.raw_html or block.text or ""
                    result.append((block, text))
        return result

    def parse_tables(self, blocks: list[tuple[TextBlock, str]]) -> list[StructuredTable]:
        tables = []
        for block, text in blocks:
            if "<table" not in text.lower():
                continue
            try:
                cell_bboxes = [BBox(x0=b[0], y0=b[1], x1=b[2], y1=b[3]) for b in block.table_cell_bboxes] if block.table_cell_bboxes else None
                parsed = parse_html_tables(
                    text,
                    page_no=block.page_no,
                    source="ppstructure_html",
                    source_block_id=block.block_id,
                    cell_bboxes=cell_bboxes,
                    source_text=block.text if block.raw_html and block.text != block.raw_html else "",
                )
                for t in parsed:
                    if t.rows and any(cell.text.strip() for row in t.rows for cell in row.cells):
                        tables.append(t)
            except Exception:
                logger.debug("HTML table parsing failed for block %s", block.block_id, exc_info=True)
        return tables

    def stitch_logical_tables(self, tables: list[StructuredTable], repair_service) -> list[_LogicalTable]:
        """Merge consecutive product-list table fragments into logical tables."""
        logical: list[_LogicalTable] = []
        pending: list[StructuredTable] = []

        def flush_pending() -> None:
            nonlocal pending
            if pending:
                table = self._build_logical_table(pending, repair_service)
                if table.rows:
                    logical.append(table)
                pending = []

        for table in tables:
            if pending and self._is_summary_only_table(table) and self._should_stitch_summary_table(pending[-1], table):
                pending.append(table)
                continue
            if self._is_product_like_table(table):
                pending.append(table)
                continue
            flush_pending()
            logical_table = self._build_logical_table([table], repair_service)
            if logical_table.rows:
                logical.append(logical_table)
        flush_pending()
        return logical

    def _build_logical_table(self, tables: list[StructuredTable], repair_service) -> _LogicalTable:
        rows: list[_LogicalRow] = []
        current_section = ""
        col_count = max((table.col_count for table in tables), default=0)
        logical_index = 0

        for table in tables:
            for row in table.rows:
                cells = self._logical_cells(table, row.row_index, logical_index)
                if not cells:
                    continue
                row_text = utils.normalize(" ".join(cell.text for cell in cells if cell.text))
                if not row_text:
                    continue
                if self._is_table_header_cells(cells):
                    continue
                if self._is_ocr_fragment_row(cells):
                    continue
                section = self._section_title(cells, table.col_count)
                if section:
                    current_section = section
                    if self._is_product_like_table(table):
                        continue
                rows.append(_LogicalRow(
                    row_index=logical_index,
                    cells=cells,
                    page_no=table.page_no,
                    source_block_id=table.source_block_id,
                    source_row=row.row_index,
                    section_title=current_section,
                    source_text=table.source_text,
                ))
                logical_index += 1

        rows = repair_service.normalize_merged_sequence_rows(rows, col_count)
        rows = repair_service.repair_merged_adjacent_sequence_rows(rows, col_count)
        rows = repair_service.repair_product_continuation_rows(rows, col_count)
        rows = repair_service.repair_shifted_product_field_rows(rows, col_count)
        rows = repair_service.repair_embedded_summary_transitions(rows, col_count)
        rows = repair_service.normalize_summary_rows(rows, col_count, tables)
        rows = repair_service.remove_orphan_overflow_sequence_cells(rows, col_count)
        first = tables[0] if tables else None
        return _LogicalTable(
            rows=rows,
            col_count=col_count,
            page_no=first.page_no if first else 0,
            source_block_id=first.source_block_id if first else "",
            source="logical_table",
        )

    def _logical_cells(self, table: StructuredTable, row: int, logical_row: int) -> list[_LogicalCell]:
        cells: list[_LogicalCell] = []
        for col in range(table.col_count):
            cell = utils.anchor_cell(table, row, col)
            if cell is None:
                continue
            cells.append(_LogicalCell(
                row_index=logical_row,
                col_index=col,
                text=cell.text,
                bbox=cell.bbox,
                page_no=table.page_no,
                source_block_id=table.source_block_id,
                source_row=row,
                source_col=col,
                colspan=cell.colspan,
                rowspan=cell.rowspan,
            ))
        return cells

    def is_table_block(self, block: TextBlock) -> bool:
        block_type = (block.block_type or "").lower()
        return block_type in self.table_block_types

    def _is_summary_only_table(self, table: StructuredTable) -> bool:
        saw_summary = False
        for row in table.rows:
            cells = [
                utils.normalize(cell.text)
                for cell in row.cells
                if utils.normalize(cell.text)
            ]
            if not cells:
                continue
            row_has_summary = any(utils.summary_labels(text) for text in cells)
            if row_has_summary:
                saw_summary = True
                continue
            if all(utils.is_number_like(text) or utils.is_amount_like(text) for text in cells):
                continue
            return False
        return saw_summary

    @staticmethod
    def _should_stitch_summary_table(previous: StructuredTable, current: StructuredTable) -> bool:
        return current.page_no in {previous.page_no, previous.page_no + 1}

    def _is_product_like_table(self, table: StructuredTable) -> bool:
        if table.col_count >= 8:
            return True
        text = utils.normalize(table.all_cell_text())
        return table.col_count >= 6 and any(token in text for token in ("产品名称", "详细配置", "单价", "金额"))

    def _is_table_header_cells(self, cells: list[_LogicalCell]) -> bool:
        texts = [utils.normalize(cell.text) for cell in cells if utils.normalize(cell.text)]
        if not texts:
            return False
        header_count = sum(1 for text in texts if text in TABLE_HEADERS)
        return header_count >= 3 and header_count / len(texts) >= 0.5

    def _is_ocr_fragment_row(self, cells: list[_LogicalCell]) -> bool:
        nonempty = [c for c in cells if utils.normalize(c.text)]
        if not nonempty:
            return False
        total_text = "".join(utils.normalize(c.text) for c in nonempty)
        if utils.is_number_like(total_text):
            return False
        if len(nonempty) / max(len(cells), 1) >= 0.3:
            return False
        return len(total_text) <= 3

    def _section_title(self, cells: list[_LogicalCell], col_count: int) -> str:
        nonempty = [cell for cell in cells if utils.normalize(cell.text)]
        if len(nonempty) != 1:
            return ""
        cell = nonempty[0]
        text = utils.normalize(cell.text)
        if utils.summary_labels(text):
            return ""
        if cell.colspan >= max(2, col_count - 1) or ("系统" in text and ("硬件" in text or "软件" in text or "v" in text)):
            return text
        return ""
