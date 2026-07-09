from __future__ import annotations

from app.services.clause_alignment import ClauseAlignmentFingerprint
from app.services.clause_keys import ClauseKeyBuilder, ClauseSectionPathBuilder
from app.services.clause_numbering import ClauseNumberParser
from app.services.normalizer import TextNormalizer


def test_section_path_builder_keeps_formal_parent_for_decimal_children() -> None:
    parser = ClauseNumberParser()
    builder = ClauseSectionPathBuilder(
        number_parser=parser,
        is_formal_clause_marker=parser.is_formal_number,
    )

    assert builder.path(section_type="main_contract", clause_no="第一章", title="总则", level=1) == ["第一章 总则"]
    assert builder.path(section_type="main_contract", clause_no="第1条", title="定义", level=3) == [
        "第一章 总则",
        "第1条 定义",
    ]
    assert builder.path(section_type="main_contract", clause_no="1.1", title="服务内容", level=2) == [
        "第一章 总则",
        "第1条 定义",
        "1.1 服务内容",
    ]
    assert builder.path(section_type="main_contract", clause_no="第2条", title="付款", level=3) == [
        "第一章 总则",
        "第2条 付款",
    ]


def test_section_path_builder_preserves_explicit_zero_level() -> None:
    parser = ClauseNumberParser()
    builder = ClauseSectionPathBuilder(
        number_parser=parser,
        is_formal_clause_marker=parser.is_formal_number,
    )

    assert builder.path(section_type="main_contract", clause_no="0", title="前言", level=0) == ["0 前言"]
    assert builder.section_levels["main_contract"] == [(0, "0 前言")]


def test_clause_key_builder_canonicalizes_path_and_alignment_tokens() -> None:
    builder = ClauseKeyBuilder(
        normalizer=TextNormalizer(),
        number_parser=ClauseNumberParser(),
        title_from_text=lambda text: text.strip().splitlines()[0][:40] if text.strip() else "",
    )
    fingerprint = ClauseAlignmentFingerprint(
        clause_no_key="3.1",
        title_key="付款",
        normalized_body_key="",
        body_fingerprint="",
        critical_tokens=("amount:1000.00", "date:2026-06-30"),
        critical_token_fingerprint="",
        structure_key="main_contract",
        page_span=None,
    )

    base_key = builder.clause_key("main_contract", ["第3.1条 付款"], "", "第3.1条 付款")
    alignment_key = builder.alignment_clause_key(base_key, fingerprint)

    assert base_key == "main_contract/n3_1付款"
    assert "n3_1" in alignment_key
    assert "amount:1000.00" in alignment_key
    assert "date:2026-06-30" in alignment_key
