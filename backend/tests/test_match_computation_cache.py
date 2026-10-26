from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import random
import threading

import pytest

from app.models import Clause
from app.services.matcher import ClauseMatcher
from app.services.matching.providers import SemanticMatcher
from app.services.matching.similarity_cache import VectorSimilarityCache
from app.services.page_alignment import PageAlignmentEntry, PageAlignmentResult, PageAlignmentStatus


@pytest.mark.parametrize("max_vectors,max_pairs", [(0, 0), (1, 1), (2, 3), (4096, 100_000)])
def test_similarity_reuse_and_eviction_preserve_exact_float_results(max_vectors, max_pairs):
    rng = random.Random(41)
    vectors = [[rng.uniform(-1, 1) for _ in range(31)] for _ in range(8)]
    vectors.extend([[], [0.0] * 31, [1.0], [float("nan")], [float("inf")]])
    cache = VectorSimilarityCache(max_vectors=max_vectors, max_pairs=max_pairs)
    for left in vectors:
        for right in reversed(vectors):
            expected = SemanticMatcher._cosine_score(left, right)
            assert cache(left, right) == expected
            assert cache(left, right) == expected
            assert cache(right, left) == SemanticMatcher._cosine_score(right, left)
            assert cache.snapshot()["vectors"] <= max_vectors
            assert cache.snapshot()["pairs"] <= max_pairs
    if max_vectors == 2:
        assert cache.snapshot()["vector_evictions"] > 0
        assert cache.snapshot()["pair_evictions"] > 0


def test_evicted_vector_tokens_cannot_reuse_another_vectors_score():
    a, b, c = [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]
    cache = VectorSimilarityCache(max_vectors=2, max_pairs=10)
    assert cache(a, b) == 0.0
    assert cache(c, c) == SemanticMatcher._cosine_score(c, c)
    assert cache(a, c) == SemanticMatcher._cosine_score(a, c)
    assert cache(a, b) == 0.0


def test_similarity_scope_releases_vectors_on_error_and_recomputes_modified_inputs():
    provider = SemanticMatcher()
    left, right = [1.0, 0.0], [1.0, 0.0]
    original = provider._cosine_score
    with pytest.raises(RuntimeError, match="abort"):
        with provider.cache_similarities() as cache:
            assert provider._cosine_score(left, right) == 100.0
            assert provider._cosine_score(left, right) == 100.0
            assert cache.snapshot()["pair_hits"] == 1
            raise RuntimeError("abort")
    assert provider._cosine_score is original
    assert cache.snapshot()["vectors"] == cache.snapshot()["pairs"] == 0
    right[:] = [0.0, 1.0]
    with provider.cache_similarities():
        assert provider._cosine_score(left, right) == 0.0


def test_independent_matchers_do_not_share_vector_cache_state():
    barrier = threading.Barrier(2)

    def compare(right):
        provider = SemanticMatcher()
        with provider.cache_similarities(max_vectors=2, max_pairs=2) as cache:
            barrier.wait(timeout=5)
            result = provider._cosine_score([1.0, 0.0], right)
        assert cache.snapshot()["vectors"] == cache.snapshot()["pairs"] == 0
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(compare, ([1.0, 0.0], [0.0, 1.0]))) == [100.0, 0.0]


def test_match_scope_keys_include_current_path_and_page_values(monkeypatch):
    matcher = ClauseMatcher()
    plain = ClauseMatcher(feature_cache_enabled=False)
    clause = Clause(clause_id="same-id", text="付款", normalized_text="付款", title="付款", clause_no="1",
                    section_path=["第一条 付款"], page_numbers=[1])
    peer = clause.model_copy(deep=True)
    alignment = PageAlignmentResult((PageAlignmentEntry(
        PageAlignmentStatus.MATCHED, (1,), (1,), 0.9, (), "test",
    ),))
    plain.page_alignment = alignment

    def check(original, compare):
        assert matcher._canonical_path(clause) == plain._canonical_path(clause)
        assert matcher._page_alignment_score(clause, peer) == 0.9
        for index in range(3):
            clause.section_path = [f"第{index + 2}条 服务"]
            clause.clause_no = str(index + 2)
            clause.page_numbers = [index + 2]
            assert matcher._canonical_path(clause) == plain._canonical_path(clause)
            assert matcher._page_alignment_score(clause, peer) == 0.0
        return []

    monkeypatch.setattr(matcher, "_match_clauses", check)
    matcher.match([clause], [peer], page_alignment=alignment)
    counters = matcher.last_performance_metrics["counters"]
    assert counters["canonical_path_from_parts_cache_misses"] == 4
    assert counters["page_relationship_score_cache_misses"] == 4
    assert not hasattr(matcher._canonical_path_from_parts, "cache_info")
    assert not hasattr(matcher._page_relationship_score, "cache_info")


def test_match_error_cleans_all_caches_and_disabled_feature_avoids_cache_setup(monkeypatch):
    matcher = ClauseMatcher()
    captured = []
    factory = matcher.semantic_matcher.cache_similarities

    @contextmanager
    def capture():
        with factory() as cache:
            captured.append(cache)
            yield cache

    def fail(*args):
        matcher.semantic_matcher._cosine_score([1.0], [1.0])
        matcher._canonical_path_from_parts("", ("第一条 服务",), "1", "")
        raise RuntimeError("matching failed")

    monkeypatch.setattr(matcher.semantic_matcher, "cache_similarities", capture)
    monkeypatch.setattr(matcher, "_match_clauses", fail)
    with pytest.raises(RuntimeError, match="matching failed"):
        matcher.match([], [])
    assert captured[0].snapshot()["vectors"] == captured[0].snapshot()["pairs"] == 0
    assert not hasattr(matcher._canonical_path_from_parts, "cache_info")
    assert not hasattr(matcher._page_relationship_score, "cache_info")
    assert matcher.last_performance_metrics["counters"]["similarity_cache_peak_pairs"] == 1
    matcher.feature_cache_enabled = False
    monkeypatch.setattr(matcher, "_match_clauses", lambda *args: [])
    matcher.match([], [])
    assert len(captured) == 1


def test_lru_eviction_preserves_path_and_page_relationships(monkeypatch):
    from functools import lru_cache

    # Exercise the production match context with a deliberately tiny LRU.
    monkeypatch.setattr("app.services.matcher.lru_cache", lambda **kwargs: lru_cache(maxsize=2))
    matcher = ClauseMatcher()
    plain = ClauseMatcher(feature_cache_enabled=False)

    def check(*args):
        for _ in range(2):
            for index in range(6):
                parts = ("main_contract", (f"第{index + 1}条 服务",), str(index), "")
                assert matcher._canonical_path_from_parts(*parts) == plain._canonical_path_from_parts(*parts)
                assert matcher._page_relationship_score((index,), (index,)) == 0.0
        return []

    monkeypatch.setattr(matcher, "_match_clauses", check)
    matcher.match([], [])
    counters = matcher.last_performance_metrics["counters"]
    assert counters["canonical_path_from_parts_cache_currsize"] == 2
    assert counters["page_relationship_score_cache_currsize"] == 2
