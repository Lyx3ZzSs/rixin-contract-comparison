from __future__ import annotations

from app.models import AuditItemReview, BBox, DiffItem, EvidenceBox
from app.services.audit_summary import (
    audit_stats_summary,
    build_audit_items,
    build_audit_stats,
    normalized_audit_item_reviews,
)


def evidence(text: str, highlight_type: str | None, page_no: int = 1) -> EvidenceBox:
    return EvidenceBox(
        page_no=page_no,
        bbox=BBox(x0=1, y0=2, x1=3, y1=4),
        text=text,
        highlight_type=highlight_type,
    )


def test_audit_items_create_stable_fallback_without_typed_evidence() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        title="付款",
        readable_change="付款期限调整。",
        original_evidence=[evidence("30 days", None)],
        compare_evidence=[evidence("45 days", None)],
    )

    first = build_audit_items([diff])
    second = build_audit_items([diff.model_copy(deep=True)])

    assert len(first) == 1
    assert first[0].item_id == second[0].item_id == "D001:MODIFY"
    assert first[0].evidence_state == "LOCATED"


def test_audit_items_mark_fallback_without_coordinates_for_review() -> None:
    diff = DiffItem(
        diff_id="DUNLOCATED",
        diff_type="DELETE",
        original_text="removed without coordinates",
    )

    item = build_audit_items([diff])[0]

    assert item.item_id == "DUNLOCATED:DELETE"
    assert item.quality_status == "NEEDS_REVIEW"
    assert item.evidence_state == "UNLOCATED"
    assert "EVIDENCE_UNLOCATED" in item.review_flags


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


def test_audit_item_ids_do_not_depend_on_evidence_order() -> None:
    diff = DiffItem(
        diff_id="DORDER",
        diff_type="MODIFY",
        original_evidence=[evidence("old", "MODIFY", 2), evidence("removed", "DELETE", 1)],
        compare_evidence=[evidence("new", "MODIFY", 2), evidence("added", "ADD", 1)],
    )

    forward = build_audit_items([diff])
    reversed_evidence = diff.model_copy(
        update={
            "original_evidence": list(reversed(diff.original_evidence)),
            "compare_evidence": list(reversed(diff.compare_evidence)),
        }
    )
    reverse = build_audit_items([reversed_evidence])

    assert {item.item_id for item in forward} == {item.item_id for item in reverse}


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

    assert stats.total == 5
    assert stats.add == 2
    assert stats.delete == 2
    assert stats.modify == 1
    assert stats.raw_diff_count == 4
    expected_summary = "本次共识别 5 个审计点，其中新增 2 个、删除 2 个、修改 1 个；对应原始差异记录 4 条。"
    assert audit_stats_summary(stats) == expected_summary


def test_normalization_broadcasts_legacy_review_to_every_child() -> None:
    diff = DiffItem(
        diff_id="DLEGACY",
        diff_type="MODIFY",
        review_status="CONFIRMED",
        review_comment="legacy",
        reviewed_by="legacy-user",
        reviewed_at="2026-01-01T00:00:00Z",
        original_evidence=[evidence("old", "MODIFY"), evidence("removed", "DELETE")],
        compare_evidence=[evidence("new", "MODIFY"), evidence("added", "ADD")],
    )

    reviews = normalized_audit_item_reviews([diff], {})

    assert set(reviews) == {"DLEGACY:ADD", "DLEGACY:DELETE", "DLEGACY:MODIFY"}
    assert {review.review_status for review in reviews.values()} == {"CONFIRMED"}
    assert {review.review_comment for review in reviews.values()} == {"legacy"}


def test_normalization_prefers_canonical_review_and_only_fills_missing_children() -> None:
    diff = DiffItem(
        diff_id="DPARTIAL",
        diff_type="MODIFY",
        review_status="CONFIRMED",
        review_comment="legacy",
        original_evidence=[evidence("old", "MODIFY"), evidence("removed", "DELETE")],
        compare_evidence=[evidence("new", "MODIFY"), evidence("added", "ADD")],
    )
    canonical = {"DPARTIAL:ADD": AuditItemReview(review_status="FALSE_POSITIVE", review_comment="item")}

    reviews = normalized_audit_item_reviews([diff], canonical)

    assert reviews["DPARTIAL:ADD"].review_status == "FALSE_POSITIVE"
    assert reviews["DPARTIAL:ADD"].review_comment == "item"
    assert reviews["DPARTIAL:DELETE"].review_status == "CONFIRMED"
    assert reviews["DPARTIAL:MODIFY"].review_status == "CONFIRMED"


def test_normalization_omits_unreviewed_entries() -> None:
    diff = DiffItem(
        diff_id="DUNREVIEWED",
        diff_type="ADD",
        review_status="UNREVIEWED",
        compare_evidence=[evidence("added", "ADD")],
    )

    reviews = normalized_audit_item_reviews(
        [diff],
        {"DUNREVIEWED:ADD": AuditItemReview(review_status="UNREVIEWED")},
    )

    assert reviews == {}
