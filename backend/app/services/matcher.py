from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
import math

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - fallback for minimal environments
    fuzz = None

from app.models import Clause, ClausePair
from app.services.clause_splitter import ClauseSplitter
from app.services.normalizer import TextNormalizer


@dataclass(frozen=True)
class MatchCandidate:
    original: Clause
    compare: Clause
    score: float
    method: str
    details: dict[str, float]
    sources: tuple[str, ...] = ()


class LocalSemanticMatcher:
    """Optional local embedding scorer used only for candidate recall and tie-breaking."""

    def __init__(self, *, enabled: bool = False, model_path: str = "") -> None:
        self.enabled = False
        self.model = None
        self._cache: dict[str, list[float]] = {}
        if not enabled or not model_path:
            return
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception:
            return
        try:
            self.model = SentenceTransformer(model_path)
        except Exception:
            return
        self.enabled = True

    def prepare(self, clauses: list[Clause]) -> dict[int, list[float]]:
        if not self.enabled:
            return {}
        return {index: self._embedding(self._semantic_text(clause)) for index, clause in enumerate(clauses)}

    def top_k(
        self,
        query: Clause,
        compare: list[Clause],
        choices: dict[int, list[float]],
        *,
        limit: int,
        score_cutoff: float,
    ) -> list[int]:
        query_vector = self._embedding(self._semantic_text(query))
        scored = [
            (self._cosine_score(query_vector, vector), index)
            for index, vector in choices.items()
            if vector
        ]
        scored = [(score, index) for score, index in scored if score >= score_cutoff]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [index for _, index in scored[:limit] if index < len(compare)]

    def score(self, left: Clause, right: Clause, right_vector: list[float] | None = None) -> float:
        if not self.enabled:
            return 0.0
        left_vector = self._embedding(self._semantic_text(left))
        right_vector = right_vector or self._embedding(self._semantic_text(right))
        return self._cosine_score(left_vector, right_vector)

    def _embedding(self, text: str) -> list[float]:
        if not self.enabled or self.model is None or not text:
            return []
        if text not in self._cache:
            vector = self.model.encode(text, normalize_embeddings=True)
            self._cache[text] = [float(item) for item in vector]
        return self._cache[text]

    @staticmethod
    def _semantic_text(clause: Clause) -> str:
        return "\n".join(part for part in [clause.title, clause.text] if part)[:1200]

    @staticmethod
    def _cosine_score(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        dot = sum(a * b for a, b in zip(left, right, strict=False))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if left_norm <= 0 or right_norm <= 0:
            return 0.0
        return max(0.0, min(100.0, dot / (left_norm * right_norm) * 100.0))


class ClauseMatcher:
    def __init__(
        self,
        threshold: int = 85,
        use_prefilter: bool = True,
        body_top_k: int = 8,
        title_top_k: int = 5,
        enable_semantic_match: bool = False,
        semantic_model_path: str = "",
        semantic_weight: float = 0.08,
        low_confidence_review_threshold: float = 78.0,
    ) -> None:
        self.threshold = threshold
        self.normalizer = TextNormalizer()
        self._use_prefilter = use_prefilter
        self.body_top_k = body_top_k
        self.title_top_k = title_top_k
        self.semantic_weight = semantic_weight
        self.low_confidence_review_threshold = low_confidence_review_threshold
        self.semantic_matcher = LocalSemanticMatcher(
            enabled=enable_semantic_match,
            model_path=semantic_model_path,
        )

    def match(self, original: list[Clause], compare: list[Clause]) -> list[ClausePair]:
        pairs: list[ClausePair] = []
        matched_original: set[str] = set()
        matched_compare: set[str] = set()
        consumed_spans: dict[str, list[tuple[int, int]]] = {}

        all_candidates = self._build_candidates(original, compare)
        candidates_by_original = self._candidates_by_original(all_candidates)
        for candidate in self._select_global_candidates(all_candidates):
            if candidate.original.clause_id in matched_original or candidate.compare.clause_id in matched_compare:
                continue
            if not self._candidate_acceptable(candidate):
                continue
            pairs.append(
                ClausePair(
                    original=candidate.original,
                    compare=candidate.compare,
                    score=candidate.score,
                    match_method=candidate.method,
                    score_details=candidate.details,
                    match_candidates=self._candidate_summaries(candidates_by_original[candidate.original.clause_id]),
                    match_confidence=self._match_confidence(candidate),
                )
            )
            matched_original.add(candidate.original.clause_id)
            matched_compare.add(candidate.compare.clause_id)

        pairs.sort(
            key=lambda pair: (
                original.index(pair.original) if pair.original in original else len(original),
                compare.index(pair.compare) if pair.compare in compare else len(compare),
            )
        )

        synthetic_pairs = self._match_contained_numbered_clauses(
            original,
            compare,
            matched_compare,
            consumed_spans,
        )
        if consumed_spans:
            pairs = [
                pair.model_copy(update={"original": self._redact_clause(pair.original, consumed_spans)})
                if pair.original is not None and pair.original.clause_id in consumed_spans
                else pair
                for pair in pairs
            ]
        pairs.extend(synthetic_pairs)

        for left in original:
            if left.clause_id not in matched_original:
                redacted = self._redact_clause(left, consumed_spans)
                if redacted is not None:
                    pairs.append(ClausePair(original=redacted, compare=None, match_method="delete"))
        for right in compare:
            if right.clause_id not in matched_compare:
                pairs.append(ClausePair(original=None, compare=right, match_method="add"))

        return pairs

    def _select_global_candidates(self, candidates: list[MatchCandidate]) -> list[MatchCandidate]:
        acceptable = [candidate for candidate in candidates if self._candidate_acceptable(candidate)]
        by_original: dict[str, list[MatchCandidate]] = {}
        by_compare: dict[str, list[MatchCandidate]] = {}
        for candidate in acceptable:
            by_original.setdefault(candidate.original.clause_id, []).append(candidate)
            by_compare.setdefault(candidate.compare.clause_id, []).append(candidate)

        selected: list[MatchCandidate] = []
        used_original: set[str] = set()
        used_compare: set[str] = set()

        for candidate in acceptable:
            if candidate.original.clause_id in used_original or candidate.compare.clause_id in used_compare:
                continue
            if self._is_mutual_best(candidate, by_original, by_compare):
                selected.append(candidate)
                used_original.add(candidate.original.clause_id)
                used_compare.add(candidate.compare.clause_id)

        for candidate in acceptable:
            if candidate.original.clause_id in used_original or candidate.compare.clause_id in used_compare:
                continue
            selected.append(candidate)
            used_original.add(candidate.original.clause_id)
            used_compare.add(candidate.compare.clause_id)

        return selected

    def _is_mutual_best(
        self,
        candidate: MatchCandidate,
        by_original: dict[str, list[MatchCandidate]],
        by_compare: dict[str, list[MatchCandidate]],
    ) -> bool:
        original_best = by_original.get(candidate.original.clause_id, [])[:1]
        compare_best = by_compare.get(candidate.compare.clause_id, [])[:1]
        return bool(original_best and compare_best and original_best[0] is candidate and compare_best[0] is candidate)

    def _build_candidates(self, original: list[Clause], compare: list[Clause]) -> list[MatchCandidate]:
        n, m = len(original), len(compare)
        if not self._use_prefilter or n * m < 100:
            return self._build_candidates_exhaustive(original, compare)
        return self._build_candidates_prefiltered(original, compare)

    def _build_candidates_exhaustive(self, original: list[Clause], compare: list[Clause]) -> list[MatchCandidate]:
        candidates: list[MatchCandidate] = []
        original_count = max(1, len(original) - 1)
        compare_count = max(1, len(compare) - 1)
        for original_index, left in enumerate(original):
            for compare_index, right in enumerate(compare):
                details = self._score_details(left, right, original_index, compare_index, original, compare, original_count, compare_count)
                score = self._weighted_score(details)
                method = self._match_method(left, right, details, score)
                candidates.append(MatchCandidate(left, right, score, method, details, ("exhaustive",)))
        candidates.sort(
            key=lambda item: (
                item.score,
                item.details["body_score"],
                item.details["clause_no_score"],
                item.details["title_score"],
            ),
            reverse=True,
        )
        return candidates

    def _build_candidates_prefiltered(self, original: list[Clause], compare: list[Clause]) -> list[MatchCandidate]:
        compare_no_index = self._build_clause_no_index(compare)
        original_count = max(1, len(original) - 1)
        compare_count = max(1, len(compare) - 1)
        position_window = 0.15
        index_window = 3

        candidates: list[MatchCandidate] = []
        compare_body_choices = {index: self._match_text(clause) for index, clause in enumerate(compare)}
        compare_title_choices = {index: clause.title for index, clause in enumerate(compare) if clause.title}
        semantic_choices = self.semantic_matcher.prepare(compare) if self.semantic_matcher.enabled else {}
        for original_index, left in enumerate(original):
            candidate_sources: dict[int, set[str]] = {}

            def add_candidate(index: int, source: str) -> None:
                if 0 <= index < len(compare):
                    candidate_sources.setdefault(index, set()).add(source)

            left_norm = self._normalize_clause_no(left.clause_no)
            if left_norm and left_norm in compare_no_index:
                for ci in compare_no_index[left_norm]:
                    add_candidate(ci, "clause_no")

            orig_ratio = original_index / original_count
            for ci, right in enumerate(compare):
                if abs(ci / compare_count - orig_ratio) <= position_window:
                    add_candidate(ci, "position")

            approx_ci = round(orig_ratio * compare_count)
            for ci in range(max(0, approx_ci - index_window), min(len(compare), approx_ci + index_window + 1)):
                add_candidate(ci, "index_window")

            for ci in self._top_k_indices(
                self._match_text(left),
                compare_body_choices,
                limit=self.body_top_k,
                score_cutoff=55.0,
                scorer=self._ratio_score,
            ):
                add_candidate(ci, "body_top_k")

            if left.title:
                for ci in self._top_k_indices(
                    left.title,
                    compare_title_choices,
                    limit=self.title_top_k,
                    score_cutoff=70.0,
                    scorer=self._token_score,
                ):
                    add_candidate(ci, "title_top_k")

            if semantic_choices:
                for ci in self.semantic_matcher.top_k(left, compare, semantic_choices, limit=self.body_top_k, score_cutoff=62.0):
                    add_candidate(ci, "semantic_top_k")

            if not candidate_sources:
                for ci in range(len(compare)):
                    add_candidate(ci, "fallback_all")

            for compare_index, sources in candidate_sources.items():
                right = compare[compare_index]
                details = self._score_details(left, right, original_index, compare_index, original, compare, original_count, compare_count)
                if semantic_choices and compare_index in semantic_choices:
                    details["semantic_score"] = round(self.semantic_matcher.score(left, right, semantic_choices[compare_index]), 2)
                for source in sources:
                    details[f"candidate_source_{source}"] = 1.0
                score = self._weighted_score(details)
                method = self._match_method(left, right, details, score)
                candidates.append(MatchCandidate(left, right, score, method, details, tuple(sorted(sources))))

        candidates.sort(
            key=lambda item: (
                item.score,
                item.details["body_score"],
                item.details["clause_no_score"],
                item.details["title_score"],
            ),
            reverse=True,
        )
        return candidates

    def _build_clause_no_index(self, clauses: list[Clause]) -> dict[str, list[int]]:
        index: dict[str, list[int]] = {}
        for i, clause in enumerate(clauses):
            norm = self._normalize_clause_no(clause.clause_no)
            if norm:
                index.setdefault(norm, []).append(i)
        return index

    def _score_details(
        self,
        left: Clause,
        right: Clause,
        original_index: int,
        compare_index: int,
        original: list[Clause],
        compare: list[Clause],
        original_count: int,
        compare_count: int,
    ) -> dict[str, float]:
        title_score = self._token_score(left.title, right.title) if left.title and right.title else 0.0
        body_details = self._body_score_details(self._match_text(left), self._match_text(right))
        clause_no_score = self._clause_no_score(left.clause_no, right.clause_no)
        clause_key_score = self._clause_key_score(left, right)
        section_score = self._section_score(left, right)
        position_score = self._position_score(original_index / original_count, compare_index / compare_count)
        neighbor_score = self._neighbor_score(original, compare, original_index, compare_index)
        business_token_score, business_mismatch = self._business_token_score(left.text, right.text, body_details["body_score"])
        details = {
            "clause_key_score": round(clause_key_score, 2),
            "clause_no_score": round(clause_no_score, 2),
            "section_score": round(section_score, 2),
            "title_score": round(title_score, 2),
            "position_score": round(position_score, 2),
            "neighbor_score": round(neighbor_score, 2),
            "business_token_score": round(business_token_score, 2),
            "business_token_mismatch": 1.0 if business_mismatch else 0.0,
            "weak_numeric_marker": 1.0 if self._has_weak_numeric_marker(left, right) else 0.0,
            "semantic_score": 0.0,
        }
        details.update({key: round(value, 2) for key, value in body_details.items()})
        return details

    def _weighted_score(self, details: dict[str, float]) -> float:
        weighted = (
            details["clause_key_score"] * 0.16
            + details["section_score"] * 0.10
            + details["clause_no_score"] * 0.14
            + details["title_score"] * 0.18
            + details["body_score"] * 0.32
            + details["business_token_score"] * 0.07
            + details["position_score"] * 0.06
            + details["neighbor_score"] * 0.05
            + details.get("semantic_score", 0.0) * self.semantic_weight
        )
        if details["section_score"] < 100 and details["body_score"] < 90:
            weighted = min(weighted, 72.0)
        if details["clause_key_score"] == 100 and details["body_score"] >= 45:
            weighted = max(weighted, 88.0 + min(12.0, details["body_score"] * 0.12))
        elif details["clause_no_score"] == 100 and details["title_score"] >= 90 and details["body_score"] >= 45:
            weighted = max(weighted, 100.0)
        elif details["clause_no_score"] == 100 and (details["body_score"] >= 55 or details["title_score"] >= 80):
            weighted = max(weighted, min(100.0, 70.0 + details["body_score"] * 0.20 + details["title_score"] * 0.10))
        elif details["body_score"] >= self.threshold:
            if details["business_token_score"] < 45:
                weighted = max(weighted, details["body_score"] * 0.90)
            else:
                weighted = max(weighted, details["body_score"])
        elif details.get("semantic_score", 0.0) >= 88 and details["body_score"] >= 60:
            weighted = max(weighted, details["semantic_score"] * 0.92)
        elif details["body_score"] >= min(self.threshold, 78):
            weighted = max(weighted, details["body_score"] * 0.92)
        if details["weak_numeric_marker"] >= 1 and details["body_score"] < 80 and details["title_score"] < 75:
            weighted = min(weighted, 68.0)
        return round(weighted, 2)

    def _candidate_acceptable(self, candidate: MatchCandidate) -> bool:
        details = candidate.details
        same_clause_no = self._same_clause_no(candidate.original.clause_no, candidate.compare.clause_no)
        if (candidate.original.section_type or "main_contract") != (candidate.compare.section_type or "main_contract"):
            return False
        if details["section_score"] < 100 and details["body_score"] < 90 and details.get("semantic_score", 0.0) < 88:
            return False
        if (
            details["weak_numeric_marker"] >= 1
            and details["body_ratio_score"] < 70
            and (details["title_score"] < 75 or self._has_weak_titles(candidate.original, candidate.compare))
        ):
            return False
        if details["weak_numeric_marker"] >= 1 and details["body_score"] < 80 and details["title_score"] < 75:
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

    def _match_method(self, left: Clause, right: Clause, details: dict[str, float], score: float) -> str:
        same_clause_no = self._same_clause_no(left.clause_no, right.clause_no)
        if details["section_score"] < 100:
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

    def _match_confidence(self, candidate: MatchCandidate) -> str:
        details = candidate.details
        if candidate.score < self.low_confidence_review_threshold:
            return "LOW"
        if details.get("weak_numeric_marker", 0.0) >= 1:
            return "LOW" if details["body_score"] < 88 else "MEDIUM"
        if candidate.method in {"same_clause_no_low_similarity", "section_mismatch_blocked"}:
            return "LOW"
        if details["body_score"] < 65 and details["title_score"] < 75:
            return "MEDIUM"
        return "NORMAL"

    def _clause_no_score(self, left: str, right: str) -> float:
        left_norm = self._normalize_clause_no(left)
        right_norm = self._normalize_clause_no(right)
        if left_norm and right_norm and left_norm == right_norm:
            return 100.0
        if not left_norm or not right_norm:
            return 0.0
        left_parts = left_norm.split(".")
        right_parts = right_norm.split(".")
        if left_parts[-1:] == right_parts[-1:] and len(left_parts) == len(right_parts):
            return 70.0
        if len(left_parts) == len(right_parts):
            return 40.0
        return self._score(left_norm, right_norm) * 0.5

    def _clause_key_score(self, left: Clause, right: Clause) -> float:
        if left.clause_key and right.clause_key and left.clause_key == right.clause_key:
            return 100.0
        if not left.clause_key or not right.clause_key:
            return 0.0
        if left.section_type and right.section_type and left.section_type != right.section_type:
            return 0.0
        return self._token_score(left.clause_key, right.clause_key)

    def _section_score(self, left: Clause, right: Clause) -> float:
        left_type = left.section_type or "main_contract"
        right_type = right.section_type or "main_contract"
        if left_type != right_type:
            return 0.0
        left_path = "/".join(left.section_path[:-1])
        right_path = "/".join(right.section_path[:-1])
        if not left_path and not right_path:
            return 100.0
        if left_path and right_path:
            return self._token_score(left_path, right_path)
        return 70.0

    def _has_weak_numeric_marker(self, left: Clause, right: Clause) -> bool:
        flags = {*left.split_flags, *right.split_flags}
        if "WEAK_NUMERIC_MARKER" in flags:
            return True
        return bool(
            self._normalize_clause_no(left.clause_no)
            and self._normalize_clause_no(left.clause_no) == self._normalize_clause_no(right.clause_no)
            and re.fullmatch(r"\d+", self._normalize_clause_no(left.clause_no))
            and (len(left.title.strip()) < 4 or len(right.title.strip()) < 4)
        )

    def _has_weak_titles(self, left: Clause, right: Clause) -> bool:
        return self._weak_title(left.title) or self._weak_title(right.title)

    def _weak_title(self, title: str) -> bool:
        compact = re.sub(r"[\s、.．:：]+", "", title or "")
        return not compact or bool(re.fullmatch(r"\d{1,3}", compact))

    def _same_clause_no(self, left: str, right: str) -> bool:
        left_norm = self._normalize_clause_no(left)
        right_norm = self._normalize_clause_no(right)
        return bool(left_norm and right_norm and left_norm == right_norm)

    def _normalize_clause_no(self, value: str) -> str:
        value = unicodedata.normalize("NFKC", value or "").strip()
        value = value.strip("、. ")
        value = value.replace("第", "").replace("条", "").replace("章", "").replace("节", "")
        value = value.strip("（）()")
        chinese_digits = {
            "一": "1",
            "二": "2",
            "三": "3",
            "四": "4",
            "五": "5",
            "六": "6",
            "七": "7",
            "八": "8",
            "九": "9",
            "十": "10",
        }
        return chinese_digits.get(value, value.lower())

    def _position_score(self, left_ratio: float, right_ratio: float) -> float:
        distance = abs(left_ratio - right_ratio)
        return max(0.0, 100.0 - distance * 180.0)

    def _neighbor_score(
        self,
        original: list[Clause],
        compare: list[Clause],
        original_index: int,
        compare_index: int,
    ) -> float:
        scores: list[float] = []
        if original_index > 0 and compare_index > 0:
            scores.append(self._clause_no_score(original[original_index - 1].clause_no, compare[compare_index - 1].clause_no))
        if original_index + 1 < len(original) and compare_index + 1 < len(compare):
            scores.append(self._clause_no_score(original[original_index + 1].clause_no, compare[compare_index + 1].clause_no))
        return sum(scores) / len(scores) if scores else 0.0

    def _score(self, left: str, right: str) -> float:
        return self._token_score(left, right)

    def _token_score(self, left: str, right: str) -> float:
        if not left and not right:
            return 100.0
        if not left or not right:
            return 0.0
        if fuzz is not None:
            return float(fuzz.token_set_ratio(left, right))
        return SequenceMatcher(None, left, right).ratio() * 100

    def _ratio_score(self, left: str, right: str) -> float:
        if not left and not right:
            return 100.0
        if not left or not right:
            return 0.0
        if fuzz is not None:
            return float(fuzz.ratio(left, right))
        return SequenceMatcher(None, left, right).ratio() * 100

    def _partial_score(self, left: str, right: str) -> float:
        if not left and not right:
            return 100.0
        if not left or not right:
            return 0.0
        if fuzz is not None:
            return float(fuzz.partial_ratio(left, right))
        return SequenceMatcher(None, left, right).ratio() * 100

    def _body_score_details(self, left: str, right: str) -> dict[str, float]:
        ratio_score = self._ratio_score(left, right)
        token_score = self._token_score(left, right)
        partial_score = self._partial_score(left, right)
        min_len = min(len(left or ""), len(right or ""))
        max_len = max(len(left or ""), len(right or ""))
        length_coverage = (min_len / max_len) if max_len else 1.0
        capped_token = token_score
        capped_partial = partial_score
        if min_len < 8:
            capped_token = min(capped_token, 70.0)
            capped_partial = min(capped_partial, 70.0)
        elif length_coverage < 0.50:
            capped_token = min(capped_token, 76.0)
            capped_partial = min(capped_partial, 72.0)
        elif length_coverage < 0.70:
            capped_token = min(capped_token, 86.0)
            capped_partial = min(capped_partial, 82.0)
        elif length_coverage < 0.85:
            capped_partial = min(capped_partial, 92.0)
        body_score = max(ratio_score, capped_token, capped_partial)
        return {
            "body_ratio_score": ratio_score,
            "body_token_score": token_score,
            "body_token_capped_score": capped_token,
            "body_partial_score": partial_score,
            "body_length_coverage": length_coverage,
            "body_score": body_score,
        }

    def _top_k_indices(
        self,
        query: str,
        choices: dict[int, str],
        limit: int,
        score_cutoff: float,
        scorer,
    ) -> list[int]:
        if not query or limit <= 0:
            return []
        scored: list[tuple[float, int]] = []
        for index, choice in choices.items():
            if not choice:
                continue
            score = scorer(query, choice)
            if score >= score_cutoff:
                scored.append((score, index))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [index for _, index in scored[:limit]]

    def _business_token_score(self, left: str, right: str, body_score: float) -> tuple[float, bool]:
        left_tokens = self._business_tokens(left)
        right_tokens = self._business_tokens(right)
        if not left_tokens and not right_tokens:
            return 100.0, False
        if not left_tokens or not right_tokens:
            return 70.0, body_score >= 80
        intersection = len(left_tokens & right_tokens)
        union = len(left_tokens | right_tokens)
        score = (intersection / union) * 100 if union else 100.0
        return score, bool(score < 45 and body_score >= 70)

    def _business_tokens(self, text: str) -> set[str]:
        normalized = unicodedata.normalize("NFKC", text or "").lower()
        tokens: set[str] = set()
        token_patterns = [
            r"\bv\s*\d+(?:\.\d+)*\b",
            r"\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|‰)",
            r"\d+(?:,\d{3})*(?:\.\d+)?\s*(?:元|万元|亿元|usd|rmb|cny|人民币|美元)",
            r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?",
            r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}",
            r"\d+(?:,\d{3})*(?:\.\d+)?",
            r"\bparty\s+[ab]\b",
            r"\bcompany\b",
            r"\bbank\b",
            r"\baccount\b",
        ]
        for pattern in token_patterns:
            tokens.update(re.sub(r"\s+", "", match.group(0)) for match in re.finditer(pattern, normalized))
        for keyword in ("甲方", "乙方", "丙方", "公司", "银行", "账号", "合同金额", "违约金", "质保期", "付款", "交货", "期限"):
            if keyword in normalized:
                tokens.add(keyword)
        return tokens

    def _match_text(self, clause: Clause) -> str:
        return clause.match_text or clause.normalized_text

    def _match_contained_numbered_clauses(
        self,
        original: list[Clause],
        compare: list[Clause],
        matched_compare: set[str],
        consumed_spans: dict[str, list[tuple[int, int]]],
    ) -> list[ClausePair]:
        pairs: list[ClausePair] = []
        synthetic_index = 1
        for right in compare:
            if right.clause_id in matched_compare or not right.clause_no:
                continue
            right_body = self._strip_clause_prefix_only(right.text)
            if len(self._search_text(right_body)[0]) < 4:
                continue
            match = self._best_contained_match(right_body, right.section_type, original, consumed_spans)
            if match is None:
                continue
            parent, start, end, score = match
            left = self._slice_clause(parent, start, end, f"{parent.clause_id}S{synthetic_index:02d}")
            if left is None:
                continue
            synthetic_index += 1
            pairs.append(
                ClausePair(
                    original=left,
                    compare=right,
                    score=score,
                    match_method="contained_original",
                    score_details={
                        "clause_no_score": 0.0,
                        "title_score": self._score(left.title, right.title) if left.title and right.title else 0.0,
                        "body_score": round(score, 2),
                        "position_score": 0.0,
                        "neighbor_score": 0.0,
                    },
                )
            )
            consumed_spans.setdefault(parent.clause_id, []).append((start, end))
            matched_compare.add(right.clause_id)
        return pairs

    def _best_contained_match(
        self,
        body: str,
        section_type: str,
        original: list[Clause],
        consumed_spans: dict[str, list[tuple[int, int]]],
    ) -> tuple[Clause, int, int, float] | None:
        best: tuple[Clause, int, int, float] | None = None
        for clause in original:
            if (clause.section_type or "main_contract") != (section_type or "main_contract"):
                continue
            span = self._find_contained_span(body, clause.text)
            if span is None or self._overlaps_existing(span, consumed_spans.get(clause.clause_id, [])):
                continue
            start, end, score = span
            if best is None or score > best[3]:
                best = (clause, start, end, score)
        return best

    def _find_contained_span(self, needle_text: str, haystack_text: str) -> tuple[int, int, float] | None:
        needle, _ = self._search_text(needle_text)
        haystack, haystack_map = self._search_text(haystack_text)
        if not needle or not haystack:
            return None
        if len(haystack) <= len(needle) + 8:
            return None

        exact = haystack.find(needle)
        if exact >= 0:
            return self._source_span(exact, exact + len(needle), haystack_map, 100.0)

        if len(needle) < 20:
            return None

        anchor_len = min(16, max(8, len(needle) // 8))
        start_anchor = needle[:anchor_len]
        end_anchor = needle[-anchor_len:]
        start_index = haystack.find(start_anchor)
        if start_index < 0:
            return None
        end_anchor_index = haystack.find(end_anchor, start_index + anchor_len)
        if end_anchor_index < 0:
            return None
        compact_end = end_anchor_index + anchor_len
        candidate = haystack[start_index:compact_end]
        score = self._ratio(needle, candidate)
        if score < self.threshold:
            return None
        return self._source_span(start_index, compact_end, haystack_map, score)

    def _source_span(
        self,
        compact_start: int,
        compact_end: int,
        index_map: list[int],
        score: float,
    ) -> tuple[int, int, float] | None:
        if compact_start >= compact_end or compact_end > len(index_map):
            return None
        start = index_map[compact_start]
        end = index_map[compact_end - 1] + 1
        return start, end, score

    def _strip_clause_prefix_only(self, text: str) -> str:
        stripped = (text or "").strip()
        if not stripped:
            return ""
        lines = stripped.splitlines()
        first_line = lines[0]
        match = ClauseSplitter.clause_start_pattern.match(first_line)
        if not match:
            return stripped
        first_line_body = first_line[match.end(1):].lstrip("、. 　")
        return "\n".join([first_line_body, *lines[1:]]).strip()

    def _slice_clause(self, clause: Clause, start: int, end: int, clause_id: str) -> Clause | None:
        while start < end and clause.text[start].isspace():
            start += 1
        while end > start and clause.text[end - 1].isspace():
            end -= 1
        if start >= end:
            return None
        text = clause.text[start:end]
        char_boxes = clause.char_boxes[start:end] if len(clause.char_boxes) == len(clause.text) else []
        return Clause(
            clause_id=clause_id,
            clause_no="",
            title=self._title_from_text(text),
            text=text,
            normalized_text=self.normalizer.normalize_for_diff(text),
            match_text=self.normalizer.normalize_for_match(text),
            page_numbers=clause.page_numbers,
            bboxes=clause.bboxes,
            source_block_ids=clause.source_block_ids,
            char_boxes=char_boxes,
            section_type=clause.section_type,
            section_path=clause.section_path,
            clause_key=clause.clause_key,
            order_index=clause.order_index,
            split_flags=clause.split_flags,
        )

    def _redact_clause(self, clause: Clause | None, consumed_spans: dict[str, list[tuple[int, int]]]) -> Clause | None:
        if clause is None:
            return None
        spans = self._merge_spans(consumed_spans.get(clause.clause_id, []), len(clause.text))
        if not spans:
            return clause

        kept_chars: list[str] = []
        kept_boxes = [] if len(clause.char_boxes) == len(clause.text) else None
        span_index = 0
        for index, char in enumerate(clause.text):
            while span_index < len(spans) and index >= spans[span_index][1]:
                span_index += 1
            if span_index < len(spans) and spans[span_index][0] <= index < spans[span_index][1]:
                continue
            kept_chars.append(char)
            if kept_boxes is not None:
                kept_boxes.append(clause.char_boxes[index])

        text = "".join(kept_chars).strip()
        if not text:
            return None
        if kept_boxes is not None:
            leading = len("".join(kept_chars)) - len("".join(kept_chars).lstrip())
            trailing_text = "".join(kept_chars).strip()
            kept_boxes = kept_boxes[leading:leading + len(trailing_text)]
        return clause.model_copy(
            update={
                "text": text,
                "normalized_text": self.normalizer.normalize_for_diff(text),
                "match_text": self.normalizer.normalize_for_match(text),
                "title": clause.title if text.startswith(clause.title) else self._title_from_text(text),
                "char_boxes": kept_boxes if kept_boxes is not None else [],
            }
        )

    def _merge_spans(self, spans: list[tuple[int, int]], max_len: int) -> list[tuple[int, int]]:
        cleaned = sorted((max(0, start), min(max_len, end)) for start, end in spans if start < end)
        if not cleaned:
            return []
        merged = [cleaned[0]]
        for start, end in cleaned[1:]:
            prev_start, prev_end = merged[-1]
            if start <= prev_end:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))
        return merged

    def _overlaps_existing(self, span: tuple[int, int, float], existing: list[tuple[int, int]]) -> bool:
        start, end, _ = span
        return any(start < right and left < end for left, right in existing)

    def _search_text(self, text: str) -> tuple[str, list[int]]:
        chars: list[str] = []
        index_map: list[int] = []
        for index, char in enumerate(text or ""):
            normalized = unicodedata.normalize("NFKC", char).lower()
            if not normalized or normalized.isspace():
                continue
            for normalized_char in normalized:
                if normalized_char.isspace():
                    continue
                chars.append(normalized_char)
                index_map.append(index)
        return "".join(chars), index_map

    def _ratio(self, left: str, right: str) -> float:
        if fuzz is not None:
            return float(fuzz.ratio(left, right))
        return SequenceMatcher(None, left, right).ratio() * 100

    def _title_from_text(self, text: str) -> str:
        first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        return re.sub(r"\s+", " ", first_line)[:40]
