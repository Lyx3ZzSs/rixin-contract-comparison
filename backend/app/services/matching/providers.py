from __future__ import annotations

import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from app.models import Clause
from app.services.matching.types import MatchCandidate
from app.services.pipeline_metrics import PerformanceRecorder

logger = logging.getLogger(__name__)


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
        max_inflight: int = 3,
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
        self.max_inflight = max(1, min(16, max_inflight))
        self.timeout_seconds = max(1, timeout_seconds)
        self.max_retries = max(0, max_retries)
        self.local_model = None
        self._cache: dict[str, list[float]] = {}
        self._http_client: httpx.Client | None = None
        self._http_client_lock = threading.Lock()
        self.performance_recorder: PerformanceRecorder | None = None
        if not enabled:
            return
        if self.provider == "openai":
            self._enable_openai()
        else:
            self._enable_local()

    def set_performance_recorder(self, recorder: PerformanceRecorder | None) -> None:
        self.performance_recorder = recorder

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

    def prepare_pair(
        self,
        original: list[Clause],
        compare: list[Clause],
    ) -> dict[int, list[float]]:
        """Batch both sides once while returning vectors indexed for compare."""
        compare_count = len(compare)
        vectors = self._embeddings(
            [
                *(self._semantic_text(clause) for clause in compare),
                *(self._semantic_text(clause) for clause in original),
            ]
        )
        return {index: vector for index, vector in enumerate(vectors[:compare_count]) if vector}

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
        scored = [(self._cosine_score(query_vector, vector), index) for index, vector in choices.items() if vector]
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
            vectors = self.local_model.encode(
                texts,
                normalize_embeddings=True,
                batch_size=self.batch_size,
            )
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
        batches = [texts[start : start + self.batch_size] for start in range(0, len(texts), self.batch_size)]
        worker_count = min(self.max_inflight, len(batches))
        recorder = self.performance_recorder
        if recorder is not None:
            recorder.set_counter("embedding_max_inflight", self.max_inflight)
            recorder.set_counter("embedding_parallel_worker_count", worker_count)
        if worker_count <= 1:
            batch_vectors = [self._post_openai_embeddings(batch) for batch in batches]
        else:
            self._get_http_client()
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="semantic-embedding") as executor:
                batch_vectors = list(executor.map(self._post_openai_embeddings, batches))
        vectors = [vector for batch in batch_vectors for vector in batch]
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
        recorder = self.performance_recorder
        if recorder is not None:
            recorder.increment("embedding_batch_count")
            recorder.increment("embedding_input_count", len(texts))
        for _ in range(attempts):
            try:
                if recorder is not None:
                    recorder.increment("embedding_http_request_count")
                    with recorder.measure("embedding_http_request"):
                        response = self._get_http_client().post(
                            endpoint,
                            headers=headers,
                            json=body,
                        )
                        response.raise_for_status()
                        payload = response.json()
                else:
                    response = self._get_http_client().post(
                        endpoint,
                        headers=headers,
                        json=body,
                    )
                    response.raise_for_status()
                    payload = response.json()
                return self._parse_openai_embeddings(
                    payload,
                    expected_count=len(texts),
                )
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                last_error = exc
                if recorder is not None:
                    recorder.increment("embedding_http_failure_count")
        logger.warning("Semantic matching disabled: OpenAI-compatible embedding request failed (%s).", last_error)
        self.enabled = False
        return [[] for _ in texts]

    def _get_http_client(self) -> httpx.Client:
        with self._http_client_lock:
            if self._http_client is None:
                self._http_client = httpx.Client(timeout=self.timeout_seconds)
                if self.performance_recorder is not None:
                    self.performance_recorder.increment("embedding_http_client_create_count")
            return self._http_client

    def close(self) -> None:
        with self._http_client_lock:
            client = self._http_client
            self._http_client = None
        if client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                close()

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
        max_inflight: int = 8,
        timeout_seconds: int = 30,
        max_retries: int = 1,
    ) -> None:
        self.enabled = False
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.model_name = model.strip()
        self.max_inflight = max(1, min(16, max_inflight))
        self.timeout_seconds = max(1, timeout_seconds)
        self.max_retries = max(0, max_retries)
        self._http_client: httpx.Client | None = None
        self._http_client_lock = threading.Lock()
        self.performance_recorder: PerformanceRecorder | None = None
        if not enabled:
            return
        if not self.base_url:
            logger.warning("Clause rerank is enabled but MATCH_RERANK_BASE_URL is empty.")
            return
        self.enabled = True

    def set_performance_recorder(self, recorder: PerformanceRecorder | None) -> None:
        self.performance_recorder = recorder

    def score_candidates(self, candidates: list[MatchCandidate]) -> list[tuple[float, bool, str]]:
        if not self.enabled or not candidates:
            return [(0.0, False, "") for _ in candidates]
        query = self._rerank_text(candidates[0].original)
        documents = [self._rerank_text(candidate.compare) for candidate in candidates]
        endpoint = self.base_url if self.base_url.endswith("/rerank") else f"{self.base_url}/rerank"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body: dict[str, Any] = {
            "query": query,
            "documents": documents,
            "top_n": len(documents),
        }
        if self.model_name:
            body["model"] = self.model_name
        attempts = self.max_retries + 1
        last_error: Exception | None = None
        recorder = self.performance_recorder
        if recorder is not None:
            recorder.increment("rerank_group_count")
            recorder.increment("rerank_document_count", len(documents))
        for _ in range(attempts):
            try:
                if recorder is not None:
                    recorder.increment("rerank_http_request_count")
                    with recorder.measure("rerank_http_request"):
                        response = self._get_http_client().post(
                            endpoint,
                            headers=headers,
                            json=body,
                        )
                        response.raise_for_status()
                        payload = response.json()
                else:
                    response = self._get_http_client().post(
                        endpoint,
                        headers=headers,
                        json=body,
                    )
                    response.raise_for_status()
                    payload = response.json()
                return self._parse_scores(payload, expected_count=len(candidates))
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                last_error = exc
                if recorder is not None:
                    recorder.increment("rerank_http_failure_count")
        logger.warning("Clause rerank disabled: rerank request failed (%s).", last_error)
        self.enabled = False
        return [(0.0, False, "") for _ in candidates]

    def score_candidate_groups(
        self,
        groups: list[list[MatchCandidate]],
    ) -> list[list[tuple[float, bool, str]]]:
        if not self.enabled or not groups:
            return [[(0.0, False, "") for _ in group] for group in groups]
        worker_count = min(self.max_inflight, len(groups))
        recorder = self.performance_recorder
        if recorder is not None:
            recorder.set_counter("rerank_max_inflight", self.max_inflight)
            recorder.set_counter("rerank_parallel_worker_count", worker_count)
        if worker_count <= 1:
            return [self.score_candidates(group) for group in groups]
        self._get_http_client()
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="clause-rerank") as executor:
            return list(executor.map(self.score_candidates, groups))

    def _get_http_client(self) -> httpx.Client:
        with self._http_client_lock:
            if self._http_client is None:
                self._http_client = httpx.Client(timeout=self.timeout_seconds)
                if self.performance_recorder is not None:
                    self.performance_recorder.increment("rerank_http_client_create_count")
            return self._http_client

    def close(self) -> None:
        with self._http_client_lock:
            client = self._http_client
            self._http_client = None
        if client is not None:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _parse_scores(payload: dict[str, Any], *, expected_count: int) -> list[tuple[float, bool, str]]:
        if isinstance(payload.get("results"), list):
            return RerankMatcher._parse_indexed_result_scores(payload["results"], expected_count=expected_count)

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
    def _parse_indexed_result_scores(results: list[Any], *, expected_count: int) -> list[tuple[float, bool, str]]:
        if len(results) != expected_count:
            raise ValueError("rerank response results count does not match input count")

        scores: list[tuple[float, bool, str] | None] = [None] * expected_count
        for item in results:
            if not isinstance(item, dict):
                raise ValueError("rerank response result item must be an object")
            raw_index = item.get("index")
            if not isinstance(raw_index, int) or raw_index < 0 or raw_index >= expected_count:
                raise ValueError("rerank response result item has invalid index")
            raw_score = item.get("relevance_score", item.get("score"))
            if not isinstance(raw_score, (int, float)):
                raise ValueError("rerank response result item is missing numeric score")
            score = float(raw_score)
            if 0.0 <= score <= 1.0:
                score *= 100.0
            reason = str(item.get("reason") or "")[:300]
            scores[raw_index] = (max(0.0, min(100.0, score)), True, reason)

        if any(score is None for score in scores):
            raise ValueError("rerank response result indexes are incomplete")
        return [score for score in scores if score is not None]

    @staticmethod
    def _rerank_text(clause: Clause) -> str:
        return "\n".join(part for part in [clause.title, clause.text] if part)[:1600]
