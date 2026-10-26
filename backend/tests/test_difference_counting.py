import json
from pathlib import Path
import pytest
from app.services.diff_events.counting import count_changes
from app.services.diff_events import build_snapshot, event_response
from app.models import CompareTask, DiffItem, AuditItemReview


def test_saved_textin_hierarchy_counts_23_not_lines_cells_or_match():
    data = json.loads((Path(__file__).parent / "fixtures/textin-counting/reference.json").read_text())
    assert data["provider_counts"]["total"] == 24
    assert count_changes(data["diff_result"]) == dict(insert=13, delete=3, change=7, total=23)
    assert data["diff_result"][7]["cell_cnt"] == 2
    assert count_changes([data["diff_result"][7]])["total"] == 1
    assert len(data["diff_result"][18]["diff"][1]) == 66
    assert count_changes([data["diff_result"][18]])["total"] == 1


def cell(col, kind="MODIFY"):
    def value(text):
        return dict(
            row=1,
            col=col,
            rowspan=1,
            colspan=1,
            page_no=2,
            text=text,
            bbox=dict(x0=col * 50, y0=30, x1=col * 50 + 40, y1=50),
            table_id="t",
        )

    return dict(
        diff_type=kind,
        original_cell=value("old") if kind != "ADD" else None,
        compare_cell=value("new") if kind != "DELETE" else None,
    )


def task(scope="cell"):
    t = CompareTask(
        task_id="table",
        diffs=[
            DiffItem(
                diff_id="d",
                diff_type="MODIFY",
                source_type="table",
                match_score_details={"cell_observations": [cell(0), cell(1)], "table_change_scope": scope},
            )
        ],
    )
    t.event_snapshot = build_snapshot(t)
    return t


def test_independent_cells_count_twice_but_display_group_once():
    t = task()
    r = event_response(t)
    assert r["counts"] == dict(insert=0, delete=0, change=2, total=2)
    assert len(r["display_groups"]) == 1
    assert all(len(e["diff"]["compare_evidence"]) == 1 for e in r["events"])
    t.event_reviews[r["events"][0]["event_id"]] = AuditItemReview(review_status="FALSE_POSITIVE")
    after = event_response(t)
    assert after["counts"] == r["counts"]
    assert [e["diff"]["review_status"] for e in after["events"]] == ["FALSE_POSITIVE", "UNREVIEWED"]


@pytest.mark.parametrize("scope", ["row", "col", "table"])
def test_explicit_structural_unit_not_double_counted(scope):
    r = event_response(task(scope))
    assert r["difference_count"] == 1
    assert r["events"][0]["element_type"] == scope


def test_table_change_invalidates_snapshot():
    t = task()
    t.diffs[0].match_score_details["cell_observations"][1]["compare_cell"]["text"] = "changed"
    assert not event_response(t)["snapshot_persisted"]


def test_unknown_status_is_not_silently_counted_or_discarded():
    with pytest.raises(ValueError):
        count_changes([{"status": "UNKNOWN"}])


def test_separate_text_replacements_require_equal_anchors_and_distinct_evidence():
    from app.models import EvidenceBox, BBox, TextRange

    d = DiffItem(
        diff_id="text",
        diff_type="MODIFY",
        source_type="clause",
        original_text="甲100中间200乙",
        compare_text="甲300中间400乙",
    )
    for side, parts in [("original", ["100", "200"]), ("compare", ["300", "400"])]:
        setattr(d, f"{side}_change_ranges", [TextRange(start=1, end=4), TextRange(start=6, end=9)])
        setattr(
            d,
            f"{side}_evidence",
            [
                EvidenceBox(page_no=1, bbox=BBox(x0=i * 100, y0=10, x1=i * 100 + 30, y1=30), text=part)
                for i, part in enumerate(parts)
            ],
        )
    t = CompareTask(task_id="text", diffs=[d])
    t.event_snapshot = build_snapshot(t)
    r = event_response(t)
    assert r["counts"]["change"] == 2
    assert [e["diff"]["compare_text"] for e in r["events"]] == ["300", "400"]
    assert len(r["display_groups"]) == 1
    d.compare_text = "甲300不同400乙"
    t.event_snapshot = build_snapshot(t)
    assert event_response(t)["difference_count"] == 1


def test_duplicate_observation_of_same_cell_does_not_add_count():
    t = task()
    t.diffs[0].match_score_details["cell_observations"].append(cell(0))
    t.event_snapshot = build_snapshot(t)
    assert event_response(t)["difference_count"] == 2


def test_complete_new_column_is_one_unit_but_partial_coverage_is_not():
    diffs = []
    for row in range(3):
        c = cell(2, "ADD")
        c["compare_cell"]["row"] = row
        c["original_row"] = row
        c["original_col"] = None
        diffs.append(
            DiffItem(
                diff_id=f"d{row}",
                diff_type="ADD",
                source_type="table",
                match_score_details={
                    "cell_observations": [c],
                    "table_shapes": {
                        "original": {"row_count": 3, "col_count": 2},
                        "compare": {"row_count": 3, "col_count": 3},
                    },
                },
            )
        )
    t = CompareTask(task_id="column", diffs=diffs)
    t.event_snapshot = build_snapshot(t)
    r = event_response(t)
    assert r["difference_count"] == 1
    assert r["events"][0]["element_type"] == "col"
    assert len(r["events"][0]["diff"]["match_score_details"]["cell_observations"]) == 3
    t.diffs = t.diffs[:2]
    t.event_snapshot = build_snapshot(t)
    assert event_response(t)["difference_count"] == 2
