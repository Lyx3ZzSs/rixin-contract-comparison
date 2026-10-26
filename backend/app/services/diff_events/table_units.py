"""Expose independent table cells without counting a structural parent twice."""

from app.models import BBox, EvidenceBox, TextRange


def partitions(event, raw, identity, version, task_id):
    cells = raw.match_score_details.get("cell_observations", [])
    if raw.source_type != "table" or not cells:
        return [event]
    # Historical observations without actual cells cannot be safely partitioned.
    if not all(
        c.get("diff_type") in {"ADD", "DELETE", "MODIFY"} and any(c.get(f"{s}_cell") for s in ("original", "compare"))
        for c in cells
    ):
        return [event]
    scope = raw.match_score_details.get("table_change_scope", "cell")
    groups = [list(range(len(cells)))] if scope in {"row", "col", "table"} else [[i] for i in range(len(cells))]
    result = []
    seen = set()
    for indices in groups:
        selected = [cells[i] for i in indices]
        physical_identity = identity(
            [{key: c.get(key) for key in ("diff_type", "original_cell", "compare_cell")} for c in selected]
        )
        if physical_identity in seen:
            continue
        seen.add(physical_identity)
        directions = {c["diff_type"] for c in selected}
        result.append(
            {
                **event,
                "event_id": "E-" + identity([version, task_id, event["event_id"], scope, selected])[:24],
                "cell_selection": indices,
                "element_type": scope,
                "direction": next(iter(directions)) if len(directions) == 1 else "MODIFY",
                "grouping_reason": f"table_{scope}_change",
                "related_event_group": event["event_id"],
            }
        )
    return result


def project(result, event):
    if "cell_selection" not in event:
        return result
    cells = [result.match_score_details["cell_observations"][i] for i in event["cell_selection"]]
    multi_source = any(len((c.get(f'{side}_cell') or {}).get('source_fragments', [])) > 1
                       for c in cells for side in ('original', 'compare'))
    result.match_score_details = {**result.match_score_details, "cell_observations": cells}
    result.diff_type = event["direction"]
    for side in ("original", "compare"):
        values = [c[f"{side}_cell"] for c in cells if c.get(f"{side}_cell")]
        source_evidence = getattr(result, f'{side}_evidence')
        text = "\n".join(c["text"] for c in values)
        setattr(result, f"{side}_text", text)
        setattr(
            result,
            f"{side}_change_ranges",
            [TextRange(start=0, end=len(text), highlight_type=result.diff_type)] if text else [],
        )
        if multi_source:
            fragments = [f for c in values for f in c.get('source_fragments', []) if f.get('bbox')]
            selected = [e for e in source_evidence if any(
                e.page_no == f['page_no']
                and min(e.bbox.x1, f['bbox']['x1']) > max(e.bbox.x0, f['bbox']['x0'])
                and min(e.bbox.y1, f['bbox']['y1']) > max(e.bbox.y0, f['bbox']['y0'])
                for f in fragments)]
            # Preserve changed-text locations from the comparator. A single
            # legacy cell box cannot represent a multi-page logical field.
            setattr(result, f'{side}_evidence', selected or [
                EvidenceBox(page_no=f['page_no'], bbox=BBox.model_validate(f['bbox']), text=f['text'],
                            method='table_cell_fragment', highlight_type=result.diff_type)
                for f in fragments])
            continue
        setattr(
            result,
            f"{side}_evidence",
            [
                EvidenceBox(
                    page_no=c["page_no"],
                    bbox=BBox.model_validate(c["bbox"]),
                    text=c["text"],
                    method="table_cell",
                    highlight_type=result.diff_type,
                )
                for c in values
                if c.get("bbox")
            ],
        )
    return result
