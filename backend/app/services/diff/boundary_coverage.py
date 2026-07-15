from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import fitz

from app.models import Clause, DiffItem, Document, EvidenceBox, TextRange
from app.services.diff.text_utils import shorten
from app.services.native_heading_repair import NativeHeadingIndex, load_native_heading_index, normalize_heading_text

STRUCTURAL_RISK_FLAGS = {
    "POSSIBLE_SPLIT_DRIFT",
    "LOW_CONFIDENCE_MATCH",
    "LOW_COVERAGE_CLAUSE_KEY_MATCH",
    "PARAGRAPH_MERGED",
    "POSSIBLE_BOUNDARY_DRIFT",
    "READING_ORDER_REPAIRED",
    "READING_ORDER_RISK",
}

_APPENDIX_HEADING_PATTERN = re.compile(r"^\s*(附件\s*[一二三四五六七八九十百\d]+)\s*[:：.．、]?\s*$")
_APPENDIX_HEADING_LINE_PATTERN = re.compile(r"^\s*(附件\s*[一二三四五六七八九十百\d]+)\s*(?:[:：.．、]|$)")
_COMPACT_PUNCTUATION_PATTERN = re.compile(r"[\s，。；：、”“‘’（）()\[\]【】《》!?:;\"'.,．]+")
_COVERAGE_SEPARATOR_PATTERN = re.compile(r"[-－—_]+")
_FIELD_LABEL_PATTERN = re.compile(
    r"(?P<label>联系人|电话|传真|邮箱|电子邮箱|Email|E-mail|统一社会信用代码)\s*[:：]?",
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\d{3,4}-\d{7,8}|1[3-9]\d{9})(?!\d)")
_CREDIT_CODE_PATTERN = re.compile(r"统一社会信用代码\s*[:：]?\s*(?P<value>[0-9A-ZＸX\s\-－]{15,32})", re.IGNORECASE)
_CONTACT_VALUE_PATTERN = re.compile(r"\s*(?P<value>[^\s，。；;、,]+)")
_CREDIT_CODE_VALUE_PATTERN = re.compile(r"\s*(?P<value>[0-9A-ZＸX\s\-－]{8,32})", re.IGNORECASE)


@dataclass(frozen=True)
class _FieldToken:
    label: str
    kind: str
    value: str
    normalized_value: str
    start: int
    end: int


@dataclass(frozen=True)
class _SigningLabelToken:
    label: str
    start: int
    end: int


@dataclass(frozen=True)
class CoverageFragment:
    kind: str
    text: str
    normalized: str


@dataclass(frozen=True)
class CoverageSequence:
    text: str
    normalized: str


@dataclass(frozen=True)
class _ClauseIndex:
    clauses: tuple[Clause, ...]
    positions: dict[str, int]

    @classmethod
    def from_clauses(cls, clauses: list[Clause]) -> _ClauseIndex:
        sorted_clauses = tuple(sorted(clauses, key=lambda item: (item.order_index, item.clause_id)))
        return cls(
            clauses=sorted_clauses,
            positions={clause.clause_id: index for index, clause in enumerate(sorted_clauses)},
        )

    def window_text(self, clause_id: str | None, radius: int = 2) -> str:
        if not clause_id or clause_id not in self.positions:
            return ""
        position = self.positions[clause_id]
        start = max(0, position - radius)
        end = min(len(self.clauses), position + radius + 1)
        return "\n".join(clause.text for clause in self.clauses[start:end] if clause.text)


@dataclass
class BoundaryCoverageContext:
    original_clauses: list[Clause] = field(default_factory=list)
    compare_clauses: list[Clause] = field(default_factory=list)
    original_document: Document | None = None
    compare_document: Document | None = None
    _original_index: _ClauseIndex | None = field(default=None, init=False, repr=False)
    _compare_index: _ClauseIndex | None = field(default=None, init=False, repr=False)
    _native_indexes: dict[str, NativeHeadingIndex] = field(default_factory=dict, init=False, repr=False)

    @property
    def original_index(self) -> _ClauseIndex:
        if self._original_index is None:
            self._original_index = _ClauseIndex.from_clauses(self.original_clauses)
        return self._original_index

    @property
    def compare_index(self) -> _ClauseIndex:
        if self._compare_index is None:
            self._compare_index = _ClauseIndex.from_clauses(self.compare_clauses)
        return self._compare_index

    def native_heading_index(self, document: Document | None) -> NativeHeadingIndex:
        if document is None or not document.path:
            return NativeHeadingIndex()
        if document.path not in self._native_indexes:
            self._native_indexes[document.path] = load_native_heading_index(document.path)
        return self._native_indexes[document.path]


@dataclass
class BoundaryCoverageDecision:
    action: str
    diff_id: str
    detail: dict[str, Any] = field(default_factory=dict)


