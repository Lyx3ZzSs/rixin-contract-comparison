"""Table matching and row alignment."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.models_table import StructuredTable
from app.services.table_compare import utils


class TableMatcher:
    """Matches tables and aligns rows between original and compare documents."""

    row_similarity_threshold: float = 0.55
    table_similarity_threshold: float = 0.3

    _split_merged_cell_text = staticmethod(utils.split_merged_cell_text)
    _cell_merge_similarity = staticmethod(utils.cell_merge_similarity)

    def match_tables(self, original: list, compare: list) -> list[tuple[int, int, float]]:
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
        text_score = SequenceMatcher(None, utils.normalize(left_text), utils.normalize(right_text)).ratio()
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

    def align_rows(self, original: StructuredTable, compare: StructuredTable) -> list[tuple[int | None, int | None]]:
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
            text = utils.normalize(self._cell_text(table, row, c))
            if text and not utils.is_noise(text):
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
        table_type = utils.table_type(table)
        section = utils.normalize(getattr(table.rows[row], "section_title", "")) if row < len(table.rows) else ""
        summary_label = self._summary_label_from_row(table.rows[row]) if row < len(table.rows) else ""
        if summary_label:
            return "|".join(part for part in (section, "summary", utils.normalize(summary_label)) if part)
        sequence = ""
        product = ""
        details = ""

        for index, cell in enumerate(cells):
            text = utils.normalize(cell.text)
            if not text or utils.is_noise(text):
                continue
            if table_type == "payment" and not product and not utils.is_amount_like(text):
                product = text
                continue
            if table_type == "contact" and not product and utils.looks_like_person_or_role(text):
                product = text
                continue
            if not sequence and re.fullmatch(r"\d{1,2}", text):
                sequence = text
                for next_cell in cells[index + 1:]:
                    candidate = utils.normalize(next_cell.text)
                    if candidate and not utils.is_noise(candidate) and not utils.is_number_like(candidate):
                        product = candidate
                        break
                continue
            if not product and not utils.is_number_like(text):
                product = text
            elif product and not details and not utils.is_number_like(text):
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
            text = utils.normalize(cell.text)
            if re.fullmatch(r"\d{1,2}", text):
                return text
        return ""

    def _row_nonempty_cells(self, table, row: int):
        result = []
        for c in range(table.col_count):
            cell = utils.anchor_cell(table, row, c)
            if cell and utils.normalize(cell.text):
                result.append(cell)
        return result

    def _cell_text(self, table: StructuredTable, row: int, col: int) -> str:
        cell = utils.anchor_cell(table, row, col)
        return cell.text if cell else ""

    def _summary_label_from_row(self, row) -> str:
        for cell in row.cells:
            labels = utils.summary_labels_from_summary_text(cell.text)
            if labels:
                return labels[0]
        return ""

    # --- OCR noise / fragment detection ---

    def is_similar_ocr_noise(self, orig_norm: str, comp_norm: str, cell_similarity_threshold: float = 0.85) -> bool:
        if not orig_norm or not comp_norm:
            return False
        if self._has_contextual_box_or_kou_noise(orig_norm, comp_norm):
            return True
        if SequenceMatcher(None, orig_norm, comp_norm).ratio() < cell_similarity_threshold:
            return False
        orig_fold = utils.punctuation_fold(orig_norm)
        comp_fold = utils.punctuation_fold(comp_norm)
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
            if any(left_delta in group and right_delta in group for group in ambiguous_groups):
                return True
            if self._is_contextual_box_or_kou_noise(orig_norm, comp_norm, i1, j1):
                return True
        return False

    @staticmethod
    def _has_contextual_box_or_kou_noise(orig_norm: str, comp_norm: str) -> bool:
        if len(orig_norm) != len(comp_norm):
            return False
        opcodes = [
            opcode for opcode in SequenceMatcher(None, orig_norm, comp_norm).get_opcodes()
            if opcode[0] != "equal"
        ]
        if len(opcodes) != 1:
            return False
        tag, i1, i2, j1, j2 = opcodes[0]
        if tag != "replace" or i2 - i1 != 1 or j2 - j1 != 1:
            return False
        return TableMatcher._is_contextual_box_or_kou_noise(orig_norm, comp_norm, i1, j1)

    @staticmethod
    def _is_contextual_box_or_kou_noise(orig_norm: str, comp_norm: str, orig_index: int, comp_index: int) -> bool:
        left_delta = orig_norm[orig_index]
        right_delta = comp_norm[comp_index]
        if {left_delta, right_delta} != {"口", "□"}:
            return False
        if len(orig_norm) != len(comp_norm):
            return False

        source = orig_norm if left_delta == "口" else comp_norm
        index = orig_index if left_delta == "口" else comp_index
        before = source[index - 1] if index > 0 else ""
        after = source[index + 1] if index + 1 < len(source) else ""
        context = f"{before}口{after}"
        known_word_noise = any(word in context for word in ("接口", "端口", "窗口", "开口", "网口"))
        chinese_context = bool(before and "\u4e00" <= before <= "\u9fff") or bool(after and "\u4e00" <= after <= "\u9fff")
        return known_word_noise or (len(source) >= 4 and chinese_context)

    def is_short_text_fragment(
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
        if utils.is_number_like(text):
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
            if (cell := utils.anchor_cell(table, row, col)) is not None
        ]
        return self._is_ocr_fragment_row(cells)

    def _is_ocr_fragment_row(self, cells: list) -> bool:
        nonempty = [c for c in cells if utils.normalize(c.text)]
        if not nonempty:
            return False
        total_text = "".join(utils.normalize(c.text) for c in nonempty)
        if utils.is_number_like(total_text):
            return False
        if len(nonempty) / max(len(cells), 1) >= 0.3:
            return False
        return len(total_text) <= 3

    # --- Row matching helpers (used by diff_builder and summary) ---

    def row_match_cells(self, table: StructuredTable, row: int) -> list[dict[str, object]]:
        cells = []
        for col in range(table.col_count):
            cell = utils.anchor_cell(table, row, col)
            if not cell:
                continue
            norm = utils.normalize_cell_for_compare(cell.text)
            if not norm or utils.is_noise(norm):
                continue
            cells.append({"col": col, "text": cell.text, "norm": norm})
        return cells

    def row_cell_match_score(
        self,
        original_norm: str,
        compare_norm: str,
        original_source: str,
        compare_source: str,
        cell_similarity_threshold: float = 0.85,
    ) -> float:
        if original_norm == compare_norm:
            return 1.0
        if self.is_similar_ocr_noise(original_norm, compare_norm, cell_similarity_threshold):
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

    def row_source_text(self, table: StructuredTable, row: int, block) -> str:
        parts = [cell.text for cell in self._row_nonempty_cells(table, row)]
        if block is not None:
            if block.text:
                parts.append(block.text)
            if block.raw_html:
                parts.append(utils.strip_html(block.raw_html))
        return utils.normalize(" ".join(parts))

    @staticmethod
    def row_plain_source_text(table: StructuredTable, row: int) -> str:
        if row < 0 or row >= len(table.rows):
            return ""
        return getattr(table.rows[row], "source_text", "") or ""
