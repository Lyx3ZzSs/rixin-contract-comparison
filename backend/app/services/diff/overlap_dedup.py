from __future__ import annotations

import difflib
import re

from app.models import DiffItem, EvidenceBox, TextRange
from app.services.clause_splitter import ClauseSplitter

from app.services.diff.text_utils import shorten

try:
    from rapidfuzz import fuzz as rfuzz
except Exception:
    rfuzz = None


def deduplicate_overlaps(diffs: list[DiffItem]) -> list[DiffItem]:
    add_diffs = [d for d in diffs if d.diff_type == "ADD"]
    delete_diffs = [d for d in diffs if d.diff_type == "DELETE"]
    modify_diffs = [d for d in diffs if d.diff_type == "MODIFY"]

    # --- Phase 1: ADD vs MODIFY overlap ---
    result: list[DiffItem] = diffs
    if add_diffs and modify_diffs:
        add_bodies: dict[str, str] = {}
        for add in add_diffs:
            add_bodies[add.diff_id] = strip_clause_prefix(add.compare_text)

        add_overlap_map: dict[str, list[str]] = {}
        for mod in modify_diffs:
            delete_evidence_text = _delete_evidence_text(mod)
            if not delete_evidence_text:
                continue
            for add in add_diffs:
                body = add_bodies[add.diff_id]
                if body and len(body) >= 10:
                    matched = text_is_contained(body, delete_evidence_text)
                else:
                    matched = title_in_delete_evidence(add, mod)
                if matched:
                    add_overlap_map.setdefault(mod.diff_id, []).append(add.diff_id)

        if add_overlap_map:
            add_by_id = {d.diff_id: d for d in add_diffs}
            result = []
            for diff in diffs:
                if diff.diff_type == "ADD" and diff.diff_id in {
                    aid for aids in add_overlap_map.values() for aid in aids
                }:
                    body = add_bodies.get(diff.diff_id, "")
                    if body and len(body) >= 10:
                        result.append(shrink_add_to_prefix(diff))
                    else:
                        result.append(shrink_title_only_add(diff))
                    continue
                if diff.diff_id in add_overlap_map:
                    diff = remove_delete_overlap(diff, add_overlap_map[diff.diff_id], add_by_id, add_bodies)
                    if not diff.original_change_ranges:
                        continue
                result.append(diff)

    # --- Phase 2: DELETE vs MODIFY overlap ---
    if delete_diffs and modify_diffs:
        delete_bodies: dict[str, str] = {}
        for d in delete_diffs:
            delete_bodies[d.diff_id] = strip_clause_prefix(d.original_text)

        delete_overlap_map: dict[str, list[str]] = {}
        for mod in modify_diffs:
            add_ev_text = _add_evidence_text(mod)
            if not add_ev_text:
                continue
            for d in delete_diffs:
                body = delete_bodies[d.diff_id]
                if body and len(body) >= 10:
                    matched = text_is_contained(body, add_ev_text)
                else:
                    matched = title_in_compare_evidence(d, mod)
                if matched:
                    delete_overlap_map.setdefault(mod.diff_id, []).append(d.diff_id)

        if delete_overlap_map:
            delete_by_id = {d.diff_id: d for d in delete_diffs}
            final: list[DiffItem] = []
            for diff in result:
                if diff.diff_type == "DELETE" and diff.diff_id in {
                    did for dids in delete_overlap_map.values() for did in dids
                }:
                    body = delete_bodies.get(diff.diff_id, "")
                    if body and len(body) >= 10:
                        final.append(shrink_delete_to_prefix(diff))
                    else:
                        final.append(shrink_title_only_delete(diff))
                    continue
                if diff.diff_id in delete_overlap_map:
                    diff = remove_compare_overlap(
                        diff, delete_overlap_map[diff.diff_id], delete_by_id, delete_bodies,
                    )
                    if not diff.compare_change_ranges and not diff.original_change_ranges:
                        continue
                final.append(diff)
            result = final

    return result


def strip_clause_prefix(text: str) -> str:
    stripped = text.strip()
    match = ClauseSplitter.clause_start_pattern.match(stripped.splitlines()[0] if stripped else "")
    if match:
        first_line = stripped.splitlines()[0]
        prefix_len = len(first_line)
        return stripped[prefix_len:].strip()
    return stripped


