"""Thin facade for TableComparator — delegates to submodules."""

from __future__ import annotations

from difflib import SequenceMatcher
import logging
import re

from app.models import DiffItem, Document, EvidenceBox
from app.services.table_compare.parser import LogicalTableParser
from app.services.table_compare.repair import TableRepairService
from app.services.table_compare.matcher import TableMatcher
from app.services.table_compare.diff_builder import TableDiffBuilder
from app.services.table_compare.summary import SummaryComparator
from app.services.table_compare.flat import FlatTextComparator
from app.services.table_compare.constants import TABLE_BLOCK_TYPES
from app.services.table_compare.types import CellDiff, _LogicalTable
from app.services.table_compare import utils
from app.utils.id_utils import generate_diff_id
from app.services.region_comparison import RegionComparator, RegionComparisonResult

logger = logging.getLogger(__name__)


class TableComparator:
    """Public API for table comparison — delegates to specialized submodules."""

    table_block_types = TABLE_BLOCK_TYPES
    cell_similarity_threshold: float = 0.85
    row_similarity_threshold: float = 0.55
    table_similarity_threshold: float = 0.3

    def __init__(self) -> None:
        self._parser = LogicalTableParser()
        self._repair = TableRepairService()
        self._matcher = TableMatcher()
        self._matcher.row_similarity_threshold = self.row_similarity_threshold
        self._matcher.table_similarity_threshold = self.table_similarity_threshold
        self._summary = SummaryComparator(self._matcher, self._repair)
        self._diff_builder = TableDiffBuilder(self._matcher, self._summary)
        self._diff_builder.cell_similarity_threshold = self.cell_similarity_threshold
        self._flat = FlatTextComparator()
        self.last_debug_payload: dict[str, object] = {}
        self.region_result = RegionComparisonResult()
        self._region_comparator = None

    def build_diffs(
        self,
        original: Document,
        compare: Document,
        start_index: int = 1,
        *,
        page_alignment=None,
    ) -> tuple[list[DiffItem], list[str]]:
        self._matcher.page_alignment = page_alignment
        self._region_comparator = RegionComparator(original, compare, page_alignment)
        self.region_result = self._region_comparator.compare_documents()
        self._diff_builder.claimed_rows = self.region_result.claimed_rows
        self._diff_builder.page_blocks = {
            "original": [b for p in original.pages for b in p.blocks],
            "compare": [b for p in compare.pages for b in p.blocks],
        }
        warnings: list[str] = []
        self.last_debug_payload = {
            "table_block_counts": {"original": 0, "compare": 0},
            "parsed_table_counts": {"original": 0, "compare": 0},
            "logical_table_counts": {"original": 0, "compare": 0},
            "quality": {},
            "quality_metrics": {},
            "shape_changes": {},
            "suppressed_diffs": {},
            "tier": "not_run",
            "repair": {"original": {}, "compare": {}},
            "tables": {"original": [], "compare": []},
        }

        original_blocks = self._parser.table_blocks(original)
        compare_blocks = self._parser.table_blocks(compare)
        self.last_debug_payload["table_block_counts"] = {
            "original": len(original_blocks),
            "compare": len(compare_blocks),
        }
        if not original_blocks and not compare_blocks:
            if any(page.blocks for page in [*original.pages, *compare.pages]):
                warnings.append("未获得结构化表格区域，已跳过表格比对。")
            self.last_debug_payload["tier"] = "no_structured_tables"
            self.last_debug_payload["warnings"] = list(warnings)
            return self._finish_regions([], start_index), warnings

        original_parsed = self._parser.parse_tables(original_blocks)
        compare_parsed = self._parser.parse_tables(compare_blocks)
        self.last_debug_payload["parsed_table_counts"] = {
            "original": len(original_parsed),
            "compare": len(compare_parsed),
        }

        self._repair.reset_diagnostics("original")
        original_tables = self._parser.stitch_logical_tables(
            original_parsed,
            self._repair,
        )
        original_repair_debug = self._repair.diagnostics_payload()
        self._repair.reset_diagnostics("compare")
        compare_tables = self._parser.stitch_logical_tables(
            compare_parsed,
            self._repair,
        )
        compare_repair_debug = self._repair.diagnostics_payload()
        self.last_debug_payload["logical_table_counts"] = {
            "original": len(original_tables),
            "compare": len(compare_tables),
        }
        self.last_debug_payload["repair"] = {
            "original": original_repair_debug,
            "compare": compare_repair_debug,
        }
        self.last_debug_payload["tables"] = {
            "original": [self._table_debug_payload(table) for table in original_tables],
            "compare": [self._table_debug_payload(table) for table in compare_tables],
        }
        self.last_debug_payload["quality_metrics"] = {
            "original": self._tables_quality_metrics(original_tables),
            "compare": self._tables_quality_metrics(compare_tables),
        }
        self.last_debug_payload["shape_changes"] = {
            "original": self._table_shape_change(original_parsed, original_tables),
            "compare": self._table_shape_change(compare_parsed, compare_tables),
            "compare_vs_original_logical": self._logical_shape_delta(original_tables, compare_tables),
        }

        if original_tables or compare_tables:
            original_quality = self._compute_table_quality_detail(original_tables) if original_tables else self._perfect_quality("no_original_tables")
            compare_quality = self._compute_table_quality_detail(compare_tables) if compare_tables else self._perfect_quality("no_compare_tables")
            orig_score = float(original_quality["score"])
            comp_score = float(compare_quality["score"])
            min_score = min(orig_score, comp_score)
            diagnostic_reasons = self._quality_diagnostic_reasons(original_quality, compare_quality, min_score)
            self.last_debug_payload["quality"] = {
                "original_score": round(orig_score, 4),
                "compare_score": round(comp_score, 4),
                "min": round(min_score, 4),
                "details": {
                    "original": original_quality,
                    "compare": compare_quality,
                },
                "diagnostic_reasons": diagnostic_reasons,
                "route_decision": "structured_table_compare",
                "score_policy": "diagnostic_only",
            }

            self.last_debug_payload["tier"] = "cell_level"
            self._diff_builder.reset_diagnostics()
            diffs, warnings = self._compare_tables(
                original_tables, compare_tables, original_blocks, compare_blocks, start_index, warnings
            )
            # Resolve field identity before treating a missing layout as a
            # deleted form, including forms with real printed-field changes.
            from app.services.signing_table_equivalence import reconcile_printed_form
            kept = []
            equivalent_forms = []
            for diff in diffs:
                deleted = diff.diff_type == "DELETE"
                present, counterpart = (original, compare) if deleted else (compare, original)
                evidences = diff.original_evidence if deleted else diff.compare_evidence
                alignment = diff.match_score_details.get("page_alignment", {})
                counterpart_pages = alignment.get("compare_pages" if deleted else "original_pages", [])
                pages = {e.page_no for e in evidences}
                replacement = None
                if (diff.match_score_details.get("decision_reason") == "ALIGNED_PAGE_STRUCTURE_NOT_EXTRACTED"
                        and len(pages) == len(counterpart_pages) == 1):
                    left = next((p for p in present.pages if p.page_no in pages), None)
                    right = next((p for p in counterpart.pages if p.page_no in counterpart_pages), None)
                    if left is not None and right is not None:
                        replacement = reconcile_printed_form(diff, left, right) if deleted else reconcile_printed_form(diff, right, left)
                if replacement is not None:
                    kept.extend(replacement)
                    equivalent_forms.append({"diff_id": diff.diff_id,
                                             "reason": "party_printed_field_changes" if replacement else "same_party_printed_fields",
                                             "replacement_diff_ids": [item.diff_id for item in replacement],
                                             "present_pages": sorted(pages), "counterpart_pages": counterpart_pages})
                else:
                    kept.append(diff)
            diffs = kept
            for diff in diffs:
                if (diff.source_type != "table" or diff.diff_type not in {"DELETE", "ADD"}
                        or not diff.readable_change.startswith("整表")):
                    continue
                deleted = diff.diff_type == "DELETE"
                if diff.match_score_details.get("counterpart_table_state") == "PAGE_ABSENT":
                    continue
                counterpart = compare if deleted else original
                counterpart_blocks = compare_blocks if deleted else original_blocks
                if self._protect_unstructured_table_presence(diff, counterpart_blocks, counterpart):
                    continue
                # Absence is an explicit observation, not a parser miss. Old
                # documents default to UNKNOWN and therefore remain reviewable.
                alignment = diff.match_score_details.get("page_alignment") or {}
                key = "compare_pages" if deleted else "original_pages"
                pages = alignment.get(key) or [e.page_no for e in (
                    diff.original_evidence if deleted else diff.compare_evidence)]
                known = [p for p in counterpart.pages if p.page_no in pages]
                if known and len(known) == len(set(pages)) and all(
                    p.table_presence_status == "TABLE_ABSENT"
                    and not any(self._parser.is_table_block(block) for block in p.blocks)
                    for p in known
                ):
                    diff.match_score_details = {**diff.match_score_details,
                                                "counterpart_table_state": "TABLE_ABSENT"}
                    continue
                diff.diff_type = "MODIFY"
                diff.title = "表格存在性待复核"
                diff.readable_change = "对侧表格存在性证据不足；需人工复核"
                diff.quality_status = "NEEDS_REVIEW"
                diff.match_score_details = {**diff.match_score_details,
                                            "change_decision": "CANDIDATE",
                                            "decision_reason": "TABLE_PRESENCE_UNKNOWN",
                                            "counterpart_table_state": "UNKNOWN"}
                diff.review_flags.append("TABLE_PRESENCE_UNKNOWN")
            self.last_debug_payload["cross_representation_equivalence"] = equivalent_forms
            self.last_debug_payload["diff_count"] = len(diffs)
            self.last_debug_payload["suppressed_diffs"] = self._diff_builder.diagnostics_payload()
            self.last_debug_payload["warnings"] = list(warnings)
            return self._finish_regions(diffs, start_index), warnings

        # Fallback: flat-text comparison for blocks without HTML
        self.last_debug_payload["tier"] = "flat_text_no_html"
        diffs, warnings = self._flat.flat_compare(
            [(b, t) for b, t in original_blocks if not self.region_result.claims('original', b)],
            [(b, t) for b, t in compare_blocks if not self.region_result.claims('compare', b)],
            start_index, warnings)
        self.last_debug_payload["flat_text"] = self._flat.last_debug_payload
        self.last_debug_payload["diff_count"] = len(diffs)
        self.last_debug_payload["suppressed_diffs"] = self._empty_suppressed_diff_payload()
        self.last_debug_payload["warnings"] = list(warnings)
        return self._finish_regions(diffs, start_index), warnings

    def _finish_regions(self, diffs, start_index):
        diffs = self._renumber_diffs([*diffs, *self.region_result.diffs], start_index)
        self.last_debug_payload['region_comparison'] = self.region_result.diagnostics()
        self.last_debug_payload['diff_count'] = len(diffs)
        return diffs

    @staticmethod
    def _tables_quality_ok(tables: list[_LogicalTable], min_score: float = 0.1) -> bool:
        """Backward-compatible boolean quality gate — delegates to _compute_table_quality."""
        return TableComparator._compute_table_quality(tables) >= min_score

    @staticmethod
    def _compute_table_quality(tables: list[_LogicalTable]) -> float:
        return float(TableComparator._compute_table_quality_detail(tables)["score"])

    @staticmethod
    def _compute_table_quality_detail(tables: list[_LogicalTable]) -> dict[str, object]:
        if not tables:
            return {
                "score": 0.0,
                "grid_score": 0.0,
                "bbox_score": 0.0,
                "text_score": 0.0,
                "continuation_score": 0.0,
                "business_score": 0.0,
                "reasons": ["no_logical_tables"],
            }

        total_cells = 0
        filled_cells = 0
        total_text_len = 0
        bbox_cells = 0

        for table in tables:
            total_cells += table.col_count * len(table.rows)
            for row in table.rows:
                for cell in row.cells:
                    text = cell.text.strip()
                    if text:
                        filled_cells += 1
                        total_text_len += len(utils.normalize(text))
                    if cell.bbox is not None:
                        bbox_cells += 1

        if total_cells == 0:
            return {
                "score": 0.0,
                "grid_score": 0.0,
                "bbox_score": 0.0,
                "text_score": 0.0,
                "continuation_score": 0.0,
                "business_score": 0.0,
                "reasons": ["empty_table_grid"],
            }

        fill_rate = filled_cells / total_cells
        col_counts = {table.col_count for table in tables}
        col_consistency = 1.0 if len(col_counts) <= 1 else 0.8
        row_presence = min(sum(len(table.rows) for table in tables) / max(len(tables), 1), 3.0) / 3.0
        grid_score = min(1.0, fill_rate * 0.20 + col_consistency * 0.45 + row_presence * 0.35)

        bbox_coverage = bbox_cells / total_cells
        geometry_confidence = min((table.geometry_confidence for table in tables), default=1.0)
        geometry_statuses = {getattr(table, "geometry_status", "not_available") for table in tables}
        if geometry_statuses == {"not_available"}:
            bbox_score = 0.9
        elif "geometry_unusable" in geometry_statuses and "severe_conflict" not in geometry_statuses:
            bbox_score = 0.75
        else:
            bbox_score = min(1.0, bbox_coverage * 0.55 + geometry_confidence * 0.45)
        if "severe_conflict" in geometry_statuses:
            bbox_score = min(bbox_score, 0.2)
        elif "low_confidence" in geometry_statuses and "geometry_unusable" not in geometry_statuses:
            bbox_score = min(bbox_score, 0.70)

        avg_text_len = total_text_len / max(filled_cells, 1)
        content_density = min(avg_text_len / 8.0, 1.0)
        token_coverages = [
            coverage
            for table in tables
            if (coverage := TableComparator._source_text_token_coverage(table)) is not None
        ]
        has_source_text = any(row.source_text for table in tables for row in table.rows)
        if token_coverages:
            token_coverage = sum(token_coverages) / len(token_coverages)
            text_score = min(1.0, token_coverage * 0.65 + content_density * 0.35)
            if token_coverage >= 0.35:
                text_score = max(0.75, text_score)
        elif has_source_text:
            text_score = content_density
        else:
            text_score = max(0.85, content_density)

        continuation_score = TableComparator._continuation_quality_score(tables)
        business_score = TableComparator._business_quality_score(tables)
        html_grid_usable = (
            grid_score >= 0.80
            and text_score >= 0.75
            and business_score >= 0.75
            and "severe_conflict" not in geometry_statuses
        )

        score = (
            grid_score * 0.30
            + bbox_score * 0.20
            + text_score * 0.20
            + continuation_score * 0.10
            + business_score * 0.20
        )
        reasons = TableComparator._quality_reasons(
            grid_score,
            bbox_score,
            text_score,
            continuation_score,
            business_score,
            tables,
        )
        return {
            "score": round(score, 4),
            "grid_score": round(grid_score, 4),
            "bbox_score": round(bbox_score, 4),
            "text_score": round(text_score, 4),
            "continuation_score": round(continuation_score, 4),
            "business_score": round(business_score, 4),
            "fill_rate": round(fill_rate, 4),
            "bbox_coverage": round(bbox_coverage, 4),
            "geometry_statuses": sorted(geometry_statuses),
            "html_grid_usable": html_grid_usable,
            "reasons": reasons,
        }

    @staticmethod
    def _perfect_quality(reason: str) -> dict[str, object]:
        return {
            "score": 1.0,
            "grid_score": 1.0,
            "bbox_score": 1.0,
            "text_score": 1.0,
            "continuation_score": 1.0,
            "business_score": 1.0,
            "html_grid_usable": True,
            "reasons": [reason],
        }

    @staticmethod
    def _continuation_quality_score(tables: list[_LogicalTable]) -> float:
        pages = {row.page_no for table in tables for row in table.rows if row.page_no}
        if len(pages) <= 1:
            return 1.0
        product_like = any(table.col_count >= 6 and utils.table_type(table) == "product" for table in tables)
        sequence_count = 0
        reset_count = 0
        previous_seq: int | None = None
        for table in tables:
            for row in table.rows:
                seq = TableComparator._row_sequence_int(row)
                if seq is None:
                    continue
                sequence_count += 1
                if previous_seq is not None and seq <= previous_seq:
                    reset_count += 1
                previous_seq = seq
        if sequence_count >= 2 and reset_count <= 1:
            return 0.9
        if product_like and sequence_count >= 2:
            return 0.8
        if sequence_count:
            return 0.65
        if any("续" in row.source_text or "continued" in row.source_text.lower() for table in tables for row in table.rows):
            return 0.75
        return 0.45

    @staticmethod
    def _business_quality_score(tables: list[_LogicalTable]) -> float:
        product_like = any(table.col_count >= 6 for table in tables)
        if not product_like:
            return 0.9
        scored_rows = 0
        complete_rows = 0
        partial_credit = 0.0
        for table in tables:
            for row in table.rows:
                if TableComparator._row_sequence_int(row) is None:
                    continue
                scored_rows += 1
                name = TableComparator._cell_text_from_row(row, 1)
                unit_col, quantity_col = (3, 4) if table.col_count >= 10 else (4, 5)
                unit = TableComparator._cell_text_from_row(row, unit_col)
                quantity = TableComparator._cell_text_from_row(row, quantity_col)
                signals = [
                    bool(utils.normalize(name)),
                    bool(utils.normalize(unit)),
                    bool(utils.canonical_quantity(quantity) or utils.is_number_like(utils.normalize(quantity))),
                ]
                row_score = sum(1 for signal in signals if signal) / len(signals)
                partial_credit += row_score
                if row_score >= 0.75:
                    complete_rows += 1
        if scored_rows == 0:
            return 0.65
        return max(0.78, min(1.0, (complete_rows / scored_rows) * 0.55 + (partial_credit / scored_rows) * 0.45))

    @staticmethod
    def _quality_reasons(
        grid_score: float,
        bbox_score: float,
        text_score: float,
        continuation_score: float,
        business_score: float,
        tables: list[_LogicalTable],
    ) -> list[str]:
        reasons: list[str] = []
        if grid_score < 0.75:
            reasons.append("low_grid_topology_score")
        if bbox_score < 0.75:
            reasons.append("low_bbox_alignment_score")
        if text_score < 0.75:
            reasons.append("low_html_ocr_token_coverage")
        if continuation_score < 0.75:
            reasons.append("weak_continuation_signals")
        if business_score < 0.75:
            reasons.append("incomplete_business_fields")
        geometry_warnings = sorted({
            warning
            for table in tables
            for warning in table.geometry_warnings
        })
        reasons.extend(f"geometry:{warning}" for warning in geometry_warnings)
        if any(table.geometry_status == "severe_conflict" for table in tables):
            reasons.append("geometry:severe_conflict")
        return list(dict.fromkeys(reasons))

    @staticmethod
    def _quality_diagnostic_reasons(
        original_quality: dict[str, object],
        compare_quality: dict[str, object],
        min_score: float,
    ) -> list[str]:
        reasons = [f"score={min_score:.4f}"]
        for side, quality in (("original", original_quality), ("compare", compare_quality)):
            for reason in quality.get("reasons", []):
                if reason in {"no_original_tables", "no_compare_tables"}:
                    continue
                reasons.append(f"{side}:{reason}")
        return list(dict.fromkeys(reasons))

    @staticmethod
    def _row_sequence_int(row) -> int | None:
        for cell in row.cells:
            text = utils.normalize(cell.text)
            if re.fullmatch(r"\d{1,3}", text):
                return int(text)
        return None

    @staticmethod
    def _cell_text_from_row(row, col: int) -> str:
        for cell in row.cells:
            if cell.col_index <= col < cell.col_index + cell.colspan:
                return cell.text
        return ""

    @staticmethod
    def _table_debug_payload(table: _LogicalTable) -> dict[str, object]:
        expected_cells = table.col_count * len(table.rows)
        nonempty_cells = sum(
            1
            for row in table.rows
            for cell in row.cells
            if utils.normalize(cell.text)
        )
        bbox_cells = sum(
            1
            for row in table.rows
            for cell in row.cells
            if cell.bbox is not None
        )
        return {
            "page_no": table.page_no,
            "source_block_id": table.source_block_id,
            "caption": table.caption,
            "row_count": len(table.rows),
            "col_count": table.col_count,
            "expected_cell_count": expected_cells,
            "nonempty_cell_count": nonempty_cells,
            "bbox_cell_count": bbox_cells,
            "fill_rate": round(nonempty_cells / expected_cells, 4) if expected_cells else 0.0,
            "bbox_coverage": round(bbox_cells / expected_cells, 4) if expected_cells else 0.0,
            "source_text_token_coverage": TableComparator._source_text_token_coverage(table),
            "geometry_status": table.geometry_status,
            "geometry_confidence": round(table.geometry_confidence, 4),
            "geometry_warnings": table.geometry_warnings,
            "geometry_strategy": table.geometry_strategy,
            "bbox_grid_row_count": table.bbox_grid_row_count,
            "bbox_grid_col_count": table.bbox_grid_col_count,
            "text_preview": table.all_cell_text()[:300],
        }

    @staticmethod
    def _tables_quality_metrics(tables: list[_LogicalTable]) -> dict[str, object]:
        table_payloads = [TableComparator._table_debug_payload(table) for table in tables]
        table_count = len(tables)
        row_count = sum(int(item["row_count"]) for item in table_payloads)
        expected_cells = sum(int(item["expected_cell_count"]) for item in table_payloads)
        nonempty_cells = sum(int(item["nonempty_cell_count"]) for item in table_payloads)
        bbox_cells = sum(int(item["bbox_cell_count"]) for item in table_payloads)
        token_coverages = [
            float(item["source_text_token_coverage"])
            for item in table_payloads
            if item["source_text_token_coverage"] is not None
        ]
        return {
            "table_count": table_count,
            "row_count": row_count,
            "col_counts": [table.col_count for table in tables],
            "max_col_count": max((table.col_count for table in tables), default=0),
            "expected_cell_count": expected_cells,
            "nonempty_cell_count": nonempty_cells,
            "bbox_cell_count": bbox_cells,
            "fill_rate": round(nonempty_cells / expected_cells, 4) if expected_cells else 0.0,
            "bbox_coverage": round(bbox_cells / expected_cells, 4) if expected_cells else 0.0,
            "source_text_token_coverage": (
                round(sum(token_coverages) / len(token_coverages), 4)
                if token_coverages
                else None
            ),
            "geometry_status_counts": TableComparator._geometry_status_counts(tables),
            "geometry_warning_counts": TableComparator._geometry_warning_counts(tables),
        }

    @staticmethod
    def _geometry_status_counts(tables: list[_LogicalTable]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in tables:
            counts[table.geometry_status] = counts.get(table.geometry_status, 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _geometry_warning_counts(tables: list[_LogicalTable]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in tables:
            for warning in table.geometry_warnings:
                counts[warning] = counts.get(warning, 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _table_shape_change(parsed_tables: list, logical_tables: list[_LogicalTable]) -> dict[str, object]:
        parsed_rows = sum(len(table.rows) for table in parsed_tables)
        logical_rows = sum(len(table.rows) for table in logical_tables)
        parsed_max_cols = max((table.col_count for table in parsed_tables), default=0)
        logical_max_cols = max((table.col_count for table in logical_tables), default=0)
        return {
            "parsed_table_count": len(parsed_tables),
            "logical_table_count": len(logical_tables),
            "table_count_delta": len(logical_tables) - len(parsed_tables),
            "parsed_row_count": parsed_rows,
            "logical_row_count": logical_rows,
            "row_count_delta": logical_rows - parsed_rows,
            "parsed_max_col_count": parsed_max_cols,
            "logical_max_col_count": logical_max_cols,
            "max_col_count_delta": logical_max_cols - parsed_max_cols,
        }

    @staticmethod
    def _logical_shape_delta(original: list[_LogicalTable], compare: list[_LogicalTable]) -> dict[str, object]:
        original_metrics = TableComparator._tables_quality_metrics(original)
        compare_metrics = TableComparator._tables_quality_metrics(compare)
        return {
            "table_count_delta": int(compare_metrics["table_count"]) - int(original_metrics["table_count"]),
            "row_count_delta": int(compare_metrics["row_count"]) - int(original_metrics["row_count"]),
            "max_col_count_delta": int(compare_metrics["max_col_count"]) - int(original_metrics["max_col_count"]),
            "fill_rate_delta": round(float(compare_metrics["fill_rate"]) - float(original_metrics["fill_rate"]), 4),
            "bbox_coverage_delta": round(
                float(compare_metrics["bbox_coverage"]) - float(original_metrics["bbox_coverage"]),
                4,
            ),
        }

    @staticmethod
    def _source_text_token_coverage(table: _LogicalTable) -> float | None:
        source_text = " ".join(
            dict.fromkeys(row.source_text for row in table.rows if row.source_text)
        )
        source_norm = utils.normalize(source_text)
        if not source_norm:
            return None
        tokens = TableComparator._coverage_tokens(table.all_cell_text())
        if not tokens:
            return None
        covered = sum(
            1
            for token in tokens
            if token in source_norm
            or utils.punctuation_fold(token) in utils.punctuation_fold(source_norm)
            or utils.is_loose_subsequence_present(token, source_norm)
        )
        return round(covered / len(tokens), 4)

    @staticmethod
    def _coverage_tokens(text: str) -> list[str]:
        compact = utils.normalize(text)
        if not compact:
            return []
        tokens: list[str] = []
        tokens.extend(match.group(0) for match in re.finditer(r"[A-Za-z]*\d[A-Za-z0-9._/\-]*", compact))
        tokens.extend(match.group(0) for match in re.finditer(r"[\u4e00-\u9fff]{2,}", compact))
        return [token for token in dict.fromkeys(tokens) if len(token) >= 2]

    @staticmethod
    def _empty_suppressed_diff_payload() -> dict[str, object]:
        return {
            "suppressed_diff_count": 0,
            "suppressed_diff_counts_by_reason": {},
            "suppressed_diffs": [],
        }

    def is_table_block(self, block) -> bool:
        return self._parser.is_table_block(block)

    def _compare_tables(
        self,
        original_tables,
        compare_tables,
        original_blocks,
        compare_blocks,
        start_index: int,
        warnings: list[str],
    ) -> tuple[list[DiffItem], list[str]]:
        pairs = self._matcher.match_tables(original_tables, compare_tables)
        diffs: list[DiffItem] = []
        next_index = start_index
        used_original: set[int] = set()
        used_compare: set[int] = set()

        from app.services.table_compare.continuation import join_continuations
        pages = self._region_comparator.pages if self._region_comparator is not None else {}
        used_original, left_joins = join_continuations(
            original_tables, compare_tables, [(a, b) for a, b, _ in pairs], pages.get('original'))
        used_compare, right_joins = join_continuations(
            compare_tables, original_tables, [(b, a) for a, b, _ in pairs], pages.get('compare'))
        self.last_debug_payload["continuation_content_matches"] = [
            *[{"side": "original", **item} for item in left_joins],
            *[{"side": "compare", **item} for item in right_joins],
        ]

        if self._region_comparator is not None:
            self._region_comparator.reconcile_rows(original_tables, compare_tables, pairs, self._matcher)

        for orig_idx, comp_idx, score in pairs:
            used_original.add(orig_idx)
            used_compare.add(comp_idx)
            orig_table = original_tables[orig_idx]
            comp_table = compare_tables[comp_idx]

            orig_block = self._diff_builder.find_block(original_blocks, orig_table)
            comp_block = self._diff_builder.find_block(compare_blocks, comp_table)
            self._diff_builder.source_blocks = {
                "original": {block.block_id: block for block, _ in self._diff_builder.find_blocks(original_blocks, orig_table)},
                "compare": {block.block_id: block for block, _ in self._diff_builder.find_blocks(compare_blocks, comp_table)},
            }
            cell_diffs = self._diff_builder.diff_cells(
                orig_table,
                comp_table,
                orig_block[0] if orig_block else None,
                comp_block[0] if comp_block else None,
            )
            cell_diffs = self._reconcile_quote_remark_cell_diffs(orig_table, comp_table, cell_diffs)
            for group in self._diff_builder.group_cell_diffs(cell_diffs, orig_table, comp_table):
                diffs.append(self._diff_builder.make_diff(group, next_index, orig_block, comp_block, orig_table, comp_table))
                next_index += 1

        # Unmatched tables: whole-table ADD / DELETE
        for idx in range(len(original_tables)):
            if idx not in used_original:
                blocks = self._diff_builder.find_blocks(original_blocks, original_tables[idx])
                diff = self._diff_builder.whole_table_diff(
                    original_tables[idx], "DELETE", next_index, blocks, None
                )
                self._mark_aligned_unmatched_table_candidate(diff, original_tables[idx].page_no, deleted=True)
                diffs.append(diff)
                next_index += 1
        for idx in range(len(compare_tables)):
            if idx not in used_compare:
                blocks = self._diff_builder.find_blocks(compare_blocks, compare_tables[idx])
                diff = self._diff_builder.whole_table_diff(
                    compare_tables[idx], "ADD", next_index, None, blocks
                )
                self._mark_aligned_unmatched_table_candidate(diff, compare_tables[idx].page_no, deleted=False)
                diffs.append(diff)
                next_index += 1

        diffs = self._reconcile_quote_remark_diffs(diffs)
        return self._renumber_diffs(diffs, start_index), warnings

    def _protect_unstructured_table_presence(
        self, diff: DiffItem, counterpart_blocks: list[tuple], counterpart: Document,
    ) -> bool:
        """A located OCR table on the aligned page is evidence against deletion."""
        alignment = diff.match_score_details.get("page_alignment") or {}
        deleted = diff.diff_type == "DELETE"
        source_evidence = diff.original_evidence if deleted else diff.compare_evidence
        source_pages = {e.page_no for e in source_evidence}
        counterpart_key = "compare_pages" if deleted else "original_pages"
        counterpart_pages = {int(page) for page in alignment.get(counterpart_key) or []}
        if not counterpart_pages:
            counterpart_pages = {p.page_no for p in counterpart.pages if p.page_no in source_pages}
        if not counterpart_pages or not source_evidence:
            return False

        def area(box) -> float:
            return max(0.0, box.x1 - box.x0) * max(0.0, box.y1 - box.y0)

        def coverage(first, second) -> float:
            intersection = max(0.0, min(first.x1, second.x1) - max(first.x0, second.x0)) * max(
                0.0, min(first.y1, second.y1) - max(first.y0, second.y0)
            )
            return intersection / max(min(area(first), area(second)), 1.0)

        regions = {}
        for block, _ in counterpart_blocks:
            if (block.page_no not in counterpart_pages or block.block_type != "table"
                    or self._parser.structure_state(block) != "UNSTRUCTURED_TABLE_PRESENT"
                    or not block.text.strip()):
                continue
            bounds = block.layout_bbox or block.bbox
            if not any(coverage(evidence.bbox, bounds) >= 0.5 for evidence in source_evidence):
                continue
            key = (block.page_no, block.layout_block_id or block.block_id)
            regions.setdefault(key, []).append(block)
        if not regions:
            return False

        blocks = [block for group in regions.values() for block in group]
        diff.diff_type = "MODIFY"
        diff.title = "表格结构待复核"
        diff.readable_change = "对侧存在表格文字，但结构未解析；需人工复核"
        text = " ".join(block.text for block in blocks)[:300]
        if deleted:
            diff.compare_text = diff.compare_snippet = text
        else:
            diff.original_text = diff.original_snippet = text
        counterpart_evidence = [
            EvidenceBox(page_no=group[0].page_no, bbox=group[0].layout_bbox or group[0].bbox,
                        method="table_region_unstructured", text=" ".join(block.text for block in group)[:300],
                        highlight_type="MODIFY")
            for group in regions.values()
        ]
        if deleted:
            diff.compare_evidence = counterpart_evidence
        else:
            diff.original_evidence = counterpart_evidence
        for evidence in source_evidence:
            evidence.highlight_type = "MODIFY"
        diff.quality_status = "NEEDS_REVIEW"
        diff.match_score_details = {
            **diff.match_score_details,
            "change_decision": "CANDIDATE",
            "decision_reason": "TABLE_STRUCTURE_UNRESOLVED",
            "original_table_state": "STRUCTURED_TABLE" if deleted else "UNSTRUCTURED_TABLE_PRESENT",
            "compare_table_state": "UNSTRUCTURED_TABLE_PRESENT" if deleted else "STRUCTURED_TABLE",
            "counterpart_region_ids": [key[1] for key in regions],
        }
        if "TABLE_STRUCTURE_UNRESOLVED" not in diff.review_flags:
            diff.review_flags.append("TABLE_STRUCTURE_UNRESOLVED")
        return True

    def _mark_aligned_unmatched_table_candidate(
        self,
        diff: DiffItem,
        page_no: int,
        *,
        deleted: bool,
    ) -> None:
        alignment = self._matcher.page_alignment
        lookup = getattr(alignment, "entry_for_original" if deleted else "entry_for_compare", None)
        if not callable(lookup):
            return
        entry = lookup(page_no)
        evidence_pages = {e.page_no for e in (diff.original_evidence if deleted else diff.compare_evidence)} or {page_no}
        absent_status = "DELETED" if deleted else "INSERTED"
        entries = [lookup(number) for number in evidence_pages]
        if entries and all(item is not None and str(item.status) == absent_status for item in entries):
            diff.match_score_details = {
                **diff.match_score_details,
                "counterpart_table_state": "PAGE_ABSENT",
                "page_alignment": entry.to_debug_payload(),
                "page_alignments": [item.to_debug_payload() for item in entries],
            }
            return
        if entry is None or str(entry.status) not in {"MATCHED", "MERGED", "SPLIT"}:
            return
        counterpart_pages = entry.compare_pages if deleted else entry.original_pages
        if not counterpart_pages:
            return
        diff.quality_status = "NEEDS_REVIEW"
        diff.match_method = "page_sequence_alignment"
        diff.match_score = entry.alignment_score * 100.0
        diff.match_score_details = {
            **diff.match_score_details,
            "change_decision": "CANDIDATE",
            "decision_reason": "ALIGNED_PAGE_STRUCTURE_NOT_EXTRACTED",
            "page_alignment": entry.to_debug_payload(),
        }
        if "ALIGNED_PAGE_STRUCTURE_NOT_EXTRACTED" not in diff.review_flags:
            diff.review_flags.append("ALIGNED_PAGE_STRUCTURE_NOT_EXTRACTED")

    def _is_confident_business_row_pair(
        self,
        original: _LogicalTable,
        compare: _LogicalTable,
        orig_row: int,
        comp_row: int,
    ) -> bool:
        left_key = self._matcher._row_business_key(original, orig_row)
        right_key = self._matcher._row_business_key(compare, comp_row)
        if not left_key or not right_key:
            return False
        if left_key == right_key:
            return True
        return SequenceMatcher(None, left_key, right_key).ratio() >= 0.92

    @staticmethod
    def _is_trusted_business_field_col(original: _LogicalTable, compare: _LogicalTable, col: int) -> bool:
        if col not in {5, 6, 7}:
            return False
        return original.col_count >= 8 and compare.col_count >= 8

    @staticmethod
    def _is_amount_or_quantity_change(original_text: str, compare_text: str, col: int) -> bool:
        original_norm = utils.normalize_cell_for_compare(original_text)
        compare_norm = utils.normalize_cell_for_compare(compare_text)
        if not original_norm or not compare_norm or original_norm == compare_norm:
            return False
        original_numeric = TableComparator._is_business_numeric_value(original_text)
        compare_numeric = TableComparator._is_business_numeric_value(compare_text)
        if not original_numeric or not compare_numeric:
            return False
        if original_norm.startswith(("amount:", "quantity:")) and compare_norm.startswith(("amount:", "quantity:")):
            return True
        if col in {5, 6, 7}:
            original_value = utils.normalize(original_text)
            compare_value = utils.normalize(compare_text)
            if not original_value or not compare_value or original_value == compare_value:
                return False
            return True
        return False

    @staticmethod
    def _is_business_numeric_value(text: str) -> bool:
        value = utils.normalize(text)
        return bool(
            utils.is_number_like(value)
            or utils.canonical_amount(text)
            or utils.canonical_quantity(text)
        )

    @staticmethod
    def _business_field_name(col: int) -> str:
        return {
            5: "数量",
            6: "单价",
            7: "金额",
        }.get(col, "金额/数量")

    def _reconcile_quote_remark_diffs(self, diffs: list[DiffItem]) -> list[DiffItem]:
        remark_indices = [
            index for index, diff in enumerate(diffs)
            if self._is_quote_remark_diff(diff)
        ]
        if not remark_indices:
            return diffs
        if not any(diffs[index].diff_type == "DELETE" for index in remark_indices):
            return diffs
        if not any(diffs[index].diff_type == "ADD" for index in remark_indices):
            return diffs

        original_text = " ".join(diffs[index].original_text for index in remark_indices if diffs[index].original_text)
        compare_text = " ".join(diffs[index].compare_text for index in remark_indices if diffs[index].compare_text)
        if not original_text or not compare_text:
            return diffs

        similarity = self._quote_remark_text_similarity(original_text, compare_text)
        if similarity < 0.72:
            return diffs

        replacement_index = remark_indices[0]
        skipped = set(remark_indices)
        replacement = None
        if not self._quote_remark_text_equivalent(original_text, compare_text):
            replacement = self._make_quote_remark_modify_diff(
                [diffs[index] for index in remark_indices],
                original_text,
                compare_text,
            )

        result: list[DiffItem] = []
        for index, diff in enumerate(diffs):
            if index == replacement_index and replacement is not None:
                result.append(replacement)
            if index not in skipped:
                result.append(diff)
        return result

    @staticmethod
    def _is_quote_remark_diff(diff: DiffItem) -> bool:
        if diff.source_type != "table":
            return False
        text = diff.original_text or diff.compare_text
        if not utils.is_quote_remark_text(text):
            return False
        compact = utils.normalize_quote_remark(text)
        return 10 <= len(compact) <= 500

    @staticmethod
    def _quote_remark_text_similarity(left: str, right: str) -> float:
        left_norm = utils.normalize_quote_remark(left)
        right_norm = utils.normalize_quote_remark(right)
        if not left_norm or not right_norm:
            return 0.0
        if left_norm == right_norm:
            return 1.0
        return SequenceMatcher(None, left_norm, right_norm).ratio()

    @staticmethod
    def _quote_remark_text_equivalent(left: str, right: str) -> bool:
        left_norm = utils.normalize_quote_remark(left)
        right_norm = utils.normalize_quote_remark(right)
        return bool(left_norm and right_norm and left_norm == right_norm)

    @staticmethod
    def _make_quote_remark_modify_diff(diffs: list[DiffItem], original_text: str, compare_text: str) -> DiffItem:
        base = diffs[0]
        review_flags = list(dict.fromkeys(flag for diff in diffs for flag in diff.review_flags))
        original_evidence = [evidence for diff in diffs for evidence in diff.original_evidence]
        compare_evidence = [evidence for diff in diffs for evidence in diff.compare_evidence]
        return base.model_copy(update={
            "diff_type": "MODIFY",
            "original_text": original_text,
            "compare_text": compare_text,
            "original_snippet": original_text[:300],
            "compare_snippet": compare_text[:300],
            "readable_change": "表格备注变更",
            "review_flags": review_flags,
            "original_evidence": original_evidence,
            "compare_evidence": compare_evidence,
        })

    @staticmethod
    def _renumber_diffs(diffs: list[DiffItem], start_index: int) -> list[DiffItem]:
        return [
            diff.model_copy(update={"diff_id": generate_diff_id(start_index + offset)})
            for offset, diff in enumerate(diffs)
        ]

    def _reconcile_quote_remark_cell_diffs(
        self,
        original_table: _LogicalTable,
        compare_table: _LogicalTable,
        cell_diffs: list[CellDiff],
    ) -> list[CellDiff]:
        original_rows = self._quote_remark_rows(original_table)
        compare_rows = self._quote_remark_rows(compare_table)
        if not original_rows or not compare_rows:
            return cell_diffs

        original_text = self._quote_remark_rows_text(original_table, original_rows)
        compare_text = self._quote_remark_rows_text(compare_table, compare_rows)
        if self._quote_remark_text_similarity(original_text, compare_text) < 0.72:
            return cell_diffs

        filtered = [
            diff for diff in cell_diffs
            if diff.original_row not in original_rows and diff.compare_row not in compare_rows
        ]
        if self._quote_remark_text_equivalent(original_text, compare_text):
            return filtered

        filtered.append(CellDiff(
            row=min(min(original_rows), min(compare_rows)),
            col=0,
            original_text=original_text,
            compare_text=compare_text,
            diff_type="MODIFY",
            original_row=min(original_rows),
            compare_row=min(compare_rows),
            original_col=self._first_quote_remark_col(original_table, min(original_rows)),
            compare_col=self._first_quote_remark_col(compare_table, min(compare_rows)),
        ))
        return filtered

    @staticmethod
    def _quote_remark_rows(table: _LogicalTable) -> set[int]:
        rows: set[int] = set()
        for row in table.rows:
            text = " ".join(cell.text for cell in row.cells if cell.text)
            if utils.is_quote_remark_text(text):
                rows.add(row.row_index)
        return rows

    @staticmethod
    def _quote_remark_rows_text(table: _LogicalTable, row_indices: set[int]) -> str:
        parts: list[str] = []
        for row in table.rows:
            if row.row_index not in row_indices:
                continue
            text = " ".join(cell.text for cell in sorted(row.cells, key=lambda cell: cell.col_index) if cell.text)
            if text:
                parts.append(TableComparator._quote_remark_row_text(text))
        return " ".join(parts)

    @staticmethod
    def _quote_remark_row_text(text: str) -> str:
        normalized = utils.normalize(text)
        marker = "备注"
        marker_index = normalized.find(marker)
        if marker_index < 0:
            return text

        compact_seen = 0
        for index, char in enumerate(text):
            if utils.normalize(char):
                compact_seen += 1
            if compact_seen > marker_index:
                return text[index:]
        return text

    @staticmethod
    def _first_quote_remark_col(table: _LogicalTable, row_index: int) -> int:
        for row in table.rows:
            if row.row_index != row_index:
                continue
            for cell in sorted(row.cells, key=lambda item: item.col_index):
                if cell.text and utils.normalize(cell.text):
                    return cell.col_index
        return 0
