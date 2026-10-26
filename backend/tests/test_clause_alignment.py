from __future__ import annotations

from app.models import Clause
from app.services.clause_alignment import ClauseAlignmentAnalyzer


def _clause(
    text: str,
    *,
    clause_no: str = "3.1",
    title: str = "付款条款",
    section_type: str = "main_contract",
    page_numbers: list[int] | None = None,
) -> Clause:
    return Clause(
        clause_id="C001",
        clause_no=clause_no,
        title=title,
        text=text,
        normalized_text=text,
        match_text=text,
        section_type=section_type,
        page_numbers=page_numbers if page_numbers is not None else [1],
    )


def test_fingerprint_normalizes_format_noise_and_keeps_critical_tokens() -> None:
    analyzer = ClauseAlignmentAnalyzer()
    left = analyzer.fingerprint(
        _clause("甲方应在（2026年6月30日）前支付人民币 1,000.00 元；税率：6%。")
    )
    right = analyzer.fingerprint(
        _clause("甲方应在 2026 年 6 月 30 日前支付人民币1000.00元 税率6%")
    )

    assert left.clause_no_key == "3.1"
    assert left.title_key == right.title_key
    assert "人民币1000.00元" in left.normalized_body_key
    assert "人民币100000元" not in left.normalized_body_key
    assert left.body_fingerprint == right.body_fingerprint
    assert isinstance(left.critical_tokens, tuple)
    assert left.critical_tokens == tuple(sorted(left.critical_tokens))
    assert left.critical_token_fingerprint == right.critical_token_fingerprint
    assert left.critical_token_fingerprint == "|".join(left.critical_tokens)
    assert "amount:1000" in left.critical_tokens
    assert "date:2026-06-30" in left.critical_tokens
    assert "percent:6%" in left.critical_tokens


def test_fingerprint_extracts_contract_number_quantity_and_page_span() -> None:
    analyzer = ClauseAlignmentAnalyzer()

    fingerprint = analyzer.fingerprint(
        _clause(
            "合同编号：HT-2026-001，乙方应交付10台设备。",
            clause_no="",
            title="交付",
            section_type=" Main-Contract：",
            page_numbers=[2, 3],
        )
    )

    assert "contract_no:HT-2026-001" in fingerprint.critical_tokens
    assert "quantity:10台" in fingerprint.critical_tokens
    assert fingerprint.page_span == (2, 3)
    assert fingerprint.structure_key == "maincontract"


def test_title_key_normalizes_whitespace_nfkc_and_punctuation_noise() -> None:
    analyzer = ClauseAlignmentAnalyzer()

    noisy = analyzer.fingerprint(_clause("正文", title="付款 条款："))
    clean = analyzer.fingerprint(_clause("正文", title="付款条款"))

    assert noisy.title_key == clean.title_key


def test_empty_number_and_title_do_not_report_as_matches() -> None:
    analyzer = ClauseAlignmentAnalyzer()

    diagnostics = analyzer.diagnostics(
        _clause("甲方提供服务。", clause_no="", title=""),
        _clause("乙方提供服务。", clause_no="", title=""),
    )

    assert diagnostics["number_match"] is False
    assert diagnostics["title_match"] is False


def test_page_span_handles_unsorted_and_empty_page_numbers() -> None:
    analyzer = ClauseAlignmentAnalyzer()

    unsorted = analyzer.fingerprint(_clause("正文", page_numbers=[3, 2]))
    empty = analyzer.fingerprint(_clause("正文", page_numbers=[]))

    assert unsorted.page_span == (2, 3)
    assert empty.page_span is None


def test_fingerprint_extracts_amount_terms_parties_and_normalizes_clause_no() -> None:
    analyzer = ClauseAlignmentAnalyzer()

    fingerprint = analyzer.fingerprint(
        _clause(
            "甲方：北京示例科技有限公司，乙方为上海样例贸易有限公司。"
            "应支付1000元，期限为12个月，有效期3年，"
            "服务期为30日，服务期为30天，服务期为30个工作日。",
            clause_no="第3.1条",
        )
    )

    assert fingerprint.clause_no_key == "3.1"
    assert "amount:1000" in fingerprint.critical_tokens
    assert "term:12个月" in fingerprint.critical_tokens
    assert "term:3年" in fingerprint.critical_tokens
    assert "term:30日" in fingerprint.critical_tokens
    assert "term:30天" in fingerprint.critical_tokens
    assert "term:30个工作日" in fingerprint.critical_tokens
    assert "party:甲方:北京示例科技有限公司" in fingerprint.critical_tokens
    assert "party:乙方:上海样例贸易有限公司" in fingerprint.critical_tokens


