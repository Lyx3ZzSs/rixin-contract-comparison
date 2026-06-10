from __future__ import annotations

from app.models import BBox, DiffItem, EvidenceBox
from app.services.audit_summary import audit_stats_summary, build_audit_items, build_audit_stats


def evidence(text: str, highlight_type: str | None, page_no: int = 1) -> EvidenceBox:
    return EvidenceBox(
        page_no=page_no,
        bbox=BBox(x0=1, y0=2, x1=3, y1=4),
        text=text,
        highlight_type=highlight_type,
    )


def test_audit_items_skip_diff_without_typed_evidence() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        title="付款",
        readable_change="付款期限调整。",
        original_evidence=[evidence("30 days", None)],
        compare_evidence=[evidence("45 days", None)],
    )

    items = build_audit_items([diff])

    assert items == []


def test_audit_items_split_mixed_evidence_by_highlight_type() -> None:
    diff = DiffItem(
        diff_id="D002",
        diff_type="MODIFY",
        title="付款",
        original_evidence=[evidence("30 days", "MODIFY"), evidence("旧付款说明", "DELETE")],
        compare_evidence=[
            evidence("45 days", "MODIFY"),
            evidence("新增付款说明", "ADD"),
        ],
    )

    items = build_audit_items([diff])

    assert [(item.item_id, item.diff_type) for item in items] == [
        ("D002:ADD", "ADD"),
        ("D002:DELETE", "DELETE"),
        ("D002:MODIFY", "MODIFY"),
    ]
    assert items[0].summary == "新增付款说明"
    assert items[1].summary == "旧付款说明"
    assert items[2].summary == "原文：30 days 修改后：45 days"
    assert items[0].compare_evidence[0].text == "新增付款说明"
    assert items[1].original_evidence[0].text == "旧付款说明"


def test_audit_items_follow_diff_type() -> None:
    add = DiffItem(
        diff_id="DADD",
        diff_type="ADD",
        compare_evidence=[evidence("新增条款", "ADD")],
    )
    delete = DiffItem(
        diff_id="DDEL",
        diff_type="DELETE",
        original_evidence=[evidence("删除条款", "DELETE")],
    )

    items = build_audit_items([add, delete])

    assert [(item.item_id, item.diff_type) for item in items] == [
        ("DADD:ADD", "ADD"),
        ("DDEL:DELETE", "DELETE"),
    ]


def test_audit_items_reclassify_legacy_modify_with_single_evidence_type() -> None:
    add = DiffItem(
        diff_id="DLEGACYADD",
        diff_type="MODIFY",
        compare_evidence=[evidence("新增条款", "ADD")],
    )
    delete = DiffItem(
        diff_id="DLEGACYDELETE",
        diff_type="MODIFY",
        original_evidence=[evidence("删除条款", "DELETE")],
    )

    items = build_audit_items([add, delete])

    assert [(item.item_id, item.diff_type) for item in items] == [
        ("DLEGACYADD:ADD", "ADD"),
        ("DLEGACYDELETE:DELETE", "DELETE"),
    ]


def test_audit_stats_count_each_audit_item() -> None:
    diffs = [
        DiffItem(
            diff_id="D001",
            diff_type="MODIFY",
            original_evidence=[evidence("30 days", "MODIFY")],
            compare_evidence=[evidence("45 days", "MODIFY"), evidence("新增付款说明", "ADD")],
        ),
        DiffItem(diff_id="D002", diff_type="DELETE", original_evidence=[evidence("删除条款", "DELETE")]),
        DiffItem(diff_id="D003", diff_type="MODIFY", compare_evidence=[evidence("新增条款", "ADD")]),
        DiffItem(diff_id="D004", diff_type="DELETE", original_text="无证据删除"),
    ]

    stats = build_audit_stats(diffs)

    assert stats.total == 4
    assert stats.add == 2
    assert stats.delete == 1
    assert stats.modify == 1
    assert stats.raw_diff_count == 4
    expected_summary = "本次共识别 4 个审计点，其中新增 2 个、删除 1 个、修改 1 个；对应原始差异记录 4 条。"
    assert audit_stats_summary(stats) == expected_summary
