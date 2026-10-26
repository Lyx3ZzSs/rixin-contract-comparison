"""Regressions for noise-tainted table metadata and uncertain row events."""

from PIL import Image, ImageDraw
import pytest
from app.models import BBox, CompareTask, DiffItem, Document, Page, TextBlock
from app.services.table_compare import TableComparator
from app.services.diff_events import event_response
from app.services.extractors.contract_v3.table_raster_evidence import validate_raster_evidence


def document(name="甲项目", noise="", extra=False):
    title = f"项目名称：{name}报价单位：人民币元"
    cells = '<td colspan="10">' + title + "</td>"
    if noise:
        cells += '<td rowspan="3">' + noise + "</td>"
    header = "".join(
        "<td>" + v + "</td>" for v in ["序号", "名称", "型号", "单位", "数量", "产地", "厂家", "单价", "总价", "备注"]
    )

    def row(n, label):
        return (
            "<tr>"
            + "".join("<td>" + v + "</td>" for v in [n, label, "ABC", "台", "1", "中国", "某公司", "100", "100", ""])
            + "</tr>"
        )

    html = (
        "<table><tr>"
        + cells
        + "</tr><tr>"
        + header
        + "</tr>"
        + row("1", "服务器")
        + (row("2", "交换机") if extra else "")
        + "</table>"
    )
    block = TextBlock(
        block_id="table", page_no=3, text=html, block_type="table", bbox=BBox(x0=10, y0=100, x1=550, y1=400)
    )
    return Document(
        filename="x.pdf", path="x.pdf", page_count=1, pages=[Page(page_no=3, width=600, height=800, blocks=[block])]
    )


def test_same_metadata_with_spanning_noise_is_not_added_row():
    diffs, _ = TableComparator().build_diffs(document(), document(noise="Bià"))
    assert not any(d.diff_type == "ADD" and "项目名称" in d.compare_text for d in diffs)


def test_real_project_name_change_survives_title_classification():
    diffs, _ = TableComparator().build_diffs(document("甲项目"), document("乙工程"))
    assert any("甲项目" in d.original_text and "乙工程" in d.compare_text and d.diff_type == "MODIFY" for d in diffs)


def test_real_new_purchase_row_is_retained():
    diffs, _ = TableComparator().build_diffs(document(), document(extra=True))
    assert any(d.diff_type == "ADD" and "交换机" in d.compare_text for d in diffs)


