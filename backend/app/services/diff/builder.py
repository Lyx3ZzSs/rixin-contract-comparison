from __future__ import annotations

from app.models import ClausePair, DiffItem, TextRange
from app.utils.id_utils import generate_diff_id

from app.services.diff.cross_column_prefix import repair_cross_column_prefix_fragments
from app.services.diff.critical_field_guard import (
    critical_field_diff_types,
    critical_field_review_flags,
)
from app.services.diff.range_refiner import changed_snippets
from app.services.diff.spatial_line_pairing import repair_spatial_line_pairing
from app.services.diff.spatial_repair import rebuild_change_text, repair_spatial_duplicate_ranges
from app.services.diff.spatial_substring_coverage import repair_spatial_substring_coverage
from app.services.diff.spatial_value_coverage import repair_spatial_value_coverage
from app.services.diff.text_utils import shorten

LOW_CONFIDENCE_MATCH_THRESHOLD = 75.0
CRITICAL_FIELD_CONTEXT_CHARS = set("0123456789,.-/%‰¥￥人民币年月日天个工作万元亿元")


def build_diffs(pairs: list[ClausePair], start_index: int = 1) -> list[DiffItem]:
    diffs: list[DiffItem] = []
    next_index = start_index
    for pair in pairs:
        if pair.original is None and pair.compare is not None:
            diffs.append(build_add(pair, next_index))
            next_index += 1
        elif pair.compare is None and pair.original is not None:
            diffs.append(build_delete(pair, next_index))
            next_index += 1
        elif pair.original is not None and pair.compare is not None:
            if pair.original.normalized_text == pair.compare.normalized_text:
                continue
            diff = build_modify(pair, next_index)
            if diff is not None:
                diffs.append(diff)
                next_index += 1
    return diffs


def build_add(pair: ClausePair, index: int) -> DiffItem:
    clause = pair.compare
    assert clause is not None
    return DiffItem(
        diff_id=generate_diff_id(index),
        diff_type="ADD",
        compare_clause_id=clause.clause_id,
        clause_no=clause.clause_no,
        title=clause.title,
        compare_text=clause.text,
        compare_snippet=shorten(clause.text),
        readable_change=f"新增条款：{shorten(clause.text)}",
        source_type="clause",
        section_type=clause.section_type,
        section_path=clause.section_path,
        match_method=pair.match_method,
        match_score=pair.score or None,
        match_score_details=pair.score_details,
        match_candidates=pair.match_candidates,
        match_confidence=pair.match_confidence,
        structural_flags=clause.split_flags,
        review_flags=list(dict.fromkeys([*structural_review_flags(clause), *unmatched_review_flags(pair)])),
        compare_evidence=clause.bboxes,
        compare_change_ranges=[TextRange(start=0, end=len(clause.text), highlight_type="ADD")],
    )


def build_delete(pair: ClausePair, index: int) -> DiffItem:
    clause = pair.original
    assert clause is not None
    return DiffItem(
        diff_id=generate_diff_id(index),
        diff_type="DELETE",
        original_clause_id=clause.clause_id,
        clause_no=clause.clause_no,
        title=clause.title,
        original_text=clause.text,
        original_snippet=shorten(clause.text),
        readable_change=f"删除条款：{shorten(clause.text)}",
        source_type="clause",
        section_type=clause.section_type,
        section_path=clause.section_path,
        match_method=pair.match_method,
        match_score=pair.score or None,
        match_score_details=pair.score_details,
        match_candidates=pair.match_candidates,
        match_confidence=pair.match_confidence,
        structural_flags=clause.split_flags,
        review_flags=list(dict.fromkeys([*structural_review_flags(clause), *unmatched_review_flags(pair)])),
        original_evidence=clause.bboxes,
        original_change_ranges=[TextRange(start=0, end=len(clause.text), highlight_type="DELETE")],
    )


