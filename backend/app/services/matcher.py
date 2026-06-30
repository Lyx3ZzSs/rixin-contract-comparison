from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
import logging
import math
from typing import Any

import httpx

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - fallback for minimal environments
    fuzz = None

from app.models import Clause, ClausePair
from app.services.clause_alignment import ClauseAlignmentAnalyzer
from app.services.clause_numbering import ClauseNumberParser
from app.services.normalizer import TextNormalizer

logger = logging.getLogger(__name__)

SAME_KEY_LOW_BODY_COVERAGE = "SAME_KEY_LOW_BODY_COVERAGE"
CRITICAL_TOKEN_CONFLICT = "CRITICAL_TOKEN_CONFLICT"
SAME_NUMBER_LOW_BODY_SIMILARITY = "SAME_NUMBER_LOW_BODY_SIMILARITY"
BODY_ONLY_ALIGNMENT_RISK = "BODY_ONLY_ALIGNMENT_RISK"


@dataclass(frozen=True)
class MatchCandidate:
    original: Clause
    compare: Clause
    score: float
    method: str
    details: dict[str, Any]
    sources: tuple[str, ...] = ()


@dataclass
class _FlowEdge:
    to: int
    reverse: int
    capacity: int
    cost: int


class SemanticMatcher:
    """Optional embedding scorer used only for candidate recall and tie-breaking."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        provider: str = "local",
        model_path: str = "",
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        device: str = "auto",
        batch_size: int = 32,
        timeout_seconds: int = 60,
        max_retries: int = 2,
    ) -> None:
        self.enabled = False
        self.provider = provider.strip().lower() or "local"
        self.model_path = model_path.strip()
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.model_name = model.strip()
        self.device = device.strip() or "auto"
        self.batch_size = max(1, batch_size)
        self.timeout_seconds = max(1, timeout_seconds)
        self.max_retries = max(0, max_retries)
        self.local_model = None
        self._cache: dict[str, list[float]] = {}
        if not enabled:
            return
        if self.provider == "openai":
            self._enable_openai()
        else:
            self._enable_local()

    def _enable_local(self) -> None:
        if not self.model_path:
            logger.warning("Semantic matching is enabled but MATCH_SEMANTIC_MODEL_PATH is empty.")
            return
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:
            logger.warning("Semantic matching disabled: sentence-transformers is not installed (%s).", exc)
            return
        try:
            kwargs: dict[str, str] = {}
            if self.device != "auto":
                kwargs["device"] = self.device
            self.local_model = SentenceTransformer(self.model_path, **kwargs)
        except Exception as exc:
            logger.warning("Semantic matching disabled: failed to load local model '%s' (%s).", self.model_path, exc)
            return
        self.enabled = True

    def _enable_openai(self) -> None:
        if not self.base_url:
            logger.warning("Semantic matching is enabled but MATCH_SEMANTIC_BASE_URL is empty.")
            return
        if not self.model_name:
            logger.warning("Semantic matching is enabled but MATCH_SEMANTIC_MODEL is empty.")
            return
        self.enabled = True

    def prepare(self, clauses: list[Clause]) -> dict[int, list[float]]:
        if not self.enabled:
            return {}
        texts = [self._semantic_text(clause) for clause in clauses]
        vectors = self._embeddings(texts)
        return {index: vector for index, vector in enumerate(vectors) if vector}

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

    def _embeddings(self, texts: list[str]) -> list[list[float]]:
        if not self.enabled:
            return [[] for _ in texts]
        results: list[list[float] | None] = []
        missing: list[str] = []
        for text in texts:
            if not text:
                results.append([])
            elif text in self._cache:
                results.append(self._cache[text])
            else:
                results.append(None)
                missing.append(text)
        if missing:
            embedded = self._embed_uncached(missing)
            for text, vector in zip(missing, embedded, strict=False):
                self._cache[text] = vector
        return [
            self._cache.get(text, []) if result is None else result
            for text, result in zip(texts, results, strict=False)
        ]

    def _embed_uncached(self, texts: list[str]) -> list[list[float]]:
        if self.provider == "openai":
            return self._embed_openai(texts)
        return self._embed_local(texts)

    def _embed_local(self, texts: list[str]) -> list[list[float]]:
        if not self.enabled or self.local_model is None:
            return [[] for _ in texts]
        try:
            vectors = self.local_model.encode(texts, normalize_embeddings=True, batch_size=self.batch_size)
        except TypeError:
            vectors = self.local_model.encode(texts, normalize_embeddings=True)
        except Exception as exc:
            logger.warning("Semantic matching disabled: local embedding failed (%s).", exc)
            self.enabled = False
            return [[] for _ in texts]
        return [[float(item) for item in vector] for vector in vectors]

    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        if not self.enabled or not texts:
            return [[] for _ in texts]
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            vectors.extend(self._post_openai_embeddings(batch))
        if len(vectors) != len(texts):
            logger.warning("Semantic matching disabled: embedding response count mismatch.")
            self.enabled = False
            return [[] for _ in texts]
        return vectors

    def _post_openai_embeddings(self, texts: list[str]) -> list[list[float]]:
        endpoint = self.base_url if self.base_url.endswith("/embeddings") else f"{self.base_url}/embeddings"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {"model": self.model_name, "input": texts}
        attempts = self.max_retries + 1
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(endpoint, headers=headers, json=body)
                    response.raise_for_status()
                    return self._parse_openai_embeddings(response.json(), expected_count=len(texts))
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                last_error = exc
        logger.warning("Semantic matching disabled: OpenAI-compatible embedding request failed (%s).", last_error)
        self.enabled = False
        return [[] for _ in texts]

    @staticmethod
    def _parse_openai_embeddings(payload: dict[str, Any], *, expected_count: int) -> list[list[float]]:
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected_count:
            raise ValueError("embedding response data count does not match input count")
        vectors: list[list[float]] = []
        for item in data:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(embedding, list):
                raise ValueError("embedding response item is missing embedding list")
            vectors.append([float(value) for value in embedding])
        return vectors

    def _embedding(self, text: str) -> list[float]:
        if not self.enabled or not text:
            return []
        return self._embeddings([text])[0]

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


class RerankMatcher:
    """Optional private reranker used after candidate recall and before assignment."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        timeout_seconds: int = 30,
        max_retries: int = 1,
    ) -> None:
        self.enabled = False
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.model_name = model.strip()
        self.timeout_seconds = max(1, timeout_seconds)
        self.max_retries = max(0, max_retries)
        if not enabled:
            return
        if not self.base_url:
            logger.warning("Clause rerank is enabled but MATCH_RERANK_BASE_URL is empty.")
            return
        self.enabled = True

    def score_candidates(self, candidates: list[MatchCandidate]) -> list[tuple[float, bool, str]]:
        if not self.enabled or not candidates:
            return [(0.0, False, "") for _ in candidates]
        pairs = [
            {
                "original": self._rerank_text(candidate.original),
                "compare": self._rerank_text(candidate.compare),
            }
            for candidate in candidates
        ]
        endpoint = self.base_url if self.base_url.endswith("/rerank") else f"{self.base_url}/rerank"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body: dict[str, Any] = {"pairs": pairs}
        if self.model_name:
            body["model"] = self.model_name
        attempts = self.max_retries + 1
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(endpoint, headers=headers, json=body)
                    response.raise_for_status()
                    return self._parse_scores(response.json(), expected_count=len(candidates))
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                last_error = exc
        logger.warning("Clause rerank disabled: rerank request failed (%s).", last_error)
        self.enabled = False
        return [(0.0, False, "") for _ in candidates]

    @staticmethod
    def _parse_scores(payload: dict[str, Any], *, expected_count: int) -> list[tuple[float, bool, str]]:
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected_count:
            raise ValueError("rerank response data count does not match input count")
        scores: list[tuple[float, bool, str]] = []
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("rerank response item must be an object")
            raw_score = item.get("score")
            if not isinstance(raw_score, (int, float)):
                raise ValueError("rerank response item is missing numeric score")
            score = float(raw_score)
            if 0.0 <= score <= 1.0:
                score *= 100.0
            reason = str(item.get("reason") or "")[:300]
            scores.append((max(0.0, min(100.0, score)), True, reason))
        return scores

    @staticmethod
    def _rerank_text(clause: Clause) -> str:
        return "\n".join(part for part in [clause.title, clause.text] if part)[:1600]


