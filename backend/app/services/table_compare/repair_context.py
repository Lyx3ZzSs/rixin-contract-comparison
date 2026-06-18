"""Shared repair context, diagnostics, and business-change guards."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import re

from app.models import BBox
from app.models_table import StructuredTable
from app.services.table_compare import utils
from app.services.table_compare.types import _LogicalRow


@dataclass(frozen=True)
class TableRepairContext:
    table: StructuredTable | None = None
    source_text: str = ""
    cell_bboxes: list[BBox] = field(default_factory=list)
    layout_bbox: BBox | None = None
    table_type: str = "unknown"
    quality: float = 0.0
    block_id: str = ""
    source_text_by_block: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_tables(cls, tables: list[StructuredTable], *, quality: float = 0.0) -> TableRepairContext:
        first = tables[0] if tables else None
        source_text_by_block: dict[str, str] = {}
        cell_bboxes: list[BBox] = []
        for table in tables:
            if table.source_block_id and table.source_text:
                source_text_by_block.setdefault(table.source_block_id, table.source_text)
            for row in table.rows:
                for cell in row.cells:
                    if cell.bbox is not None:
                        cell_bboxes.append(cell.bbox)
        source_text = "\n".join(dict.fromkeys(text for text in source_text_by_block.values() if text))
        return cls(
            table=first,
            source_text=source_text,
            cell_bboxes=cell_bboxes,
            layout_bbox=first.bbox if first is not None else None,
            table_type=utils.table_type(first) if first is not None else "unknown",
            quality=quality,
            block_id=first.source_block_id if first is not None else "",
            source_text_by_block=source_text_by_block,
        )

    def source_text_for_block(self, block_id: str, fallback: str = "") -> str:
        return self.source_text_by_block.get(block_id) or fallback or self.source_text

    def source_text_for_row(self, row: _LogicalRow | None) -> str:
        if row is None:
            return self.source_text
        return self.source_text_for_block(row.source_block_id, row.source_text)


class RepairDiagnosticsMixin:
    """Records repair decisions without changing repair behavior."""

    def __init__(self) -> None:
        self._diagnostic_side = ""
        self._repair_decisions: list[dict[str, object]] = []
        self._active_context: TableRepairContext | None = None

    def reset_diagnostics(self, side: str = "") -> None:
        self._diagnostic_side = side
        self._repair_decisions = []

    def diagnostics_payload(self) -> dict[str, object]:
        return {
            "side": self._diagnostic_side,
            "decision_count": len(self._repair_decisions),
            "decisions": list(self._repair_decisions),
        }

    def _activate_context(self, context: TableRepairContext) -> None:
        self._active_context = context

    def _source_text_for_row(
        self,
        row: _LogicalRow | None,
        context: TableRepairContext | None = None,
    ) -> str:
        active_context = context or self._active_context
        if active_context is not None:
            return active_context.source_text_for_row(row)
        return row.source_text if row is not None else ""

    def _source_text_for_block(
        self,
        block_id: str,
        fallback: str = "",
        context: TableRepairContext | None = None,
    ) -> str:
        active_context = context or self._active_context
        if active_context is not None:
            return active_context.source_text_for_block(block_id, fallback)
        return fallback

    def _record_repair_decision(
        self,
        repair_type: str,
        *,
        before: list[_LogicalRow],
        after: list[_LogicalRow],
        reason: str,
        confidence: float = 1.0,
        signals: dict[str, object] | None = None,
    ) -> None:
        if len(self._repair_decisions) >= 500:
            return
        source_row = before[0] if before else after[0] if after else None
        self._repair_decisions.append({
            "side": self._diagnostic_side,
            "repair_type": repair_type,
            "source_block_id": source_row.source_block_id if source_row is not None else "",
            "row_index": source_row.row_index if source_row is not None else None,
            "reason": reason,
            "confidence": round(confidence, 4),
            "signals": signals or {},
            "before_metrics": self._rows_metrics(before),
            "after_metrics": self._rows_metrics(after),
            "before": [self._row_debug_payload(row) for row in before],
            "after": [self._row_debug_payload(row) for row in after],
        })

    def _row_debug_payload(self, row: _LogicalRow) -> dict[str, object]:
        cells = [
            {
                "col": cell.col_index,
                "text": cell.text,
                "colspan": cell.colspan,
                "rowspan": cell.rowspan,
            }
            for cell in sorted(row.cells, key=lambda item: item.col_index)
            if utils.normalize(cell.text)
        ]
        return {
            "row_index": row.row_index,
            "page_no": row.page_no,
            "source_block_id": row.source_block_id,
            "source_row": row.source_row,
            "section_title": row.section_title,
            "cells": cells,
            "text": " | ".join(str(cell["text"]) for cell in cells),
        }

    def _rows_metrics(self, rows: Iterable[_LogicalRow]) -> dict[str, object]:
        row_list = list(rows)
        total_cells = sum(len(row.cells) for row in row_list)
        nonempty_cells = sum(
            1
            for row in row_list
            for cell in row.cells
            if utils.normalize(cell.text)
        )
        bbox_cells = sum(
            1
            for row in row_list
            for cell in row.cells
            if cell.bbox is not None
        )
        col_indexes = {
            cell.col_index
            for row in row_list
            for cell in row.cells
        }
        return {
            "row_count": len(row_list),
            "cell_count": total_cells,
            "nonempty_cell_count": nonempty_cells,
            "bbox_cell_count": bbox_cells,
            "col_count": len(col_indexes),
            "fill_rate": round(nonempty_cells / total_cells, 4) if total_cells else 0.0,
            "bbox_coverage": round(bbox_cells / total_cells, 4) if total_cells else 0.0,
        }


class BusinessChangeProtector:
    """Centralized guard for high-value business field changes."""

    _YES_NO_VALUES = {"是", "否", "有", "无", "已", "未"}
    _CHINESE_DIGITS = {
        "零": "0",
        "〇": "0",
        "一": "1",
        "二": "2",
        "两": "2",
        "三": "3",
        "四": "4",
        "五": "5",
        "六": "6",
        "七": "7",
        "八": "8",
        "九": "9",
        "十": "10",
    }

    @classmethod
    def protected_change_reason(cls, original_text: str, compare_text: str) -> str | None:
        original_norm = utils.normalize_cell_for_compare(original_text)
        compare_norm = utils.normalize_cell_for_compare(compare_text)
        if not original_norm or not compare_norm or original_norm == compare_norm:
            return None

        for prefix, reason in (
            ("amount:", "amount_change"),
            ("quantity:", "quantity_change"),
            ("date:", "date_change"),
        ):
            if original_norm.startswith(prefix) and compare_norm.startswith(prefix):
                return reason

        original_simple = cls._business_scalar(original_text)
        compare_simple = cls._business_scalar(compare_text)
        if (
            original_simple
            and compare_simple
            and (
                original_simple != compare_simple
                or utils.normalize(original_text) != utils.normalize(compare_text)
            )
        ):
            return "business_scalar_change"
        return None

    @classmethod
    def is_protected_change(cls, original_text: str, compare_text: str) -> bool:
        return cls.protected_change_reason(original_text, compare_text) is not None

    @classmethod
    def _business_scalar(cls, text: str) -> str:
        compact = re.sub(r"\s+", "", text or "")
        if compact in cls._YES_NO_VALUES:
            return compact
        if compact in cls._CHINESE_DIGITS:
            return cls._CHINESE_DIGITS[compact]
        if re.fullmatch(r"\d{1,2}", compact):
            return str(int(compact))
        return ""
