"""Replay the frozen provider/OCR boundary through the real TableResolver."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image
import pytest

from app.models import Document, Page
from app.services.extractors.contract_v3.artifact import to_page
from app.services.extractors.contract_v3.tables import TableResolver
from app.services.table_compare import TableComparator
from app.services.table_compare.parser import LogicalTableParser


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "output/analysis/scanned-table-paddle-vs-tatr-20260924/input-manifest.json"
PAGE_ARTIFACTS = ROOT / "output/analysis/extraction-challenger-full-golden/artifacts-0.4"
GRAPHS = ROOT / "output/analysis/extraction-challenger-full-golden/tables/graphs"
PADDLE = ROOT / "output/analysis/scanned-table-paddle-vs-tatr-20260924/artifacts/paddle"
MINERU = ROOT / "output/analysis/complex-wired-table-benchmark-20260924/artifacts/mineru_wired"
FROZEN_EXTRACTION = ROOT / "output/analysis/scanned-table-structure-poc-20260924/after"
pytestmark = pytest.mark.skipif(not MANIFEST.exists(), reason="frozen table inputs unavailable")


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _page_artifact(contract: str, side: str, page_no: int, suffix: int):
    stem = f"{contract}{'-compare' if side == 'compare' else ''}-p{page_no}"
    artifact = json.loads((PAGE_ARTIFACTS / f"{stem}.json").read_text())
    region = next(r for r in artifact["regions"] if r["region_id"] == f"{stem}:v3:{suffix}")
    artifact["regions"] = [region]
    artifact["blocks"] = [b for b in artifact["blocks"] if b["region_id"] == region["region_id"]]
    return artifact, region, stem


def _ocr_pdf_tokens(item):
    ox, oy = item["render_crop"][:2]
    a, b, c, d, e, f = item["crop_to_pdf"]

    def point(x, y):
        return (a * (x + ox) + c * (y + oy) + e, b * (x + ox) + d * (y + oy) + f)

    tokens = []
    for token in item["ocr"]:
        x0, y0, x1, y1 = token["bbox"]
        corners = [point(x, y) for x in (x0, x1) for y in (y0, y1)]
        tokens.append({"id": token["id"], "text": token["text"],
                       "bbox": [min(x for x, _ in corners), min(y for _, y in corners),
                                max(x for x, _ in corners), max(y for _, y in corners)]})
    return tokens


def replay_raster(contract: str, side: str, page_no: int, suffix: int, provider: str):
    manifest = json.loads(MANIFEST.read_text())
    item = next(x for x in manifest["regions"] if x["contract_id"] == contract and x["side"] == side
                and x["page_no"] == page_no and x["region_id"].endswith(f":v3:{suffix}"))
    crop = Image.open(ROOT / item["crop_path"]).convert("RGB")
    assert hashlib.sha256(crop.tobytes()).hexdigest() == item["crop_sha256"]
    assert _digest(item["ocr"]) == item["ocr_result_sha256"]
    artifact, region, stem = _page_artifact(contract, side, page_no, suffix)
    assert max(abs(a - b) for a, b in zip(region["bbox"], item["table_bbox"], strict=True)) <= 1.0
    if provider == "graph":
        source = json.loads((GRAPHS / f"{stem}-v3-{suffix}.json").read_text())
        cells = source["cells"]
        issues = source["issues"]
        raw_boxes = [c["raw_crop_bbox"] for c in cells]
    else:
        path = (MINERU / f"{item['region_id']}.json" if provider == "mineru" else
                next(PADDLE.rglob(f"{item['region_id']}.json")))
        source = json.loads(path.read_text())
        assert source["crop_sha256"] == item["crop_sha256"]
        assert source["ocr_result_sha256"] == item["ocr_result_sha256"]
        cells = [{"row": c["row"], "column": c["col"], "rowspan": c["rowspan"],
                  "colspan": c["colspan"], "bbox": c["pdf_page_bbox"]} for c in source["cells"]]
        issues = [] if source["structure_status"] == "STRUCTURE_PASS" else [
            {"type": "FROZEN_PROVIDER_REJECTED"}
        ]
        raw_boxes = [c["bbox"] for c in source["cells"]]
    graph = {"cells": cells, "issues": issues}
    transform = {"model_to_page": [1, 0, 0, 1, 0, 0], "page_to_model": [1, 0, 0, 1, 0, 0]}
    crop_bgr = cv2.cvtColor(np.asarray(crop), cv2.COLOR_RGB2BGR)
    with (patch("app.services.extractors.contract_v3.tables._tokens", return_value=_ocr_pdf_tokens(item)),
          patch("app.services.extractors.contract_v3.tables._native_cells", return_value=None),
          patch("app.services.extractors.contract_v3.tables.upright_table_crop", return_value=(crop_bgr, transform)),
          patch("app.services.extractors.contract_v3.tables._raster_cells", return_value=raw_boxes),
          patch("app.services.extractors.contract_v3.tables.build_graph", return_value=graph)):
        count = TableResolver().resolve(artifact, object(), Image.open(ROOT / item["page_image_path"]).convert("RGB"))
    assert count == 1
    table = artifact["tables"][0]
    page = Page.model_validate_json(to_page(artifact).model_dump_json())
    return table, page, source


def replay_vector(contract: str, page_no: int, suffix: int):
    artifact, region, _ = _page_artifact(contract, "original", page_no, suffix)
    source = next(t for t in artifact["tables"] if t["region_id"] == region["region_id"]
                  and t["source"] == "PDF_vector_ruling")
    cells = [{k: c[k] for k in ("row", "column", "rowspan", "colspan", "bbox")} for c in source["cells"]]
    with patch("app.services.extractors.contract_v3.tables._native_cells", return_value=copy.deepcopy(cells)):
        count = TableResolver().resolve(artifact, object(), Image.new("RGB", (1, 1), "white"))
    assert count == 1
    return artifact["tables"][0], Page.model_validate_json(to_page(artifact).model_dump_json())


def replay_graph(contract: str, side: str, page_no: int, suffix: int):
    """Replay one of the 16 saved graph outputs; no crop pixels are consumed."""
    artifact, region, stem = _page_artifact(contract, side, page_no, suffix)
    graph = json.loads((GRAPHS / f"{stem}-v3-{suffix}.json").read_text())
    assert graph["table_id"] == region["region_id"].replace(":", "-")
    raw_boxes = [c["raw_crop_bbox"] for c in graph["cells"]]
    transform = {"model_to_page": [1, 0, 0, 1, 0, 0], "page_to_model": [1, 0, 0, 1, 0, 0]}
    with (patch("app.services.extractors.contract_v3.tables._tokens", return_value=copy.deepcopy(graph["tokens"])),
          patch("app.services.extractors.contract_v3.tables._native_cells", return_value=None),
          patch("app.services.extractors.contract_v3.tables.upright_table_crop",
                return_value=(np.zeros((1, 1, 3), dtype=np.uint8), transform)),
          patch("app.services.extractors.contract_v3.tables._raster_cells", return_value=raw_boxes),
          patch("app.services.extractors.contract_v3.tables.build_graph", return_value=copy.deepcopy(graph))):
        count = TableResolver().resolve(artifact, object(), Image.new("RGB", (1, 1), "white"))
    assert count == 1
    return artifact["tables"][0], Page.model_validate_json(to_page(artifact).model_dump_json()), graph


def _doc(page):
    return Document(filename="frozen.pdf", path="frozen.pdf", page_count=1, pages=[page])


def test_frozen_validator_failure_survives_resolver_and_json_round_trip():
    for page_no, suffix, reason in ((3, 3, "LOGICAL_SLOT_OVERLAP"), (4, 0, "UNCOVERED_OCR_ROW_BAND")):
        table, page, _ = replay_raster("NANJING_DINGYI_XUZHOU_BANGYU", "compare", page_no, suffix, "mineru")
        assert table["structure_status"] == "UNSTRUCTURED_TABLE_PRESENT"
        assert reason in table["validation"]["reasons"]
        assert table["html"] is None and table["cells"] == []
        assert all(block.structure_status == "UNSTRUCTURED_TABLE_PRESENT" and not block.raw_html
                   and not block.table_cell_bboxes and not block.table_id and block.text and block.table_validation["usable"] is False
                   for block in page.blocks)
        parser = LogicalTableParser()
        assert parser.parse_tables(parser.table_blocks(_doc(page))) == []


def test_resolver_failure_reaches_compare_without_deterministic_delete():
    original = Document.model_validate_json((FROZEN_EXTRACTION / "NANJING_DINGYI_XUZHOU_BANGYU"
                                            / "original-extraction.json").read_text())
    for page_no, suffix in ((3, 3), (4, 0)):
        _, page, _ = replay_raster("NANJING_DINGYI_XUZHOU_BANGYU", "compare", page_no, suffix, "mineru")
        compare = Document.model_validate_json(_doc(page).model_dump_json())
        diffs, _ = TableComparator().build_diffs(original, compare)
        assert diffs and all(d.diff_type != "DELETE" for d in diffs)
        assert any(d.match_score_details.get("decision_reason") in
                   {"TABLE_STRUCTURE_UNRESOLVED", "TABLE_PRESENCE_UNKNOWN"} for d in diffs)


def test_frozen_provider_failure_cannot_be_upgraded_by_validator_pass():
    table, page, source = replay_raster("RETURN_CONFIRMATION", "compare", 3, 5, "graph")
    assert source["issues"]
    assert table["validation"]["metrics"]["slot_overlap_count"] == 0
    assert table["validation"]["reasons"] == ["RASTER_RULE_NOT_CONFIRMED"]
    assert not table["validation"]["usable"] and not table["html"] and not table["cells"]
    assert LogicalTableParser().parse_tables(LogicalTableParser().table_blocks(_doc(page))) == []


def test_frozen_provider_and_validator_pass_keep_structured_compare():
    table, page, source = replay_raster("RETURN_CONFIRMATION", "compare", 3, 5, "paddle")
    assert source["structure_status"] == "STRUCTURE_PASS"
    assert table["validation"]["usable"] and table["structure_status"] == "STRUCTURED_TABLE"
    assert table["html"] and table["cells"]
    parser = LogicalTableParser()
    assert parser.parse_tables(parser.table_blocks(_doc(page)))
    diffs, _ = TableComparator().build_diffs(_doc(page), _doc(page))
    assert diffs == []


def test_frozen_pdf_vector_resolver_publishes_and_round_trips():
    table, page = replay_vector("RETURN_CONFIRMATION", 1, 4)
    assert table["source"] == "PDF_vector_ruling"
    assert table["validation"]["usable"] and table["html"] and table["cells"]
    assert all(block.structure_status == "STRUCTURED_TABLE" for block in page.blocks)
    parser = LogicalTableParser()
    assert parser.parse_tables(parser.table_blocks(_doc(page)))


def test_all_frozen_production_providers_keep_publication_monotonic():
    contracts = ("RETURN_CONFIRMATION", "KAILI_FOUR_PROJECTS", "NANJING_DINGYI_XUZHOU_BANGYU")
    tested = {"graph": 0, "vector": 0, "graph_published": 0, "vector_published": 0}
    for contract in contracts:
        for path in sorted(GRAPHS.glob(f"{contract}-*.json")):
            name = path.stem
            side = "compare" if "-compare-" in name else "original"
            page_no = int(name.rsplit("-p", 1)[1].split("-v3-")[0])
            suffix = int(name.rsplit("-v3-", 1)[1])
            table, page, source = replay_graph(contract, side, page_no, suffix)
            tested["graph"] += 1
            if source["issues"]:
                assert not table["html"] and not table["cells"]
            if table["html"]:
                tested["graph_published"] += 1
                assert table["validation"]["usable"]
            assert all(b.structure_status == table["structure_status"] for b in page.blocks)
        for path in sorted(PAGE_ARTIFACTS.glob(f"{contract}-*.json")):
            artifact = json.loads(path.read_text())
            if "-compare-" in path.stem:
                continue
            page_no = int(path.stem.rsplit("-p", 1)[1])
            for source in artifact.get("tables", []):
                if source["source"] != "PDF_vector_ruling":
                    continue
                suffix = int(source["region_id"].rsplit(":v3:", 1)[1])
                table, page = replay_vector(contract, page_no, suffix)
                tested["vector"] += 1
                tested["vector_published"] += int(bool(table["html"]))
                assert table["validation"]["usable"] and page.blocks[0].structure_status == "STRUCTURED_TABLE"
    assert tested == {"graph": 16, "vector": 9, "graph_published": 3, "vector_published": 9}
