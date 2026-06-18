from __future__ import annotations

from app.services.clause_numbering import ClauseNumberParser


def test_parser_normalizes_formal_chinese_and_arabic_numbers() -> None:
    parser = ClauseNumberParser()

    chinese = parser.parse_line("第十一条 付款方式")
    arabic = parser.parse_line("第11条 付款方式")

    assert chinese is not None
    assert arabic is not None
    assert chinese.raw_number == "第十一条"
    assert chinese.canonical_number == "11"
    assert arabic.canonical_number == "11"
    assert chinese.level == arabic.level == 1


def test_parser_keeps_deep_decimal_numbering() -> None:
    parser = ClauseNumberParser()
    parsed = parser.parse_line("1.2.3.4.5 五级标题")

    assert parsed is not None
    assert parsed.raw_number == "1.2.3.4.5"
    assert parsed.canonical_number == "1.2.3.4.5"
    assert parsed.level == 5
    assert parsed.title == "五级标题"


def test_parser_recognizes_common_nested_numbering_styles() -> None:
    parser = ClauseNumberParser()

    parenthesized = parser.parse_line("（一）服务内容")
    circled = parser.parse_line("①服务内容")

    assert parenthesized is not None
    assert parenthesized.canonical_number == "1"
    assert parenthesized.style == "parenthesized"
    assert circled is not None
    assert circled.canonical_number == "1"
    assert circled.style == "circled"


def test_parser_marks_amount_date_and_quantity_as_non_clause_numbers() -> None:
    parser = ClauseNumberParser()

    for text in ["1000元", "6%", "2026.01.01", "2 台设备"]:
        parsed = parser.parse_line(text)
        assert parser.is_non_clause_numeric(text, parsed)