def edge_candidate(confidence=0.5, outer_rule=False, width=8, spanning=True):
    image = Image.new("RGB", (300, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.line((270, 10, 270, 390), fill="black", width=2)
    if outer_rule:
        draw.line((270 + width, 10, 270 + width, 390), fill="black", width=2)
    cell = dict(
        cell_id="edge", row=0, col=10, rowspan=15 if spanning else 1, colspan=1, bbox=[272, 12, 270 + width - 1, 388]
    )
    artifact = dict(cols=11, rows=15, cells=[cell])
    ocr = [dict(text="Bià", bbox=[272, 180, 270 + width - 1, 200], confidence=confidence)]
    return artifact, image, ocr


@pytest.mark.parametrize("confidence", [0.5, 0.99])
def test_tall_narrow_edge_column_requires_real_outer_boundary(confidence):
    assert not validate_raster_evidence(*edge_candidate(confidence)).usable


def test_real_narrow_spanning_column_with_rules_is_retained():
    assert validate_raster_evidence(*edge_candidate(0.5, outer_rule=True, width=18)).usable


def test_low_confidence_text_does_not_exempt_normal_edge_column():
    assert not validate_raster_evidence(*edge_candidate(0.5, width=25, spanning=False)).usable


def test_known_text_in_normal_column_remains_valid():
    assert validate_raster_evidence(*edge_candidate(0.99, width=25, spanning=False)).usable


@pytest.mark.parametrize(
    "decision,title",
    [("NEEDS_REVIEW", "表格行对应关系待复核"), ("CANDIDATE", "表格行对应关系待复核"), ("CONFIRMED", "表格整行新增")],
)
def test_row_event_title_respects_decision(decision, title):
    cell = {
        "row": 0,
        "col": 0,
        "rowspan": 1,
        "colspan": 1,
        "page_no": 3,
        "text": "项目名称：甲",
        "bbox": {"x0": 10, "y0": 10, "x1": 200, "y1": 30},
        "table_id": "t",
    }
    d = DiffItem(
        diff_id="D1",
        diff_type="ADD",
        source_type="table",
        compare_text=cell["text"],
        match_score_details={
            "change_decision": decision,
            "table_change_scope": "row",
            "cell_observations": [{"diff_type": "ADD", "original_cell": None, "compare_cell": cell}],
        },
    )
    response = event_response(CompareTask(task_id="t", diffs=[d]))
    assert response["events"][0]["diff"]["title"] == title


def test_metadata_in_two_plain_blocks_recovers_missing_grid_row():
    original = document()
    block = original.pages[0].blocks[0]
    block.text = block.text.replace('<tr><td colspan="10">项目名称：甲项目报价单位：人民币元</td></tr>', "")
    for i, (text, x) in enumerate([("项目名称：甲项目", 10), ("报价单位：人民币元", 350)]):
        original.pages[0].blocks.append(
            TextBlock(
                block_id=f"line{i}",
                page_no=3,
                text=text,
                block_type="text",
                bbox=BBox(x0=x, y0=100, x1=x + 140, y1=120),
            )
        )
    cmp = TableComparator()
    diffs, _ = cmp.build_diffs(original, document())
    assert not any("项目名称" in d.compare_text for d in diffs)
    assert (
        cmp.last_debug_payload["suppressed_diffs"]["suppressed_diff_counts_by_reason"]["metadata_row_present_in_source"]
        == 1
    )
    # A different value must not be hidden by the same evidence-recovery path.
    changed, _ = TableComparator().build_diffs(original, document("乙工程"))
    assert any("乙工程" in d.compare_text for d in changed)


def test_low_confidence_source_reading_is_carried_to_table_diff():
    original = document("甲项目")
    compare = document("乙工程")
    for doc, score in [(original, 0.99), (compare, 0.5)]:
        text = "项目名称：" + ("甲项目" if doc is original else "乙工程") + "报价单位：人民币元"
        owner = doc.pages[0].blocks[0]
        owner.raw_html = owner.text
        owner.text = text
        owner.confidence = score
        owner.table_cell_bboxes = [[10, 100, 550, 120]] + [
            [10 + 54 * c, 120 + 40 * r, 10 + 54 * (c + 1), 160 + 40 * r] for r in range(2) for c in range(10)
        ]
    diffs, _ = TableComparator().build_diffs(original, compare)
    assert diffs
    d = next(d for d in diffs if "乙工程" in d.compare_text)
    assert d.text_confidence == 0.5 and d.content_recognition_status == "NEEDS_REVIEW"
    assert d.match_score_details["change_decision"] == "NEEDS_REVIEW"


@pytest.mark.parametrize("score,outer,expected", [(0.5, False, True), (0.99, False, False), (0.5, True, False)])
def test_edge_repair_requires_absent_outer_rule_and_unreliable_text(score, outer, expected):
    from app.services.extractors.contract_v3.table_raster_evidence import repair_verified_edge_strip

    artifact, image, tokens = edge_candidate(score, outer_rule=outer, width=8)
    artifact["cells"][0]["bbox"][0] = 271
    ghost = artifact["cells"][0]
    ghost["column"] = ghost.pop("col")
    tokens[0]["id"] = "noise"
    main = dict(cell_id="main", row=0, column=0, rowspan=15, colspan=10, bbox=[10, 12, 270, 388])
    cells = [main, ghost]
    candidate, kept, audit = repair_verified_edge_strip(cells, image, tokens)
    assert bool(audit) == expected
    if expected:
        assert candidate == [main] and not kept
        assert audit[0]["excluded_tokens"] == tokens
        assert cells[1]["column"] == 10 and tokens[0]["text"] == "Bià"  # original evidence retained
    else:
        assert candidate == cells and kept == tokens


def test_faint_real_outer_rule_vetoes_edge_strip_repair():
    from app.services.extractors.contract_v3.table_raster_evidence import repair_verified_edge_strip

    artifact, image, tokens = edge_candidate(0.5, width=8)
    ghost = artifact["cells"][0]
    ghost["column"] = ghost.pop("col")
    ghost["bbox"][0] = 271
    tokens[0]["id"] = "noise"
    ImageDraw.Draw(image).line((278, 10, 278, 390), fill=(220, 220, 220), width=1)
    main = dict(cell_id="main", row=0, column=0, rowspan=15, colspan=10, bbox=[10, 12, 270, 388])
    candidate, kept, audit = repair_verified_edge_strip([main, ghost], image, tokens)
    assert not audit and len(candidate) == 2 and kept == tokens


def test_metadata_split_into_two_cells_is_equivalent():
    original = document()
    compare = document()
    block = compare.pages[0].blocks[0]
    block.text = block.text.replace(
        '<td colspan="10">项目名称：甲项目报价单位：人民币元</td>',
        '<td colspan="5">项目名称：甲项目</td><td colspan="5">报价单位：人民币元</td>',
    )
    diffs, _ = TableComparator().build_diffs(original, compare)
    assert not diffs
