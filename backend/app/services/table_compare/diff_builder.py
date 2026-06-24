"""Cell diffing, evidence extraction, and DiffItem construction."""

from __future__ import annotations

from collections import Counter
import re
from difflib import SequenceMatcher

from app.models import BBox, CharBox, DiffItem, DiffType, EvidenceBox, TextBlock
from app.models_table import StructuredTable
from app.services.table_compare.types import (
    CellDiff,
    CellDiffGroup,
    CharSegment,
)
from app.services.table_compare import utils
from app.services.table_compare.matcher import TableMatcher
from app.services.table_compare.repair_context import BusinessChangeProtector
from app.services.table_compare.summary import SummaryComparator
from app.utils.id_utils import generate_diff_id


class TableDiffBuilder:
    """Produces cell diffs, char-level evidence, and DiffItems."""

    cell_similarity_threshold: float = 0.85

    def __init__(self, matcher: TableMatcher, summary: SummaryComparator) -> None:
        self._matcher = matcher
        self._summary = summary
        self._suppressed_diffs: list[dict[str, object]] = []
        self._change_protector = BusinessChangeProtector()

    def reset_diagnostics(self) -> None:
        self._suppressed_diffs = []

    def diagnostics_payload(self) -> dict[str, object]:
        reason_counts = Counter(str(item["reason"]) for item in self._suppressed_diffs)
        return {
            "suppressed_diff_count": len(self._suppressed_diffs),
            "suppressed_diff_counts_by_reason": dict(sorted(reason_counts.items())),
            "suppressed_diffs": list(self._suppressed_diffs),
        }

    def _record_suppressed_diff(
        self,
        reason: str,
        original: StructuredTable,
        compare: StructuredTable,
        orig_row: int | None,
        comp_row: int | None,
        *,
        col: int | None = None,
        original_text: str = "",
        compare_text: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        if len(self._suppressed_diffs) >= 500:
            return
        self._suppressed_diffs.append({
            "reason": reason,
            "original_source_block_id": original.source_block_id,
            "compare_source_block_id": compare.source_block_id,
            "original_row": orig_row,
            "compare_row": comp_row,
            "col": col,
            "original_text": original_text,
            "compare_text": compare_text,
            "details": details or {},
        })

    def _is_protected_business_change(self, original_text: str, compare_text: str) -> bool:
        return self._change_protector.is_protected_change(original_text, compare_text)

    def _row_has_protected_business_change(
        self,
        original: StructuredTable,
        compare: StructuredTable,
        orig_row: int | None,
        comp_row: int | None,
    ) -> bool:
        if orig_row is None or comp_row is None:
            return False
        for col in range(max(original.col_count, compare.col_count)):
            original_text = self._matcher._cell_text(original, orig_row, col)
            compare_text = self._matcher._cell_text(compare, comp_row, col)
            if self._is_protected_business_change(original_text, compare_text):
                return True
        return False

    def diff_cells(
        self,
        original: StructuredTable,
        compare: StructuredTable,
        original_block: TextBlock | None = None,
        compare_block: TextBlock | None = None,
    ) -> list[CellDiff]:
        max_cols = max(original.col_count, compare.col_count)
        diffs: list[CellDiff] = []

        for orig_row, comp_row in self._matcher.align_rows(original, compare):
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
                    if filter_col is not None and self._summary.is_covered_duplicate_amount_cell(
                        original,
                        compare,
                        filter_orig_row,
                        filter_comp_row,
                        filter_col,
                        utils.normalize_cell_for_compare(cell_diff.original_text),
                        utils.normalize_cell_for_compare(cell_diff.compare_text),
                        original_block,
                        compare_block,
                    ) and not self._is_protected_business_change(cell_diff.original_text, cell_diff.compare_text):
                        self._record_suppressed_diff(
                            "duplicate_amount_covered_by_source",
                            original,
                            compare,
                            filter_orig_row,
                            filter_comp_row,
                            col=filter_col,
                            original_text=cell_diff.original_text,
                            compare_text=cell_diff.compare_text,
                        )
                        continue
                    if self._short_remark_cell_covered_by_missing_source(
                        original,
                        compare,
                        filter_orig_row,
                        filter_comp_row,
                        cell_diff.original_text,
                        cell_diff.compare_text,
                        utils.normalize_cell_for_compare(cell_diff.original_text),
                        utils.normalize_cell_for_compare(cell_diff.compare_text),
                        original_block,
                        compare_block,
                    ) and not self._is_protected_business_change(cell_diff.original_text, cell_diff.compare_text):
                        self._record_suppressed_diff(
                            "short_remark_covered_by_source",
                            original,
                            compare,
                            filter_orig_row,
                            filter_comp_row,
                            col=filter_col,
                            original_text=cell_diff.original_text,
                            compare_text=cell_diff.compare_text,
                        )
                        continue
                    diffs.append(cell_diff)
                continue
            if self._summary.is_sparse_row_covered_by_source(
                original,
                compare,
                orig_row,
                comp_row,
                original_block,
                compare_block,
            ) and not self._row_has_protected_business_change(original, compare, orig_row, comp_row):
                self._record_suppressed_diff(
                    "sparse_row_covered_by_source",
                    original,
                    compare,
                    orig_row,
                    comp_row,
                )
                continue
            if self._summary.matched_malformed_summary_row_covered_by_source(
                original,
                compare,
                orig_row,
                comp_row,
            ) and not self._row_has_protected_business_change(original, compare, orig_row, comp_row):
                self._record_suppressed_diff(
                    "matched_malformed_summary_covered_by_source",
                    original,
                    compare,
                    orig_row,
                    comp_row,
                )
                continue
            if self._summary.matched_sparse_product_row_covered_by_source(
                original,
                compare,
                orig_row,
                comp_row,
                original_block,
                compare_block,
            ) and not self._row_has_protected_business_change(original, compare, orig_row, comp_row):
                self._record_suppressed_diff(
                    "matched_sparse_product_covered_by_source",
                    original,
                    compare,
                    orig_row,
                    comp_row,
                )
                continue
            if self._summary.one_sided_summary_row_covered_by_source(original, compare, orig_row, comp_row):
                self._record_suppressed_diff(
                    "one_sided_summary_covered_by_source",
                    original,
                    compare,
                    orig_row,
                    comp_row,
                )
                continue
            if self._summary.one_sided_summary_row_covered_by_malformed_source(
                original,
                compare,
                orig_row,
                comp_row,
            ):
                self._record_suppressed_diff(
                    "one_sided_summary_covered_by_malformed_source",
                    original,
                    compare,
                    orig_row,
                    comp_row,
                )
                continue
            if self._summary.one_sided_product_row_covered_by_source(
                original,
                compare,
                orig_row,
                comp_row,
                original_block,
                compare_block,
            ):
                self._record_suppressed_diff(
                    "one_sided_product_covered_by_source",
                    original,
                    compare,
                    orig_row,
                    comp_row,
                )
                continue
            if self._row_covered_by_merged_cell_source(
                original,
                compare,
                orig_row,
                comp_row,
            ) and not self._row_has_protected_business_change(original, compare, orig_row, comp_row):
                self._record_suppressed_diff(
                    "row_covered_by_merged_cell_source",
                    original,
                    compare,
                    orig_row,
                    comp_row,
                )
                continue

            for c in range(max_cols):
                orig_text = self._matcher._cell_text(original, orig_row, c) if orig_row is not None else ""
                comp_text = self._matcher._cell_text(compare, comp_row, c) if comp_row is not None else ""
                orig_norm = utils.normalize_cell_for_compare(orig_text)
                comp_norm = utils.normalize_cell_for_compare(comp_text)

                if orig_norm == comp_norm:
                    continue
                if utils.is_noise(orig_norm) and utils.is_noise(comp_norm):
                    self._record_suppressed_diff(
                        "both_sides_noise",
                        original,
                        compare,
                        orig_row,
                        comp_row,
                        col=c,
                        original_text=orig_text,
                        compare_text=comp_text,
                    )
                    continue
                if (not orig_norm or not comp_norm) and (utils.is_noise(orig_norm) or utils.is_noise(comp_norm)):
                    self._record_suppressed_diff(
                        "one_sided_noise",
                        original,
                        compare,
                        orig_row,
                        comp_row,
                        col=c,
                        original_text=orig_text,
                        compare_text=comp_text,
                    )
                    continue
                if not orig_norm and not comp_norm:
                    continue

                if self._matcher.is_similar_ocr_noise(orig_norm, comp_norm, self.cell_similarity_threshold):
                    self._record_suppressed_diff(
                        "similar_ocr_noise",
                        original,
                        compare,
                        orig_row,
                        comp_row,
                        col=c,
                        original_text=orig_text,
                        compare_text=comp_text,
                    )
                    continue

                if (
                    self._matcher.is_short_text_fragment(original, compare, orig_row, comp_row, orig_norm, comp_norm)
                    and not self._is_protected_business_change(orig_text, comp_text)
                ):
                    self._record_suppressed_diff(
                        "short_text_fragment",
                        original,
                        compare,
                        orig_row,
                        comp_row,
                        col=c,
                        original_text=orig_text,
                        compare_text=comp_text,
                    )
                    continue

                if self._summary.is_covered_duplicate_amount_cell(
                    original,
                    compare,
                    orig_row,
                    comp_row,
                    c,
                    orig_norm,
                    comp_norm,
                    original_block,
                    compare_block,
                ) and not self._is_protected_business_change(orig_text, comp_text):
                    self._record_suppressed_diff(
                        "duplicate_amount_covered_by_source",
                        original,
                        compare,
                        orig_row,
                        comp_row,
                        col=c,
                        original_text=orig_text,
                        compare_text=comp_text,
                    )
                    continue

                if self._short_remark_cell_covered_by_missing_source(
                    original,
                    compare,
                    orig_row,
                    comp_row,
                    orig_text,
                    comp_text,
                    orig_norm,
                    comp_norm,
                    original_block,
                    compare_block,
                ) and not self._is_protected_business_change(orig_text, comp_text):
                    self._record_suppressed_diff(
                        "short_remark_covered_by_source",
                        original,
                        compare,
                        orig_row,
                        comp_row,
                        col=c,
                        original_text=orig_text,
                        compare_text=comp_text,
                    )
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

    def group_cell_diffs(self, cell_diffs: list[CellDiff], orig_table: StructuredTable, comp_table: StructuredTable) -> list[CellDiffGroup]:
        if not cell_diffs:
            return []
        by_row: dict[int, list[CellDiff]] = {}
        for cd in cell_diffs:
            by_row.setdefault(cd.row, []).append(cd)
        return [CellDiffGroup(row=row, diffs=diffs) for row, diffs in sorted(by_row.items())]

    def make_diff(self, group: CellDiffGroup, index: int, orig_block: tuple[TextBlock, str] | None, comp_block: tuple[TextBlock, str] | None, orig_table: StructuredTable | None = None, comp_table: StructuredTable | None = None) -> DiffItem:
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

        table_type = utils.joint_table_type(orig_table, comp_table)
        review_flags = []
        if needs_orig_evidence and not orig_evidences:
            review_flags.append("LOW_CONFIDENCE_ORIGINAL_TABLE_EVIDENCE")
        if needs_comp_evidence and not comp_evidences:
            review_flags.append("LOW_CONFIDENCE_COMPARE_TABLE_EVIDENCE")

        caption = self._resolve_caption(orig_table, comp_table)
        caption_prefix = f"「{caption}」" if caption else ""

        diff_type = self._group_diff_type(group)
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type=diff_type,
            title=f"表格字段{caption_prefix}：{utils.table_type_label(table_type)}",
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

    def _group_diff_type(self, group: CellDiffGroup) -> DiffType:
        diff_types = {cd.diff_type for cd in group.diffs}
        if diff_types == {"ADD"}:
            return "ADD"
        if diff_types == {"DELETE"}:
            return "DELETE"
        return "MODIFY"

    def _short_remark_cell_covered_by_missing_source(
        self,
        original: StructuredTable,
        compare: StructuredTable,
        orig_row: int | None,
        comp_row: int | None,
        orig_text: str,
        comp_text: str,
        orig_norm: str,
        comp_norm: str,
        original_block: TextBlock | None,
        compare_block: TextBlock | None,
    ) -> bool:
        if orig_row is None or comp_row is None:
            return False
        if bool(orig_norm) == bool(comp_norm):
            return False

        present_text = orig_text if orig_norm else comp_text
        present_norm = orig_norm or comp_norm
        if not self._is_short_remark_label_for_source_cover(present_text, present_norm):
            return False
        if self._matcher._row_similarity(original, orig_row, compare, comp_row) < 0.82:
            return False

        missing_table = compare if orig_norm else original
        missing_row = comp_row if orig_norm else orig_row
        missing_block = compare_block if orig_norm else original_block
        present_table = original if orig_norm else compare
        present_row = orig_row if orig_norm else comp_row
        if not self._matched_row_has_source_cover_anchor(present_table, present_row, missing_table, missing_row):
            return False

        source_text = self._source_text_for_cell_cover(missing_table, missing_row, missing_block)
        source_norm = utils.normalize(source_text)
        return bool(source_norm and present_norm in source_norm)

    def _row_covered_by_merged_cell_source(
        self,
        original: StructuredTable,
        compare: StructuredTable,
        orig_row: int | None,
        comp_row: int | None,
    ) -> bool:
        if orig_row is not None and comp_row is not None:
            return self._matched_row_covered_by_merged_cell(original, compare, orig_row, comp_row)
        if orig_row is not None:
            return self._one_sided_row_covered_by_merged_cell(original, compare, orig_row)
        if comp_row is not None:
            return self._one_sided_row_covered_by_merged_cell(compare, original, comp_row)
        return False

    def _matched_row_covered_by_merged_cell(
        self,
        original: StructuredTable,
        compare: StructuredTable,
        orig_row: int,
        comp_row: int,
    ) -> bool:
        if not self._row_window_has_spanning_cell(original, orig_row, radius=1) and not self._row_window_has_spanning_cell(compare, comp_row, radius=1):
            return False

        original_tokens = self._row_coverage_tokens(original, orig_row)
        compare_tokens = self._row_coverage_tokens(compare, comp_row)
        if not original_tokens or not compare_tokens:
            return False

        original_text = utils.normalize(" ".join(original_tokens))
        compare_text = utils.normalize(" ".join(compare_tokens))
        if original_text == compare_text or original_text in compare_text or compare_text in original_text:
            return True

        compare_window = self._row_window_norm(compare, comp_row, radius=1)
        if self._all_coverage_tokens_covered(original_tokens, compare_window):
            return True

        original_window = self._row_window_norm(original, orig_row, radius=1)
        return self._all_coverage_tokens_covered(compare_tokens, original_window)

    def _one_sided_row_covered_by_merged_cell(
        self,
        present_table: StructuredTable,
        other_table: StructuredTable,
        present_row: int,
    ) -> bool:
        if not self._row_window_has_spanning_cell(other_table, present_row, radius=4):
            return False

        present_tokens = self._row_coverage_tokens(present_table, present_row)
        if not present_tokens:
            return False

        other_window = self._row_window_norm(other_table, present_row, radius=4)
        return self._all_coverage_tokens_covered(present_tokens, other_window)

    def _row_window_has_spanning_cell(self, table: StructuredTable, row: int, radius: int) -> bool:
        return any(self._row_has_spanning_cell(table, candidate) for candidate in self._nearby_rows(table, row, radius))

    def _row_has_spanning_cell(self, table: StructuredTable, row: int) -> bool:
        if row < 0 or row >= len(table.rows) or table.col_count < 2:
            return False
        return any(cell.colspan >= max(2, table.col_count) for cell in table.rows[row].cells)

    def _row_coverage_tokens(self, table: StructuredTable, row: int | None) -> list[str]:
        if row is None or row < 0 or row >= len(table.rows):
            return []
        tokens: list[str] = []
        for cell in self._matcher._row_nonempty_cells(table, row):
            tokens.extend(self._coverage_tokens(cell.text))
        return list(dict.fromkeys(tokens))

    def _row_window_norm(self, table: StructuredTable, row: int, radius: int) -> str:
        parts: list[str] = []
        for candidate in self._nearby_rows(table, row, radius):
            for cell in self._matcher._row_nonempty_cells(table, candidate):
                parts.append(cell.text)
        return utils.normalize(" ".join(parts))

    @staticmethod
    def _nearby_rows(table: StructuredTable, row: int, radius: int) -> list[int]:
        start = max(0, row - radius)
        end = min(len(table.rows), row + radius + 1)
        return list(range(start, end))

    def _all_coverage_tokens_covered(self, tokens: list[str], source: str) -> bool:
        if not source:
            return False
        for token in tokens:
            if not self._coverage_token_covered(token, source):
                return False
        return bool(tokens)

    def _coverage_token_covered(self, token: str, source: str) -> bool:
        if not token or not source:
            return False
        if token in source:
            return True
        folded_token = utils.punctuation_fold(token)
        folded_source = utils.punctuation_fold(source)
        if folded_token and folded_token in folded_source:
            return True
        return len(token) >= 4 and utils.is_loose_subsequence_present(token, source)

    @staticmethod
    def _coverage_tokens(text: str) -> list[str]:
        compact = utils.normalize(text)
        if not compact:
            return []

        value_text = re.sub(r"[\u4e00-\u9fffA-Za-z]{1,10}:", " ", compact)
        tokens: list[str] = []
        tokens.extend(match.group(0) for match in re.finditer(r"[A-Za-z]*\d[A-Za-z0-9._/\-]*", value_text))
        tokens.extend(match.group(0) for match in re.finditer(r"[\u4e00-\u9fff]{2,}", value_text))
        return [token for token in dict.fromkeys(tokens) if len(token) >= 2]

    @staticmethod
    def _is_short_remark_label_for_source_cover(text: str, norm: str) -> bool:
        if not norm or len(norm) < 2 or len(norm) > 12:
            return False
        if norm.startswith(("amount:", "quantity:", "date:", "percent:")):
            return False
        if utils.canonical_amount(text) or utils.canonical_date(text) or utils.canonical_percent(text):
            return False
        if utils.is_number_like(norm):
            return False
        return bool(any(token in norm for token in ("国产", "芯片", "操作系统", "数据库")))

    def _matched_row_has_source_cover_anchor(
        self,
        present_table: StructuredTable,
        present_row: int,
        missing_table: StructuredTable,
        missing_row: int,
    ) -> bool:
        for cell in self._matcher.row_match_cells(present_table, present_row):
            norm = str(cell["norm"])
            if len(norm) < 2 or norm.startswith(("quantity:", "percent:", "date:")):
                continue
            if self._source_cover_anchor_norm(norm, missing_table, missing_row):
                return True
        return False

    def _source_cover_anchor_norm(self, norm: str, table: StructuredTable, row: int) -> bool:
        if norm.startswith("amount:"):
            raw = norm.split(":", 1)[1].rstrip("0").rstrip(".")
            if not raw:
                return False
            return any(raw and raw in utils.normalize(cell.text) for cell in self._matcher._row_nonempty_cells(table, row))
        if utils.is_number_like(norm):
            return False
        return any(
            norm in utils.normalize(cell.text) or utils.normalize(cell.text) in norm
            for cell in self._matcher._row_nonempty_cells(table, row)
        )

    def _source_text_for_cell_cover(
        self,
        table: StructuredTable,
        row: int,
        block: TextBlock | None,
    ) -> str:
        parts = [TableMatcher.row_plain_source_text(table, row)]
        if block is not None:
            parts.append(block.text or "")
            if block.raw_html:
                parts.append(utils.strip_html(block.raw_html))
        return " ".join(part for part in parts if part)

    def whole_table_diff(
        self,
        table: StructuredTable,
        diff_type: str,
        index: int,
        blocks: list[tuple[TextBlock, str]] | tuple[TextBlock, str] | None,
        other_blocks: list[tuple[TextBlock, str]] | tuple[TextBlock, str] | None,
    ) -> DiffItem:
        all_text = table.all_cell_text()[:300]
        orig_blocks = self._as_block_list(blocks) if diff_type == "DELETE" else []
        comp_blocks = self._as_block_list(other_blocks) if diff_type == "ADD" else []
        caption = getattr(table, "caption", "") or ""
        caption_prefix = f"「{caption}」" if caption else ""
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type=diff_type,
            title=f"表格{caption_prefix}：{utils.table_type_label(utils.table_type(table))}",
            original_text=all_text if diff_type == "DELETE" else "",
            compare_text=all_text if diff_type == "ADD" else "",
            original_snippet=all_text if diff_type == "DELETE" else "",
            compare_snippet=all_text if diff_type == "ADD" else "",
            readable_change=f"整表{'删除' if diff_type == 'DELETE' else '新增'}",
            source_type="table",
            original_evidence=self._evidence_from_blocks(orig_blocks, diff_type),
            compare_evidence=self._evidence_from_blocks(comp_blocks, diff_type),
        )

    def find_block(self, blocks: list[tuple[TextBlock, str]], table: StructuredTable) -> tuple[TextBlock, str] | None:
        found = self.find_blocks(blocks, table)
        return found[0] if found else None

    def find_blocks(self, blocks: list[tuple[TextBlock, str]], table: StructuredTable) -> list[tuple[TextBlock, str]]:
        source_block_ids = self._table_source_block_ids(table)
        found: list[tuple[TextBlock, str]] = []
        seen: set[str] = set()
        for block, text in blocks:
            if source_block_ids and block.block_id in source_block_ids:
                found.append((block, text))
                seen.add(block.block_id)
        if found:
            return found
        for block, text in blocks:
            if block.page_no == table.page_no:
                table_text = table.all_cell_text()
                block_clean = utils.normalize(utils.strip_html(text))
                table_clean = utils.normalize(table_text)
                if table_clean and block_clean and SequenceMatcher(None, block_clean[:200], table_clean[:200]).ratio() > 0.5:
                    if block.block_id not in seen:
                        found.append((block, text))
                        seen.add(block.block_id)
                    break
        return found

    @staticmethod
    def _table_source_block_ids(table: StructuredTable) -> set[str]:
        source_block_ids: set[str] = set()
        table_source = getattr(table, "source_block_id", "") or ""
        if table_source:
            source_block_ids.add(table_source)
        for row in getattr(table, "rows", []):
            row_source = getattr(row, "source_block_id", "") or ""
            if row_source:
                source_block_ids.add(row_source)
            for cell in getattr(row, "cells", []):
                cell_source = getattr(cell, "source_block_id", "") or ""
                if cell_source:
                    source_block_ids.add(cell_source)
        return source_block_ids

    @staticmethod
    def _as_block_list(
        blocks: list[tuple[TextBlock, str]] | tuple[TextBlock, str] | None,
    ) -> list[tuple[TextBlock, str]]:
        if blocks is None:
            return []
        if isinstance(blocks, tuple):
            return [blocks]
        return blocks

    @staticmethod
    def _resolve_caption(orig_table, comp_table) -> str:
        """Pick the best caption from either side of the comparison."""
        orig_caption = getattr(orig_table, "caption", "") or ""
        comp_caption = getattr(comp_table, "caption", "") or ""
        if orig_caption and comp_caption:
            return orig_caption if len(orig_caption) <= len(comp_caption) else comp_caption
        return orig_caption or comp_caption

    # --- Layout shift detection ---

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

        original_cells = self._matcher.row_match_cells(original, orig_row)
        compare_cells = self._matcher.row_match_cells(compare, comp_row)
        if len(original_cells) < 2 or len(compare_cells) < 2:
            return None

        original_source = self._matcher.row_source_text(original, orig_row, original_block)
        compare_source = self._matcher.row_source_text(compare, comp_row, compare_block)
        candidates: list[tuple[float, int, int]] = []
        for orig_cell in original_cells:
            for comp_cell in compare_cells:
                score = self._matcher.row_cell_match_score(
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
        original_unmatched = [cell for cell in self._matcher.row_match_cells(original, orig_row) if int(cell["col"]) not in matched_orig]
        compare_unmatched = [cell for cell in self._matcher.row_match_cells(compare, comp_row) if int(cell["col"]) not in matched_comp]

        diffs: list[CellDiff] = []
        pair_count = min(len(original_unmatched), len(compare_unmatched))
        for index in range(pair_count):
            orig_cell = original_unmatched[index]
            comp_cell = compare_unmatched[index]
            orig_text = str(orig_cell["text"])
            comp_text = str(comp_cell["text"])
            orig_norm = str(orig_cell["norm"])
            comp_norm = str(comp_cell["norm"])
            if orig_norm == comp_norm or self._matcher.is_similar_ocr_noise(orig_norm, comp_norm):
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
            if utils.normalize_cell_for_compare(self._matcher._cell_text(original, orig_row, col)) != utils.normalize_cell_for_compare(self._matcher._cell_text(compare, comp_row, col)):
                preliminary_diffs += 1
        if preliminary_diffs < 2:
            return False

        first_original = str(original_cells[0]["norm"])
        first_compare = str(compare_cells[0]["norm"])
        same_leading_identifier = first_original == first_compare
        row_score = self._matcher._row_similarity(original, orig_row, compare, comp_row)
        return same_leading_identifier or row_score >= 0.72 or off_column_matches >= 2

    # --- Evidence methods ---

    def _evidence_from_blocks(
        self,
        blocks: list[tuple[TextBlock, str]],
        highlight_type: str,
    ) -> list[EvidenceBox]:
        evidences: list[EvidenceBox] = []
        seen: set[tuple[str, int, float, float, float, float]] = set()
        for block_and_text in blocks:
            for evidence in self._evidence_from_block(block_and_text, highlight_type):
                bbox = evidence.bbox
                key = (
                    block_and_text[0].block_id,
                    evidence.page_no,
                    bbox.x0,
                    bbox.y0,
                    bbox.x1,
                    bbox.y1,
                )
                if key in seen:
                    continue
                seen.add(key)
                evidences.append(evidence)
        return evidences

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
            cell = utils.anchor_cell(table, row, col)
            bbox = self._usable_cell_bbox(table, row, cell, cell.text if cell else "")
            if bbox:
                bboxes.append(bbox)
        if not bboxes:
            return None
        row_heights: list[float] = []
        rows = {row for cd in group.diffs if (row := self._diff_row(cd, side)) is not None}
        for row in rows:
            for c in range(table.col_count):
                cell = utils.anchor_cell(table, row, c)
                if cell and cell.bbox:
                    row_heights.append(cell.bbox.y1 - cell.bbox.y0)
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
        compact_text = utils.normalize(text)
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
            other = utils.anchor_cell(table, row, other_col)
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

        compact = utils.normalize(text)
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
            cell = utils.anchor_cell(table, row, col)
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
        if block is None or not block.char_boxes or not block.text or not utils.normalize(fragment):
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
        normalized_fragment = utils.normalize(fragment)
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
            normalized = utils.normalize(char)
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
            elif side == "original" and cd.diff_type == "DELETE" and utils.normalize(cd.original_text):
                return True
            elif side == "compare" and cd.diff_type == "ADD" and utils.normalize(cd.compare_text):
                return True
        return False

    def _side_fallback_highlight_type(self, group: CellDiffGroup, side: str) -> str:
        types: set[str] = set()
        for cd in group.diffs:
            if cd.char_segments:
                types.update(highlight_type for _, _, highlight_type in self._side_char_ranges(cd, side))
            elif side == "original" and cd.diff_type == "DELETE" and utils.normalize(cd.original_text):
                types.add("DELETE")
            elif side == "compare" and cd.diff_type == "ADD" and utils.normalize(cd.compare_text):
                types.add("ADD")
        return next(iter(types)) if len(types) == 1 else "MODIFY"

    def _side_char_ranges(self, cd: CellDiff, side: str) -> list[tuple[int, int, str]]:
        ranges: list[tuple[int, int, str]] = []
        for segment in cd.char_segments:
            original_fragment = cd.original_text[segment.orig_start:segment.orig_end]
            compare_fragment = cd.compare_text[segment.comp_start:segment.comp_end]
            original_visible = bool(utils.normalize(original_fragment))
            compare_visible = bool(utils.normalize(compare_fragment))

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

    @staticmethod
    def _compute_char_segments(orig_text: str, comp_text: str) -> list[CharSegment]:
        sm = SequenceMatcher(None, orig_text, comp_text)
        segments: list[CharSegment] = []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                segments.append(CharSegment(tag=tag, orig_start=i1, orig_end=i2, comp_start=j1, comp_end=j2))
        return segments
