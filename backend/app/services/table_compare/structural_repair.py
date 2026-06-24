"""Structural table repair rules: grid normalization, split/merge, shifted fields."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from app.services.table_compare.types import (
    _LogicalCell,
    _LogicalRow,
)
from app.services.table_compare import utils
from app.services.table_compare.repair_context import TableRepairContext

class StructuralRepairMixin:
    # --- Merged sequence row normalization ---

    def normalize_merged_sequence_rows(self, context: TableRepairContext, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
        self._activate_context(context)
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
            self._record_repair_decision(
                "merged_sequence_split",
                before=[row, *continuation_rows],
                after=split_rows,
                reason="split row containing consecutive sequence values",
                confidence=0.9,
                signals={
                    "sequence_col": sequence_col,
                    "sequences": sequences,
                    "continuation_row_count": len(continuation_rows),
                    "col_count": col_count,
                },
            )
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

        # When a name column (typically col 1) cannot be split but a
        # continuation row has a product-name-like token in its col 0,
        # use that token as the missing name for part 1+.
        name_col = None
        consumed_cont_text: str | None = None
        for cell in row.cells:
            if cell.col_index == sequence_col:
                 continue
            if cell.col_index not in per_col_parts and cell.col_index == 1:
                name_col = cell.col_index
                break
        if name_col is not None and continuation_rows:
            for cont in continuation_rows:
                cont_col0 = self._cell_text_from_row(cont, 0)
                if cont_col0 and self._is_product_name_like(cont_col0):
                    orig_text = self._cell_text_from_row(row, name_col)
                    per_col_parts[name_col] = [orig_text, cont_col0]
                    split_cols.add(name_col)
                    consumed_cont_text = cont_col0
                    break

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
                    # Skip if this continuation text was already used as a name column split part
                    if consumed_cont_text and utils.normalize(cont_text) == utils.normalize(consumed_cont_text):
                        continue
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
                source_text=self._source_text_for_row(row),
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
                source_text=self._source_text_for_row(row),
            ))
        return reindexed

    # --- Merged adjacent sequence row repair ---

    # --- Merged adjacent sequence row repair ---

    def repair_merged_adjacent_sequence_rows(self, context: TableRepairContext, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
        self._activate_context(context)
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
                self._record_repair_decision(
                    "adjacent_sequence_split",
                    before=[row],
                    after=split_rows,
                    reason="split row inferred to contain the missing adjacent sequence",
                    confidence=0.86,
                    signals={
                        "current_sequence": current_seq,
                        "missing_sequence": missing_seq,
                        "next_sequence": next_seq,
                        "col_count": col_count,
                    },
                )
            index += 1

        return self._reindex_logical_rows(repaired)

    def _split_adjacent_sequence_merged_row(
        self,
        row: _LogicalRow,
        missing_seq: int,
        col_count: int,
    ) -> list[_LogicalRow] | None:
        candidate = self._select_missing_sequence_candidate_for_row(row, missing_seq)
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
            utils.normalize(self._source_text_for_row(row)),
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
                source_text=self._source_text_for_row(row),
            ),
            _LogicalRow(
                row_index=1,
                cells=missing_cells,
                page_no=row.page_no,
                source_block_id=row.source_block_id,
                source_row=row.source_row,
                section_title=row.section_title,
                source_text=self._source_text_for_row(row),
            ),
        ]

    def repair_phantom_merged_name_rows(
        self,
        context: TableRepairContext,
        rows: list[_LogicalRow],
        col_count: int,
    ) -> list[_LogicalRow]:
        self._activate_context(context)
        """Repair rows where OCR merged two product rows into one and left a phantom row."""
        if col_count < 6:
            return rows

        repaired: list[_LogicalRow] = []
        index = 0
        while index < len(rows):
            row = rows[index]
            current_seq = self._row_sequence_int(row)

            if current_seq is None or index + 1 >= len(rows):
                repaired.append(row)
                index += 1
                continue

            next_row = rows[index + 1]
            next_seq = self._row_sequence_int(next_row)

            # Only trigger when seq is consecutive (no gap)
            if next_seq != current_seq + 1:
                repaired.append(row)
                index += 1
                continue

            split_rows = self._try_split_phantom_merged_name_pair(
                row, next_row, current_seq, col_count,
            )
            if split_rows is not None:
                repaired.extend(split_rows)
                self._record_repair_decision(
                    "phantom_row_split",
                    before=[row, next_row],
                    after=split_rows,
                    reason="split merged product name row and consume shifted phantom row",
                    confidence=0.86,
                    signals={
                        "current_sequence": current_seq,
                        "next_sequence": next_seq,
                        "col_count": col_count,
                    },
                )
                index += 2  # consume both original rows
            else:
                sparse_split = self._try_split_sparse_following_sequence_pair(
                    row, next_row, current_seq, col_count,
                )
                if sparse_split is not None:
                    repaired.extend(sparse_split)
                    self._record_repair_decision(
                        "sparse_following_sequence_split",
                        before=[row, next_row],
                        after=sparse_split,
                        reason="split merged product row and consume sparse following sequence row",
                        confidence=0.84,
                        signals={
                            "current_sequence": current_seq,
                            "next_sequence": next_seq,
                            "col_count": col_count,
                        },
                    )
                    index += 2
                else:
                    repaired.append(row)
                    index += 1

        return self._reindex_logical_rows(repaired)

    def _try_split_sparse_following_sequence_pair(
        self,
        row: _LogicalRow,
        next_row: _LogicalRow,
        current_seq: int,
        col_count: int,
    ) -> list[_LogicalRow] | None:
        name_tokens = self._split_name_tokens(self._cell_text_from_row(row, 1))
        if len(name_tokens) != 2:
            return None
        if not self._is_sparse_sequence_residue_row(next_row):
            return None

        detail_parts = self._split_cell_text_for_row_count(self._cell_text_from_row(row, 2), 2)
        if len(detail_parts) != 2:
            return None

        brand = self._dedupe_repeated_cell_text(self._cell_text_from_row(row, 3))
        if not utils.normalize(brand):
            return None
        current_unit = utils.first_unit_token([self._cell_text_from_row(row, 4)])
        current_qty = self._cell_text_from_row(row, 5)
        next_unit = self._first_unit_from_row(next_row)
        next_qty = self._first_quantity_from_row(next_row)
        if not current_unit or not next_unit or utils.normalize(current_unit) != utils.normalize(next_unit):
            return None
        if not utils.normalize(current_qty) or not utils.normalize(next_qty):
            return None

        kept_cells: list[_LogicalCell] = []
        missing_cells: list[_LogicalCell] = []
        for col in range(col_count):
            source_cell = self._cell_at_col(row, col) or row.cells[0]
            next_cell = self._cell_at_col(next_row, col)
            kept_text = self._cell_text_from_row(row, col)
            missing_text = ""
            missing_source = source_cell
            if col == 0:
                kept_text = str(current_seq)
                missing_text = str(current_seq + 1)
                missing_source = next_cell or source_cell
            elif col == 1:
                kept_text = name_tokens[0]
                missing_text = name_tokens[1]
            elif col == 2:
                kept_text = detail_parts[0]
                missing_text = detail_parts[1]
            elif col == 3:
                kept_text = brand
                missing_text = brand
            elif col == 4:
                kept_text = current_unit
                missing_text = next_unit
                missing_source = next_cell or source_cell
            elif col == 5:
                kept_text = current_qty
                missing_text = next_qty
                missing_source = next_cell or source_cell

            if kept_text or self._cell_at_col(row, col) is not None:
                kept_cells.append(self._clone_logical_cell(source_cell, row_index=0, col_index=col, text=kept_text))
            if missing_text:
                missing_cells.append(
                    self._clone_logical_cell(missing_source, row_index=1, col_index=col, text=missing_text)
                )

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
                source_text=self._source_text_for_row(row),
            ),
            _LogicalRow(
                row_index=1,
                cells=missing_cells,
                page_no=next_row.page_no,
                source_block_id=next_row.source_block_id,
                source_row=next_row.source_row,
                section_title=row.section_title,
                source_text=self._source_text_for_row(next_row),
            ),
        ]

    def _try_split_phantom_merged_name_pair(
        self,
        row: _LogicalRow,
        next_row: _LogicalRow,
        current_seq: int,
        col_count: int,
    ) -> list[_LogicalRow] | None:
        # Signal 1: col 1 of current row contains space-separated tokens
        name_text = self._cell_text_from_row(row, 1)
        name_tokens = self._split_name_tokens(name_text)
        if len(name_tokens) < 2:
            return None

        # Signal 2: next row shows column shift (brand/unit in wrong columns)
        # In a phantom row, OCR puts content shifted left:
        #   col 1 = detail text, col 2 = brand, col 3 = unit, col 4 = qty
        # In a normal row: col 1 = name, col 2 = detail, col 3 = brand, col 4 = unit
        next_brand = self._cell_text_from_row(next_row, 3)
        next_unit_col3 = self._cell_text_from_row(next_row, 3)

        # A phantom row has columns shifted left by one position:
        #   col 1 = detail text, col 2 = brand, col 3 = unit, col 4 = qty
        # A normal row has:
        #   col 1 = name, col 2 = detail, col 3 = brand, col 4 = unit
        # Detect phantom by checking if col 3 contains a unit token (shifted left)
        # instead of a brand name.
        next_unit_at_col3 = bool(next_unit_col3 and utils.first_unit_token([next_unit_col3]))
        next_brand_is_real = bool(next_brand and not utils.first_unit_token([next_brand]))
        if next_brand_is_real:
            # col 3 has a real brand name -> not a phantom row
            return None
        if not next_unit_at_col3:
            # col 3 doesn't have a unit either -> can't confirm phantom pattern
            return None

        # Signal 3: find the missing name in source_text near the seq number
        missing_seq = current_seq + 1
        first_name = name_tokens[0]
        candidate = self._extract_phantom_candidate_from_source(
            self._source_text_for_row(row), missing_seq, name_tokens,
        )
        if candidate is None:
            candidate = self._extract_phantom_candidate_from_source(
                self._source_text_for_row(next_row), missing_seq, name_tokens,
            )
        if candidate is None:
            return None

        # Build the repaired rows
        kept_cells: list[_LogicalCell] = []
        missing_cells: list[_LogicalCell] = []

        for col in range(col_count):
            source_cell = self._cell_at_col(row, col) or row.cells[0]
            kept_text = self._cell_text_from_row(row, col)
            missing_text = ""
            if col == 0:
                kept_text = str(current_seq)
                missing_text = str(missing_seq)
            elif col == 1:
                kept_text = first_name
                missing_text = candidate["name"]
            elif col == 2:
                missing_text = candidate["detail"]
            elif col == 3:
                missing_text = candidate["brand"]
            elif col == 4:
                missing_text = candidate["unit"]
            elif col == 5:
                missing_text = candidate["quantity"]

            if kept_text or self._cell_at_col(row, col) is not None:
                kept_cells.append(
                    self._clone_logical_cell(source_cell, row_index=0, col_index=col, text=kept_text)
                )
            if missing_text:
                missing_cells.append(
                    self._clone_logical_cell(source_cell, row_index=1, col_index=col, text=missing_text)
                )

        if len([cell for cell in missing_cells if utils.normalize(cell.text)]) < 4:
            return None

        return [
            _LogicalRow(
                row_index=0,
                cells=kept_cells,
                page_no=row.page_no,
                source_block_id=row.source_block_id,
                source_row=row.source_row,
                section_title=row.section_title,
                source_text=self._source_text_for_row(row),
            ),
            _LogicalRow(
                row_index=1,
                cells=missing_cells,
                page_no=row.page_no,
                source_block_id=row.source_block_id,
                source_row=row.source_row,
                section_title=row.section_title,
                source_text=self._source_text_for_row(row),
            ),
        ]

    def _extract_phantom_candidate_from_source(
        self,
        source_text: str,
        missing_seq: int,
        name_tokens: list[str],
    ) -> dict[str, str] | None:
        """Find the missing row info by scanning source_text for a name token near the seq number."""
        if not source_text:
            return None

        tokens = self._source_line_tokens(source_text)
        target_seq = str(missing_seq)

        for index, token in enumerate(tokens):
            if utils.normalize(token) != target_seq:
                continue

            # Scan backwards (up to 8 tokens) for a name token match
            matched_name = ""
            matched_index = -1
            for back in range(1, min(9, index + 1)):
                candidate_token = tokens[index - back]
                candidate_norm = utils.normalize(candidate_token)
                for name_tok in name_tokens[1:]:
                    if utils.normalize(name_tok) == candidate_norm:
                        matched_name = candidate_token
                        matched_index = index - back
                        break
                if matched_name:
                    break

            if not matched_name:
                continue

            # Guard: the matched name token must be a real product name, not an OCR fragment.
            # It should appear as a standalone line in the source text.
            matched_name_standalone = False
            for t in tokens:
                if utils.normalize(t) == utils.normalize(matched_name):
                    matched_name_standalone = True
                    break
            if not matched_name_standalone:
                continue

            # Guard: there must be a brand-like token between the matched name and the seq
            gap_tokens = tokens[matched_index + 1:index]
            has_brand_in_gap = any(
                not self._is_source_row_field_noise(utils.normalize(t))
                and 2 <= len(utils.normalize(t)) <= 12
                for t in gap_tokens
            )
            if not has_brand_in_gap:
                continue

            # Extract detail from tokens between matched_name and the seq number
            detail = ""
            if matched_index > 0:
                detail_candidate = tokens[matched_index - 1]
                detail_norm = utils.normalize(detail_candidate)
            if len(detail_norm) >= 4 and not self._is_source_row_field_noise(detail_norm):
                    detail = detail_candidate

            # Extract brand: scan backward between name and seq, then forward after seq
            brand = ""
            for back in range(1, min(4, index - matched_index)):
                back_token = tokens[index - back]
                back_norm = utils.normalize(back_token)
                if self._is_source_row_field_noise(back_norm):
                    continue
                if 2 <= len(back_norm) <= 12:
                    brand = back_token
                    break
            if not brand:
                for fwd in range(1, min(4, len(tokens) - index)):
                    fwd_token = tokens[index + fwd] if index + fwd < len(tokens) else ""
                    fwd_norm = utils.normalize(fwd_token)
                    if self._is_source_row_field_noise(fwd_norm):
                        continue
                    if 2 <= len(fwd_norm) <= 12:
                        brand = fwd_token
                        break

            # Extract unit: next unit-like token after seq
            unit = ""
            search_start = index + 1
            for fwd in range(search_start, min(search_start + 5, len(tokens))):
                if utils.first_unit_token([tokens[fwd]]):
                    unit = tokens[fwd]
                    break

            # Extract quantity: first number after unit
            quantity = ""
            if unit:
                try:
                    unit_index = tokens.index(unit, search_start)
                except ValueError:
                    unit_index = -1
                if unit_index >= 0:
                    for fwd in range(unit_index + 1, min(unit_index + 3, len(tokens))):
                        fwd_norm = utils.normalize(tokens[fwd])
                        if re.fullmatch(r"\d{1,3}(?:\.\d+)?", fwd_norm):
                           quantity = tokens[fwd]
                           break

            # Look for detail continuation after quantity
            if quantity and detail:
                try:
                    qty_idx = tokens.index(quantity, index)
                except ValueError:
                    qty_idx = -1
                if qty_idx >= 0 and qty_idx + 1 < len(tokens):
                    cont = tokens[qty_idx + 1]
                    cont_norm = utils.normalize(cont)
                    if cont_norm and not self._is_source_row_field_noise(cont_norm) and len(cont_norm) >= 2:
                        detail = detail + " " + cont

            return {
                "name": matched_name,
                "detail": detail or matched_name,
                "brand": brand,
                "unit": unit,
                "quantity": quantity,
            }

        return None

    def _split_name_tokens(self, text: str) -> list[str]:
        """Split a cell text into candidate name tokens."""
        raw = unicodedata.normalize("NFKC", text or "").strip()
        if not raw:
            return []
        parts = [p.strip() for p in re.split(r"\s+", raw) if p.strip()]
        return [p for p in parts if self._is_product_name_like(p)]

    def _is_product_name_like(self, text: str) -> bool:
        norm = utils.normalize(text)
        if len(norm) < 2 or len(norm) > 20:
            return False
        if not re.search(r"[一-鿿]", text):
            return False
        if norm.endswith(("。", "；", ";", "，", ",")):
            return False
        return True

    def _looks_like_detail_not_name(self, text: str) -> bool:
        """Check if text looks like detail/description text rather than a product name."""
        norm = utils.normalize(text)
        if not norm or len(norm) < 2:
            return False
        if norm.endswith(("。", ".")):
            return True
        detail_markers = ("开发", "计算", "模型开发", "预报", "预测", "维护", "服务", "建立")
        if any(marker in norm for marker in detail_markers):
            if len(norm) > 6:
                return True
        return False

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

        source_norm = utils.normalize(self._source_text_for_row(row))
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
                kept_text = self._remove_merged_name_suffix(kept_text, candidate["name"] or missing_detail)
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
                source_text=self._source_text_for_row(row),
            ),
            _LogicalRow(
                row_index=1,
                cells=missing_cells,
                page_no=row.page_no,
                source_block_id=row.source_block_id,
                source_row=row.source_row,
                section_title=row.section_title,
                source_text=self._source_text_for_row(row),
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

    def _select_missing_sequence_candidate_for_row(self, row: _LogicalRow, missing_seq: int) -> dict[str, str] | None:
        candidates = self._missing_sequence_candidates_from_source(self._source_text_for_row(row), missing_seq)
        if not candidates:
            return None
        matched = [
            candidate
            for candidate in candidates
            if self._missing_sequence_candidate_matches_row(row, candidate)
        ]
        if len(matched) != 1:
            return None
        return matched[0]

    def _missing_sequence_candidate_matches_row(self, row: _LogicalRow, candidate: dict[str, str]) -> bool:
        name_text = self._cell_text_from_row(row, 1)
        detail_text = self._cell_text_from_row(row, 2)
        brand_text = self._cell_text_from_row(row, 3)
        expected_name = self._merged_name_suffix(name_text, detail_text, candidate.get("name", ""))
        if not expected_name:
            expected_name = candidate.get("name", "")

        expected_norm = utils.normalize(expected_name)
        candidate_name_norm = utils.normalize(candidate.get("name", ""))
        candidate_detail_norm = utils.normalize(candidate.get("detail", ""))
        candidate_brand_norm = utils.normalize(candidate.get("brand", ""))
        if expected_norm and not (
            expected_norm in candidate_name_norm
            or candidate_name_norm in expected_norm
            or expected_norm in candidate_detail_norm
            or candidate_detail_norm in expected_norm
        ):
            return False

        detail_norm = utils.normalize(detail_text)
        if candidate_detail_norm and detail_norm and candidate_detail_norm not in detail_norm:
            merged_name_norm = utils.normalize(name_text)
            if candidate_detail_norm not in merged_name_norm:
                return False

        if candidate_brand_norm:
            brand_norm = utils.normalize(brand_text)
            if candidate_brand_norm not in brand_norm and not self._has_repeated_cell_value(brand_text):
                return False

        return True

    def _missing_sequence_candidate_from_source(self, source_text: str, missing_seq: int) -> dict[str, str] | None:
        candidates = self._missing_sequence_candidates_from_source(source_text, missing_seq)
        return candidates[0] if candidates else None

    def _missing_sequence_candidates_from_source(self, source_text: str, missing_seq: int) -> list[dict[str, str]]:
        tokens = self._source_line_tokens(source_text)
        if not tokens:
            return []
        candidates: list[dict[str, str]] = []
        for index, token in enumerate(tokens):
            if utils.normalize(token) != str(missing_seq):
                continue
            window = tokens[index + 1:index + 10]
            detail = self._first_detail_token(window)
            if not detail:
                detail = ""
            if detail:
                detail_index = window.index(detail)
                brand = self._first_brand_token(window[detail_index + 1:])
                unit = utils.first_unit_token(window[detail_index + 1:])
                if unit:
                    unit_index = window.index(unit)
                    quantity = self._first_quantity_token(window[unit_index + 1:])
                    if quantity:
                        candidates.append(
                            self._build_missing_sequence_candidate(
                                tokens,
                                index,
                                window,
                                detail=detail,
                                brand=brand,
                                unit=unit,
                                quantity=quantity,
                                trailing_index=unit_index + 1,
                            )
                        )

            unit = utils.first_unit_token(window[:4])
            if not unit:
                continue
            unit_index = window.index(unit)
            brand = self._first_brand_token(window[unit_index + 1:])
            if not brand:
                continue
            brand_index = unit_index + 1 + window[unit_index + 1:].index(brand)
            detail = self._first_detail_token(window[brand_index + 1:])
            if not detail:
                continue
            detail_index = brand_index + 1 + window[brand_index + 1:].index(detail)
            quantity = self._first_quantity_token(window[detail_index + 1:])
            if not quantity:
                continue
            candidates.append(
                self._build_missing_sequence_candidate(
                    tokens,
                    index,
                    window,
                    detail=detail,
                    brand=brand,
                    unit=unit,
                    quantity=quantity,
                    trailing_index=detail_index + 2,
                )
            )
        return candidates

    def _build_missing_sequence_candidate(
        self,
        tokens: list[str],
        index: int,
        window: list[str],
        *,
        detail: str,
        brand: str,
        unit: str,
        quantity: str,
        trailing_index: int,
    ) -> dict[str, str]:
        name = detail
        previous = tokens[index - 1] if index > 0 else ""
        trailing = ""
        if trailing_index < len(window):
            after_anchor = window[trailing_index]
            if self._looks_like_row_name_fragment(after_anchor):
                trailing = after_anchor
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
            "_token_index": str(index),
        }

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

    def _is_sparse_sequence_residue_row(self, row: _LogicalRow) -> bool:
        if self._row_sequence_int(row) is None:
            return False
        nonempty = [cell for cell in row.cells if utils.normalize(cell.text)]
        if len(nonempty) > 3:
            return False
        return bool(self._first_unit_from_row(row) and self._first_quantity_from_row(row))

    def _first_unit_from_row(self, row: _LogicalRow) -> str:
        for cell in sorted(row.cells, key=lambda item: item.col_index):
            if cell.col_index == 0:
                continue
            unit = utils.first_unit_token([cell.text])
            if unit:
                return unit
        return ""

    def _first_quantity_from_row(self, row: _LogicalRow) -> str:
        for cell in sorted(row.cells, key=lambda item: item.col_index):
            if cell.col_index == 0:
                continue
            norm = utils.normalize(cell.text)
            if re.fullmatch(r"\d{1,3}(?:\.\d+)?", norm):
                return cell.text
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
        bbox = source.bbox if utils.normalize(source.text) == utils.normalize(text) else None
        return _LogicalCell(
            row_index=row_index,
            col_index=col_index,
            text=text,
            bbox=bbox,
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

    # --- Shifted product field row repair ---

    def repair_shifted_product_field_rows(self, context: TableRepairContext, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
        self._activate_context(context)
        if col_count < 8:
            return rows

        repaired: list[_LogicalRow] = []
        for row in rows:
            repaired_row = self._repair_shifted_product_field_row(row, col_count)
            if repaired_row is not None:
                self._record_repair_decision(
                    "shifted_field_repair",
                    before=[row],
                    after=[repaired_row],
                    reason="realign shifted product fields using plain OCR evidence",
                    confidence=0.82,
                    signals={
                        "sequence": self._row_sequence_int(row),
                        "col_count": col_count,
                    },
                )
                repaired.append(repaired_row)
            else:
                repaired.append(row)
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
            source_text=self._source_text_for_row(row),
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
        tokens = self._source_line_tokens(self._source_text_for_row(row))
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
        source_norm = utils.normalize(self._source_text_for_row(row))
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

    # --- Orphan overflow sequence cell removal ---

    def remove_orphan_overflow_sequence_cells(self, context: TableRepairContext, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
        self._activate_context(context)
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
                    removed_cell = cell
                    continue
                kept_cells.append(cell)

            if not removed:
                repaired.append(row)
                continue

            changed = True
            repaired_row = _LogicalRow(
                row_index=row.row_index,
                cells=kept_cells,
                page_no=row.page_no,
                source_block_id=row.source_block_id,
                source_row=row.source_row,
                section_title=row.section_title,
                source_text=self._source_text_for_row(row),
            )
            repaired.append(repaired_row)
            self._record_repair_decision(
                "orphan_sequence_drop",
                before=[row],
                after=[repaired_row],
                reason="remove right-side overflow cell that duplicates the next missing sequence",
                confidence=0.8,
                signals={
                    "current_sequence": current_seq,
                    "next_sequence": next_seq,
                    "removed_col": removed_cell.col_index,
                    "removed_text": removed_cell.text,
                    "col_count": col_count,
                },
            )

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
