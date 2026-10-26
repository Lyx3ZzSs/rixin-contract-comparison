"""Split model row spans only where neighboring rows and ink agree."""

from copy import deepcopy
import math
import cv2
import numpy as np


def split_observed_row_spans(cells, crop):
    pixels = np.asarray(crop.convert("RGB") if hasattr(crop, "convert") else crop)
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    result, audit = [], []
    for cell in cells:
        if cell["rowspan"] < 2 or cell["colspan"] != 1:
            result.append(cell)
            continue
        x0, y0, x1, y1 = cell["bbox"]
        xs = np.arange(max(0, math.ceil(x0) + 4), min(width, math.floor(x1) - 3))
        if len(xs) < 20:
            result.append(cell)
            continue
        boundaries = [(cell["row"], y0)]
        proofs = []
        for row in range(cell["row"] + 1, cell["row"] + cell["rowspan"]):
            neighbors = [
                c
                for c in cells
                if c["row"] == row
                and c["rowspan"] == 1
                and (c["column"] + c["colspan"] == cell["column"] or c["column"] == cell["column"] + 1)
            ]
            if not neighbors:
                continue
            predicted = float(np.median([c["bbox"][1] for c in neighbors]))
            band = min(16, max(6, (y1 - y0) / cell["rowspan"] * 0.22))
            best = None
            # Bound book/scan curvature can tilt a short edge cell even when
            # its neighboring wide row is level. Still require full ink support.
            for slope in (-0.30, -0.24, -0.18, -0.12, -0.08, -0.04, 0, 0.04, 0.08, 0.12, 0.18, 0.24, 0.30):
                for center in np.arange(predicted - band, predicted + band + 1):
                    if not y0 + 10 < center < y1 - 10:
                        continue
                    ys = np.rint(center + (xs - xs.mean()) * slope).astype(int)
                    if ys.min() < 2 or ys.max() >= height - 2:
                        continue
                    # Narrow line support across nearly the entire cell. Digits
                    # and ordinary wrapped text cannot provide a ruled boundary.
                    support = np.min(np.stack([gray[ys + dy, xs] for dy in (-1, 0, 1)]), axis=0) < 150
                    coverage = float(support.mean())
                    if coverage < 0.92:
                        continue
                    score = (coverage, -abs(center - predicted))
                    if best is None or score > best[0]:
                        best = (score, float(center), slope)
            if best is not None and all(abs(best[1] - y) > 10 for _, y in boundaries):
                boundaries.append((row, best[1]))
                proofs.append(dict(row=row, y=best[1], coverage=best[0][0], slope=best[2]))
        if not proofs:
            result.append(cell)
            continue
        boundaries.append((cell["row"] + cell["rowspan"], y1))
        boundaries.sort()
        if any(b[1] <= a[1] + 8 for a, b in zip(boundaries, boundaries[1:])):
            result.append(cell)
            continue
        for (row, top), (end, bottom) in zip(boundaries, boundaries[1:]):
            part = deepcopy(cell)
            part.update(
                row=row,
                rowspan=end - row,
                bbox=[x0, top, x1, bottom],
                polygon=[[x0, top], [x1, top], [x1, bottom], [x0, bottom]],
            )
            result.append(part)
        audit.append(
            dict(
                type="OBSERVED_HORIZONTAL_RULES_IN_ROWSPAN",
                row=cell["row"],
                column=cell["column"],
                original_rowspan=cell["rowspan"],
                boundaries=proofs,
            )
        )
    # The provider index is an identity, not a topology input. Splitting creates
    # new physical cells, so allocate distinct identities before adaptation.
    if audit:
        for index, cell in enumerate(result):
            cell = deepcopy(cell)
            cell["raw_geometry_index"] = index
            result[index] = cell
    return result, audit
