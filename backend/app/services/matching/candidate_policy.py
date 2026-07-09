from __future__ import annotations

from typing import Any

from app.services.matching.constants import (
    BODY_ONLY_ALIGNMENT_RISK,
    CRITICAL_TOKEN_CONFLICT,
    SAME_KEY_LOW_BODY_COVERAGE,
    SAME_NUMBER_LOW_BODY_SIMILARITY,
    SECTION_PATH_MISMATCH_REVIEW,
)
from app.services.matching.types import MatchCandidate


class CandidatePolicyMixin:
    def _apply_matcher_guard_details(self, details: dict[str, Any]) -> None:
        flags = self._matcher_risk_flags(details)
        details["matcher_risk_flags"] = flags
        details["matcher_guard_applied"] = 1.0 if flags else 0.0

    def _matcher_risk_flags(self, details: dict[str, Any]) -> list[str]:
        flags: list[str] = []
        alignment = details.get("alignment")
        alignment_flags = self._alignment_risk_flags(alignment)
        body_similarity = self._alignment_number(alignment, "body_similarity", default=1.0)

        if (
            details.get("clause_key_score", 0.0) >= 96
            and details.get("body_length_coverage", 1.0) < 0.70
        ):
            flags.append(SAME_KEY_LOW_BODY_COVERAGE)

        if "CRITICAL_TOKEN_MISMATCH" in alignment_flags:
            flags.append(CRITICAL_TOKEN_CONFLICT)

        if (
            details.get("clause_no_score", 0.0) >= 100
            and body_similarity < 0.45
        ):
            flags.append(SAME_NUMBER_LOW_BODY_SIMILARITY)

        if (
            alignment_flags
            and details.get("clause_key_score", 0.0) < 96
            and details.get("clause_no_score", 0.0) < 100
            and details.get("body_score", 0.0) >= min(self.threshold, 78)
        ):
            flags.append(BODY_ONLY_ALIGNMENT_RISK)

        if details.get("section_path_mismatch", 0.0) >= 1:
            flags.append(SECTION_PATH_MISMATCH_REVIEW)

        return flags

    @staticmethod
    def _alignment_risk_flags(alignment: Any) -> set[str]:
        if not isinstance(alignment, dict):
            return set()
        raw_flags = alignment.get("risk_flags")
        if not isinstance(raw_flags, list | tuple | set):
            return set()
        return {flag for flag in raw_flags if isinstance(flag, str) and flag}

    @staticmethod
    def _alignment_number(alignment: Any, field: str, *, default: float) -> float:
        if not isinstance(alignment, dict):
            return default
        value = alignment.get(field)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
        return default

    def _candidate_acceptable(self, candidate: MatchCandidate) -> bool:
        details = candidate.details
        same_clause_no = self._same_clause_no(candidate.original.clause_no, candidate.compare.clause_no)
        if (candidate.original.section_type or "main_contract") != (candidate.compare.section_type or "main_contract"):
            return False
        if details["section_score"] < 100 and details["body_score"] < 90 and details.get("semantic_score", 0.0) < 88:
            if not self._allow_section_path_mismatch_match(candidate, details, same_clause_no):
                return False
        if (
            details["weak_numeric_marker"] >= 1
            and details["body_ratio_score"] < 70
            and (details["title_score"] < 75 or self._has_weak_titles(candidate.original, candidate.compare))
        ):
            return False
        if details["weak_numeric_marker"] >= 1 and details["body_score"] < 80 and details["title_score"] < 75:
            return False
        risk_flags = set(details.get("matcher_risk_flags", []))
        if (
            SAME_KEY_LOW_BODY_COVERAGE in risk_flags
            and details["body_score"] < 55
            and details["title_score"] < 80
        ):
            return False
        if (
            SAME_NUMBER_LOW_BODY_SIMILARITY in risk_flags
            and details["title_score"] < 80
            and candidate.score < self.low_confidence_review_threshold
        ):
            return False
        if details["clause_key_score"] >= 96 and details["body_score"] >= 35:
            return True
        if same_clause_no:
            return details["body_score"] >= 55 or details["title_score"] >= 80 or candidate.score >= self.low_confidence_review_threshold
        if details["body_score"] >= self.threshold:
            return True
        if candidate.score >= min(self.threshold, 78) and details["body_score"] >= 55:
            return True
        if details["title_score"] >= 92 and details["body_score"] >= 55:
            return True
        if details.get("semantic_score", 0.0) >= 88 and details["body_score"] >= 58:
            return True
        return bool(
            details.get("body_partial_score", 0.0) >= 90
            and details.get("body_length_coverage", 0.0) >= 0.70
            and min(len(self._match_text(candidate.original)), len(self._match_text(candidate.compare))) >= 20
        )

    def _allow_section_path_mismatch_match(
        self,
        candidate: MatchCandidate,
        details: dict[str, Any],
        same_clause_no: bool,
    ) -> bool:
        if details.get("section_path_mismatch", 0.0) < 1:
            return False
        if (candidate.original.section_type or "main_contract") != (candidate.compare.section_type or "main_contract"):
            return False
        body_score = details["body_score"]
        title_score = details["title_score"]
        if details["clause_key_score"] >= 96 and body_score >= 35:
            return True
        if details.get("canonical_path_score", 0.0) >= 96 and body_score >= 45:
            return True
        if same_clause_no and body_score >= 70:
            return True
        return bool(same_clause_no and title_score >= 80 and body_score >= 55)

    def _match_method(self, left, right, details: dict[str, Any], score: float) -> str:
        same_clause_no = self._same_clause_no(left.clause_no, right.clause_no)
        if details["section_score"] < 100 and details.get("section_path_mismatch", 0.0) < 1:
            return "section_mismatch_blocked"
        if details["clause_key_score"] >= 96:
            return "same_clause_key_weighted"
        if same_clause_no and details["body_score"] < 55:
            return "same_clause_no_low_similarity"
        if same_clause_no:
            return "same_clause_no_weighted"
        if details.get("semantic_score", 0.0) >= 88 and score >= min(self.threshold, 78):
            return "semantic_weighted_similarity"
        if left.clause_no and right.clause_no and details["body_score"] >= 70:
            return "renumbered_similarity"
        if details.get("body_partial_score", 0.0) >= 90 and details.get("body_length_coverage", 0.0) >= 0.70:
            return "partial_body_similarity"
        if details["title_score"] > details["body_score"] and score >= min(self.threshold, 78):
            return "title_weighted_similarity"
        return "body_weighted_similarity"

    def _candidates_by_original(self, candidates: list[MatchCandidate]) -> dict[str, list[MatchCandidate]]:
        grouped: dict[str, list[MatchCandidate]] = {}
        for candidate in candidates:
            grouped.setdefault(candidate.original.clause_id, []).append(candidate)
        return grouped

    def _candidate_summaries(self, candidates: list[MatchCandidate]) -> list[dict[str, object]]:
        result = []
        for candidate in candidates[:5]:
            result.append(
                {
                    "compare_clause_id": candidate.compare.clause_id,
                    "compare_clause_no": candidate.compare.clause_no,
                    "compare_clause_key": candidate.compare.clause_key,
                    "compare_section_type": candidate.compare.section_type,
                    "compare_section_path": candidate.compare.section_path,
                    "score": round(candidate.score, 2),
                    "match_method": candidate.method,
                    "match_confidence": self._match_confidence(candidate),
                    "score_details": candidate.details,
                }
            )
        return result

    def _section_mismatch_summaries(self, candidates: list[MatchCandidate]) -> list[dict[str, object]]:
        flagged = [
            candidate
            for candidate in candidates
            if candidate.details.get("section_mismatch_candidate", 0.0) >= 1
            or candidate.details.get("matcher_risk_flags")
        ]
        return self._candidate_summaries(flagged)

    def _section_mismatch_summaries_for_compare(
        self,
        candidates: list[MatchCandidate],
        compare_clause_id: str,
    ) -> list[dict[str, object]]:
        flagged = [
            candidate
            for candidate in candidates
            if candidate.compare.clause_id == compare_clause_id
            and candidate.details.get("section_mismatch_candidate", 0.0) >= 1
        ]
        flagged.sort(
            key=lambda item: (
                item.details.get("section_mismatch_score", 0.0),
                item.details.get("body_score", 0.0),
            ),
            reverse=True,
        )
        return self._candidate_summaries(flagged)

    def _match_confidence(self, candidate: MatchCandidate) -> str:
        details = candidate.details
        if details.get("matcher_risk_flags"):
            return "LOW"
        alignment = details.get("alignment")
        if isinstance(alignment, dict) and alignment.get("risk_flags"):
            return "LOW"
        if candidate.score < self.low_confidence_review_threshold:
            return "LOW"
        if details.get("body_length_coverage", 1.0) < 0.50:
            return "LOW"
        if details.get("body_length_coverage", 1.0) < 0.70:
            return "MEDIUM"
        if details.get("weak_numeric_marker", 0.0) >= 1:
            return "LOW" if details["body_score"] < 88 else "MEDIUM"
        if (candidate.original.section_type or "main_contract") != "main_contract" or (candidate.compare.section_type or "main_contract") != "main_contract":
            return "MEDIUM" if details["body_score"] >= 70 else "LOW"
        if candidate.method in {"same_clause_no_low_similarity", "section_mismatch_blocked"}:
            return "LOW"
        if details["body_score"] < 65 and details["title_score"] < 75:
            return "MEDIUM"
        return "NORMAL"
