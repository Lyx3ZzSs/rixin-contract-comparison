"""A single logical table graph owns topology, token assignment and HTML.

Independent structure tokens are diagnostics, never ordinal geometry assignments.
For ruled scans, detected cell edges must form a complete non-overlapping grid and
be corroborated by raster rules. Unresolved tables publish no HTML.
"""

from html import escape
from html.parser import HTMLParser
from statistics import median
from app.services.extractors.contract_v3.artifact import rectangle, transform_point, enclosing, inside


class LogicalHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.row = -1
        self.cells = []
        self.occupied = set()

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row += 1
        if tag not in {"td", "th"}:
            return
        a = dict(attrs)
        rowspan = int(a.get("rowspan", 1))
        colspan = int(a.get("colspan", 1))
        col = 0
        while (self.row, col) in self.occupied:
            col += 1
        self.cells.append(dict(row=self.row, column=col, rowspan=rowspan, colspan=colspan))
        self.occupied.update((r, c) for r in range(self.row, self.row + rowspan) for c in range(col, col + colspan))


def logical_html(text):
    p = LogicalHTML()
    p.feed(text)
    return p.cells


def clustered_edges(values, tolerance):
    groups = []
    for v in sorted(values):
        if not groups or v - min(groups[-1]) > tolerance:
            groups.append([v])
        else:
            groups[-1].append(v)
    return [median(g) for g in groups]


def derived_html(graph):
    if graph["status"] != "CONSISTENT":
        return None
    cells = graph["cells"]
    html = "<table><tbody>"
    for r in range(graph["row_count"]):
        html += (
            "<tr>"
            + "".join(
                f'<td data-cell-id="{c["cell_id"]}" rowspan="{c["rowspan"]}" colspan="{c["colspan"]}">{escape(c["text"])}</td>'
                for c in cells
                if c["row"] == r
            )
            + "</tr>"
        )
    return html + "</tbody></table>"


def build_graph(table_id, boxes, tokens, crop_origin, pixel_to_native, image, *, page_to_model=None):
    import numpy as np

    gray = np.asarray(image.convert("L"))
    height, width = gray.shape
    tolerance = max(3.0, min(width, height) * 0.005)
    xs = clustered_edges([b[i] for b in boxes for i in (0, 2)], tolerance)
    ys = clustered_edges([b[i] for b in boxes for i in (1, 3)], tolerance)

    def nearest(v, edges):
        return min(range(len(edges)), key=lambda i: abs(edges[i] - v))

    issues = []
    cells = []
    occupied = {}
    line_checks = []
    if len(boxes) == 0:
        issues.append(dict(type="EMPTY_CELL_GRAPH"))

    def rule_present(b):
        # Pixel-only corroboration: each of four cell sides needs a continuous
        # dark rule near its detected edge. No text recognition is invoked.
        x0, y0, x1, y1 = map(lambda v: int(round(v)), b)
        results = []
        for horizontal, fixed, lo, hi in [
            (True, y0, x0, x1),
            (True, y1, x0, x1),
            (False, x0, y0, y1),
            (False, x1, y0, y1),
        ]:
            length = width if horizontal else height
            cross = height if horizontal else width
            lo = max(0, lo + 3)
            hi = min(length, hi - 3)
            low = max(0, fixed - 4)
            high = min(cross, fixed + 5)
            strip = gray[low:high, lo:hi] if horizontal else gray[lo:hi, low:high]
            coverage = float(np.mean(np.any(strip < 160, axis=0 if horizontal else 1))) if strip.size else 0
            results.append(coverage)
        return results

    for i, b in enumerate(boxes):
        col, endcol = nearest(b[0], xs), nearest(b[2], xs)
        row, endrow = nearest(b[1], ys), nearest(b[3], ys)
        poly = [transform_point([x + crop_origin[0], y + crop_origin[1]], pixel_to_native) for x, y in rectangle(b)]
        cell = dict(
            cell_id=f"{table_id}:cell:{i}",
            row=row,
            column=col,
            rowspan=endrow - row,
            colspan=endcol - col,
            bbox=enclosing(poly),
            polygon=poly,
            raw_geometry_index=i,
            raw_crop_bbox=b,
            token_refs=[],
            text="",
            structure_token_refs=[],
        )
        cells.append(cell)
        if endcol <= col or endrow <= row:
            issues.append(dict(type="DEGENERATE_CELL", cell_id=cell["cell_id"]))
        for slot in ((r, c) for r in range(row, endrow) for c in range(col, endcol)):
            if slot in occupied:
                issues.append(dict(type="OVERLAPPING_LOGICAL_CELL", slot=slot, cells=[occupied[slot], cell["cell_id"]]))
            occupied[slot] = cell["cell_id"]
        checks = rule_present(b)
        line_checks.append(dict(cell_id=cell["cell_id"], sides=checks))
        if min(checks) < 0.8:
            issues.append(dict(type="RASTER_RULE_NOT_CONFIRMED", cell_id=cell["cell_id"], coverage=checks))
    missing = [(r, c) for r in range(len(ys) - 1) for c in range(len(xs) - 1) if (r, c) not in occupied]
    if missing:
        issues.append(dict(type="UNEXPLAINED_GRID_HOLES", slots=missing))
    assignments = {}
    for t in tokens:
        center = [(t["bbox"][0] + t["bbox"][2]) / 2, (t["bbox"][1] + t["bbox"][3]) / 2]
        matches = [c for c in cells if inside(center, c["polygon"])]
        if len(matches) != 1:
            issues.append(dict(type="TOKEN_ASSIGNMENT_INVALID", token_id=t["id"], candidate_cells=len(matches)))
        else:
            matches[0]["token_refs"].append(t["id"])
            assignments[t["id"]] = matches[0]["cell_id"]
    lookup = {t["id"]: t for t in tokens}
    if len(lookup) != len(tokens):
        issues.append(dict(type="DUPLICATE_TOKEN_ID"))
    cells.sort(key=lambda c: (c["row"], c["column"]))
    sort_boxes = {t["id"]: t["bbox"] for t in tokens}
    if page_to_model is not None:
        scale = max(abs(v) for v in page_to_model[:4])
        sort_boxes = {
            t["id"]: [v / scale for v in enclosing([transform_point(p, page_to_model) for p in rectangle(t["bbox"])])]
            for t in tokens
        }
    for i, c in enumerate(cells):
        # OCR character indexes within a returned word are preserved.
        c["token_refs"].sort(
            key=lambda id: (
                round((sort_boxes[id][1] + sort_boxes[id][3]) / 8) * 4,
                sort_boxes[id][0],
                lookup[id].get("text_index", lookup[id].get("native_index", 0)),
            )
        )
        c["text"] = "".join(lookup[id]["text"] for id in c["token_refs"])
        c["reading_order"] = i
    graph = dict(
        schema="TableCellGraph.v1",
        table_id=table_id,
        row_count=len(ys) - 1,
        column_count=len(xs) - 1,
        cells=cells,
        tokens=tokens,
        token_assignment=assignments,
        raster_rule_checks=line_checks,
        edge_cluster_tolerance=tolerance,
        issues=issues,
        status="TABLE_STRUCTURE_UNRESOLVED" if issues else "CONSISTENT",
        additional_ocr_requests=0,
    )
    graph["html"] = derived_html(graph)
    if issues:
        graph["fallback"] = dict(
            mode="canonical_text_and_page_evidence_only",
            structured_comparison_allowed=False,
            publish_html=False,
            review_required=True,
            reason="Graph consistency or raster/token validation failed; no cell structure inferred from OCR",
        )
    return graph
