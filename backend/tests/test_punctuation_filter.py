from __future__ import annotations

from app.models import BBox, DiffItem, EvidenceBox, TextRange
from app.services.diff.punctuation_filter import clean_punctuation_diff


def evidence(text: str, highlight_type: str) -> EvidenceBox:
    return EvidenceBox(
        page_no=1,
        bbox=BBox(x0=0, y0=0, x1=10, y1=10),
        text=text,
        highlight_type=highlight_type,
    )


def test_clean_punctuation_diff_preserves_mixed_range_types() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        original_text="大金:授权(签字)",
        compare_text="达徐2026.",
        original_change_ranges=[
            TextRange(start=0, end=2, highlight_type="MODIFY"),
            TextRange(start=3, end=5, highlight_type="DELETE"),
            TextRange(start=5, end=9, highlight_type="DELETE"),
        ],
        compare_change_ranges=[
            TextRange(start=0, end=2, highlight_type="MODIFY"),
            TextRange(start=2, end=7, highlight_type="ADD"),
        ],
        original_evidence=[
            evidence("大金", "MODIFY"),
            evidence("授权:", "DELETE"),
            evidence("(签字)", "DELETE"),
        ],
        compare_evidence=[
            evidence("达徐", "MODIFY"),
            evidence("2026.", "ADD"),
        ],
    )

    cleaned = clean_punctuation_diff(diff)

    assert cleaned is not None
    assert cleaned.original_snippet == "大金授权签字"
    assert cleaned.compare_snippet == "达徐2026"
    assert [(item.start, item.end, item.highlight_type) for item in cleaned.original_change_ranges] == [
        (0, 2, "MODIFY"),
        (3, 5, "DELETE"),
        (6, 8, "DELETE"),
    ]
    assert [(item.start, item.end, item.highlight_type) for item in cleaned.compare_change_ranges] == [
        (0, 2, "MODIFY"),
        (2, 6, "ADD"),
    ]
    assert [(item.text, item.highlight_type) for item in cleaned.original_evidence] == [
        ("大金", "MODIFY"),
        ("授权", "DELETE"),
        ("签字", "DELETE"),
    ]
    assert [(item.text, item.highlight_type) for item in cleaned.compare_evidence] == [
        ("达徐", "MODIFY"),
        ("2026", "ADD"),
    ]


def test_clean_punctuation_diff_removes_punctuation_only_modify() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        original_text="甲方:",
        compare_text="甲方",
        original_change_ranges=[TextRange(start=2, end=3, highlight_type="DELETE")],
        compare_change_ranges=[],
    )

    assert clean_punctuation_diff(diff) is None


def test_clean_punctuation_diff_does_not_create_compare_range_for_empty_modify_side() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        original_text="一、系统开放性与可配置性要求",
        compare_text="系统开放性与可配置性要求",
        original_change_ranges=[TextRange(start=0, end=2, highlight_type="DELETE")],
        compare_change_ranges=[],
        original_evidence=[evidence("一、", "DELETE")],
        compare_evidence=[],
    )

    cleaned = clean_punctuation_diff(diff)

    assert cleaned is not None
    assert cleaned.original_snippet == "一"
    assert cleaned.compare_snippet == ""
    assert [(item.start, item.end, item.highlight_type) for item in cleaned.original_change_ranges] == [
        (0, 1, "DELETE"),
    ]
    assert cleaned.compare_change_ranges == []
    assert [(item.text, item.highlight_type) for item in cleaned.original_evidence] == [("一", "DELETE")]
    assert cleaned.compare_evidence == []


def test_clean_punctuation_diff_does_not_create_original_range_for_empty_modify_side() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        original_text="系统开放性与可配置性要求",
        compare_text="一、系统开放性与可配置性要求",
        original_change_ranges=[],
        compare_change_ranges=[TextRange(start=0, end=2, highlight_type="ADD")],
        original_evidence=[],
        compare_evidence=[evidence("一、", "ADD")],
    )

    cleaned = clean_punctuation_diff(diff)

    assert cleaned is not None
    assert cleaned.original_snippet == ""
    assert cleaned.compare_snippet == "一"
    assert cleaned.original_change_ranges == []
    assert [(item.start, item.end, item.highlight_type) for item in cleaned.compare_change_ranges] == [
        (0, 1, "ADD"),
    ]
    assert cleaned.original_evidence == []
    assert [(item.text, item.highlight_type) for item in cleaned.compare_evidence] == [("一", "ADD")]


def test_clean_punctuation_diff_keeps_one_sided_fallback() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="ADD",
        compare_text="新增条款。",
        compare_change_ranges=[],
        compare_evidence=[evidence("新增条款。", "ADD")],
    )

    cleaned = clean_punctuation_diff(diff)

    assert cleaned is not None
    assert cleaned.compare_snippet == "新增条款"
    assert [(item.start, item.end, item.highlight_type) for item in cleaned.compare_change_ranges] == [
        (0, 4, "ADD"),
    ]
    assert [(item.text, item.highlight_type) for item in cleaned.compare_evidence] == [("新增条款", "ADD")]
