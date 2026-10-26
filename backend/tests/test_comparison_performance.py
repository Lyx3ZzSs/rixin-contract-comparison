from pathlib import Path

import fitz
import numpy as np
import pytest

from app.models import BBox, CharBox, Clause, Document, Page, TextBlock, TextSourceEvidence
from app.services.extractors.base import ExtractionResult
from app.services.matcher import ClauseMatcher
from app.services.matching.providers import RerankMatcher, SemanticMatcher
from app.services.matching.types import MatchCandidate
from app.services.page_alignment import PageAlignmentEntry, PageAlignmentResult, PageAlignmentStatus
from app.services.page_render_cache import PageRenderCache


def pdf(path, count=2):
    with fitz.open() as doc:
        for index in range(count):
            doc.new_page(width=100, height=100).insert_text((10, 20), f"page {index + 1}")
        doc.save(path)
    return path


def test_render_cache_is_bounded_immutable_and_separates_parameters(tmp_path):
    path = pdf(tmp_path / "doc.pdf")
    cache = PageRenderCache(35_000)
    with fitz.open(path) as doc:
        first = cache.render(doc[0], scale=1)
        assert cache.render(doc[0], scale=1) is first
        with pytest.raises(ValueError):
            first[0, 0, 0] = 1
        gray = cache.render(doc[0], scale=1, colorspace=fitz.csGRAY)
        assert gray.ndim == 2
        cache.render(doc[1], scale=1)
        again = cache.render(doc[0], scale=1)
        assert np.array_equal(first, again)
        assert cache.bytes <= 35_000
        assert cache.metrics.snapshot()["counters"]["evictions"] > 0
    cache.clear()
    assert cache.bytes == 0


def test_embedding_duplicate_inputs_keep_order_and_empty_values(monkeypatch):
    provider = SemanticMatcher(enabled=False)
    provider.enabled = True
    provider.deduplicate_inputs = True
    calls = []

    def embed(texts):
        calls.append(texts)
        return [[float(len(text))] for text in texts]

    monkeypatch.setattr(provider, "_embed_uncached", embed)
    assert provider._embeddings(["alpha", "b", "alpha", "", "b"]) == [[5.0], [1.0], [5.0], [], [1.0]]
    assert calls == [["alpha", "b"]]
    assert provider._embeddings(["b", "alpha"]) == [[1.0], [5.0]]
    assert len(calls) == 1


def test_render_cache_does_not_reuse_pixels_after_in_memory_pdf_change(tmp_path):
    path = pdf(tmp_path / "modified.pdf")
    cache = PageRenderCache()
    with fitz.open(path) as doc:
        before = cache.render(doc[0], scale=1)
        doc[0].draw_rect(fitz.Rect(0, 0, 80, 80), fill=(0, 0, 0))
        after = cache.render(doc[0], scale=1)
        assert not np.array_equal(before, after)


def clause(key, text):
    return Clause(clause_id=key, text=text, normalized_text=text, title="付款", clause_no="1", page_numbers=[1])


def test_rerank_dedup_preserves_group_order_and_candidate_order(monkeypatch):
    provider = RerankMatcher(enabled=True, base_url="http://unused", max_inflight=1)
    provider.deduplicate_inputs = True
    left, a, b = clause("o", "付款"), clause("a", "甲方"), clause("b", "乙方")
    first = [MatchCandidate(left, right, 90, "rule", {}, ()) for right in (a, b)]
    reversed_group = list(reversed(first))
    calls = []

    def score(group):
        calls.append(group)
        return [(1.0 if item.compare.clause_id == "a" else 2.0, True, "") for item in group]

    monkeypatch.setattr(provider, "score_candidates", score)
    scores = provider.score_candidate_groups([first, reversed_group, first])
    assert len(calls) == 2
    assert scores == [[(1.0, True, ""), (2.0, True, "")],
                      [(2.0, True, ""), (1.0, True, "")],
                      [(1.0, True, ""), (2.0, True, "")]]


