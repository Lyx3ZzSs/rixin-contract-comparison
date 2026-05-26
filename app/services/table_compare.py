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

NOISE_PATTERN = re.compile(
    r"^(共\d+页第\d+页|第?\d+页|小计|\d+\s*套总计|\d+\s*套合计|\d+\s*套总合计)$"
)
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
                parsed = parse_html_tables(text, page_no=block.page_no, source="ppstructure_html", source_block_id=block.block_id, cell_bboxes=cell_bboxes)
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
                if self._is_table_header_cells(cells) or self._is_summary_cells(cells):
                    continue
                section = self._section_title(cells, table.col_count)
                if section:
                    current_section = section
                rows.append(_LogicalRow(
                    row_index=logical_index,
                    cells=cells,
                    page_no=table.page_no,
                    source_block_id=table.source_block_id,
                    source_row=row.row_index,
                    section_title=current_section,
                ))
                logical_index += 1

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
        if all(self._is_noise(text) or self._is_number_like(text) for text in texts):
            return True
        return len(texts) <= 3 and any(self._is_noise(text) for text in texts)

    def _section_title(self, cells: list[_LogicalCell], col_count: int) -> str:
        nonempty = [cell for cell in cells if self._normalize(cell.text)]
        if len(nonempty) != 1:
            return ""
        cell = nonempty[0]
        text = self._normalize(cell.text)
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

            cell_diffs = self._diff_cells(orig_table, comp_table)
            for group in self._group_cell_diffs(cell_diffs, orig_table, comp_table):
                orig_block = self._find_block(original_blocks, orig_table)
                comp_block = self._find_block(compare_blocks, comp_table)
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

    def _diff_cells(self, original: StructuredTable, compare: StructuredTable) -> list[CellDiff]:
        max_cols = max(original.col_count, compare.col_count)
        diffs: list[CellDiff] = []

        for orig_row, comp_row in self._align_rows(original, compare):
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
            cell = self._anchor_cell(table, row, cd.col)
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
        if row_span > 1 and height > median * 2.2:
            return None
        if len(compact_text) <= 12 and height > max(50.0, median * 3.0):
            return None
        if height > max(120.0, median * 7.0):
            return None
        return bbox

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
            cell = self._anchor_cell(table, row, cd.col)
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
    __slots__ = ("row_index", "cells", "page_no", "source_block_id", "source_row", "section_title")

    def __init__(
        self,
        row_index: int,
        cells: list[_LogicalCell],
        page_no: int,
        source_block_id: str,
        source_row: int,
        section_title: str = "",
    ):
        self.row_index = row_index
        self.cells = cells
        self.page_no = page_no
        self.source_block_id = source_block_id
        self.source_row = source_row
        self.section_title = section_title


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


class CellDiff:
    __slots__ = (
        "row",
        "col",
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
    ):
        self.row = row
        self.col = col
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
