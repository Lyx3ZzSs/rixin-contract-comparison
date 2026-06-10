from __future__ import annotations

from app.models import BBox


class RowSignature:
    """Fingerprint of a table row for structure comparison.

    Inspired by MinerU's RowSignature in table_merge.py: captures column
    count, colspan/rowspan layout, and normalized cell texts so rows can
    be compared across pages to detect repeated headers or compatible
    continuation tables.
    """

    __slots__ = ("col_count", "colspans", "rowspans", "texts")

    def __init__(
        self,
        col_count: int,
        colspans: tuple[int, ...],
        rowspans: tuple[int, ...],
        texts: tuple[str, ...],
    ):
        self.col_count = col_count
        self.colspans = colspans
        self.rowspans = rowspans
        self.texts = texts

    def matches_structure(self, other: RowSignature) -> bool:
        """Check whether two rows share the same column layout."""
        return (
            self.col_count == other.col_count
            and self.colspans == other.colspans
            and self.rowspans == other.rowspans
        )

    def matches_with_text(self, other: RowSignature, threshold: float = 0.6) -> bool:
        """Check structural match *and* sufficient text overlap."""
        if not self.matches_structure(other):
            return False
        if not self.texts or not other.texts:
            return False
        matches = sum(1 for a, b in zip(self.texts, other.texts) if a == b)
        return matches / len(self.texts) >= threshold


class LogicalCell:
    __slots__ = (
        "row_index",
        "col_index",
        "text",
        "bbox",
        "page_no",
        "source_block_id",
        "source_row",
        "source_col",
        "colspan",
        "rowspan",
    )

    def __init__(
        self,
        row_index: int,
        col_index: int,
        text: str,
        bbox: BBox | None,
        page_no: int,
        source_block_id: str,
        source_row: int,
        source_col: int,
        colspan: int = 1,
        rowspan: int = 1,
    ):
        self.row_index = row_index
        self.col_index = col_index
        self.text = text
        self.bbox = bbox
        self.page_no = page_no
        self.source_block_id = source_block_id
        self.source_row = source_row
        self.source_col = source_col
        self.colspan = colspan
        self.rowspan = rowspan


class LogicalRow:
    __slots__ = ("row_index", "cells", "page_no", "source_block_id", "source_row", "section_title", "source_text")

    def __init__(
        self,
        row_index: int,
        cells: list[LogicalCell],
        page_no: int,
        source_block_id: str,
        source_row: int,
        section_title: str = "",
        source_text: str = "",
    ):
        self.row_index = row_index
        self.cells = cells
        self.page_no = page_no
        self.source_block_id = source_block_id
        self.source_row = source_row
        self.section_title = section_title
        self.source_text = source_text


class LogicalTable:
    __slots__ = ("rows", "col_count", "page_no", "source_block_id", "source", "caption", "footnote")

    def __init__(
        self,
        rows: list[LogicalRow],
        col_count: int,
        page_no: int,
        source_block_id: str = "",
        source: str = "",
        caption: str = "",
        footnote: str = "",
    ):
        self.rows = rows
        self.col_count = col_count
        self.page_no = page_no
        self.source_block_id = source_block_id
        self.source = source
        self.caption = caption
        self.footnote = footnote

    def get_cell(self, row: int, col: int) -> LogicalCell | None:
        for table_row in self.rows:
            if table_row.row_index != row:
                continue
            for cell in table_row.cells:
                if cell.col_index <= col < cell.col_index + cell.colspan:
                    return cell
        return None

    def all_cell_text(self) -> str:
        parts: list[str] = []
        for row in self.rows:
            for cell in row.cells:
                parts.append(cell.text)
        return " ".join(parts)


class CharSegment:
    __slots__ = ("tag", "orig_start", "orig_end", "comp_start", "comp_end")

    def __init__(self, tag: str, orig_start: int, orig_end: int, comp_start: int, comp_end: int):
        self.tag = tag
        self.orig_start = orig_start
        self.orig_end = orig_end
        self.comp_start = comp_start
        self.comp_end = comp_end


class SummaryPair:
    __slots__ = ("label", "amount", "canonical_amount", "line_index")

    def __init__(self, label: str, amount: str, canonical_amount: str, line_index: int):
        self.label = label
        self.amount = amount
        self.canonical_amount = canonical_amount
        self.line_index = line_index


class SummaryTransition:
    __slots__ = ("label", "amount", "canonical_amount", "line_index", "section")

    def __init__(self, label: str, amount: str, canonical_amount: str, line_index: int, section: str):
        self.label = label
        self.amount = amount
        self.canonical_amount = canonical_amount
        self.line_index = line_index
        self.section = section


class CellDiff:
    __slots__ = (
        "row",
        "col",
        "original_col",
        "compare_col",
        "original_text",
        "compare_text",
        "diff_type",
        "char_segments",
        "original_row",
        "compare_row",
    )

    def __init__(
        self,
        row: int,
        col: int,
        original_text: str,
        compare_text: str,
        diff_type: str,
        char_segments: list[CharSegment] | None = None,
        original_row: int | None = None,
        compare_row: int | None = None,
        original_col: int | None = None,
        compare_col: int | None = None,
    ):
        self.row = row
        self.col = col
        self.original_col = col if original_col is None and diff_type != "ADD" else original_col
        self.compare_col = col if compare_col is None and diff_type != "DELETE" else compare_col
        self.original_text = original_text
        self.compare_text = compare_text
        self.diff_type = diff_type
        self.char_segments = char_segments or []
        self.original_row = row if original_row is None and diff_type != "ADD" else original_row
        self.compare_row = row if compare_row is None and diff_type != "DELETE" else compare_row


class CellDiffGroup:
    __slots__ = ("row", "diffs")

    def __init__(self, row: int, diffs: list[CellDiff]):
        self.row = row
        self.diffs = diffs


class FlatUnit:
    __slots__ = ("text", "normalized", "page_no", "bbox")

    def __init__(self, text: str, normalized: str, page_no: int, bbox: BBox):
        self.text = text
        self.normalized = normalized
        self.page_no = page_no
        self.bbox = bbox


_LogicalCell = LogicalCell
_LogicalRow = LogicalRow
_LogicalTable = LogicalTable
_SummaryPair = SummaryPair
_SummaryTransition = SummaryTransition
_FlatUnit = FlatUnit