@pytest.mark.parametrize("workers", [1, 3])
def test_rerank_without_dedup_keeps_every_group_without_building_keys(monkeypatch, workers):
    provider = RerankMatcher(enabled=True, base_url="http://unused", max_inflight=workers)
    monkeypatch.setattr(provider, "_get_http_client", lambda: None)
    monkeypatch.setattr(provider, "_rerank_text", lambda _: pytest.fail("Disabled dedup built a text key"))
    a, b = clause("a", "甲方"), clause("b", "乙方")
    group = [MatchCandidate(a, b, 90, "rule", {}, ())]
    calls = []

    def score(items):
        calls.append(items)
        return [(90.0, True, "") for _ in items]

    monkeypatch.setattr(provider, "score_candidates", score)
    assert provider.score_candidate_groups([group, [], group]) == [[(90.0, True, "")], [], [(90.0, True, "")]]
    assert len(calls) == 3


def test_exact_strategy_retains_changed_duplicate_and_uncertain_clauses():
    text = "甲方应在验收完成并收到发票后的三十日内支付合同价款10000元，乙方应提供相关凭据。"
    left, right = clause("o", text), clause("c", text)
    matcher = ClauseMatcher(performance_strategy="enabled")
    matcher.page_alignment = PageAlignmentResult((PageAlignmentEntry(
        PageAlignmentStatus.MATCHED, (1,), (1,), 1.0, (), "test"),))
    assert matcher._unique_aligned_exact_matches([left], [right]) == {0: 0}
    assert matcher._unique_aligned_exact_matches([left, clause("o2", text)], [right]) == {}
    right.text = text.replace("10000", "10001")
    assert matcher._unique_aligned_exact_matches([left], [right]) == {}
    right.text = text
    matcher.page_alignment = PageAlignmentResult((PageAlignmentEntry(
        PageAlignmentStatus.UNCERTAIN, (1,), (1,), 0.7, (), "test"),))
    assert matcher._unique_aligned_exact_matches([left], [right]) == {}


def test_shadow_diagnostics_preserve_pairs_and_reset_between_tasks():
    left = [clause(f"o{i}", f"第{i}项条款：采购方应在收到完整验收报告之后支付合同价款，供货方应保留原始凭据。") for i in range(12)]
    right = [item.model_copy(update={"clause_id": f"c{i}"}) for i, item in enumerate(left)]
    alignment = PageAlignmentResult((PageAlignmentEntry(
        PageAlignmentStatus.MATCHED, (1,), (1,), 1.0, (), "test"),))
    baseline = ClauseMatcher().match(left, right, page_alignment=alignment)
    matcher = ClauseMatcher(performance_strategy="shadow")
    actual = matcher.match(left, right, page_alignment=alignment)
    assert [p.model_dump() for p in actual] == [p.model_dump() for p in baseline]
    counters = matcher.last_performance_metrics["counters"]
    assert counters["exact_shadow_agreed"] == 12
    assert counters["exact_shadow_disagreed"] == 0
    assert counters["exact_screen_eligible"] == 12
    matcher.match(left[:1], right[:1])
    assert matcher.last_performance_metrics["counters"]["exact_shadow_agreed"] == 0


def test_exact_screen_keeps_role_value_swaps_out_of_fast_path():
    template = "{}公司应当依照本协议履行义务，并在完成验收后支付合同价款{}元。"
    left = [clause("o1", template.format("采购方甲", 12345)), clause("o2", template.format("供货方乙", 67890))]
    right = [clause("c1", template.format("采购方甲", 67890)), clause("c2", template.format("供货方乙", 12345))]
    matcher = ClauseMatcher(performance_strategy="enabled")
    matcher.page_alignment = PageAlignmentResult((PageAlignmentEntry(
        PageAlignmentStatus.MATCHED, (1,), (1,), 1.0, (), "test"),))
    assert matcher._unique_aligned_exact_matches(left, right) == {}
    assert matcher._performance.snapshot()["counters"]["exact_screen_text_changed"] == 2


def test_feature_cache_released_between_matches_after_input_mutation():
    a, b = clause("o", "验收后支付合同款10000元"), clause("c", "验收后支付合同款10000元")
    matcher = ClauseMatcher()
    matcher.match([a], [b])
    b.text = b.normalized_text = "验收后支付合同款20000元"
    actual = matcher.match([a], [b])
    expected = ClauseMatcher(feature_cache_enabled=False).match([a], [b])
    assert [p.model_dump() for p in actual] == [p.model_dump() for p in expected]