def _delete_evidence_text(diff: DiffItem) -> str:
    parts: list[str] = []
    for e in diff.original_evidence:
        if e.highlight_type in ("DELETE", "MODIFY"):
            parts.append(e.text)
    return " ".join(parts)


def _add_evidence_text(diff: DiffItem) -> str:
    parts: list[str] = []
    for e in diff.compare_evidence:
        if e.highlight_type in ("ADD", "MODIFY"):
            parts.append(e.text)
    return " ".join(parts)


def text_overlap_score(left: str, right: str) -> float:
    if rfuzz is not None:
        return float(rfuzz.token_set_ratio(left, right))
    return difflib.SequenceMatcher(None, left, right).ratio() * 100


def text_is_contained(needle: str, haystack: str) -> bool:
    if rfuzz is not None:
        return float(rfuzz.partial_ratio(needle, haystack)) >= 85
    norm_needle = re.sub(r"\s+", "", needle)
    norm_hay = re.sub(r"\s+", "", haystack)
    return norm_needle in norm_hay


def title_in_delete_evidence(add: DiffItem, mod: DiffItem) -> bool:
    title = add.title or add.compare_snippet
    if not title or len(title) < 4:
        return False
    for e in mod.original_evidence:
        if e.highlight_type in ("DELETE", "MODIFY") and text_is_contained(title, e.text):
            return True
    return False


def title_in_compare_evidence(delete: DiffItem, mod: DiffItem) -> bool:
    title = delete.title or delete.original_snippet
    if not title or len(title) < 4:
        return False
    for e in mod.compare_evidence:
        if e.highlight_type in ("ADD", "MODIFY") and text_is_contained(title, e.text):
            return True
    return False


def shrink_add_to_prefix(diff: DiffItem) -> DiffItem:
    text = diff.compare_text
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    match = ClauseSplitter.clause_start_pattern.match(first_line)
    if not match:
        return diff
    prefix_len = len(match.group(1))
    prefix = first_line[:prefix_len]

    prefix_evidence = [
        e for e in diff.compare_evidence
        if e.highlight_type == "ADD" and len(e.text) <= prefix_len + 5
    ]
    if not prefix_evidence:
        prefix_evidence = diff.compare_evidence[:1]

    return diff.model_copy(update={
        "compare_snippet": prefix,
        "readable_change": f"缺失编号：{prefix}",
        "compare_change_ranges": [TextRange(start=0, end=prefix_len, highlight_type="ADD")],
        "compare_evidence": prefix_evidence,
    })


def shrink_title_only_add(diff: DiffItem) -> DiffItem:
    title = diff.title or diff.compare_snippet
    if not title:
        return diff
    title_len = len(title)
    return diff.model_copy(update={
        "compare_snippet": title,
        "readable_change": f"缺失编号：{title}",
        "compare_change_ranges": [TextRange(start=0, end=title_len, highlight_type="ADD")],
    })


def shrink_delete_to_prefix(diff: DiffItem) -> DiffItem:
    text = diff.original_text
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    match = ClauseSplitter.clause_start_pattern.match(first_line)
    if not match:
        return diff
    prefix_len = len(match.group(1))
    prefix = first_line[:prefix_len]

    prefix_evidence = [
        e for e in diff.original_evidence
        if e.highlight_type in ("DELETE", "MODIFY") and len(e.text) <= prefix_len + 5
    ]
    if not prefix_evidence:
        prefix_evidence = diff.original_evidence[:1]
    return diff.model_copy(update={
        "original_snippet": prefix,
        "readable_change": f"缺失编号：{prefix}",
        "original_change_ranges": [TextRange(start=0, end=prefix_len, highlight_type="DELETE")],
        "original_evidence": prefix_evidence,
    })


def shrink_title_only_delete(diff: DiffItem) -> DiffItem:
    title = diff.title or diff.original_snippet
    if not title:
        return diff
    title_len = len(title)
    return diff.model_copy(update={
        "original_snippet": title,
        "readable_change": f"缺失编号：{title}",
        "original_change_ranges": [TextRange(start=0, end=title_len, highlight_type="DELETE")],
    })