class ClauseMatcher:
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
        semantic_timeout_seconds: int = 60,
        semantic_max_retries: int = 2,
        semantic_weight: float = 0.08,
        enable_rerank: bool = False,
        rerank_base_url: str = "",
        rerank_api_key: str = "",
        rerank_model: str = "",
        rerank_top_k: int = 30,
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
            timeout_seconds=semantic_timeout_seconds,
            max_retries=semantic_max_retries,
        )
        self.rerank_matcher = RerankMatcher(
            enabled=enable_rerank,
            base_url=rerank_base_url,
            api_key=rerank_api_key,
            model=rerank_model,
            timeout_seconds=rerank_timeout_seconds,
            max_retries=rerank_max_retries,
        )

    def match(self, original: list[Clause], compare: list[Clause]) -> list[ClausePair]:
        pairs: list[ClausePair] = []
        matched_original: set[str] = set()
        matched_compare: set[str] = set()
        consumed_original_spans: dict[str, list[tuple[int, int]]] = {}
        consumed_compare_spans: dict[str, list[tuple[int, int]]] = {}

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

        structural_pairs = self._match_structural_drift_candidates(
            original,
            compare,
            matched_original,
            matched_compare,
            consumed_original_spans,
            consumed_compare_spans,
        )
        pairs.extend(structural_pairs)

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
                            match_candidates=self._section_mismatch_summaries(candidates_by_original.get(left.clause_id, [])),
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

        return pairs

    def _select_global_candidates(self, candidates: list[MatchCandidate]) -> list[MatchCandidate]:
        acceptable = [candidate for candidate in candidates if self._candidate_acceptable(candidate)]
        if self.assignment_strategy == "optimal":
            try:
                selected = self._select_optimal_candidates(acceptable)
            except Exception:
                logger.warning("Optimal clause assignment failed; falling back to greedy selection.", exc_info=True)
                return self._select_greedy_candidates(acceptable)
            if acceptable and not selected:
                logger.warning("Optimal clause assignment returned no matches; falling back to greedy selection.")
                return self._select_greedy_candidates(acceptable)
            return selected
        return self._select_greedy_candidates(acceptable)

    def _select_greedy_candidates(self, acceptable: list[MatchCandidate]) -> list[MatchCandidate]:
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

    def _select_optimal_candidates(self, candidates: list[MatchCandidate]) -> list[MatchCandidate]:
        if not candidates:
            return []
        best_by_pair: dict[tuple[str, str], MatchCandidate] = {}
        for candidate in candidates:
            key = (candidate.original.clause_id, candidate.compare.clause_id)
            existing = best_by_pair.get(key)
            if existing is None or candidate.score > existing.score:
                best_by_pair[key] = candidate
        sparse_candidates = list(best_by_pair.values())
        original_ids = sorted({candidate.original.clause_id for candidate in sparse_candidates})
        compare_ids = sorted({candidate.compare.clause_id for candidate in sparse_candidates})
        original_index = {clause_id: index for index, clause_id in enumerate(original_ids)}
        compare_index = {clause_id: index for index, clause_id in enumerate(compare_ids)}
        selected_keys = self._max_weight_matching(
            [
                (
                    original_index[candidate.original.clause_id],
                    compare_index[candidate.compare.clause_id],
                    candidate.score,
                )
                for candidate in sparse_candidates
            ],
            left_count=len(original_ids),
            right_count=len(compare_ids),
        )
        by_index_pair = {
            (original_index[candidate.original.clause_id], compare_index[candidate.compare.clause_id]): candidate
            for candidate in sparse_candidates
        }
        selected = [by_index_pair[key] for key in selected_keys if key in by_index_pair]
        selected.sort(
            key=lambda item: (
                item.score,
                item.details["body_score"],
                item.details["clause_no_score"],
                item.details["title_score"],
            ),
            reverse=True,
        )
        return selected

    def _max_weight_matching(
        self,
        edges: list[tuple[int, int, float]],
        *,
        left_count: int,
        right_count: int,
    ) -> set[tuple[int, int]]:
        if not edges or left_count <= 0 or right_count <= 0:
            return set()
        source = 0
        left_start = 1
        right_start = left_start + left_count
        sink = right_start + right_count
        graph: list[list[_FlowEdge]] = [[] for _ in range(sink + 1)]

        def add_edge(left: int, right: int, capacity: int, cost: int) -> None:
            graph[left].append(_FlowEdge(right, len(graph[right]), capacity, cost))
            graph[right].append(_FlowEdge(left, len(graph[left]) - 1, 0, -cost))

        for left in range(left_count):
            add_edge(source, left_start + left, 1, 0)
        for right in range(right_count):
            add_edge(right_start + right, sink, 1, 0)
        cardinality_bonus = 100_000
        for left, right, score in edges:
            add_edge(left_start + left, right_start + right, 1, -(cardinality_bonus + int(round(score * 100))))

        selected: set[tuple[int, int]] = set()
        while True:
            distance = [10**18] * len(graph)
            previous_node = [-1] * len(graph)
            previous_edge = [-1] * len(graph)
            in_queue = [False] * len(graph)
            queue = [source]
            distance[source] = 0
            in_queue[source] = True
            while queue:
                node = queue.pop(0)
                in_queue[node] = False
                for edge_index, edge in enumerate(graph[node]):
                    if edge.capacity <= 0:
                        continue
                    next_distance = distance[node] + edge.cost
                    if next_distance < distance[edge.to]:
                        distance[edge.to] = next_distance
                        previous_node[edge.to] = node
                        previous_edge[edge.to] = edge_index
                        if not in_queue[edge.to]:
                            queue.append(edge.to)
                            in_queue[edge.to] = True
            if distance[sink] >= 0 or previous_node[sink] < 0:
                break
            node = sink
            while node != source:
                prev = previous_node[node]
                edge_index = previous_edge[node]
                edge = graph[prev][edge_index]
                edge.capacity -= 1
                graph[node][edge.reverse].capacity += 1
                node = prev

        for left in range(left_count):
            graph_node = left_start + left
            for edge in graph[graph_node]:
                if right_start <= edge.to < right_start + right_count and edge.capacity == 0:
                    selected.add((left, edge.to - right_start))
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
        return self._apply_rerank(candidates)

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
        return self._apply_rerank(candidates)

    def _apply_rerank(self, candidates: list[MatchCandidate]) -> list[MatchCandidate]:
        if not self.rerank_matcher.enabled or not candidates:
            return candidates
        rerank_by_key: dict[tuple[str, str], tuple[float, str]] = {}
        for group in self._rerank_groups(candidates):
            rerank_scores = self.rerank_matcher.score_candidates(group)
            if not any(available for _, available, _ in rerank_scores):
                continue
            for candidate, (rerank_score, available, reason) in zip(group, rerank_scores, strict=False):
                if available:
                    rerank_by_key[(candidate.original.clause_id, candidate.compare.clause_id)] = (rerank_score, reason)
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
            score = self._weighted_score(details)
            method = self._match_method(candidate.original, candidate.compare, details, score)
            reranked.append(MatchCandidate(candidate.original, candidate.compare, score, method, details, candidate.sources))
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
    ) -> dict[str, Any]:
        title_score = self._token_score(left.title, right.title) if left.title and right.title else 0.0
        body_details = self._body_score_details(self._match_text(left), self._match_text(right))
        clause_no_score = self._clause_no_score(left.clause_no, right.clause_no)
        clause_key_score = self._clause_key_score(left, right)
        canonical_path_score = self._canonical_path_score(left, right)
        section_score = self._section_score(left, right)
        section_mismatch_score = self._section_mismatch_score(left, right, body_details["body_score"], clause_no_score, title_score)
        position_score = self._position_score(original_index / original_count, compare_index / compare_count)
        neighbor_score = self._neighbor_score(original, compare, original_index, compare_index)
        business_token_score, business_mismatch = self._business_token_score(left.text, right.text, body_details["body_score"])
        details = {
            "clause_key_score": round(clause_key_score, 2),
            "canonical_path_score": round(canonical_path_score, 2),
            "clause_no_score": round(clause_no_score, 2),
            "section_score": round(section_score, 2),
            "section_mismatch_candidate": 1.0 if section_mismatch_score >= 88 else 0.0,
            "section_mismatch_score": round(section_mismatch_score, 2),
            "title_score": round(title_score, 2),
            "position_score": round(position_score, 2),
            "neighbor_score": round(neighbor_score, 2),
            "business_token_score": round(business_token_score, 2),
            "business_token_mismatch": 1.0 if business_mismatch else 0.0,
            "weak_numeric_marker": 1.0 if self._has_weak_numeric_marker(left, right) else 0.0,
            "short_clause_pair": 1.0 if self._is_short_clause(left) or self._is_short_clause(right) else 0.0,
            "semantic_score": 0.0,
            "semantic_rerank_available": 0.0,
            "semantic_rerank_score": 0.0,
            "semantic_rerank_reason": "",
            "semantic_rerank_reason_present": 0.0,
            "rerank_available": 0.0,
            "rerank_score": 0.0,
            "rerank_reason_present": 0.0,
            "assignment_strategy_optimal": 1.0 if self.assignment_strategy == "optimal" else 0.0,
        }
        details.update({key: round(value, 2) for key, value in body_details.items()})
        details["alignment"] = self.alignment_analyzer.diagnostics(left, right)
        self._apply_matcher_guard_details(details)
        return details

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

    def _weighted_score(self, details: dict[str, Any]) -> float:
        weighted = (
            details["clause_key_score"] * 0.16
            + details.get("canonical_path_score", 0.0) * 0.05
            + details["section_score"] * 0.10
            + details["clause_no_score"] * 0.14
            + details["title_score"] * 0.18
            + details["body_score"] * 0.32
            + details["business_token_score"] * 0.07
            + details["position_score"] * 0.06
            + details["neighbor_score"] * 0.05
            + details.get("semantic_score", 0.0) * self.semantic_weight
            + float(details.get("semantic_rerank_score", 0.0)) * float(details.get("semantic_rerank_available", 0.0)) * self.rerank_weight
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
        risk_flags = set(details.get("matcher_risk_flags", []))
        if SAME_KEY_LOW_BODY_COVERAGE in risk_flags:
            weighted = min(weighted, 82.0)
        if (
            CRITICAL_TOKEN_CONFLICT in risk_flags
            and self._alignment_number(details.get("alignment"), "critical_token_overlap", default=1.0) <= 0.0
        ):
            weighted = min(weighted, 84.0)
        if (
            SAME_NUMBER_LOW_BODY_SIMILARITY in risk_flags
            and details["title_score"] < 80
        ):
            weighted = min(weighted, 78.0)
        if BODY_ONLY_ALIGNMENT_RISK in risk_flags:
            weighted = min(weighted, 84.0)
        return round(max(0.0, min(100.0, weighted)), 2)

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

    def _match_method(self, left: Clause, right: Clause, details: dict[str, Any], score: float) -> str:
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

    def _canonical_path_score(self, left: Clause, right: Clause) -> float:
        left_path = self._canonical_path(left)
        right_path = self._canonical_path(right)
        if left_path and right_path and left_path == right_path:
            return 100.0
        if not left_path or not right_path:
            return 0.0
        if (left.section_type or "main_contract") != (right.section_type or "main_contract"):
            return 0.0
        return self._token_score(left_path, right_path)

    def _canonical_path(self, clause: Clause) -> str:
        parts = [clause.section_type or "main_contract"]
        for item in clause.section_path:
            parts.append(self._canonical_path_item(item))
        if clause.clause_no:
            parts.append(self._normalize_clause_no(clause.clause_no))
        elif clause.title:
            parts.append(self.normalizer.normalize_for_match(clause.title)[:32])
        return "/".join(part for part in parts if part)

    def _canonical_path_item(self, item: str) -> str:
        parsed = self.number_parser.parse_line(item)
        if parsed is not None:
            return f"{parsed.canonical_number}:{self.normalizer.normalize_for_match(parsed.title)}"
        return self.normalizer.normalize_for_match(item)

    def _section_mismatch_score(
        self,
        left: Clause,
        right: Clause,
        body_score: float,
        clause_no_score: float,
        title_score: float,
    ) -> float:
        if (left.section_type or "main_contract") == (right.section_type or "main_contract"):
            return 0.0
        score = body_score
        if clause_no_score >= 100:
            score = max(score, 90.0)
        if title_score >= 92 and body_score >= 80:
            score = max(score, 88.0)
        return score

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
        left_raw = (left.clause_no or "").strip()
        right_raw = (right.clause_no or "").strip()
        return bool(
            self._normalize_clause_no(left.clause_no)
            and self._normalize_clause_no(left.clause_no) == self._normalize_clause_no(right.clause_no)
            and re.fullmatch(r"\d+", left_raw)
            and re.fullmatch(r"\d+", right_raw)
            and (len(left.title.strip()) < 4 or len(right.title.strip()) < 4)
        )

    def _has_weak_titles(self, left: Clause, right: Clause) -> bool:
        return self._weak_title(left.title) or self._weak_title(right.title)

    def _is_short_clause(self, clause: Clause) -> bool:
        compact = self._match_text(clause)
        return 0 < len(compact) < 20

    def _weak_title(self, title: str) -> bool:
        compact = re.sub(r"[\s、.．:：]+", "", title or "")
        return not compact or bool(re.fullmatch(r"\d{1,3}", compact))

    def _same_clause_no(self, left: str, right: str) -> bool:
        left_norm = self._normalize_clause_no(left)
        right_norm = self._normalize_clause_no(right)
        return bool(left_norm and right_norm and left_norm == right_norm)

    def _normalize_clause_no(self, value: str) -> str:
        return self.number_parser.normalize_number(value)

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

    def _match_structural_drift_candidates(
        self,
        original: list[Clause],
        compare: list[Clause],
        matched_original: set[str],
        matched_compare: set[str],
        consumed_original_spans: dict[str, list[tuple[int, int]]],
        consumed_compare_spans: dict[str, list[tuple[int, int]]],
    ) -> list[ClausePair]:
        pairs: list[ClausePair] = []
        pairs.extend(
            self._match_many_originals_to_one_compare(
                original,
                compare,
                matched_original,
                matched_compare,
                consumed_compare_spans,
            )
        )
        pairs.extend(
            self._match_one_original_to_many_compares(
                original,
                compare,
                matched_original,
                matched_compare,
                consumed_original_spans,
            )
        )
        return pairs

    def _match_many_originals_to_one_compare(
        self,
        original: list[Clause],
        compare: list[Clause],
        matched_original: set[str],
        matched_compare: set[str],
        consumed_compare_spans: dict[str, list[tuple[int, int]]],
    ) -> list[ClausePair]:
        pairs: list[ClausePair] = []
        synthetic_index = 1
        for right in compare:
            if right.clause_id in matched_compare:
                continue
            matches: list[tuple[Clause, int, int, float]] = []
            local_spans: list[tuple[int, int]] = []
            for left in original:
                if left.clause_id in matched_original:
                    continue
                if (left.section_type or "main_contract") != (right.section_type or "main_contract"):
                    continue
                if not self._eligible_for_containment(left):
                    continue
                span = self._best_span_for_clause_in_text(left, right.text, local_spans)
                if span is None:
                    continue
                start, end, score = span
                matches.append((left, start, end, score))
                local_spans.append((start, end))
            if len(matches) < 2:
                continue
            matches.sort(key=lambda item: (item[1], item[0].order_index, item[0].clause_id))
            group_pairs: list[tuple[ClausePair, Clause, tuple[int, int]]] = []
            for left, start, end, score in matches:
                right_slice = self._slice_clause(right, start, end, f"{right.clause_id}M{synthetic_index:02d}")
                if right_slice is None:
                    continue
                synthetic_index += 1
                group_pairs.append(
                    (
                        ClausePair(
                            original=left,
                            compare=right_slice,
                            score=score,
                            match_method="merged_compare",
                            score_details=self._structural_drift_score_details(
                                score,
                                body_length_coverage=self._body_length_coverage(left.text, right_slice.text),
                                merged=True,
                            ),
                            match_confidence="MEDIUM",
                        ),
                        left,
                        (start, end),
                    )
                )
            if len(group_pairs) < 2:
                continue
            for pair, left, span in group_pairs:
                pairs.append(pair)
                matched_original.add(left.clause_id)
                consumed_compare_spans.setdefault(right.clause_id, []).append(span)
            matched_compare.add(right.clause_id)
        return pairs

    def _match_one_original_to_many_compares(
        self,
        original: list[Clause],
        compare: list[Clause],
        matched_original: set[str],
        matched_compare: set[str],
        consumed_original_spans: dict[str, list[tuple[int, int]]],
    ) -> list[ClausePair]:
        pairs: list[ClausePair] = []
        synthetic_index = 1
        for left in original:
            if left.clause_id in matched_original:
                continue
            matches: list[tuple[Clause, int, int, float]] = []
            local_spans: list[tuple[int, int]] = []
            for right in compare:
                if right.clause_id in matched_compare:
                    continue
                if (left.section_type or "main_contract") != (right.section_type or "main_contract"):
                    continue
                if not self._eligible_for_containment(right):
                    continue
                span = self._best_span_for_clause_in_text(right, left.text, local_spans)
                if span is None:
                    continue
                start, end, score = span
                matches.append((right, start, end, score))
                local_spans.append((start, end))
            if len(matches) < 2:
                continue
            matches.sort(key=lambda item: (item[1], item[0].order_index, item[0].clause_id))
            group_pairs: list[tuple[ClausePair, Clause, tuple[int, int]]] = []
            for right, start, end, score in matches:
                left_slice = self._slice_clause(left, start, end, f"{left.clause_id}P{synthetic_index:02d}")
                if left_slice is None:
                    continue
                synthetic_index += 1
                group_pairs.append(
                    (
                        ClausePair(
                            original=left_slice,
                            compare=right,
                            score=score,
                            match_method="split_original",
                            score_details=self._structural_drift_score_details(
                                score,
                                body_length_coverage=self._body_length_coverage(left_slice.text, right.text),
                                split=True,
                            ),
                            match_confidence="MEDIUM",
                        ),
                        right,
                        (start, end),
                    )
                )
            if len(group_pairs) < 2:
                continue
            for pair, right, span in group_pairs:
                pairs.append(pair)
                matched_compare.add(right.clause_id)
                consumed_original_spans.setdefault(left.clause_id, []).append(span)
            matched_original.add(left.clause_id)
        return pairs

    def _best_span_for_clause_in_text(
        self,
        needle: Clause,
        haystack_text: str,
        existing_spans: list[tuple[int, int]],
    ) -> tuple[int, int, float] | None:
        best: tuple[int, int, float] | None = None
        for needle_text in self._coverage_texts(needle):
            span = self._find_contained_span(needle_text, haystack_text)
            if span is None or self._overlaps_existing(span, existing_spans):
                continue
            if best is None or span[2] > best[2]:
                best = span
        return best

    def _structural_drift_score_details(
        self,
        score: float,
        *,
        body_length_coverage: float,
        merged: bool = False,
        split: bool = False,
    ) -> dict[str, Any]:
        rounded = round(score, 2)
        return {
            "clause_no_score": 0.0,
            "title_score": 0.0,
            "body_score": rounded,
            "body_ratio_score": rounded,
            "body_token_score": rounded,
            "body_partial_score": rounded,
            "body_length_coverage": round(body_length_coverage, 4),
            "position_score": 0.0,
            "neighbor_score": 0.0,
            "contained_span_score": rounded,
            "structural_drift_candidate": 1.0,
            "partial_clause_match": 1.0,
            "merged_compare_clause": 1.0 if merged else 0.0,
            "split_original_clause": 1.0 if split else 0.0,
        }

    def _body_length_coverage(self, left_text: str, right_text: str) -> float:
        left, _ = self._search_text(left_text)
        right, _ = self._search_text(right_text)
        max_len = max(len(left), len(right))
        return min(len(left), len(right)) / max_len if max_len else 1.0

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

    def _match_unmatched_originals_inside_matched_compare(
        self,
        original: list[Clause],
        compare: list[Clause],
        pairs: list[ClausePair],
        matched_original: set[str],
        consumed_compare_spans: dict[str, list[tuple[int, int]]],
    ) -> list[ClausePair]:
        matched_compare_clauses = [
            pair.compare
            for pair in pairs
            if pair.original is not None and pair.compare is not None
        ]
        if not matched_compare_clauses:
            return []

        synthetic_pairs: list[ClausePair] = []
        synthetic_index = 1
        compare_order = {clause.clause_id: index for index, clause in enumerate(compare)}
        for left in original:
            if left.clause_id in matched_original:
                continue
            if not self._eligible_for_containment(left):
                continue
            match = self._best_compare_containment(left, matched_compare_clauses, consumed_compare_spans)
            if match is None:
                continue
            right, start, end, score = match
            right_slice = self._slice_clause(right, start, end, f"{right.clause_id}S{synthetic_index:02d}")
            if right_slice is None:
                continue
            synthetic_index += 1
            synthetic_pairs.append(
                ClausePair(
                    original=left,
                    compare=right_slice,
                    score=score,
                    match_method="contained_compare",
                    score_details={
                        "clause_no_score": 0.0,
                        "title_score": self._score(left.title, right_slice.title) if left.title and right_slice.title else 0.0,
                        "body_score": round(score, 2),
                        "body_ratio_score": round(score, 2),
                        "body_token_score": round(score, 2),
                        "body_partial_score": round(score, 2),
                        "body_length_coverage": 1.0,
                        "position_score": self._position_score(
                            original.index(left) / max(1, len(original) - 1),
                            compare_order.get(right.clause_id, len(compare)) / max(1, len(compare) - 1),
                        ),
                        "neighbor_score": 0.0,
                        "contained_in_matched_compare": 1.0,
                    },
                    match_confidence="MEDIUM",
                )
            )
            consumed_compare_spans.setdefault(right.clause_id, []).append((start, end))
            matched_original.add(left.clause_id)
        return synthetic_pairs

    def _best_compare_containment(
        self,
        needle: Clause,
        haystacks: list[Clause],
        consumed_compare_spans: dict[str, list[tuple[int, int]]],
    ) -> tuple[Clause, int, int, float] | None:
        best: tuple[Clause, int, int, float] | None = None
        for right in haystacks:
            if (needle.section_type or "main_contract") != (right.section_type or "main_contract"):
                continue
            span = None
            for needle_text in self._coverage_texts(needle):
                span = self._find_contained_span(needle_text, right.text)
                if span is not None:
                    break
            if span is None or self._overlaps_existing(span, consumed_compare_spans.get(right.clause_id, [])):
                continue
            start, end, score = span
            if best is None or score > best[3]:
                best = (right, start, end, score)
        return best

    def _containment_texts(self, clause: Clause) -> list[str]:
        texts = [clause.text, self._strip_clause_prefix_only(clause.text)]
        stripped = (clause.text or "").strip()
        title = (clause.title or "").strip()
        if title and stripped.startswith(title):
            without_title = stripped[len(title):].lstrip("\n\r:： \t")
            if without_title and without_title != stripped:
                texts.append(without_title)
        if title:
            compact_title = self.normalizer.normalize_for_match(title)
            for line in stripped.splitlines():
                compact_line = self.normalizer.normalize_for_match(line)
                if compact_line and compact_line != compact_title and compact_title not in compact_line:
                    texts.append(line)
        return list(dict.fromkeys(text for text in texts if text))

    def _coverage_texts(self, clause: Clause) -> list[str]:
        texts = [*self._containment_texts(clause), *self._paragraph_sentence_fragments(clause.text)]
        return list(dict.fromkeys(text for text in texts if len(self._search_text(text)[0]) >= 8))

    def _paragraph_sentence_fragments(self, text: str) -> list[str]:
        fragments: list[str] = []
        for paragraph in re.split(r"[\n\r]+", text or ""):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            fragments.append(paragraph)
            sentence_parts = re.split(r"(?<=[。；;.!?！？])\s*|[；;]\s*", paragraph)
            for sentence in sentence_parts:
                sentence = sentence.strip()
                if sentence and sentence != paragraph:
                    fragments.append(sentence)
        return fragments

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
        min_exact_len = 8 if len(needle) < 20 else 1
        if exact >= 0 and len(needle) >= min_exact_len:
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

    def _eligible_for_containment(self, clause: Clause) -> bool:
        compact_len = len(self._search_text(clause.text)[0])
        if compact_len >= 20:
            return True
        if compact_len < 8:
            return False
        return bool(clause.clause_no or len(self.normalizer.normalize_for_match(clause.title)) >= 2 or self._has_segmentation_flag(clause))

    @staticmethod
    def _has_segmentation_flag(clause: Clause) -> bool:
        return any(
            flag in {"PARAGRAPH_MERGED", "WEAK_NUMERIC_MARKER", "WEAK_HEADING", "READING_ORDER_REPAIRED"}
            for flag in clause.split_flags
        )

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
        parsed = self.number_parser.parse_line(first_line)
        if parsed is None:
            return stripped
        first_line_body = first_line[len(parsed.marker_text) :].lstrip("、.． 　")
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
