"""Partition separately located text edits only across verified matching anchors."""

from app.models import TextRange


def plan(raw):
    if raw.source_type != "clause":
        return None
    sides = ("original", "compare")
    ranges = [getattr(raw, f"{s}_change_ranges") for s in sides]
    if not (len(ranges[0]) == len(ranges[1]) and len(ranges[0]) > 1):
        return None
    texts = [getattr(raw, f"{s}_text") for s in sides]
    # Aligned unchanged prefixes, inter-edit anchors and suffixes prove pairing.
    retained = []
    selections = [[], []]
    for side_index, side in enumerate(sides):
        cursor = 0
        anchors = []
        for change in ranges[side_index]:
            if not cursor <= change.start < change.end <= len(texts[side_index]):
                return None
            anchors.append(texts[side_index][cursor : change.start])
            part = texts[side_index][change.start : change.end]
            indices = [i for i, b in enumerate(getattr(raw, f"{side}_evidence")) if b.text == part]
            if len(indices) != 1:
                return None
            selections[side_index].append(indices)
            cursor = change.end
        anchors.append(texts[side_index][cursor:])
        retained.append(anchors)
    if retained[0] != retained[1] or not all(anchor.strip() for anchor in retained[0][1:-1]):
        return None
    if any(len({x[0] for x in indices}) != len(indices) for indices in selections):
        return None
    return [
        {side: {"range": i, "evidence": selections[j][i]} for j, side in enumerate(sides)}
        for i in range(len(ranges[0]))
    ]


def partitions(event, raw, identity, version, task_id):
    if len(event["raw_diff_ids"]) != 1:
        return [event]
    units = plan(raw)
    if not units:
        return [event]
    return [
        {
            **event,
            "event_id": "E-" + identity([version, task_id, event["event_id"], "text", selection])[:24],
            "text_selection": selection,
            "direction": "MODIFY",
            "element_type": "text",
            "related_event_group": event["event_id"],
            "grouping_reason": "text_edits_separated_by_matching_content",
        }
        for selection in units
    ]


def project(result, event):
    for side, selection in event.get("text_selection", {}).items():
        change = getattr(result, f"{side}_change_ranges")[selection["range"]]
        text = getattr(result, f"{side}_text")[change.start : change.end]
        setattr(result, f"{side}_text", text)
        setattr(result, f"{side}_change_ranges", [TextRange(start=0, end=len(text), highlight_type="MODIFY")])
        setattr(result, f"{side}_evidence", [getattr(result, f"{side}_evidence")[i] for i in selection["evidence"]])
    return result
