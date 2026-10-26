"""Versioned presentation events; raw engine differences remain lossless."""

from __future__ import annotations

import hashlib
import json
from app.services.diff_events.counting import CONTRACT, count_changes
from app.services.diff_events import table_units, text_units, column_units, party_units
from typing import Any

from app.models import CompareTask, DiffItem, TextRange

VERSION = "textin-events-v3"


def _identity(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _only_inserted_text(changed: str, unchanged: str, ranges: list[TextRange], direction: str) -> bool:
    cursor = 0
    retained = []
    for r in ranges:
        if r.highlight_type != direction or not cursor <= r.start < r.end <= len(changed):
            return False
        retained.append(changed[cursor : r.start])
        cursor = r.end
    retained.append(changed[cursor:])
    return bool(ranges) and "".join("".join(retained).split()) == "".join(unchanged.split())


def _direction(diff: DiffItem) -> str:
    if diff.diff_type != "MODIFY":
        return diff.diff_type
    # Only an explicitly located, one-sided change is eligible. Never infer
    # insertion from a blank OCR snippet or an absent opposite evidence box.
    if not diff.original_change_ranges and _only_inserted_text(
        diff.compare_text, diff.original_text, diff.compare_change_ranges, "ADD"
    ):
        return "ADD"
    if not diff.compare_change_ranges and _only_inserted_text(
        diff.original_text, diff.compare_text, diff.original_change_ranges, "DELETE"
    ):
        return "DELETE"
    return "MODIFY"


def _projection(diff: DiffItem) -> DiffItem:
    result = diff.model_copy(deep=True)
    direction = _direction(diff)
    if diff.diff_type == "MODIFY" and direction != "MODIFY":
        result.clause_display_evidence = None
        result.field_display_evidence = None
        side = "compare" if direction == "ADD" else "original"
        opposite = "original" if direction == "ADD" else "compare"
        text = getattr(diff, f"{side}_text")
        ranges = getattr(diff, f"{side}_change_ranges")
        changed = "\n".join(text[r.start : r.end] for r in ranges)
        setattr(result, f"{side}_text", changed)
        setattr(result, f"{side}_change_ranges", [TextRange(start=0, end=len(changed), highlight_type=direction)])
        setattr(result, f"{opposite}_text", "")
        setattr(result, f"{opposite}_evidence", [])
        setattr(result, f"{opposite}_change_ranges", [])
        result.diff_type = direction
        for evidence in getattr(result, f"{side}_evidence"):
            if evidence.highlight_type is not None:
                evidence.highlight_type = direction
    return result


def _position(diff: DiffItem, side: str) -> tuple:
    boxes = getattr(diff, f"{side}_evidence")
    return min(((e.page_no, e.bbox.y0, e.bbox.x0) for e in boxes), default=(10**6, 0, 0))


def build_snapshot(
    task: CompareTask,
    *,
    clauses: dict[str, list[dict]] | None = None,
    matches: list[dict] | None = None,
    page_alignment: list[dict] | None = None,
    merge: bool = True,
) -> dict:
    """Merge only successive one-sided clauses without a matched content anchor.

    Missing segmentation context intentionally produces singleton events. The
    caller must explicitly rebuild historical snapshots with their own context.
    No artifact paths are followed during normal API reads.
    """
    by_id = {d.diff_id: d for d in task.diffs}
    groups: list[list[str]] = []
    assigned: set[str] = set()
    if merge and clauses is not None and matches is not None:
        for side, direction in (("original", "DELETE"), ("compare", "ADD")):
            other = "compare" if side == "original" else "original"
            matched = {m.get(f"{side}_clause_id") for m in matches if m.get(f"{other}_clause_id")}
            eligible: dict[str, list[DiffItem]] = {}
            for d in task.diffs:
                cid = getattr(d, f"{side}_clause_id")
                if cid and d.source_type == "clause" and _direction(d) == direction:
                    eligible.setdefault(cid, []).append(d)
            run: list[str] = []
            last_page = None

            def flush():
                nonlocal run, last_page
                if run:
                    groups.append(run)
                    assigned.update(run)
                run, last_page = [], None

            for clause in sorted(clauses.get(side, []), key=lambda c: c["order_index"]):
                cid = clause["clause_id"]
                candidates = eligible.get(cid, [])
                if not candidates:
                    # Suppressed unmatched OCR/title fragments are not anchors;
                    # every matched clause, even one without an emitted diff, is.
                    if cid in matched:
                        flush()
                    continue
                if len(candidates) != 1:
                    flush()
                    continue
                diff = candidates[0]
                page = _position(diff, side)[0]
                if last_page is not None and (page < last_page or page > last_page + 1):
                    flush()
                if diff.diff_type == "MODIFY":
                    ranges = getattr(diff, f"{side}_change_ranges")
                    text = getattr(diff, f"{side}_text")
                    # A common prefix separates this insertion from prior ones.
                    if text[: ranges[0].start].strip():
                        flush()
                    run.append(diff.diff_id)
                    assigned.add(diff.diff_id)
                    # A matched suffix terminates the contiguous event.
                    flush()
                else:
                    run.append(diff.diff_id)
                    last_page = (
                        max(e.page_no for e in getattr(diff, f"{side}_evidence"))
                        if getattr(diff, f"{side}_evidence")
                        else page
                    )
            flush()
    groups.extend([[d.diff_id] for d in task.diffs if d.diff_id not in assigned])
    events = []
    for ids in groups:
        members = [by_id[i] for i in ids]
        children = [_projection(d) for d in members]
        # Identity excludes review state and transient raw IDs; scoped to task.
        identity = [
            {
                k: d.model_dump()[k]
                for k in (
                    "source_type",
                    "diff_type",
                    "original_text",
                    "compare_text",
                    "original_evidence",
                    "compare_evidence",
                )
            }
            for d in children
        ]
        event_id = "E-" + _identity([VERSION, task.task_id, identity])[:24]
        events.append(
            {
                "event_id": event_id,
                "raw_diff_ids": ids,
                "direction": children[0].diff_type,
                "grouping_reason": "contiguous_unmatched_clauses" if len(ids) > 1 else "independent_change",
            }
        )
    counting_steps = [
        {"stage": "raw_engine_details", "count": len(task.diffs), "unit": "raw_detail"},
        {"stage": "contiguous_text_units", "count": len(events), "unit": "difference"},
    ]
    # Partition repeated watermark evidence by page and physical overlap. Each
    # partition retains a reference to the raw diff and explicit evidence indices.
    partitioned = []
    for event in events:
        raw = by_id[event["raw_diff_ids"][0]]
        if (
            len(event["raw_diff_ids"]) != 1
            or raw.match_score_details.get("field_key") != "watermark"
            or raw.diff_type == "MODIFY"
        ):
            partitioned.append(event)
            continue
        side = "compare" if raw.diff_type == "ADD" else "original"
        boxes = getattr(raw, f"{side}_evidence")
        components = []
        for index, box in enumerate(boxes):
            intersecting = [c for c in components if any(_overlap(box, boxes[i]) for i in c)]
            merged = [index]
            for component in intersecting:
                merged.extend(component)
                components.remove(component)
            components.append(merged)
        if len(components) <= 1:
            partitioned.append(event)
            continue
        for component in components:
            selected = sorted(component)
            event_id = (
                "E-"
                + _identity(
                    [VERSION, task.task_id, event["event_id"], side, [boxes[i].model_dump() for i in selected]]
                )[:24]
            )
            partitioned.append(
                {
                    **event,
                    "event_id": event_id,
                    "grouping_reason": "watermark_physical_region",
                    "related_event_group": event["event_id"],
                    "evidence_selection": {side: selected},
                }
            )
    counting_steps.append({"stage": "watermark_regions", "count": len(partitioned), "unit": "difference"})
    events = [
        unit
        for event in partitioned
        for unit in table_units.partitions(event, by_id[event["raw_diff_ids"][0]], _identity, VERSION, task.task_id)
    ]
    events = column_units.aggregate(events, by_id, _identity, VERSION, task.task_id)
    counting_steps.append({"stage": "table_units", "count": len(events), "unit": "difference"})
    events = [unit for event in events
              for unit in party_units.partitions(event, by_id[event["raw_diff_ids"][0]], _identity, VERSION, task.task_id)]
    counting_steps.append({"stage": "party_fields", "count": len(events), "unit": "difference"})
    events = [
        unit
        for event in events
        for unit in text_units.partitions(event, by_id[event["raw_diff_ids"][0]], _identity, VERSION, task.task_id)
    ]
    counting_steps.append({"stage": "separate_text_edits", "count": len(events), "unit": "difference"})
    events.sort(
        key=lambda e: min(
            (
                _position(_selected_projection(by_id[i], e), "compare")
                if by_id[i].compare_evidence
                else _position(_selected_projection(by_id[i], e), "original")
                for i in e["raw_diff_ids"]
            )
        )
    )
    return {
        "counting_steps": counting_steps,
        "version": VERSION,
        "merge_diff": merge,
        "raw_diff_count": len(task.diffs),
        "events": events,
        "page_alignment": page_alignment or [],
        "context_available": clauses is not None and matches is not None,
        "source_fingerprint": source_fingerprint(task, version=VERSION),
    }


def source_fingerprint(task: CompareTask, *, version: str | None = None) -> str:
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
    payload = [{k: d.model_dump()[k] for k in fields} for d in task.diffs]
    version = version or (task.event_snapshot or {}).get("version", VERSION)
    if version == VERSION:
        for item, diff in zip(payload, task.diffs):
            if diff.source_type == "table":
                item["table_unit_identity"] = {
                    key: diff.match_score_details.get(key)
                    for key in ("cell_observations", "table_change_scope", "table_shapes")
                }
    # Empty character geometry did not exist in v1 snapshots.
    for item in payload:
        for side in ("original_evidence", "compare_evidence"):
            for box in item[side]:
                if not box.get("char_boxes"):
                    box.pop("char_boxes", None)
    return _identity(payload)


def event_response(task: CompareTask, *, merge: bool | None = None) -> dict:
    from app.services.audit_summary import build_task_audit_items
    from app.services.seal_semantics import normalize_seal_source

    snapshot = task.event_snapshot
    if not snapshot or snapshot.get("source_fingerprint") != source_fingerprint(task) or merge is False:
        snapshot = build_snapshot(task, merge=False)
    by_id = {d.diff_id: d for d in task.diffs}
    audits = build_task_audit_items(task)
    from app.services.comparison_observations import is_unresolved_change, observations_for_task
    observations = observations_for_task(task)
    events = []
    for entry in snapshot["events"]:
        if any(is_unresolved_change(by_id[i]) for i in entry["raw_diff_ids"]):
            continue
        members = [by_id[i] for i in entry["raw_diff_ids"]]
        children = [normalize_seal_source(_selected_projection(d, entry).model_copy(deep=True)) for d in members]
        projection = children[0].model_copy(deep=True)
        if entry.get("cell_selection_by_raw"):
            projection.match_score_details["cell_observations"] = [
                cell for child in children for cell in child.match_score_details.get("cell_observations", [])
            ]
        projection.diff_id = entry["event_id"]
        projection.title = (
            (members[0].title or {"seal": "印章变化", "table": "表格变化"}.get(members[0].source_type, "内容变化"))
            if len(members) == 1
            else f"连续{'新增' if projection.diff_type == 'ADD' else '删除'}内容"
        )
        if entry.get("field_selection"):
            projection.title = entry["field_title"]
        if entry.get("element_type") in {"row", "col"}:
            projection.title = (
                "表格"
                + {"row": "整行", "col": "整列"}[entry["element_type"]]
                + {"ADD": "新增", "DELETE": "删除", "MODIFY": "修改"}[entry["direction"]]
            )
            if (entry["direction"] in {"ADD", "DELETE"}
                    and projection.match_score_details.get("change_decision") != "CONFIRMED"):
                projection.title = "表格" + {"row": "行", "col": "列"}[entry["element_type"]] + "对应关系待复核"
        for side in ("original", "compare"):
            text, ranges, evidence = "", [], []
            for child in children:
                part = getattr(child, f"{side}_text")
                offset = len(text)
                ranges.extend(
                    r.model_copy(update={"start": r.start + offset, "end": r.end + offset})
                    for r in getattr(child, f"{side}_change_ranges")
                )
                text += part + ("\n" if part else "")
                evidence.extend(getattr(child, f"{side}_evidence"))
            setattr(projection, f"{side}_text", text.rstrip("\n"))
            setattr(projection, f"{side}_change_ranges", ranges)
            setattr(projection, f"{side}_evidence", evidence)
        member_audits = [a for a in audits if a.diff_id in entry["raw_diff_ids"]]
        statuses = {a.review_status for a in member_audits}
        projection.review_status = next(iter(statuses)) if len(statuses) == 1 else "NEEDS_REVIEW"
        projection.quality_status = (
            "NEEDS_REVIEW" if any(d.quality_status == "NEEDS_REVIEW" for d in members) else "NORMAL"
        )
        split = (
            bool(entry.get("evidence_selection"))
            or "cell_selection" in entry
            or "text_selection" in entry
            or "field_selection" in entry
            or "cell_selection_by_raw" in entry
        )
        if split and any(status != "UNREVIEWED" for status in statuses):
            projection.review_status = "NEEDS_REVIEW"
        own_review = task.event_reviews.get(entry["event_id"])
        if own_review is not None:
            projection.review_status = own_review.review_status
            projection.review_comment = own_review.review_comment
        conflict = (len(statuses) > 1 or (split and any(s != "UNREVIEWED" for s in statuses))) and own_review is None
        projection.content_recognition_status = (
            "NEEDS_REVIEW" if any(d.content_recognition_status == "NEEDS_REVIEW" for d in members) else "CONFIRMED"
        )
        events.append(
            {
                **entry,
                **({"context": {"original_text": members[0].original_text,
                                "compare_text": members[0].compare_text}} if entry.get("field_selection") else {}),
                "diff": projection.model_dump(mode="json"),
                "children": [d.model_dump(mode="json") for d in children],
                "audit_item_ids": [a.item_id for a in member_audits],
                "review_conflict": conflict,
            }
        )
    counts = count_changes(events, status_key="direction")
    return {
        "task_id": task.task_id,
        "revision": task.revision,
        "version": snapshot["version"],
        "snapshot_persisted": bool(task.event_snapshot) and snapshot is task.event_snapshot,
        "context_available": snapshot["context_available"],
        "merge_diff": snapshot["merge_diff"],
        "raw_diff_count": len(task.diffs),
        "event_count": len(events),
        "counts": counts,
        "counting_contract": CONTRACT,
        "counting_steps": snapshot.get("counting_steps", []),
        "difference_count": counts["total"],
        "display_groups": [
            {
                "group_id": key,
                "difference_ids": [e["event_id"] for e in events if e.get("related_event_group", e["event_id"]) == key],
            }
            for key in dict.fromkeys(e.get("related_event_group", e["event_id"]) for e in events)
        ],
        "page_alignment": snapshot["page_alignment"],
        "graphic_observations": task.metrics.get("graphic_observations", []),
        "comparison_observations": [o.model_dump(mode="json") for o in observations],
        "comparison_observation_count": len(observations),
        "coordinate_system": {"unit": "pdf_point", "page_base": 1, "orientation": "display", "origin": "top_left"},
        "events": events,
    }


def event_audit_items(task: CompareTask):
    """Report projection: exactly one audit row per top-level event."""
    from dataclasses import replace
    from app.services.audit_summary import _audit_item

    result = []
    for event in event_response(task)["events"]:
        diff = DiffItem.model_validate(event["diff"])
        item = _audit_item(
            diff, diff.diff_type,
            diff.readable_change if (diff.match_score_details.get('seal_origin_decision') or diff.match_score_details.get('footer_origin_decision')) else diff.title,
            diff.original_evidence, diff.compare_evidence, is_fallback=True
        )
        result.append(replace(item, item_id=event["event_id"], review_status=diff.review_status))
    return result


def _overlap(a, b):
    return (
        a.page_no == b.page_no
        and min(a.bbox.x1, b.bbox.x1) > max(a.bbox.x0, b.bbox.x0)
        and min(a.bbox.y1, b.bbox.y1) > max(a.bbox.y0, b.bbox.y0)
    )


def _selected_projection(diff, event):
    if event.get("field_selection"):
        result = party_units.project(diff, event)
        result.clause_display_evidence = None
        result.field_display_evidence = None
        return result
    result = _projection(diff)
    if any(key in event for key in ("cell_selection_by_raw", "cell_selection", "evidence_selection", "text_selection")):
        result.clause_display_evidence = None
        result.field_display_evidence = None
    if event.get("cell_selection_by_raw"):
        event = {**event, "cell_selection": event["cell_selection_by_raw"][diff.diff_id]}
    for side, indices in event.get("evidence_selection", {}).items():
        boxes = getattr(result, f"{side}_evidence")
        selected = [boxes[i] for i in indices]
        setattr(result, f"{side}_evidence", selected)
        text = "\n".join(dict.fromkeys(b.text for b in selected if b.text)) or "水印区域（文字见原图）"
        setattr(result, f"{side}_text", text)
        setattr(result, f"{side}_change_ranges", [TextRange(start=0, end=len(text), highlight_type=result.diff_type)])
    return text_units.project(table_units.project(result, event), event)
