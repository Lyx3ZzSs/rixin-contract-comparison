"""Reconcile a party field only when the clause owns the same physical change."""
from __future__ import annotations

import unicodedata

from app.models import BBox, Clause, DiffItem


def _compact(text: str) -> tuple[str, list[int]]:
    chars, indices = [], []
    for index, char in enumerate(text):
        for normalized in unicodedata.normalize("NFKC", char):
            if not normalized.isspace():
                chars.append(normalized)
                indices.append(index)
    return "".join(chars), indices


def _owns_side(field: DiffItem, diff: DiffItem, clause: Clause | None, side: str) -> bool:
    if clause is None:
        return False
    value = getattr(field, f"{side}_text")
    target, field_indices = _compact(value)
    text, clause_indices = _compact(clause.text)
    anchors = field.match_score_details.get(f"{side}_field_anchors", [])
    if not target or not anchors:
        return False
    start = text.find(target)
    # Repeated labels in one clause are ambiguous without stronger ownership.
    if start < 0 or text.find(target, start + 1) >= 0:
        return False
    indices = clause_indices[start:start + len(target)]
    for index in indices:
        char = clause.char_boxes[index] if index < len(clause.char_boxes) else None
        if char is None or not any(
            char.page_no == a["page_no"] and _contains_center(BBox.model_validate(a["bbox"]), char.bbox)
            for a in anchors
        ):
            return False
    field_ranges = getattr(field, f"{side}_change_ranges")
    clause_ranges = getattr(diff, f"{side}_change_ranges")
    evidence = getattr(diff, f"{side}_evidence")
    for local_index, raw_index in enumerate(field_indices):
        if not any(r.start <= raw_index < r.end for r in field_ranges):
            continue
        clause_index = indices[local_index]
        if not any(r.start <= clause_index < r.end for r in clause_ranges):
            return False
        char = clause.char_boxes[clause_index]
        if not any(e.page_no == char.page_no and _contains_center(e.bbox, char.bbox) for e in evidence):
            return False
    return True


def _contains_center(outer: BBox, inner: BBox) -> bool:
    return (outer.x0 - 1 <= (inner.x0 + inner.x1) / 2 <= outer.x1 + 1
            and outer.y0 - 1 <= (inner.y0 + inner.y1) / 2 <= outer.y1 + 1)


def reconcile_preamble_fields(
    diffs: list[DiffItem], original: dict[str, Clause], compare: dict[str, Clause],
) -> list[DiffItem]:
    clauses = [d for d in diffs if d.source_type == "clause" and d.diff_type == "MODIFY"]
    emptied = set()
    for field in diffs:
        if field.source_type != "metadata" or field.match_score_details.get("field_kind") not in {"preamble_party", "preamble_address"}:
            continue
        for diff in clauses:
            left, right = original.get(diff.original_clause_id), compare.get(diff.compare_clause_id)
            if not (_owns_side(field, diff, left, "original") and _owns_side(field, diff, right, "compare")):
                continue
            # The independently named field owns this change. Keep unrelated
            # title/body edits in the clause, including their original offsets.
            for side, clause in (("original", left), ("compare", right)):
                target, _ = _compact(getattr(field, f"{side}_text"))
                text, indices = _compact(clause.text)
                start = text.index(target)
                lo, hi = indices[start], indices[start + len(target) - 1] + 1
                remaining = []
                for r in getattr(diff, f"{side}_change_ranges"):
                    if r.start < lo:
                        remaining.append(r.model_copy(update={"end": min(r.end, lo)}))
                    if r.end > hi:
                        remaining.append(r.model_copy(update={"start": max(r.start, hi)}))
                setattr(diff, f"{side}_change_ranges", remaining)
                setattr(diff, f"{side}_snippet", "".join(clause.text[r.start:r.end] for r in remaining))
            diff.match_score_details.setdefault("preamble_fields_excluded", []).append(field.diff_id)
            if not diff.original_change_ranges and not diff.compare_change_ranges:
                emptied.add(diff.diff_id)
            else:
                diff.readable_change = f"原文：{diff.original_snippet}\n修改后：{diff.compare_snippet}"
    return [d for d in diffs if d.diff_id not in emptied]
