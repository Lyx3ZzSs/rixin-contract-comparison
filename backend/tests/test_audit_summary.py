from __future__ import annotations

import math

import pytest

from app.models import (
    AuditItemReview,
    BBox,
    CompareTask,
    DiffItem,
    EvidenceBox,
    OcrRemediationAction,
    PageOcrQualityProfile,
    TaskOcrQualitySummary,
    TaskOcrRemediationSummary,
    TextRange,
)
from app.services.audit_summary import (
    audit_stats_summary,
    build_audit_items,
    build_audit_stats,
    build_task_audit_items,
    normalized_audit_item_reviews,
    project_diff_reviews,
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


@pytest.mark.parametrize(
    ("page_no", "coordinates"),
    [
        (0, (1, 2, 3, 4)),
        (-1, (1, 2, 3, 4)),
        (1, (1, 2, 1, 4)),
        (1, (1, 2, 3, 2)),
        (1, (3, 2, 1, 4)),
        (1, (1, 4, 3, 2)),
        (1, (1, 2, math.inf, 4)),
        (1, (1, 2, math.nan, 4)),
    ],
)
def test_audit_items_treat_invalid_evidence_coordinates_as_unlocated(page_no, coordinates) -> None:
    x0, y0, x1, y1 = coordinates
    diff = DiffItem(
        diff_id="DINVALID",
        diff_type="ADD",
        compare_evidence=[
            EvidenceBox(
                page_no=page_no,
                bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
                text="invalid",
                highlight_type="ADD",
            )
        ],
    )

    item = build_audit_items([diff])[0]

    assert item.evidence_state == "UNLOCATED"
    assert item.quality_status == "NEEDS_REVIEW"
    assert "EVIDENCE_UNLOCATED" in item.review_flags
    assert item.compare_evidence == []
    assert item.compare_text == "invalid"
    assert item.is_fallback is False


def test_audit_items_prefer_valid_evidence_when_valid_and_invalid_boxes_are_mixed() -> None:
    diff = DiffItem(
        diff_id="DMIXEDVALIDITY",
        diff_type="ADD",
        compare_evidence=[
            evidence("valid", "ADD", 2),
            EvidenceBox(
                page_no=-1,
                bbox=BBox(x0=1, y0=2, x1=3, y1=4),
                text="invalid",
                highlight_type="ADD",
            ),
        ],
    )

    item = build_audit_items([diff])[0]

    assert item.evidence_state == "LOCATED"
    assert [box.text for box in item.compare_evidence] == ["valid"]
    assert item.compare_text == "valid"


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


def test_audit_items_keep_change_ranges_local_to_each_typed_child() -> None:
    diff = DiffItem(
        diff_id="DRANGES",
        diff_type="MODIFY",
        clause_no="1",
        title="付款",
        original_text="removed old",
        compare_text="added new",
        original_evidence=[evidence("removed", "DELETE", 1), evidence("old", "MODIFY", 3)],
        compare_evidence=[evidence("added", "ADD", 2), evidence("new", "MODIFY", 4)],
        original_change_ranges=[
            TextRange(start=0, end=7, highlight_type="DELETE"),
            TextRange(start=8, end=11, highlight_type="MODIFY"),
        ],
        compare_change_ranges=[
            TextRange(start=0, end=5, highlight_type="ADD"),
            TextRange(start=6, end=9, highlight_type="MODIFY"),
        ],
    )

    items = {item.diff_type: item for item in build_audit_items([diff])}

    assert items["ADD"].title == "1 付款"
    assert items["ADD"].original_text == ""
    assert items["ADD"].compare_text == "added"
    assert items["ADD"].original_change_ranges == []
    assert [item.highlight_type for item in items["ADD"].compare_change_ranges] == ["ADD"]
    assert [(item.start, item.end) for item in items["ADD"].compare_change_ranges] == [(0, len("added"))]
    assert items["DELETE"].original_text == "removed"
    assert items["DELETE"].compare_text == ""
    assert [item.highlight_type for item in items["DELETE"].original_change_ranges] == ["DELETE"]
    assert [(item.start, item.end) for item in items["DELETE"].original_change_ranges] == [(0, len("removed"))]
    assert items["DELETE"].compare_change_ranges == []
    assert [item.highlight_type for item in items["MODIFY"].original_change_ranges] == ["MODIFY"]
    assert [item.highlight_type for item in items["MODIFY"].compare_change_ranges] == ["MODIFY"]
    assert [(item.start, item.end) for item in items["MODIFY"].original_change_ranges] == [(0, len("old"))]
    assert [(item.start, item.end) for item in items["MODIFY"].compare_change_ranges] == [(0, len("new"))]


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


def test_read_projection_aggregates_normalized_children_without_mutating_diffs() -> None:
    diff = DiffItem(
        diff_id="DPROJECT",
        diff_type="MODIFY",
        review_status="CONFIRMED",
        review_comment="legacy",
        original_evidence=[evidence("old", "MODIFY"), evidence("removed", "DELETE")],
        compare_evidence=[evidence("new", "MODIFY"), evidence("added", "ADD")],
    )
    items = build_audit_items(
        [diff],
        {"DPROJECT:ADD": AuditItemReview(review_status="FALSE_POSITIVE", review_comment="canonical")},
    )

    projected = project_diff_reviews([diff], items)

    assert projected[0] is not diff
    assert projected[0].review_status == "NEEDS_REVIEW"
    assert projected[0].review_comment == ""
    assert diff.review_status == "CONFIRMED"
    assert diff.review_comment == "legacy"


def test_task_audit_items_associate_ocr_and_remediation_by_child_side_and_page() -> None:
    diff = DiffItem(
        diff_id="DCONTEXT",
        diff_type="MODIFY",
        original_evidence=[evidence("removed", "DELETE", 1), evidence("old", "MODIFY", 3)],
        compare_evidence=[evidence("added", "ADD", 2), evidence("new", "MODIFY", 4)],
    )
    task = CompareTask(
        task_id="TCONTEXT",
        diffs=[diff],
        ocr_quality_summary=TaskOcrQualitySummary(
            requires_review=True,
            profiles=[
                PageOcrQualityProfile(
                    side="original",
                    page_no=1,
                    status="LOW_TEXT_CONFIDENCE",
                    reasons=["DELETE_ONLY"],
                    affected_diff_ids=["DCONTEXT"],
                ),
                PageOcrQualityProfile(
                    side="compare",
                    page_no=2,
                    status="TABLE_RISK",
                    reasons=["ADD_ONLY"],
                    affected_diff_ids=["DCONTEXT"],
                ),
                PageOcrQualityProfile(
                    side="compare",
                    page_no=4,
                    status="UNRELIABLE",
                    reasons=["MODIFY_ONLY"],
                    affected_diff_ids=["DCONTEXT"],
                ),
                PageOcrQualityProfile(
                    side="compare",
                    page_no=99,
                    status="LAYOUT_MISMATCH",
                    reasons=["NO_CHILD_MATCH"],
                    affected_diff_ids=["DCONTEXT"],
                ),
            ],
        ),
        ocr_remediation_summary=TaskOcrRemediationSummary(
            actions=[
                OcrRemediationAction(
                    action_id="original:1:DCONTEXT:MARK_REVIEW",
                    action_type="MARK_REVIEW",
                    reason="DELETE_ONLY",
                    status="PLANNED",
                    side="original",
                    page_no=1,
                    diff_id="DCONTEXT",
                ),
                OcrRemediationAction(
                    action_id="compare:2:DCONTEXT:REPAIR_TABLE",
                    action_type="REPAIR_TABLE",
                    reason="ADD_ONLY",
                    status="SUCCEEDED",
                    side="compare",
                    page_no=2,
                    diff_id="DCONTEXT",
                    changed_evidence=True,
                ),
                OcrRemediationAction(
                    action_id="compare:4:DCONTEXT:RELOCATE_EVIDENCE",
                    action_type="RELOCATE_EVIDENCE",
                    reason="MODIFY_ONLY",
                    status="MANUAL_REVIEW_REQUIRED",
                    side="compare",
                    page_no=4,
                    diff_id="DCONTEXT",
                ),
                OcrRemediationAction(
                    action_id="DCONTEXT:UNSCOPED",
                    action_type="ESCALATE_MANUAL_REVIEW",
                    reason="NO_RELIABLE_CHILD",
                    status="MANUAL_REVIEW_REQUIRED",
                    diff_id="DCONTEXT",
                ),
            ]
        ),
    )

    items = build_task_audit_items(task)

    by_type = {item.diff_type: item for item in items}
    assert by_type["DELETE"].ocr_context.scope == "ITEM"
    assert by_type["DELETE"].ocr_context.reasons == ("DELETE_ONLY",)
    assert by_type["DELETE"].remediation_context.action_ids == ("original:1:DCONTEXT:MARK_REVIEW",)
    assert by_type["ADD"].ocr_context.scope == "ITEM"
    assert by_type["ADD"].ocr_context.reasons == ("ADD_ONLY",)
    assert by_type["ADD"].remediation_context.action_ids == ("compare:2:DCONTEXT:REPAIR_TABLE",)
    assert by_type["ADD"].remediation_context.changed_evidence is True
    assert by_type["MODIFY"].ocr_context.scope == "ITEM"
    assert by_type["MODIFY"].ocr_context.reasons == ("MODIFY_ONLY",)
    assert by_type["MODIFY"].remediation_context.action_ids == ("compare:4:DCONTEXT:RELOCATE_EVIDENCE",)
    assert by_type["MODIFY"].remediation_context.requires_manual_review is True
    assert all("NO_CHILD_MATCH" not in item.ocr_context.reasons for item in items)
    assert all("DCONTEXT:UNSCOPED" not in item.remediation_context.action_ids for item in items)


def test_typed_item_context_without_matching_side_and_page_has_none_scope() -> None:
    task = CompareTask(
        task_id="TNONECONTEXT",
        diffs=[
            DiffItem(
                diff_id="DNONECONTEXT",
                diff_type="ADD",
                compare_evidence=[evidence("added", "ADD", 2)],
            )
        ],
        ocr_quality_summary=TaskOcrQualitySummary(
            profiles=[
                PageOcrQualityProfile(
                    side="compare",
                    page_no=9,
                    status="UNRELIABLE",
                    affected_diff_ids=["DNONECONTEXT"],
                )
            ]
        ),
        ocr_remediation_summary=TaskOcrRemediationSummary(
            actions=[
                OcrRemediationAction(
                    action_id="DNONECONTEXT:UNSCOPED",
                    action_type="MARK_REVIEW",
                    reason="DIFF_ONLY",
                    diff_id="DNONECONTEXT",
                )
            ]
        ),
    )

    item = build_task_audit_items(task)[0]

    assert item.ocr_context.scope == "NONE"
    assert item.ocr_context.affected is False
    assert item.remediation_context.scope == "NONE"
    assert item.remediation_context.action_ids == ()


def test_fallback_item_accepts_diff_scoped_ocr_and_remediation_context() -> None:
    task = CompareTask(
        task_id="TDIFFCONTEXT",
        diffs=[DiffItem(diff_id="DDIFFCONTEXT", diff_type="DELETE", original_text="legacy")],
        ocr_quality_summary=TaskOcrQualitySummary(
            profiles=[
                PageOcrQualityProfile(
                    side="original",
                    page_no=9,
                    status="UNRELIABLE",
                    reasons=["DIFF_LEVEL"],
                    affected_diff_ids=["DDIFFCONTEXT"],
                )
            ]
        ),
        ocr_remediation_summary=TaskOcrRemediationSummary(
            actions=[
                OcrRemediationAction(
                    action_id="DDIFFCONTEXT:UNSCOPED",
                    action_type="MARK_REVIEW",
                    reason="DIFF_LEVEL",
                    diff_id="DDIFFCONTEXT",
                )
            ]
        ),
    )

    item = build_task_audit_items(task)[0]

    assert item.ocr_context.scope == "DIFF"
    assert item.ocr_context.reasons == ("DIFF_LEVEL",)
    assert item.remediation_context.scope == "DIFF"
    assert item.remediation_context.action_ids == ("DDIFFCONTEXT:UNSCOPED",)


def test_malformed_typed_evidence_fallback_accepts_diff_scoped_context() -> None:
    task = CompareTask(
        task_id="TMALFORMEDTYPED",
        diffs=[
            DiffItem(
                diff_id="DMALFORMEDTYPED",
                diff_type="ADD",
                original_evidence=[evidence("wrong side", "ADD", 2)],
            )
        ],
        ocr_quality_summary=TaskOcrQualitySummary(
            profiles=[
                PageOcrQualityProfile(
                    side="compare",
                    page_no=9,
                    status="UNRELIABLE",
                    reasons=["DIFF_LEVEL"],
                    affected_diff_ids=["DMALFORMEDTYPED"],
                )
            ]
        ),
        ocr_remediation_summary=TaskOcrRemediationSummary(
            actions=[
                OcrRemediationAction(
                    action_id="DMALFORMEDTYPED:UNSCOPED",
                    action_type="MARK_REVIEW",
                    reason="DIFF_LEVEL",
                    diff_id="DMALFORMEDTYPED",
                )
            ]
        ),
    )

    item = build_task_audit_items(task)[0]

    assert item.is_fallback is True
    assert item.ocr_context.scope == "DIFF"
    assert item.ocr_context.reasons == ("DIFF_LEVEL",)
    assert item.remediation_context.scope == "DIFF"
    assert item.remediation_context.action_ids == ("DMALFORMEDTYPED:UNSCOPED",)


def test_task_audit_item_context_defaults_are_conservative_for_legacy_fallback() -> None:
    task = CompareTask(
        task_id="TLEGACYCONTEXT",
        diffs=[DiffItem(diff_id="DLEGACYCONTEXT", diff_type="DELETE", original_text="missing")],
    )

    item = build_task_audit_items(task)[0]

    assert item.ocr_context.affected is False
    assert item.ocr_context.scope == "NONE"
    assert item.ocr_context.statuses == ()
    assert item.remediation_context.action_ids == ()
    assert item.remediation_context.scope == "NONE"
    assert item.remediation_context.requires_manual_review is False
