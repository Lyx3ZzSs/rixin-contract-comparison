from __future__ import annotations

from app.services.clause_numbering import ClauseNumberParser


def test_parser_normalizes_formal_chinese_and_arabic_numbers() -> None:
    parser = ClauseNumberParser()

    chapter = parser.parse_line("第一章 总则")
    section = parser.parse_line("第一节 服务说明")
    chinese = parser.parse_line("第十一条 付款方式")
    arabic = parser.parse_line("第11条 付款方式")

    assert chapter is not None
    assert chapter.level == 1
    assert section is not None
    assert section.level == 2
    assert chinese is not None
    assert arabic is not None
    assert chinese.raw_number == "第十一条"
    assert chinese.canonical_number == "11"
    assert arabic.canonical_number == "11"
    assert chinese.level == arabic.level == 3


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

    for text in ["1000元", "6%", "2026.01.01", "2 台设备", "30个工作日"]:
        parsed = parser.parse_line(text)
        assert parser.is_non_clause_numeric(text, parsed)


def test_parser_prefers_long_quantity_units() -> None:
    parser = ClauseNumberParser()

    assert parser.quantity_or_amount_pattern.match("30个工作日内提交文件").group(0) == "30个工作日"
    assert parser.quantity_or_amount_pattern.match("3个月内完成验收").group(0) == "3个月"


def test_parser_keeps_numbered_clause_titles_that_look_like_duration_units() -> None:
    parser = ClauseNumberParser()

    for text in ["30. 个工作日提交文件", "30、个工作日提交文件", "第30条 个工作日提交文件"]:
        parsed = parser.parse_line(text)
        assert parsed is not None
        assert parsed.non_clause is False
        assert not parser.is_non_clause_numeric(text, parsed)
