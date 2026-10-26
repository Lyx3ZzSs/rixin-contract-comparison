"""Provider-neutral, deterministic gate for publishing table cell structure."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from app.services.extractors.contract_v3.table_ocr_assignment import assign_ocr_to_cells


@dataclass(frozen=True)
class TableStructureValidationResult:
    usable: bool
    reasons: list[str]
    metrics: dict

    def as_dict(self) -> dict:
        return {"usable": self.usable, "reasons": self.reasons, "metrics": self.metrics}


def _area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _intersection(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _union_area(boxes: list[list[float]]) -> float:
    """Exact rectangular union area using horizontal strips."""
    ys = sorted({y for box in boxes for y in (box[1], box[3])})
    total = 0.0
    for y0, y1 in zip(ys, ys[1:]):
        spans = sorted((b[0], b[2]) for b in boxes if b[1] < y1 and b[3] > y0)
        end = float("-inf")
        width = 0.0
        for x0, x1 in spans:
            width += max(0.0, x1 - max(x0, end))
            end = max(end, x1)
        total += width * (y1 - y0)
    return total


class TableStructureValidator:
    """Validate topology, geometry and independent OCR support.

    Boxes supplied to one call must share a coordinate space. OCR assignment is
    computed here from geometry, so frozen and production providers use the same
    checks without trusting a provider's own assignment summary.
    """

    def validate(self, artifact: dict, ocr: list[dict] | None = None) -> TableStructureValidationResult:
        started = perf_counter()
        rows = int(artifact.get("rows", artifact.get("row_count", 0)) or 0)
        cols = int(artifact.get("cols", artifact.get("column_count", 0)) or 0)
        cells = artifact.get("cells") or []
        boxes = [c.get("bbox") for c in cells]
        reasons: set[str] = set()
        metrics: dict = {"rows": rows, "cols": cols, "cell_count": len(cells)}
        if rows <= 0 or cols <= 0 or not cells:
            reasons.add("EMPTY_TABLE_STRUCTURE")
        slots: dict[tuple[int, int], str] = {}
        slot_overlaps = []
        out_of_grid = []
        for index, cell in enumerate(cells):
            name = str(cell.get("cell_id", index))
            row, col = int(cell.get("row", -1)), int(cell.get("col", cell.get("column", -1)))
            rowspan, colspan = int(cell.get("rowspan", 0)), int(cell.get("colspan", 0))
            if row < 0 or col < 0 or rowspan < 1 or colspan < 1 or row + rowspan > rows or col + colspan > cols:
                out_of_grid.append(name)
                continue
            for r in range(row, row + rowspan):
                for c in range(col, col + colspan):
                    key = (r, c)
                    if key in slots:
                        slot_overlaps.append({"slot": [r, c], "cells": [slots[key], name]})
                    else:
                        slots[key] = name
        if out_of_grid:
            reasons.add("CELL_OUT_OF_GRID")
        if slot_overlaps:
            reasons.add("LOGICAL_SLOT_OVERLAP")
        metrics.update(slot_overlap_count=len(slot_overlaps), slot_overlaps=slot_overlaps[:20],
                       out_of_grid_cells=out_of_grid[:20])

        severe = []
        for i, first in enumerate(boxes):
            if first is None or _area(first) <= 0:
                reasons.add("INVALID_CELL_BBOX")
                continue
            for j in range(i + 1, len(boxes)):
                second = boxes[j]
                if second is None or _area(second) <= 0:
                    continue
                intersection = _intersection(first, second)
                if not intersection:
                    continue
                small_fraction = intersection / min(_area(first), _area(second))
                iou = intersection / (_area(first) + _area(second) - intersection)
                # A little line-edge overlap is normal. Half of a smaller cell
                # is not; the IoU floor excludes long, thin border artifacts.
                if small_fraction >= 0.5 and iou >= 0.1:
                    severe.append({"cell_a": str(cells[i].get("cell_id", i)),
                                   "cell_b": str(cells[j].get("cell_id", j)),
                                   "intersection_area": round(intersection, 3), "iou": round(iou, 4),
                                   "intersection_over_smaller": round(small_fraction, 4)})
        if severe:
            reasons.add("SEVERE_CELL_BBOX_OVERLAP")
        if (severe or slot_overlaps) and any(c.get("rowspan", 1) > 1 or c.get("colspan", 1) > 1 for c in cells):
            reasons.add("SUSPICIOUS_MERGED_CELL")
        metrics.update(severe_bbox_overlap_count=len(severe), severe_bbox_overlaps=severe[:20])

        table_box = artifact.get("table_bbox")
        if table_box and artifact.get("table_bbox_space") == "PDF_PAGE_COORDS" and artifact.get("crop_width"):
            table_box = [0, 0, artifact["crop_width"], artifact["crop_height"]]
        if not table_box and boxes:
            valid = [b for b in boxes if b and _area(b) > 0]
            if valid:
                table_box = [min(b[0] for b in valid), min(b[1] for b in valid),
                             max(b[2] for b in valid), max(b[3] for b in valid)]
        valid_boxes = [b for b in boxes if b and _area(b) > 0]
        metrics["cell_coverage_ratio"] = round(min(1.0, _union_area(valid_boxes) / _area(table_box)), 4) if table_box and _area(table_box) else None

        if ocr is None:
            ocr = artifact.get("ocr") or artifact.get("tokens") or []
        valid_ocr = [t for t in ocr if t.get("bbox") and _area(t["bbox"]) > 0]
        assignment_cells = [{"cell_id": str(c.get("cell_id", i)), "bbox": c.get("bbox")}
                            for i, c in enumerate(cells)]
        assignment = assign_ocr_to_cells(valid_ocr, assignment_cells)
        assigned_ids = set(assignment["word_to_cell"])
        assigned = [t for t in valid_ocr if t["id"] in assigned_ids]
        unassigned = [t for t in valid_ocr if t["id"] not in assigned_ids]
        total = len(assigned) + len(unassigned)
        rate = len(assigned) / total if total else None
        weighted = sum(_area(t["bbox"]) for t in assigned) / max(sum(_area(t["bbox"]) for t in assigned + unassigned), 1)
        metrics.update(ocr_total=total, ocr_assigned=len(assigned), ocr_unassigned=len(unassigned),
                       assignment_rate=round(rate, 4) if rate is not None else None,
                       ocr_weighted_coverage_ratio=round(weighted, 4))
        if total >= 5 and len(unassigned) >= 3 and rate is not None and rate < 0.8:
            reasons.add("LOW_OCR_ASSIGNMENT")

        # Unassigned OCR is clustered by vertically overlapping lines. A band
        # needs independent horizontal extent and no cell at that height.
        bands: list[list[dict]] = []
        for token in sorted(unassigned, key=lambda t: (t["bbox"][1] + t["bbox"][3]) / 2):
            b = token["bbox"]
            cy = (b[1] + b[3]) / 2
            group = next((group for group in bands if abs(cy - sum((t["bbox"][1] + t["bbox"][3]) / 2 for t in group) / len(group))
                          <= max(12, (b[3] - b[1]) * 0.8)), None)
            if group is None:
                bands.append([token])
            else:
                group.append(token)
        # A full-width note cell can contain several wrapped OCR lines. Merge
        # nearby unassigned lines before deciding whether the entire row lacks
        # a cell; isolated footer words remain too small to trigger this gate.
        merged_bands: list[list[dict]] = []
        for group in bands:
            if merged_bands:
                previous = merged_bands[-1]
                gap = min(t["bbox"][1] for t in group) - max(t["bbox"][3] for t in previous)
                height = max(t["bbox"][3] - t["bbox"][1] for t in previous + group)
                if gap <= height * 1.5:
                    previous.extend(group)
                    continue
            merged_bands.append(group)
        bands = merged_bands
        uncovered = []
        for group in bands:
            x0 = min(t["bbox"][0] for t in group)
            x1 = max(t["bbox"][2] for t in group)
            y0 = min(t["bbox"][1] for t in group)
            y1 = max(t["bbox"][3] for t in group)
            width = (table_box[2] - table_box[0]) if table_box else 0
            inside = table_box and x0 >= table_box[0] and x1 <= table_box[2] and y0 >= table_box[1] and y1 <= table_box[3]
            has_cell = any(_intersection(b, [x0, y0, x1, y1]) > 0 for b in valid_boxes)
            if inside and len(group) >= 2 and x1 - x0 >= width * 0.25 and not has_cell:
                uncovered.append({"bbox": [x0, y0, x1, y1], "ocr_count": len(group)})
        metrics.update(uncovered_ocr_row_bands=len(uncovered), uncovered_bands=uncovered[:20])
        if uncovered:
            reasons.add("UNCOVERED_OCR_ROW_BAND")
        metrics["validation_seconds"] = round(perf_counter() - started, 6)
        return TableStructureValidationResult(not reasons, sorted(reasons), metrics)