def build_modify(pair: ClausePair, index: int) -> DiffItem | None:
    left = pair.original
    right = pair.compare
    assert left is not None and right is not None
    original_snippet, compare_snippet, original_ranges, compare_ranges = changed_snippets(left.text, right.text)
    original_ranges, compare_ranges, line_pairing_reasons = repair_spatial_line_pairing(
        left,
        right,
        original_ranges,
        compare_ranges,
    )
    original_ranges, compare_ranges, cross_column_reasons = repair_cross_column_prefix_fragments(
        left,
        right,
        original_ranges,
        compare_ranges,
    )
    original_ranges, compare_ranges, substring_coverage_reasons = repair_spatial_substring_coverage(
        left,
        right,
        original_ranges,
        compare_ranges,
    )
    original_ranges, compare_ranges, repair_reasons = repair_spatial_duplicate_ranges(
        left,
        right,
        original_ranges,
        compare_ranges,
    )
    original_ranges, compare_ranges, value_coverage_reasons = repair_spatial_value_coverage(
        left,
        right,
        original_ranges,
        compare_ranges,
    )
    if not original_ranges and not compare_ranges:
        return None
    if line_pairing_reasons or cross_column_reasons or substring_coverage_reasons or repair_reasons or value_coverage_reasons:
        original_snippet, compare_snippet, readable_change = rebuild_change_text(
            left.text,
            right.text,
            original_ranges,
            compare_ranges,
        )
    else:
        readable_change = f"原文：{original_snippet}\n修改后：{compare_snippet}"
    flags = review_flags(pair)
    score_details = dict(pair.score_details)
    original_field_snippet = critical_field_guard_snippet(left.text, original_ranges, original_snippet)
    compare_field_snippet = critical_field_guard_snippet(right.text, compare_ranges, compare_snippet)
    field_types = critical_field_diff_types(
        left.text,
        right.text,
        original_field_snippet,
        compare_field_snippet,
    )
    if field_types:
        flags.extend(critical_field_review_flags(field_types))
        score_details["critical_field_diff_types"] = field_types
        score_details["critical_field_guard_applied"] = 1.0
    if line_pairing_reasons:
        flags.append("SPATIAL_LINE_PAIRING_REPAIRED")
    if cross_column_reasons:
        flags.append("SPATIAL_CROSS_COLUMN_PREFIX_REPAIRED")
    if substring_coverage_reasons:
        flags.append("SPATIAL_SUBSTRING_COVERAGE_REPAIRED")
    if repair_reasons:
        flags.append("SPATIAL_DUPLICATE_TOKEN_REPAIRED")
    if value_coverage_reasons:
        flags.append("SPATIAL_VALUE_COVERAGE_REPAIRED")
    return DiffItem(
        diff_id=generate_diff_id(index),
        diff_type="MODIFY",
        original_clause_id=left.clause_id,
        compare_clause_id=right.clause_id,
        clause_no=left.clause_no or right.clause_no,
        title=right.title or left.title,
        original_text=left.text,
        compare_text=right.text,
        original_snippet=original_snippet,
        compare_snippet=compare_snippet,
        readable_change=readable_change,
        source_type="clause",
        section_type=right.section_type or left.section_type,
        section_path=right.section_path or left.section_path,
        match_score=pair.score,
        match_method=pair.match_method,
        match_score_details=score_details,
        match_candidates=pair.match_candidates,
        match_confidence=pair.match_confidence,
        structural_flags=list(dict.fromkeys([*left.split_flags, *right.split_flags])),
        review_flags=flags,
        original_evidence=left.bboxes,
        compare_evidence=right.bboxes,
        original_change_ranges=original_ranges,
        compare_change_ranges=compare_ranges,
    )


def critical_field_guard_snippet(text: str, ranges: list[TextRange], fallback: str) -> str:
    if not ranges:
        return fallback
    start = min(item.start for item in ranges)
    end = max(item.end for item in ranges)
    while start > 0 and text[start - 1] in CRITICAL_FIELD_CONTEXT_CHARS:
        start -= 1
    while end < len(text) and text[end] in CRITICAL_FIELD_CONTEXT_CHARS:
        end += 1
    snippet = text[start:end]
    return snippet or fallback


