"""Keep model topology and polygon ownership in the original crop/PDF spaces."""

from copy import deepcopy
import math

from app.services.extractors.contract_v3.artifact import enclosing, rectangle, transform_point
from app.services.extractors.contract_v3.table_structure_validator import TableStructureValidator


def adapt_candidate(response: dict, transform: dict, region_id: str) -> tuple[list[dict], list[dict]]:
    local, page = [], []
    width, height = response["width"], response["height"]
    seen = set()
    for raw in response["cells"]:
        cell = deepcopy(raw)
        box, poly = cell["bbox"], cell["polygon"]
        if (
            len(box) != 4
            or len(poly) != 4
            or any(len(p) != 2 for p in poly)
            or not all(math.isfinite(v) for v in box + [v for p in poly for v in p])
            or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)
            or any(not (0 <= p[0] <= width and 0 <= p[1] <= height) for p in poly)
            or max(abs(a - b) for a, b in zip(box, enclosing(poly))) > 0.05
            or cell["raw_geometry_index"] in seen
        ):
            raise ValueError("INVALID_MODEL_CELL_GEOMETRY")
        seen.add(cell["raw_geometry_index"])
        cell["cell_id"] = f"{region_id}:cell:{cell['row']}:{cell['column']}"
        local.append(cell)
        mapped = deepcopy(cell)
        mapped["polygon"] = [transform_point(p, transform["model_to_page"]) for p in poly]
        mapped["bbox"] = enclosing(mapped["polygon"])
        mapped["crop_bbox"] = box
        page.append(mapped)
    return local, page


def crop_tokens(tokens: list[dict], transform: dict) -> list[dict]:
    result = []
    for token in tokens:
        item = deepcopy(token)
        item["bbox"] = enclosing([transform_point(p, transform["page_to_model"]) for p in rectangle(item["bbox"])])
        result.append(item)
    return result


def component_reasons(cells: list[dict], tokens: list[dict]) -> list[str]:
    rows = max((c["row"] + c["rowspan"] for c in cells), default=0)
    cols = max((c["column"] + c["colspan"] for c in cells), default=0)
    result = TableStructureValidator().validate(dict(rows=rows, cols=cols, cells=cells), tokens)
    occupied = {
        (r, c)
        for cell in cells
        for r in range(cell["row"], cell["row"] + cell["rowspan"])
        for c in range(cell["column"], cell["column"] + cell["colspan"])
    }
    reasons = list(result.reasons)
    if len(occupied) != rows * cols:
        reasons.append("INCOMPLETE_GRID")
    if not tokens:
        reasons.append("MODEL_TEXT_EVIDENCE_UNAVAILABLE")
    return sorted(set(reasons))
