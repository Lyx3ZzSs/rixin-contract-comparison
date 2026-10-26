"""Immutable text-only table snapshots owned by one matching invocation.

Repairs and evidence continue to use the source tables. No cached source object
survives match_tables/align_rows, so a later call always sees repaired values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from app.services.table_compare import utils


@dataclass(frozen=True)
class _Cell:
    row_index: int
    col_index: int
    text: str
    colspan: int


@dataclass(frozen=True)
class _Row:
    row_index: int
    cells: tuple[_Cell, ...]
    section_title: str


@dataclass(frozen=True, eq=False)
class TableMatchSnapshot:
    rows: tuple[_Row, ...]
    col_count: int
    page_no: int
    _cells: Mapping[tuple[int, int], _Cell]
    _text: str
    # The keys and source values are immutable; only memoized results change.
    _normalized: dict[str, str] = field(default_factory=dict, repr=False)
    _features: dict[tuple[str, int], object] = field(default_factory=dict, repr=False)
    _summary_labels: dict[str, tuple[str, ...]] = field(default_factory=dict, repr=False)

    @classmethod
    def from_table(cls, table):
        rows = tuple(_Row(row.row_index, tuple(
            _Cell(cell.row_index, cell.col_index, cell.text, cell.colspan) for cell in row.cells
        ), getattr(row, "section_title", "")) for row in table.rows)
        cells = {}
        for row in rows:
            for cell in row.cells:
                # get_cell returns the first covering cell, including malformed
                # overlaps. anchor_cell then rejects a non-anchor coordinate.
                for col in range(max(0, cell.col_index), min(table.col_count, cell.col_index + cell.colspan)):
                    cells.setdefault((row.row_index, col), cell)
        return cls(rows, table.col_count, table.page_no, MappingProxyType(cells), table.all_cell_text())

    def get_cell(self, row, col):
        return self._cells.get((row, col))

    def all_cell_text(self):
        return self._text

    def normalize(self, text):
        if text not in self._normalized:
            self._normalized[text] = utils.normalize(text)
        return self._normalized[text]

    def summary_labels(self, text):
        if text not in self._summary_labels:
            self._summary_labels[text] = tuple(utils.summary_labels_from_summary_text(text))
        return self._summary_labels[text]

    def table_type(self):
        key = ("table_type", -1)
        if key not in self._features:
            self._features[key] = utils.table_type(self)
        return self._features[key]


def normalize(table, text):
    return table.normalize(text) if isinstance(table, TableMatchSnapshot) else utils.normalize(text)
