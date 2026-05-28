from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher

from app.models import BBox, CharBox, DiffItem, Document, EvidenceBox, TextBlock
from app.models_table import StructuredTable
from app.services.table_html_parser import parse_html_tables
from app.utils.id_utils import generate_diff_id

logger = logging.getLogger(__name__)

NOISE_PATTERN = re.compile(r"^(共\d+页第\d+页|第?\d+页)$")
SUMMARY_LABEL_PATTERN = re.compile(
    r"(?:(?<!\d)\d{1,3}(?:套|项|台|个|批|份|件|年|月)?(?:总合计|总计|合计)|小计|总合计|总计|合计)"
)
AMOUNT_TOKEN_PATTERN = re.compile(r"(?:人民币|[¥￥])?[+-]?\d[\d,]*(?:\.\d+)?(?:万)?元?")
TABLE_HEADERS = {"序号", "产品名称", "详细配置", "品牌", "单位", "数量", "单价", "金额", "备注"}
TABLE_BLOCK_TYPES = {"table", "table_title", "table_cell"}
TABLE_TYPE_LABELS = {
    "cover": "封面信息",
    "product": "标的物",
    "payment": "付款节点",
    "acceptance": "验收标准",
    "contact": "联系人",
    "generic": "通用表格",
}


class TableComparator:
    table_block_types = TABLE_BLOCK_TYPES
    cell_similarity_threshold: float = 0.85
    row_similarity_threshold: float = 0.55
    table_similarity_threshold: float = 0.3

    def build_diffs(
        self, original: Document, compare: Document, start_index: int = 1
    ) -> tuple[list[DiffItem], list[str]]:
        warnings: list[str] = []

        original_blocks = self._table_blocks(original)
        compare_blocks = self._table_blocks(compare)
        if not original_blocks and not compare_blocks:
            if any(page.blocks for page in [*original.pages, *compare.pages]):
                warnings.append("未获得结构化表格区域，已跳过表格比对。")
            return [], warnings

        original_tables = self._stitch_logical_tables(self._parse_tables(original_blocks))
        compare_tables = self._stitch_logical_tables(self._parse_tables(compare_blocks))

        if original_tables or compare_tables:
            return self._compare_tables(
                original_tables, compare_tables, original_blocks, compare_blocks, start_index, warnings
            )

        # Fallback: flat-text comparison for blocks without HTML
        return self._flat_compare(original_blocks, compare_blocks, start_index, warnings)

    # --- Table extraction ---

    def _table_blocks(self, document: Document) -> list[tuple[TextBlock, str]]:
        result = []
        for page in document.pages:
            for block in page.blocks:
                if self.is_table_block(block):
                    text = block.raw_html or block.text or ""
                    result.append((block, text))
        return result

    def _parse_tables(self, blocks: list[tuple[TextBlock, str]]) -> list[StructuredTable]:
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

    def _stitch_logical_tables(self, tables: list[StructuredTable]) -> list[_LogicalTable]:
        """Merge consecutive product-list table fragments into logical tables."""
        logical: list[_LogicalTable] = []
        pending: list[StructuredTable] = []

        def flush_pending() -> None:
            nonlocal pending
            if pending:
                table = self._build_logical_table(pending)
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
            logical_table = self._build_logical_table([table])
            if logical_table.rows:
                logical.append(logical_table)
        flush_pending()
        return logical

    def _build_logical_table(self, tables: list[StructuredTable]) -> _LogicalTable:
        rows: list[_LogicalRow] = []
        current_section = ""
        col_count = max((table.col_count for table in tables), default=0)
        logical_index = 0

        for table in tables:
            for row in table.rows:
                cells = self._logical_cells(table, row.row_index, logical_index)
                if not cells:
                    continue
                row_text = self._normalize(" ".join(cell.text for cell in cells if cell.text))
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

        rows = self._normalize_merged_sequence_rows(rows, col_count)
        rows = self._repair_merged_adjacent_sequence_rows(rows, col_count)
        rows = self._repair_product_continuation_rows(rows, col_count)
        rows = self._repair_shifted_product_field_rows(rows, col_count)
        rows = self._repair_embedded_summary_transitions(rows, col_count)
        rows = self._normalize_summary_rows(rows, col_count, tables)
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
            cell = self._anchor_cell(table, row, col)
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

    def _normalize_merged_sequence_rows(self, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
        normalized: list[_LogicalRow] = []
        index = 0
        while index < len(rows):
            row = rows[index]
            sequence_info = self._merged_sequence_info(row)
            if sequence_info is None:
                normalized.append(row)
                index += 1
                continue

            sequence_col, sequences = sequence_info
            continuation_rows = self._continuation_rows(rows, index + 1, len(sequences))
            split_rows = self._split_merged_sequence_row(row, sequence_col, sequences, continuation_rows, col_count)
            if split_rows is None:
                normalized.append(row)
                index += 1
                continue

            normalized.extend(split_rows)
            index += 1 + len(continuation_rows)

        return self._reindex_logical_rows(normalized)

    def _merged_sequence_info(self, row: _LogicalRow) -> tuple[int, list[str]] | None:
        for cell in row.cells:
            parts = self._split_sequence_cell(cell.text)
            if len(parts) >= 2:
                return cell.col_index, parts
        return None

    def _split_sequence_cell(self, text: str) -> list[str]:
        raw = unicodedata.normalize("NFKC", text or "").strip()
        if not re.fullmatch(r"\d{1,3}(?:[\s,，、/]+(?:\d{1,3}))+", raw):
            return []
        values = [int(part) for part in re.findall(r"\d{1,3}", raw)]
        if len(values) < 2 or len(values) > 6:
            return []
        for left, right in zip(values, values[1:]):
            if right != left + 1:
                return []
        return [str(value) for value in values]

    def _continuation_rows(self, rows: list[_LogicalRow], start: int, split_count: int) -> list[_LogicalRow]:
        continuation: list[_LogicalRow] = []
        for row in rows[start:start + max(0, split_count - 1)]:
            if self._row_sequence_from_cells(row.cells):
                break
            if not self._looks_like_merged_row_continuation(row):
                break
            continuation.append(row)
        return continuation

    def _looks_like_merged_row_continuation(self, row: _LogicalRow) -> bool:
        nonempty = [cell for cell in row.cells if self._normalize(cell.text)]
        if not nonempty:
            return False
        if len(nonempty) > 5:
            return False
        texts = [self._normalize_cell_for_compare(cell.text) for cell in nonempty]
        if any(self._is_number_like(text) for text in texts) and len(nonempty) == 1:
            return False
        return True

    def _split_merged_sequence_row(
        self,
        row: _LogicalRow,
        sequence_col: int,
        sequences: list[str],
        continuation_rows: list[_LogicalRow],
        col_count: int,
    ) -> list[_LogicalRow] | None:
        split_count = len(sequences)
        per_col_parts: dict[int, list[str]] = {sequence_col: sequences}
        split_cols = {sequence_col}

        for cell in row.cells:
            if cell.col_index == sequence_col:
                continue
            parts = self._split_cell_text_for_row_count(cell.text, split_count)
            if parts:
                per_col_parts[cell.col_index] = parts
                split_cols.add(cell.col_index)

        if len(split_cols) <= 1 and not continuation_rows:
            return None

        continuation_shift = max(split_cols) + 1 if split_cols else sequence_col + 1
        result: list[_LogicalRow] = []
        for part_index in range(split_count):
            continuation = continuation_rows[part_index - 1] if part_index > 0 and part_index - 1 < len(continuation_rows) else None
            cells: list[_LogicalCell] = []
            for col in range(col_count):
                source_cell = self._cell_at_col(row, col)
                text = ""
                if col in per_col_parts:
                    text = per_col_parts[col][part_index]
                elif source_cell is not None:
                    text = source_cell.text

                continuation_cell = self._cell_at_col(continuation, col - continuation_shift) if continuation else None
                if continuation_cell is not None and self._normalize(continuation_cell.text):
                    cont_text = continuation_cell.text
                    if col not in per_col_parts or not self._normalize(text) or self._normalize(text) == self._normalize(cont_text):
                        text = cont_text
                        source_cell = continuation_cell

                if source_cell is None and not text:
                    continue
                template = source_cell or row.cells[0]
                cells.append(_LogicalCell(
                    row_index=part_index,
                    col_index=col,
                    text=text,
                    bbox=template.bbox,
                    page_no=template.page_no,
                    source_block_id=template.source_block_id,
                    source_row=template.source_row,
                    source_col=template.source_col,
                    colspan=template.colspan,
                    rowspan=template.rowspan,
                ))

            result.append(_LogicalRow(
                row_index=part_index,
                cells=cells,
                page_no=row.page_no,
                source_block_id=row.source_block_id,
                source_row=row.source_row,
                section_title=row.section_title,
                source_text=row.source_text,
            ))
        return result

    def _split_cell_text_for_row_count(self, text: str, count: int) -> list[str]:
        raw = unicodedata.normalize("NFKC", text or "").strip()
        if not raw or count < 2:
            return []
        sentence_parts = [
            part.strip()
            for part in re.findall(r"[^。；;]+[。；;]?", raw)
            if part.strip()
        ]
        if len(sentence_parts) == count and all(self._normalize(part) for part in sentence_parts):
            return sentence_parts
        if re.search(r"[。；;]", raw):
            return []

        whitespace_parts = [part.strip() for part in re.split(r"\s+", raw) if part.strip()]
        if len(whitespace_parts) == count:
            return whitespace_parts
        return []

    def _cell_at_col(self, row: _LogicalRow | None, col: int) -> _LogicalCell | None:
        if row is None or col < 0:
            return None
        for cell in row.cells:
            if cell.col_index == col:
                return cell
        return None

    def _row_sequence_from_cells(self, cells: list[_LogicalCell]) -> str:
        nonempty = [cell for cell in cells if self._normalize(cell.text)]
        if not nonempty:
            return ""
        text = self._normalize(nonempty[0].text)
        if re.fullmatch(r"\d{1,3}", text):
            return text
        return ""

    def _reindex_logical_rows(self, rows: list[_LogicalRow]) -> list[_LogicalRow]:
        reindexed: list[_LogicalRow] = []
        for row_index, row in enumerate(rows):
            cells = [
                _LogicalCell(
                    row_index=row_index,
                    col_index=cell.col_index,
                    text=cell.text,
                    bbox=cell.bbox,
                    page_no=cell.page_no,
                    source_block_id=cell.source_block_id,
                    source_row=cell.source_row,
                    source_col=cell.source_col,
                    colspan=cell.colspan,
                    rowspan=cell.rowspan,
                )
                for cell in row.cells
            ]
            reindexed.append(_LogicalRow(
                row_index=row_index,
                cells=cells,
                page_no=row.page_no,
                source_block_id=row.source_block_id,
                source_row=row.source_row,
                section_title=row.section_title,
                source_text=row.source_text,
            ))
        return reindexed

    def _repair_merged_adjacent_sequence_rows(self, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
        if col_count < 6:
            return rows

        repaired: list[_LogicalRow] = []
        index = 0
        while index < len(rows):
            row = rows[index]
            current_seq = self._row_sequence_int(row)
            next_seq = self._next_sequence_int(rows, index + 1)
            if current_seq is None or next_seq != current_seq + 2:
                repaired.append(row)
                index += 1
                continue

            missing_seq = current_seq + 1
            split_rows = self._split_adjacent_sequence_merged_row(row, missing_seq, col_count)
            if split_rows is None:
                repaired.append(row)
            else:
                repaired.extend(split_rows)
            index += 1

        return self._reindex_logical_rows(repaired)

    def _split_adjacent_sequence_merged_row(
        self,
        row: _LogicalRow,
        missing_seq: int,
        col_count: int,
    ) -> list[_LogicalRow] | None:
        candidate = self._missing_sequence_candidate_from_source(row.source_text, missing_seq)
        if candidate is None:
            return None

        name_text = self._cell_text_from_row(row, 1)
        detail_text = self._cell_text_from_row(row, 2)
        brand_text = self._cell_text_from_row(row, 3)

        missing_name = self._merged_name_suffix(name_text, detail_text, candidate["name"])
        if not missing_name:
            missing_name = candidate["name"]

        merged_name_norm = self._normalize(name_text)
        current_detail_norm = self._normalize(detail_text)
        missing_name_norm = self._normalize(missing_name)
        source_supports_name = self._source_contains_token(
            self._normalize(row.source_text),
            missing_name_norm,
            allow_loose_cjk=True,
        )
        row_contains_missing = bool(missing_name_norm and missing_name_norm in merged_name_norm)
        row_contains_current = bool(current_detail_norm and current_detail_norm in merged_name_norm)
        brand_repeated = self._has_repeated_cell_value(brand_text)

        if not source_supports_name:
            return None
        if not (row_contains_missing and row_contains_current):
            return None
        if not (brand_repeated or candidate["brand"] and self._normalize(candidate["brand"]) in self._normalize(brand_text)):
            return None
        kept_cells: list[_LogicalCell] = []
        missing_cells: list[_LogicalCell] = []
        current_name = self._remove_merged_name_suffix(name_text, missing_name) or detail_text or name_text
        current_brand = self._dedupe_repeated_cell_text(brand_text)

        for col in range(col_count):
            source_cell = self._cell_at_col(row, col) or row.cells[0]
            kept_text = self._cell_text_from_row(row, col)
            missing_text = ""
            if col == 0:
                kept_text = str(self._row_sequence_int(row) or kept_text)
                missing_text = str(missing_seq)
            elif col == 1:
                kept_text = current_name
                missing_text = missing_name
            elif col == 2:
                missing_text = candidate["detail"]
            elif col == 3:
                kept_text = current_brand
                missing_text = candidate["brand"]
            elif col == 4:
                missing_text = candidate["unit"]
            elif col == 5:
                missing_text = candidate["quantity"]

            if kept_text or self._cell_at_col(row, col) is not None:
                kept_cells.append(self._clone_logical_cell(source_cell, row_index=0, col_index=col, text=kept_text))
            if missing_text:
                missing_cells.append(self._clone_logical_cell(source_cell, row_index=1, col_index=col, text=missing_text))

        if len([cell for cell in missing_cells if self._normalize(cell.text)]) < 5:
            return None

        return [
            _LogicalRow(
                row_index=0,
                cells=kept_cells,
                page_no=row.page_no,
                source_block_id=row.source_block_id,
                source_row=row.source_row,
                section_title=row.section_title,
                source_text=row.source_text,
            ),
            _LogicalRow(
                row_index=1,
                cells=missing_cells,
                page_no=row.page_no,
                source_block_id=row.source_block_id,
                source_row=row.source_row,
                section_title=row.section_title,
                source_text=row.source_text,
            ),
        ]

    def _missing_sequence_candidate_from_source(self, source_text: str, missing_seq: int) -> dict[str, str] | None:
        tokens = self._source_line_tokens(source_text)
        if not tokens:
            return None
        for index, token in enumerate(tokens):
            if self._normalize(token) != str(missing_seq):
                continue
            window = tokens[index + 1:index + 10]
            detail = self._first_detail_token(window)
            if not detail:
                continue
            detail_index = window.index(detail)
            brand = self._first_brand_token(window[detail_index + 1:])
            unit = self._first_unit_token(window[detail_index + 1:])
            if not unit:
                continue
            unit_index = window.index(unit)
            quantity = self._first_quantity_token(window[unit_index + 1:])
            if not quantity:
                continue

            name = detail
            previous = tokens[index - 1] if index > 0 else ""
            trailing = ""
            if unit_index + 1 < len(window):
                after_unit = window[unit_index + 1]
                if self._looks_like_row_name_fragment(after_unit):
                    trailing = after_unit
            if self._looks_like_row_name_fragment(previous):
                previous_norm = self._normalize(previous)
                detail_norm = self._normalize(detail)
                if previous_norm and previous_norm in detail_norm and len(detail_norm) > len(previous_norm):
                    name = detail
                else:
                    name = f"{previous} {trailing}".strip() if trailing else previous

            return {
                "name": name,
                "detail": detail,
                "brand": brand,
                "unit": unit,
                "quantity": quantity,
            }
        return None

    def _source_line_tokens(self, source_text: str) -> list[str]:
        raw = unicodedata.normalize("NFKC", source_text or "")
        tokens = [line.strip() for line in raw.splitlines() if line.strip()]
        if len(tokens) <= 1:
            tokens = [part.strip() for part in re.split(r"[\s|]+", raw) if part.strip()]
        return tokens

    def _first_detail_token(self, tokens: list[str]) -> str:
        for token in tokens:
            norm = self._normalize(token)
            if self._is_source_row_field_noise(norm):
                continue
            if len(norm) >= 4:
                return token
        return ""

    def _first_brand_token(self, tokens: list[str]) -> str:
        for token in tokens:
            norm = self._normalize(token)
            if self._is_source_row_field_noise(norm):
                continue
            if 2 <= len(norm) <= 12:
                return token
        return ""

    @staticmethod
    def _first_unit_token(tokens: list[str]) -> str:
        units = {"套", "台", "个", "项", "批", "份", "件", "年", "月", "天", "人天"}
        for token in tokens:
            if unicodedata.normalize("NFKC", token or "").strip() in units:
                return token
        return ""

    def _first_quantity_token(self, tokens: list[str]) -> str:
        for token in tokens:
            norm = self._normalize(token)
            if re.fullmatch(r"\d{1,3}(?:\.\d+)?", norm) and not self._looks_like_amount_value(f"amount:{self._canonical_amount(token)}"):
                return token
        return ""

    def _is_source_row_field_noise(self, norm: str) -> bool:
        return (
            not norm
            or self._is_number_like(norm)
            or norm in {"套", "台", "个", "项", "批", "份", "件", "年", "月", "天", "人天"}
            or bool(self._canonical_amount(norm))
        )

    def _looks_like_row_name_fragment(self, text: str) -> bool:
        norm = self._normalize(text)
        if len(norm) < 2 or len(norm) > 20:
            return False
        if self._is_source_row_field_noise(norm):
            return False
        return bool(re.search(r"[\u4e00-\u9fffA-Za-z]", text or ""))

    def _merged_name_suffix(self, name_text: str, current_text: str, fallback: str) -> str:
        name_norm = self._normalize(name_text)
        current_norm = self._normalize(current_text)
        fallback_norm = self._normalize(fallback)
        if fallback_norm and fallback_norm in name_norm and fallback_norm != current_norm:
            return fallback
        if current_norm and name_norm.startswith(current_norm) and len(name_norm) > len(current_norm) + 1:
            return name_norm[len(current_norm):]
        return fallback if fallback_norm and fallback_norm in name_norm else ""

    def _remove_merged_name_suffix(self, name_text: str, suffix: str) -> str:
        if not name_text or not suffix:
            return name_text
        name_norm = self._normalize(name_text)
        suffix_norm = self._normalize(suffix)
        if suffix_norm and name_norm.endswith(suffix_norm):
            compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", name_text))
            if compact.endswith(suffix_norm):
                return compact[:len(compact) - len(suffix_norm)]
        return name_text

    def _has_repeated_cell_value(self, text: str) -> bool:
        return self._dedupe_repeated_cell_text(text) != text

    def _dedupe_repeated_cell_text(self, text: str) -> str:
        raw = unicodedata.normalize("NFKC", text or "").strip()
        if not raw:
            return text
        parts = [part for part in re.split(r"\s+", raw) if part]
        if len(parts) == 2 and self._normalize(parts[0]) == self._normalize(parts[1]):
            return parts[0]
        compact = self._normalize(raw)
        if len(compact) % 2 == 0 and compact[:len(compact) // 2] == compact[len(compact) // 2:]:
            return compact[:len(compact) // 2]
        return text

    def _row_sequence_int(self, row: _LogicalRow) -> int | None:
        sequence = self._row_sequence_from_cells(row.cells)
        if not sequence:
            return None
        try:
            return int(sequence)
        except ValueError:
            return None

    def _next_sequence_int(self, rows: list[_LogicalRow], start: int) -> int | None:
        for row in rows[start:]:
            sequence = self._row_sequence_int(row)
            if sequence is not None:
                return sequence
            if self._summary_label_from_row(row) or self._section_title(row.cells, len(row.cells)):
                return None
        return None

    def _cell_text_from_row(self, row: _LogicalRow, col: int) -> str:
        cell = self._cell_at_col(row, col)
        return cell.text if cell is not None else ""

    def _clone_logical_cell(self, source: _LogicalCell, row_index: int, col_index: int, text: str) -> _LogicalCell:
        return _LogicalCell(
            row_index=row_index,
            col_index=col_index,
            text=text,
            bbox=source.bbox,
            page_no=source.page_no,
            source_block_id=source.source_block_id,
            source_row=source.source_row,
            source_col=source.source_col,
            colspan=1,
            rowspan=1,
        )

    def _repair_product_continuation_rows(self, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
        if col_count < 6:
            return rows

        repaired: list[_LogicalRow] = []
        for row in rows:
            if repaired and self._is_product_continuation_row(row, col_count):
                merged = self._merge_product_continuation_row(repaired[-1], row, col_count)
                if merged is not None:
                    repaired[-1] = merged
                    continue
            repaired.append(row)
        return self._reindex_logical_rows(repaired)

    def _is_product_continuation_row(self, row: _LogicalRow, col_count: int) -> bool:
        if self._row_sequence_int(row) is not None:
            return False
        if self._summary_label_from_row(row) or self._section_title(row.cells, col_count):
            return False
        nonempty = [cell for cell in row.cells if self._normalize(cell.text)]
        if not nonempty or len(nonempty) > 3:
            return False
        texts = [self._normalize_cell_for_compare(cell.text) for cell in nonempty]
        if all(self._is_number_like(text) or self._canonical_amount(text) for text in texts):
            return False
        return any(len(text) >= 4 and re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text) for text in texts)

    def _merge_product_continuation_row(
        self,
        previous: _LogicalRow,
        continuation: _LogicalRow,
        col_count: int,
    ) -> _LogicalRow | None:
        if self._row_sequence_int(previous) is None:
            return None
        source_norm = self._normalize("\n".join(
            text for text in (previous.source_text, continuation.source_text) if text
        ))
        if not source_norm:
            return None

        cells_by_col = {
            cell.col_index: self._clone_logical_cell(cell, previous.row_index, cell.col_index, cell.text)
            for cell in previous.cells
        }
        changed = False
        for cont_cell in continuation.cells:
            cont_text = unicodedata.normalize("NFKC", cont_cell.text or "").strip()
            if not self._normalize(cont_text):
                continue
            target_col = self._continuation_target_col(previous, cont_cell, source_norm, col_count)
            if target_col is None:
                return None
            prev_cell = cells_by_col.get(target_col) or self._cell_at_col(previous, target_col)
            if prev_cell is None:
                return None
            merged_text = self._join_continuation_text(prev_cell.text, cont_text)
            cells_by_col[target_col] = self._clone_logical_cell(prev_cell, previous.row_index, target_col, merged_text)
            changed = True

        if not changed:
            return None
        return _LogicalRow(
            row_index=previous.row_index,
            cells=list(cells_by_col.values()),
            page_no=previous.page_no,
            source_block_id=previous.source_block_id,
            source_row=previous.source_row,
            section_title=previous.section_title,
            source_text=previous.source_text,
        )

    def _continuation_target_col(
        self,
        previous: _LogicalRow,
        continuation_cell: _LogicalCell,
        source_norm: str,
        col_count: int,
    ) -> int | None:
        candidate_cols = [continuation_cell.col_index]
        if continuation_cell.col_index != 2 and col_count > 2:
            candidate_cols.append(2)
        for col in candidate_cols:
            prev_text = self._cell_text_from_row(previous, col)
            if not self._normalize(prev_text):
                continue
            if self._source_supports_text_join(source_norm, prev_text, continuation_cell.text):
                return col
        return None

    def _source_supports_text_join(self, source_norm: str, left: str, right: str) -> bool:
        left_norm = self._normalize(left)
        right_norm = self._normalize(right)
        if not left_norm or not right_norm:
            return False
        joined = self._normalize(f"{left_norm}{right_norm}")
        spaced = self._normalize(f"{left_norm} {right_norm}")
        folded_source = self._punctuation_fold(source_norm)
        return (
            joined in source_norm
            or spaced in source_norm
            or self._punctuation_fold(joined) in folded_source
            or self._punctuation_fold(spaced) in folded_source
            or any(
                self._normalize(f"{part}{right_norm}") in source_norm
                or self._punctuation_fold(self._normalize(f"{part}{right_norm}")) in folded_source
                for part in self._source_line_tokens(left)
                if len(self._normalize(part)) >= 4
            )
        )

    def _join_continuation_text(self, left: str, right: str) -> str:
        left_raw = unicodedata.normalize("NFKC", left or "").strip()
        right_raw = unicodedata.normalize("NFKC", right or "").strip()
        if not left_raw:
            return right_raw
        if not right_raw:
            return left_raw
        if re.search(r"[\w\u4e00-\u9fff]$", left_raw) and re.search(r"^[\w\u4e00-\u9fff]", right_raw):
            return f"{left_raw} {right_raw}"
        return f"{left_raw}{right_raw}"

    def _repair_shifted_product_field_rows(self, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
        if col_count < 8:
            return rows

        repaired: list[_LogicalRow] = []
        for row in rows:
            repaired.append(self._repair_shifted_product_field_row(row, col_count) or row)
        return self._reindex_logical_rows(repaired)

    def _repair_shifted_product_field_row(self, row: _LogicalRow, col_count: int) -> _LogicalRow | None:
        if self._row_sequence_int(row) is None:
            return None
        name = self._cell_text_from_row(row, 1)
        shifted_brand = self._cell_text_from_row(row, 2)
        shifted_unit = self._cell_text_from_row(row, 3)
        shifted_quantity = self._cell_text_from_row(row, 4)
        shifted_price = self._cell_text_from_row(row, 5)
        shifted_amount = self._cell_text_from_row(row, 6)
        misplaced_detail_cell = self._misplaced_detail_cell(row, col_count)
        if misplaced_detail_cell is None:
            return None
        if not self._looks_like_shifted_product_fields(
            name,
            shifted_brand,
            shifted_unit,
            shifted_quantity,
            shifted_price,
            shifted_amount,
        ):
            return None

        detail_text = self._detail_text_from_shifted_row(row, misplaced_detail_cell)
        if not detail_text:
            return None
        if not self._source_supports_shifted_product_row(
            row,
            detail_text,
            shifted_brand,
            shifted_unit,
            shifted_quantity,
            shifted_price,
            shifted_amount,
        ):
            return None

        cells: list[_LogicalCell] = []
        templates = {cell.col_index: cell for cell in row.cells}
        for col, text in (
            (0, self._cell_text_from_row(row, 0)),
            (1, name),
            (2, detail_text),
            (3, shifted_brand),
            (4, shifted_unit),
            (5, shifted_quantity),
            (6, shifted_price),
            (7, shifted_amount),
        ):
            template = templates.get(col) or misplaced_detail_cell or row.cells[0]
            cells.append(self._clone_logical_cell(template, row.row_index, col, text))
        for col in range(8, col_count):
            if col == misplaced_detail_cell.col_index:
                continue
            text = self._cell_text_from_row(row, col)
            if text:
                template = templates.get(col) or row.cells[0]
                cells.append(self._clone_logical_cell(template, row.row_index, col, text))

        return _LogicalRow(
            row_index=row.row_index,
            cells=cells,
            page_no=row.page_no,
            source_block_id=row.source_block_id,
            source_row=row.source_row,
            section_title=row.section_title,
            source_text=row.source_text,
        )

    def _misplaced_detail_cell(self, row: _LogicalRow, col_count: int) -> _LogicalCell | None:
        for col in range(7, col_count):
            cell = self._cell_at_col(row, col)
            if cell is None:
                continue
            text = self._normalize(cell.text)
            if not text or self._is_number_like(text) or self._canonical_amount(cell.text):
                continue
            if len(text) >= 6 and re.search(r"[\u4e00-\u9fffA-Za-z]", text):
                return cell
        return None

    def _looks_like_shifted_product_fields(
        self,
        name: str,
        brand: str,
        unit: str,
        quantity: str,
        price: str,
        amount: str,
    ) -> bool:
        if not self._normalize(name) or not self._looks_like_brand_value(brand):
            return False
        if not self._first_unit_token([unit]):
            return False
        if not re.fullmatch(r"\d{1,4}(?:\.\d+)?", self._normalize(quantity)):
            return False
        price_amount = self._canonical_amount(price)
        total_amount = self._canonical_amount(amount)
        return bool(price_amount and total_amount)

    def _looks_like_brand_value(self, text: str) -> bool:
        norm = self._normalize(text)
        if len(norm) < 2 or len(norm) > 20:
            return False
        if self._is_number_like(norm) or self._canonical_amount(text) or self._first_unit_token([text]):
            return False
        return bool(re.search(r"[\u4e00-\u9fffA-Za-z]", text or ""))

    def _detail_text_from_shifted_row(self, row: _LogicalRow, detail_cell: _LogicalCell) -> str:
        detail = unicodedata.normalize("NFKC", detail_cell.text or "").strip()
        prefix = self._source_detail_prefix_for_shifted_row(row, detail)
        if prefix and self._normalize(prefix) not in self._normalize(detail):
            return f"{prefix} {detail}".strip()
        return detail

    def _source_detail_prefix_for_shifted_row(self, row: _LogicalRow, detail_text: str) -> str:
        tokens = self._source_line_tokens(row.source_text)
        if not tokens:
            return ""
        detail_parts = [part for part in self._source_line_tokens(detail_text) if self._normalize(part)]
        if not detail_parts:
            detail_parts = [detail_text]
        name_norm = self._normalize(self._cell_text_from_row(row, 1))
        brand_norm = self._normalize(self._cell_text_from_row(row, 2))
        field_norms = {
            name_norm,
            brand_norm,
            self._normalize(self._cell_text_from_row(row, 3)),
            self._normalize(self._cell_text_from_row(row, 4)),
            self._normalize(self._cell_text_from_row(row, 5)),
            self._normalize(self._cell_text_from_row(row, 6)),
            self._normalize(self._cell_text_from_row(row, 0)),
        }
        for token_index, token in enumerate(tokens):
            token_norm = self._normalize(token)
            if not any(part and self._source_contains_token(token_norm, self._normalize(part), True) for part in detail_parts):
                continue
            if token_index == 0:
                return ""
            prefix = unicodedata.normalize("NFKC", tokens[token_index - 1] or "").strip()
            prefix_norm = self._normalize(prefix)
            if not prefix_norm or prefix_norm in field_norms:
                return ""
            if self._is_source_row_field_noise(prefix_norm):
                return ""
            if brand_norm and prefix_norm == brand_norm:
                return ""
            if name_norm and name_norm in prefix_norm:
                return prefix
        return ""

    def _source_supports_shifted_product_row(
        self,
        row: _LogicalRow,
        detail: str,
        brand: str,
        unit: str,
        quantity: str,
        price: str,
        amount: str,
    ) -> bool:
        source_norm = self._normalize(row.source_text)
        if not source_norm:
            return False
        checks = [
            (detail, True),
            (brand, False),
            (unit, False),
            (quantity, False),
            (price, False),
            (amount, False),
        ]
        for text, loose in checks:
            if text and not self._source_contains_token(source_norm, self._normalize(text), allow_loose_cjk=loose):
                return False
        return True

    def _repair_embedded_summary_transitions(self, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
        if col_count < 6:
            return rows

        repaired: list[_LogicalRow] = []
        pending_section = ""
        index = 0
        while index < len(rows):
            row = rows[index]
            next_row = rows[index + 1] if index + 1 < len(rows) else None
            split = self._split_embedded_summary_transition(row, next_row, col_count)
            if split is not None:
                product_row, summary_row, next_section, consume_next = split
                repaired.append(product_row)
                repaired.append(summary_row)
                pending_section = next_section
                index += 2 if consume_next else 1
                continue

            if pending_section and self._row_sequence_int(row) is not None:
                row = self._clone_logical_row(row, section_title=pending_section)
            repaired.append(row)
            index += 1

        return self._reindex_logical_rows(repaired)

    def _split_embedded_summary_transition(
        self,
        row: _LogicalRow,
        next_row: _LogicalRow | None,
        col_count: int,
    ) -> tuple[_LogicalRow, _LogicalRow, str, bool] | None:
        if self._row_sequence_int(row) is None:
            return None

        transitions = self._summary_transitions_from_text(row.source_text)
        if not transitions:
            return None

        for transition in transitions:
            if not self._row_matches_embedded_summary_transition(row, next_row, transition):
                continue

            product_row = self._clean_embedded_summary_product_row(row, transition, col_count)
            consume_next = self._row_is_summary_transition_fragment(next_row, transition)
            summary_source = next_row if consume_next and next_row is not None else row
            summary_row = self._summary_transition_row(summary_source, transition)
            return product_row, summary_row, transition.section, consume_next
        return None

    def _row_matches_embedded_summary_transition(
        self,
        row: _LogicalRow,
        next_row: _LogicalRow | None,
        transition: "_SummaryTransition",
    ) -> bool:
        row_text = self._normalize(" ".join(cell.text for cell in row.cells))
        if not row_text:
            return False
        label_present = self._source_contains_token(row_text, self._normalize(transition.label), allow_loose_cjk=True)

        current_has_amount = self._row_has_canonical_amount(row, transition.canonical_amount)
        current_has_section = self._row_has_text(row, transition.section)
        next_has_transition = self._row_is_summary_transition_fragment(next_row, transition)
        if label_present and ((current_has_amount and current_has_section) or next_has_transition):
            return True
        return self._row_followed_by_section_restart(row, next_row, transition)

    def _row_followed_by_section_restart(
        self,
        row: _LogicalRow,
        next_row: _LogicalRow | None,
        transition: "_SummaryTransition",
    ) -> bool:
        current_seq = self._row_sequence_int(row)
        next_seq = self._row_sequence_int(next_row) if next_row is not None else None
        if current_seq is None or current_seq < 2 or next_seq != 1:
            return False
        return next_row is not None and self._row_has_text(next_row, transition.section)

    def _row_is_summary_transition_fragment(
        self,
        row: _LogicalRow | None,
        transition: "_SummaryTransition",
    ) -> bool:
        if row is None:
            return False
        if self._row_sequence_int(row) is not None:
            return False
        nonempty = [cell for cell in row.cells if self._normalize(cell.text)]
        if len(nonempty) > 3:
            return False
        return self._row_has_canonical_amount(row, transition.canonical_amount) and self._row_has_text(row, transition.section)

    def _clean_embedded_summary_product_row(
        self,
        row: _LogicalRow,
        transition: "_SummaryTransition",
        col_count: int,
    ) -> _LogicalRow:
        cells_by_col: dict[int, _LogicalCell] = {}
        for cell in row.cells:
            text = self._remove_embedded_summary_text(cell.text, transition)
            cells_by_col[cell.col_index] = self._clone_logical_cell(cell, row.row_index, cell.col_index, text)

        for col in range(6, col_count):
            cell = cells_by_col.get(col)
            if cell is None:
                continue
            cell.text = self._remove_summary_amount_from_amount_text(cell.text, transition.canonical_amount)

        self._split_embedded_price_amount_cells(cells_by_col, row)

        return _LogicalRow(
            row_index=row.row_index,
            cells=list(cells_by_col.values()),
            page_no=row.page_no,
            source_block_id=row.source_block_id,
            source_row=row.source_row,
            section_title=row.section_title,
            source_text=row.source_text,
        )

    def _remove_embedded_summary_text(self, text: str, transition: "_SummaryTransition") -> str:
        raw = unicodedata.normalize("NFKC", text or "").strip()
        if not raw:
            return text

        section_norm = self._normalize(transition.section)
        if section_norm and self._normalize(raw) == section_norm:
            return ""

        labels = self._summary_labels(raw)
        if transition.label in labels:
            cleaned = re.sub(self._loose_literal_pattern(transition.label), " ", raw)
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" |,，、;；")
            if cleaned and not self._summary_labels_from_summary_text(cleaned):
                return cleaned
        return raw

    def _remove_summary_amount_from_amount_text(self, text: str, canonical_amount: str) -> str:
        amounts = self._summary_amounts(text, labels=[])
        if not amounts:
            return text
        raw = unicodedata.normalize("NFKC", text or "").strip()
        residual = raw
        for amount in amounts:
            residual = re.sub(self._loose_literal_pattern(amount), " ", residual, count=1)
        if re.sub(r"[\s,，、;；:.。|/\\\-]+", "", residual):
            return text

        removed = False
        remaining: list[str] = []
        for amount in amounts:
            if not removed and self._canonical_amount(amount) == canonical_amount:
                removed = True
                continue
            remaining.append(amount)
        if not removed:
            return text
        return " ".join(remaining)

    def _split_embedded_price_amount_cells(self, cells_by_col: dict[int, _LogicalCell], source_row: _LogicalRow) -> None:
        price_cell = cells_by_col.get(6)
        if price_cell is None:
            return
        amounts = self._summary_amounts(price_cell.text, labels=[])
        if len(amounts) < 2:
            return
        if not self._amount_text_has_only_amounts(price_cell.text, amounts):
            return

        price_cell.text = amounts[0]
        amount_cell = cells_by_col.get(7)
        if amount_cell is None:
            template = price_cell or source_row.cells[0]
            amount_cell = self._clone_logical_cell(template, source_row.row_index, 7, "")
            cells_by_col[7] = amount_cell
        if not self._normalize(amount_cell.text):
            amount_cell.text = amounts[1]

    def _summary_transition_row(self, source_row: _LogicalRow, transition: "_SummaryTransition") -> _LogicalRow:
        template = source_row.cells[0]
        cells = [
            self._clone_logical_cell(template, source_row.row_index, 0, transition.label),
            self._clone_logical_cell(template, source_row.row_index, 1, transition.amount),
        ]
        return _LogicalRow(
            row_index=source_row.row_index,
            cells=cells,
            page_no=source_row.page_no,
            source_block_id=source_row.source_block_id,
            source_row=source_row.source_row,
            section_title=source_row.section_title,
            source_text=source_row.source_text,
        )

    def _summary_transitions_from_text(self, text: str) -> list["_SummaryTransition"]:
        raw = unicodedata.normalize("NFKC", self._strip_html(text or ""))
        if not raw:
            return []

        lines = [line.strip() for line in raw.splitlines()]
        result: list[_SummaryTransition] = []
        pending_labels: list[str] = []
        for line_index, line in enumerate(lines):
            if not line:
                continue
            labels = self._summary_labels_from_summary_text(line)
            amounts = self._summary_amounts(line, labels=labels) if labels else []
            if not labels and pending_labels:
                amounts = self._summary_amount_line_amounts(line)
            pending_labels.extend(labels)

            for amount in amounts:
                if not pending_labels:
                    break
                label = pending_labels.pop(0)
                canonical_amount = self._canonical_amount(amount)
                section = self._next_summary_section_from_lines(lines, line_index)
                if canonical_amount and section:
                    result.append(_SummaryTransition(label, amount, canonical_amount, line_index, section))
        return result

    def _next_summary_section_from_lines(self, lines: list[str], start_index: int) -> str:
        for line in lines[start_index + 1:start_index + 8]:
            candidate = unicodedata.normalize("NFKC", line or "").strip()
            if not candidate:
                continue
            if self._summary_labels_from_summary_text(candidate):
                return ""
            if self._summary_amount_line_amounts(candidate):
                continue
            if self._is_summary_transition_section(candidate):
                return candidate
            if self._normalize(candidate):
                return ""
        return ""

    def _is_summary_transition_section(self, text: str) -> bool:
        norm = self._normalize(text)
        if len(norm) < 4 or len(norm) > 40:
            return False
        if self._is_number_like(norm) or self._canonical_amount(norm):
            return False
        if not re.search(r"[\u4e00-\u9fffA-Za-z]", text or ""):
            return False
        return "系统" in norm or "软件" in norm or "硬件" in norm or bool(re.search(r"v\d", norm, re.IGNORECASE))

    def _row_has_canonical_amount(self, row: _LogicalRow, canonical_amount: str) -> bool:
        for cell in row.cells:
            for amount in self._summary_amounts(cell.text, labels=[]):
                if self._canonical_amount(amount) == canonical_amount:
                    return True
        return False

    def _row_has_text(self, row: _LogicalRow, text: str) -> bool:
        row_text = self._normalize(" ".join(cell.text for cell in row.cells))
        return self._source_contains_token(row_text, self._normalize(text), allow_loose_cjk=True)

    def _amount_text_has_only_amounts(self, text: str, amounts: list[str]) -> bool:
        raw = unicodedata.normalize("NFKC", text or "")
        for amount in amounts:
            raw = re.sub(self._loose_literal_pattern(amount), " ", raw, count=1)
        residual = re.sub(r"[\s,，、;；:.。|/\\\-]+", "", raw)
        return not residual

    def _clone_logical_row(self, row: _LogicalRow, section_title: str | None = None) -> _LogicalRow:
        return _LogicalRow(
            row_index=row.row_index,
            cells=[
                self._clone_logical_cell(cell, row.row_index, cell.col_index, cell.text)
                for cell in row.cells
            ],
            page_no=row.page_no,
            source_block_id=row.source_block_id,
            source_row=row.source_row,
            section_title=row.section_title if section_title is None else section_title,
            source_text=row.source_text,
        )

    def _normalize_summary_rows(
        self,
        rows: list[_LogicalRow],
        col_count: int,
        source_tables: list[StructuredTable],
    ) -> list[_LogicalRow]:
        source_pairs: dict[str, list[_SummaryPair]] = {}
        for table in source_tables:
            if table.source_block_id and table.source_block_id not in source_pairs:
                source_pairs[table.source_block_id] = self._summary_pairs_from_text(table.source_text)

        normalized: list[_LogicalRow] = []
        used_source_pairs: set[tuple[str, int]] = set()
        for row in rows:
            split_rows = self._split_merged_summary_row(row, col_count)
            if split_rows is None:
                normalized.append(row)
                continue

            for split_row in split_rows:
                label = self._summary_label_from_row(split_row)
                if not label:
                    continue
                if not self._summary_amount_from_row(split_row):
                    fallback = self._next_summary_amount_from_source(
                        source_pairs.get(split_row.source_block_id, []),
                        split_row.source_block_id,
                        label,
                        used_source_pairs,
                    )
                    if fallback:
                        self._set_summary_row_amount(split_row, fallback)
                if self._summary_amount_from_row(split_row):
                    normalized.append(split_row)

        return self._reindex_logical_rows(normalized)

    def _split_merged_summary_row(self, row: _LogicalRow, col_count: int) -> list[_LogicalRow] | None:
        if not self._is_summary_candidate_row(row, col_count):
            return None

        labels: list[str] = []
        label_cell: _LogicalCell | None = None
        amounts: list[str] = []
        amount_cell: _LogicalCell | None = None

        for cell in row.cells:
            cell_labels = self._summary_labels_from_summary_text(cell.text)
            if cell_labels:
                labels.extend(cell_labels)
                label_cell = label_cell or cell
                amounts.extend(self._summary_amounts(cell.text, labels=cell_labels))
                amount_cell = amount_cell or cell
                continue
            cell_amounts = self._summary_amounts(cell.text)
            if cell_amounts:
                amounts.extend(cell_amounts)
                amount_cell = amount_cell or cell

        if not labels:
            return None

        result: list[_LogicalRow] = []
        for index, label in enumerate(labels):
            label_source = label_cell or row.cells[0]
            amount_source = amount_cell or label_source
            amount = amounts[index] if index < len(amounts) else ""
            cells = [
                self._virtual_cell(label_source, row_index=index, col_index=0, text=label),
                self._virtual_cell(amount_source, row_index=index, col_index=1, text=amount),
            ]
            result.append(_LogicalRow(
                row_index=index,
                cells=cells,
                page_no=row.page_no,
                source_block_id=row.source_block_id,
                source_row=row.source_row,
                section_title=row.section_title,
                source_text=row.source_text,
            ))

        return result

    def _is_summary_candidate_row(self, row: _LogicalRow, col_count: int) -> bool:
        nonempty = [cell for cell in row.cells if self._normalize(cell.text)]
        if not nonempty:
            return False

        if self._row_sequence_from_cells(row.cells) and len(nonempty) >= 4:
            return False

        label_cells = [
            cell for cell in nonempty
            if self._summary_labels_from_summary_text(cell.text)
        ]
        if not label_cells:
            return False

        if len(nonempty) == 1:
            cell = nonempty[0]
            return bool(cell.colspan >= max(2, col_count - 2) or self._summary_amounts(cell.text, labels=[]))

        allowed_cols = {cell.col_index for cell in label_cells}
        for cell in nonempty:
            if cell.col_index in allowed_cols:
                continue
            if not self._summary_amounts(cell.text, labels=[]):
                return False

        return len(nonempty) <= 3 or any(cell.colspan >= max(2, col_count - 2) for cell in label_cells)

    def _virtual_cell(self, source: _LogicalCell, row_index: int, col_index: int, text: str) -> _LogicalCell:
        return _LogicalCell(
            row_index=row_index,
            col_index=col_index,
            text=text,
            bbox=source.bbox,
            page_no=source.page_no,
            source_block_id=source.source_block_id,
            source_row=source.source_row,
            source_col=source.source_col,
            colspan=1,
            rowspan=1,
        )

    def _summary_label_from_row(self, row: _LogicalRow) -> str:
        for cell in row.cells:
            labels = self._summary_labels_from_summary_text(cell.text)
            if labels:
                return labels[0]
        return ""

    def _summary_amount_from_row(self, row: _LogicalRow) -> str:
        for cell in row.cells:
            if self._summary_labels_from_summary_text(cell.text):
                continue
            amounts = self._summary_amounts(cell.text)
            if amounts:
                return amounts[0]
        return ""

    def _set_summary_row_amount(self, row: _LogicalRow, amount: str) -> None:
        for cell in row.cells:
            if cell.col_index == 1:
                cell.text = amount
                return
        template = row.cells[0]
        row.cells.append(self._virtual_cell(template, row_index=row.row_index, col_index=1, text=amount))

    def _summary_labels(self, text: str) -> list[str]:
        compact = self._normalize(text)
        if not compact:
            return []
        return [match.group(0) for match in SUMMARY_LABEL_PATTERN.finditer(compact)]

    def _summary_labels_from_summary_text(self, text: str) -> list[str]:
        labels = self._summary_labels(text)
        if not labels:
            return []
        if not self._summary_text_has_only_labels_and_amounts(text, labels):
            return []
        return labels

    def _summary_text_has_only_labels_and_amounts(self, text: str, labels: list[str]) -> bool:
        raw = unicodedata.normalize("NFKC", text or "")
        for label in labels:
            raw = re.sub(self._loose_literal_pattern(label), " ", raw)
        raw = AMOUNT_TOKEN_PATTERN.sub(" ", raw)
        residual = re.sub(r"[\s,，、;；:.。|/\\\-]+", "", raw)
        return not residual

    def _summary_amounts(self, text: str, labels: list[str] | None = None) -> list[str]:
        raw = unicodedata.normalize("NFKC", text or "")
        for label in (self._summary_labels(raw) if labels is None else labels):
            raw = re.sub(self._loose_literal_pattern(label), " ", raw)

        amounts: list[str] = []
        for match in AMOUNT_TOKEN_PATTERN.finditer(raw):
            token = match.group(0)
            if self._canonical_amount(token):
                amounts.append(token)
        return amounts

    @staticmethod
    def _loose_literal_pattern(text: str) -> str:
        return r"\s*".join(re.escape(char) for char in text)

    def _summary_pairs_from_text(self, text: str) -> list[_SummaryPair]:
        raw = unicodedata.normalize("NFKC", self._strip_html(text or ""))
        if not raw:
            return []

        result: list[_SummaryPair] = []
        pending_labels: list[str] = []
        for line_index, line in enumerate(raw.splitlines()):
            labels = self._summary_labels_from_summary_text(line)
            amounts = self._summary_amounts(line, labels=labels) if labels else []
            if not labels and pending_labels:
                amounts = self._summary_amount_line_amounts(line)
            pending_labels.extend(labels)
            for amount in amounts:
                if not pending_labels:
                    break
                label = pending_labels.pop(0)
                canonical_amount = self._canonical_amount(amount)
                if canonical_amount:
                    result.append(_SummaryPair(label, amount, canonical_amount, line_index))
        return result

    def _summary_amount_line_amounts(self, text: str) -> list[str]:
        amounts = self._summary_amounts(text, labels=[])
        if not amounts:
            return []
        raw = unicodedata.normalize("NFKC", text or "")
        raw = AMOUNT_TOKEN_PATTERN.sub(" ", raw)
        residual = re.sub(r"[\s,，、;；:.。|/\\\-]+", "", raw)
        return amounts if not residual else []

    @staticmethod
    def _next_summary_amount_from_source(
        pairs: list[_SummaryPair],
        source_block_id: str,
        label: str,
        used: set[tuple[str, int]],
    ) -> str:
        for index, pair in enumerate(pairs):
            key = (source_block_id, index)
            if key in used:
                continue
            if pair.label != label:
                continue
            used.add(key)
            return pair.amount
        return ""

    def _is_summary_only_table(self, table: StructuredTable) -> bool:
        saw_summary = False
        for row in table.rows:
            cells = [
                self._normalize(cell.text)
                for cell in row.cells
                if self._normalize(cell.text)
            ]
            if not cells:
                continue
            row_has_summary = any(self._summary_labels(text) for text in cells)
            if row_has_summary:
                saw_summary = True
                continue
            if all(self._is_number_like(text) or self._is_amount_like(text) for text in cells):
                continue
            return False
        return saw_summary

    @staticmethod
    def _should_stitch_summary_table(previous: StructuredTable, current: StructuredTable) -> bool:
        return current.page_no in {previous.page_no, previous.page_no + 1}

    def _is_product_like_table(self, table: StructuredTable) -> bool:
        if table.col_count >= 8:
            return True
        text = self._normalize(table.all_cell_text())
        return table.col_count >= 6 and any(token in text for token in ("产品名称", "详细配置", "单价", "金额"))

    def _is_table_header_cells(self, cells: list[_LogicalCell]) -> bool:
        texts = [self._normalize(cell.text) for cell in cells if self._normalize(cell.text)]
        if not texts:
            return False
        header_count = sum(1 for text in texts if text in TABLE_HEADERS)
        return header_count >= 3 and header_count / len(texts) >= 0.5

    def _is_summary_cells(self, cells: list[_LogicalCell]) -> bool:
        texts = [self._normalize(cell.text) for cell in cells if self._normalize(cell.text)]
        if not texts:
            return False
        if len(texts) == 1 and re.fullmatch(r"\d{1,2}", texts[0]):
            return False
        if all(self._is_noise(text) or self._is_number_like(text) for text in texts):
            return True
        return len(texts) <= 3 and any(self._is_noise(text) for text in texts)

    def _is_ocr_fragment_row(self, cells: list[_LogicalCell]) -> bool:
        nonempty = [c for c in cells if self._normalize(c.text)]
        if not nonempty:
            return False
        total_text = "".join(self._normalize(c.text) for c in nonempty)
        if self._is_number_like(total_text):
            return False
        if len(nonempty) / max(len(cells), 1) >= 0.3:
            return False
        return len(total_text) <= 3

    def _section_title(self, cells: list[_LogicalCell], col_count: int) -> str:
        nonempty = [cell for cell in cells if self._normalize(cell.text)]
        if len(nonempty) != 1:
            return ""
        cell = nonempty[0]
        text = self._normalize(cell.text)
        if self._summary_labels(text):
            return ""
        if cell.colspan >= max(2, col_count - 1) or ("系统" in text and ("硬件" in text or "软件" in text or "v" in text)):
            return text
        return ""

    # --- Table matching ---

    def _compare_tables(
        self,
        original_tables: list[_LogicalTable],
        compare_tables: list[_LogicalTable],
        original_blocks: list[tuple[TextBlock, str]],
        compare_blocks: list[tuple[TextBlock, str]],
        start_index: int,
        warnings: list[str],
    ) -> tuple[list[DiffItem], list[str]]:
        pairs = self._match_tables(original_tables, compare_tables)
        diffs: list[DiffItem] = []
        next_index = start_index
        used_original: set[int] = set()
        used_compare: set[int] = set()

        for orig_idx, comp_idx, score in pairs:
            used_original.add(orig_idx)
            used_compare.add(comp_idx)
            orig_table = original_tables[orig_idx]
            comp_table = compare_tables[comp_idx]

            orig_block = self._find_block(original_blocks, orig_table)
            comp_block = self._find_block(compare_blocks, comp_table)
            cell_diffs = self._diff_cells(
                orig_table,
                comp_table,
                orig_block[0] if orig_block else None,
                comp_block[0] if comp_block else None,
            )
            for group in self._group_cell_diffs(cell_diffs, orig_table, comp_table):
                diffs.append(self._make_diff(group, next_index, orig_block, comp_block, orig_table, comp_table))
                next_index += 1

        # Unmatched tables: whole-table ADD / DELETE
        for idx in range(len(original_tables)):
            if idx not in used_original:
                block = self._find_block(original_blocks, original_tables[idx])
                diffs.append(self._whole_table_diff(original_tables[idx], "DELETE", next_index, block, None))
                next_index += 1
        for idx in range(len(compare_tables)):
            if idx not in used_compare:
                block = self._find_block(compare_blocks, compare_tables[idx])
                diffs.append(self._whole_table_diff(compare_tables[idx], "ADD", next_index, None, block))
                next_index += 1

        return diffs, warnings

    def _match_tables(self, original: list[StructuredTable], compare: list[StructuredTable]) -> list[tuple[int, int, float]]:
        n = len(original)
        m = len(compare)
        scores = [[0.0] * m for _ in range(n)]
        for oi, ot in enumerate(original):
            for ci, ct in enumerate(compare):
                score = self._table_similarity(ot, ct)
                if score >= self.table_similarity_threshold:
                    scores[oi][ci] = score

        dp = [[0.0] * (m + 1) for _ in range(n + 1)]
        move: list[list[str]] = [[""] * (m + 1) for _ in range(n + 1)]
        for i in range(1, n + 1):
            for j in range(1, m + 1):
                best = dp[i - 1][j]
                best_move = "up"
                if dp[i][j - 1] > best:
                    best = dp[i][j - 1]
                    best_move = "left"
                if scores[i - 1][j - 1] > 0 and dp[i - 1][j - 1] + scores[i - 1][j - 1] > best:
                    best = dp[i - 1][j - 1] + scores[i - 1][j - 1]
                    best_move = "diag"
                dp[i][j] = best
                move[i][j] = best_move

        pairs: list[tuple[int, int, float]] = []
        i, j = n, m
        while i > 0 and j > 0:
            if move[i][j] == "diag":
                pairs.append((i - 1, j - 1, scores[i - 1][j - 1]))
                i -= 1
                j -= 1
            elif move[i][j] == "left":
                j -= 1
            else:
                i -= 1
        pairs.reverse()
        return pairs

    def _table_similarity(self, left: StructuredTable, right: StructuredTable) -> float:
        left_text = left.all_cell_text()
        right_text = right.all_cell_text()
        if not left_text and not right_text:
            return 0.0
        text_score = SequenceMatcher(None, self._normalize(left_text), self._normalize(right_text)).ratio()
        if len(left.rows) <= 2 and len(right.rows) <= 2:
            row_score = max(
                (
                    self._row_similarity(left, left_row.row_index, right, right_row.row_index)
                    for left_row in left.rows
                    for right_row in right.rows
                ),
                default=0.0,
            )
            text_score = max(text_score, row_score)
        col_penalty = 1.0 if left.col_count == right.col_count else 0.7
        return text_score * col_penalty

    # --- Cell-level comparison ---

    def _diff_cells(
        self,
        original: StructuredTable,
        compare: StructuredTable,
        original_block: TextBlock | None = None,
        compare_block: TextBlock | None = None,
    ) -> list[CellDiff]:
        max_cols = max(original.col_count, compare.col_count)
        diffs: list[CellDiff] = []

        for orig_row, comp_row in self._align_rows(original, compare):
            layout_matches = self._layout_shift_matches(
                original,
                compare,
                orig_row,
                comp_row,
                original_block,
                compare_block,
            )
            if layout_matches:
                layout_diffs = self._diff_layout_shift_unmatched(original, compare, orig_row, comp_row, layout_matches)
                for cell_diff in layout_diffs:
                    filter_orig_row = cell_diff.original_row if cell_diff.original_row is not None else orig_row
                    filter_comp_row = cell_diff.compare_row if cell_diff.compare_row is not None else comp_row
                    filter_col = cell_diff.original_col if cell_diff.original_col is not None else cell_diff.compare_col
                    if filter_col is not None and self._is_covered_duplicate_amount_cell(
                        original,
                        compare,
                        filter_orig_row,
                        filter_comp_row,
                        filter_col,
                        self._normalize_cell_for_compare(cell_diff.original_text),
                        self._normalize_cell_for_compare(cell_diff.compare_text),
                        original_block,
                        compare_block,
                    ):
                        continue
                    diffs.append(cell_diff)
                continue
            if self._is_sparse_row_covered_by_source(
                original,
                compare,
                orig_row,
                comp_row,
                original_block,
                compare_block,
            ):
                continue
            if self._one_sided_summary_row_covered_by_source(original, compare, orig_row, comp_row):
                continue
            if self._one_sided_product_row_covered_by_source(
                original,
                compare,
                orig_row,
                comp_row,
                original_block,
                compare_block,
            ):
                continue

            for c in range(max_cols):
                orig_text = self._cell_text(original, orig_row, c) if orig_row is not None else ""
                comp_text = self._cell_text(compare, comp_row, c) if comp_row is not None else ""
                orig_norm = self._normalize_cell_for_compare(orig_text)
                comp_norm = self._normalize_cell_for_compare(comp_text)

                if orig_norm == comp_norm:
                    continue
                if self._is_noise(orig_norm) and self._is_noise(comp_norm):
                    continue
                if (not orig_norm or not comp_norm) and (self._is_noise(orig_norm) or self._is_noise(comp_norm)):
                    continue
                if not orig_norm and not comp_norm:
                    continue

                if self._is_similar_ocr_noise(orig_norm, comp_norm):
                    continue

                if self._is_short_text_fragment(original, compare, orig_row, comp_row, orig_norm, comp_norm):
                    continue

                if self._is_covered_duplicate_amount_cell(
                    original,
                    compare,
                    orig_row,
                    comp_row,
                    c,
                    orig_norm,
                    comp_norm,
                    original_block,
                    compare_block,
                ):
                    continue

                if not orig_norm:
                    diff_type = "ADD"
                elif not comp_norm:
                    diff_type = "DELETE"
                else:
                    diff_type = "MODIFY"

                char_segments = []
                if diff_type == "MODIFY" and orig_text and comp_text:
                    char_segments = self._compute_char_segments(orig_text, comp_text)

                display_row = orig_row if orig_row is not None else comp_row
                diffs.append(CellDiff(
                    row=display_row or 0,
                    col=c,
                    original_text=orig_text,
                    compare_text=comp_text,
                    diff_type=diff_type,
                    char_segments=char_segments,
                    original_row=orig_row,
                    compare_row=comp_row,
                ))

        return diffs

    def _align_rows(self, original: StructuredTable, compare: StructuredTable) -> list[tuple[int | None, int | None]]:
        n = len(original.rows)
        m = len(compare.rows)
        scores = [[0.0] * m for _ in range(n)]
        for oi in range(n):
            if not self._row_text(original, oi):
                continue
            for ci in range(m):
                if not self._row_text(compare, ci):
                    continue
                score = self._row_similarity(original, oi, compare, ci)
                if score >= self.row_similarity_threshold:
                    scores[oi][ci] = score

        dp = [[0.0] * (m + 1) for _ in range(n + 1)]
        move: list[list[str]] = [[""] * (m + 1) for _ in range(n + 1)]
        for i in range(1, n + 1):
            for j in range(1, m + 1):
                best = dp[i - 1][j]
                best_move = "up"
                if dp[i][j - 1] > best:
                    best = dp[i][j - 1]
                    best_move = "left"
                if scores[i - 1][j - 1] > 0 and dp[i - 1][j - 1] + scores[i - 1][j - 1] > best:
                    best = dp[i - 1][j - 1] + scores[i - 1][j - 1]
                    best_move = "diag"
                dp[i][j] = best
                move[i][j] = best_move

        matched: list[tuple[int, int]] = []
        i, j = n, m
        while i > 0 and j > 0:
            if move[i][j] == "diag":
                matched.append((i - 1, j - 1))
                i -= 1
                j -= 1
            elif move[i][j] == "left":
                j -= 1
            else:
                i -= 1
        matched.reverse()

        result: list[tuple[int | None, int | None]] = []
        prev_o = -1
        prev_c = -1
        for oi, ci in matched:
            for row in range(prev_o + 1, oi):
                if self._row_text(original, row):
                    result.append((row, None))
            for row in range(prev_c + 1, ci):
                if self._row_text(compare, row):
                    result.append((None, row))
            result.append((oi, ci))
            prev_o = oi
            prev_c = ci
        for row in range(prev_o + 1, n):
            if self._row_text(original, row):
                result.append((row, None))
        for row in range(prev_c + 1, m):
            if self._row_text(compare, row):
                result.append((None, row))
        return result

    def _row_text(self, table: StructuredTable, row: int) -> str:
        parts = []
        for c in range(table.col_count):
            text = self._normalize(self._cell_text(table, row, c))
            if text and not self._is_noise(text):
                parts.append(text)
        return "|".join(parts)

    def _row_similarity(self, left_table, left_row: int, right_table, right_row: int) -> float:
        left_text = self._row_text(left_table, left_row)
        right_text = self._row_text(right_table, right_row)
        if not left_text or not right_text:
            return 0.0

        left_key = self._row_business_key(left_table, left_row)
        right_key = self._row_business_key(right_table, right_row)
        text_score = SequenceMatcher(None, left_text, right_text).ratio()
        if left_key and right_key:
            key_score = SequenceMatcher(None, left_key, right_key).ratio()
            if left_key == right_key:
                return max(text_score, 0.98)
            if key_score >= 0.82:
                return max(text_score, key_score * 0.95)
            if self._row_sequence(left_table, left_row) and self._row_sequence(left_table, left_row) == self._row_sequence(right_table, right_row):
                return max(text_score, key_score * 0.85)
        return text_score

    def _row_business_key(self, table, row: int) -> str:
        cells = self._row_nonempty_cells(table, row)
        if not cells:
            return ""
        table_type = self._table_type(table)
        section = self._normalize(getattr(table.rows[row], "section_title", "")) if row < len(table.rows) else ""
        summary_label = self._summary_label_from_row(table.rows[row]) if row < len(table.rows) else ""
        if summary_label:
            return "|".join(part for part in (section, "summary", self._normalize(summary_label)) if part)
        sequence = ""
        product = ""
        details = ""

        for index, cell in enumerate(cells):
            text = self._normalize(cell.text)
            if not text or self._is_noise(text):
                continue
            if table_type == "payment" and not product and not self._is_amount_like(text):
                product = text
                continue
            if table_type == "contact" and not product and self._looks_like_person_or_role(text):
                product = text
                continue
            if not sequence and re.fullmatch(r"\d{1,2}", text):
                sequence = text
                for next_cell in cells[index + 1:]:
                    candidate = self._normalize(next_cell.text)
                    if candidate and not self._is_noise(candidate) and not self._is_number_like(candidate):
                        product = candidate
                        break
                continue
            if not product and not self._is_number_like(text):
                product = text
            elif product and not details and not self._is_number_like(text):
                details = text[:40]

        if sequence and product:
            return "|".join(part for part in (section, sequence, product) if part)
        if product and details:
            return "|".join(part for part in (section, product, details) if part)
        if product:
            return "|".join(part for part in (section, table_type, product) if part)
        return ""

    def _row_sequence(self, table, row: int) -> str:
        for cell in self._row_nonempty_cells(table, row):
            text = self._normalize(cell.text)
            if re.fullmatch(r"\d{1,2}", text):
                return text
        return ""

    def _row_nonempty_cells(self, table, row: int):
        result = []
        for c in range(table.col_count):
            cell = self._anchor_cell(table, row, c)
            if cell and self._normalize(cell.text):
                result.append(cell)
        return result

    @staticmethod
    def _compute_char_segments(orig_text: str, comp_text: str) -> list[CharSegment]:
        sm = SequenceMatcher(None, orig_text, comp_text)
        segments: list[CharSegment] = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                segments.append(CharSegment(tag=tag, orig_start=i1, orig_end=i2, comp_start=j1, comp_end=j2))
        return segments

    def _is_similar_ocr_noise(self, orig_norm: str, comp_norm: str) -> bool:
        if not orig_norm or not comp_norm:
            return False
        if SequenceMatcher(None, orig_norm, comp_norm).ratio() < self.cell_similarity_threshold:
            return False
        orig_fold = self._punctuation_fold(orig_norm)
        comp_fold = self._punctuation_fold(comp_norm)
        if orig_fold == comp_fold:
            return True
        if min(len(orig_fold), len(comp_fold)) >= 15 and SequenceMatcher(None, orig_fold, comp_fold).ratio() >= 0.96:
            return True

        opcodes = [
            opcode for opcode in SequenceMatcher(None, orig_norm, comp_norm).get_opcodes()
            if opcode[0] != "equal"
        ]
        if len(opcodes) != 1:
            return False

        tag, i1, i2, j1, j2 = opcodes[0]
        left_delta = orig_norm[i1:i2]
        right_delta = comp_norm[j1:j2]
        if tag in {"insert", "delete"} and len(left_delta + right_delta) == 1:
            delta = left_delta or right_delta
            if delta in {"0", "o", "O", "1", "l", "I", "|"} and max(len(orig_norm), len(comp_norm)) >= 10:
                return True
        if tag == "replace" and len(left_delta) == len(right_delta) == 1:
            ambiguous_groups = ({"0", "o", "O"}, {"1", "l", "I", "|"})
            return any(left_delta in group and right_delta in group for group in ambiguous_groups)
        return False

    def _is_short_text_fragment(
        self,
        original: StructuredTable,
        compare: StructuredTable,
        orig_row: int | None,
        comp_row: int | None,
        orig_norm: str,
        comp_norm: str,
    ) -> bool:
        if orig_norm and comp_norm:
            return False
        text = orig_norm or comp_norm
        if len(text) > 3:
            return False
        if self._is_number_like(text):
            return False
        if not text:
            return False
        table = original if orig_norm else compare
        row = orig_row if orig_norm else comp_row
        return self._is_ocr_fragment_table_row(table, row)

    def _is_ocr_fragment_table_row(self, table: StructuredTable, row: int | None) -> bool:
        if row is None:
            return False
        cells = [
            cell
            for col in range(table.col_count)
            if (cell := self._anchor_cell(table, row, col)) is not None
        ]
        return self._is_ocr_fragment_row(cells)

    def _layout_shift_matches(
        self,
        original: StructuredTable,
        compare: StructuredTable,
        orig_row: int | None,
        comp_row: int | None,
        original_block: TextBlock | None,
        compare_block: TextBlock | None,
    ) -> dict[str, dict[int, int]] | None:
        if orig_row is None or comp_row is None:
            return None

        original_cells = self._row_match_cells(original, orig_row)
        compare_cells = self._row_match_cells(compare, comp_row)
        if len(original_cells) < 2 or len(compare_cells) < 2:
            return None

        original_source = self._row_source_text(original, orig_row, original_block)
        compare_source = self._row_source_text(compare, comp_row, compare_block)
        candidates: list[tuple[float, int, int]] = []
        for orig_cell in original_cells:
            for comp_cell in compare_cells:
                score = self._row_cell_match_score(
                    orig_cell["norm"],
                    comp_cell["norm"],
                    original_source,
                    compare_source,
                )
                if score > 0:
                    candidates.append((score, orig_cell["col"], comp_cell["col"]))

        candidates.sort(reverse=True)
        orig_to_comp: dict[int, int] = {}
        comp_to_orig: dict[int, int] = {}
        for _score, orig_col, comp_col in candidates:
            if orig_col in orig_to_comp or comp_col in comp_to_orig:
                continue
            orig_to_comp[orig_col] = comp_col
            comp_to_orig[comp_col] = orig_col

        if not self._is_possible_ocr_shift_row(original, compare, orig_row, comp_row, original_cells, compare_cells, orig_to_comp):
            return None
        return {"orig_to_comp": orig_to_comp, "comp_to_orig": comp_to_orig}

    def _diff_layout_shift_unmatched(
        self,
        original: StructuredTable,
        compare: StructuredTable,
        orig_row: int | None,
        comp_row: int | None,
        layout_matches: dict[str, dict[int, int]],
    ) -> list[CellDiff]:
        if orig_row is None or comp_row is None:
            return []
        matched_orig = set(layout_matches["orig_to_comp"])
        matched_comp = set(layout_matches["comp_to_orig"])
        original_unmatched = [cell for cell in self._row_match_cells(original, orig_row) if int(cell["col"]) not in matched_orig]
        compare_unmatched = [cell for cell in self._row_match_cells(compare, comp_row) if int(cell["col"]) not in matched_comp]

        diffs: list[CellDiff] = []
        pair_count = min(len(original_unmatched), len(compare_unmatched))
        for index in range(pair_count):
            orig_cell = original_unmatched[index]
            comp_cell = compare_unmatched[index]
            orig_text = str(orig_cell["text"])
            comp_text = str(comp_cell["text"])
            orig_norm = str(orig_cell["norm"])
            comp_norm = str(comp_cell["norm"])
            if orig_norm == comp_norm or self._is_similar_ocr_noise(orig_norm, comp_norm):
                continue
            diffs.append(CellDiff(
                row=orig_row,
                col=int(orig_cell["col"]),
                original_text=orig_text,
                compare_text=comp_text,
                diff_type="MODIFY",
                char_segments=self._compute_char_segments(orig_text, comp_text),
                original_row=orig_row,
                compare_row=comp_row,
                original_col=int(orig_cell["col"]),
                compare_col=int(comp_cell["col"]),
            ))

        for orig_cell in original_unmatched[pair_count:]:
            diffs.append(CellDiff(
                row=orig_row,
                col=int(orig_cell["col"]),
                original_text=str(orig_cell["text"]),
                compare_text="",
                diff_type="DELETE",
                original_row=orig_row,
                compare_row=None,
                original_col=int(orig_cell["col"]),
                compare_col=None,
            ))
        for comp_cell in compare_unmatched[pair_count:]:
            diffs.append(CellDiff(
                row=comp_row,
                col=int(comp_cell["col"]),
                original_text="",
                compare_text=str(comp_cell["text"]),
                diff_type="ADD",
                original_row=None,
                compare_row=comp_row,
                original_col=None,
                compare_col=int(comp_cell["col"]),
            ))
        return diffs

    def _is_sparse_row_covered_by_source(
        self,
        original: StructuredTable,
        compare: StructuredTable,
        orig_row: int | None,
        comp_row: int | None,
        original_block: TextBlock | None,
        compare_block: TextBlock | None,
    ) -> bool:
        if orig_row is None or comp_row is None:
            return False

        original_cells = self._row_match_cells(original, orig_row)
        compare_cells = self._row_match_cells(compare, comp_row)
        if len(original_cells) >= 5 and len(compare_cells) <= 2:
            return self._dense_row_tokens_present_in_source(original_cells, compare, comp_row, compare_cells, compare_block)
        if len(compare_cells) >= 5 and len(original_cells) <= 2:
            return self._dense_row_tokens_present_in_source(compare_cells, original, orig_row, original_cells, original_block)
        return False

    def _one_sided_summary_row_covered_by_source(
        self,
        original: StructuredTable,
        compare: StructuredTable,
        orig_row: int | None,
        comp_row: int | None,
    ) -> bool:
        if bool(orig_row is not None) == bool(comp_row is not None):
            return False

        present_table = original if orig_row is not None else compare
        missing_table = compare if orig_row is not None else original
        present_row = orig_row if orig_row is not None else comp_row
        if present_row is None:
            return False

        pair = self._summary_pair_from_row(present_table, present_row)
        if pair is None:
            return False
        label, amount = pair
        reference_section = self._normalize(getattr(present_table.rows[present_row], "section_title", ""))
        if not reference_section:
            return False
        return self._table_source_has_summary_pair(
            missing_table,
            label,
            amount,
            present_table.rows[present_row].page_no,
            reference_section,
        )

    def _summary_pair_from_row(self, table: StructuredTable, row: int) -> tuple[str, str] | None:
        if row < 0 or row >= len(table.rows):
            return None
        logical_row = table.rows[row]
        label = self._summary_label_from_row(logical_row)
        amount = self._summary_amount_from_row(logical_row)
        canonical_amount = self._canonical_amount(amount)
        if not label or not canonical_amount:
            return None
        return label, canonical_amount

    def _table_source_has_summary_pair(
        self,
        table: StructuredTable,
        label: str,
        canonical_amount: str,
        reference_page: int,
        reference_section: str,
    ) -> bool:
        seen_sources: set[tuple[str, str]] = set()
        for row in table.rows:
            if abs(row.page_no - reference_page) > 1:
                continue
            row_section = self._normalize(getattr(row, "section_title", ""))
            if row_section != reference_section:
                continue
            source_text = getattr(row, "source_text", "") or ""
            if not source_text:
                continue
            source_key = (row.source_block_id, source_text)
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            if any(
                pair.label == label and pair.canonical_amount == canonical_amount
                for pair in self._summary_pairs_from_text(source_text)
            ):
                return True
        return False

    def _one_sided_product_row_covered_by_source(
        self,
        original: StructuredTable,
        compare: StructuredTable,
        orig_row: int | None,
        comp_row: int | None,
        original_block: TextBlock | None,
        compare_block: TextBlock | None,
    ) -> bool:
        if bool(orig_row is not None) == bool(comp_row is not None):
            return False

        present_table = compare if orig_row is None else original
        missing_table = original if orig_row is None else compare
        present_row = comp_row if orig_row is None else orig_row
        missing_block = original_block if orig_row is None else compare_block
        if present_row is None:
            return False
        if not self._is_dense_product_row_for_source_cover(present_table, present_row):
            return False

        reference_page = present_table.rows[present_row].page_no
        for source_text in self._nearby_plain_source_texts(missing_table, reference_page, missing_block):
            if self._product_row_covered_by_source_text(present_table, present_row, source_text):
                return True
        return False

    def _is_dense_product_row_for_source_cover(self, table: StructuredTable, row: int) -> bool:
        if row < 0 or row >= len(table.rows):
            return False
        if self._table_type(table) != "product" and table.col_count < 8:
            return False
        if self._summary_label_from_row(table.rows[row]):
            return False

        cells = self._row_nonempty_cells(table, row)
        if len(cells) < 6:
            return False
        if not self._row_sequence(table, row):
            return False
        return bool(self._row_likely_amount_values(table, row))

    def _nearby_plain_source_texts(
        self,
        table: StructuredTable,
        reference_page: int,
        block: TextBlock | None,
    ) -> list[str]:
        texts: list[str] = []
        seen: set[str] = set()

        def add(text: str) -> None:
            if not text:
                return
            key = self._normalize(text)
            if not key or key in seen:
                return
            seen.add(key)
            texts.append(text)

        for row in table.rows:
            if abs(row.page_no - reference_page) <= 1:
                add(getattr(row, "source_text", "") or "")
        if block is not None and abs(block.page_no - reference_page) <= 1:
            add(block.text or "")
            if block.raw_html:
                add(self._strip_html(block.raw_html))
        return texts

    def _product_row_covered_by_source_text(self, table: StructuredTable, row: int, source_text: str) -> bool:
        if not source_text:
            return False

        source_norm = self._normalize(source_text)
        if not source_norm:
            return False

        product = self._product_row_identity_text(table, row)
        if not product or not self._source_contains_token(source_norm, self._normalize(product), allow_loose_cjk=True):
            return False

        brand = self._product_row_brand_text(table, row)
        if brand and not self._source_contains_token(source_norm, self._normalize(brand), allow_loose_cjk=False):
            return False

        amounts = self._row_likely_amount_values(table, row)
        if not amounts:
            return False
        for amount in set(amounts):
            if self._amount_occurrence_count(source_text, amount) < amounts.count(amount):
                return False

        detail = self._product_row_detail_text(table, row)
        if detail and self._source_contains_fuzzy_detail(source_norm, self._normalize(detail)):
            return True
        return bool(brand and len(self._normalize(brand)) >= 2)

    def _product_row_identity_text(self, table: StructuredTable, row: int) -> str:
        if table.col_count >= 8:
            text = self._cell_text(table, row, 1)
            if self._normalize(text):
                return text
        for cell in self._row_nonempty_cells(table, row):
            text = self._normalize(cell.text)
            if text and not self._is_noise(text) and not self._is_number_like(text) and not self._canonical_amount(cell.text):
                return cell.text
        return ""

    def _product_row_detail_text(self, table: StructuredTable, row: int) -> str:
        if table.col_count >= 8:
            return self._cell_text(table, row, 2)
        cells = [
            cell for cell in self._row_nonempty_cells(table, row)
            if not self._is_number_like(self._normalize(cell.text)) and not self._canonical_amount(cell.text)
        ]
        return cells[1].text if len(cells) >= 2 else ""

    def _product_row_brand_text(self, table: StructuredTable, row: int) -> str:
        if table.col_count >= 8:
            return self._cell_text(table, row, 3)
        return ""

    def _row_likely_amount_values(self, table: StructuredTable, row: int) -> list[str]:
        amounts: list[str] = []
        for cell in self._row_nonempty_cells(table, row):
            if not self._is_likely_amount_column(table, row, cell.col_index):
                continue
            amount = self._canonical_amount(cell.text)
            if amount and self._looks_like_amount_value(f"amount:{amount}"):
                amounts.append(amount)
        return amounts

    def _source_contains_token(self, source_norm: str, token_norm: str, allow_loose_cjk: bool) -> bool:
        if not token_norm:
            return False
        if token_norm in source_norm:
            return True
        if self._punctuation_fold(token_norm) in self._punctuation_fold(source_norm):
            return True
        if allow_loose_cjk and self._is_loose_subsequence_present(token_norm, source_norm):
            return True
        return False

    def _source_contains_fuzzy_detail(self, source_norm: str, token_norm: str) -> bool:
        if not token_norm:
            return False
        if self._source_contains_token(source_norm, token_norm, allow_loose_cjk=True):
            return True
        token_chunks = re.findall(r"[a-z0-9][a-z0-9\-_/]{5,}", token_norm)
        source_chunks = re.findall(r"[a-z0-9][a-z0-9\-_/]{5,}", source_norm)
        for token in token_chunks:
            for source in source_chunks:
                if SequenceMatcher(None, token, source).ratio() >= 0.88:
                    return True
        return False

    @staticmethod
    def _is_loose_subsequence_present(token: str, source: str) -> bool:
        if len(token) < 4 or not re.search(r"[\u4e00-\u9fff]", token):
            return False
        positions: list[int] = []
        start = 0
        for char in token:
            index = source.find(char, start)
            if index < 0:
                return False
            positions.append(index)
            start = index + 1
        return positions[-1] - positions[0] <= max(80, len(token) * 8)

    def _dense_row_tokens_present_in_source(
        self,
        dense_cells: list[dict[str, object]],
        sparse_table: StructuredTable,
        sparse_row: int,
        sparse_cells: list[dict[str, object]],
        sparse_block: TextBlock | None,
    ) -> bool:
        source_text = self._row_plain_source_text(sparse_table, sparse_row)
        if not source_text and sparse_block is not None and sparse_block.raw_html:
            source_text = sparse_block.text
        if not source_text:
            return False

        sparse_norms = [str(cell["norm"]) for cell in sparse_cells if str(cell["norm"])]
        dense_norms = [str(cell["norm"]) for cell in dense_cells if str(cell["norm"])]
        if not sparse_norms or not dense_norms:
            return False

        has_anchor = any(
            sparse in dense or dense in sparse
            for sparse in sparse_norms
            for dense in dense_norms
            if not self._is_number_like(sparse) and not self._is_number_like(dense)
        )
        if not has_anchor:
            return False

        source = self._normalize(source_text)
        checked = 0
        present = 0
        for norm in dense_norms:
            if self._is_number_like(norm) and len(norm) < 3:
                continue
            if norm.startswith(("amount:", "quantity:", "date:", "percent:")):
                raw_value = norm.split(":", 1)[1].rstrip("0").rstrip(".")
                candidates = [raw_value] if raw_value else []
            else:
                candidates = [norm]
            candidates = [candidate for candidate in candidates if len(candidate) >= 2]
            if not candidates:
                continue
            checked += 1
            if any(candidate and candidate in source for candidate in candidates):
                present += 1

        return checked >= 4 and present / checked >= 0.8

    def _is_covered_duplicate_amount_cell(
        self,
        original: StructuredTable,
        compare: StructuredTable,
        orig_row: int | None,
        comp_row: int | None,
        col: int,
        orig_norm: str,
        comp_norm: str,
        original_block: TextBlock | None,
        compare_block: TextBlock | None,
    ) -> bool:
        if bool(orig_norm) == bool(comp_norm):
            return False
        present_table = compare if not orig_norm else original
        missing_table = original if not orig_norm else compare
        present_row = comp_row if not orig_norm else orig_row
        missing_row = orig_row if not orig_norm else comp_row
        present_norm = comp_norm if not orig_norm else orig_norm
        missing_block = original_block if not orig_norm else compare_block
        if present_row is None:
            return False

        if not (
            self._table_type(present_table) == "product"
            or self._table_type(missing_table) == "product"
            or present_table.col_count >= 8
            or missing_table.col_count >= 8
        ):
            return False
        if not present_norm.startswith("amount:"):
            return False
        if not self._looks_like_amount_value(present_norm):
            return False
        if not self._is_likely_amount_column(present_table, present_row, col):
            return False

        if missing_row is None:
            missing_row = self._find_matching_business_row(present_table, present_row, missing_table)
        if missing_row is None:
            return False

        if not self._rows_have_matching_business_identity(present_table, present_row, missing_table, missing_row):
            return False

        amount = present_norm.split(":", 1)[1]
        present_count = self._row_amount_count(present_table, present_row, amount)
        missing_count = self._row_amount_count(missing_table, missing_row, amount)
        if present_count <= missing_count:
            return False

        source_text = self._row_plain_source_text(missing_table, missing_row)
        if not source_text and missing_block is not None:
            source_text = missing_block.text
        source_count = self._amount_occurrence_count(source_text, amount)
        return source_count >= present_count

    def _rows_share_business_identity(
        self,
        original: StructuredTable,
        compare: StructuredTable,
        orig_row: int,
        comp_row: int,
    ) -> bool:
        return self._rows_have_matching_business_identity(original, orig_row, compare, comp_row)

    def _rows_have_matching_business_identity(
        self,
        left: StructuredTable,
        left_row: int,
        right: StructuredTable,
        right_row: int,
    ) -> bool:
        left_key = self._row_business_key(left, left_row)
        right_key = self._row_business_key(right, right_row)
        if left_key and right_key:
            if left_key == right_key:
                return True
            if SequenceMatcher(None, left_key, right_key).ratio() >= 0.82:
                return True
        left_seq = self._row_sequence(left, left_row)
        right_seq = self._row_sequence(right, right_row)
        return bool(left_seq and left_seq == right_seq and self._row_similarity(left, left_row, right, right_row) >= 0.72)

    def _find_matching_business_row(
        self,
        source_table: StructuredTable,
        source_row: int,
        target_table: StructuredTable,
    ) -> int | None:
        best_row = None
        best_score = 0.0
        source_key = self._row_business_key(source_table, source_row)
        for row in range(len(target_table.rows)):
            if not self._row_text(target_table, row):
                continue
            if not self._rows_have_matching_business_identity(source_table, source_row, target_table, row):
                continue
            target_key = self._row_business_key(target_table, row)
            score = self._row_similarity(source_table, source_row, target_table, row)
            if source_key and target_key:
                score = max(score, SequenceMatcher(None, source_key, target_key).ratio())
            if score > best_score:
                best_score = score
                best_row = row
        return best_row

    def _row_amount_count(self, table: StructuredTable, row: int, amount: str) -> int:
        count = 0
        for cell in self._row_nonempty_cells(table, row):
            norm = self._normalize_cell_for_compare(cell.text)
            if norm == f"amount:{amount}" and self._is_likely_amount_column(table, row, cell.col_index):
                count += 1
        return count

    def _amount_occurrence_count(self, text: str, amount: str) -> int:
        if not text:
            return 0
        count = 0
        for match in AMOUNT_TOKEN_PATTERN.finditer(unicodedata.normalize("NFKC", text)):
            if self._canonical_amount(match.group(0)) == amount:
                count += 1
        return count

    @staticmethod
    def _looks_like_amount_value(norm: str) -> bool:
        if not norm.startswith("amount:"):
            return False
        try:
            return abs(float(norm.split(":", 1)[1])) >= 100.0
        except ValueError:
            return False

    def _is_likely_amount_column(self, table: StructuredTable, row: int, col: int) -> bool:
        if col < 0 or table.col_count <= 0:
            return False
        if table.col_count >= 8:
            return col >= 6
        if col >= max(0, table.col_count - 2):
            text = self._cell_text(table, row, col)
            return bool(self._canonical_amount(text))
        return False

    @staticmethod
    def _row_plain_source_text(table: StructuredTable, row: int) -> str:
        if row < 0 or row >= len(table.rows):
            return ""
        return getattr(table.rows[row], "source_text", "") or ""

    def _row_match_cells(self, table: StructuredTable, row: int) -> list[dict[str, object]]:
        cells = []
        for col in range(table.col_count):
            cell = self._anchor_cell(table, row, col)
            if not cell:
                continue
            norm = self._normalize_cell_for_compare(cell.text)
            if not norm or self._is_noise(norm):
                continue
            cells.append({"col": col, "text": cell.text, "norm": norm})
        return cells

    def _row_cell_match_score(
        self,
        original_norm: str,
        compare_norm: str,
        original_source: str,
        compare_source: str,
    ) -> float:
        if original_norm == compare_norm:
            return 1.0
        if self._is_similar_ocr_noise(original_norm, compare_norm):
            return 0.98

        long_norm, short_norm, short_source = "", "", ""
        if len(original_norm) > len(compare_norm):
            long_norm, short_norm, short_source = original_norm, compare_norm, compare_source
        elif len(compare_norm) > len(original_norm):
            long_norm, short_norm, short_source = compare_norm, original_norm, original_source
        if len(short_norm) >= 6 and short_norm in long_norm:
            extra = long_norm.replace(short_norm, "", 1)
            if len(extra) >= 2 and (long_norm in short_source or extra in short_source):
                return 0.93
        return 0.0

    def _is_possible_ocr_shift_row(
        self,
        original: StructuredTable,
        compare: StructuredTable,
        orig_row: int,
        comp_row: int,
        original_cells: list[dict[str, object]],
        compare_cells: list[dict[str, object]],
        orig_to_comp: dict[int, int],
    ) -> bool:
        if len(orig_to_comp) < 2:
            return False

        off_column_matches = sum(1 for orig_col, comp_col in orig_to_comp.items() if orig_col != comp_col)
        if off_column_matches == 0:
            return False

        coverage = (2 * len(orig_to_comp)) / max(len(original_cells) + len(compare_cells), 1)
        if coverage < 0.6:
            return False

        preliminary_diffs = 0
        for col in range(max(original.col_count, compare.col_count)):
            if self._normalize_cell_for_compare(self._cell_text(original, orig_row, col)) != self._normalize_cell_for_compare(self._cell_text(compare, comp_row, col)):
                preliminary_diffs += 1
        if preliminary_diffs < 2:
            return False

        first_original = str(original_cells[0]["norm"])
        first_compare = str(compare_cells[0]["norm"])
        same_leading_identifier = first_original == first_compare
        row_score = self._row_similarity(original, orig_row, compare, comp_row)
        return same_leading_identifier or row_score >= 0.72 or off_column_matches >= 2

    def _row_source_text(self, table: StructuredTable, row: int, block: TextBlock | None) -> str:
        parts = [cell.text for cell in self._row_nonempty_cells(table, row)]
        if block is not None:
            if block.text:
                parts.append(block.text)
            if block.raw_html:
                parts.append(self._strip_html(block.raw_html))
        return self._normalize(" ".join(parts))

    def _cell_text(self, table: StructuredTable, row: int, col: int) -> str:
        cell = self._anchor_cell(table, row, col)
        return cell.text if cell else ""

    @staticmethod
    def _anchor_cell(table: StructuredTable, row: int, col: int):
        cell = table.get_cell(row, col)
        if not cell or cell.row_index != row or cell.col_index != col:
            return None
        return cell

    def _group_cell_diffs(self, cell_diffs: list[CellDiff], orig_table: StructuredTable, comp_table: StructuredTable) -> list[CellDiffGroup]:
        if not cell_diffs:
            return []
        by_row: dict[int, list[CellDiff]] = {}
        for cd in cell_diffs:
            by_row.setdefault(cd.row, []).append(cd)
        return [CellDiffGroup(row=row, diffs=diffs) for row, diffs in sorted(by_row.items())]

    # --- DiffItem construction ---

    def _make_diff(self, group: CellDiffGroup, index: int, orig_block: tuple[TextBlock, str] | None, comp_block: tuple[TextBlock, str] | None, orig_table: StructuredTable | None = None, comp_table: StructuredTable | None = None) -> DiffItem:
        descriptions = []
        for cd in group.diffs:
            if cd.diff_type == "MODIFY":
                descriptions.append(f"'{cd.original_text[:20]}' -> '{cd.compare_text[:20]}'")
            elif cd.diff_type == "ADD":
                descriptions.append(f"新增 '{cd.compare_text[:20]}'")
            else:
                descriptions.append(f"删除 '{cd.original_text[:20]}'")

        orig_texts = [cd.original_text for cd in group.diffs if cd.original_text]
        comp_texts = [cd.compare_text for cd in group.diffs if cd.compare_text]
        readable = f"表格行{group.row + 1}: {', '.join(descriptions)}"

        orig_evidences = self._char_level_evidence(
            orig_table,
            group,
            orig_block[0] if orig_block else None,
            "original",
        )
        comp_evidences = self._char_level_evidence(
            comp_table,
            group,
            comp_block[0] if comp_block else None,
            "compare",
        )

        # When one side lacks precise cell bboxes, clamp the layout fallback to a reasonable height
        needs_orig_evidence = self._side_needs_evidence(group, "original")
        needs_comp_evidence = self._side_needs_evidence(group, "compare")
        if (needs_orig_evidence and not orig_evidences) or (needs_comp_evidence and not comp_evidences):
            orig_bbox = self._row_evidence_bbox(orig_table, group, "original") if orig_table else None
            comp_bbox = self._row_evidence_bbox(comp_table, group, "compare") if comp_table else None
            ref_height = None
            if orig_bbox and comp_bbox:
                ref_height = min(orig_bbox.y1 - orig_bbox.y0, comp_bbox.y1 - comp_bbox.y0)
            elif orig_bbox:
                ref_height = orig_bbox.y1 - orig_bbox.y0
            elif comp_bbox:
                ref_height = comp_bbox.y1 - comp_bbox.y0

            allow_orig_block_fallback = orig_bbox is not None or not self._table_has_cell_bboxes(orig_table)
            allow_comp_block_fallback = comp_bbox is not None or not self._table_has_cell_bboxes(comp_table)

            if needs_orig_evidence and not orig_evidences and orig_block and allow_orig_block_fallback:
                orig_evidences = self._evidence_from_block(
                    orig_block,
                    self._side_fallback_highlight_type(group, "original"),
                    bbox_override=orig_bbox,
                    max_height=ref_height,
                )
            if needs_comp_evidence and not comp_evidences and comp_block and allow_comp_block_fallback:
                comp_evidences = self._evidence_from_block(
                    comp_block,
                    self._side_fallback_highlight_type(group, "compare"),
                    bbox_override=comp_bbox,
                    max_height=ref_height,
                )

        table_type = self._joint_table_type(orig_table, comp_table)
        review_flags = []
        if needs_orig_evidence and not orig_evidences:
            review_flags.append("LOW_CONFIDENCE_ORIGINAL_TABLE_EVIDENCE")
        if needs_comp_evidence and not comp_evidences:
            review_flags.append("LOW_CONFIDENCE_COMPARE_TABLE_EVIDENCE")

        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="MODIFY",
            title=f"表格字段：{self._table_type_label(table_type)}",
            original_text=" | ".join(orig_texts),
            compare_text=" | ".join(comp_texts),
            original_snippet=" | ".join(orig_texts)[:300],
            compare_snippet=" | ".join(comp_texts)[:300],
            readable_change=readable,
            source_type="table",
            review_flags=review_flags,
            original_evidence=orig_evidences,
            compare_evidence=comp_evidences,
        )

    def _whole_table_diff(
        self, table: StructuredTable, diff_type: str, index: int, block: tuple[TextBlock, str] | None, other_block: tuple[TextBlock, str] | None
    ) -> DiffItem:
        all_text = table.all_cell_text()[:300]
        orig_block = block if diff_type == "DELETE" else None
        comp_block = block if diff_type == "ADD" else None
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type=diff_type,
            title=f"表格：{self._table_type_label(self._table_type(table))}",
            original_text=all_text if diff_type == "DELETE" else "",
            compare_text=all_text if diff_type == "ADD" else "",
            original_snippet=all_text if diff_type == "DELETE" else "",
            compare_snippet=all_text if diff_type == "ADD" else "",
            readable_change=f"整表{'删除' if diff_type == 'DELETE' else '新增'}",
            source_type="table",
            original_evidence=self._evidence_from_block(orig_block, diff_type) if orig_block else [],
            compare_evidence=self._evidence_from_block(comp_block, diff_type) if comp_block else [],
        )

    def _find_block(self, blocks: list[tuple[TextBlock, str]], table: StructuredTable) -> tuple[TextBlock, str] | None:
        for block, text in blocks:
            if table.source_block_id and block.block_id == table.source_block_id:
                return (block, text)
            if block.page_no == table.page_no:
                table_text = table.all_cell_text()
                block_clean = self._normalize(self._strip_html(text))
                table_clean = self._normalize(table_text)
                if table_clean and block_clean and SequenceMatcher(None, block_clean[:200], table_clean[:200]).ratio() > 0.5:
                    return (block, text)
        return None

    def _evidence_from_block(self, block_and_text: tuple[TextBlock, str] | None, highlight_type: str, bbox_override: BBox | None = None, max_height: float | None = None) -> list[EvidenceBox]:
        if block_and_text is None:
            return []
        block, _ = block_and_text
        bbox = bbox_override or block.layout_bbox or block.bbox
        if max_height and not bbox_override and (bbox.y1 - bbox.y0) > max_height:
            mid_y = (bbox.y0 + bbox.y1) / 2
            bbox = BBox(x0=bbox.x0, y0=mid_y - max_height / 2, x1=bbox.x1, y1=mid_y + max_height / 2)
        return [EvidenceBox(
            page_no=block.page_no,
            bbox=bbox,
            method="table_cell",
            text=(block.text or "")[:300],
            highlight_type=highlight_type,
        )]

    def _row_evidence_bbox(self, table: StructuredTable, group: CellDiffGroup, side: str) -> BBox | None:
        bboxes: list[BBox] = []
        for cd in group.diffs:
            row = self._diff_row(cd, side)
            if row is None:
                continue
            col = self._diff_col(cd, side)
            if col is None:
                continue
            cell = self._anchor_cell(table, row, col)
            bbox = self._usable_cell_bbox(table, row, cell, cell.text if cell else "")
            if bbox:
                bboxes.append(bbox)
        if not bboxes:
            return None
        # Use the narrowest cell in the row as base height to clamp rowspan cells
        row_heights: list[float] = []
        rows = {row for cd in group.diffs if (row := self._diff_row(cd, side)) is not None}
        for row in rows:
            for c in range(table.col_count):
                cell = self._anchor_cell(table, row, c)
                if cell and cell.bbox:
                    row_heights.append(cell.bbox.y1 - cell.bbox.y0)
        # Fallback: table-wide median cell height when entire row is rowspan
        if row_heights:
            base_h = min(row_heights)
        else:
            base_h = 0
        if base_h <= 0 or base_h > 80:
            all_heights: list[float] = []
            for row in table.rows:
                for cell in row.cells:
                    if cell.bbox:
                        all_heights.append(cell.bbox.y1 - cell.bbox.y0)
            if all_heights:
                all_heights.sort()
                table_median = all_heights[len(all_heights) // 2]
                if base_h <= 0 or table_median < base_h:
                    base_h = table_median
        if base_h > 0:
            clamped: list[BBox] = []
            for b in bboxes:
                mid_y = (b.y0 + b.y1) / 2
                clamped.append(BBox(x0=b.x0, y0=mid_y - base_h / 2, x1=b.x1, y1=mid_y + base_h / 2))
            bboxes = clamped
        return BBox(
            x0=min(b.x0 for b in bboxes),
            y0=min(b.y0 for b in bboxes),
            x1=max(b.x1 for b in bboxes),
            y1=max(b.y1 for b in bboxes),
        )

    @staticmethod
    def _table_has_cell_bboxes(table: StructuredTable | None) -> bool:
        if table is None:
            return False
        return any(cell.bbox is not None for row in table.rows for cell in row.cells)

    def _usable_cell_bbox(self, table, row: int, cell, text: str) -> BBox | None:
        if not cell or not cell.bbox:
            return None
        bbox = cell.bbox
        height = bbox.y1 - bbox.y0
        if height <= 0:
            return None
        median = self._table_median_cell_height(table)
        row_span = max(1, int(getattr(cell, "rowspan", 1) or 1))
        compact_text = self._normalize(text)
        if not self._cell_bbox_matches_column_order(table, row, cell, bbox):
            return None
        if row_span > 1 and height > median * 2.2:
            return None
        if len(compact_text) <= 12 and height > max(50.0, median * 3.0):
            return None
        if height > max(120.0, median * 7.0):
            return None
        return bbox

    def _cell_bbox_matches_column_order(self, table, row: int, cell, bbox: BBox) -> bool:
        col = int(getattr(cell, "col_index", -1))
        if col < 0:
            return True
        tolerance = 4.0
        for other_col in range(table.col_count):
            if other_col == col:
                continue
            other = self._anchor_cell(table, row, other_col)
            if not other or not other.bbox:
                continue
            other_bbox = other.bbox
            if other_col < col and other_bbox.x0 > bbox.x1 + tolerance:
                return False
            if other_col > col and other_bbox.x1 < bbox.x0 - tolerance:
                return False
        return True

    def _table_median_cell_height(self, table) -> float:
        heights: list[float] = []
        for row in table.rows:
            for cell in row.cells:
                if cell.bbox:
                    height = cell.bbox.y1 - cell.bbox.y0
                    if height > 0:
                        heights.append(height)
        if not heights:
            return 18.0
        heights.sort()
        small_heights = [height for height in heights if height <= 80.0] or heights
        return max(8.0, small_heights[len(small_heights) // 4])

    @staticmethod
    def _cell_char_bbox(cell_bbox: BBox, text: str, start: int, end: int) -> BBox:
        n = max(len(text), 1)
        width = max(cell_bbox.x1 - cell_bbox.x0, 1.0)
        start = max(0, min(start, n))
        end = max(0, min(end, n))
        if end < start:
            start, end = end, start

        x0 = cell_bbox.x0 + width * start / n
        x1 = cell_bbox.x0 + width * end / n
        if x1 <= x0:
            caret_width = min(max(width / n * 0.7, 1.5), width)
            center = cell_bbox.x0 + width * start / n
            x0 = max(cell_bbox.x0, center - caret_width / 2)
            x1 = min(cell_bbox.x1, center + caret_width / 2)
            if x1 <= x0:
                x1 = min(cell_bbox.x1, x0 + 1.0)
        return BBox(x0=x0, y0=cell_bbox.y0, x1=x1, y1=cell_bbox.y1)

    def _cell_char_bbox_for_text(self, table, row: int, cell, text: str, start: int, end: int) -> BBox | None:
        if not cell or not cell.bbox:
            return None
        bbox = cell.bbox
        height = bbox.y1 - bbox.y0
        median = self._table_median_cell_height(table)
        if not self._cell_bbox_matches_column_order(table, row, cell, bbox):
            return None
        if max(1, int(getattr(cell, "rowspan", 1) or 1)) > 1 and height > median * 2.2:
            return None
        if height <= max(36.0, median * 2.2):
            return self._cell_char_bbox(bbox, text, start, end)

        compact = self._normalize(text)
        if len(compact) <= 12 and height > max(50.0, median * 3.0):
            return None

        n = max(len(text), 1)
        line_count = max(1, min(12, round(height / max(median, 1.0))))
        chars_per_line = max(1, (n + line_count - 1) // line_count)
        start = max(0, min(start, n))
        end = max(0, min(end, n))
        if end < start:
            start, end = end, start
        start_line = min(line_count - 1, start // chars_per_line)
        end_line = min(line_count - 1, max(start, end - 1) // chars_per_line)
        line_height = height / line_count
        y0 = bbox.y0 + start_line * line_height
        y1 = bbox.y0 + (end_line + 1) * line_height
        if end_line != start_line:
            return BBox(x0=bbox.x0, y0=y0, x1=bbox.x1, y1=y1)

        line_start = start_line * chars_per_line
        line_end = min(n, line_start + chars_per_line)
        line_len = max(1, line_end - line_start)
        local_start = max(0, start - line_start)
        local_end = max(local_start + 1, min(line_len, end - line_start))
        width = max(1.0, bbox.x1 - bbox.x0)
        x0 = bbox.x0 + width * local_start / line_len
        x1 = bbox.x0 + width * local_end / line_len
        return BBox(x0=x0, y0=y0, x1=x1, y1=y1)

    def _char_level_evidence(
        self,
        table: StructuredTable | None,
        group: CellDiffGroup,
        block: TextBlock | None,
        side: str,
    ) -> list[EvidenceBox]:
        if table is None:
            return []
        evidences: list[EvidenceBox] = []
        for cd in group.diffs:
            row = self._diff_row(cd, side)
            if row is None:
                continue
            col = self._diff_col(cd, side)
            if col is None:
                continue
            cell = self._anchor_cell(table, row, col)
            if not cell or not cell.bbox:
                continue

            if side == "original":
                if cd.diff_type == "ADD":
                    continue
                text = cd.original_text
            else:
                if cd.diff_type == "DELETE":
                    continue
                text = cd.compare_text
            if not cd.char_segments:
                if not text:
                    continue
                estimated_bbox = self._usable_cell_bbox(table, row, cell, text)
                bbox = self._block_char_bbox_for_fragment(block, cell.bbox, text, estimated_bbox) or estimated_bbox
                if not bbox:
                    continue
                highlight_type = "DELETE" if side == "original" else "ADD"
                evidences.append(EvidenceBox(
                    page_no=getattr(cell, "page_no", block.page_no if block else 1),
                    bbox=bbox,
                    method="table_cell",
                    text=text,
                    highlight_type=highlight_type,
                ))
                continue
            for start, end, highlight_type in self._side_char_ranges(cd, side):
                evidence_text = text[start:end]
                if not evidence_text and text:
                    evidence_text = text[max(0, start - 1):min(len(text), start + 1)]
                estimated_bbox = self._cell_char_bbox_for_text(table, row, cell, text, start, end)
                bbox = self._block_char_bbox_for_fragment(block, cell.bbox, evidence_text, estimated_bbox) or estimated_bbox
                if not bbox:
                    continue
                evidences.append(EvidenceBox(
                    page_no=getattr(cell, "page_no", block.page_no if block else 1),
                    bbox=bbox,
                    method="table_cell",
                    text=evidence_text,
                    highlight_type=highlight_type,
                ))
        return evidences

    def _block_char_bbox_for_fragment(
        self,
        block: TextBlock | None,
        cell_bbox: BBox | None,
        fragment: str,
        estimated_bbox: BBox | None,
    ) -> BBox | None:
        fragment = fragment or ""
        if block is None or not block.char_boxes or not block.text or not self._normalize(fragment):
            return None

        candidates: list[tuple[BBox, list[CharBox]]] = []
        for start, end in self._fragment_spans(block.text, fragment):
            boxes = self._char_boxes_for_span(block.char_boxes, start, end)
            if not boxes:
                continue
            bbox = self._merge_char_box_bboxes(boxes)
            if bbox is None:
                continue
            if cell_bbox is not None and self._bbox_overlap_area(bbox, cell_bbox) <= 0:
                continue
            candidates.append((bbox, boxes))

        if not candidates:
            return None

        candidates.sort(
            key=lambda item: self._char_bbox_score(item[0], cell_bbox),
            reverse=True,
        )
        bbox, boxes = candidates[0]
        if not self._accept_block_char_bbox(bbox, boxes, fragment, estimated_bbox):
            return None
        return bbox

    def _fragment_spans(self, text: str, fragment: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        start = text.find(fragment)
        while start >= 0:
            spans.append((start, start + len(fragment)))
            start = text.find(fragment, start + 1)
        if spans:
            return spans

        normalized_text, index_map = self._normalized_index_map(text)
        normalized_fragment = self._normalize(fragment)
        if not normalized_fragment:
            return []
        start = normalized_text.find(normalized_fragment)
        while start >= 0:
            end = start + len(normalized_fragment)
            if start < len(index_map) and end - 1 < len(index_map):
                spans.append((index_map[start], index_map[end - 1] + 1))
            start = normalized_text.find(normalized_fragment, start + 1)
        return spans

    def _normalized_index_map(self, text: str) -> tuple[str, list[int]]:
        chars: list[str] = []
        index_map: list[int] = []
        for index, char in enumerate(text or ""):
            normalized = self._normalize(char)
            if not normalized:
                continue
            for normalized_char in normalized:
                chars.append(normalized_char)
                index_map.append(index)
        return "".join(chars), index_map

    @staticmethod
    def _char_boxes_for_span(char_boxes: list[CharBox], start: int, end: int) -> list[CharBox]:
        by_index: dict[int, list[CharBox]] = {}
        for fallback_index, char_box in enumerate(char_boxes):
            index = char_box.text_index if char_box.text_index is not None else fallback_index
            by_index.setdefault(index, []).append(char_box)
        result: list[CharBox] = []
        for index in range(start, end):
            result.extend(by_index.get(index, []))
        return [box for box in result if box.char.strip()]

    @staticmethod
    def _merge_char_box_bboxes(char_boxes: list[CharBox]) -> BBox | None:
        if not char_boxes:
            return None
        return BBox(
            x0=min(box.bbox.x0 for box in char_boxes),
            y0=min(box.bbox.y0 for box in char_boxes),
            x1=max(box.bbox.x1 for box in char_boxes),
            y1=max(box.bbox.y1 for box in char_boxes),
        )

    def _char_bbox_score(self, bbox: BBox, cell_bbox: BBox | None) -> tuple[float, float]:
        if cell_bbox is None:
            return (1.0, 0.0)
        overlap = self._bbox_overlap_area(bbox, cell_bbox)
        area = max(self._bbox_area(bbox), 1.0)
        overlap_ratio = overlap / area
        bbox_cx = (bbox.x0 + bbox.x1) / 2
        bbox_cy = (bbox.y0 + bbox.y1) / 2
        cell_cx = (cell_bbox.x0 + cell_bbox.x1) / 2
        cell_cy = (cell_bbox.y0 + cell_bbox.y1) / 2
        distance = abs(bbox_cx - cell_cx) + abs(bbox_cy - cell_cy)
        return (overlap_ratio, -distance)

    @staticmethod
    def _bbox_overlap_area(left: BBox, right: BBox) -> float:
        x0 = max(left.x0, right.x0)
        y0 = max(left.y0, right.y0)
        x1 = min(left.x1, right.x1)
        y1 = min(left.y1, right.y1)
        if x1 <= x0 or y1 <= y0:
            return 0.0
        return (x1 - x0) * (y1 - y0)

    @staticmethod
    def _bbox_area(bbox: BBox) -> float:
        return max(0.0, bbox.x1 - bbox.x0) * max(0.0, bbox.y1 - bbox.y0)

    @staticmethod
    def _accept_block_char_bbox(
        bbox: BBox,
        char_boxes: list[CharBox],
        fragment: str,
        estimated_bbox: BBox | None,
    ) -> bool:
        if estimated_bbox is None:
            return True
        fragment_len = len(fragment.strip())
        if fragment_len <= 3:
            width = bbox.x1 - bbox.x0
            estimated_width = max(estimated_bbox.x1 - estimated_bbox.x0, 1.0)
            if width > max(24.0, estimated_width * 1.8):
                return False
        height = bbox.y1 - bbox.y0
        estimated_height = max(estimated_bbox.y1 - estimated_bbox.y0, 1.0)
        if height > estimated_height * 2.5:
            return False
        return True

    def _side_needs_evidence(self, group: CellDiffGroup, side: str) -> bool:
        for cd in group.diffs:
            if cd.char_segments:
                if self._side_char_ranges(cd, side):
                    return True
            elif side == "original" and cd.diff_type == "DELETE" and self._normalize(cd.original_text):
                return True
            elif side == "compare" and cd.diff_type == "ADD" and self._normalize(cd.compare_text):
                return True
        return False

    def _side_fallback_highlight_type(self, group: CellDiffGroup, side: str) -> str:
        types: set[str] = set()
        for cd in group.diffs:
            if cd.char_segments:
                types.update(highlight_type for _, _, highlight_type in self._side_char_ranges(cd, side))
            elif side == "original" and cd.diff_type == "DELETE" and self._normalize(cd.original_text):
                types.add("DELETE")
            elif side == "compare" and cd.diff_type == "ADD" and self._normalize(cd.compare_text):
                types.add("ADD")
        return next(iter(types)) if len(types) == 1 else "MODIFY"

    def _side_char_ranges(self, cd: CellDiff, side: str) -> list[tuple[int, int, str]]:
        ranges: list[tuple[int, int, str]] = []
        for segment in cd.char_segments:
            original_fragment = cd.original_text[segment.orig_start:segment.orig_end]
            compare_fragment = cd.compare_text[segment.comp_start:segment.comp_end]
            original_visible = bool(self._normalize(original_fragment))
            compare_visible = bool(self._normalize(compare_fragment))

            if side == "original":
                if segment.tag == "insert" or not original_visible:
                    continue
                highlight_type = "DELETE" if not compare_visible else "MODIFY"
                ranges.append((segment.orig_start, segment.orig_end, highlight_type))
            else:
                if segment.tag == "delete" or not compare_visible:
                    continue
                highlight_type = "ADD" if not original_visible else "MODIFY"
                ranges.append((segment.comp_start, segment.comp_end, highlight_type))
        return ranges

    @staticmethod
    def _diff_row(cell_diff: CellDiff, side: str) -> int | None:
        return cell_diff.original_row if side == "original" else cell_diff.compare_row

    @staticmethod
    def _diff_col(cell_diff: CellDiff, side: str) -> int | None:
        return cell_diff.original_col if side == "original" else cell_diff.compare_col

    # --- Flat-text fallback ---

    def _flat_compare(
        self,
        original_blocks: list[tuple[TextBlock, str]],
        compare_blocks: list[tuple[TextBlock, str]],
        start_index: int,
        warnings: list[str],
    ) -> tuple[list[DiffItem], list[str]]:
        original_units = self._extract_flat_units(original_blocks)
        compare_units = self._extract_flat_units(compare_blocks)
        if not original_units and not compare_units:
            return [], warnings

        original_unmatched, compare_unmatched = self._unmatched_units(original_units, compare_units)
        if not original_unmatched and not compare_unmatched:
            return [], warnings
        if len(original_unmatched) + len(compare_unmatched) > 8:
            warnings.append("表格抽取顺序差异较大，已跳过大范围表格字符级高亮。")
            return [], warnings

        diffs: list[DiffItem] = []
        next_index = start_index
        paired_original: set[int] = set()
        paired_compare: set[int] = set()

        for left_index, right_index, score in self._similar_pairs(original_unmatched, compare_unmatched):
            if score < 0.82:
                continue
            left = original_unmatched[left_index]
            right = compare_unmatched[right_index]
            if self._is_layout_only_change(self._normalize(left.text), self._normalize(right.text)):
                paired_original.add(left_index)
                paired_compare.add(right_index)
                continue
            diffs.append(DiffItem(
                diff_id=generate_diff_id(next_index),
                diff_type="MODIFY",
                title="表格字段：标的物",
                original_text=left.text,
                compare_text=right.text,
                original_snippet=left.text,
                compare_snippet=right.text,
                readable_change=f"表格字段变更：{left.text} -> {right.text}",
                source_type="table",
                original_evidence=[self._evidence(left, "MODIFY")],
                compare_evidence=[self._evidence(right, "MODIFY")],
            ))
            next_index += 1
            paired_original.add(left_index)
            paired_compare.add(right_index)

        deletions = [u for i, u in enumerate(original_unmatched) if i not in paired_original]
        additions = [u for i, u in enumerate(compare_unmatched) if i not in paired_compare]
        if deletions or additions:
            warnings.append("部分表格文本无法可靠按行/单元格配对，已跳过大范围表格字符级高亮。")

        return diffs, warnings

    def _extract_flat_units(self, blocks: list[tuple[TextBlock, str]]) -> list[_FlatUnit]:
        units: list[_FlatUnit] = []
        for block, text in blocks:
            for line in self._clean_text(text).splitlines():
                line = line.strip()
                if not line:
                    continue
                normalized = self._normalize(line)
                if not normalized or self._is_noise(normalized):
                    continue
                units.append(_FlatUnit(text=line, normalized=normalized, page_no=block.page_no, bbox=block.bbox))
        return units

    def _unmatched_units(self, original: list[_FlatUnit], compare: list[_FlatUnit]) -> tuple[list[_FlatUnit], list[_FlatUnit]]:
        from collections import Counter
        compare_counts = Counter(u.normalized for u in compare)
        original_unmatched: list[_FlatUnit] = []
        for unit in original:
            if compare_counts[unit.normalized] > 0:
                compare_counts[unit.normalized] -= 1
            else:
                original_unmatched.append(unit)
        original_counts = Counter(u.normalized for u in original)
        compare_unmatched: list[_FlatUnit] = []
        for unit in compare:
            if original_counts[unit.normalized] > 0:
                original_counts[unit.normalized] -= 1
            else:
                compare_unmatched.append(unit)
        return original_unmatched, compare_unmatched

    def _similar_pairs(self, original: list[_FlatUnit], compare: list[_FlatUnit]) -> list[tuple[int, int, float]]:
        candidates: list[tuple[int, int, float]] = []
        for li, left in enumerate(original):
            for ri, right in enumerate(compare):
                score = SequenceMatcher(None, left.normalized, right.normalized).ratio()
                if score >= 0.7:
                    candidates.append((li, ri, score))
        candidates.sort(key=lambda x: x[2], reverse=True)
        used_l: set[int] = set()
        used_r: set[int] = set()
        result: list[tuple[int, int, float]] = []
        for li, ri, score in candidates:
            if li in used_l or ri in used_r:
                continue
            used_l.add(li)
            used_r.add(ri)
            result.append((li, ri, score))
        return result

    def _is_layout_only_change(self, left: str, right: str) -> bool:
        left_no_row = re.sub(r"^\d{1,2}", "", left)
        right_no_row = re.sub(r"^\d{1,2}", "", right)
        if left_no_row == right_no_row:
            return True
        if left in right or right in left:
            return True
        return self._canonical_config(left) == self._canonical_config(right)

    def _canonical_config(self, text: str) -> str:
        text = re.sub(r"^\d{1,2}", "", text)
        text = text.replace(":", "").replace(";", "").replace(",", "").replace(".", "")
        text = text.replace("；", "").replace("，", "").replace("。", "")
        return text

    def _evidence(self, unit: _FlatUnit, highlight_type: str) -> EvidenceBox:
        return EvidenceBox(
            page_no=unit.page_no,
            bbox=unit.bbox,
            method="table_cell",
            text=unit.text[:300],
            highlight_type=highlight_type,
        )

    def _clean_text(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text or "").replace("\r", "\n")
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line).strip()

    # --- Utilities ---

    def is_table_block(self, block: TextBlock) -> bool:
        block_type = (block.block_type or "").lower()
        return block_type in self.table_block_types

    @staticmethod
    def _normalize(text: str) -> str:
        text = unicodedata.normalize("NFKC", text or "")
        text = text.lower()
        text = re.sub(r"[\s\n\r\t]+", "", text)
        text = text.replace("：", ":").replace("；", ";").replace("，", ",").replace("。", ".")
        text = re.sub(r"[()（）]", "", text)
        return text.strip()

    def _normalize_cell_for_compare(self, text: str) -> str:
        date = self._canonical_date(text)
        if date:
            return f"date:{date}"
        percent = self._canonical_percent(text)
        if percent:
            return f"percent:{percent}"
        quantity = self._canonical_quantity(text)
        if quantity:
            return f"quantity:{quantity}"
        amount = self._canonical_amount(text)
        if amount:
            return f"amount:{amount}"
        return self._normalize(text)

    def _canonical_amount(self, text: str) -> str:
        raw = unicodedata.normalize("NFKC", text or "")
        compact = re.sub(r"\s+", "", raw)
        if re.search(r"[年月日]", compact):
            return ""
        if not re.fullmatch(r"(?:人民币|¥|￥)?[+-]?\d[\d,]*(?:\.\d+)?(?:万)?元?", compact):
            return ""
        match = re.fullmatch(r"(?:人民币|¥|￥)?([+-]?\d[\d,]*(?:\.\d+)?)(万)?元?", compact)
        if not match:
            return ""
        value = float(match.group(1).replace(",", ""))
        if match.group(2) or "万元" in compact:
            value *= 10000
        return f"{value:.2f}"

    def _canonical_date(self, text: str) -> str:
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
        match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", compact)
        if match:
            return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        match = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", compact)
        if match:
            return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        return ""

    def _canonical_percent(self, text: str) -> str:
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
        match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)(%|％|百分之)", compact)
        if match:
            return f"{float(match.group(1)):.4f}"
        return ""

    def _canonical_quantity(self, text: str) -> str:
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
        match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)(套|台|个|项|批|份|件|人天|天|月|年)", compact)
        if match:
            value = float(match.group(1))
            return f"{value:.4f}{match.group(2)}"
        return ""

    def _joint_table_type(self, original: StructuredTable | None, compare: StructuredTable | None) -> str:
        types = [self._table_type(table) for table in (original, compare) if table is not None]
        if "product" in types:
            return "product"
        if "payment" in types:
            return "payment"
        if "cover" in types:
            return "cover"
        return types[0] if types else "generic"

    def _table_type(self, table) -> str:
        text = self._normalize(table.all_cell_text()) if table is not None else ""
        if not text:
            return "generic"
        if any(token in text for token in ("产品名称", "详细配置", "单价", "金额", "标的物")):
            return "product"
        if any(token in text for token in ("付款", "支付", "付款节点", "付款条件", "进度款", "验收款")):
            return "payment"
        if any(token in text for token in ("验收", "标准", "指标", "测试")):
            return "acceptance"
        if any(token in text for token in ("联系人", "电话", "邮箱", "通讯地址")):
            return "contact"
        if any(token in text for token in ("甲方", "乙方", "签订日期", "合同编号", "签订地点")):
            return "cover"
        return "generic"

    def _table_type_label(self, table_type: str) -> str:
        return TABLE_TYPE_LABELS.get(table_type, TABLE_TYPE_LABELS["generic"])

    def _is_amount_like(self, text: str) -> bool:
        return bool(self._canonical_amount(text) or re.fullmatch(r"[\d,.]+", text or ""))

    def _looks_like_person_or_role(self, text: str) -> bool:
        return bool(re.search(r"(联系人|负责人|经理|电话|手机|邮箱|@)", text or "") or len(text or "") <= 8)

    @staticmethod
    def _punctuation_fold(text: str) -> str:
        return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)

    @staticmethod
    def _is_noise(text: str) -> bool:
        return bool(NOISE_PATTERN.fullmatch(text)) or text in TABLE_HEADERS

    @staticmethod
    def _is_number_like(text: str) -> bool:
        return bool(re.fullmatch(r"[\d,.]+", text or ""))

    @staticmethod
    def _strip_html(text: str) -> str:
        return re.sub(r"<[^>]+>", "", text)


class _LogicalCell:
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


class _LogicalRow:
    __slots__ = ("row_index", "cells", "page_no", "source_block_id", "source_row", "section_title", "source_text")

    def __init__(
        self,
        row_index: int,
        cells: list[_LogicalCell],
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


class _LogicalTable:
    __slots__ = ("rows", "col_count", "page_no", "source_block_id", "source")

    def __init__(
        self,
        rows: list[_LogicalRow],
        col_count: int,
        page_no: int,
        source_block_id: str = "",
        source: str = "",
    ):
        self.rows = rows
        self.col_count = col_count
        self.page_no = page_no
        self.source_block_id = source_block_id
        self.source = source

    def get_cell(self, row: int, col: int) -> _LogicalCell | None:
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


class _SummaryPair:
    __slots__ = ("label", "amount", "canonical_amount", "line_index")

    def __init__(self, label: str, amount: str, canonical_amount: str, line_index: int):
        self.label = label
        self.amount = amount
        self.canonical_amount = canonical_amount
        self.line_index = line_index


class _SummaryTransition:
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


class _FlatUnit:
    __slots__ = ("text", "normalized", "page_no", "bbox")

    def __init__(self, text: str, normalized: str, page_no: int, bbox: BBox):
        self.text = text
        self.normalized = normalized
        self.page_no = page_no
        self.bbox = bbox
