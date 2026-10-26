"""Structure only for V3 table regions; unresolved cells keep canonical text."""

from __future__ import annotations

from html import escape
from copy import deepcopy

from app.config import Settings, settings

import cv2
import numpy as np
from PIL import Image

from app.services.extractors.contract_v3.artifact import inside, rectangle, enclosing, transform_point
from app.services.extractors.contract_v3.geometry import iou
from app.services.extractors.contract_v3.table_cell_graph import build_graph
from app.services.extractors.contract_v3.table_structure_validator import TableStructureValidator
from app.services.extractors.contract_v3.table_transform import upright_table_crop, orient_table_crop
from app.services.extractors.contract_v3.table_orientation import TableOrientation
from app.services.extractors.contract_v3.table_structure_client import TableStructureClient, TableStructureError
from app.services.extractors.contract_v3.table_model_adapter import adapt_candidate, component_reasons, crop_tokens
from app.services.extractors.contract_v3.table_raster_evidence import (
    validate_raster_evidence,
    validate_internal_dividers,
    repair_clear_merged_rows,
    repair_verified_edge_strip,
)


def _tokens(artifact: dict, region: dict) -> list[dict]:
    result = []
    for char in artifact["native_chars"]:
        box = char["bbox"]
        center = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
        if char["usable"] and inside(center, region["polygon"]):
            result.append(
                {
                    "id": f"native:{char['index']}",
                    "text": char["char"],
                    "bbox": box,
                    "source": "native_pdf",
                    "confidence": 1.0,
                    "native_index": char["index"],
                    "block_id": next(
                        (
                            b["block_id"]
                            for b in artifact["blocks"]
                            if char["index"] in b.get("native_char_indices", [])
                        ),
                        "",
                    ),
                }
            )
    for block in artifact["blocks"]:
        if block["source"] != "PP-OCRv6" or block["region_id"] != region["region_id"]:
            continue
        for char in block.get("ocr_char_provenance", []):
            result.append(
                {
                    "id": f"{block['block_id']}:{char['text_index']}",
                    "text": char["char"],
                    "bbox": char["bbox"],
                    "source": "PP-OCRv6",
                    "confidence": block.get("confidence", 0.0),
                    "ocr_request_id": block["ocr_request_id"],
                    "text_index": char["text_index"],
                    "block_id": block["block_id"],
                }
            )
    return result


def _native_cells(page, region: dict) -> list[dict] | None:
    tables = page.find_tables().tables
    match = max(tables, key=lambda table: iou(table.bbox, region["bbox"]), default=None)
    if match is None or iou(match.bbox, region["bbox"]) <= 0.5:
        return None
    boxes = [list(box) for box in match.cells if box]
    xs = sorted({round(x, 2) for box in boxes for x in (box[0], box[2])})
    ys = sorted({round(y, 2) for box in boxes for y in (box[1], box[3])})
    return [
        {
            "bbox": box,
            "row": ys.index(round(box[1], 2)),
            "column": xs.index(round(box[0], 2)),
            "rowspan": ys.index(round(box[3], 2)) - ys.index(round(box[1], 2)),
            "colspan": xs.index(round(box[2], 2)) - xs.index(round(box[0], 2)),
        }
        for box in boxes
    ]


