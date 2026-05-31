"""Thin facade for TableComparator — delegates to submodules."""

from __future__ import annotations

import logging

from app.models import DiffItem, Document
from app.services.table_compare.parser import LogicalTableParser
from app.services.table_compare.repair import TableRepairService
from app.services.table_compare.matcher import TableMatcher
from app.services.table_compare.diff_builder import TableDiffBuilder
from app.services.table_compare.summary import SummaryComparator
from app.services.table_compare.flat import FlatTextComparator
from app.services.table_compare.constants import TABLE_BLOCK_TYPES
from app.services.table_compare.types import _LogicalTable

logger = logging.getLogger(__name__)


class TableComparator:
    """Public API for table comparison — delegates to specialized submodules."""

    table_block_types = TABLE_BLOCK_TYPES
    cell_similarity_threshold: float = 0.85
    row_similarity_threshold: float = 0.55
    table_similarity_threshold: float = 0.3

    def __init__(self) -> None:
        self._parser = LogicalTableParser()
        self._repair = TableRepairService()
        self._matcher = TableMatcher()
        self._matcher.row_similarity_threshold = self.row_similarity_threshold
        self._matcher.table_similarity_threshold = self.table_similarity_threshold
        self._summary = SummaryComparator(self._matcher, self._repair)
        self._diff_builder = TableDiffBuilder(self._matcher, self._summary)
        self._diff_builder.cell_similarity_threshold = self.cell_similarity_threshold
        self._flat = FlatTextComparator()

    def build_diffs(
        self, original: Document, compare: Document, start_index: int = 1
    ) -> tuple[list[DiffItem], list[str]]:
        warnings: list[str] = []

        original_blocks = self._parser.table_blocks(original)
        compare_blocks = self._parser.table_blocks(compare)
        if not original_blocks and not compare_blocks:
            if any(page.blocks for page in [*original.pages, *compare.pages]):
                warnings.append("未获得结构化表格区域，已跳过表格比对。")
            return [], warnings

        original_tables = self._parser.stitch_logical_tables(
            self._parser.parse_tables(original_blocks),
            self._repair,
        )
        compare_tables = self._parser.stitch_logical_tables(
            self._parser.parse_tables(compare_blocks),
            self._repair,
        )

        if original_tables or compare_tables:
            # Quality verification: degrade to flat text if tables are too sparse.
            # Only check the side that has tables — empty side is valid (means ADD/DELETE).
            orig_ok = self._tables_quality_ok(original_tables) if original_tables else True
            comp_ok = self._tables_quality_ok(compare_tables) if compare_tables else True
            if not orig_ok or not comp_ok:
                logger.info("表格结构质量不足，降级到纯文本对比")
                return self._flat.flat_compare(original_blocks, compare_blocks, start_index, warnings)

            return self._compare_tables(
                original_tables, compare_tables, original_blocks, compare_blocks, start_index, warnings
            )

        # Fallback: flat-text comparison for blocks without HTML
        return self._flat.flat_compare(original_blocks, compare_blocks, start_index, warnings)

    @staticmethod
    def _tables_quality_ok(tables: list[_LogicalTable], min_fill_rate: float = 0.1) -> bool:
        """Check whether parsed tables have sufficient content.

        Inspired by MinerU's table quality verification in
        ``unet_table/main.py`` which checks fill rate, cell count, and
        text content before accepting a recognition result.
        """
        total_cells = 0
        filled_cells = 0
        for table in tables:
            for row in table.rows:
                for cell in row.cells:
                    total_cells += 1
                    if cell.text.strip():
                        filled_cells += 1
        if total_cells == 0:
            return False
        return (filled_cells / total_cells) >= min_fill_rate

    def is_table_block(self, block) -> bool:
        return self._parser.is_table_block(block)

    def _compare_tables(
        self,
        original_tables,
        compare_tables,
        original_blocks,
        compare_blocks,
        start_index: int,
        warnings: list[str],
    ) -> tuple[list[DiffItem], list[str]]:
        pairs = self._matcher.match_tables(original_tables, compare_tables)
        diffs: list[DiffItem] = []
        next_index = start_index
        used_original: set[int] = set()
        used_compare: set[int] = set()

        for orig_idx, comp_idx, score in pairs:
            used_original.add(orig_idx)
            used_compare.add(comp_idx)
            orig_table = original_tables[orig_idx]
            comp_table = compare_tables[comp_idx]

            orig_block = self._diff_builder.find_block(original_blocks, orig_table)
            comp_block = self._diff_builder.find_block(compare_blocks, comp_table)
            cell_diffs = self._diff_builder.diff_cells(
                orig_table,
                comp_table,
                orig_block[0] if orig_block else None,
                comp_block[0] if comp_block else None,
            )
            for group in self._diff_builder.group_cell_diffs(cell_diffs, orig_table, comp_table):
                diffs.append(self._diff_builder.make_diff(group, next_index, orig_block, comp_block, orig_table, comp_table))
                next_index += 1

        # Unmatched tables: whole-table ADD / DELETE
        for idx in range(len(original_tables)):
            if idx not in used_original:
                block = self._diff_builder.find_block(original_blocks, original_tables[idx])
                diffs.append(self._diff_builder.whole_table_diff(original_tables[idx], "DELETE", next_index, block, None))
                next_index += 1
        for idx in range(len(compare_tables)):
            if idx not in used_compare:
                block = self._diff_builder.find_block(compare_blocks, compare_tables[idx])
                diffs.append(self._diff_builder.whole_table_diff(compare_tables[idx], "ADD", next_index, None, block))
                next_index += 1

        return diffs, warnings