def test_amount_tokens_accept_optional_prefix_without_matching_quantity() -> None:
    analyzer = ClauseAlignmentAnalyzer()

    no_prefix = analyzer.fingerprint(_clause("乙方应支付1000元，并交付10台设备。"))
    rmb_prefix = analyzer.fingerprint(_clause("乙方应支付人民币1000元。"))
    amount_prefix = analyzer.fingerprint(_clause("金额为1000元。"))

    assert "amount:1000" in no_prefix.critical_tokens
    assert "amount:1000" in rmb_prefix.critical_tokens
    assert "amount:1000" in amount_prefix.critical_tokens
    assert "amount:10" not in no_prefix.critical_tokens
    assert "quantity:10台" in no_prefix.critical_tokens


def test_amount_tokens_keep_unit_specific_fingerprint() -> None:
    analyzer = ClauseAlignmentAnalyzer()

    yuan = analyzer.fingerprint(_clause("合同价款1000元。"))
    ten_thousand_yuan = analyzer.fingerprint(_clause("合同价款1000万元。"))

    assert "amount:1000" in yuan.critical_tokens
    assert "amount:1000" in ten_thousand_yuan.critical_tokens
    assert "amount_unit:1000元" in yuan.critical_tokens
    assert "amount_unit:1000万元" in ten_thousand_yuan.critical_tokens
    assert set(yuan.critical_tokens) != set(ten_thousand_yuan.critical_tokens)


def test_amount_tokens_normalize_equivalent_decimal_formatting() -> None:
    analyzer = ClauseAlignmentAnalyzer()

    formatted = analyzer.fingerprint(_clause("甲方应支付人民币1,000.00元。"))
    plain = analyzer.fingerprint(_clause("甲方应支付人民币1000元。"))

    assert formatted.critical_tokens == plain.critical_tokens
    assert "amount:1000" in formatted.critical_tokens
    assert "amount_unit:1000元" in formatted.critical_tokens


def test_formal_chinese_and_arabic_clause_numbers_share_alignment_key() -> None:
    analyzer = ClauseAlignmentAnalyzer()

    diagnostics = analyzer.diagnostics(
        _clause("甲方应付款。", clause_no="第十一条"),
        _clause("甲方应付款。", clause_no="第11条"),
    )

    assert diagnostics["number_match"] is True
    assert diagnostics["risk_flags"] == []


def test_contract_number_accepts_space_or_no_separator() -> None:
    analyzer = ClauseAlignmentAnalyzer()

    spaced = analyzer.fingerprint(_clause("合同编号 HT-2026-002"))
    compact = analyzer.fingerprint(_clause("编号HT-2026-003"))

    assert "contract_no:HT-2026-002" in spaced.critical_tokens
    assert "contract_no:HT-2026-003" in compact.critical_tokens


def test_text_similarity_handles_empty_and_partial_boundaries() -> None:
    analyzer = ClauseAlignmentAnalyzer()

    assert analyzer.text_similarity("", "") == 1.0
    assert analyzer.text_similarity("", "付款") == 0.0
    assert analyzer.text_similarity("abc", "abd") == 0.6667
    assert analyzer.text_similarity("甲", "乙") == 0.0


def test_token_overlap_handles_empty_and_set_boundaries() -> None:
    analyzer = ClauseAlignmentAnalyzer()

    assert analyzer.token_overlap((), ()) == 1.0
    assert analyzer.token_overlap(("amount:1000",), ()) == 0.0
    assert analyzer.token_overlap(("a", "b"), ("b", "c")) == 0.3333
    assert analyzer.token_overlap(("a",), ("b",)) == 0.0


def test_alignment_diagnostics_reports_match_signals() -> None:
    analyzer = ClauseAlignmentAnalyzer()
    left = _clause("甲方应在2026年6月30日前支付人民币1000元。")
    right = _clause("甲方应在2026年6月30日前支付人民币1000元。")

    diagnostics = analyzer.diagnostics(left, right)

    assert diagnostics["number_match"] is True
    assert diagnostics["title_match"] is True
    assert diagnostics["body_similarity"] == 1.0
    assert diagnostics["critical_token_overlap"] == 1.0
    assert diagnostics["section_type_match"] is True
    assert diagnostics["risk_flags"] == []


def test_alignment_diagnostics_reports_page_distance() -> None:
    analyzer = ClauseAlignmentAnalyzer()

    assert analyzer.diagnostics(_clause("正文", page_numbers=[2]), _clause("正文", page_numbers=[2]))["page_distance"] == 0
    assert analyzer.diagnostics(_clause("正文", page_numbers=[2, 3]), _clause("正文", page_numbers=[3, 4]))["page_distance"] == 0
    assert analyzer.diagnostics(_clause("正文", page_numbers=[]), _clause("正文", page_numbers=[1]))["page_distance"] is None
    assert analyzer.diagnostics(_clause("正文", page_numbers=[1, 2]), _clause("正文", page_numbers=[5, 6]))["page_distance"] == 3
