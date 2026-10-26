"""Failure-mode contracts for the optional table service and source evidence."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from PIL import Image, ImageDraw
import pytest

from app.config import Settings
from app.models import BBox, CharBox, TextBlock
from app.services.extractors.contract_v3.artifact import rectangle
from app.services.extractors.contract_v3.table_model_adapter import adapt_candidate
from app.services.extractors.contract_v3.table_raster_evidence import validate_internal_dividers
from app.services.extractors.contract_v3.table_structure_client import TableStructureClient, TableStructureError
from app.services.extractors.contract_v3.tables import TableResolver
from app.services.table_compare.diff_builder import TableDiffBuilder
from app.models_table import StructuredTable

ROOT = Path(__file__).resolve().parents[2]


def config(**kwargs):
    return Settings(_env_file=None, table_structure_mode="enabled", table_structure_wireless_enabled=True, **kwargs)


def response(branch="wireless", width=120, height=80):
    return dict(
        schema_version=1,
        request_id="t",
        width=width,
        height=height,
        classification=dict(label=branch, score=0.99, scores=[0.99, 0.01] if branch == "wired" else [0.01, 0.99]),
        branch=branch,
        model="UNet+TableRecover" if branch == "wired" else "SLANet_plus",
        weight_sha256="a" * 64,
        runtime_version="mineru-4.0.10",
        coordinate_space="CROP_PIXELS",
        padding=0,
        rotated_fix=branch == "wired",
        cells=[
            dict(
                row=0,
                column=0,
                rowspan=1,
                colspan=1,
                bbox=[0, 0, width, height],
                polygon=rectangle([0, 0, width, height]),
                raw_geometry_index=0,
            )
        ],
        elapsed_seconds=0.1,
        additional_ocr_requests=0,
    )


def artifact():
    return dict(
        native_chars=[],
        regions=[
            dict(region_id="t", raw_layout_type="table", bbox=[0, 0, 119, 79], polygon=rectangle([0, 0, 119, 79]))
        ],
        coordinate_transform=dict(native_to_pixel=[1, 0, 0, 1, 0, 0]),
        blocks=[
            dict(
                block_id="b",
                region_id="t",
                source="PP-OCRv6",
                text="500",
                ocr_request_id="o",
                ocr_char_provenance=[
                    dict(char=c, text_index=i, bbox=[10 + 5 * i, 10, 15 + 5 * i, 20]) for i, c in enumerate("500")
                ],
            )
        ],
    )


class Client:
    def __init__(self, result=None, failure=None):
        self.result = result or response()
        self.failure = failure
        self.calls = []

    def predict(self, image, rid, **kwargs):
        self.calls.append(kwargs)
        if self.failure:
            raise self.failure
        result = copy.deepcopy(self.result)
        result["padding"] = kwargs.get("padding", 0)
        return result


def test_wireless_candidate_publishes_without_raster_lines_and_keeps_source_refs():
    page = artifact()
    before = copy.deepcopy(page["blocks"])
    client = Client()
    TableResolver(config(), client).resolve(page, None, Image.new("RGB", (120, 80), "white"))
    table = page["tables"][0]
    assert table["status"] == "CONSISTENT" and table["source"] == "SLANet_plus"
    assert [b["text"] for b in page["blocks"]] == [b["text"] for b in before]
    refs = page["blocks"][0]["table_cells"][0]["token_refs"]
    assert refs == [dict(id=f"b:{i}", block_id="b", text_index=i) for i in range(3)]
    block = TextBlock(
        block_id="b",
        page_no=1,
        text="500",
        bbox=BBox(x0=0, y0=0, x1=120, y1=80),
        table_cells=page["blocks"][0]["table_cells"],
        table_model_diagnostics=table["model_diagnostics"],
    )
    assert TextBlock.model_validate_json(block.model_dump_json()).table_cells == block.table_cells


def test_shadow_preserves_legacy_structure_and_canonical_text():
    old, new = artifact(), artifact()
    image = Image.new("RGB", (120, 80), "white")
    TableResolver(Settings(_env_file=None, table_orientation_mode="off")).resolve(old, None, image)
    cfg = config()
    cfg.table_structure_mode = "shadow"
    TableResolver(cfg, Client()).resolve(new, None, image)
    old["tables"][0]["validation"]["metrics"].pop("validation_seconds")
    new["tables"][0]["validation"]["metrics"].pop("validation_seconds")
    assert {k: v for k, v in new["tables"][0].items() if k != "model_diagnostics"} == old["tables"][0]
    assert new["tables"][0]["model_diagnostics"]["candidate_status"] == "CONSISTENT"
    assert not new["blocks"][0].get("raw_html") and new["blocks"][0]["text"] == "500"


@pytest.mark.parametrize("failure", [TableStructureError("timeout"), TableStructureError("unavailable")])
def test_service_failure_is_regional_and_clears_old_structure(failure):
    page = artifact()
    page["blocks"][0].update(raw_html="<table>stale</table>", table_cell_bboxes=[[0, 0, 1, 1]], table_id="stale")
    TableResolver(config(), Client(failure=failure)).resolve(page, None, Image.new("RGB", (120, 80), "white"))
    assert page["tables"][0]["fallback"]["review_required"]
    assert not page["blocks"][0].get("raw_html") and not page["blocks"][0].get("table_id")
    assert page["blocks"][0]["text"] == "500"


def test_native_rule_success_skips_models_even_in_enabled_mode():
    page = artifact()
    page["native_chars"] = [
        dict(index=i, char=c, bbox=[10 + 5 * i, 10, 15 + 5 * i, 20], usable=True) for i, c in enumerate("500")
    ]
    page["blocks"][0].update(source="native_pdf", native_char_indices=[0, 1, 2])
    pdf = SimpleNamespace(
        find_tables=lambda: SimpleNamespace(tables=[SimpleNamespace(bbox=[0, 0, 119, 79], cells=[[0, 0, 119, 79]])])
    )
    client = Client(failure=AssertionError("Must not call service"))
    TableResolver(config(), client).resolve(page, pdf, Image.new("RGB", (120, 80), "white"))
    assert page["tables"][0]["source"] == "PDF_vector_ruling" and not client.calls


def test_false_internal_divider_retries_bounded_candidates_and_still_rejects():
    page = artifact()
    result = response("wired")
    result["cells"] = [
        dict(
            row=0,
            column=c,
            rowspan=1,
            colspan=1,
            bbox=[60 * c, 0, 60 * (c + 1), 80],
            polygon=rectangle([60 * c, 0, 60 * (c + 1), 80]),
            raw_geometry_index=c,
        )
        for c in range(2)
    ]
    client = Client(result)
    TableResolver(config(), client).resolve(page, None, Image.new("RGB", (120, 80), "white"))
    assert client.calls == [dict(branch="auto", padding=0), dict(branch="wired", padding=64), dict(branch="wireless", padding=0)]
    assert "UNSUPPORTED_INTERNAL_DIVIDER" in page["tables"][0]["validation"]["reasons"]
    assert not page["blocks"][0].get("raw_html")


def test_client_rejects_wrong_coordinate_space_and_timeout():
    for payload in [dict(response(), coordinate_space="PDF_PAGE_COORDS"), dict(response(), request_id="wrong")]:
        client = TableStructureClient(
            config(table_structure_endpoint="http://table.test"),
            httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))),
        )
        with pytest.raises(TableStructureError):
            client.predict(Image.new("RGB", (120, 80)), "t")

    def fail(request):
        raise httpx.ReadTimeout("timeout", request=request)

    client = TableStructureClient(
        config(table_structure_endpoint="http://table.test"), httpx.Client(transport=httpx.MockTransport(fail))
    )
    with pytest.raises(TableStructureError):
        client.predict(Image.new("RGB", (120, 80)), "t")


def test_rotation_transform_preserves_model_topology_and_polygon():
    local, mapped = adapt_candidate(response(), dict(model_to_page=[0, 0.5, -0.5, 0, 100, 10]), "t")
    assert mapped[0]["polygon"] == [[100, 10], [100, 70], [60, 70], [60, 10]]
    assert mapped[0]["bbox"] == [60, 10, 100, 70]
    assert mapped[0]["row"] == local[0]["row"] == 0


def test_real_known_title_false_split_is_rejected():
    path = ROOT / "output/analysis/table-solution-exploration-20261005/late-retry/PO202604090100-compare-p8:v3:12.json"
    if not path.exists():
        pytest.skip("Known regression artifact unavailable")
    a = json.loads(path.read_text())["selected"]
    manifest = json.loads(
        (ROOT / "output/analysis/mineru-unet-validation-20261005/holdout/input-manifest.json").read_text()
    )
    region = next(r for r in manifest["regions"] if r["region_id"] == a["region_id"])
    check = validate_internal_dividers(a, Image.open(ROOT / region["crop_path"]))
    assert not check.usable and "UNSUPPORTED_INTERNAL_DIVIDER" in check.reasons
    assert any(c["row"] == 1 for c in check.metrics["unsupported_segments"])


def test_true_empty_internal_cells_and_merged_title_keep_raster_support():
    image = Image.new("RGB", (200, 160), "white")
    draw = ImageDraw.Draw(image)
    for y in (10, 60, 110, 150):
        draw.line((10, y, 190, y), fill="black", width=2)
    for x in (10, 190):
        draw.line((x, 10, x, 150), fill="black", width=2)
    draw.line((100, 60, 100, 150), fill="black", width=2)
    cells = [dict(row=0, column=0, rowspan=1, colspan=2, bbox=[10, 10, 190, 60])]
    for row, (y0, y1) in enumerate([(60, 110), (110, 150)], 1):
        for col, (x0, x1) in enumerate([(10, 100), (100, 190)]):
            cells.append(dict(row=row, column=col, rowspan=1, colspan=1, bbox=[x0, y0, x1, y1]))
    assert validate_internal_dividers(dict(cells=cells), image).usable


def source_block(name, text, box, region="r"):
    return TextBlock(
        block_id=name,
        page_no=1,
        text=text,
        bbox=box,
        block_type="table",
        layout_block_id=region,
        char_boxes=[CharBox(char=c, page_no=1, text_index=i, bbox=box) for i, c in enumerate(text)],
    )


def test_amount_evidence_comes_from_other_block_and_repeated_amount_is_spatially_disambiguated():
    box = BBox(x0=50, y0=10, x1=90, y1=30)
    anchor = source_block("anchor", "不含税价", BBox(x0=0, y0=0, x1=30, y1=10))
    amount = source_block("amount", "64500", box)
    duplicate = source_block("repeat", "64500", BBox(x0=50, y0=40, x1=90, y1=60))
    unrelated = source_block("other", "64500", box, "other-region")
    builder = TableDiffBuilder(None, None)
    table = StructuredTable(page_no=1, rows=[], col_count=1, source_block_id="anchor")
    group = builder.find_blocks([(b, b.text) for b in (anchor, amount, duplicate, unrelated)], table)
    assert {b.block_id for b, _ in group} == {"anchor", "amount", "repeat"}
    builder.source_blocks["compare"] = {b.block_id: b for b, _ in group}
    assert builder._table_fragment_bbox("compare", anchor, box, "64500", box) == box
    builder.source_blocks["compare"]["ambiguous"] = source_block("ambiguous", "64500", BBox(x0=55, y0=11, x1=85, y1=29))
    assert builder._table_fragment_bbox("compare", anchor, box, "64500", box) is None


def test_clear_raster_merges_false_title_dividers_and_keeps_real_amount_boundary():
    from app.services.extractors.contract_v3.table_raster_evidence import repair_clear_merged_rows

    image = Image.new("RGB", (200, 160), "white")
    draw = ImageDraw.Draw(image)
    for y in (10, 60, 110, 150):
        draw.line((10, y, 190, y), fill="black", width=2)
    for x in (10, 190):
        draw.line((x, 10, x, 150), fill="black", width=2)
    draw.line((100, 60, 100, 150), fill="black", width=2)
    cells = []
    for row, (y0, y1) in enumerate([(10, 60), (60, 110), (110, 150)]):
        for col, (x0, x1) in enumerate([(10, 100), (100, 190)]):
            cells.append(dict(cell_id=f"{row}:{col}", row=row, column=col, rowspan=1, colspan=1, bbox=[x0, y0, x1, y1]))
    repaired, repairs = repair_clear_merged_rows(cells, image)
    assert len(repaired) == 5 and len(repairs) == 1
    assert repaired[0]["row"] == 0 and repaired[0]["colspan"] == 2
    assert [c["colspan"] for c in repaired if c["row"] == 1] == [1, 1]
    assert validate_internal_dividers(dict(cells=repaired), image).usable
    # Missing outer border cannot substantiate a merge.
    image = Image.new("RGB", (200, 160), "white")
    assert not repair_clear_merged_rows(cells, image)[1]


def test_published_topology_is_not_replaced_by_bbox_clustering():
    from app.services.table_compare.html_parser import parse_html_tables
    from unittest.mock import patch

    html = '<table><tr><td colspan="2">标题</td></tr><tr><td>A</td><td>B</td></tr></table>'
    boxes = [BBox(x0=10, y0=10, x1=190, y1=60), BBox(x0=10, y0=60, x1=100, y1=110), BBox(x0=100, y0=60, x1=190, y1=110)]
    with patch(
        "app.services.table_compare.html_parser.BBoxGridAnalyzer.analyze",
        side_effect=AssertionError("No second topology inference"),
    ):
        table = parse_html_tables(html, cell_bboxes=boxes, trusted_topology=True)[0]
    assert table.col_count == 2 and table.rows[0].cells[0].colspan == 2
    assert table.geometry_strategy == "published_cell_topology"


def test_character_box_baseline_jitter_cannot_reverse_source_text():
    from app.services.extractors.contract_v3.tables import _publish, _tokens

    a = artifact()
    a["blocks"][0]["text"] = "序号"
    a["blocks"][0]["ocr_char_provenance"] = [
        dict(char="序", text_index=0, bbox=[10, 13, 20, 23]),
        dict(char="号", text_index=1, bbox=[20, 11, 30, 21]),
    ]
    table = _publish(a, a["regions"][0], response()["cells"], _tokens(a, a["regions"][0]), "UNet+TableRecover")
    assert table["cells"][0]["text"] == "序号"
    assert [t["text_index"] for t in table["cells"][0]["tokens"]] == [0, 1]


def test_quarter_rotated_page_reads_in_upright_crop_order():
    from app.services.extractors.contract_v3.tables import _publish

    region = dict(region_id="t", bbox=[0, 0, 100, 100])
    a = dict(blocks=[dict(block_id="b", region_id="t")])
    # In native page coordinates B is left of A. The renderer rotates the
    # page, making A the upper line. Preserve the source text inside each span.
    tokens = [
        dict(id="a", block_id="a", text="计量", text_index=0, bbox=[70, 10, 90, 20]),
        dict(id="b", block_id="b", text="单位", text_index=0, bbox=[40, 10, 60, 20]),
    ]
    cell = dict(row=0, column=0, rowspan=1, colspan=1, bbox=[0, 0, 100, 100])
    result = _publish(a, region, [cell], tokens, "UNet+TableRecover", page_to_model=[0, -1, 1, 0, 0, 100])
    assert result["cells"][0]["text"] == "计量单位"


def test_precise_and_missing_source_evidence_have_explicit_granularity():
    from app.models_table import TableCell, TableRow
    from app.services.table_compare.types import CellDiff, CellDiffGroup

    full = BBox(x0=0, y0=0, x1=100, y1=50)
    tight = BBox(x0=10, y0=10, x1=30, y1=20)
    block = source_block("amount", "500", tight)
    block.table_cells = [dict(bbox=[0, 0, 100, 50], token_refs=[dict(block_id="amount", text_index=0)])]
    table = StructuredTable(
        page_no=1,
        col_count=1,
        source_block_id="amount",
        rows=[TableRow(row_index=0, cells=[TableCell(row_index=0, col_index=0, text="500", bbox=full)])],
    )
    group = CellDiffGroup(0, [CellDiff(0, 0, "", "500", "MODIFY")])
    builder = TableDiffBuilder(None, None)
    evidence = builder._char_level_evidence(table, group, block, "compare")[0]
    assert evidence.bbox == tight and evidence.granularity == "source_text"
    block.char_boxes = []
    evidence = builder._char_level_evidence(table, group, block, "compare")[0]
    assert evidence.bbox == full and evidence.granularity == "cell"


def test_same_line_blocks_with_baseline_jitter_read_left_to_right():
    from app.services.extractors.contract_v3.tables import _publish

    region = dict(region_id="t", bbox=[0, 0, 200, 100])
    a = dict(blocks=[dict(block_id="b", region_id="t")])
    tokens = [
        dict(id="left", block_id="left", text="□同意", text_index=0, bbox=[10, 13, 65, 30]),
        dict(id="right", block_id="right", text="□不同意", text_index=0, bbox=[100, 11, 175, 28]),
        dict(id="date", block_id="date", text="年月日", text_index=0, bbox=[100, 60, 175, 78]),
    ]
    cell = dict(row=0, column=0, rowspan=1, colspan=1, bbox=[0, 0, 200, 100])
    result = _publish(a, region, [cell], tokens, "UNet+TableRecover")
    assert result["cells"][0]["text"] == "□同意□不同意年月日"


def test_legacy_cell_order_conflict_requires_canonical_source_contradiction():
    from app.models_table import TableCell, TableRow
    from app.services.table_compare.types import CellDiff, CellDiffGroup

    full = BBox(x0=0, y0=0, x1=100, y1=50)
    original = source_block("original", "C 类", full)
    compare = source_block("compare", "C 类", full)
    compare.table_cells = [dict(token_refs=[dict(block_id="compare")])]
    compare.table_validation = dict(topology_source="UNet+TableRecover", usable=True)
    tables = [
        StructuredTable(
            page_no=1,
            col_count=1,
            source_block_id=b.block_id,
            rows=[TableRow(row_index=0, cells=[TableCell(row_index=0, col_index=0, text=b.text, bbox=full)])],
        )
        for b in (original, compare)
    ]
    builder = TableDiffBuilder(None, None)
    group = CellDiffGroup(0, [CellDiff(0, 0, "类C", "C 类", "MODIFY")])
    assert builder._group_source_order_conflict(group, *tables, original, compare)
    original.text = "类C"
    assert not builder._group_source_order_conflict(group, *tables, original, compare)
    # A real changed amount with different digit order is retained.
    original.text = "18"
    compare.text = "81"
    group = CellDiffGroup(0, [CellDiff(0, 0, "18", "81", "MODIFY")])
    assert not builder._group_source_order_conflict(group, *tables, original, compare)


def test_model_provenance_does_not_change_legacy_or_shadow_topology_parsing():
    from app.services.table_compare.parser import LogicalTableParser
    from app.services.table_compare.html_parser import parse_html_tables
    from unittest.mock import patch

    block = source_block("anchor", "标题", BBox(x0=0, y0=0, x1=100, y1=50))
    block.raw_html = "<table><tr><td>标题</td></tr></table>"
    block.table_cell_bboxes = [[0, 0, 100, 50]]
    block.table_cells = [dict(cell_id="0", token_refs=[])]
    block.table_validation = dict(usable=True, topology_source="PDF_vector_ruling")
    block.table_model_diagnostics = dict(mode="shadow", selected_branch="wired")
    parser = LogicalTableParser()
    with patch("app.services.table_compare.parser.parse_html_tables", wraps=parse_html_tables) as spy:
        assert parser.parse_tables([(block, block.raw_html)])
        assert not spy.call_args.kwargs["trusted_topology"]
    block.table_validation["topology_source"] = "UNet+TableRecover"
    with patch("app.services.table_compare.parser.parse_html_tables", wraps=parse_html_tables) as spy:
        assert parser.parse_tables([(block, block.raw_html)])
        assert spy.call_args.kwargs["trusted_topology"]


@pytest.mark.parametrize('recovered', [True, False])
def test_missing_cell_has_one_bounded_context_retry_and_preserves_ocr(recovered):
    import numpy as np
    # A real ruled 2x2 topology; losing the bottom-right contour must not publish
    # the three surviving cells as a complete table.
    image = Image.new('RGB', (120, 80), 'white')
    draw = ImageDraw.Draw(image)
    for x in (0, 60, 119):
        draw.line((x, 0, x, 79), fill='black', width=2)
    for y in (0, 40, 79):
        draw.line((0, y, 119, y), fill='black', width=2)
    cells = [dict(row=r, column=c, rowspan=1, colspan=1, bbox=[60*c,40*r,min(119,60*(c+1)),min(79,40*(r+1))],
                  polygon=rectangle([60*c,40*r,min(119,60*(c+1)),min(79,40*(r+1))]), raw_geometry_index=r*2+c)
             for r in range(2) for c in range(2)]
    class MissingCellClient(Client):
        def predict(self, image, rid, **kwargs):
            self.calls.append(kwargs)
            value=response('wired')
            value.update(cells=cells if recovered and kwargs.get('padding') == 128 else cells[:-1],padding=kwargs.get('padding',0))
            return value
    page=artifact()
    before=copy.deepcopy(page['blocks'])
    client=MissingCellClient()
    cfg=config(table_structure_alternate_enabled=False)
    tokens=[dict(id='b:0',text='5',block_id='b',text_index=0,bbox=[80,50,85,60])]
    result=TableResolver(cfg,client)._model(page,page['regions'][0],np.asarray(image),tokens)
    assert [c['padding'] for c in client.calls] == [0,64,128]
    assert (result['status']=='CONSISTENT') is recovered
    assert page['blocks'][0]['text']==before[0]['text']
    if recovered:
        assert result['assigned_token_count']==1 and result['candidate_cell_count']==4
    else:
        assert not result['cells'] and not result['validation']['usable']
