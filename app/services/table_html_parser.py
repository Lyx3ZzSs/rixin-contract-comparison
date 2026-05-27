from __future__ import annotations

from html.parser import HTMLParser

from app.models import BBox
from app.models_table import TableCell, TableRow, StructuredTable


class TableHTMLParser(HTMLParser):
    """Parse HTML <table> elements into StructuredTable models."""

    def __init__(self) -> None:
        super().__init__()
        self._tables: list[_TableBuilder] = []
        self._current: _TableBuilder | None = None
        self._in_cell = False
        self._cell_text = ""
        self._cell_colspan = 1
        self._cell_rowspan = 1
        self._cell_bboxes: list[BBox] = []
        self._cell_bbox_idx = 0

    def parse_tables(
        self, html: str, page_no: int = 0, source: str = "", source_block_id: str = "",
        cell_bboxes: list[BBox] | None = None, source_text: str = "",
    ) -> list[StructuredTable]:
        self._tables = []
        self._current = None
        self._in_cell = False
        self._cell_text = ""
        self._cell_colspan = 1
        self._cell_rowspan = 1
        self._cell_bboxes = cell_bboxes or []
        self._cell_bbox_idx = 0
        self.feed(html)
        return [
            builder.build(page_no, source=source, source_block_id=source_block_id, source_text=source_text)
            for builder in self._tables
        ]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()
        attr_dict = {k.lower(): (v or "") for k, v in attrs}

        if tag_lower == "table":
            self._current = _TableBuilder()
            self._tables.append(self._current)
            return

        if self._current is None:
            return

        if tag_lower in ("tr",):
            self._current.start_row()

        if tag_lower in ("td", "th"):
            self._in_cell = True
            self._cell_text = ""
            self._cell_colspan = max(1, int(attr_dict.get("colspan", "1")))
            self._cell_rowspan = max(1, int(attr_dict.get("rowspan", "1")))

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()

        if tag_lower == "table":
            self._current = None
            return

        if self._current is None:
            return

        if tag_lower in ("td", "th") and self._in_cell:
            self._in_cell = False
            bbox = None
            if self._cell_bbox_idx < len(self._cell_bboxes):
                bbox = self._cell_bboxes[self._cell_bbox_idx]
                self._cell_bbox_idx += 1
            self._current.add_cell(
                text=self._cell_text.strip(),
                colspan=self._cell_colspan,
                rowspan=self._cell_rowspan,
                bbox=bbox,
            )

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_text += data

    def handle_entityref(self, name: str) -> None:
        if self._in_cell:
            entities = {"nbsp": " ", "lt": "<", "gt": ">", "amp": "&", "quot": '"'}
            self._cell_text += entities.get(name, f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._in_cell:
            try:
                if name.startswith("x"):
                    self._cell_text += chr(int(name[1:], 16))
                else:
                    self._cell_text += chr(int(name))
            except (ValueError, OverflowError):
                self._cell_text += f"&#{name};"


class _TableBuilder:
    """Accumulates rows and cells during HTML parsing, then normalizes the grid."""

    def __init__(self) -> None:
        self._raw_rows: list[list[dict]] = []
        self._current_row: list[dict] | None = None

    def start_row(self) -> None:
        self._current_row = []
        self._raw_rows.append(self._current_row)

    def add_cell(self, text: str, colspan: int = 1, rowspan: int = 1, bbox: BBox | None = None) -> None:
        if self._current_row is None:
            self.start_row()
        self._current_row.append({"text": text, "colspan": colspan, "rowspan": rowspan, "bbox": bbox})

    def build(
        self, page_no: int = 0, source: str = "", source_block_id: str = "", source_text: str = ""
    ) -> StructuredTable:
        if not self._raw_rows:
            return StructuredTable(page_no=page_no, rows=[], col_count=0, source=source, source_block_id=source_block_id, source_text=source_text)

        max_cols = self._estimate_col_count()
        grid = self._normalize_grid(max_cols)
        rows = self._grid_to_rows(grid)
        return StructuredTable(
            page_no=page_no,
            rows=rows,
            col_count=max_cols,
            source=source,
            source_block_id=source_block_id,
            source_text=source_text,
        )

    def _estimate_col_count(self) -> int:
        max_cols = 0
        for row in self._raw_rows:
            total = sum(c["colspan"] for c in row)
            max_cols = max(max_cols, total)
        return max_cols

    def _normalize_grid(self, total_cols: int) -> list[list[tuple[int, int] | None]]:
        total_rows = len(self._raw_rows)
        for row_idx, row in enumerate(self._raw_rows):
            for cell in row:
                total_rows = max(total_rows, row_idx + cell["rowspan"])

        grid: list[list[tuple[int, int] | None]] = [[None] * total_cols for _ in range(total_rows)]

        for row_idx, raw_row in enumerate(self._raw_rows):
            col_cursor = 0
            for cell_idx, cell_data in enumerate(raw_row):
                while col_cursor < total_cols and grid[row_idx][col_cursor] is not None:
                    col_cursor += 1
                if col_cursor >= total_cols:
                    break

                colspan = cell_data["colspan"]
                rowspan = cell_data["rowspan"]

                for dr in range(rowspan):
                    for dc in range(colspan):
                        r, c = row_idx + dr, col_cursor + dc
                        if r < total_rows and c < total_cols:
                            grid[r][c] = (row_idx, cell_idx)

                col_cursor += colspan

        return grid

    def _grid_to_rows(self, grid: list[list[tuple[int, int] | None]]) -> list[TableRow]:
        rows: list[TableRow] = []
        emitted: set[tuple[int, int]] = set()

        for row_idx, grid_row in enumerate(grid):
            cells: list[TableCell] = []
            for col_idx, anchor in enumerate(grid_row):
                if anchor is None:
                    cells.append(TableCell(row_index=row_idx, col_index=col_idx, text="", colspan=1, rowspan=1))
                    continue

                anchor_row, cell_idx = anchor
                if (anchor_row, cell_idx) in emitted:
                    continue
                emitted.add((anchor_row, cell_idx))

                raw_cell = self._raw_rows[anchor_row][cell_idx]
                cells.append(TableCell(
                    row_index=row_idx,
                    col_index=col_idx,
                    text=raw_cell["text"],
                    colspan=raw_cell["colspan"],
                    rowspan=raw_cell["rowspan"],
                    bbox=raw_cell.get("bbox"),
                ))

            if cells:
                rows.append(TableRow(row_index=row_idx, cells=cells))

        return rows


def parse_html_tables(
    html: str, page_no: int = 0, source: str = "", source_block_id: str = "",
    cell_bboxes: list[BBox] | None = None, source_text: str = "",
) -> list[StructuredTable]:
    parser = TableHTMLParser()
    return parser.parse_tables(html, page_no=page_no, source=source, source_block_id=source_block_id, cell_bboxes=cell_bboxes, source_text=source_text)