class ClauseBoundaryCoverageFilter:
    def filter(
        self,
        diffs: list[DiffItem],
        context: BoundaryCoverageContext,
    ) -> tuple[list[DiffItem], list[BoundaryCoverageDecision]]:
        kept: list[DiffItem] = []
        decisions: list[BoundaryCoverageDecision] = []
        for diff in diffs:
            diff, trim_decision = self._trim_covered_contact_fields(diff, context)
            if trim_decision is not None:
                decisions.append(trim_decision)
            diff, trim_decision = self._trim_covered_signing_form_labels(diff, context)
            if trim_decision is not None:
                decisions.append(trim_decision)
            suppression_reason = self._suppression_reason(diff, context)
            if suppression_reason:
                decisions.append(
                    BoundaryCoverageDecision(
                        action="suppressed_by_neighbor_clause_coverage",
                        diff_id=diff.diff_id,
                        detail={"reason": suppression_reason},
                    )
                )
                continue
            kept.append(diff)
        return kept, decisions

    def _suppression_reason(self, diff: DiffItem, context: BoundaryCoverageContext) -> str:
        if self._short_appendix_heading_covered(diff, context):
            return "short_appendix_heading_covered"
        if self._heading_text_present_on_opposite_page(diff, context):
            return "heading_text_present_on_opposite_page"
        if self._changed_text_covered_by_opposite_page_text(diff, context):
            return "changed_text_covered_by_opposite_page_text"
        if self._heading_add_covered_by_native_heading(diff, context):
            return "heading_add_covered_by_native_heading"
        if self._native_heading_title_only_modify_covered(diff, context):
            return "native_heading_title_only_covered"
        if self._visual_heading_marker_ocr_dropout(diff, context):
            return "visual_heading_marker_ocr_dropout"
        if self._heading_add_covered_by_opposite_numbering(diff, context):
            return "heading_add_covered_by_opposite_numbering"
        if self._heading_layer_mismatch_covered_by_both_pages(diff, context):
            return "heading_layer_mismatch_covered_by_both_pages"
        if self._modify_fragment_covered_by_opposite_page_text(diff, context):
            return "modify_fragment_covered_by_opposite_page_text"
        if self._short_heading_text_covered_by_bare_number(diff, context):
            return "short_heading_text_covered_by_bare_number"
        if self._heading_with_bare_number_and_body_covered(diff, context):
            return "heading_with_bare_number_and_body_covered"
        if self._ocr_stitched_count_heading_body_covered(diff, context):
            return "ocr_stitched_count_heading_body_covered"
        if self._low_coverage_split_fragments_covered_by_opposite_page(diff, context):
            return "low_coverage_split_page_fragment_covered"
        if self._changed_fragments_covered_by_neighbor_clauses(diff, context):
            return "changed_fragments_covered_by_neighbor_clauses"
        return ""

    def _short_appendix_heading_covered(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
        if diff.source_type != "clause" or diff.diff_type not in {"ADD", "DELETE"}:
            return False
        if diff.section_type != "appendix":
            return False

        changed = (
            (diff.original_text or diff.original_snippet)
            if diff.diff_type == "DELETE"
            else (diff.compare_text or diff.compare_snippet)
        )
        heading_key = appendix_heading_key(changed)
        if not heading_key:
            return False

        opposite_document = context.compare_document if diff.diff_type == "DELETE" else context.original_document
        if opposite_document is None:
            return False

        evidence_pages = self._evidence_pages(diff)
        if not evidence_pages:
            return False

        candidate_pages = {page_no + offset for page_no in evidence_pages for offset in (-1, 0, 1)}
        for page in opposite_document.pages:
            if page.page_no not in candidate_pages:
                continue
            page_text = "\n".join(block.text for block in page.blocks)
            if contains_appendix_heading(page_text, heading_key):
                return True
        return False

    @staticmethod
    def _evidence_pages(diff: DiffItem) -> set[int]:
        if diff.diff_type == "DELETE":
            return {evidence.page_no for evidence in diff.original_evidence}
        return {evidence.page_no for evidence in diff.compare_evidence}

    def _heading_text_present_on_opposite_page(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
        if diff.source_type != "clause" or diff.diff_type not in {"ADD", "DELETE"}:
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if not flags.intersection(STRUCTURAL_RISK_FLAGS | _PAGE_TEXT_COVERAGE_REVIEW_FLAGS):
            return False

        changed = (
            (diff.original_snippet or diff.original_text)
            if diff.diff_type == "DELETE"
            else (diff.compare_snippet or diff.compare_text)
        ).strip()
        changed_key = normalize_for_coverage(changed)
        if not _safe_for_heading_number_coverage(changed, changed_key):
            return False
        if _is_subclause_heading_fragment(changed):
            return False
        if not _looks_like_top_level_numbered_heading_line(changed):
            return False
        if not _looks_like_explicit_heading_fragment(changed):
            return False

        evidence_pages = self._evidence_pages(diff)
        if not evidence_pages:
            return False
        opposite_document = context.compare_document if diff.diff_type == "DELETE" else context.original_document
        if opposite_document is None:
            return False

        search_pages = {page_no + offset for page_no in evidence_pages for offset in (-1, 0, 1)}
        for page in opposite_document.pages:
            if page.page_no not in search_pages:
                continue
            if any(normalize_for_coverage(line) == changed_key for line in _page_block_lines(page)):
                return True
        return False

    def _changed_text_covered_by_opposite_page_text(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
        if diff.source_type not in {"clause", "metadata"} or diff.diff_type not in {"ADD", "DELETE"}:
            return False
        if not _eligible_page_text_coverage_diff(diff):
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        direct_evidence_pages = self._evidence_pages(diff)
        new_page_coverage_flags = {
            "LAYOUT_MISMATCH_RISK",
            "PAGE_UNRELIABLE",
            "NON_MAIN_CONTRACT_SECTION",
            "OCR_LOW_CONFIDENCE",
        }
        legacy_page_coverage_flags = (STRUCTURAL_RISK_FLAGS | _PAGE_TEXT_COVERAGE_REVIEW_FLAGS) - new_page_coverage_flags
        if (
            not direct_evidence_pages
            and flags.intersection(new_page_coverage_flags)
            and not flags.intersection(legacy_page_coverage_flags)
        ):
            return False

        opposite_document = context.compare_document if diff.diff_type == "DELETE" else context.original_document
        if opposite_document is None:
            return False

        candidate_pages = self._candidate_pages_for_diff(diff, context)
        if not candidate_pages:
            return False
        search_pages = {page_no + offset for page_no in candidate_pages for offset in (-1, 0, 1)}
        candidates = self._page_text_coverage_candidates(diff)
        if not candidates:
            return False

        for page in opposite_document.pages:
            if page.page_no not in search_pages:
                continue
            page_lines = _page_block_lines(page)
            page_text = "\n".join(page_lines)
            for _changed, changed_key in candidates:
                short_numeric_heading = bool(re.fullmatch(r"\d{1,2}", changed_key))
                if short_numeric_heading:
                    if any(
                        _line_has_numeric_heading_boundary(line, changed_key)
                        and re.search(r"[\u4e00-\u9fff]", line)
                        for line in page_lines
                    ):
                        return True
                    continue
                if _page_text_contains_changed_text(page_text, page_lines, changed_key):
                    return True
        return False

    @staticmethod
    def _page_text_coverage_candidates(diff: DiffItem) -> list[tuple[str, str]]:
        if diff.diff_type == "DELETE":
            raw_candidates = [diff.original_snippet] if (diff.original_snippet or "").strip() else [diff.original_text]
        elif diff.diff_type == "ADD":
            raw_candidates = [diff.compare_snippet] if (diff.compare_snippet or "").strip() else [diff.compare_text]
        else:
            raw_candidates = []

        candidates: list[tuple[str, str]] = []
        seen: set[str] = set()
        for changed in raw_candidates:
            changed = (changed or "").strip()
            changed_key = normalize_for_coverage(changed)
            if not changed_key or changed_key in seen:
                continue
            short_numeric_heading = bool(re.fullmatch(r"\d{1,2}", changed_key))
            if not short_numeric_heading and not _safe_for_page_text_coverage(changed, changed_key):
                continue
            if diff.diff_type == "ADD" and _contains_material_heading_fragment(diff, changed):
                continue
            if diff.diff_type == "DELETE" and _contains_delete_material_heading_fragment(diff, changed):
                continue
            seen.add(changed_key)
            candidates.append((changed, changed_key))
        return candidates

    def _candidate_pages_for_diff(self, diff: DiffItem, context: BoundaryCoverageContext) -> set[int]:
        pages = self._evidence_pages(diff)
        if pages:
            return pages
        clauses = context.original_clauses if diff.diff_type == "DELETE" else context.compare_clauses
        clause_id = diff.original_clause_id if diff.diff_type == "DELETE" else diff.compare_clause_id
        for clause in clauses:
            if clause.clause_id == clause_id:
                return set(clause.page_numbers)
        return set()

    def _modify_fragment_covered_by_opposite_page_text(
        self,
        diff: DiffItem,
        context: BoundaryCoverageContext,
    ) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        if not _eligible_page_text_coverage_diff(diff):
            return False
        has_original = bool((diff.original_snippet or "").strip())
        has_compare = bool((diff.compare_snippet or "").strip())
        if has_original == has_compare:
            return False

        changed = diff.original_snippet if has_original else diff.compare_snippet
        changed_key = normalize_for_coverage(changed)
        if not _safe_for_page_text_coverage(changed, changed_key):
            return False
        evidence = diff.original_evidence if has_original else diff.compare_evidence
        evidence_texts = [item.text for item in evidence if item.text]
        if _contains_material_heading_fragment(
            diff,
            changed,
            _material_heading_guard_evidence_texts(evidence_texts),
        ):
            return False
        opposite_document = context.compare_document if has_original else context.original_document
        if opposite_document is None:
            return False
        pages = self._candidate_pages_for_modify_side(diff, "original" if has_original else "compare")
        if not pages:
            return False

        search_pages = {page_no + offset for page_no in pages for offset in (-1, 0, 1)}
        for page in opposite_document.pages:
            if page.page_no not in search_pages:
                continue
            page_lines = [block.text for block in page.blocks if block.text]
            page_text = "\n".join(page_lines)
            if _page_text_contains_changed_text(page_text, page_lines, changed_key):
                return True
        return False

    def _short_heading_text_covered_by_bare_number(
        self,
        diff: DiffItem,
        context: BoundaryCoverageContext,
    ) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if not flags.intersection({"LOW_CONFIDENCE_MATCH", "POSSIBLE_CLAUSE_MISMATCH", "READING_ORDER_RISK"}):
            return False
        if (diff.original_snippet or "").strip() or not (diff.compare_snippet or "").strip():
            return False

        changed = diff.compare_snippet.strip()
        changed_key = normalize_for_coverage(changed)
        if not _safe_for_heading_number_coverage(changed, changed_key):
            return False
        if _contains_material_heading_fragment(diff, changed):
            return False

        parent_no = self._parent_number_from_short_heading_line(diff.compare_text, changed)
        if not parent_no:
            return False
        pages = self._candidate_pages_for_modify_side(diff, "compare")
        return self._opposite_pages_have_bare_parent_and_child(
            context.original_document,
            pages,
            parent_no,
        )

    def _heading_with_bare_number_and_body_covered(
        self,
        diff: DiffItem,
        context: BoundaryCoverageContext,
    ) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if not flags.intersection(
            {
                "LOW_COVERAGE_MATCH_REVIEW",
                "PARTIAL_CLAUSE_MATCH",
                "POSSIBLE_SPLIT_CLAUSE",
                "TEXT_FOUND_IN_OTHER_CLAUSE",
                "READING_ORDER_RISK",
                "PARAGRAPH_MERGED",
            }
        ):
            return False
        if (diff.original_snippet or "").strip() or not (diff.compare_snippet or "").strip():
            return False
        heading = diff.compare_snippet.strip()
        heading_key = normalize_for_coverage(heading)
        if not _safe_for_heading_number_coverage(heading, heading_key):
            return False
        match = re.match(r"^\s*(\d{1,2})\s*[.．。]\s*(.+)$", heading)
        if not match:
            return False
        parent_no = match.group(1)
        heading_title = match.group(2)
        if _is_material_heading_fragment(diff, heading_title):
            return False
        body_key = normalize_for_coverage(diff.original_text)
        if not body_key or len(body_key) < 8:
            return False
        pages = self._candidate_pages_for_modify_side(diff, "compare")
        if not pages:
            return False
        if not self._opposite_pages_have_bare_parent_number(context.original_document, pages, parent_no):
            return False
        return self._document_pages_contain_all(context.original_document, pages, {body_key})

    def _ocr_stitched_count_heading_body_covered(
        self,
        diff: DiffItem,
        context: BoundaryCoverageContext,
    ) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if not flags.issuperset({"POSSIBLE_OCR_NOISE", "SPATIAL_SUBSTRING_COVERAGE_REPAIRED"}):
            return False
        details = diff.match_score_details or {}
        if details.get("body_length_coverage", 0.0) < 0.70:
            return False
        if (diff.original_snippet or "").strip() or not (diff.compare_snippet or "").strip():
            return False

        changed_key = normalize_for_coverage(diff.compare_snippet)
        match = re.fullmatch(r"\d{1,2}(份数)", changed_key)
        if not match:
            return False
        title = match.group(1)
        parent_no = self._parent_number_from_title_line(diff.compare_text, title)
        if not parent_no:
            return False
        pages = self._candidate_pages_for_modify_side(diff, "compare")
        if not self._opposite_pages_have_bare_parent_number(context.original_document, pages, parent_no):
            return False
        return self._opposite_pages_have_count_body(context.original_document, pages)

    @staticmethod
    def _parent_number_from_short_heading_line(text: str, heading: str) -> str:
        if not text or not heading:
            return ""
        pattern = re.compile(
            rf"(?:^|\n)\s*(\d{{1,2}})\s*[.．。]\s*{re.escape(heading)}\s*(?:\n|$)"
        )
        match = pattern.search(text)
        return match.group(1) if match else ""

    @staticmethod
    def _parent_number_from_title_line(text: str, title: str) -> str:
        if not text or not title:
            return ""
        pattern = re.compile(
            rf"(?:^|\n)\s*(\d{{1,2}})\s*[.．。]\s*{re.escape(title)}\s*(?:\n|$)"
        )
        match = pattern.search(text)
        return match.group(1) if match else ""

    def _heading_layer_mismatch_covered_by_both_pages(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if not flags.intersection({"LOW_CONFIDENCE_MATCH", "POSSIBLE_CLAUSE_MISMATCH", "READING_ORDER_RISK"}):
            return False
        if _contains_protected_value(diff.original_snippet) or _contains_protected_value(diff.compare_snippet):
            return False
        original_key = normalize_for_coverage(diff.original_snippet or diff.original_text)
        compare_key = normalize_for_coverage(diff.compare_snippet or diff.compare_text)
        if not original_key or not compare_key:
            return False
        if len(original_key) > 40 or len(compare_key) > 40:
            return False
        keys = {original_key, compare_key}
        original_pages = self._candidate_pages_for_modify_side(diff, "original")
        compare_pages = self._candidate_pages_for_modify_side(diff, "compare")
        return self._document_pages_contain_all(
            context.original_document, original_pages, keys
        ) and self._document_pages_contain_all(context.compare_document, compare_pages, keys)

    def _heading_add_covered_by_opposite_numbering(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "ADD":
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if not flags.intersection({"READING_ORDER_REPAIRED", "READING_ORDER_RISK", "POSSIBLE_SEGMENTATION_DRIFT"}):
            return False
        changed = diff.compare_text or diff.compare_snippet
        changed_key = normalize_for_coverage(changed)
        if not _safe_for_heading_number_coverage(changed, changed_key):
            return False
        compare_clause = self._clause_by_id(context.compare_clauses, diff.compare_clause_id)
        if compare_clause is None:
            return False
        parent_no = self._heading_parent_number(compare_clause, context.compare_index)
        if not parent_no:
            return False
        is_parent_heading_only = self._is_parent_heading_only_clause(compare_clause, changed, parent_no)
        if not is_parent_heading_only:
            return False
        if _is_material_heading_fragment(diff, changed):
            if _contains_high_risk_heading_term(changed):
                return False
            if "PARAGRAPH_MERGED" not in flags and not (
                self._clauses_have_child_for_parent(context.original_clauses, parent_no)
                and self._clauses_have_child_for_parent(context.compare_clauses, parent_no)
            ):
                return False
        pages = self._candidate_pages_for_diff(diff, context) or set(compare_clause.page_numbers)
        return self._opposite_pages_have_bare_parent_and_child(
            context.original_document,
            pages,
            parent_no,
        )

    def _heading_add_covered_by_native_heading(
        self,
        diff: DiffItem,
        context: BoundaryCoverageContext,
    ) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "ADD":
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if not flags.intersection({"READING_ORDER_REPAIRED", "READING_ORDER_RISK", "POSSIBLE_SEGMENTATION_DRIFT"}):
            return False
        changed = diff.compare_text or diff.compare_snippet
        changed_key = normalize_for_coverage(changed)
        if not _safe_for_heading_number_coverage(changed, changed_key):
            return False
        if not _contains_critical_heading_term(changed):
            return False
        compare_clause = self._clause_by_id(context.compare_clauses, diff.compare_clause_id)
        if compare_clause is None:
            return False
        parent_no = self._heading_parent_number(compare_clause, context.compare_index)
        if not parent_no or not self._native_heading_parent_only_clause(compare_clause, changed, parent_no):
            return False
        if not (
            self._native_heading_has_child_for_parent(context.original_clauses, parent_no)
            and self._native_heading_has_child_for_parent(context.compare_clauses, parent_no)
        ):
            return False
        pages = self._candidate_pages_for_diff(diff, context) or set(compare_clause.page_numbers)
        if not self._native_heading_confirms(
            context.original_document,
            pages,
            parent_no,
            compare_clause.title,
            context,
        ):
            return False
        return self._opposite_pages_have_bare_parent_and_child(
            context.original_document,
            pages,
            parent_no,
        )

    def _native_heading_confirms(
        self,
        document: Document | None,
        pages: set[int],
        number: str,
        title: str,
        context: BoundaryCoverageContext,
    ) -> bool:
        if not pages or not number or not title:
            return False
        candidate_pages = {page + offset for page in pages for offset in (-1, 0, 1) if page + offset > 0}
        return context.native_heading_index(document).contains_exact(number, title, candidate_pages)

    def _native_heading_title_only_modify_covered(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        original_clause = self._clause_by_id(context.original_clauses, diff.original_clause_id)
        compare_clause = self._clause_by_id(context.compare_clauses, diff.compare_clause_id)
        if original_clause is None or compare_clause is None:
            return False
        if not original_clause.clause_no or original_clause.clause_no != compare_clause.clause_no:
            return False
        details = diff.match_score_details or {}
        alignment = details.get("alignment") if isinstance(details.get("alignment"), dict) else {}
        body_similarity = float(alignment.get("body_similarity", details.get("body_similarity", 0.0)) or 0.0)
        if body_similarity < 0.90 or float(details.get("business_token_mismatch", 0.0) or 0.0) > 0.0:
            return False
        changed = (diff.compare_snippet or "").strip()
        title = compare_clause.title or diff.title
        changed_key = normalize_heading_text(changed)
        allowed = {normalize_heading_text(title), normalize_heading_text(f"{compare_clause.clause_no}{title}")}
        if changed_key not in allowed or _contains_protected_value(changed):
            return False
        original_changed = (diff.original_snippet or "").strip()
        if original_changed and not re.fullmatch(r"\d{1,2}\s*[.．、]?", unicodedata.normalize("NFKC", original_changed)):
            return False
        pages = self._candidate_pages_for_modify_side(diff, "original") or set(original_clause.page_numbers)
        return self._native_heading_confirms(context.original_document, pages, compare_clause.clause_no, title, context)

    def _visual_heading_marker_ocr_dropout(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY" or diff.section_type != "appendix":
            return False
        original_changed = unicodedata.normalize("NFKC", diff.original_snippet or "").strip()
        compare_changed = unicodedata.normalize("NFKC", diff.compare_snippet or "").strip()
        marker_pattern = re.compile(r"^[一二三四五六七八九十百千万]+[、.．]$")
        if bool(original_changed) == bool(compare_changed):
            return False
        if original_changed:
            if not marker_pattern.fullmatch(original_changed):
                return False
            present_clause = self._clause_by_id(context.original_clauses, diff.original_clause_id)
            missing_clause = self._clause_by_id(context.compare_clauses, diff.compare_clause_id)
            missing_document = context.compare_document
        else:
            if not marker_pattern.fullmatch(compare_changed):
                return False
            present_clause = self._clause_by_id(context.compare_clauses, diff.compare_clause_id)
            missing_clause = self._clause_by_id(context.original_clauses, diff.original_clause_id)
            missing_document = context.original_document
        if present_clause is None or missing_clause is None or missing_document is None:
            return False
        if not present_clause.clause_no or missing_clause.clause_no:
            return False
        if present_clause.section_type != "appendix" or missing_clause.section_type != "appendix":
            return False
        if normalize_heading_text(present_clause.title) != normalize_heading_text(missing_clause.title):
            return False

        details = diff.match_score_details or {}
        alignment = details.get("alignment") if isinstance(details.get("alignment"), dict) else {}
        title_score = float(details.get("title_score", 0.0) or 0.0)
        body_similarity = float(alignment.get("body_similarity", details.get("body_similarity", 0.0)) or 0.0)
        if title_score < 99.0 or body_similarity < 0.94:
            return False
        if float(details.get("business_token_mismatch", 0.0) or 0.0) > 0.0:
            return False
        if float(details.get("short_clause_pair", 0.0) or 0.0) < 1.0:
            return False
        return self._missing_marker_has_visual_ink(present_clause, missing_clause, missing_document)

    @staticmethod
    def _missing_marker_has_visual_ink(
        present_clause: Clause,
        missing_clause: Clause,
        missing_document: Document,
    ) -> bool:
        if not present_clause.bboxes or not missing_clause.bboxes or not missing_document.path:
            return False
        present_box = present_clause.bboxes[0].bbox
        missing_evidence = missing_clause.bboxes[0]
        missing_box = missing_evidence.bbox
        x_shift = missing_box.x0 - present_box.x0
        width_delta = (present_box.x1 - present_box.x0) - (missing_box.x1 - missing_box.x0)
        if not 8.0 <= x_shift <= 45.0 or width_delta < 8.0 or abs(x_shift - width_delta) > 16.0:
            return False

        crop_width = max(16.0, min(40.0, max(width_delta + 6.0, x_shift + 3.0)))
        try:
            pdf = fitz.open(missing_document.path)
        except Exception:
            return False
        try:
            if not 1 <= missing_evidence.page_no <= len(pdf):
                return False
            page = pdf[missing_evidence.page_no - 1]
            clip = fitz.Rect(
                missing_box.x0 - crop_width,
                missing_box.y0 - 2.0,
                missing_box.x0 - 0.5,
                missing_box.y1 + 2.0,
            ) & page.rect
            if clip.is_empty:
                return False
            pixmap = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=clip, colorspace=fitz.csRGB, alpha=False)
        except Exception:
            return False
        finally:
            pdf.close()

        neutral_dark = 0
        samples = pixmap.samples
        for index in range(0, len(samples) - 2, 3):
            red, green, blue = samples[index : index + 3]
            if max(red, green, blue) < 190 and max(red, green, blue) - min(red, green, blue) <= 45:
                neutral_dark += 1
        pixel_count = max(1, pixmap.width * pixmap.height)
        return neutral_dark >= 12 and neutral_dark / pixel_count >= 0.006

    @staticmethod
    def _native_heading_parent_only_clause(clause: Clause, changed: str, parent_no: str) -> bool:
        clause_no = unicodedata.normalize("NFKC", clause.clause_no or "").strip()
        title_key = normalize_for_coverage(clause.title or "")
        if not title_key:
            return False
        allowed_keys = {title_key}
        if re.fullmatch(r"\d{1,2}", clause_no):
            allowed_keys.add(normalize_for_coverage(f"{clause_no}{clause.title or ''}"))
        if parent_no:
            allowed_keys.add(normalize_for_coverage(f"{parent_no}{clause.title or ''}"))
        allowed_keys.discard(clause_no)
        allowed_keys.discard(parent_no)
        clause_text = (clause.text or "").strip()
        if clause_text:
            return normalize_for_coverage(clause_text) in allowed_keys
        changed_key = normalize_for_coverage(changed)
        return changed_key in allowed_keys

    @staticmethod
    def _native_heading_has_child_for_parent(clauses: list[Clause], parent_no: str) -> bool:
        if not parent_no:
            return False
        child_pattern = re.compile(rf"{re.escape(parent_no)}\.\d+")
        return any(child_pattern.fullmatch((clause.clause_no or "").strip()) for clause in clauses)

    @staticmethod
    def _is_parent_heading_only_clause(clause: Clause, changed: str, parent_no: str) -> bool:
        clause_no = unicodedata.normalize("NFKC", clause.clause_no or "").strip()
        title_key = normalize_for_coverage(clause.title or "")
        if not title_key:
            return False
        allowed_keys = {title_key}
        if re.fullmatch(r"\d{1,2}", clause_no):
            allowed_keys.add(normalize_for_coverage(f"{clause_no}{clause.title or ''}"))
        if parent_no:
            allowed_keys.add(normalize_for_coverage(f"{parent_no}{clause.title or ''}"))
        allowed_keys.discard(clause_no)
        allowed_keys.discard(parent_no)
        text_key = normalize_for_coverage(clause.text or changed)
        changed_key = normalize_for_coverage(changed)
        return text_key in allowed_keys or changed_key in allowed_keys

    @staticmethod
    def _clauses_have_child_for_parent(clauses: list[Clause], parent_no: str) -> bool:
        if not parent_no:
            return False
        child_pattern = re.compile(rf"{re.escape(parent_no)}\.\d+")
        return any(child_pattern.fullmatch((clause.clause_no or "").strip()) for clause in clauses)

    @staticmethod
    def _clause_by_id(clauses: list[Clause], clause_id: str | None) -> Clause | None:
        if not clause_id:
            return None
        return next((clause for clause in clauses if clause.clause_id == clause_id), None)

    @staticmethod
    def _heading_parent_number(clause: Clause, index: _ClauseIndex) -> str:
        direct = re.fullmatch(r"(\d{1,2})", clause.clause_no.strip())
        if direct:
            return direct.group(1)
        position = index.positions.get(clause.clause_id)
        if position is None:
            return ""
        for neighbor in index.clauses[position + 1 : position + 4]:
            child = re.fullmatch(r"(\d{1,2})\.\d+", neighbor.clause_no.strip())
            if child:
                return child.group(1)
        return ""

    @staticmethod
    def _opposite_pages_have_bare_parent_and_child(
        document: Document | None,
        pages: set[int],
        parent_no: str,
    ) -> bool:
        if document is None or not pages:
            return False
        search_pages = {page_no + offset for page_no in pages for offset in (-1, 0, 1)}
        bare_parent = re.compile(rf"(?:^|\n)\s*{re.escape(parent_no)}\s*[.．。]?\s*(?:\n|$)")
        child = re.compile(rf"(?:^|\n)\s*{re.escape(parent_no)}\.\d+\s*")
        for page in document.pages:
            if page.page_no not in search_pages:
                continue
            page_text = "\n".join(block.text for block in page.blocks if block.text)
            if bare_parent.search(page_text) and child.search(page_text):
                return True
        return False

    @staticmethod
    def _opposite_pages_have_bare_parent_number(
        document: Document | None,
        pages: set[int],
        parent_no: str,
    ) -> bool:
        if document is None or not pages:
            return False
        search_pages = {page_no + offset for page_no in pages for offset in (-1, 0, 1)}
        bare_parent = re.compile(rf"(?:^|\n)\s*{re.escape(parent_no)}\s*[.．。]?\s*(?:\n|$)")
        for page in document.pages:
            if page.page_no not in search_pages:
                continue
            page_text = "\n".join(block.text for block in page.blocks if block.text)
            if bare_parent.search(page_text):
                return True
        return False

    @staticmethod
    def _opposite_pages_have_count_body(document: Document | None, pages: set[int]) -> bool:
        if document is None or not pages:
            return False
        search_pages = {page_no + offset for page_no in pages for offset in (-1, 0, 1)}
        for page in document.pages:
            if page.page_no not in search_pages:
                continue
            page_key = normalize_for_coverage("\n".join(block.text for block in page.blocks if block.text))
            if "一式" in page_key and "份" in page_key:
                return True
        return False

    @staticmethod
    def _candidate_pages_for_modify_side(diff: DiffItem, side: str) -> set[int]:
        evidence = diff.original_evidence if side == "original" else diff.compare_evidence
        return {item.page_no for item in evidence}

    @staticmethod
    def _document_pages_contain_all(document: Document | None, pages: set[int], keys: set[str]) -> bool:
        if document is None or not pages:
            return False
        search_pages = {page_no + offset for page_no in pages for offset in (-1, 0, 1)}
        for page in document.pages:
            if page.page_no not in search_pages:
                continue
            page_key = normalize_for_coverage("\n".join(block.text for block in page.blocks if block.text))
            if all(key in page_key for key in keys):
                return True
        return False

    def _low_coverage_split_fragments_covered_by_opposite_page(
        self,
        diff: DiffItem,
        context: BoundaryCoverageContext,
    ) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if not flags.intersection(
            {
                "LOW_COVERAGE_MATCH_REVIEW",
                "PARTIAL_CLAUSE_MATCH",
                "POSSIBLE_SPLIT_CLAUSE",
                "TEXT_FOUND_IN_OTHER_CLAUSE",
            }
        ):
            return False
        details = diff.match_score_details or {}
        if details.get("body_length_coverage", 1.0) >= 0.70 and details.get("split_original_clause", 0.0) < 1:
            return False

        has_original = bool((diff.original_snippet or "").strip())
        has_compare = bool((diff.compare_snippet or "").strip())
        if has_original == has_compare:
            return False
        changed = diff.original_snippet if has_original else diff.compare_snippet
        evidence = diff.original_evidence if has_original else diff.compare_evidence
        evidence_texts = [item.text for item in evidence if item.text]
        if _contains_material_heading_fragment(
            diff,
            changed,
            _material_heading_guard_evidence_texts(evidence_texts),
        ):
            return False
        if _looks_like_multi_field_structural_fragment(normalize_for_coverage(changed)) and not _has_sufficient_structural_evidence_fragments(evidence_texts):
            return False
        fragments = _material_fragment_keys(changed, evidence_texts)
        if not fragments:
            return False

        opposite_document = context.compare_document if has_original else context.original_document
        if opposite_document is None:
            return False
        pages = self._candidate_pages_for_modify_side(diff, "original" if has_original else "compare")
        if not pages:
            return False
        search_pages = {page_no + offset for page_no in pages for offset in (-1, 0, 1)}
        for page in opposite_document.pages:
            if page.page_no not in search_pages:
                continue
            page_lines = [block.text for block in page.blocks if block.text]
            page_text = "\n".join(page_lines)
            if _page_text_contains_all_fragments(page_text, page_lines, fragments):
                return True
        return False

    def _changed_fragments_covered_by_neighbor_clauses(self, diff: DiffItem, context: BoundaryCoverageContext) -> bool:
        if not _eligible_structural_clause_diff(diff):
            return False

        original_window = context.original_index.window_text(diff.original_clause_id)
        compare_window = context.compare_index.window_text(diff.compare_clause_id)
        if not original_window or not compare_window:
            return False

        fragments = _dedupe_fragments(
            [
                *protected_fragments(diff.original_snippet),
                *protected_fragments(diff.compare_snippet),
            ]
        )
        if not fragments:
            return False
        if not _snippets_contain_only_protected_boundary_fields(diff, original_window, compare_window):
            return False

        original_coverage = normalize_for_coverage(original_window)
        compare_coverage = normalize_for_coverage(compare_window)
        if not all(
            fragment.normalized
            and fragment.normalized in original_coverage
            and fragment.normalized in compare_coverage
            for fragment in fragments
        ):
            return False

        required_sequences = _dedupe_sequences(
            [
                *contact_field_sequences(diff.original_snippet),
                *contact_field_sequences(diff.compare_snippet),
            ]
        )
        if not required_sequences:
            if not _bare_phone_fragments_safe(diff):
                return False
            return True

        original_sequences = contact_field_coverage_sequences(original_window)
        compare_sequences = contact_field_coverage_sequences(compare_window)
        return all(
            sequence.normalized in original_sequences and sequence.normalized in compare_sequences
            for sequence in required_sequences
        )

    def _trim_covered_contact_fields(
        self,
        diff: DiffItem,
        context: BoundaryCoverageContext,
    ) -> tuple[DiffItem, BoundaryCoverageDecision | None]:
        if not _eligible_structural_clause_diff(diff):
            return diff, None

        original_window = context.original_index.window_text(diff.original_clause_id)
        compare_window = context.compare_index.window_text(diff.compare_clause_id)
        if not original_window or not compare_window:
            return diff, None

        original_tokens = _covered_contact_tokens(diff.original_snippet, original_window, compare_window)
        compare_tokens = _covered_contact_tokens(diff.compare_snippet, original_window, compare_window)
        if not original_tokens and not compare_tokens:
            return diff, None

        update: dict[str, Any] = {}
        detail: dict[str, Any] = {}
        if original_tokens:
            trimmed_ranges = _remove_ranges_for_tokens(diff.original_text, diff.original_change_ranges, original_tokens)
            trimmed_evidence = _remove_evidence_for_tokens(diff.original_evidence, original_tokens)
            update["original_change_ranges"] = trimmed_ranges
            update["original_evidence"] = trimmed_evidence
            update["original_snippet"] = _rebuild_snippet(diff.original_text, trimmed_ranges)
            detail["removed_original_fields"] = _token_labels(original_tokens)
        if compare_tokens:
            trimmed_ranges = _remove_ranges_for_tokens(diff.compare_text, diff.compare_change_ranges, compare_tokens)
            trimmed_evidence = _remove_evidence_for_tokens(diff.compare_evidence, compare_tokens)
            update["compare_change_ranges"] = trimmed_ranges
            update["compare_evidence"] = trimmed_evidence
            update["compare_snippet"] = _rebuild_snippet(diff.compare_text, trimmed_ranges)
            detail["removed_compare_fields"] = _token_labels(compare_tokens)

        original_ranges = update.get("original_change_ranges", diff.original_change_ranges)
        compare_ranges = update.get("compare_change_ranges", diff.compare_change_ranges)
        update["readable_change"] = _rebuild_readable(diff.original_text, diff.compare_text, original_ranges, compare_ranges)
        return (
            diff.model_copy(update=update),
            BoundaryCoverageDecision(
                action="trimmed_by_neighbor_clause_coverage",
                diff_id=diff.diff_id,
                detail=detail,
            ),
        )

    def _trim_covered_signing_form_labels(
        self,
        diff: DiffItem,
        context: BoundaryCoverageContext,
    ) -> tuple[DiffItem, BoundaryCoverageDecision | None]:
        if not _eligible_structural_clause_diff(diff):
            return diff, None

        original_window = context.original_index.window_text(diff.original_clause_id)
        compare_window = context.compare_index.window_text(diff.compare_clause_id)
        if not original_window or not compare_window:
            return diff, None

        original_tokens = _covered_signing_label_tokens(
            diff.original_text,
            diff.original_change_ranges,
            original_window,
            compare_window,
        )
        compare_tokens = _covered_signing_label_tokens(
            diff.compare_text,
            diff.compare_change_ranges,
            original_window,
            compare_window,
        )
        if not original_tokens and not compare_tokens:
            return diff, None

        update: dict[str, Any] = {}
        detail: dict[str, Any] = {}
        if original_tokens:
            trimmed_ranges = _remove_ranges_for_signing_labels(diff.original_change_ranges, original_tokens)
            trimmed_evidence = _remove_evidence_for_signing_labels(diff.original_evidence, original_tokens)
            update["original_change_ranges"] = trimmed_ranges
            update["original_evidence"] = trimmed_evidence
            update["original_snippet"] = _rebuild_snippet(diff.original_text, trimmed_ranges)
            detail["removed_original_fields"] = _signing_token_labels(original_tokens)
        if compare_tokens:
            trimmed_ranges = _remove_ranges_for_signing_labels(diff.compare_change_ranges, compare_tokens)
            trimmed_evidence = _remove_evidence_for_signing_labels(diff.compare_evidence, compare_tokens)
            update["compare_change_ranges"] = trimmed_ranges
            update["compare_evidence"] = trimmed_evidence
            update["compare_snippet"] = _rebuild_snippet(diff.compare_text, trimmed_ranges)
            detail["removed_compare_fields"] = _signing_token_labels(compare_tokens)

        original_ranges = update.get("original_change_ranges", diff.original_change_ranges)
        compare_ranges = update.get("compare_change_ranges", diff.compare_change_ranges)
        update["readable_change"] = _rebuild_readable(diff.original_text, diff.compare_text, original_ranges, compare_ranges)
        return (
            diff.model_copy(update=update),
            BoundaryCoverageDecision(
                action="trimmed_by_neighbor_clause_coverage",
                diff_id=diff.diff_id,
                detail=detail,
            ),
        )


def compact_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    return _COMPACT_PUNCTUATION_PATTERN.sub("", normalized).lower()


def normalize_for_coverage(text: str) -> str:
    return _COVERAGE_SEPARATOR_PATTERN.sub("", compact_text(text))


def _eligible_page_text_coverage_diff(diff: DiffItem) -> bool:
    flags = set(diff.review_flags) | set(diff.structural_flags)
    return bool(
        flags.intersection(
            STRUCTURAL_RISK_FLAGS | _PAGE_TEXT_COVERAGE_REVIEW_FLAGS
        )
    )


_PAGE_TEXT_COVERAGE_REVIEW_FLAGS = {
    "SHORT_CLAUSE_MATCH_REVIEW",
    "PUNCTUATED_HEADING",
    "TEXT_FOUND_IN_OTHER_CLAUSE",
    "POSSIBLE_SEGMENTATION_DRIFT",
    "POSSIBLE_SPLIT_CLAUSE",
    "POSSIBLE_MERGED_CLAUSE",
    "LAYOUT_MISMATCH_RISK",
    "PAGE_UNRELIABLE",
    "NON_MAIN_CONTRACT_SECTION",
    "OCR_LOW_CONFIDENCE",
}


def _safe_for_page_text_coverage(text: str, changed_key: str) -> bool:
    if not changed_key or len(changed_key) < 3 or len(changed_key) > 120:
        return False
    normalized = unicodedata.normalize("NFKC", text or "")
    if _contains_protected_value(normalized):
        return False
    return True


def _safe_for_heading_number_coverage(text: str, changed_key: str) -> bool:
    if not changed_key or len(changed_key) < 2 or len(changed_key) > 40:
        return False
    normalized = unicodedata.normalize("NFKC", text or "")
    if _contains_protected_value(normalized):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", normalized))


def _contains_protected_value(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text or "")
    return bool(
        re.search(r"(¥|￥|元|万元|亿元|%|％|‰|统一社会信用代码|合同编号)", normalized)
        or re.search(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", normalized)
        or re.search(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", normalized)
    )


def _page_text_contains_changed_text(page_text: str, page_lines: list[str], changed_key: str) -> bool:
    if not changed_key:
        return False
    if len(changed_key) <= 8:
        return any(_short_line_covers_changed_text(line, changed_key) for line in page_lines)
    return changed_key in normalize_for_coverage(page_text)


def _page_block_lines(page: Any) -> list[str]:
    return [line for block in page.blocks if block.text for line in block.text.splitlines() if line]


def _line_has_numeric_heading_boundary(line: str, changed_key: str) -> bool:
    if not re.fullmatch(r"\d{1,2}", changed_key):
        return False
    normalized = unicodedata.normalize("NFKC", line or "")
    return bool(re.match(rf"^\s*{re.escape(changed_key)}\s*[.．。]\s*\D", normalized))


def _contains_critical_heading_term(text: str) -> bool:
    normalized = normalize_for_coverage(text)
    return bool(
        re.search(
            r"(违约|免责|终止|解除|付款|支付|金额|价款|费用|质保|保证金|赔偿|索赔|"
            r"不可抗力|争议|仲裁|诉讼|签署|签字|签章|盖章|授权代表|日期|期限|"
            r"保密|知识产权|验收|交付|管辖|法律适用|税费|发票|责任|义务)",
            normalized,
        )
    )


def _contains_high_risk_heading_term(text: str) -> bool:
    normalized = normalize_for_coverage(text)
    return bool(
        re.search(
            r"(违约|免责|终止|解除|不可抗力|争议|仲裁|诉讼|保密|知识产权|赔偿|索赔|"
            r"管辖|法律适用|保证金|质保)",
            normalized,
        )
    )


def _is_critical_heading_fragment(text: str) -> bool:
    if not _contains_critical_heading_term(text):
        return False
    return _looks_like_heading_fragment(text)


def _is_material_heading_fragment(diff: DiffItem, text: str) -> bool:
    if not _looks_like_heading_fragment(text):
        return False
    if _contains_critical_heading_term(text):
        return True
    return any(flag.startswith("CRITICAL_") for flag in diff.review_flags) and not _is_generic_heading_fragment(text)


def _contains_material_heading_fragment(diff: DiffItem, text: str, evidence_texts: list[str] | None = None) -> bool:
    fragments: list[str] = []
    fragments.extend(evidence_texts or [])
    fragments.extend((text or "").splitlines())
    if "\n" not in (text or ""):
        fragments.append(text or "")
    return any(
        _is_material_heading_fragment(diff, fragment.strip())
        for fragment in fragments
        if fragment.strip() and _looks_like_explicit_heading_fragment(fragment)
    )


def _material_heading_guard_evidence_texts(evidence_texts: list[str]) -> list[str]:
    guarded: list[str] = []
    for text in evidence_texts:
        key = normalize_for_coverage(text)
        if not key:
            continue
        if _is_structural_field_or_ocr_shard(key) and not _contains_critical_heading_term(text):
            continue
        if not _contains_critical_heading_term(text) and not _looks_like_numbered_heading_text(text):
            continue
        guarded.append(text)
    return guarded


def _is_structural_field_or_ocr_shard(key: str) -> bool:
    if key in {"项目名称", "甲方", "乙方", "协议有效期", "合同编号", "甲", "乙", "方"}:
        return True
    return len(key) < 3 and not re.search(r"\d", key)


def _looks_like_numbered_heading_text(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text or "").strip()
    return bool(
        re.match(
            r"^(?:第?[一二三四五六七八九十百\d]+(?:章|节|条)?|"
            r"\d{1,2}(?:\.\d{1,2})*)\s*[.．、:：]\s*\S+",
            normalized,
        )
    )


def _looks_like_top_level_numbered_heading_line(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text or "").strip()
    return bool(
        re.match(
            r"^(?:第?[一二三四五六七八九十百]+(?:章|节|条)?|\d{1,2})\s*(?:[.．、:：]|\s+)\s*\S+",
            normalized,
        )
    )


def _contains_delete_material_heading_fragment(diff: DiffItem, text: str) -> bool:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) >= 2 and any(
        _is_material_heading_fragment(diff, line)
        for line in lines[:2]
        if _looks_like_explicit_heading_fragment(line)
    ):
        return True
    if len(lines) != 1:
        return False
    line = lines[0]
    if _is_subclause_heading_fragment(line):
        return False
    return _is_material_heading_fragment(diff, line) and _looks_like_explicit_heading_fragment(line)


def _is_subclause_heading_fragment(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text or "").strip()
    return bool(re.match(r"^\d{1,2}\.\d{1,2}\s*[.．、:：\s]", normalized))


def _looks_like_explicit_heading_fragment(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text or "").strip()
    numbered = re.match(
        r"^(?:第?[一二三四五六七八九十百\d]+(?:章|节|条)?|\d{1,2}(?:\.\d{1,2})*)\s*[.．、:：]\s*(?P<title>.+)$",
        normalized,
    )
    if numbered:
        return bool(re.search(r"[\u4e00-\u9fffA-Za-z]", numbered.group("title")))
    if re.fullmatch(r"(?:第?[一二三四五六七八九十百\d]+(?:章|节|条)?|\d{1,2}(?:\.\d{1,2})*)\s*[.．、:：]?", normalized):
        return False
    if re.match(r"^(?:第?[一二三四五六七八九十百\d]+(?:章|节|条)?|\d{1,2}(?:\.\d{1,2})*)\s*[.．、:：]", normalized):
        return True
    return len(normalize_for_coverage(normalized)) <= 12 and not re.search(r"[，,。；;:：]", normalized)


def _is_generic_heading_fragment(text: str) -> bool:
    key = normalize_for_coverage(text)
    key = re.sub(r"^\d{1,2}(?:\.\d{1,2})*", "", key)
    return key in {"服务内容", "服务内容概述", "说明", "定义", "其他", "总则"}


def _looks_like_heading_fragment(text: str) -> bool:
    key = normalize_for_coverage(text)
    if len(key) <= 16:
        return True
    normalized = unicodedata.normalize("NFKC", text or "")
    return bool(
        re.fullmatch(
            r"\s*(?:第?[一二三四五六七八九十百\d]+(?:章|节|条)?|"
            r"\d{1,2}(?:\.\d{1,2})*)\s*[.．、:：]?\s*[^\n。；;，,]{1,20}\s*",
            normalized,
        )
    )


def _material_fragment_keys(text: str, evidence_texts: list[str] | None = None) -> list[str]:
    raw_fragments: list[tuple[str, bool]] = []
    raw_fragments.extend((fragment, True) for fragment in evidence_texts or [])
    raw_fragments.extend((fragment, False) for fragment in re.split(r"[\n。；;，,]+", text or ""))
    fragments: list[str] = []
    seen: set[str] = set()
    for fragment, from_evidence in raw_fragments:
        key = normalize_for_coverage(fragment)
        if not key or key in seen:
            continue
        if not from_evidence and _looks_like_unstable_quoted_fragment(fragment, key):
            continue
        if not from_evidence and _looks_like_multi_field_structural_fragment(key):
            continue
        if not from_evidence and len(key) > 120:
            continue
        if len(key) < 3 and not re.search(r"\d", key):
            continue
        if re.fullmatch(r"\d{1,3}", key):
            continue
        seen.add(key)
        fragments.append(key)
    return fragments


def _looks_like_unstable_quoted_fragment(fragment: str, key: str) -> bool:
    if len(key) > 8:
        return False
    normalized = unicodedata.normalize("NFKC", fragment or "")
    quote_count = sum(normalized.count(ch) for ch in "“”\"'‘’")
    return quote_count >= 2


def _looks_like_multi_field_structural_fragment(key: str) -> bool:
    if len(key) <= 60:
        return False
    field_hits = sum(
        1
        for token in ("项目名称", "甲方", "乙方", "协议有效期", "合同编号")
        if token in key
    )
    return field_hits >= 2


def _has_sufficient_structural_evidence_fragments(evidence_texts: list[str]) -> bool:
    material_keys = {
        normalize_for_coverage(text)
        for text in evidence_texts
        if text and not _is_structural_field_or_ocr_shard(normalize_for_coverage(text))
    }
    material_keys = {
        key
        for key in material_keys
        if len(key) >= 4 and not re.fullmatch(r"\d{1,4}", key)
    }
    return len(material_keys) >= 4


def _page_text_contains_all_fragments(page_text: str, page_lines: list[str], fragments: list[str]) -> bool:
    if not fragments:
        return False
    page_key = normalize_for_coverage(page_text)
    return all(
        fragment in page_key or _page_text_contains_changed_text(page_text, page_lines, fragment)
        for fragment in fragments
    )


def _short_line_covers_changed_text(line: str, changed_key: str) -> bool:
    line_key = normalize_for_coverage(line)
    if not line_key:
        return False
    if line_key == changed_key:
        return True
    if re.match(r"\d", changed_key):
        return False
    if _contains_critical_heading_term(changed_key):
        return False
    return bool(re.fullmatch(r"(?:第)?[一二三四五六七八九十百\d]+(?:章|节|条)?\.?" + re.escape(changed_key), line_key))


def protected_fragments(text: str) -> list[CoverageFragment]:
    fragments: list[CoverageFragment] = []
    fragments.extend(
        CoverageFragment("contact_field", value, normalize_for_coverage(value)) for value in contact_field_values(text)
    )
    fragments.extend(CoverageFragment("email", value, normalize_email(value)) for value in emails(text))
    fragments.extend(CoverageFragment("phone", value, normalize_phone(value)) for value in phones(text))
    fragments.extend(
        CoverageFragment("credit_code", value, normalize_credit_code(value)) for value in credit_code_candidates(text)
    )
    return [fragment for fragment in fragments if fragment.normalized]


def contact_field_values(text: str) -> list[str]:
    return [token.value for token in _field_tokens(text) if token.kind in {"contact", "phone", "fax"}]


def contact_field_sequences(text: str) -> list[CoverageSequence]:
    tokens = [token for token in _field_tokens(text) if token.kind in {"contact", "phone", "fax"}]
    if not tokens:
        return []
    parts = [f"{token.label}:{token.value}" for token in tokens]
    sequence_text = "".join(parts)
    return [CoverageSequence(sequence_text, normalize_for_coverage(sequence_text))]


def contact_field_coverage_sequences(text: str) -> set[str]:
    tokens = [token for token in _field_tokens(text) if token.kind in {"contact", "phone", "fax"}]
    sequences = {sequence.normalized for sequence in _contiguous_contact_field_sequences(tokens)}
    sequences.update(_inferred_contact_field_sequences(tokens))
    return {sequence for sequence in sequences if sequence}


def _covered_contact_tokens(
    snippet: str,
    original_window: str,
    compare_window: str,
) -> list[_FieldToken]:
    tokens = [token for token in _field_tokens(snippet) if token.kind in {"contact", "phone", "fax"}]
    if not tokens:
        return []

    original_sequences = contact_field_coverage_sequences(original_window)
    compare_sequences = contact_field_coverage_sequences(compare_window)
    covered_indexes: set[int] = set()
    for sequence_tokens in _contact_token_groups(tokens):
        sequence = _sequence_from_tokens(sequence_tokens)
        if sequence.normalized in original_sequences and sequence.normalized in compare_sequences:
            covered_indexes.update(id(token) for token in sequence_tokens)
    return [token for token in tokens if id(token) in covered_indexes]


def _contact_token_groups(tokens: list[_FieldToken]) -> list[list[_FieldToken]]:
    groups: list[list[_FieldToken]] = [list(group) for group in _contiguous_contact_token_groups(tokens)]
    groups.extend(_columnar_contact_groups(tokens))
    return groups


def _contiguous_contact_token_groups(tokens: list[_FieldToken]) -> list[list[_FieldToken]]:
    groups: list[list[_FieldToken]] = []
    current: list[_FieldToken] = []
    for token in tokens:
        if token.kind == "contact":
            if current:
                groups.append(current)
            current = [token]
            continue
        if current:
            current.append(token)
        else:
            groups.append([token])
    if current:
        groups.append(current)
    return groups


def _remove_ranges_for_tokens(text: str, ranges: list[TextRange], tokens: list[_FieldToken]) -> list[TextRange]:
    if not ranges or not tokens:
        return list(ranges)
    return [
        range_
        for range_ in ranges
        if not any(_range_matches_token(text, range_, token) for token in tokens)
    ]


def _range_matches_token(text: str, range_: TextRange, token: _FieldToken) -> bool:
    fragment = normalize_for_coverage(text[range_.start : range_.end])
    token_text = normalize_for_coverage(f"{token.label}:{token.value}")
    token_value = token.normalized_value
    return bool(
        fragment
        and (
            fragment == token_text
            or fragment == token_value
            or (len(fragment) >= 3 and fragment in token_text)
            or (len(token_text) >= 3 and token_text in fragment)
            or (len(token_value) >= 3 and token_value in fragment)
        )
    )


def _remove_evidence_for_tokens(evidence: list[EvidenceBox], tokens: list[_FieldToken]) -> list[EvidenceBox]:
    if not evidence or not tokens:
        return list(evidence)
    return [item for item in evidence if not any(_evidence_matches_token(item, token) for token in tokens)]


def _evidence_matches_token(evidence: EvidenceBox, token: _FieldToken) -> bool:
    evidence_text = normalize_for_coverage(evidence.text)
    if not evidence_text:
        return False
    token_label = normalize_for_coverage(token.label)
    token_text = normalize_for_coverage(f"{token.label}:{token.value}")
    token_value = token.normalized_value
    return evidence_text in {token_label, token_value, token_text} or evidence_text in token_text


def _token_labels(tokens: list[_FieldToken]) -> list[str]:
    return [f"{token.label}:{token.value}" for token in tokens]


def _covered_signing_label_tokens(
    text: str,
    ranges: list[TextRange],
    original_window: str,
    compare_window: str,
) -> list[_SigningLabelToken]:
    tokens: list[_SigningLabelToken] = []
    seen: set[tuple[str, int, int]] = set()
    for range_ in ranges:
        label = _signing_form_label_key(text[range_.start : range_.end])
        if not label:
            continue
        if not _signing_form_label_present(label, original_window):
            continue
        if not _signing_form_label_present(label, compare_window):
            continue
        key = (label, range_.start, range_.end)
        if key in seen:
            continue
        seen.add(key)
        tokens.append(_SigningLabelToken(label=label, start=range_.start, end=range_.end))
    return tokens


def _remove_ranges_for_signing_labels(ranges: list[TextRange], tokens: list[_SigningLabelToken]) -> list[TextRange]:
    if not ranges or not tokens:
        return list(ranges)
    token_spans = {(token.start, token.end) for token in tokens}
    return [range_ for range_ in ranges if (range_.start, range_.end) not in token_spans]


def _remove_evidence_for_signing_labels(evidence: list[EvidenceBox], tokens: list[_SigningLabelToken]) -> list[EvidenceBox]:
    if not evidence or not tokens:
        return list(evidence)
    labels = {token.label for token in tokens}
    return [item for item in evidence if _signing_form_label_key(item.text) not in labels]


def _signing_token_labels(tokens: list[_SigningLabelToken]) -> list[str]:
    labels: list[str] = []
    for token in tokens:
        if token.label not in labels:
            labels.append(token.label)
    return labels


def _signing_form_label_key(text: str) -> str:
    compact = normalize_for_coverage(text)
    if not compact:
        return ""
    if re.search(r"\d|[A-Za-z]", compact):
        return ""
    if "盖章" in compact and len(compact) <= 6:
        return "盖章"
    for label, aliases in _signing_form_label_aliases().items():
        if compact in aliases:
            return label
    return ""


def _signing_form_label_present(label: str, text: str) -> bool:
    compact = normalize_for_coverage(text)
    if not compact:
        return False
    if label == "授权代表签字":
        return bool(re.search(r"授权代表签字|授权代表签(?!署)|代表签字", compact))
    return any(alias in compact for alias in _signing_form_presence_aliases().get(label, (label,)))


def _signing_form_label_aliases() -> dict[str, tuple[str, ...]]:
    return {
        "盖章": ("盖章",),
        "授权代表签字": ("授权代表签字", "授权代表签", "授权代表", "代表签字", "签字"),
        "纳税人识别号": ("纳税人识别号", "纳税人识别", "识别号"),
        "地址": ("地址",),
        "电话": ("电话",),
        "开户行": ("开户行",),
        "账号": ("账号",),
        "银行行号": ("银行行号",),
        "日期": ("日期",),
    }


def _signing_form_presence_aliases() -> dict[str, tuple[str, ...]]:
    aliases = _signing_form_label_aliases()
    return {
        **aliases,
        "授权代表签字": ("授权代表签字", "授权代表签", "代表签字"),
    }


def _rebuild_snippet(text: str, ranges: list[TextRange]) -> str:
    if not ranges:
        return ""
    return shorten("".join(text[range_.start : range_.end] for range_ in ranges))


def _rebuild_readable(
    original_text: str,
    compare_text: str,
    original_ranges: list[TextRange],
    compare_ranges: list[TextRange],
) -> str:
    original_part = _rebuild_snippet(original_text, original_ranges)
    compare_part = _rebuild_snippet(compare_text, compare_ranges)
    return f"原文：{original_part}\n修改后：{compare_part}"


def emails(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text or "")
    return [match.group(0) for match in _EMAIL_PATTERN.finditer(normalized)]


def phones(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text or "")
    return [match.group(0) for match in _PHONE_PATTERN.finditer(normalized)]


def _bare_phone_fragments_safe(diff: DiffItem) -> bool:
    original_phones = [normalize_phone(value) for value in phones(diff.original_snippet)]
    compare_phones = [normalize_phone(value) for value in phones(diff.compare_snippet)]
    if not original_phones and not compare_phones:
        return True
    return set(original_phones) == set(compare_phones)


def credit_code_candidates(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text or "")
    candidates: list[str] = []
    for match in _CREDIT_CODE_PATTERN.finditer(normalized):
        candidate = normalize_credit_code(match.group("value"))
        if len(candidate) == 18:
            candidates.append(candidate)
    return candidates


def normalize_email(text: str) -> str:
    return normalize_for_coverage(text)


def normalize_phone(text: str) -> str:
    return normalize_for_coverage(text)


def normalize_credit_code(text: str) -> str:
    return normalize_for_coverage(text).upper()


def _field_tokens(text: str) -> list[_FieldToken]:
    normalized = unicodedata.normalize("NFKC", text or "")
    labels = list(_FIELD_LABEL_PATTERN.finditer(normalized))
    tokens: list[_FieldToken] = []
    for index, label_match in enumerate(labels):
        label = label_match.group("label")
        kind = _field_kind(label)
        next_label_start = labels[index + 1].start() if index + 1 < len(labels) else len(normalized)
        value_region = normalized[label_match.end() : next_label_start]
        value_match = _field_value_match(kind, value_region)
        if value_match is None:
            continue
        value_group: str | int = "value" if "value" in value_match.re.groupindex else 0
        value = value_match.group(value_group).strip()
        if not value:
            continue
        value_end = label_match.end() + value_match.end(value_group)
        normalized_value = _normalize_field_value(kind, value)
        tokens.append(
            _FieldToken(
                label=label,
                kind=kind,
                value=value,
                normalized_value=normalized_value,
                start=label_match.start(),
                end=value_end,
            )
        )
    return tokens


def _field_kind(label: str) -> str:
    normalized = unicodedata.normalize("NFKC", label).lower()
    if normalized == "联系人":
        return "contact"
    if normalized == "电话":
        return "phone"
    if normalized == "传真":
        return "fax"
    if normalized in {"邮箱", "电子邮箱", "email", "e-mail"}:
        return "email"
    return "credit_code"


def _field_value_match(kind: str, value_region: str) -> re.Match[str] | None:
    if kind == "contact":
        return _CONTACT_VALUE_PATTERN.match(value_region)
    if kind in {"phone", "fax"}:
        return _PHONE_PATTERN.search(value_region)
    if kind == "email":
        return _EMAIL_PATTERN.search(value_region)
    return _CREDIT_CODE_VALUE_PATTERN.match(value_region)


def _normalize_field_value(kind: str, value: str) -> str:
    if kind == "email":
        return normalize_email(value)
    if kind in {"phone", "fax"}:
        return normalize_phone(value)
    if kind == "credit_code":
        return normalize_credit_code(value)
    return normalize_for_coverage(value)


def _contiguous_contact_field_sequences(tokens: list[_FieldToken]) -> list[CoverageSequence]:
    sequences: list[CoverageSequence] = []
    if not tokens:
        return sequences

    current: list[_FieldToken] = []
    for token in tokens:
        if token.kind == "contact":
            if current:
                sequences.append(_sequence_from_tokens(current))
            current = [token]
            continue
        if current:
            current.append(token)
        else:
            sequences.append(_sequence_from_tokens([token]))
    if current:
        sequences.append(_sequence_from_tokens(current))
    return sequences


def _inferred_contact_field_sequences(tokens: list[_FieldToken]) -> set[str]:
    return {
        _sequence_from_tokens(group).normalized
        for group in _columnar_contact_groups(tokens)
        if len(group) > 1
    }


def _sequence_from_tokens(tokens: list[_FieldToken]) -> CoverageSequence:
    text = "".join(f"{token.label}:{token.value}" for token in tokens)
    return CoverageSequence(text, normalize_for_coverage(text))


def _columnar_contact_groups(tokens: list[_FieldToken]) -> list[list[_FieldToken]]:
    groups: list[list[_FieldToken]] = []
    index = 0
    while index < len(tokens):
        if tokens[index].kind != "contact":
            index += 1
            continue

        contact_start = index
        while index < len(tokens) and tokens[index].kind == "contact":
            index += 1
        contacts = tokens[contact_start:index]
        following = tokens[index:]
        phones = [token for token in following if token.kind == "phone"]
        faxes = [token for token in following if token.kind == "fax"]
        for offset, contact in enumerate(contacts):
            group = [contact]
            if offset < len(phones):
                group.append(phones[offset])
            if offset < len(faxes):
                group.append(faxes[offset])
            groups.append(group)
    return groups


def _snippets_contain_only_protected_boundary_fields(
    diff: DiffItem,
    original_window: str,
    compare_window: str,
) -> bool:
    return _credit_code_tokens_safe(
        diff.original_snippet,
        diff.compare_snippet,
        original_window,
        compare_window,
    ) and (
        _contains_only_protected_boundary_fields(diff.original_snippet)
        and _contains_only_protected_boundary_fields(diff.compare_snippet)
    )


def _contains_only_protected_boundary_fields(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text or "")
    if not normalized:
        return True

    mask = [False] * len(normalized)
    for token in _field_tokens(normalized):
        _mark_span(mask, token.start, token.end)
    for pattern in (_EMAIL_PATTERN, _PHONE_PATTERN):
        for match in pattern.finditer(normalized):
            _mark_span(mask, match.start(), match.end())

    residual = "".join(" " if masked else char for char, masked in zip(normalized, mask, strict=True))
    return not compact_text(residual)


def _mark_span(mask: list[bool], start: int, end: int) -> None:
    for index in range(max(0, start), min(len(mask), end)):
        mask[index] = True


def _credit_code_tokens_safe(
    original_snippet: str,
    compare_snippet: str,
    original_window: str,
    compare_window: str,
) -> bool:
    original_values = _credit_code_token_values(original_snippet)
    compare_values = _credit_code_token_values(compare_snippet)
    all_values = [*original_values, *compare_values]
    if not any(len(value) != 18 for value in all_values):
        return True
    if original_values and original_values == compare_values:
        return True

    original_window_values = _credit_code_token_values(original_window)
    compare_window_values = _credit_code_token_values(compare_window)
    if not original_window_values or not compare_window_values:
        return False
    return all(
        _prefix_compatible_with_any(value, original_window_values)
        and _prefix_compatible_with_any(value, compare_window_values)
        for value in all_values
    )


def _credit_code_token_values(text: str) -> list[str]:
    return [token.normalized_value for token in _field_tokens(text) if token.kind == "credit_code"]


def _prefix_compatible_with_any(value: str, candidates: list[str]) -> bool:
    return any(candidate.startswith(value) or value.startswith(candidate) for candidate in candidates)


def _eligible_structural_clause_diff(diff: DiffItem) -> bool:
    if diff.source_type != "clause":
        return False
    risk_flags = set(diff.structural_flags) | set(diff.review_flags)
    return bool(risk_flags.intersection(STRUCTURAL_RISK_FLAGS))


def _dedupe_fragments(fragments: list[CoverageFragment]) -> list[CoverageFragment]:
    deduped: list[CoverageFragment] = []
    seen: set[tuple[str, str]] = set()
    for fragment in fragments:
        key = (fragment.kind, fragment.normalized)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(fragment)
    return deduped


def _dedupe_sequences(sequences: list[CoverageSequence]) -> list[CoverageSequence]:
    deduped: list[CoverageSequence] = []
    seen: set[str] = set()
    for sequence in sequences:
        if not sequence.normalized or sequence.normalized in seen:
            continue
        seen.add(sequence.normalized)
        deduped.append(sequence)
    return deduped


def appendix_heading_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    if len(compact_text(normalized)) > 8:
        return ""
    match = _APPENDIX_HEADING_PATTERN.fullmatch(normalized)
    if not match:
        return ""
    return compact_text(match.group(1))


def contains_appendix_heading(text: str, heading_key: str) -> bool:
    if not heading_key:
        return False
    normalized = unicodedata.normalize("NFKC", text or "").replace("\f", "\n")
    for line in normalized.splitlines():
        match = _APPENDIX_HEADING_LINE_PATTERN.match(line)
        if match and compact_text(match.group(1)) == heading_key:
            return True
    return False
