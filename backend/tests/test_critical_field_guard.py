from __future__ import annotations

from app.services.diff.critical_field_guard import (
    critical_field_diff_types,
    critical_field_review_flags,
)


def test_detects_amount_change_from_numeric_snippet_and_full_context() -> None:
    assert critical_field_diff_types(
        "甲方应支付人民币1000元。",
        "甲方应支付人民币5000元。",
        "1000",
        "5000",
    ) == ["AMOUNT"]


def test_detects_one_sided_amount_deletion_in_modify_diff() -> None:
    assert critical_field_diff_types(
        "付款金额为1000元。",
        "付款金额为元。",
        "1000",
        "",
    ) == ["AMOUNT"]


def test_numeric_snippet_does_not_pull_unrelated_duration_with_same_digits() -> None:
    assert critical_field_diff_types(
        "付款金额为1000元,宽限期1000日。",
        "付款金额为5000元,宽限期1000日。",
        "1000",
        "5000",
    ) == ["AMOUNT"]


def test_detects_multiple_field_types_from_concatenated_snippets() -> None:
    assert critical_field_diff_types(
        "付款金额为1000元，期限1000日，甲方负责。",
        "付款金额为5000元，期限1000日，乙方负责。",
        "1000甲",
        "5000乙",
    ) == ["AMOUNT", "PARTY_ROLE"]


def test_detects_date_change() -> None:
    assert critical_field_diff_types(
        "甲方应在2026年6月30日前付款。",
        "甲方应在2027年7月31日前付款。",
        "2026年6月30日",
        "2027年7月31日",
    ) == ["DATE"]


def test_detects_percent_rate_change() -> None:
    assert critical_field_diff_types(
        "乙方应提供6%增值税专用发票。",
        "乙方应提供13%增值税专用发票。",
        "6%",
        "13%",
    ) == ["PERCENT_RATE"]


def test_detects_chinese_percent_rate_change() -> None:
    assert critical_field_diff_types(
        "违约金按每日千分之一计算。",
        "违约金按每日千分之三计算。",
        "千分之一",
        "千分之三",
    ) == ["PERCENT_RATE"]


def test_detects_duration_change() -> None:
    assert critical_field_diff_types(
        "甲方应在30日内完成付款。",
        "甲方应在45日内完成付款。",
        "30",
        "45",
    ) == ["DURATION"]


def test_detects_chinese_workday_duration_change() -> None:
    assert critical_field_diff_types(
        "甲方应在十个工作日内完成验收。",
        "甲方应在十五个工作日内完成验收。",
        "十个工作日",
        "十五个工作日",
    ) == ["DURATION"]


def test_detects_quantity_change() -> None:
    assert critical_field_diff_types(
        "乙方应交付3台服务器。",
        "乙方应交付5台服务器。",
        "3",
        "5",
    ) == ["QUANTITY"]


def test_detects_party_role_change() -> None:
    assert critical_field_diff_types(
        "甲方负责组织验收。",
        "乙方负责组织验收。",
        "甲方",
        "乙方",
    ) == ["PARTY_ROLE"]


def test_does_not_mark_equivalent_amount_spacing() -> None:
    assert critical_field_diff_types(
        "合同金额为1000元。",
        "合同金额为1000 元。",
        "1000元",
        "1000 元",
    ) == []


def test_does_not_mark_equivalent_date_formats() -> None:
    assert critical_field_diff_types(
        "签订日期为2026.6.30。",
        "签订日期为2026-06-30。",
        "2026.6.30",
        "2026-06-30",
    ) == []


def test_does_not_mark_layout_or_punctuation_noise() -> None:
    assert critical_field_diff_types(
        "甲方应付款。",
        "甲方应付款，",
        "。",
        "，",
    ) == []


def test_review_flags_for_field_types_are_deduplicated_and_ordered() -> None:
    assert critical_field_review_flags(["AMOUNT", "DATE", "AMOUNT"]) == [
        "CRITICAL_FIELD_CHANGE",
        "CRITICAL_FIELD_AMOUNT_CHANGE",
        "CRITICAL_FIELD_DATE_CHANGE",
    ]
