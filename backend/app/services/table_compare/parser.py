"""Table parsing and logical table construction."""

from __future__ import annotations

import logging
import re

from app.models import BBox, TextBlock
from app.models_table import StructuredTable, TableRow
from app.services.table_compare.constants import (
    TABLE_BLOCK_TYPES,
    TABLE_HEADERS,
)
from app.services.table_compare.types import (
    RowSignature,
    _LogicalCell,
    _LogicalRow,
    _LogicalTable,
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
        tables: list[StructuredTable] = []
        pending_caption = ""
        last_page_no = -1

        for block, text in blocks:
            # Reset caption when moving to a new page
            if block.page_no != last_page_no:
                pending_caption = ""
                last_page_no = block.page_no

            has_html_table = "<table" in text.lower()

            if not has_html_table:
                # Non-HTML table_title block — remember as potential caption
                if block.block_type == "table_title" and block.text.strip():
                    pending_caption = block.text.strip()
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
                        if pending_caption:
                            t.caption = pending_caption
                            pending_caption = ""
                        tables.append(t)
            except Exception:
                logger.debug("HTML table parsing failed for block %s", block.block_id, exc_info=True)
        return tables

    # ------------------------------------------------------------------
    # Cross-page table merge (enhanced with MinerU-inspired techniques)
    # ------------------------------------------------------------------

    _MAX_HEADER_SCAN = 5
    _MAX_RESTART_CONTEXT_SCAN = 10

    def stitch_logical_tables(self, tables: list[StructuredTable], repair_service) -> list[_LogicalTable]:
        """Merge consecutive table fragments into logical tables.

        Enhanced with MinerU-inspired cross-page merge logic:
        - Continuation marker detection (续表, continued, etc.)
        - Row-signature-based header caching and deduplication
        - Structure-compatible stitching across consecutive pages
        """
        logical: list[_LogicalTable] = []
        pending: list[StructuredTable] = []
        header_sigs: list[RowSignature] = []

        def flush_pending() -> None:
            nonlocal pending, header_sigs
            if pending:
                table = self._build_logical_table(pending, repair_service)
                if table.rows:
                    logical.append(table)
                pending = []
                header_sigs = []

        for table in tables:
            if pending:
                # Case 1: summary-only table following product-like pending group
                if self._is_summary_only_table(table) and self._should_stitch_summary_table(pending[-1], table):
                    pending.append(table)
                    continue

                # Case 2: cross-page continuation detection
                if self._should_stitch_continuation(pending, table, header_sigs):
                    table = self._strip_matching_headers(table, header_sigs)
                    pending.append(table)
                    continue

            # Start a new group
            flush_pending()

            if self._is_product_like_table(table):
                header_sigs = self._extract_header_signatures(table)
                pending.append(table)
                continue

            logical_table = self._build_logical_table([table], repair_service)
            if logical_table.rows:
                logical.append(logical_table)

        flush_pending()
        return logical

    def _should_stitch_continuation(
        self, pending: list[StructuredTable], candidate: StructuredTable,
        header_sigs: list[RowSignature],
    ) -> bool:
        """Decide whether *candidate* should be stitched onto *pending*.

        Three signals, any one suffices:
        1. Explicit continuation marker detected on or near the candidate.
        2. Candidate header rows match cached header signatures (duplicate header).
        3. Product-like candidate on a consecutive page with compatible columns.

        col_count difference is a soft signal, not a hard gate: strong signals
        (continuation marker, header match) tolerate up to 3 columns difference;
        product-like signal tolerates up to 2; no signal keeps the original limit.
        """
        last = pending[-1]
        if candidate.page_no not in {last.page_no, last.page_no + 1}:
            return False

        col_diff = abs(candidate.col_count - last.col_count)

        # Signal 1: explicit continuation marker in table text or source text
        if self._has_continuation_marker(candidate):
            return col_diff <= 3

        # Signal 2: matching header rows (strong indicator of continuation)
        if header_sigs and self._candidate_matches_cached_headers(candidate, header_sigs):
            return col_diff <= 3

        # Signal 3: product-like on consecutive page with compatible structure
        if candidate.page_no == last.page_no + 1 and self._is_product_like_table(candidate):
            if self._looks_like_independent_product_table(pending, candidate, header_sigs):
                return False
            return col_diff <= 2

        # No supporting signal — keep strict col_count gate
        return col_diff <= 1

    def _looks_like_independent_product_table(
        self,
        pending: list[StructuredTable],
        candidate: StructuredTable,
        header_sigs: list[RowSignature],
    ) -> bool:
        """Guard weak cross-page stitching from absorbing a new product-like table."""
        if self._has_distinct_header(candidate, header_sigs):
            return True
        first_sequence = self._first_data_sequence(candidate)
        if first_sequence == 1 and self._has_data_sequence(pending):
            if self._has_sequence_restart_continuation_context(candidate, header_sigs):
                return False
            return True
        return False

    def _has_sequence_restart_continuation_context(
        self, candidate: StructuredTable, header_sigs: list[RowSignature],
    ) -> bool:
        """Detect subtotal/section restarts inside one long product quotation table."""
        return (
            self._has_embedded_matching_header(candidate, header_sigs)
            or self._has_near_top_summary_context(candidate)
        )

    def _has_embedded_matching_header(
        self, candidate: StructuredTable, header_sigs: list[RowSignature],
    ) -> bool:
        if not header_sigs:
            return False
        for row in candidate.rows[1:self._MAX_RESTART_CONTEXT_SCAN]:
            if not self._is_table_header_cells_from_struct(row.cells):
                continue
            sig = utils.build_row_signature(row.cells)
            if any(sig.matches_with_text(cached, threshold=0.5) for cached in header_sigs):
                return True
        return False

    def _has_near_top_summary_context(self, candidate: StructuredTable) -> bool:
        for row in candidate.rows[:self._MAX_RESTART_CONTEXT_SCAN]:
            if any(utils.summary_labels(cell.text) for cell in row.cells):
                return True
        return False

    def _has_distinct_header(self, candidate: StructuredTable, header_sigs: list[RowSignature]) -> bool:
        if not header_sigs:
            return False
        for row in candidate.rows[:self._MAX_HEADER_SCAN]:
            if not self._is_table_header_cells_from_struct(row.cells):
                continue
            sig = utils.build_row_signature(row.cells)
            return not any(sig.matches_with_text(cached, threshold=0.5) for cached in header_sigs)
        return False

    def _has_data_sequence(self, tables: list[StructuredTable]) -> bool:
        return any(self._first_data_sequence(table) is not None for table in tables)

    def _first_data_sequence(self, table: StructuredTable) -> int | None:
        for row in table.rows:
            if self._is_table_header_cells_from_struct(row.cells):
                continue
            ordered_cells = sorted(row.cells, key=lambda cell: cell.col_index)
            texts = [utils.normalize(cell.text) for cell in ordered_cells if utils.normalize(cell.text)]
            if not texts:
                continue
            if any(utils.summary_labels(text) for text in texts):
                continue
            first = texts[0]
            if re.fullmatch(r"\d{1,3}", first):
                return int(first)
        return None

    def _has_continuation_marker(self, table: StructuredTable) -> bool:
        """Check whether the table or its surrounding text carries a continuation marker."""
        # Check source text first (may contain caption or preceding text)
        if table.source_text and utils.is_continuation_text(table.source_text):
            return True
        # Check first row text (sometimes "(续)" appears as a row)
        for row in table.rows[:2]:
            for cell in row.cells:
                if utils.is_continuation_text(cell.text):
                    return True
        return False

    def _candidate_matches_cached_headers(
        self, candidate: StructuredTable, header_sigs: list[RowSignature],
    ) -> bool:
        """Return True if the candidate's first rows match cached header signatures."""
        for row in candidate.rows[:len(header_sigs)]:
            sig = utils.build_row_signature(row.cells)
            for cached in header_sigs:
                if sig.matches_with_text(cached, threshold=0.5):
                    return True
        return False

    def _extract_header_signatures(self, table: StructuredTable) -> list[RowSignature]:
        """Extract RowSignatures for the header rows of *table*."""
        sigs: list[RowSignature] = []
        for row in table.rows[:self._MAX_HEADER_SCAN]:
            cells = row.cells
            if not cells:
                continue
            sig = utils.build_row_signature(cells)
            if self._is_table_header_cells_from_struct(cells):
                sigs.append(sig)
            elif sigs:
                # Past the header region
                break
        return sigs

    def _strip_matching_headers(
        self, table: StructuredTable, header_sigs: list[RowSignature],
    ) -> StructuredTable:
        """Return a copy of *table* with rows matching *header_sigs* removed."""
        if not header_sigs:
            return table
        kept_rows: list[TableRow] = []
        skip_count = 0
        for row in table.rows:
            if skip_count < len(header_sigs):
                sig = utils.build_row_signature(row.cells)
                if sig.matches_with_text(header_sigs[skip_count], threshold=0.5):
                    skip_count += 1
                    continue
            kept_rows.append(row)
        if not kept_rows:
            return table
        return StructuredTable(
            page_no=table.page_no,
            rows=kept_rows,
            col_count=table.col_count,
            source_block_id=table.source_block_id,
            source=table.source,
            source_text=table.source_text,
        )

    @staticmethod
    def _is_table_header_cells_from_struct(cells) -> bool:
        """Check whether cells constitute a table header row (StructuredTable cells)."""
        texts = [utils.normalize(cell.text) for cell in cells if utils.normalize(cell.text)]
        if not texts:
            return False
        header_count = sum(1 for text in texts if text in TABLE_HEADERS)
        return header_count >= 3 and header_count / len(texts) >= 0.5

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
        rows = repair_service.repair_phantom_merged_name_rows(rows, col_count)
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
            caption=first.caption if first else "",
            footnote=first.footnote if first else "",
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
        if utils.is_quote_remark_text(text):
            return ""
        if cell.colspan >= max(2, col_count - 1) or ("系统" in text and ("硬件" in text or "软件" in text or "v" in text)):
            return text
        return ""
