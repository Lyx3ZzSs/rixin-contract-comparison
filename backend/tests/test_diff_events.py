from app.models import CompareTask, DiffItem, EvidenceBox, BBox, TextRange, AuditItemReview
from app.services.diff_events import build_snapshot, event_response


def diff(id, page=1, kind="ADD", source="clause", **kwargs):
    return DiffItem(
        diff_id=id,
        diff_type=kind,
        source_type=source,
        compare_clause_id=id,
        compare_text=id,
        compare_evidence=[EvidenceBox(page_no=page, bbox=BBox(x0=10, y0=20, x1=50, y1=40), text=id)],
        **kwargs,
    )


def snapshot(items, *, barrier=False, merge=True):
    task = CompareTask(task_id="test", diffs=items)
    ids = [d.diff_id for d in items if d.source_type == "clause"]
    if barrier:
        ids.insert(1, "unchanged")
    task.event_snapshot = build_snapshot(
        task,
        clauses={"compare": [{"clause_id": id, "order_index": i} for i, id in enumerate(ids)]},
        matches=[{"original_clause_id": "o", "compare_clause_id": "unchanged"}] if barrier else [],
        merge=merge,
    )
    return task


def test_contiguous_cross_page_with_independent_stamps():
    t = snapshot([diff("a"), diff("b", 2), diff("stamp1", 1, source="seal"), diff("stamp2", 1, source="seal")])
    result = event_response(t)
    assert result["event_count"] == 3
    assert sorted(len(e["children"]) for e in result["events"]) == [1, 1, 2]
    assert sorted(i for e in result["events"] for i in e["raw_diff_ids"]) == ["a", "b", "stamp1", "stamp2"]
    assert len({e["event_id"] for e in result["events"]}) == 3


def test_matching_anchor_and_page_gap_prevent_overmerge():
    assert event_response(snapshot([diff("a"), diff("b")], barrier=True))["event_count"] == 2
    assert event_response(snapshot([diff("a"), diff("b", 3)]))["event_count"] == 2


def test_missing_context_and_merge_off_are_conservative():
    assert event_response(CompareTask(task_id="x", diffs=[diff("a"), diff("b")]))["event_count"] == 2
    assert event_response(snapshot([diff("a"), diff("b")], merge=False))["event_count"] == 2


def test_insertion_prefix_joined_but_common_text_never_duplicated():
    b = diff(
        "b",
        kind="MODIFY",
        original_text="tail",
        compare_change_ranges=[TextRange(start=0, end=3, highlight_type="ADD")],
    )
    b.compare_text = "newtail"
    t = snapshot([diff("a"), b, diff("c")])
    r = event_response(t)
    assert r["event_count"] == 2
    group = next(e for e in r["events"] if len(e["children"]) == 2)
    assert group["diff"]["compare_text"] == "a\nnew"
    assert group["diff"]["original_text"] == ""
    assert t.diffs[1].compare_text == "newtail"


def test_mid_clause_insert_is_separate_and_real_modify_retained():
    b = diff(
        "b",
        kind="MODIFY",
        original_text="tail",
        compare_change_ranges=[TextRange(start=4, end=7, highlight_type="ADD")],
    )
    b.compare_text = "tailnew"
    assert event_response(snapshot([diff("a"), b]))["event_count"] == 2
    b.original_change_ranges = [TextRange(start=0, end=1)]
    r = event_response(snapshot([diff("a"), b]))
    assert r["counts"]["change"] == 1


def test_review_conflict_and_stable_id():
    t = snapshot([diff("a"), diff("b")])
    before = event_response(t)
    event = before["events"][0]
    t.audit_item_reviews[event["audit_item_ids"][0]] = AuditItemReview(review_status="CONFIRMED")
    after = event_response(t)["events"][0]
    assert after["review_conflict"]
    assert after["diff"]["review_status"] == "NEEDS_REVIEW"
    assert after["event_id"] == event["event_id"]
    assert event_response(t)["snapshot_persisted"]


def test_changed_raw_result_invalidates_snapshot():
    t = snapshot([diff("a"), diff("b")])
    t.diffs[0].compare_text = "real change"
    result = event_response(t)
    assert not result["snapshot_persisted"]
    assert result["event_count"] == 2


def test_event_report_keeps_event_order_and_counts(tmp_path):
    import fitz
    from app.services.report_generator import ReportGenerator

    task = snapshot([diff("a"), diff("b"), diff("seal", source="seal")])
    path = ReportGenerator().generate(task, tmp_path / "events.pdf", event_view=True)
    with fitz.open(path) as pdf:
        text = "".join(page.get_text() for page in pdf)
    ids = [e["event_id"] for e in event_response(task)["events"]]
    assert all(text.count(id) == 1 for id in ids)
    assert text.index(ids[0]) < text.index(ids[1])
    assert "新增\n2" in text
    assert "bbox" not in text


def test_watermark_partition_by_physical_region_and_review_isolation():
    from app.services.diff_events import source_fingerprint, _identity

    watermark = diff("watermark", source="metadata", match_score_details={"field_key": "watermark"})
    watermark.compare_evidence = [
        EvidenceBox(page_no=p, bbox=BBox(x0=x, y0=20, x1=x + 40, y1=40), text="水印")
        for p, x in [(1, 10), (1, 15), (1, 200), (2, 10)]
    ]
    t = snapshot([watermark])
    r = event_response(t)
    assert r["event_count"] == 3
    assert sorted(len(e["diff"]["compare_evidence"]) for e in r["events"]) == [1, 1, 2]
    selected = r["events"][0]["event_id"]
    t.event_reviews[selected] = AuditItemReview(review_status="CONFIRMED")
    assert [e["diff"]["review_status"] for e in event_response(t)["events"]].count("CONFIRMED") == 1
    assert t.diffs[0].review_status == "UNREVIEWED"
    fields = (
        "diff_id",
        "diff_type",
        "source_type",
        "original_text",
        "compare_text",
        "original_evidence",
        "compare_evidence",
        "original_change_ranges",
        "compare_change_ranges",
    )
    old = {k: watermark.model_dump()[k] for k in fields}
    for side in ("original_evidence", "compare_evidence"):
        for box in old[side]:
            box.pop("char_boxes")
    assert source_fingerprint(t) == _identity([old])
