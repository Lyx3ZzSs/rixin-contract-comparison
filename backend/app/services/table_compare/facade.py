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
from app.services.table_compare import utils

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
            # Three-tier degradation based on multi-signal quality score.
            orig_score = self._compute_table_quality(original_tables) if original_tables else 1.0
            comp_score = self._compute_table_quality(compare_tables) if compare_tables else 1.0
            min_score = min(orig_score, comp_score)

            if min_score >= 0.5:
                # Tier 1: full cell-level comparison
                return self._compare_tables(
                    original_tables, compare_tables, original_blocks, compare_blocks, start_index, warnings
                )
            elif min_score >= 0.2:
                # Tier 2: row-level summary comparison
                logger.info("表格结构质量中等(%.2f)，降级到行级对比", min_score)
                return self._flat.row_level_compare(
                    original_tables, compare_tables, start_index, warnings
                )
            else:
                # Tier 3: flat text fallback
                logger.info("表格结构质量不足(%.2f)，降级到纯文本对比", min_score)
                return self._flat.flat_compare(original_blocks, compare_blocks, start_index, warnings)

        # Fallback: flat-text comparison for blocks without HTML
        return self._flat.flat_compare(original_blocks, compare_blocks, start_index, warnings)

    @staticmethod
    def _tables_quality_ok(tables: list[_LogicalTable], min_score: float = 0.1) -> bool:
        """Backward-compatible boolean quality gate — delegates to _compute_table_quality."""
        return TableComparator._compute_table_quality(tables) >= min_score

    @staticmethod
    def _compute_table_quality(tables: list[_LogicalTable]) -> float:
        """Multi-signal quality score for a group of logical tables.

        Returns a value in [0, 1] where:
          >= 0.5  → full cell-level comparison
          >= 0.2  → row-level summary comparison
          <  0.2  → flat-text fallback

        Signals (inspired by MinerU's per-stage confidence scoring):
          1. Fill rate  — fraction of non-empty cells (weight 0.4)
          2. Column consistency — uniform col_count across tables (weight 0.3)
          3. Content density — average normalised text length per filled cell (weight 0.3)
        """
        if not tables:
            return 0.0

        total_cells = 0
        filled_cells = 0
        total_text_len = 0

        for table in tables:
            # Use table.col_count * row count for expected total — avoids
            # penalising rows where anchor_cell skips empty columns.
            total_cells += table.col_count * len(table.rows)
            for row in table.rows:
                for cell in row.cells:
                    text = cell.text.strip()
                    if text:
                        filled_cells += 1
                        total_text_len += len(utils.normalize(text))

        if total_cells == 0:
            return 0.0

        # Signal 1: fill rate (0..1)
        fill_rate = filled_cells / total_cells

        # Signal 2: column consistency — uniform col_count (0..1)
        col_counts = {table.col_count for table in tables}
        col_consistency = 1.0 if len(col_counts) <= 1 else 0.8

        # Signal 3: content density — gentler baseline for CJK text
        avg_text_len = total_text_len / max(filled_cells, 1)
        content_density = min(avg_text_len / 8.0, 1.0)

        return fill_rate * 0.4 + col_consistency * 0.3 + content_density * 0.3

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
