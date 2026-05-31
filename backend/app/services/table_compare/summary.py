"""Summary row comparison and product row coverage checks."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from app.models import TextBlock
from app.models_table import StructuredTable
from app.services.table_compare.constants import AMOUNT_TOKEN_PATTERN
from app.services.table_compare.types import _SummaryPair
from app.services.table_compare import utils
from app.services.table_compare.matcher import TableMatcher
from app.services.table_compare.repair import TableRepairService


class SummaryComparator:
    """Compares summary rows and checks product row coverage by source text."""

    def __init__(self, matcher: TableMatcher, repair: TableRepairService) -> None:
        self._matcher = matcher
        self._repair = repair

    def summary_pair_from_row(self, table: StructuredTable, row: int) -> tuple[str, str] | None:
        if row < 0 or row >= len(table.rows):
            return None
        logical_row = table.rows[row]
        label = self._repair._summary_label_from_row(logical_row)
        amount = self._repair._summary_amount_from_row(logical_row)
        canonical_amount_val = utils.canonical_amount(amount)
        if not label or not canonical_amount_val:
            return None
        return label, canonical_amount_val

    def table_source_has_summary_pair(
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
            row_section = utils.normalize(getattr(row, "section_title", ""))
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
                for pair in self._repair._summary_pairs_from_text(source_text)
            ):
                return True
        return False

    def one_sided_summary_row_covered_by_source(
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

        pair = self.summary_pair_from_row(present_table, present_row)
        if pair is None:
            return False
        label, amount = pair
        reference_section = utils.normalize(getattr(present_table.rows[present_row], "section_title", ""))
        if not reference_section:
            return False
        return self.table_source_has_summary_pair(
            missing_table,
            label,
            amount,
            present_table.rows[present_row].page_no,
            reference_section,
        )

    def one_sided_product_row_covered_by_source(
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
        if utils.table_type(table) != "product" and table.col_count < 8:
            return False
        if self._repair._summary_label_from_row(table.rows[row]):
            return False

        cells = self._matcher._row_nonempty_cells(table, row)
        if len(cells) < 6:
            return False
        if not self._matcher._row_sequence(table, row):
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
            key = utils.normalize(text)
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
                add(utils.strip_html(block.raw_html))
        return texts

    def _product_row_covered_by_source_text(self, table: StructuredTable, row: int, source_text: str) -> bool:
        if not source_text:
            return False

        source_norm = utils.normalize(source_text)
        if not source_norm:
            return False

        product = self._product_row_identity_text(table, row)
        if not product or not self._source_contains_token(source_norm, utils.normalize(product), allow_loose_cjk=True):
            return False

        brand = self._product_row_brand_text(table, row)
        if brand and not self._source_contains_token(source_norm, utils.normalize(brand), allow_loose_cjk=False):
            return False

        amounts = self._row_likely_amount_values(table, row)
        if not amounts:
            return False
        for amount in set(amounts):
            if self._amount_occurrence_count(source_text, amount) < amounts.count(amount):
                return False

        detail = self._product_row_detail_text(table, row)
        if detail and self._source_contains_fuzzy_detail(source_norm, utils.normalize(detail)):
            return True
        return bool(brand and len(utils.normalize(brand)) >= 2)

    def _product_row_identity_text(self, table: StructuredTable, row: int) -> str:
        if table.col_count >= 8:
            text = self._matcher._cell_text(table, row, 1)
            if utils.normalize(text):
                return text
        for cell in self._matcher._row_nonempty_cells(table, row):
            text = utils.normalize(cell.text)
            if text and not utils.is_noise(text) and not utils.is_number_like(text) and not utils.canonical_amount(cell.text):
                return cell.text
        return ""

    def _product_row_detail_text(self, table: StructuredTable, row: int) -> str:
        if table.col_count >= 8:
            return self._matcher._cell_text(table, row, 2)
        cells = [
            cell for cell in self._matcher._row_nonempty_cells(table, row)
            if not utils.is_number_like(utils.normalize(cell.text)) and not utils.canonical_amount(cell.text)
        ]
        return cells[1].text if len(cells) >= 2 else ""

    def _product_row_brand_text(self, table: StructuredTable, row: int) -> str:
        if table.col_count >= 8:
            return self._matcher._cell_text(table, row, 3)
        return ""

    def _row_likely_amount_values(self, table: StructuredTable, row: int) -> list[str]:
        amounts: list[str] = []
        for cell in self._matcher._row_nonempty_cells(table, row):
            if not self._is_likely_amount_column(table, row, cell.col_index):
                continue
            amount = utils.canonical_amount(cell.text)
            if amount and utils.looks_like_amount_value(f"amount:{amount}"):
                amounts.append(amount)
        return amounts

    def _source_contains_token(self, source_norm: str, token_norm: str, allow_loose_cjk: bool) -> bool:
        return self._repair._source_contains_token(source_norm, token_norm, allow_loose_cjk)

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

    def _row_amount_count(self, table: StructuredTable, row: int, amount: str) -> int:
        count = 0
        for cell in self._matcher._row_nonempty_cells(table, row):
            norm = utils.normalize_cell_for_compare(cell.text)
            if norm == f"amount:{amount}" and self._is_likely_amount_column(table, row, cell.col_index):
                count += 1
        return count

    def _amount_occurrence_count(self, text: str, amount: str) -> int:
        if not text:
            return 0
        count = 0
        for match in AMOUNT_TOKEN_PATTERN.finditer(unicodedata.normalize("NFKC", text)):
            if utils.canonical_amount(match.group(0)) == amount:
                count += 1
        return count

    def _is_likely_amount_column(self, table: StructuredTable, row: int, col: int) -> bool:
        if col < 0 or table.col_count <= 0:
            return False
        if table.col_count >= 8:
            return col >= 6
        if col >= max(0, table.col_count - 2):
            text = self._matcher._cell_text(table, row, col)
            return bool(utils.canonical_amount(text))
        return False

    def is_covered_duplicate_amount_cell(
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
            utils.table_type(present_table) == "product"
            or utils.table_type(missing_table) == "product"
            or present_table.col_count >= 8
            or missing_table.col_count >= 8
        ):
            return False
        if not present_norm.startswith("amount:"):
            return False
        if not utils.looks_like_amount_value(present_norm):
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

        source_text = TableMatcher.row_plain_source_text(missing_table, missing_row)
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
        left_key = self._matcher._row_business_key(left, left_row)
        right_key = self._matcher._row_business_key(right, right_row)
        if left_key and right_key:
            if left_key == right_key:
                return True
            if SequenceMatcher(None, left_key, right_key).ratio() >= 0.82:
                return True
        left_seq = self._matcher._row_sequence(left, left_row)
        right_seq = self._matcher._row_sequence(right, right_row)
        return bool(left_seq and left_seq == right_seq and self._matcher._row_similarity(left, left_row, right, right_row) >= 0.72)

    def _find_matching_business_row(
        self,
        source_table: StructuredTable,
        source_row: int,
        target_table: StructuredTable,
    ) -> int | None:
        best_row = None
        best_score = 0.0
        source_key = self._matcher._row_business_key(source_table, source_row)
        for row in range(len(target_table.rows)):
            if not self._matcher._row_text(target_table, row):
                continue
            if not self._rows_have_matching_business_identity(source_table, source_row, target_table, row):
                continue
            target_key = self._matcher._row_business_key(target_table, row)
            score = self._matcher._row_similarity(source_table, source_row, target_table, row)
            if source_key and target_key:
                score = max(score, SequenceMatcher(None, source_key, target_key).ratio())
            if score > best_score:
                best_score = score
                best_row = row
        return best_row

    def dense_row_tokens_present_in_source(
        self,
        dense_cells: list[dict[str, object]],
        sparse_table: StructuredTable,
        sparse_row: int,
        sparse_cells: list[dict[str, object]],
        sparse_block: TextBlock | None,
    ) -> bool:
        source_text = TableMatcher.row_plain_source_text(sparse_table, sparse_row)
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
            if not utils.is_number_like(sparse) and not utils.is_number_like(dense)
        )
        if not has_anchor:
            return False

        source = utils.normalize(source_text)
        checked = 0
        present = 0
        for norm in dense_norms:
            if utils.is_number_like(norm) and len(norm) < 3:
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

    def is_sparse_row_covered_by_source(
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

        original_cells = self._matcher.row_match_cells(original, orig_row)
        compare_cells = self._matcher.row_match_cells(compare, comp_row)
        if len(original_cells) >= 5 and len(compare_cells) <= 2:
            return self.dense_row_tokens_present_in_source(original_cells, compare, comp_row, compare_cells, compare_block)
        if len(compare_cells) >= 5 and len(original_cells) <= 2:
            return self.dense_row_tokens_present_in_source(compare_cells, original, orig_row, original_cells, original_block)
        return False
