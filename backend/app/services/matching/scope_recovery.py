"""Coordinate a bounded adjacent scope before emitting unmatched additions.

Raw clauses remain intact. Recovery needs unique heading anchors, full range
coverage, compatible ownership, and an existing match to one of the parts.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.models import Clause


def attachment_identity(clause: Clause) -> str:
    return next((f for f in clause.split_flags if f.startswith("ATTACHMENT_INSTANCE:")), "")


def _compact(text: str) -> str:
    # Preserve numbers, punctuation, case and words: this is scope proof, not
    # a forgiving comparison key that could conceal an actual contract edit.
    return re.sub(r"\s+", "", text)


def _coverage(whole: Clause, parts: list[Clause], page_alignment) -> bool:
    if any(p.section_type != whole.section_type for p in parts):
        return False
    owner = attachment_identity(whole)
    if any(attachment_identity(p) != owner for p in parts):
        return False
    if any("DOCUMENT_ENTRY_BOUNDARY" in p.split_flags for p in parts[1:]):
        return False
    pages = sorted({n for p in parts for n in p.page_numbers})
    if not pages or pages[-1] - pages[0] > 2 or not whole.page_numbers:
        return False
    relationship = getattr(page_alignment, "relationship_score", None)
    if callable(relationship):
        if relationship(set(whole.page_numbers), set(pages)) < 0.75:
            return False
    elif not set(whole.page_numbers).intersection(pages):
        return False
    text = _compact(whole.text)
    boundaries = []
    for part in parts:
        anchor = _compact(part.text.splitlines()[0])
        if len(anchor) < 4 or text.count(anchor) != 1:
            return False
        boundaries.append(text.index(anchor))
    if boundaries[0] != 0 or boundaries != sorted(set(boundaries)):
        return False
    boundaries.append(len(text))
    for i, part in enumerate(parts):
        segment, value = text[boundaries[i] : boundaries[i + 1]], _compact(part.text)
        if min(len(segment), len(value)) < 20:
            return False
        if min(len(segment), len(value)) / max(len(segment), len(value)) < 0.85:
            return False
        if SequenceMatcher(None, segment, value, autojunk=False).ratio() < 0.9:
            return False
    return True


def _combine(parts: list[Clause], normalizer) -> Clause:
    text = "\n".join(p.text for p in parts)
    boxes = []
    if all(len(p.char_boxes) == len(p.text) for p in parts):
        for i, p in enumerate(parts):
            if i:
                boxes.append(None)
            boxes.extend(p.char_boxes)
    return parts[0].model_copy(
        update={
            "text": text,
            "normalized_text": normalizer.normalize_for_diff(text),
            "match_text": normalizer.normalize_for_match(text),
            "char_boxes": boxes,
            "page_numbers": sorted({n for p in parts for n in p.page_numbers}),
            "bboxes": [e for p in parts for e in p.bboxes],
            "source_block_ids": list(dict.fromkeys(b for p in parts for b in p.source_block_ids)),
            "split_flags": list(
                dict.fromkeys([*(f for p in parts for f in p.split_flags), "ADJACENT_SCOPE_RECOVERED"])
            ),
        }
    )


def recover_adjacent_scopes(original, compare, pairs, matched_original, matched_compare, normalizer, page_alignment):
    """Recover 1:2 / 1:3 symmetrically without taking clauses from another pair."""
    for pair in pairs:
        if pair.original is None or pair.compare is None:
            continue
        left, right = pair.original, pair.compare
        if min(len(_compact(left.text)), len(_compact(right.text))) >= 0.85 * max(
            len(_compact(left.text)), len(_compact(right.text))
        ):
            continue
        reverse = len(left.text) < len(right.text)
        whole, anchor = (right, left) if reverse else (left, right)
        sequence = original if reverse else compare
        used = matched_original if reverse else matched_compare
        index = next((i for i, p in enumerate(sequence) if p.clause_id == anchor.clause_id), None)
        if index is None:
            continue
        eligible = []
        for size in (2, 3):
            for start in range(max(0, index - size + 1), min(index + 1, len(sequence) - size + 1)):
                parts = sequence[start : start + size]
                if any(p.clause_id in used and p.clause_id != anchor.clause_id for p in parts):
                    continue
                # PageAlignment is directional; pass a transposed adapter below.
                alignment = page_alignment
                if reverse and callable(getattr(page_alignment, "relationship_score", None)):

                    class Transposed:
                        def relationship_score(self, a, b):
                            return page_alignment.relationship_score(b, a)

                    alignment = Transposed()
                if _coverage(whole, parts, alignment):
                    eligible.append(parts)
        if len(eligible) != 1:
            continue
        parts = eligible[0]
        combined = _combine(parts, normalizer)
        if reverse:
            pair.original = combined
        else:
            pair.compare = combined
        used.update(p.clause_id for p in parts)
        pair.match_method = "adjacent_scope_recovery"
        pair.match_confidence = "MEDIUM"
        pair.score_details = {
            **pair.score_details,
            "scope_recovery": {
                "whole_clause_id": whole.clause_id,
                "part_clause_ids": [p.clause_id for p in parts],
                "whole_side": "compare" if reverse else "original",
                "complete_scope": True,
            },
        }
    return pairs
