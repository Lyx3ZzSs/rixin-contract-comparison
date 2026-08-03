from __future__ import annotations

import httpx

from app.models import Clause, ClausePair
from app.services.clause_alignment import ClauseAlignmentAnalyzer
from app.services.clause_numbering import ClauseNumberParser
from app.services.matching.assignment import AssignmentMixin
from app.services.matching.candidate_policy import CandidatePolicyMixin
from app.services.matching.candidate_recall import CandidateRecallMixin
from app.services.matching.candidate_scoring import CandidateScoringMixin
from app.services.matching.constants import (
    BODY_ONLY_ALIGNMENT_RISK,
    CRITICAL_TOKEN_CONFLICT,
    SAME_KEY_LOW_BODY_COVERAGE,
    SAME_NUMBER_LOW_BODY_SIMILARITY,
    SECTION_PATH_MISMATCH_REVIEW,
)
from app.services.matching.providers import RerankMatcher, SemanticMatcher
from app.services.matching.structural_drift import StructuralDriftMixin
from app.services.matching.types import FlowEdge as _FlowEdge
from app.services.matching.types import MatchCandidate
from app.services.normalizer import TextNormalizer
from app.services.pipeline_metrics import PerformanceRecorder


class ClauseMatcher(
    CandidateRecallMixin,
    AssignmentMixin,
    CandidatePolicyMixin,
    CandidateScoringMixin,
    StructuralDriftMixin,
):
    def __init__(
        self,
        threshold: int = 85,
        use_prefilter: bool = True,
        body_top_k: int = 8,
        title_top_k: int = 5,
        assignment_strategy: str = "greedy",
        enable_semantic_match: bool = False,
        semantic_provider: str = "local",
        semantic_model_path: str = "",
        semantic_base_url: str = "",
        semantic_api_key: str = "",
        semantic_model: str = "",
        semantic_device: str = "auto",
        semantic_batch_size: int = 32,
        semantic_max_inflight: int = 3,
        semantic_timeout_seconds: int = 60,
        semantic_max_retries: int = 2,
        semantic_weight: float = 0.08,
        semantic_recall_mode: str = "sparse",
        semantic_min_rule_candidates: int = 3,
        enable_rerank: bool = False,
        rerank_base_url: str = "",
        rerank_api_key: str = "",
        rerank_model: str = "",
        rerank_top_k: int = 30,
        rerank_max_inflight: int = 8,
        rerank_timeout_seconds: int = 30,
        rerank_max_retries: int = 1,
        rerank_weight: float = 0.12,
        low_confidence_review_threshold: float = 78.0,
    ) -> None:
        self.threshold = threshold
        self.normalizer = TextNormalizer()
        self.alignment_analyzer = ClauseAlignmentAnalyzer(self.normalizer)
        self.number_parser = ClauseNumberParser()
        self._use_prefilter = use_prefilter
        self.body_top_k = body_top_k
        self.title_top_k = title_top_k
        self.assignment_strategy = assignment_strategy.strip().lower() if assignment_strategy else "greedy"
        if self.assignment_strategy not in {"greedy", "optimal"}:
            self.assignment_strategy = "greedy"
        self.semantic_weight = semantic_weight
        self.semantic_recall_mode = semantic_recall_mode.strip().lower() if semantic_recall_mode else "sparse"
        if self.semantic_recall_mode not in {"sparse", "always"}:
            self.semantic_recall_mode = "sparse"
        self.semantic_min_rule_candidates = max(0, min(50, semantic_min_rule_candidates))
        self.rerank_weight = rerank_weight
        self.rerank_top_k = max(1, min(50, rerank_top_k))
        self.low_confidence_review_threshold = low_confidence_review_threshold
        self.semantic_matcher = SemanticMatcher(
            enabled=enable_semantic_match,
            provider=semantic_provider,
            model_path=semantic_model_path,
            base_url=semantic_base_url,
            api_key=semantic_api_key,
            model=semantic_model,
            device=semantic_device,
            batch_size=semantic_batch_size,
            max_inflight=semantic_max_inflight,
            timeout_seconds=semantic_timeout_seconds,
            max_retries=semantic_max_retries,
        )
        self.rerank_matcher = RerankMatcher(
            enabled=enable_rerank,
            base_url=rerank_base_url,
            api_key=rerank_api_key,
            model=rerank_model,
            max_inflight=rerank_max_inflight,
            timeout_seconds=rerank_timeout_seconds,
            max_retries=rerank_max_retries,
        )
        self._performance = PerformanceRecorder()
        self.last_performance_metrics: dict[str, object] = {}

    def match(self, original: list[Clause], compare: list[Clause]) -> list[ClausePair]:
        self._performance = PerformanceRecorder()
        for provider in (self.semantic_matcher, self.rerank_matcher):
            set_recorder = getattr(provider, "set_performance_recorder", None)
            if callable(set_recorder):
                set_recorder(self._performance)
        self._performance.set_counter("original_clause_count", len(original))
        self._performance.set_counter("compare_clause_count", len(compare))
        try:
            return self._match_clauses(original, compare)
        finally:
            for provider in (self.semantic_matcher, self.rerank_matcher):
                close = getattr(provider, "close", None)
                if callable(close):
                    close()

    def _match_clauses(self, original: list[Clause], compare: list[Clause]) -> list[ClausePair]:
        pairs: list[ClausePair] = []
        matched_original: set[str] = set()
        matched_compare: set[str] = set()
        consumed_original_spans: dict[str, list[tuple[int, int]]] = {}
        consumed_compare_spans: dict[str, list[tuple[int, int]]] = {}

        with self._performance.measure("candidate_generation"):
            all_candidates = self._build_candidates(original, compare)
        self._performance.set_counter("candidate_count", len(all_candidates))
        candidates_by_original = self._candidates_by_original(all_candidates)
        with self._performance.measure("global_assignment"):
            global_candidates = self._select_global_candidates(all_candidates)
        for candidate in global_candidates:
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

        original_order = {clause.clause_id: index for index, clause in enumerate(original)}
        compare_order = {clause.clause_id: index for index, clause in enumerate(compare)}
        pairs.sort(
            key=lambda pair: (
                original_order.get(pair.original.clause_id if pair.original is not None else "", len(original)),
                compare_order.get(pair.compare.clause_id if pair.compare is not None else "", len(compare)),
            )
        )

        with self._performance.measure("structural_drift_matching"):
            structural_pairs = self._match_structural_drift_candidates(
                original,
                compare,
                matched_original,
                matched_compare,
                consumed_original_spans,
                consumed_compare_spans,
            )
        pairs.extend(structural_pairs)

        with self._performance.measure("synthetic_match_recovery"):
            synthetic_pairs = self._match_unmatched_originals_inside_matched_compare(
                original,
                compare,
                pairs,
                matched_original,
                consumed_compare_spans,
            )
        if consumed_compare_spans:
            pairs = [
                pair.model_copy(update={"compare": self._redact_clause(pair.compare, consumed_compare_spans)})
                if pair.compare is not None and pair.compare.clause_id in consumed_compare_spans
                else pair
                for pair in pairs
            ]
            pairs = [pair for pair in pairs if pair.original is not None and pair.compare is not None]
        pairs.extend(synthetic_pairs)

        with self._performance.measure("contained_clause_recovery"):
            synthetic_pairs = self._match_contained_numbered_clauses(
                original,
                compare,
                matched_compare,
                consumed_original_spans,
            )
        if consumed_original_spans:
            pairs = [
                pair.model_copy(update={"original": self._redact_clause(pair.original, consumed_original_spans)})
                if pair.original is not None and pair.original.clause_id in consumed_original_spans
                else pair
                for pair in pairs
            ]
            pairs = [pair for pair in pairs if pair.original is not None and pair.compare is not None]
        pairs.extend(synthetic_pairs)

        for left in original:
            if left.clause_id not in matched_original:
                redacted = self._redact_clause(left, consumed_original_spans)
                if redacted is not None:
                    pairs.append(
                        ClausePair(
                            original=redacted,
                            compare=None,
                            match_method="delete",
                            match_candidates=self._section_mismatch_summaries(
                                candidates_by_original.get(left.clause_id, [])
                            ),
                        )
                    )
        for right in compare:
            if right.clause_id not in matched_compare:
                pairs.append(
                    ClausePair(
                        original=None,
                        compare=right,
                        match_method="add",
                        match_candidates=self._section_mismatch_summaries_for_compare(all_candidates, right.clause_id),
                    )
                )

        self._performance.set_counter("pair_count", len(pairs))
        self._performance.set_counter("matched_pair_count", sum(1 for pair in pairs if pair.original and pair.compare))
        self._performance.set_counter(
            "delete_pair_count", sum(1 for pair in pairs if pair.original and not pair.compare)
        )
        self._performance.set_counter("add_pair_count", sum(1 for pair in pairs if pair.compare and not pair.original))
        self.last_performance_metrics = self._performance.snapshot()
        return pairs


__all__ = [
    "BODY_ONLY_ALIGNMENT_RISK",
    "CRITICAL_TOKEN_CONFLICT",
    "ClauseMatcher",
    "MatchCandidate",
    "RerankMatcher",
    "SAME_KEY_LOW_BODY_COVERAGE",
    "SAME_NUMBER_LOW_BODY_SIMILARITY",
    "SECTION_PATH_MISMATCH_REVIEW",
    "SemanticMatcher",
    "_FlowEdge",
    "httpx",
]
