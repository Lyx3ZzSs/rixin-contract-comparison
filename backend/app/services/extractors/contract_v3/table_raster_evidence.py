"""Independent pixel evidence for blank edge columns of ruled table candidates.

This optional provider gate does not activate a table recognition model.
Inputs must share the unpadded table-crop pixel coordinate space.
"""

import math
from copy import deepcopy

import cv2
import numpy as np

from app.services.extractors.contract_v3.table_structure_validator import TableStructureValidationResult


def validate_raster_evidence(artifact: dict, crop, ocr: list[dict]) -> TableStructureValidationResult:
    """Require two vertical rules for textless exclusive outer-column cells.

    Reliable text can support ordinary columns; a tall, narrow exterior strip
    still needs two distinct boundaries, even when OCR reads a stamp in it.
    A genuine empty column is allowed: two rules corroborate its empty cells.
    Merged text spanning several columns cannot corroborate an extra column.
    Geometry/topology validation and character publication remain separate.
    Uncertain candidates are rejected whole, never silently trimmed/renumbered.
    """
    pixels = np.asarray(crop.convert("RGB") if hasattr(crop, "convert") else crop)
    if pixels.ndim != 3 or pixels.shape[2] != 3 or not pixels.size:
        return TableStructureValidationResult(False, ["RASTER_EVIDENCE_UNAVAILABLE"], {})
    height, width = pixels.shape[:2]
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    binary = np.uint8(gray < 180) * 255
    length = max(15, round(min(width, height) * 0.04))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, length)))
    # Provider boxes describe cell interiors, not the center of the drawn rule.
    # Eight crop pixels allow ordinary line thickness/inset at the frozen 144dpi.
    tolerance = max(8, round(min(width, height) * 0.005))
    columns = int(artifact.get("cols", artifact.get("column_count", 0)))
    checks, failed = [], []

    def coverage(mask, fixed, low, high, is_horizontal, edge_tolerance):
        extent, cross = (width, height) if is_horizontal else (height, width)
        low, high = max(0, math.ceil(low) + 3), min(extent, math.floor(high) - 3)
        lower, upper = max(0, round(fixed) - edge_tolerance), min(cross, round(fixed) + edge_tolerance + 1)
        strip = mask[lower:upper, low:high] if is_horizontal else mask[low:high, lower:upper]
        return float(np.mean(np.any(strip > 0, axis=0 if is_horizontal else 1))) if strip.size else 0.0

    def valid_box(cell):
        box = cell.get("bbox")
        return (
            box is not None
            and len(box) == 4
            and all(math.isfinite(v) for v in box)
            and box[2] > box[0]
            and box[3] > box[1]
        )

    def has_text(cell):
        if not valid_box(cell):
            return False
        x0, y0, x1, y1 = cell["bbox"]
        return any(
            t.get("text", "").strip()
            and (t.get("source") == "native_pdf" or float(t.get("confidence") or 0) >= 0.85)
            and t.get("bbox")
            and x0 <= (t["bbox"][0] + t["bbox"][2]) / 2 <= x1
            and y0 <= (t["bbox"][1] + t["bbox"][3]) / 2 <= y1
            for t in ocr
        )

    for col in sorted({0, columns - 1}):
        exclusive = [
            c
            for c in artifact.get("cells") or []
            if int(c.get("col", c.get("column", -1))) == col and int(c["colspan"]) == 1
        ]
        suspicious_strip = any(
            valid_box(c) and int(c["rowspan"]) >= 3
            and c["bbox"][2] - c["bbox"][0] <= width * 0.03
            and c["bbox"][3] - c["bbox"][1] >= height * 0.6
            for c in exclusive
        )
        if not exclusive or (not suspicious_strip and any(has_text(c) for c in exclusive)):
            continue
        left_area = right_area = total_length = 0.0
        names = []
        for index, cell in enumerate(exclusive):
            name = str(cell.get("cell_id", index))
            names.append(name)
            if not valid_box(cell):
                failed.append(name)
                continue
            x0, y0, x1, y1 = cell["bbox"]
            # A single rule must not corroborate both sides of a narrow strip.
            edge_tolerance = min(tolerance, max(1, int((x1 - x0) * 0.35)))
            sides = [coverage(vertical, x0, y0, y1, False, edge_tolerance),
                     coverage(vertical, x1, y0, y1, False, edge_tolerance)]
            span = y1 - y0
            total_length += span
            left_area += sides[0] * span
            right_area += sides[1] * span
            checks.append(
                {
                    "cell_id": name,
                    "row": cell["row"],
                    "col": col,
                    "rowspan": cell["rowspan"],
                    "colspan": 1,
                    "bbox": list(cell["bbox"]),
                    "coverage_left_right": [round(v, 4) for v in sides],
                }
            )
        if total_length and min(left_area / total_length, right_area / total_length) < 0.6:
            failed.extend(names)
    reasons = ["UNSUPPORTED_BLANK_EDGE_CELL"] if failed else []
    return TableStructureValidationResult(
        not reasons,
        reasons,
        {
            "blank_edge_cells_checked": len(checks),
            "unsupported_cell_ids": sorted(set(failed)),
            "checks": checks,
            "minimum_column_vertical_coverage": 0.6,
            "line_kernel_pixels": length,
            "edge_tolerance_pixels": tolerance,
        },
    )