def remove_delete_overlap(
    diff: DiffItem,
    add_ids: list[str],
    add_by_id: dict[str, DiffItem],
    add_bodies: dict[str, str],
) -> DiffItem:
    delete_texts: set[str] = set()
    for aid in add_ids:
        body = add_bodies.get(aid, "")
        if body:
            delete_texts.add(re.sub(r"\s+", "", body))
        add_diff = add_by_id.get(aid)
        if add_diff:
            title = add_diff.title or add_diff.compare_snippet
            if title:
                delete_texts.add(re.sub(r"\s+", "", title))

    filtered_evidence = [
        e for e in diff.original_evidence
        if e.highlight_type not in ("DELETE", "MODIFY")
        or not evidence_in_set(e, delete_texts)
    ]

    remaining_ranges = [
        r for r in diff.original_change_ranges
        if r.highlight_type != "DELETE" or not range_text_in_set(diff.original_text, r, delete_texts)
    ]

    return diff.model_copy(update={
        "original_evidence": filtered_evidence,
        "original_change_ranges": remaining_ranges or rebuild_ranges_from_evidence(filtered_evidence, diff.original_text),
        "original_snippet": rebuild_snippet(diff.original_text, remaining_ranges),
        "readable_change": rebuild_readable(diff.original_text, diff.compare_text, remaining_ranges, diff.compare_change_ranges),
    })


def remove_compare_overlap(
    diff: DiffItem,
    delete_ids: list[str],
    delete_by_id: dict[str, DiffItem],
    delete_bodies: dict[str, str],
) -> DiffItem:
    overlap_texts: set[str] = set()
    for did in delete_ids:
        body = delete_bodies.get(did, "")
        if body:
            overlap_texts.add(re.sub(r"\s+", "", body))
        delete_diff = delete_by_id.get(did)
        if delete_diff:
            title = delete_diff.title or delete_diff.original_snippet
            if title:
                overlap_texts.add(re.sub(r"\s+", "", title))

    filtered_evidence = [
        e for e in diff.compare_evidence
        if e.highlight_type not in ("ADD", "MODIFY")
        or not evidence_in_set(e, overlap_texts)
    ]

    remaining_ranges = [
        r for r in diff.compare_change_ranges
        if r.highlight_type != "ADD" or not range_text_in_set(diff.compare_text, r, overlap_texts)
    ]

    return diff.model_copy(update={
        "compare_evidence": filtered_evidence,
        "compare_change_ranges": remaining_ranges or rebuild_ranges_from_evidence(filtered_evidence, diff.compare_text),
        "compare_snippet": rebuild_snippet(diff.compare_text, remaining_ranges),
        "readable_change": rebuild_readable(diff.original_text, diff.compare_text, diff.original_change_ranges, remaining_ranges),
    })


def evidence_in_set(evidence: EvidenceBox, delete_texts: set[str]) -> bool:
    compact = re.sub(r"\s+", "", evidence.text)
    if not compact:
        return False
    for dt in delete_texts:
        if dt and (compact in dt or dt in compact):
            return True
        if text_overlap_score(compact, dt) >= 80:
            return True
    return False


def range_text_in_set(text: str, range_: TextRange, delete_texts: set[str]) -> bool:
    fragment = re.sub(r"\s+", "", text[range_.start:range_.end])
    if not fragment:
        return False
    for dt in delete_texts:
        if dt and len(fragment) >= 10 and (fragment in dt or dt in fragment):
            return True
    return False


def rebuild_ranges_from_evidence(evidence: list[EvidenceBox], text: str) -> list[TextRange]:
    ranges: list[TextRange] = []
    for e in evidence:
        if e.highlight_type is not None:
            idx = text.find(e.text[:20]) if len(e.text) >= 20 else text.find(e.text)
            if idx >= 0:
                ranges.append(TextRange(start=idx, end=idx + len(e.text), highlight_type=e.highlight_type))
    return ranges


def rebuild_snippet(text: str, ranges: list[TextRange]) -> str:
    if not ranges:
        return ""
    return shorten("".join(text[r.start:r.end] for r in ranges))


def rebuild_readable(original: str, compare: str, orig_ranges: list[TextRange], comp_ranges: list[TextRange]) -> str:
    orig_part = shorten("".join(original[r.start:r.end] for r in orig_ranges)) if orig_ranges else ""
    comp_part = shorten("".join(compare[r.start:r.end] for r in comp_ranges)) if comp_ranges else ""
    return f"原文：{orig_part}\n修改后：{comp_part}"