def _publish(
    artifact: dict,
    region: dict,
    cells: list[dict],
    tokens: list[dict],
    source: str,
    provider_issues: list | None = None,
    *,
    page_to_model: list[float] | None = None,
    validate_in_model_space: bool = False,
) -> dict:
    # HTML, cell IDs, geometry and token ownership must share one traversal.
    # PyMuPDF's cells can be column-major; HTML consumers are row-major.
    cells = sorted(cells, key=lambda cell: (cell["row"], cell["column"]))
    issues = []
    if len({token["id"] for token in tokens}) != len(tokens):
        issues.append("duplicate_token_id")
    slots: set[tuple[int, int]] = set()
    for index, cell in enumerate(cells):
        cell.setdefault("cell_id", f"{region['region_id']}:cell:{index}")
        cell.update(tokens=[], text="")
        if cell["rowspan"] < 1 or cell["colspan"] < 1:
            issues.append("degenerate_cell")
        for slot in (
            (r, c)
            for r in range(cell["row"], cell["row"] + cell["rowspan"])
            for c in range(cell["column"], cell["column"] + cell["colspan"])
        ):
            if slot in slots:
                issues.append("overlapping_cell")
            slots.add(slot)
    rows = max((c["row"] + c["rowspan"] for c in cells), default=0)
    columns = max((c["column"] + c["colspan"] for c in cells), default=0)
    if slots != {(r, c) for r in range(rows) for c in range(columns)}:
        issues.append("incomplete_grid")
    # Half-open polygon edges and row-band checks are directional. A rotated
    # candidate must apply them in its reading frame, not the PDF display frame.
    validation_cells, validation_tokens = cells, tokens
    validation_box = region["bbox"]
    if validate_in_model_space:
        if page_to_model is None:
            raise ValueError("Reading-frame validation requires a transform")
        validation_cells = []
        for cell in cells:
            polygon = [transform_point(p, page_to_model) for p in cell.get("polygon") or rectangle(cell["bbox"])]
            validation_cells.append(cell | dict(polygon=polygon, bbox=enclosing(polygon)))
        validation_tokens = [
            t | dict(bbox=enclosing([transform_point(p, page_to_model) for p in rectangle(t["bbox"])]))
            for t in tokens
        ]
        validation_box = enclosing([transform_point(p, page_to_model) for p in rectangle(region["bbox"])])
    for token, local_token in zip(tokens, validation_tokens):
        box = local_token["bbox"]
        center = [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]
        matching = [cell for cell, local_cell in zip(cells, validation_cells)
                    if inside(center, local_cell.get("polygon") or rectangle(local_cell["bbox"]))]
        if len(matching) != 1:
            issues.append("ambiguous_token_owner")
        else:
            matching[0]["tokens"].append(token)
    sort_boxes = {
        t["id"]: (
            enclosing([transform_point(p, page_to_model) for p in rectangle(t["bbox"])])
            if page_to_model is not None
            else t["bbox"]
        )
        for t in tokens
    }
    for cell in cells:
        cell["tokens"].sort(
            key=lambda token: (
                round((sort_boxes[token["id"]][1] + sort_boxes[token["id"]][3]) / 8) * 4,
                sort_boxes[token["id"]][0],
                token.get("text_index", token.get("native_index", 0)),
            )
        )
        if source in {"UNet+TableRecover", "SLANet_plus"}:
            # A returned word/line can contain character boxes with slightly
            # different baselines. Geometry must never reverse its source span.
            groups = {}
            for token in cell["tokens"]:
                groups.setdefault(token.get("block_id") or token["id"], []).append(token)
            spans = [
                (
                    group,
                    [
                        min(sort_boxes[t["id"]][0] for t in group),
                        min(sort_boxes[t["id"]][1] for t in group),
                        max(sort_boxes[t["id"]][2] for t in group),
                        max(sort_boxes[t["id"]][3] for t in group),
                    ],
                )
                for group in groups.values()
            ]
            lines = []
            for group, box in sorted(spans, key=lambda item: ((item[1][1] + item[1][3]) / 2, item[1][0])):
                matching = [
                    line
                    for line in lines
                    if min(box[3], line["bottom"]) - max(box[1], line["top"])
                    >= 0.5 * min(box[3] - box[1], line["bottom"] - line["top"])
                ]
                if matching:
                    line = matching[-1]
                    # Use the shared vertical band to avoid joining two lines
                    # through a tall middle span.
                    line["top"] = max(line["top"], box[1])
                    line["bottom"] = min(line["bottom"], box[3])
                    line["groups"].append((group, box))
                else:
                    lines.append(dict(top=box[1], bottom=box[3], groups=[(group, box)]))
            ordered = [group for line in lines for group, box in sorted(line["groups"], key=lambda item: item[1][0])]
            cell["tokens"] = [
                t
                for group in ordered
                for t in sorted(group, key=lambda t: t.get("text_index", t.get("native_index", 0)))
            ]
        cell["text"] = "".join(token["text"] for token in cell["tokens"])
    validation = TableStructureValidator().validate(
        {"rows": rows, "cols": columns, "cells": validation_cells, "table_bbox": validation_box}, validation_tokens
    )
    if provider_issues:
        issues.extend(provider_issues)
    diagnostics = validation.as_dict()
    diagnostics["topology_source"] = source
    if issues:
        diagnostics["usable"] = False
        diagnostics["reasons"] = sorted(
            {
                *diagnostics["reasons"],
                *(
                    str(
                        issue.get("type", "PROVIDER_STRUCTURE_UNRESOLVED") if isinstance(issue, dict) else issue
                    ).upper()
                    for issue in issues
                ),
            }
        )
    status = "CONSISTENT" if diagnostics["usable"] else "TABLE_STRUCTURE_UNRESOLVED"
    structure_status = "STRUCTURED_TABLE" if status == "CONSISTENT" else "UNSTRUCTURED_TABLE_PRESENT"
    html = None
    if status == "CONSISTENT":
        html = "<table><tbody>"
        for row in range(rows):
            html += (
                "<tr>"
                + "".join(
                    f'<td rowspan="{cell["rowspan"]}" colspan="{cell["colspan"]}">{escape(cell["text"])}</td>'
                    for cell in cells
                    if cell["row"] == row
                )
                + "</tr>"
            )
        html += "</tbody></table>"
    table = {
        "id": region["region_id"].replace(":", "-"),
        "region_id": region["region_id"],
        "source": source,
        "status": status,
        "structure_status": structure_status,
        "validation": diagnostics,
        "html": html,
        "cells": cells if status == "CONSISTENT" else [],
        "candidate_cell_count": len(cells),
        "issues": issues,
        "token_count": len(tokens),
        "assigned_token_count": sum(len(c["tokens"]) for c in cells),
        "additional_table_ocr_requests": 0,
    }
    blocks = [block for block in artifact["blocks"] if block["region_id"] == region["region_id"]]
    for block in blocks:
        block.pop("raw_html", None)
        block.pop("table_cell_bboxes", None)
        block.pop("table_cells", None)
        block.pop("table_id", None)
        block["table_structure_status"] = status
        block["structure_status"] = structure_status
        block["table_validation"] = diagnostics
    if html and blocks:
        blocks[0].update(
            raw_html=html,
            table_cell_bboxes=[c["bbox"] for c in cells],
            table_id=table["id"],
            table_cells=[
                {
                    **{k: c[k] for k in ("cell_id", "row", "column", "rowspan", "colspan", "bbox", "text")},
                    "token_refs": [
                        {k: t[k] for k in ("id", "block_id", "text_index", "native_index") if k in t}
                        for t in c["tokens"]
                    ],
                }
                for c in cells
            ],
        )
    return table


