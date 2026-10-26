"""Shared OCR word assignment for table artifacts and structure validation."""

from __future__ import annotations

import time


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _intersection(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def assign_ocr_to_cells(tokens: list[dict], cells: list[dict]) -> dict:
    """Prefer a unique center hit; use OCR-area overlap at cell boundaries."""
    started = time.perf_counter()
    word_to_cell: dict[str, str] = {}
    unassigned: list[str] = []
    ambiguous: list[str] = []
    for cell in cells:
        cell["ocr_word_ids"] = []
        cell["text"] = ""
    for token in tokens:
        b = token["bbox"]
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        hits = [i for i, c in enumerate(cells) if c.get("bbox") and c["bbox"][0] <= cx <= c["bbox"][2]
                and c["bbox"][1] <= cy <= c["bbox"][3]]
        winner = hits[0] if len(hits) == 1 else None
        if winner is None:
            scores = sorted(((_intersection(b, c["bbox"]) / max(_area(b), 1), i)
                             for i, c in enumerate(cells) if c.get("bbox")), reverse=True)
            if scores and scores[0][0] >= 0.35 and (len(scores) == 1 or scores[0][0] - scores[1][0] >= 0.15):
                winner = scores[0][1]
            elif len(hits) > 1 or (scores and scores[0][0] >= 0.35):
                ambiguous.append(token["id"])
        if winner is None:
            unassigned.append(token["id"])
            continue
        cell = cells[winner]
        cell["ocr_word_ids"].append(token["id"])
        cell["text"] += ("\n" if cell["text"] else "") + token["text"]
        word_to_cell[token["id"]] = cell["cell_id"]
    total_chars = sum(len(t["text"].strip()) for t in tokens)
    assigned_ids = set(word_to_cell)
    assigned_chars = sum(len(t["text"].strip()) for t in tokens if t["id"] in assigned_ids)
    return {
        "ocr_words_total": len(tokens), "ocr_words_assigned": len(word_to_cell),
        "ocr_words_unassigned": len(unassigned),
        "assignment_rate": len(word_to_cell) / len(tokens) if tokens else None,
        "ocr_text_coverage": assigned_chars / total_chars if total_chars else None,
        "word_to_cell": word_to_cell, "unassigned_word_ids": unassigned,
        "ambiguous_word_ids": ambiguous,
        "assignment_seconds": time.perf_counter() - started,
    }