def repair_verified_edge_strip(cells: list[dict], crop, tokens: list[dict]):
    """Propose removal only for a proven exterior strip; preserve rejected tokens.

    This is a new candidate, never a silent edit: the caller must validate its
    complete topology, remaining OCR ownership and every internal divider again.
    """
    columns = max((c["column"] + c["colspan"] for c in cells), default=0)
    rows = max((c["row"] + c["rowspan"] for c in cells), default=0)
    if columns < 2 or rows < 3:
        return cells, tokens, []
    check = validate_raster_evidence(dict(cols=columns, cells=cells), crop, tokens)
    pixels = np.asarray(crop)
    height, width = pixels.shape[:2]
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    # Failure at the normal dark-rule threshold is not proof of absence.
    # A faint but continuous outer rule vetoes repair as well.
    faint_vertical = cv2.morphologyEx(
        np.uint8(gray < 245) * 255, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, round(min(width, height) * .04)))),
    )
    for col in (0, columns - 1):
        exclusive = [c for c in cells if c["column"] == col and c["colspan"] == 1]
        if len(exclusive) != 1:
            continue
        cell = exclusive[0]
        x0, y0, x1, y1 = cell["bbox"]
        if not (cell["row"] == 0 and cell["rowspan"] == rows
                and x1 - x0 <= width * .03 and y1 - y0 >= height * .6):
            continue
        evidence = next((c for c in check.metrics["checks"] if c["cell_id"] == cell["cell_id"]), None)
        if not evidence:
            continue
        left, right = evidence["coverage_left_right"]
        inner, outer = (right, left) if col == 0 else (left, right)
        if inner < .8 or outer > .1:
            continue
        tolerance = min(check.metrics["edge_tolerance_pixels"], max(1, int((x1 - x0) * .35)))
        outer_x = x0 if col == 0 else x1
        strip = faint_vertical[max(0, math.ceil(y0) + 3):min(height, math.floor(y1) - 3),
                               max(0, round(outer_x) - tolerance):min(width, round(outer_x) + tolerance + 1)]
        faint_coverage = float(np.mean(np.any(strip > 0, axis=1))) if strip.size else 1.0
        if faint_coverage > .1:
            continue
        # A cell spanning into this column may be real content; do not cut it.
        remaining = [c for c in cells if c is not cell]
        if any(c["column"] <= col < c["column"] + c["colspan"] for c in remaining):
            continue
        excluded = [t for t in tokens if x0 <= (t["bbox"][0] + t["bbox"][2]) / 2 <= x1
                    and y0 <= (t["bbox"][1] + t["bbox"][3]) / 2 <= y1]
        if any(t.get("source") == "native_pdf" or t.get("confidence") is None
               or float(t["confidence"]) >= .85 for t in excluded):
            continue
        excluded_ids = {t["id"] for t in excluded}
        candidate = deepcopy(remaining)
        if col == 0:
            for c in candidate:
                c["column"] -= 1
        audit = dict(type="VERIFIED_EXTERIOR_STRIP", cell=deepcopy(cell),
                     inner_boundary_coverage=inner, outer_boundary_coverage=outer,
                     faint_outer_boundary_coverage=faint_coverage,
                     excluded_tokens=deepcopy(excluded), original_columns=columns, candidate_columns=columns - 1)
        return candidate, [t for t in tokens if t["id"] not in excluded_ids], [audit]
    return cells, tokens, []


