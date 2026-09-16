"""Aggregate proven new/deleted columns only when every target row is covered."""

from collections import defaultdict


def aggregate(events, by_id, identity, version, task_id):
    candidates = defaultdict(list)
    for event in events:
        if event.get("element_type") != "cell" or len(event["raw_diff_ids"]) != 1:
            continue
        raw = by_id[event["raw_diff_ids"][0]]
        index = event["cell_selection"][0]
        cell = raw.match_score_details["cell_observations"][index]
        side = "compare" if cell["diff_type"] == "ADD" else "original" if cell["diff_type"] == "DELETE" else None
        if side is None:
            continue
        other = "original" if side == "compare" else "compare"
        value = cell.get(f"{side}_cell")
        shapes = raw.match_score_details.get("table_shapes", {})
        shape = shapes.get(side)
        old = shapes.get(other)
        if not shape or not old or not value or value["colspan"] != 1:
            continue
        if (
            shape["col_count"] <= old["col_count"]
            or cell.get(f"{other}_col") is not None
            or cell.get(f"{other}_row") is None
        ):
            continue
        candidates[(side, value["table_id"], value["col"], shape["row_count"])].append((event, raw, index, value))
    consumed = set()
    groups = []
    for (side, table_id, col, row_count), members in candidates.items():
        rows = {r for _, _, _, v in members for r in range(v["row"], v["row"] + v["rowspan"])}
        if rows != set(range(row_count)) or len(members) < 2:
            continue
        selections = defaultdict(list)
        for e, raw, index, _ in members:
            selections[raw.diff_id].append(index)
            consumed.add(e["event_id"])
        first = members[0][0]
        groups.append(
            {
                **first,
                "event_id": "E-"
                + identity([version, task_id, "column", [e["event_id"] for e, _, _, _ in members]])[:24],
                "raw_diff_ids": list(selections),
                "cell_selection_by_raw": dict(selections),
                "element_type": "col",
                "grouping_reason": "complete_structural_column",
                "related_event_group": first["related_event_group"],
            }
        )
        groups[-1].pop("cell_selection", None)
    return [e for e in events if e["event_id"] not in consumed] + groups
