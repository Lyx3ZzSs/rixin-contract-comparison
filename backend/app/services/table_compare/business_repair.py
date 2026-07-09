"""Business-aware table repair rules: product rows, summaries, amounts, remarks."""

from __future__ import annotations

import re
import unicodedata

from app.models import BBox
from app.models_table import StructuredTable
from app.services.table_compare.types import (
    _LogicalCell,
    _LogicalRow,
    _SummaryPair,
    _SummaryTransition,
)
from app.services.table_compare import utils
from app.services.table_compare.repair_context import TableRepairContext


class BusinessRepairMixin:
    # --- Product continuation row repair ---

    def repair_product_continuation_rows(self, context: TableRepairContext, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
        self._activate_context(context)
        if col_count < 6:
            return rows

        repaired: list[_LogicalRow] = []
        for row in rows:
            if repaired and self._is_product_continuation_row(row, col_count):
                previous = repaired[-1]
                merged = self._merge_product_continuation_row(repaired[-1], row, col_count)
                if merged is not None:
                    repaired[-1] = merged
                    self._record_repair_decision(
                        "product_continuation_merge",
                        before=[previous, row],
                        after=[merged],
                        reason="merge sparse continuation row into previous product row",
                        confidence=0.84,
                        signals={
                            "previous_sequence": self._row_sequence_int(previous),
                            "continuation_nonempty_cells": len([
                                cell for cell in row.cells if utils.normalize(cell.text)
                            ]),
                            "col_count": col_count,
                        },
                    )
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
            text for text in (self._source_text_for_row(previous), self._source_text_for_row(continuation)) if text
        ))
        if not source_norm:
            return None

        merged_source_text = "\n".join(dict.fromkeys(
            text
            for text in (
                self._source_text_for_row(previous),
                self._source_text_for_row(continuation),
            )
            if text
        ))
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
            cells_by_col[target_col] = self._clone_continuation_merged_cell(
                prev_cell,
                cont_cell,
                previous.row_index,
                target_col,
                merged_text,
            )
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
            source_text=merged_source_text,
        )

    def _clone_continuation_merged_cell(
        self,
        previous_cell: _LogicalCell,
        continuation_cell: _LogicalCell,
        row_index: int,
        col_index: int,
        text: str,
    ) -> _LogicalCell:
        page_no = previous_cell.page_no
        source_block_id = previous_cell.source_block_id
        source_row = previous_cell.source_row
        source_col = previous_cell.source_col
        bbox = previous_cell.bbox

        if (
            continuation_cell.page_no != previous_cell.page_no
            or continuation_cell.source_block_id != previous_cell.source_block_id
        ):
            page_no = continuation_cell.page_no
            source_block_id = continuation_cell.source_block_id
            source_row = continuation_cell.source_row
            source_col = continuation_cell.source_col
            bbox = continuation_cell.bbox
        elif previous_cell.bbox is not None and continuation_cell.bbox is not None:
            bbox = self._merge_bboxes(previous_cell.bbox, continuation_cell.bbox)

        return _LogicalCell(
            row_index=row_index,
            col_index=col_index,
            text=text,
            bbox=bbox,
            page_no=page_no,
            source_block_id=source_block_id,
            source_row=source_row,
            source_col=source_col,
            colspan=previous_cell.colspan,
            rowspan=previous_cell.rowspan,
        )

    @staticmethod
    def _merge_bboxes(left: BBox, right: BBox) -> BBox:
        return BBox(
            x0=min(left.x0, right.x0),
            y0=min(left.y0, right.y0),
            x1=max(left.x1, right.x1),
            y1=max(left.y1, right.y1),
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

    # --- Embedded summary transition repair ---

    def repair_embedded_summary_transitions(self, context: TableRepairContext, rows: list[_LogicalRow], col_count: int) -> list[_LogicalRow]:
        self._activate_context(context)
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
                before_rows = [row]
                if consume_next and next_row is not None:
                    before_rows.append(next_row)
                self._record_repair_decision(
                    "embedded_summary_transition_split",
                    before=before_rows,
                    after=[product_row, summary_row],
                    reason="split product row carrying an embedded summary and next section transition",
                    confidence=0.83,
                    signals={
                        "next_section": next_section,
                        "consume_next": consume_next,
                        "col_count": col_count,
                    },
                )
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

        transitions = self._summary_transitions_from_text(self._source_text_for_row(row))
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
            source_text=self._source_text_for_row(row),
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
            source_text=self._source_text_for_row(source_row),
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
            source_text=self._source_text_for_row(row),
        )

    # --- Summary row normalization ---

    def normalize_summary_rows(
        self,
        context: TableRepairContext,
        rows: list[_LogicalRow],
        col_count: int,
        source_tables: list[StructuredTable] | None = None,
    ) -> list[_LogicalRow]:
        self._activate_context(context)
        source_pairs: dict[str, list[_SummaryPair]] = {}
        for table in source_tables or []:
            if table.source_block_id and table.source_block_id not in source_pairs:
                source_pairs[table.source_block_id] = self._summary_pairs_from_text(self._source_text_for_block(table.source_block_id, table.source_text))

        normalized: list[_LogicalRow] = []
        used_source_pairs: set[tuple[str, int]] = set()
        for row in rows:
            row_labels, row_amounts = self._summary_labels_and_amounts_from_row(row)
            prefer_source_amounts = len(row_labels) > 1 and len(row_amounts) < len(row_labels)
            split_rows = self._split_merged_summary_row(row, col_count)
            if split_rows is None:
                normalized.append(row)
                continue
            original_split_texts = [
                self._row_text_snapshot(split_row)
                for split_row in split_rows
            ]

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
                            before_fill = self._clone_logical_row(split_row)
                            self._set_summary_row_amount(split_row, source_amount)
                            self._record_repair_decision(
                                "summary_source_fill",
                                before=[before_fill],
                                after=[split_row],
                                reason="fill summary amount from plain OCR source pairs",
                                confidence=0.78,
                                signals={
                                    "label": label,
                                    "amount": source_amount,
                                    "source_block_id": split_row.source_block_id,
                                },
                            )
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
                        before_fill = self._clone_logical_row(split_row)
                        self._set_summary_row_amount(split_row, fallback)
                        self._record_repair_decision(
                            "summary_source_fill",
                            before=[before_fill],
                            after=[split_row],
                            reason="fill missing summary amount from plain OCR source pairs",
                            confidence=0.76,
                            signals={
                                "label": label,
                                "amount": fallback,
                                "source_block_id": split_row.source_block_id,
                            },
                        )
                if self._summary_amount_from_row(split_row):
                    normalized.append(split_row)
            self._record_repair_decision(
                "summary_row_split",
                before=[row],
                after=split_rows,
                reason="normalize summary labels and amounts into one logical row per pair",
                confidence=0.82,
                signals={
                    "col_count": col_count,
                    "source_pair_count": len(source_pairs.get(row.source_block_id, [])),
                    "source_amounts_used": original_split_texts != [
                        self._row_text_snapshot(split_row)
                        for split_row in split_rows
                    ],
                },
            )

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
                source_text=self._source_text_for_row(row),
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

    def _row_text_snapshot(self, row: _LogicalRow) -> tuple[tuple[int, str], ...]:
        return tuple(
            (cell.col_index, cell.text)
            for cell in sorted(row.cells, key=lambda item: item.col_index)
        )

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