def validate_internal_dividers(artifact: dict, crop) -> TableStructureValidationResult:
    """Corroborate each shared vertical segment in its own row, not other rows.

    A divider with uncertain/faint pixel evidence rejects the candidate whole.
    Empty cells are retained when the divider exists. This wired-only check
    does not turn a missing observation into a deleted contract cell.
    """
    pixels = np.asarray(crop.convert("RGB") if hasattr(crop, "convert") else crop)
    if pixels.ndim != 3 or not pixels.size:
        return TableStructureValidationResult(False, ["RASTER_EVIDENCE_UNAVAILABLE"], {})
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    cells = artifact.get("cells") or []
    tolerance = max(4, round(min(width, height) * 0.005))
    binary = np.uint8(gray < 180) * 255
    vertical = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(9, round(min(width, height) * 0.02))))
    )
    checks, failed = [], []
    for left in cells:
        col_end = left.get("column", left.get("col", -1)) + left["colspan"]
        for right in cells:
            if right.get("column", right.get("col", -1)) != col_end:
                continue
            if max(left["row"], right["row"]) >= min(left["row"] + left["rowspan"], right["row"] + right["rowspan"]):
                continue
            a, b = left.get("polygon"), right.get("polygon")

            # Polygons may have either winding order; choose the side by x.
            def side(poly, box, edge):
                if not poly:
                    return [[box[edge], box[1]], [box[edge], box[3]]]
                pairs = [(poly[i], poly[(i + 1) % 4]) for i in range(4)]
                return (max if edge == 2 else min)(pairs, key=lambda p: (p[0][0] + p[1][0]) / 2)

            p, q = side(a, left["bbox"], 2), side(b, right["bbox"], 0)
            lo = max(min(v[1] for v in p), min(v[1] for v in q)) + 3
            hi = min(max(v[1] for v in p), max(v[1] for v in q)) - 3
            if hi <= lo:
                continue
            ys = np.arange(max(0, math.ceil(lo)), min(height, math.floor(hi)))

            def x_at(points, y):
                first, last = sorted(points, key=lambda v: v[1])
                return first[0] + (last[0] - first[0]) * (y - first[1]) / max(last[1] - first[1], 1e-6)

            # Model cell edges are independently localized. Searching only
            # their rounded midpoint discards a real rule just beyond one
            # edge. Cover the interval between both predictions plus bounded
            # localization slack. Bound the extra expansion by cell width,
            # but retain the existing tolerance even for narrow columns.
            px, qx = x_at(p, ys), x_at(q, ys)
            max_band = max(tolerance, min(left['bbox'][2]-left['bbox'][0], right['bbox'][2]-right['bbox'][0]) * .2)
            supported = []
            for y, x_left, x_right in zip(ys, px, qx):
                center = (x_left+x_right)/2
                radius = min(max_band, abs(x_right-x_left)/2+tolerance)
                low = max(0, math.floor(float(center-radius)))
                high = min(width, math.ceil(float(center+radius))+1)
                supported.append(bool(np.any(vertical[y, low:high])))
            coverage = float(np.mean(supported)) if supported else 0
            entry = dict(
                left=left.get("cell_id"),
                right=right.get("cell_id"),
                row=max(left["row"], right["row"]),
                column=col_end,
                coverage=round(coverage, 4),
            )
            checks.append(entry)
            if coverage < 0.6:
                failed.append(entry)
    reasons = ["UNSUPPORTED_INTERNAL_DIVIDER"] if failed else []
    return TableStructureValidationResult(
        not reasons,
        reasons,
        dict(checks=checks, unsupported_segments=failed, minimum_segment_coverage=0.6, tolerance_pixels=tolerance),
    )


