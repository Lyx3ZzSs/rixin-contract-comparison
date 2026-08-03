from __future__ import annotations

from collections.abc import Callable

from app.models import Clause
from app.services.matching._fuzz import RAPIDFUZZ_RATIO, RAPIDFUZZ_TOKEN_SET, top_k_indices
from app.services.matching.types import MatchCandidate


STRONG_RULE_RECALL_SOURCES = frozenset(
    {
        "clause_no",
        "clause_key",
        "canonical_path",
        "body_top_k",
        "title_top_k",
    }
)


class CandidateRecallMixin:
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
                details = self._score_details(
                    left,
                    right,
                    original_index,
                    compare_index,
                    original,
                    compare,
                    original_count,
                    compare_count,
                )
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
        return self._apply_rerank(candidates)

    def _build_candidates_prefiltered(self, original: list[Clause], compare: list[Clause]) -> list[MatchCandidate]:
        compare_no_index = self._build_clause_no_index(compare)
        compare_key_index = self._build_clause_key_index(compare)
        compare_path_index = self._build_canonical_path_index(compare)
        original_count = max(1, len(original) - 1)
        compare_count = max(1, len(compare) - 1)
        position_window = 0.15
        index_window = 3

        candidates: list[MatchCandidate] = []
        compare_body_choices = {index: self._match_text(clause) for index, clause in enumerate(compare)}
        compare_title_choices = {index: clause.title for index, clause in enumerate(compare) if clause.title}
        semantic_choices: dict[int, list[float]] | None = None

        def get_semantic_choices() -> dict[int, list[float]]:
            nonlocal semantic_choices
            if semantic_choices is None:
                if not self.semantic_matcher.enabled:
                    semantic_choices = {}
                else:
                    prepare_pair = getattr(self.semantic_matcher, "prepare_pair", None)
                    semantic_choices = (
                        prepare_pair(original, compare)
                        if callable(prepare_pair)
                        else self.semantic_matcher.prepare(compare)
                    )
            return semantic_choices

        for original_index, left in enumerate(original):
            candidate_sources: dict[int, set[str]] = {}

            def add_candidate(index: int, source: str) -> None:
                if 0 <= index < len(compare):
                    candidate_sources.setdefault(index, set()).add(source)

            left_norm = self._normalize_clause_no(left.clause_no)
            if left_norm and left_norm in compare_no_index:
                for ci in compare_no_index[left_norm]:
                    add_candidate(ci, "clause_no")

            if left.clause_key and left.clause_key in compare_key_index:
                for ci in compare_key_index[left.clause_key]:
                    add_candidate(ci, "clause_key")

            left_path = self._canonical_path(left)
            if left_path and left_path in compare_path_index:
                for ci in compare_path_index[left_path]:
                    add_candidate(ci, "canonical_path")

            orig_ratio = original_index / original_count
            for ci, _right in enumerate(compare):
                if abs(ci / compare_count - orig_ratio) <= position_window:
                    add_candidate(ci, "position")

            approx_ci = round(orig_ratio * compare_count)
            for ci in range(
                max(0, approx_ci - index_window),
                min(len(compare), approx_ci + index_window + 1),
            ):
                add_candidate(ci, "index_window")

            for ci in self._top_k_indices(
                self._match_text(left),
                compare_body_choices,
                limit=self.body_top_k,
                score_cutoff=55.0,
                scorer=self._ratio_score,
                rapidfuzz_scorer=RAPIDFUZZ_RATIO,
            ):
                add_candidate(ci, "body_top_k")

            if left.title:
                for ci in self._top_k_indices(
                    left.title,
                    compare_title_choices,
                    limit=self.title_top_k,
                    score_cutoff=70.0,
                    scorer=self._token_score,
                    rapidfuzz_scorer=RAPIDFUZZ_TOKEN_SET,
                ):
                    add_candidate(ci, "title_top_k")

            rule_candidate_count = self._strong_rule_candidate_count(candidate_sources)
            semantic_scoring_choices: dict[int, list[float]] = {}
            semantic_recall_applied = False
            if self._should_apply_semantic_recall(rule_candidate_count):
                self._performance.increment("semantic_recall_clause_count")
                semantic_scoring_choices = get_semantic_choices()
                if semantic_scoring_choices:
                    semantic_recall_applied = True
                    for ci in self.semantic_matcher.top_k(
                        left,
                        compare,
                        semantic_scoring_choices,
                        limit=self.body_top_k,
                        score_cutoff=62.0,
                    ):
                        add_candidate(ci, "semantic_top_k")

            if not candidate_sources:
                for ci in range(len(compare)):
                    add_candidate(ci, "fallback_all")

            for compare_index, sources in candidate_sources.items():
                right = compare[compare_index]
                details = self._score_details(
                    left,
                    right,
                    original_index,
                    compare_index,
                    original,
                    compare,
                    original_count,
                    compare_count,
                )
                details["semantic_recall_applied"] = 1.0 if semantic_recall_applied else 0.0
                details["semantic_recall_mode"] = self.semantic_recall_mode
                details["semantic_recall_min_rule_candidates"] = float(self.semantic_min_rule_candidates)
                details["semantic_recall_rule_candidate_count"] = float(rule_candidate_count)
                if semantic_scoring_choices and compare_index in semantic_scoring_choices:
                    details["semantic_score"] = round(
                        self.semantic_matcher.score(left, right, semantic_scoring_choices[compare_index]),
                        2,
                    )
                    self._apply_matcher_guard_details(details)
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
        return self._apply_rerank(candidates)

    def _apply_rerank(self, candidates: list[MatchCandidate]) -> list[MatchCandidate]:
        if not self.rerank_matcher.enabled or not candidates:
            return candidates
        rerank_by_key: dict[tuple[str, str], tuple[float, str]] = {}
        groups = self._rerank_groups(candidates)
        self._performance.set_counter("rerank_candidate_group_count", len(groups))
        score_groups = getattr(self.rerank_matcher, "score_candidate_groups", None)
        grouped_scores = (
            score_groups(groups)
            if callable(score_groups)
            else [self.rerank_matcher.score_candidates(group) for group in groups]
        )
        for group, rerank_scores in zip(groups, grouped_scores, strict=False):
            if not any(available for _, available, _ in rerank_scores):
                continue
            for candidate, (rerank_score, available, reason) in zip(group, rerank_scores, strict=False):
                if available:
                    rerank_by_key[(candidate.original.clause_id, candidate.compare.clause_id)] = (
                        rerank_score,
                        reason,
                    )
        if not rerank_by_key:
            return candidates

        reranked: list[MatchCandidate] = []
        for candidate in candidates:
            details = dict(candidate.details)
            rerank_result = rerank_by_key.get((candidate.original.clause_id, candidate.compare.clause_id))
            if rerank_result is not None:
                rerank_score, reason = rerank_result
                details["semantic_rerank_available"] = 1.0
                details["semantic_rerank_score"] = round(rerank_score, 2)
                details["semantic_rerank_reason"] = reason
                details["semantic_rerank_reason_present"] = 1.0 if reason else 0.0
                details["rerank_available"] = 1.0
                details["rerank_score"] = round(rerank_score, 2)
                details["rerank_reason_present"] = 1.0 if reason else 0.0
            else:
                details["semantic_rerank_available"] = 0.0
                details["semantic_rerank_score"] = 0.0
                details["semantic_rerank_reason"] = ""
                details["semantic_rerank_reason_present"] = 0.0
            details["assignment_strategy_optimal"] = 1.0 if self.assignment_strategy == "optimal" else 0.0
            self._apply_matcher_guard_details(details)
            score = self._weighted_score(details)
            method = self._match_method(candidate.original, candidate.compare, details, score)
            reranked.append(
                MatchCandidate(candidate.original, candidate.compare, score, method, details, candidate.sources)
            )
        reranked.sort(
            key=lambda item: (
                item.score,
                item.details["body_score"],
                item.details["clause_no_score"],
                item.details["title_score"],
            ),
            reverse=True,
        )
        return reranked

    def _rerank_groups(self, candidates: list[MatchCandidate]) -> list[list[MatchCandidate]]:
        by_original: dict[str, list[MatchCandidate]] = {}
        for candidate in candidates:
            by_original.setdefault(candidate.original.clause_id, []).append(candidate)
        groups: list[list[MatchCandidate]] = []
        for group in by_original.values():
            group.sort(
                key=lambda item: (
                    item.score,
                    item.details["body_score"],
                    item.details["clause_no_score"],
                    item.details["title_score"],
                ),
                reverse=True,
            )
            groups.append(group[: self.rerank_top_k])
        return groups

    def _build_clause_no_index(self, clauses: list[Clause]) -> dict[str, list[int]]:
        index: dict[str, list[int]] = {}
        for i, clause in enumerate(clauses):
            norm = self._normalize_clause_no(clause.clause_no)
            if norm:
                index.setdefault(norm, []).append(i)
        return index

    def _build_clause_key_index(self, clauses: list[Clause]) -> dict[str, list[int]]:
        index: dict[str, list[int]] = {}
        for i, clause in enumerate(clauses):
            if clause.clause_key:
                index.setdefault(clause.clause_key, []).append(i)
        return index

    def _build_canonical_path_index(self, clauses: list[Clause]) -> dict[str, list[int]]:
        index: dict[str, list[int]] = {}
        for i, clause in enumerate(clauses):
            path = self._canonical_path(clause)
            if path:
                index.setdefault(path, []).append(i)
        return index

    def _top_k_indices(
        self,
        query: str,
        choices: dict[int, str],
        limit: int,
        score_cutoff: float,
        scorer: Callable[[str, str], float],
        rapidfuzz_scorer: Callable[..., float] | None = None,
    ) -> list[int]:
        return top_k_indices(
            query,
            choices,
            limit=limit,
            score_cutoff=score_cutoff,
            scorer=scorer,
            rapidfuzz_scorer=rapidfuzz_scorer,
        )

    def _strong_rule_candidate_count(self, candidate_sources: dict[int, set[str]]) -> int:
        return sum(1 for sources in candidate_sources.values() if sources & STRONG_RULE_RECALL_SOURCES)

    def _should_apply_semantic_recall(self, rule_candidate_count: int) -> bool:
        if not self.semantic_matcher.enabled:
            return False
        if self.semantic_recall_mode == "always":
            return True
        return rule_candidate_count < self.semantic_min_rule_candidates