def _raster_cells(crop: np.ndarray) -> list[list[int]]:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1]
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, crop.shape[1] // 20), 1))
    )
    vertical = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, crop.shape[0] // 20)))
    )
    grid = cv2.add(horizontal, vertical)
    contours, _ = cv2.findContours(grid, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if width > 15 and height > 12 and width < crop.shape[1] * 0.95 and height < crop.shape[0] * 0.95:
            boxes.append([x, y, x + width, y + height])
    return sorted(boxes, key=lambda b: (b[1], b[0]))


class TableResolver:
    def __init__(self, config: Settings = settings, client: TableStructureClient | None = None, *, orientation=None):
        self.config = config
        self.client = client or TableStructureClient(config)
        self.orientation = orientation or TableOrientation(config)

    def start_document(self):
        self.orientation.reset()

    def _model(self, artifact, region, image_bgr, tokens, *, oriented=None):
        crop, transform = oriented if oriented is not None else upright_table_crop(
            image_bgr, region["polygon"], artifact["coordinate_transform"]["native_to_pixel"]
        )
        image = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        local_tokens = crop_tokens(tokens, transform)
        attempts = []

        def predict(branch="auto", padding=0):
            response = self.client.predict(image, region["region_id"], branch=branch, padding=padding)
            # Validate provider geometry before any repair can replace its IDs
            # or polygons; malformed input must still fail the normal gate.
            local, mapped = adapt_candidate(response, transform, region["region_id"])
            from app.services.extractors.contract_v3.table_row_spans import split_observed_row_spans
            span_repairs = []
            if response["branch"] == "wired":
                cells, span_repairs = split_observed_row_spans(response["cells"], image)
                if span_repairs:
                    response = response | {"cells": cells}
                    local, mapped = adapt_candidate(response, transform, region["region_id"])
            reasons = component_reasons(local, local_tokens)
            raster, repairs = {}, span_repairs
            candidate_tokens = local_tokens
            page_tokens = tokens
            if response["branch"] == "wired" and not reasons:
                repaired, candidate_tokens, edge_repairs = repair_verified_edge_strip(local, image, local_tokens)
                if edge_repairs:
                    local, mapped = adapt_candidate(response | {"cells": repaired}, transform, region["region_id"])
                    kept_ids = {t["id"] for t in candidate_tokens}
                    page_tokens = [t for t in tokens if t["id"] in kept_ids]
                    repairs.extend(edge_repairs)
                    reasons.extend(component_reasons(local, candidate_tokens))
            if response["branch"] == "wired" and not reasons:
                repaired, merged_repairs = repair_clear_merged_rows(local, image)
                if merged_repairs:
                    repairs.extend(merged_repairs)
                    local, mapped = adapt_candidate(response | {"cells": repaired}, transform, region["region_id"])
                    reasons.extend(component_reasons(local, candidate_tokens))
            if response["branch"] == "wired":
                candidate = dict(cols=max((c["column"] + c["colspan"] for c in local), default=0), cells=local)
                for name, check in (
                    ("blank_edges", validate_raster_evidence(candidate, image, candidate_tokens)),
                    ("internal_dividers", validate_internal_dividers(candidate, image)),
                ):
                    raster[name] = check.as_dict()
                    reasons.extend(check.reasons)
            # Retry decisions include pixel and character evidence, rather than
            # accepting a model grid and discovering its false edge too late.
            probe = _publish(
                deepcopy(artifact),
                region,
                deepcopy(mapped),
                page_tokens,
                response["model"],
                reasons,
                page_to_model=transform["page_to_model"],
                validate_in_model_space=oriented is not None,
            )
            reasons = sorted(set(reasons + probe["validation"]["reasons"]))
            attempts.append({k: v for k, v in response.items() if k != "cells"} | {"component_reasons": list(reasons)})
            return response, local, mapped, reasons, raster, repairs, page_tokens

        response, local, mapped, reasons, raster, repairs, page_tokens = predict()
        enabled = (
            self.config.table_structure_wired_enabled
            if response["branch"] == "wired"
            else self.config.table_structure_wireless_enabled
        )
        if not enabled:
            reasons.append("MODEL_BRANCH_DISABLED")
        elif reasons and response["branch"] == "wired":
            retry = predict("wired", 64)
            if not retry[3]:
                response, local, mapped, reasons, raster, repairs, page_tokens = retry
        # One bounded edge-context retry, only after a missing-grid/ownership
        # failure. Normal tables and unsupported dividers do not pay this cost.
        if (enabled and response["branch"] == "wired" and reasons
                and {"INCOMPLETE_GRID", "AMBIGUOUS_TOKEN_OWNER"}.intersection(reasons)):
            try:
                retry = predict("wired", 128)
                if not retry[3]:
                    response, local, mapped, reasons, raster, repairs, page_tokens = retry
            except (TableStructureError, ValueError, cv2.error) as exc:
                attempts.append({"branch": "wired", "padding": 128, "error": type(exc).__name__})
        if reasons and self.config.table_structure_alternate_enabled:
            alternate = "wireless" if response["branch"] == "wired" else "wired"
            allowed = (
                self.config.table_structure_wireless_enabled
                if alternate == "wireless"
                else self.config.table_structure_wired_enabled
            )
            if allowed:
                retry = predict(alternate)
                if not retry[3]:
                    response, local, mapped, reasons, raster, repairs, page_tokens = retry
        table = _publish(
            artifact,
            region,
            mapped,
            page_tokens,
            response["model"],
            sorted(set(reasons)),
            page_to_model=transform["page_to_model"],
            validate_in_model_space=oriented is not None,
        )
        table["model_diagnostics"] = dict(
            mode=self.config.table_structure_mode,
            attempts=attempts,
            transform=transform,
            raster=raster,
            raster_repairs=repairs,
            selected_branch=response["branch"],
            weight_sha256=response["weight_sha256"],
        )
        return table

    def _oriented_candidate(self, artifact, region, tokens, crop, transform, image_bgr):
        # Graph token ownership must use the same reading frame as publication.
        graph = build_graph(
            region["region_id"], _raster_cells(crop), crop_tokens(tokens, transform), [0, 0], [1, 0, 0, 1, 0, 0],
            Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)),
        )
        cells = []
        for cell in graph["cells"]:
            polygon = [transform_point(p, transform["model_to_page"]) for p in cell["polygon"]]
            cells.append({k: cell[k] for k in ("row", "column", "rowspan", "colspan")}
                         | dict(polygon=polygon, bbox=enclosing(polygon)))
        candidate = _publish(artifact, region, cells, tokens, "raster_cell_graph", graph["issues"],
                             page_to_model=transform["page_to_model"], validate_in_model_space=True)
        if candidate["status"] != "CONSISTENT" and self.config.table_structure_mode != "legacy":
            candidate = self._model(artifact, region, image_bgr, tokens, oriented=(crop, transform))
        return candidate

    def resolve(self, artifact: dict, page, image: Image.Image) -> int:
        artifact["tables"] = []
        regions = [region for region in artifact["regions"] if region["raw_layout_type"] == "table"]
        if not regions:
            return 0
        image_bgr = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        for region in regions:
            tokens = _tokens(artifact, region)
            direction = None
            oriented = None
            if self.config.table_orientation_mode != "off":
                try:
                    crop, transform = upright_table_crop(
                        image_bgr, region["polygon"], artifact["coordinate_transform"]["native_to_pixel"])
                    direction = self.orientation.analyze(crop, transform, artifact, region, tokens)
                    if direction["status"] == "ROTATE":
                        oriented = orient_table_crop(crop, transform, direction["angle_ccw"])
                except (ValueError, cv2.error) as exc:
                    direction = dict(status="FAILED", angle_ccw=0, reason=type(exc).__name__)
            native = _native_cells(page, region) if artifact["native_chars"] else None
            if native is not None:
                table = _publish(artifact, region, native, tokens, "PDF_vector_ruling")
            else:
                try:
                    crop, transform = upright_table_crop(
                        image_bgr, region["polygon"], artifact["coordinate_transform"]["native_to_pixel"]
                    )
                    boxes = _raster_cells(crop)
                    graph = build_graph(
                        region["region_id"],
                        boxes,
                        tokens,
                        [0, 0],
                        transform["model_to_page"],
                        Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)),
                        page_to_model=transform["page_to_model"],
                    )
                    cells = [
                        {
                            key: value
                            for key, value in cell.items()
                            if key in {"row", "column", "rowspan", "colspan", "bbox", "polygon", "text"}
                        }
                        for cell in graph["cells"]
                    ]
                    table = _publish(artifact, region, cells, tokens, "raster_cell_graph", graph["issues"])
                except (ValueError, cv2.error) as exc:
                    table = _publish(artifact, region, [], tokens, "raster_cell_graph")
                    table["issues"].append({"type": "TABLE_GEOMETRY_ERROR", "detail": str(exc)})
            if table["status"] != "CONSISTENT" and self.config.table_structure_mode != "legacy":
                baseline = table
                target = deepcopy(artifact) if self.config.table_structure_mode == "shadow" else artifact
                try:
                    candidate = self._model(target, region, image_bgr, tokens)
                    if self.config.table_structure_mode == "shadow":
                        table["model_diagnostics"] = candidate["model_diagnostics"] | {
                            "candidate_status": candidate["status"],
                            "candidate_validation": candidate["validation"],
                        }
                    else:
                        table = candidate
                except (TableStructureError, ValueError, cv2.error) as exc:
                    table = baseline
                    table["model_diagnostics"] = {
                        "mode": self.config.table_structure_mode,
                        "error": str(exc),
                        "candidate_status": "TABLE_STRUCTURE_UNRESOLVED",
                    }
            if direction is not None:
                direction = dict(direction, adopted=False)
                if oriented is not None:
                    target = deepcopy(artifact)
                    try:
                        candidate = self._oriented_candidate(target, region, tokens, *oriented, image_bgr)
                        assigned = [t["id"] for c in candidate["cells"] for t in c.get("tokens", [])]
                        conserved = sorted(assigned) == sorted(t["id"] for t in tokens)
                        direction.update(candidate_status=candidate["status"], token_conservation=conserved,
                                         transform=oriented[1])
                        if (candidate["status"] == "CONSISTENT" and conserved
                                and self.config.table_orientation_mode == "enabled"
                                and (candidate["source"] == "raster_cell_graph"
                                     or self.config.table_structure_mode == "enabled")):
                            artifact["blocks"] = target["blocks"]
                            table = candidate
                            direction["adopted"] = True
                    except (TableStructureError, ValueError, cv2.error) as exc:
                        direction.update(candidate_status="FAILED", candidate_error=type(exc).__name__)
                table.setdefault("model_diagnostics", {})["orientation"] = direction
            diagnostics = table.get("model_diagnostics")
            if diagnostics:
                # Keep large line checks in the table diagnostic artifact, not
                # repeated on every OCR line of the public Document.
                compact = {k: v for k, v in diagnostics.items() if k not in {"raster", "raster_repairs"}}
                compact["raster"] = {
                    name: {"usable": check["usable"], "reasons": check["reasons"]}
                    for name, check in diagnostics.get("raster", {}).items()
                }
                compact["raster_repair_count"] = len(diagnostics.get("raster_repairs", []))
                compact["edge_strip_repairs"] = [
                    {k: repair[k] for k in ("type", "original_columns", "candidate_columns",
                                            "inner_boundary_coverage", "outer_boundary_coverage")}
                    | {"excluded_token_ids": [t["id"] for t in repair["excluded_tokens"]]}
                    for repair in diagnostics.get("raster_repairs", [])
                    if repair.get("type") == "VERIFIED_EXTERIOR_STRIP"
                ]
                first = True
                for block in artifact["blocks"]:
                    if block["region_id"] == region["region_id"]:
                        block["table_model_diagnostics"] = (
                            compact
                            if first
                            else {
                                k: v
                                for k, v in compact.items()
                                if k in {"mode", "selected_branch", "weight_sha256", "error"}
                            }
                        )
                        first = False
            if table["status"] != "CONSISTENT":
                table["fallback"] = {
                    "mode": "canonical_text_and_page_evidence_only",
                    "structured_comparison_allowed": False,
                    "publish_html": False,
                    "review_required": True,
                }
            artifact["tables"].append(table)
        return len(regions)