def repair_clear_merged_rows(cells: list[dict], crop) -> tuple[list[dict], list[dict]]:
    """Join falsely divided row segments only with a closed raster rectangle.

    Requires near-zero divider support and positive evidence on all four outer
    sides. Does not fill holes, extend spans across rows, trim blank columns or
    infer structure from words. Uncertain segments remain for rejection.
    """
    from copy import deepcopy
    from app.services.extractors.contract_v3.artifact import enclosing

    cells = deepcopy(cells)
    pixels = np.asarray(crop.convert("RGB") if hasattr(crop, "convert") else crop)
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    tolerance = max(8, round(min(width, height) * 0.005))
    evidence = validate_internal_dividers(dict(cells=cells), crop)
    absent = {(c["left"], c["right"]) for c in evidence.metrics.get("unsupported_segments", []) if c["coverage"] <= 0.1}
    repairs = []

    def corners(cell):
        poly = cell.get("polygon") or [
            [cell["bbox"][0], cell["bbox"][1]],
            [cell["bbox"][2], cell["bbox"][1]],
            [cell["bbox"][2], cell["bbox"][3]],
            [cell["bbox"][0], cell["bbox"][3]],
        ]
        left = sorted(sorted(poly, key=lambda p: p[0])[:2], key=lambda p: p[1])
        right = sorted(sorted(poly, key=lambda p: p[0])[2:], key=lambda p: p[1])
        return [left[0], right[0], right[1], left[1]]

    def coverage(first, last):
        length = max(1, math.ceil(math.dist(first, last)))
        coords = np.linspace(first, last, length)
        support = []
        for x, y in coords[3:-3]:
            x, y = round(float(x)), round(float(y))
            if abs(last[0] - first[0]) >= abs(last[1] - first[1]):
                strip = gray[max(0, y - tolerance) : min(height, y + tolerance + 1), max(0, x) : min(width, x + 1)]
            else:
                strip = gray[max(0, y) : min(height, y + 1), max(0, x - tolerance) : min(width, x + tolerance + 1)]
            support.append(bool(strip.size and np.any(strip < 180)))
        return float(np.mean(support)) if support else 0

    result = []
    consumed = set()
    for cell in sorted(cells, key=lambda c: (c["row"], c.get("column", c.get("col", 0)))):
        if cell["cell_id"] in consumed:
            continue
        group = [cell]
        if cell["rowspan"] == 1:
            while True:
                previous = group[-1]
                following = next(
                    (
                        c
                        for c in cells
                        if c["row"] == cell["row"]
                        and c["rowspan"] == 1
                        and (previous["cell_id"], c["cell_id"]) in absent
                    ),
                    None,
                )
                if following is None or following["cell_id"] in consumed or following in group:
                    break
                group.append(following)
        if len(group) > 1:
            first, last = corners(group[0]), corners(group[-1])
            poly = [first[0], last[1], last[2], first[3]]
            sides = [coverage(poly[i], poly[(i + 1) % 4]) for i in range(4)]
            if min(sides) >= 0.8:
                merged = deepcopy(group[0])
                merged["polygon"] = poly
                merged["bbox"] = enclosing(poly)
                merged["colspan"] = sum(c["colspan"] for c in group)
                merged["merged_raw_cell_ids"] = [c["cell_id"] for c in group]
                repairs.append(
                    dict(
                        cell_id=merged["cell_id"],
                        removed_dividers=len(group) - 1,
                        original_cell_ids=merged["merged_raw_cell_ids"],
                        outer_coverage=sides,
                    )
                )
                consumed.update(c["cell_id"] for c in group)
                result.append(merged)
                continue
        consumed.add(cell["cell_id"])
        result.append(cell)
    return result, repairs
