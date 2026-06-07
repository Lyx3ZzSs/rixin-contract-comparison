from __future__ import annotations

from app.models import ClausePair, DiffItem, TextRange
from app.utils.id_utils import generate_diff_id

from app.services.diff.range_refiner import changed_snippets
from app.services.diff.text_utils import shorten

LOW_CONFIDENCE_MATCH_THRESHOLD = 75.0


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
            diffs.append(build_modify(pair, next_index))
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
        match_method=pair.match_method,
        match_score=pair.score or None,
        match_score_details=pair.score_details,
        match_candidates=pair.match_candidates,
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
        match_method=pair.match_method,
        match_score=pair.score or None,
        match_score_details=pair.score_details,
        match_candidates=pair.match_candidates,
        original_evidence=clause.bboxes,
        original_change_ranges=[TextRange(start=0, end=len(clause.text), highlight_type="DELETE")],
    )


def build_modify(pair: ClausePair, index: int) -> DiffItem:
    left = pair.original
    right = pair.compare
    assert left is not None and right is not None
    original_snippet, compare_snippet, original_ranges, compare_ranges = changed_snippets(left.text, right.text)
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
        readable_change=f"原文：{original_snippet}\n修改后：{compare_snippet}",
        source_type="clause",
        match_score=pair.score,
        match_method=pair.match_method,
        match_score_details=pair.score_details,
        match_candidates=pair.match_candidates,
        review_flags=review_flags(pair),
        original_evidence=left.bboxes,
        compare_evidence=right.bboxes,
        original_change_ranges=original_ranges,
        compare_change_ranges=compare_ranges,
    )


def review_flags(pair: ClausePair) -> list[str]:
    flags: list[str] = []
    body_score = pair.score_details.get("body_score", pair.score)
    if pair.match_method == "same_clause_no_low_similarity":
        flags.append("SAME_CLAUSE_NO_LOW_SIMILARITY")
    if pair.match_method == "renumbered_similarity":
        flags.append("POSSIBLE_RENUMBERED_CLAUSE")
    if pair.score_details.get("business_token_mismatch", 0.0) >= 1:
        flags.append("BUSINESS_TOKEN_MISMATCH_REVIEW")
    if body_score < 60 and pair.score < LOW_CONFIDENCE_MATCH_THRESHOLD:
        flags.append("LOW_CONFIDENCE_MATCH")
    return flags
