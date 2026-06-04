"""Thin facade for TableComparator — delegates to submodules."""

from __future__ import annotations

from difflib import SequenceMatcher
import logging

from app.models import DiffItem, Document
from app.services.table_compare.parser import LogicalTableParser
from app.services.table_compare.repair import TableRepairService
from app.services.table_compare.matcher import TableMatcher
from app.services.table_compare.diff_builder import TableDiffBuilder
from app.services.table_compare.summary import SummaryComparator
from app.services.table_compare.flat import FlatTextComparator
from app.services.table_compare.constants import TABLE_BLOCK_TYPES
from app.services.table_compare.types import CellDiff, _LogicalTable
from app.services.table_compare import utils
from app.utils.id_utils import generate_diff_id

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
            cell_diffs = self._reconcile_quote_remark_cell_diffs(orig_table, comp_table, cell_diffs)
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

        diffs = self._reconcile_quote_remark_diffs(diffs)
        return self._renumber_diffs(diffs, start_index), warnings

    def _reconcile_quote_remark_diffs(self, diffs: list[DiffItem]) -> list[DiffItem]:
        remark_indices = [
            index for index, diff in enumerate(diffs)
            if self._is_quote_remark_diff(diff)
        ]
        if not remark_indices:
            return diffs
        if not any(diffs[index].diff_type == "DELETE" for index in remark_indices):
            return diffs
        if not any(diffs[index].diff_type == "ADD" for index in remark_indices):
            return diffs

        original_text = " ".join(diffs[index].original_text for index in remark_indices if diffs[index].original_text)
        compare_text = " ".join(diffs[index].compare_text for index in remark_indices if diffs[index].compare_text)
        if not original_text or not compare_text:
            return diffs

        similarity = self._quote_remark_text_similarity(original_text, compare_text)
        if similarity < 0.72:
            return diffs

        replacement_index = remark_indices[0]
        skipped = set(remark_indices)
        replacement = None
        if not self._quote_remark_text_equivalent(original_text, compare_text):
            replacement = self._make_quote_remark_modify_diff(
                [diffs[index] for index in remark_indices],
                original_text,
                compare_text,
            )

        result: list[DiffItem] = []
        for index, diff in enumerate(diffs):
            if index == replacement_index and replacement is not None:
                result.append(replacement)
            if index not in skipped:
                result.append(diff)
        return result

    @staticmethod
    def _is_quote_remark_diff(diff: DiffItem) -> bool:
        if diff.source_type != "table":
            return False
        text = diff.original_text or diff.compare_text
        if not utils.is_quote_remark_text(text):
            return False
        compact = utils.normalize_quote_remark(text)
        return 10 <= len(compact) <= 500

    @staticmethod
    def _quote_remark_text_similarity(left: str, right: str) -> float:
        left_norm = utils.normalize_quote_remark(left)
        right_norm = utils.normalize_quote_remark(right)
        if not left_norm or not right_norm:
            return 0.0
        if left_norm == right_norm:
            return 1.0
        return SequenceMatcher(None, left_norm, right_norm).ratio()

    @staticmethod
    def _quote_remark_text_equivalent(left: str, right: str) -> bool:
        left_norm = utils.normalize_quote_remark(left)
        right_norm = utils.normalize_quote_remark(right)
        return bool(left_norm and right_norm and left_norm == right_norm)

    @staticmethod
    def _make_quote_remark_modify_diff(diffs: list[DiffItem], original_text: str, compare_text: str) -> DiffItem:
        base = diffs[0]
        review_flags = list(dict.fromkeys(flag for diff in diffs for flag in diff.review_flags))
        original_evidence = [evidence for diff in diffs for evidence in diff.original_evidence]
        compare_evidence = [evidence for diff in diffs for evidence in diff.compare_evidence]
        return base.model_copy(update={
            "diff_type": "MODIFY",
            "original_text": original_text,
            "compare_text": compare_text,
            "original_snippet": original_text[:300],
            "compare_snippet": compare_text[:300],
            "readable_change": "表格备注变更",
            "review_flags": review_flags,
            "original_evidence": original_evidence,
            "compare_evidence": compare_evidence,
        })

    @staticmethod
    def _renumber_diffs(diffs: list[DiffItem], start_index: int) -> list[DiffItem]:
        return [
            diff.model_copy(update={"diff_id": generate_diff_id(start_index + offset)})
            for offset, diff in enumerate(diffs)
        ]

    def _reconcile_quote_remark_cell_diffs(
        self,
        original_table: _LogicalTable,
        compare_table: _LogicalTable,
        cell_diffs: list[CellDiff],
    ) -> list[CellDiff]:
        original_rows = self._quote_remark_rows(original_table)
        compare_rows = self._quote_remark_rows(compare_table)
        if not original_rows or not compare_rows:
            return cell_diffs

        original_text = self._quote_remark_rows_text(original_table, original_rows)
        compare_text = self._quote_remark_rows_text(compare_table, compare_rows)
        if self._quote_remark_text_similarity(original_text, compare_text) < 0.72:
            return cell_diffs

        filtered = [
            diff for diff in cell_diffs
            if diff.original_row not in original_rows and diff.compare_row not in compare_rows
        ]
        if self._quote_remark_text_equivalent(original_text, compare_text):
            return filtered

        filtered.append(CellDiff(
            row=min(min(original_rows), min(compare_rows)),
            col=0,
            original_text=original_text,
            compare_text=compare_text,
            diff_type="MODIFY",
            original_row=min(original_rows),
            compare_row=min(compare_rows),
            original_col=self._first_quote_remark_col(original_table, min(original_rows)),
            compare_col=self._first_quote_remark_col(compare_table, min(compare_rows)),
        ))
        return filtered

    @staticmethod
    def _quote_remark_rows(table: _LogicalTable) -> set[int]:
        rows: set[int] = set()
        for row in table.rows:
            text = " ".join(cell.text for cell in row.cells if cell.text)
            if utils.is_quote_remark_text(text):
                rows.add(row.row_index)
        return rows

    @staticmethod
    def _quote_remark_rows_text(table: _LogicalTable, row_indices: set[int]) -> str:
        parts: list[str] = []
        for row in table.rows:
            if row.row_index not in row_indices:
                continue
            text = " ".join(cell.text for cell in sorted(row.cells, key=lambda cell: cell.col_index) if cell.text)
            if text:
                parts.append(TableComparator._quote_remark_row_text(text))
        return " ".join(parts)

    @staticmethod
    def _quote_remark_row_text(text: str) -> str:
        normalized = utils.normalize(text)
        marker = "备注"
        marker_index = normalized.find(marker)
        if marker_index < 0:
            return text

        compact_seen = 0
        for index, char in enumerate(text):
            if utils.normalize(char):
                compact_seen += 1
            if compact_seen > marker_index:
                return text[index:]
        return text

    @staticmethod
    def _first_quote_remark_col(table: _LogicalTable, row_index: int) -> int:
        for row in table.rows:
            if row.row_index != row_index:
                continue
            for cell in sorted(row.cells, key=lambda item: item.col_index):
                if cell.text and utils.normalize(cell.text):
                    return cell.col_index
        return 0