def review_flags(pair: ClausePair) -> list[str]:
    flags: list[str] = []
    body_score = pair.score_details.get("body_score", pair.score)
    if pair.match_method == "same_clause_no_low_similarity":
        flags.append("SAME_CLAUSE_NO_LOW_SIMILARITY")
    if pair.match_method == "renumbered_similarity":
        flags.append("POSSIBLE_RENUMBERED_CLAUSE")
    if pair.match_method in {"contained_compare", "merged_compare"}:
        flags.extend(
            [
                "TEXT_FOUND_IN_OTHER_CLAUSE",
                "POSSIBLE_SEGMENTATION_DRIFT",
                "POSSIBLE_MERGED_CLAUSE",
                "PARTIAL_CLAUSE_MATCH",
            ]
        )
    if pair.match_method in {"contained_original", "split_original"}:
        flags.extend(
            [
                "TEXT_FOUND_IN_OTHER_CLAUSE",
                "POSSIBLE_SEGMENTATION_DRIFT",
                "POSSIBLE_SPLIT_CLAUSE",
                "PARTIAL_CLAUSE_MATCH",
            ]
        )
    if pair.score_details.get("partial_clause_match", 0.0) >= 1:
        flags.append("PARTIAL_CLAUSE_MATCH")
    if pair.match_confidence == "LOW":
        flags.append("LOW_CONFIDENCE_MATCH")
    if pair.match_method == "same_clause_key_weighted" and pair.score_details.get("body_length_coverage", 1.0) < 0.70:
        flags.append("LOW_COVERAGE_CLAUSE_KEY_MATCH")
    if pair.score_details.get("body_length_coverage", 1.0) < 0.50 and pair.match_method != "contained_compare":
        flags.append("LOW_COVERAGE_MATCH_REVIEW")
    if pair.score_details.get("short_clause_pair", 0.0) >= 1:
        flags.append("SHORT_CLAUSE_MATCH_REVIEW")
    if pair.score_details.get("section_mismatch_candidate", 0.0) >= 1:
        flags.append("POSSIBLE_SECTION_MISCLASSIFICATION")
    if "SECTION_PATH_MISMATCH_REVIEW" in pair.score_details.get("matcher_risk_flags", []):
        flags.append("SECTION_PATH_MISMATCH_REVIEW")
    if pair.match_method in {"section_mismatch_blocked", "same_clause_no_low_similarity"}:
        flags.append("POSSIBLE_CLAUSE_MISMATCH")
    if pair.score_details.get("business_token_mismatch", 0.0) >= 1:
        flags.append("BUSINESS_TOKEN_MISMATCH_REVIEW")
    if body_score < 60 and pair.score < LOW_CONFIDENCE_MATCH_THRESHOLD:
        flags.append("LOW_CONFIDENCE_MATCH")
    if pair.original is not None:
        flags.extend(structural_review_flags(pair.original))
    if pair.compare is not None:
        flags.extend(structural_review_flags(pair.compare))
    return list(dict.fromkeys(flags))


def unmatched_review_flags(pair: ClausePair) -> list[str]:
    flags: list[str] = []
    if any(
        candidate.get("score_details", {}).get("section_mismatch_candidate", 0.0) >= 1
        for candidate in pair.match_candidates
        if isinstance(candidate.get("score_details"), dict)
    ):
        flags.append("POSSIBLE_SECTION_MISCLASSIFICATION")
    if any(
        candidate.get("score_details", {}).get("short_clause_pair", 0.0) >= 1
        for candidate in pair.match_candidates
        if isinstance(candidate.get("score_details"), dict)
    ):
        flags.append("SHORT_CLAUSE_MATCH_REVIEW")
    return flags


def structural_review_flags(clause) -> list[str]:
    flags: list[str] = []
    if getattr(clause, "section_type", "main_contract") != "main_contract":
        flags.append("NON_MAIN_CONTRACT_SECTION")
    if "WEAK_NUMERIC_MARKER" in getattr(clause, "split_flags", []):
        flags.append("POSSIBLE_SPLIT_DRIFT")
    if "READING_ORDER_REPAIRED" in getattr(clause, "split_flags", []):
        flags.append("READING_ORDER_REPAIRED")
    return flags
