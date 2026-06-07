"""Row repair and normalization for logical tables."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from app.models_table import StructuredTable
from app.services.table_compare.types import (
    _LogicalCell,
    _LogicalRow,
    _SummaryPair,
    _SummaryTransition,
)
from app.services.table_compare import utils


class TableRepairService:
    """All row repair, normalization, splitting, and summary methods."""

    # --- Merged sequence row normalization ---

    def normalize_merged_sequence_rows(self, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
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
        nonempty = [cell for cell in row.cells if utils.normalize(cell.text)]
        if not nonempty:
            return False
        if len(nonempty) > 5:
            return False
        texts = [utils.normalize_cell_for_compare(cell.text) for cell in nonempty]
        if any(utils.is_number_like(text) for text in texts) and len(nonempty) == 1:
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
                if continuation_cell is not None and utils.normalize(continuation_cell.text):
                    cont_text = continuation_cell.text
                    if col not in per_col_parts or not utils.normalize(text) or utils.normalize(text) == utils.normalize(cont_text):
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
        if len(sentence_parts) == count and all(utils.normalize(part) for part in sentence_parts):
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
        nonempty = [cell for cell in cells if utils.normalize(cell.text)]
        if not nonempty:
            return ""
        text = utils.normalize(nonempty[0].text)
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

    # --- Merged adjacent sequence row repair ---

    def repair_merged_adjacent_sequence_rows(self, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
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

        detail_split = self._split_adjacent_sequence_detail_merged_row(row, missing_seq, col_count, candidate)
        if detail_split is not None:
            return detail_split

        name_text = self._cell_text_from_row(row, 1)
        detail_text = self._cell_text_from_row(row, 2)
        brand_text = self._cell_text_from_row(row, 3)

        missing_name = self._merged_name_suffix(name_text, detail_text, candidate["name"])
        if not missing_name:
            missing_name = candidate["name"]

        merged_name_norm = utils.normalize(name_text)
        current_detail_norm = utils.normalize(detail_text)
        missing_name_norm = utils.normalize(missing_name)
        source_supports_name = self._source_contains_token(
            utils.normalize(row.source_text),
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
        if not (brand_repeated or candidate["brand"] and utils.normalize(candidate["brand"]) in utils.normalize(brand_text)):
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

        if len([cell for cell in missing_cells if utils.normalize(cell.text)]) < 5:
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

    def _split_adjacent_sequence_detail_merged_row(
        self,
        row: _LogicalRow,
        missing_seq: int,
        col_count: int,
        candidate: dict[str, str],
    ) -> list[_LogicalRow] | None:
        name_text = self._cell_text_from_row(row, 1)
        detail_text = self._cell_text_from_row(row, 2)
        brand_text = self._cell_text_from_row(row, 3)
        missing_detail = candidate["detail"]
        missing_detail_norm = utils.normalize(missing_detail)
        detail_norm = utils.normalize(detail_text)
        if not missing_detail_norm or missing_detail_norm not in detail_norm:
            return None

        source_norm = utils.normalize(row.source_text)
        if not self._source_contains_token(source_norm, missing_detail_norm, allow_loose_cjk=True):
            return None

        current_detail = self._remove_merged_detail_text(detail_text, missing_detail)
        current_detail_norm = utils.normalize(current_detail)
        if not current_detail_norm or current_detail_norm == detail_norm:
            return None
        if current_detail_norm == missing_detail_norm:
            return None

        current_name_norm = utils.normalize(name_text)
        if current_name_norm and current_name_norm not in current_detail_norm and current_detail_norm not in current_name_norm:
            if SequenceMatcher(None, current_name_norm, current_detail_norm).ratio() < 0.72:
                return None

        current_brand = self._dedupe_repeated_cell_text(brand_text)
        kept_cells: list[_LogicalCell] = []
        missing_cells: list[_LogicalCell] = []
        for col in range(col_count):
            source_cell = self._cell_at_col(row, col) or row.cells[0]
            kept_text = self._cell_text_from_row(row, col)
            missing_text = ""
            if col == 0:
                kept_text = str(self._row_sequence_int(row) or kept_text)
                missing_text = str(missing_seq)
            elif col == 2:
                kept_text = current_detail
                missing_text = missing_detail
            elif col == 1:
                missing_text = candidate["name"] or missing_detail
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

        if len([cell for cell in missing_cells if utils.normalize(cell.text)]) < 5:
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

    def _remove_merged_detail_text(self, detail_text: str, missing_detail: str) -> str:
        raw = unicodedata.normalize("NFKC", detail_text or "").strip()
        missing_raw = unicodedata.normalize("NFKC", missing_detail or "").strip()
        if not raw or not missing_raw:
            return raw
        if missing_raw in raw:
            return raw.replace(missing_raw, "", 1).strip()

        detail_norm = utils.normalize(raw)
        missing_norm = utils.normalize(missing_raw)
        if missing_norm and missing_norm in detail_norm:
            return detail_norm.replace(missing_norm, "", 1).strip()
        return raw

    def _missing_sequence_candidate_from_source(self, source_text: str, missing_seq: int) -> dict[str, str] | None:
        tokens = self._source_line_tokens(source_text)
        if not tokens:
            return None
        for index, token in enumerate(tokens):
            if utils.normalize(token) != str(missing_seq):
                continue
            window = tokens[index + 1:index + 10]
            detail = self._first_detail_token(window)
            if not detail:
                continue
            detail_index = window.index(detail)
            brand = self._first_brand_token(window[detail_index + 1:])
            unit = utils.first_unit_token(window[detail_index + 1:])
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
                previous_norm = utils.normalize(previous)
                detail_norm = utils.normalize(detail)
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
            norm = utils.normalize(token)
            if self._is_source_row_field_noise(norm):
                continue
            if len(norm) >= 4:
                return token
        return ""

    def _first_brand_token(self, tokens: list[str]) -> str:
        for token in tokens:
            norm = utils.normalize(token)
            if self._is_source_row_field_noise(norm):
                continue
            if 2 <= len(norm) <= 12:
                return token
        return ""

    def _first_quantity_token(self, tokens: list[str]) -> str:
        for token in tokens:
            norm = utils.normalize(token)
            if re.fullmatch(r"\d{1,3}(?:\.\d+)?", norm) and not utils.looks_like_amount_value(f"amount:{utils.canonical_amount(token)}"):
                return token
        return ""

    def _is_source_row_field_noise(self, norm: str) -> bool:
        return (
            not norm
            or utils.is_number_like(norm)
            or norm in {"套", "台", "个", "项", "批", "份", "件", "年", "月", "天", "人天"}
            or bool(utils.canonical_amount(norm))
        )

    def _looks_like_row_name_fragment(self, text: str) -> bool:
        norm = utils.normalize(text)
        if len(norm) < 2 or len(norm) > 20:
            return False
        if self._is_source_row_field_noise(norm):
            return False
        return bool(re.search(r"[一-鿿A-Za-z]", text or ""))

    def _merged_name_suffix(self, name_text: str, current_text: str, fallback: str) -> str:
        name_norm = utils.normalize(name_text)
        current_norm = utils.normalize(current_text)
        fallback_norm = utils.normalize(fallback)
        if fallback_norm and fallback_norm in name_norm and fallback_norm != current_norm:
            return fallback
        if current_norm and name_norm.startswith(current_norm) and len(name_norm) > len(current_norm) + 1:
            return name_norm[len(current_norm):]
        return fallback if fallback_norm and fallback_norm in name_norm else ""

    def _remove_merged_name_suffix(self, name_text: str, suffix: str) -> str:
        if not name_text or not suffix:
            return name_text
        name_norm = utils.normalize(name_text)
        suffix_norm = utils.normalize(suffix)
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
        if len(parts) == 2 and utils.normalize(parts[0]) == utils.normalize(parts[1]):
            return parts[0]
        compact = utils.normalize(raw)
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
            if (
                self._row_looks_like_summary(row, len(row.cells))
                or self._section_title_from_cells(row.cells, len(row.cells))
            ):
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

    def _section_title_from_cells(self, cells: list[_LogicalCell], col_count: int) -> str:
        nonempty = [cell for cell in cells if utils.normalize(cell.text)]
        if len(nonempty) != 1:
            return ""
        cell = nonempty[0]
        text = utils.normalize(cell.text)
        if utils.summary_labels(text):
            return ""
        if cell.colspan >= max(2, col_count - 1) or ("系统" in text and ("硬件" in text or "软件" in text or "v" in text)):
            return text
        return ""

    # --- Product continuation row repair ---

    def repair_product_continuation_rows(self, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
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
        if self._row_looks_like_summary(row, col_count) or self._section_title_from_cells(row.cells, col_count):
            return False
        nonempty = [cell for cell in row.cells if utils.normalize(cell.text)]
        if not nonempty or len(nonempty) > 3:
            return False
        texts = [utils.normalize_cell_for_compare(cell.text) for cell in nonempty]
        if all(utils.is_number_like(text) or utils.canonical_amount(text) for text in texts):
            return False
        return any(len(text) >= 4 and re.search(r"[一-鿿A-Za-z0-9]", text) for text in texts)

    def _merge_product_continuation_row(
        self,
        previous: _LogicalRow,
        continuation: _LogicalRow,
        col_count: int,
    ) -> _LogicalRow | None:
        if self._row_sequence_int(previous) is None:
            return None
        source_norm = utils.normalize("\n".join(
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
            if not utils.normalize(cont_text):
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
            if not utils.normalize(prev_text):
                continue
            if self._source_supports_text_join(source_norm, prev_text, continuation_cell.text):
                return col
        return None

    def _source_supports_text_join(self, source_norm: str, left: str, right: str) -> bool:
        left_norm = utils.normalize(left)
        right_norm = utils.normalize(right)
        if not left_norm or not right_norm:
            return False
        joined = utils.normalize(f"{left_norm}{right_norm}")
        spaced = utils.normalize(f"{left_norm} {right_norm}")
        folded_source = utils.punctuation_fold(source_norm)
        return (
            joined in source_norm
            or spaced in source_norm
            or utils.punctuation_fold(joined) in folded_source
            or utils.punctuation_fold(spaced) in folded_source
            or any(
                utils.normalize(f"{part}{right_norm}") in source_norm
                or utils.punctuation_fold(utils.normalize(f"{part}{right_norm}")) in folded_source
                for part in self._source_line_tokens(left)
                if len(utils.normalize(part)) >= 4
            )
        )

    def _join_continuation_text(self, left: str, right: str) -> str:
        left_raw = unicodedata.normalize("NFKC", left or "").strip()
        right_raw = unicodedata.normalize("NFKC", right or "").strip()
        if not left_raw:
            return right_raw
        if not right_raw:
            return left_raw
        if re.search(r"[\w一-鿿]$", left_raw) and re.search(r"^[\w一-鿿]", right_raw):
            return f"{left_raw} {right_raw}"
        return f"{left_raw}{right_raw}"

    # --- Shifted product field row repair ---

    def repair_shifted_product_field_rows(self, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
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
            text = utils.normalize(cell.text)
            if not text or utils.is_number_like(text) or utils.canonical_amount(cell.text):
                continue
            if len(text) >= 6 and re.search(r"[一-鿿A-Za-z]", text):
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
        if not utils.normalize(name) or not self._looks_like_brand_value(brand):
            return False
        if not utils.first_unit_token([unit]):
            return False
        if not re.fullmatch(r"\d{1,4}(?:\.\d+)?", utils.normalize(quantity)):
            return False
        price_amount = utils.canonical_amount(price)
        total_amount = utils.canonical_amount(amount)
        return bool(price_amount and total_amount)

    def _looks_like_brand_value(self, text: str) -> bool:
        norm = utils.normalize(text)
        if len(norm) < 2 or len(norm) > 20:
            return False
        if utils.is_number_like(norm) or utils.canonical_amount(text) or utils.first_unit_token([text]):
            return False
        return bool(re.search(r"[一-鿿A-Za-z]", text or ""))

    def _detail_text_from_shifted_row(self, row: _LogicalRow, detail_cell: _LogicalCell) -> str:
        detail = unicodedata.normalize("NFKC", detail_cell.text or "").strip()
        prefix = self._source_detail_prefix_for_shifted_row(row, detail)
        if prefix and utils.normalize(prefix) not in utils.normalize(detail):
            return f"{prefix} {detail}".strip()
        return detail

    def _source_detail_prefix_for_shifted_row(self, row: _LogicalRow, detail_text: str) -> str:
        tokens = self._source_line_tokens(row.source_text)
        if not tokens:
            return ""
        detail_parts = [part for part in self._source_line_tokens(detail_text) if utils.normalize(part)]
        if not detail_parts:
            detail_parts = [detail_text]
        name_norm = utils.normalize(self._cell_text_from_row(row, 1))
        brand_norm = utils.normalize(self._cell_text_from_row(row, 2))
        field_norms = {
            name_norm,
            brand_norm,
            utils.normalize(self._cell_text_from_row(row, 3)),
            utils.normalize(self._cell_text_from_row(row, 4)),
            utils.normalize(self._cell_text_from_row(row, 5)),
            utils.normalize(self._cell_text_from_row(row, 6)),
            utils.normalize(self._cell_text_from_row(row, 0)),
        }
        for token_index, token in enumerate(tokens):
            token_norm = utils.normalize(token)
            if not any(part and self._source_contains_token(token_norm, utils.normalize(part), True) for part in detail_parts):
                continue
            if token_index == 0:
                return ""
            prefix = unicodedata.normalize("NFKC", tokens[token_index - 1] or "").strip()
            prefix_norm = utils.normalize(prefix)
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
        source_norm = utils.normalize(row.source_text)
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
            if text and not self._source_contains_token(source_norm, utils.normalize(text), allow_loose_cjk=loose):
                return False
        return True

    # --- Embedded summary transition repair ---

    def repair_embedded_summary_transitions(self, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
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
        transition: _SummaryTransition,
    ) -> bool:
        row_text = utils.normalize(" ".join(cell.text for cell in row.cells))
        if not row_text:
            return False
        label_present = self._source_contains_token(row_text, utils.normalize(transition.label), allow_loose_cjk=True)

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
        transition: _SummaryTransition,
    ) -> bool:
        current_seq = self._row_sequence_int(row)
        next_seq = self._row_sequence_int(next_row) if next_row is not None else None
        if current_seq is None or current_seq < 2 or next_seq != 1:
            return False
        return next_row is not None and self._row_has_text(next_row, transition.section)

    def _row_is_summary_transition_fragment(
        self,
        row: _LogicalRow | None,
        transition: _SummaryTransition,
    ) -> bool:
        if row is None:
            return False
        if self._row_sequence_int(row) is not None:
            return False
        nonempty = [cell for cell in row.cells if utils.normalize(cell.text)]
        if len(nonempty) > 3:
            return False
        return self._row_has_canonical_amount(row, transition.canonical_amount) and self._row_has_text(row, transition.section)

    def _clean_embedded_summary_product_row(
        self,
        row: _LogicalRow,
        transition: _SummaryTransition,
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

    def _remove_embedded_summary_text(self, text: str, transition: _SummaryTransition) -> str:
        raw = unicodedata.normalize("NFKC", text or "").strip()
        if not raw:
            return text

        section_norm = utils.normalize(transition.section)
        if section_norm and utils.normalize(raw) == section_norm:
            return ""

        labels = utils.summary_labels(raw)
        if transition.label in labels:
            cleaned = re.sub(utils.loose_literal_pattern(transition.label), " ", raw)
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" |,，、;；")
            if cleaned and not utils.summary_labels_from_summary_text(cleaned):
                return cleaned
        return raw

    def _remove_summary_amount_from_amount_text(self, text: str, canonical_amount: str) -> str:
        amounts = utils.summary_amounts(text, labels=[])
        if not amounts:
            return text
        raw = unicodedata.normalize("NFKC", text or "").strip()
        residual = raw
        for amount in amounts:
            residual = re.sub(utils.loose_literal_pattern(amount), " ", residual, count=1)
        if re.sub(r"[\s,，、;；:.。|/\\\-]+", "", residual):
            return text

        removed = False
        remaining: list[str] = []
        for amount in amounts:
            if not removed and utils.canonical_amount(amount) == canonical_amount:
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
        amounts = utils.summary_amounts(price_cell.text, labels=[])
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
        if not utils.normalize(amount_cell.text):
            amount_cell.text = amounts[1]

    def _summary_transition_row(self, source_row: _LogicalRow, transition: _SummaryTransition) -> _LogicalRow:
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

    def _summary_transitions_from_text(self, text: str) -> list[_SummaryTransition]:
        raw = unicodedata.normalize("NFKC", utils.strip_html(text or ""))
        if not raw:
            return []

        lines = [line.strip() for line in raw.splitlines()]
        result: list[_SummaryTransition] = []
        pending_labels: list[str] = []
        for line_index, line in enumerate(lines):
            if not line:
                continue
            labels = utils.summary_labels_from_summary_text(line)
            amounts = utils.summary_amounts(line, labels=labels) if labels else []
            if not labels and pending_labels:
                amounts = utils.summary_amount_line_amounts(line)
            pending_labels.extend(labels)

            for amount in amounts:
                if not pending_labels:
                    break
                label = pending_labels.pop(0)
                canonical_amount_val = utils.canonical_amount(amount)
                section = self._next_summary_section_from_lines(lines, line_index)
                if canonical_amount_val and section:
                    result.append(_SummaryTransition(label, amount, canonical_amount_val, line_index, section))
        return result

    def _next_summary_section_from_lines(self, lines: list[str], start_index: int) -> str:
        for line in lines[start_index + 1:start_index + 8]:
            candidate = unicodedata.normalize("NFKC", line or "").strip()
            if not candidate:
                continue
            if utils.summary_labels_from_summary_text(candidate):
                return ""
            if utils.summary_amount_line_amounts(candidate):
                continue
            if self._is_summary_transition_section(candidate):
                return candidate
            if utils.normalize(candidate):
                return ""
        return ""

    def _is_summary_transition_section(self, text: str) -> bool:
        norm = utils.normalize(text)
        if len(norm) < 4 or len(norm) > 40:
            return False
        if utils.is_number_like(norm) or utils.canonical_amount(norm):
            return False
        if not re.search(r"[一-鿿A-Za-z]", text or ""):
            return False
        return "系统" in norm or "软件" in norm or "硬件" in norm or bool(re.search(r"v\d", norm, re.IGNORECASE))

    def _row_has_canonical_amount(self, row: _LogicalRow, canonical_amount: str) -> bool:
        for cell in row.cells:
            for amount in utils.summary_amounts(cell.text, labels=[]):
                if utils.canonical_amount(amount) == canonical_amount:
                    return True
        return False

    def _row_has_text(self, row: _LogicalRow, text: str) -> bool:
        row_text = utils.normalize(" ".join(cell.text for cell in row.cells))
        return self._source_contains_token(row_text, utils.normalize(text), allow_loose_cjk=True)

    def _amount_text_has_only_amounts(self, text: str, amounts: list[str]) -> bool:
        raw = unicodedata.normalize("NFKC", text or "")
        for amount in amounts:
            raw = re.sub(utils.loose_literal_pattern(amount), " ", raw, count=1)
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

    # --- Summary row normalization ---

    def normalize_summary_rows(
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
            row_labels, row_amounts = self._summary_labels_and_amounts_from_row(row)
            prefer_source_amounts = len(row_labels) > 1 and len(row_amounts) < len(row_labels)
            split_rows = self._split_merged_summary_row(row, col_count)
            if split_rows is None:
                normalized.append(row)
                continue

            for split_row in split_rows:
                label = self._summary_label_from_row(split_row)
                if not label:
                    continue
                pairs = source_pairs.get(split_row.source_block_id, [])
                current_amount = self._summary_amount_from_row(split_row)
                if prefer_source_amounts:
                    if not (
                        current_amount
                        and self._mark_matching_summary_pair_used(
                            pairs,
                            split_row.source_block_id,
                            label,
                            current_amount,
                            used_source_pairs,
                        )
                    ):
                        source_amount = self._next_summary_amount_from_source(
                            pairs,
                            split_row.source_block_id,
                            label,
                            used_source_pairs,
                        )
                        if source_amount:
                            self._set_summary_row_amount(split_row, source_amount)
                elif current_amount:
                    self._mark_matching_summary_pair_used(
                        pairs,
                        split_row.source_block_id,
                        label,
                        current_amount,
                        used_source_pairs,
                    )
                if not self._summary_amount_from_row(split_row):
                    fallback = self._next_summary_amount_from_source(
                        pairs,
                        split_row.source_block_id,
                        label,
                        used_source_pairs,
                    )
                    if fallback:
                        self._set_summary_row_amount(split_row, fallback)
                if self._summary_amount_from_row(split_row):
                    normalized.append(split_row)

        return self._reindex_logical_rows(normalized)

    def _summary_labels_and_amounts_from_row(self, row: _LogicalRow) -> tuple[list[str], list[str]]:
        labels: list[str] = []
        amounts: list[str] = []
        for cell in row.cells:
            cell_labels = utils.summary_labels_from_summary_text(cell.text)
            if cell_labels:
                labels.extend(cell_labels)
                amounts.extend(utils.summary_amounts(cell.text, labels=cell_labels))
                continue
            amounts.extend(utils.summary_amounts(cell.text))
        return labels, amounts

    def _mark_matching_summary_pair_used(
        self,
        pairs: list[_SummaryPair],
        source_block_id: str,
        label: str,
        amount: str,
        used: set[tuple[str, int]],
    ) -> bool:
        canonical_amount_val = utils.canonical_amount(amount)
        if not canonical_amount_val:
            return False
        for index, pair in enumerate(pairs):
            key = (source_block_id, index)
            if key in used:
                continue
            if pair.label != label or pair.canonical_amount != canonical_amount_val:
                continue
            used.add(key)
            return True
        return False

    # --- Orphan overflow sequence cell removal ---

    def remove_orphan_overflow_sequence_cells(self, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
        if col_count < 10:
            return rows

        repaired: list[_LogicalRow] = []
        changed = False
        for index, row in enumerate(rows):
            current_seq = self._row_sequence_int(row)
            if current_seq is None or not self._is_dense_product_row_for_overflow_sequence_repair(row, col_count):
                repaired.append(row)
                continue

            next_seq = self._next_sequence_int(rows, index + 1)
            kept_cells: list[_LogicalCell] = []
            removed = False
            for cell in row.cells:
                if self._is_orphan_overflow_sequence_cell(row, cell, current_seq, next_seq):
                    removed = True
                    continue
                kept_cells.append(cell)

            if not removed:
                repaired.append(row)
                continue

            changed = True
            repaired.append(_LogicalRow(
                row_index=row.row_index,
                cells=kept_cells,
                page_no=row.page_no,
                source_block_id=row.source_block_id,
                source_row=row.source_row,
                section_title=row.section_title,
                source_text=row.source_text,
            ))

        return self._reindex_logical_rows(repaired) if changed else rows

    def _is_dense_product_row_for_overflow_sequence_repair(self, row: _LogicalRow, col_count: int) -> bool:
        if self._summary_label_from_row(row):
            return False
        main_cells = [
            cell
            for cell in row.cells
            if cell.col_index < min(col_count, 8)
            and utils.normalize(cell.text)
            and not utils.is_noise(utils.normalize(cell.text))
        ]
        if len(main_cells) < 6:
            return False
        if not utils.normalize(self._cell_text_from_row(row, 1)):
            return False
        unit = self._cell_text_from_row(row, 4)
        if not utils.first_unit_token([unit]):
            return False
        return any(
            utils.canonical_amount(self._cell_text_from_row(row, col))
            for col in (6, 7)
        )

    def _is_orphan_overflow_sequence_cell(
        self,
        row: _LogicalRow,
        cell: _LogicalCell,
        current_seq: int,
        next_seq: int | None,
    ) -> bool:
        if cell.col_index < 9:
            return False
        text = utils.normalize(cell.text)
        if not re.fullmatch(r"\d{1,3}", text):
            return False
        candidate = int(text)
        if candidate != current_seq + 1:
            return False
        if next_seq != candidate + 1:
            return False

        main_data_cols = [
            other.col_index
            for other in row.cells
            if other.col_index < 9 and utils.normalize(other.text)
        ]
        return bool(main_data_cols and cell.col_index > max(main_data_cols))

    def _split_merged_summary_row(self, row: _LogicalRow, col_count: int) -> list[_LogicalRow] | None:
        if not self._is_summary_candidate_row(row, col_count):
            return None

        labels: list[str] = []
        label_cell: _LogicalCell | None = None
        amounts: list[str] = []
        amount_cell: _LogicalCell | None = None

        for cell in row.cells:
            cell_labels = self._summary_labels_from_text(cell.text)
            if cell_labels:
                labels.extend(cell_labels)
                label_cell = label_cell or cell
                amounts.extend(utils.summary_amounts(cell.text, labels=cell_labels))
                amount_cell = amount_cell or cell
                continue
            cell_amounts = utils.summary_amounts(cell.text)
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
        nonempty = [cell for cell in row.cells if utils.normalize(cell.text)]
        if not nonempty:
            return False

        if self._row_sequence_from_cells(row.cells) and len(nonempty) >= 4:
            return False

        label_cells = [
            cell for cell in nonempty
            if self._summary_labels_from_text(cell.text)
        ]
        if not label_cells:
            return False

        if len(nonempty) == 1:
            cell = nonempty[0]
            return bool(cell.colspan >= max(2, col_count - 2) or utils.summary_amounts(cell.text, labels=[]))

        allowed_cols = {cell.col_index for cell in label_cells}
        for cell in nonempty:
            if cell.col_index in allowed_cols:
                continue
            if not utils.summary_amounts(cell.text, labels=[]):
                return False

        return len(nonempty) <= 3 or any(cell.colspan >= max(2, col_count - 2) for cell in label_cells)

    def _row_looks_like_summary(self, row: _LogicalRow, col_count: int) -> bool:
        nonempty = [cell for cell in row.cells if utils.normalize(cell.text)]
        if not nonempty:
            return False
        if self._row_sequence_from_cells(row.cells) and len(nonempty) >= 4:
            return False

        label_cells = [cell for cell in nonempty if self._summary_labels_from_text(cell.text)]
        if not label_cells:
            return False

        combined_text = " ".join(cell.text for cell in nonempty)
        has_amount = bool(utils.summary_amounts(combined_text, labels=[]))
        has_chinese_amount = self._has_chinese_amount_text(combined_text)
        if not has_amount and not has_chinese_amount:
            return False

        return len(nonempty) <= 3 or any(cell.colspan >= max(2, col_count - 3) for cell in label_cells)

    def _summary_labels_from_text(self, text: str) -> list[str]:
        labels = utils.summary_labels_from_summary_text(text)
        if labels:
            return labels
        if not self._looks_like_broad_summary_text(text):
            return []
        return utils.summary_labels(text)

    def _looks_like_broad_summary_text(self, text: str) -> bool:
        labels = utils.summary_labels(text)
        if not labels:
            return False
        compact = utils.normalize(text)
        if not compact:
            return False
        if utils.summary_amounts(text, labels=labels):
            return True
        if self._has_chinese_amount_text(text):
            return True
        return any(token in compact for token in ("人民币金额", "含税价", "大写"))

    @staticmethod
    def _has_chinese_amount_text(text: str) -> bool:
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
        return bool(re.search(r"[壹贰叁肆伍陆柒捌玖拾佰仟万亿圆元整]{2,}", compact))

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
            labels = self._summary_labels_from_text(cell.text)
            if labels:
                return labels[0]
        return ""

    def _summary_amount_from_row(self, row: _LogicalRow) -> str:
        for cell in row.cells:
            labels = self._summary_labels_from_text(cell.text)
            amounts = utils.summary_amounts(cell.text, labels=labels) if labels else utils.summary_amounts(cell.text)
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

    def _summary_pairs_from_text(self, text: str) -> list[_SummaryPair]:
        raw = unicodedata.normalize("NFKC", utils.strip_html(text or ""))
        if not raw:
            return []

        result: list[_SummaryPair] = []
        pending_labels: list[str] = []
        for line_index, line in enumerate(raw.splitlines()):
            labels = utils.summary_labels_from_summary_text(line)
            amounts = utils.summary_amounts(line, labels=labels) if labels else []
            if not labels and pending_labels:
                amounts = utils.summary_amount_line_amounts(line)
            pending_labels.extend(labels)
            for amount in amounts:
                if not pending_labels:
                    break
                label = pending_labels.pop(0)
                canonical_amount_val = utils.canonical_amount(amount)
                if canonical_amount_val:
                    result.append(_SummaryPair(label, amount, canonical_amount_val, line_index))
        return result

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

    # --- Source text matching helpers ---

    def _source_contains_token(self, source_norm: str, token_norm: str, allow_loose_cjk: bool) -> bool:
        if not token_norm:
            return False
        if token_norm in source_norm:
            return True
        if utils.punctuation_fold(token_norm) in utils.punctuation_fold(source_norm):
            return True
        if allow_loose_cjk and utils.is_loose_subsequence_present(token_norm, source_norm):
            return True
        return False
