from __future__ import annotations

from app.models import Clause
from app.services.diff_engine import DiffEngine
from app.services.matcher import ClauseMatcher
from app.services.normalizer import TextNormalizer


normalizer = TextNormalizer()


def clause(
    clause_id: str,
    clause_no: str,
    title: str,
    body: str,
    *,
    section_type: str = "main_contract",
    clause_key: str = "",
    split_flags: list[str] | None = None,
) -> Clause:
    text = f"{clause_no}. {title}\n{body}" if clause_no else f"{title}\n{body}"
    return Clause(
        clause_id=clause_id,
        clause_no=clause_no,
        title=title,
        text=text,
        normalized_text=normalizer.normalize_for_diff(text),
        match_text=normalizer.normalize_for_match(text),
        section_type=section_type,
        clause_key=clause_key,
        split_flags=split_flags or [],
    )


def filler(prefix: str, index: int) -> Clause:
    return clause(
        f"{prefix}{index:03d}",
        str(index),
        f"Standard clause {index}",
        f"This unrelated standard provision controls administrative item {index} and no payment terms.",
    )


def test_prefilter_uses_body_top_k_for_far_reordered_clause_without_same_number() -> None:
    original = [filler("O", index) for index in range(1, 16)]
    original[12] = clause(
        "O013",
        "13",
        "Service credits",
        "The supplier shall grant service credits equal to 1.5% of monthly fees when uptime is below 99.9%.",
    )
    compare = [filler("N", index) for index in range(1, 16)]
    compare[1] = clause(
        "N002",
        "B",
        "Service credit adjustments",
        "The supplier shall grant service credits equal to 1.5% of monthly fees when uptime is below 99.5%.",
    )

    pairs = ClauseMatcher().match(original, compare)
    matched = next(pair for pair in pairs if pair.original and pair.original.clause_id == "O013")

    assert matched.compare is not None
    assert matched.compare.clause_id == "N002"
    assert matched.score_details["candidate_source_body_top_k"] == 1.0


def test_matcher_keeps_match_when_title_changes_but_body_stays_similar() -> None:
    original = [
        clause(
            "O001",
            "1",
            "Payment terms",
            "Customer shall pay all invoices within 30 days after receipt of a valid invoice.",
        )
    ]
    compare = [
        clause(
            "N001",
            "A",
            "Settlement schedule",
            "Customer shall pay all invoices within 45 days after receipt of a valid invoice.",
        )
    ]

    pairs = ClauseMatcher().match(original, compare)

    assert pairs[0].compare is not None
    assert pairs[0].compare.clause_id == "N001"
    assert pairs[0].score_details["body_score"] >= 70


def test_numeric_change_still_matches_and_reports_modify_diff() -> None:
    original = [
        clause(
            "O001",
            "1",
            "Late fee",
            "Party B shall pay a late fee of 1.5% of the overdue amount for each month of delay.",
        )
    ]
    compare = [
        clause(
            "N001",
            "1",
            "Late fee",
            "Party B shall pay a late fee of 15% of the overdue amount for each month of delay.",
        )
    ]

    pairs = ClauseMatcher().match(original, compare)
    diffs = DiffEngine().build_diffs(pairs)

    assert pairs[0].compare is not None
    assert diffs[0].diff_type == "MODIFY"
    assert "BUSINESS_TOKEN_MISMATCH_REVIEW" in diffs[0].review_flags


def test_business_token_score_prefers_more_specific_template_candidate() -> None:
    original = [
        clause(
            "O001",
            "",
            "Late fee",
            "Party B shall pay a late fee of 1.5% of the overdue amount within 30 days after notice.",
        )
    ]
    compare = [
        clause(
            "N_BAD",
            "",
            "Late fee",
            "Party B shall pay a late fee of 15% of the overdue amount within 30 days after notice.",
        ),
        clause(
            "N_GOOD",
            "",
            "Late fee",
            "Party B shall pay a late fee of 1.5% of the overdue amount within 45 days after notice.",
        ),
    ]

    pairs = ClauseMatcher().match(original, compare)

    assert pairs[0].compare is not None
    assert pairs[0].compare.clause_id == "N_GOOD"
    assert pairs[0].score_details["business_token_score"] > 40


def test_short_partial_overlap_does_not_create_false_match() -> None:
    original = [clause("O001", "", "", "Payment")]
    compare = [
        clause(
            "N001",
            "",
            "",
            "Payment terms are subject to a separate approval workflow and detailed settlement calendar.",
        )
    ]

    pairs = ClauseMatcher().match(original, compare)

    assert len(pairs) == 2
    assert {pair.match_method for pair in pairs} == {"delete", "add"}


def test_weak_numeric_same_clause_number_does_not_override_unrelated_body() -> None:
    original = [
        clause(
            "O001",
            "2",
            "2.",
            "Party A shall provide daily weather forecast data to the project control center.",
            split_flags=["WEAK_NUMERIC_MARKER"],
        )
    ]
    compare = [
        clause(
            "N001",
            "2",
            "2.",
            "The supplier shall submit invoice copies and bank account records before payment.",
            split_flags=["WEAK_NUMERIC_MARKER"],
        )
    ]

    pairs = ClauseMatcher().match(original, compare)

    assert {pair.match_method for pair in pairs} == {"delete", "add"}


def test_matcher_does_not_match_different_document_sections_by_same_number() -> None:
    original = [
        clause(
            "O001",
            "1",
            "Service scope",
            "The service scope covers power forecast platform maintenance.",
            section_type="main_contract",
            clause_key="main_contract/1服务范围",
        )
    ]
    compare = [
        clause(
            "N001",
            "1",
            "Service scope",
            "The service scope covers power forecast platform maintenance.",
            section_type="quote",
            clause_key="quote/1服务范围",
        )
    ]

    pairs = ClauseMatcher().match(original, compare)

    assert {pair.match_method for pair in pairs} == {"delete", "add"}
