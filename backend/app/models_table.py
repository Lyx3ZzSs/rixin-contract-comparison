from __future__ import annotations

from pydantic import BaseModel

from app.models import BBox


class TableCell(BaseModel):
    row_index: int
    col_index: int
    text: str
    colspan: int = 1
    rowspan: int = 1
    bbox: BBox | None = None


class TableRow(BaseModel):
    row_index: int
    cells: list[TableCell]


class StructuredTable(BaseModel):
    page_no: int
    rows: list[TableRow]
    col_count: int
    source_block_id: str = ""
    source: str = ""
    source_text: str = ""
    caption: str = ""
    footnote: str = ""

    @property
    def bbox(self) -> BBox | None:
        return None

    def get_cell(self, row: int, col: int) -> TableCell | None:
        for r in self.rows:
            if r.row_index != row:
                continue
            for cell in r.cells:
                if cell.col_index <= col < cell.col_index + cell.colspan:
                    return cell
        return None

    def all_cell_text(self) -> str:
        parts: list[str] = []
        for row in self.rows:
            for cell in row.cells:
                parts.append(cell.text)
        return " ".join(parts)
