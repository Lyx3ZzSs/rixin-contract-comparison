from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.table_compare import TableComparator


def _make_table_block(block_id: str, page_no: int, html: str, bbox: BBox | None = None) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=page_no,
        text=html,
        bbox=bbox or BBox(x0=50, y0=100, x1=540, y1=500),
        block_type="table",
    )


def _make_raw_table_block(
    block_id: str,
    page_no: int,
    html: str,
    text: str,
    cell_bboxes: list[list[float]] | None = None,
) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=page_no,
        text=text,
        raw_html=html,
        bbox=BBox(x0=50, y0=100, x1=540, y1=500),
        block_type="table",
        table_cell_bboxes=cell_bboxes or [],
    )


def _make_doc(blocks: list[TextBlock]) -> Document:
    pages_by_no: dict[int, list[TextBlock]] = {}
    for b in blocks:
        pages_by_no.setdefault(b.page_no, []).append(b)
    pages = [Page(page_no=pn, width=595, height=842, blocks=blocks) for pn, blocks in sorted(pages_by_no.items())]
    return Document(filename="test.pdf", path="test.pdf", page_count=len(pages), pages=pages)


def _product_table(rows: list[str]) -> str:
    header = (
        "<tr><td>序号</td><td>产品名称</td><td>详细配置</td><td>品牌</td>"
        "<td>单位</td><td>数量</td><td>单价</td><td>金额</td><td>备注</td></tr>"
    )
    return "<table>" + header + "".join(rows) + "</table>"


def _product_row(seq: str, name: str, amount: str = "100") -> str:
    return (
        f"<tr><td>{seq}</td><td>{name}</td><td>{name}配置</td><td>国能日新</td>"
        f"<td>套</td><td>1</td><td>{amount}</td><td>{amount}</td><td></td></tr>"
    )


CONTRACT_TABLE_HTML = (
    '<div style="text-align: center;"><html><body><table border="1">'
    "<tr><td>甲方</td><td>江苏东大金智信息系统有限公司</td></tr>"
    "<tr><td>乙方</td><td>国能日新科技股份有限公司</td></tr>"
    "<tr><td>签订地点</td><td>北京</td></tr>"
    "<tr><td>签订日期</td><td>2026年4月 日</td></tr>"
    "</table></body></html></div>"
)


class TestStructuredTableComparison:
    def test_identical_tables_no_diffs(self):
        doc = _make_doc([_make_table_block("t1", 1, CONTRACT_TABLE_HTML)])
        diffs, warnings = TableComparator().build_diffs(doc, doc)
        assert diffs == []
        assert warnings == []

    def test_single_cell_change_detected(self):
        original_html = (
            '<table><tr><td>甲方</td><td>江苏东大</td></tr>'
            "<tr><td>签订日期</td><td>2026年4月 日</td></tr></table>"
        )
        compare_html = (
            '<table><tr><td>甲方</td><td>江苏东大</td></tr>'
            "<tr><td>签订日期</td><td>2026年4月21日</td></tr></table>"
        )
        orig = _make_doc([_make_table_block("o1", 1, original_html)])
        comp = _make_doc([_make_table_block("c1", 1, compare_html)])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert len(diffs) == 1
        assert diffs[0].diff_type == "MODIFY"
        assert "2026年4月 日" in diffs[0].original_text
        assert "2026年4月21日" in diffs[0].compare_text
        assert diffs[0].original_evidence == []
        assert diffs[0].compare_evidence[0].method == "table_cell"
        assert diffs[0].compare_evidence[0].highlight_type == "ADD"

    def test_no_tables_returns_empty(self):
        doc = _make_doc([
            TextBlock(block_id="b1", page_no=1, text="some text", bbox=BBox(x0=0, y0=0, x1=100, y1=20), block_type="text")
        ])
        diffs, warnings = TableComparator().build_diffs(doc, doc)
        assert diffs == []
        assert "未获得结构化表格区域" in warnings[0]

    def test_table_only_in_original_produces_delete(self):
        html = '<table><tr><td>A</td><td>B</td></tr></table>'
        orig = _make_doc([_make_table_block("o1", 1, html)])
        comp = _make_doc([])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert any(d.diff_type == "DELETE" for d in diffs)

    def test_table_only_in_compare_produces_add(self):
        html = '<table><tr><td>A</td><td>B</td></tr></table>'
        orig = _make_doc([])
        comp = _make_doc([_make_table_block("c1", 1, html)])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert any(d.diff_type == "ADD" for d in diffs)

    def test_colspan_table_comparison(self):
        orig_html = (
            '<table border="1">'
            "<tr><td>序号</td><td>产品</td><td>金额</td></tr>"
            '<tr><td colspan="3">风电系统</td></tr>'
            "<tr><td>1</td><td>服务器</td><td>8500</td></tr></table>"
        )
        comp_html = (
            '<table border="1">'
            "<tr><td>序号</td><td>产品</td><td>金额</td></tr>"
            '<tr><td colspan="3">光伏系统</td></tr>'
            "<tr><td>1</td><td>服务器</td><td>9500</td></tr></table>"
        )
        orig = _make_doc([_make_table_block("o1", 1, orig_html)])
        comp = _make_doc([_make_table_block("c1", 1, comp_html)])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert len(diffs) >= 2  # "风电系统"->"光伏系统" and "8500"->"9500"
        texts = [d.original_text + d.compare_text for d in diffs]
        combined = " ".join(texts)
        assert "风电系统" in combined or "光伏系统" in combined

    def test_evidence_has_page_and_bbox(self):
        orig_html = '<table><tr><td>名称</td><td>Server-X</td></tr></table>'
        comp_html = '<table><tr><td>名称</td><td>Server-Y</td></tr></table>'
        bbox = BBox(x0=60, y0=200, x1=500, y1=400)
        orig = _make_doc([_make_table_block("o1", 3, orig_html, bbox)])
        comp = _make_doc([_make_table_block("c1", 3, comp_html, bbox)])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert len(diffs) == 1
        assert diffs[0].original_evidence[0].page_no == 3
        assert diffs[0].original_evidence[0].bbox == bbox

    def test_evidence_uses_layout_bbox_when_available(self):
        orig_html = '<table><tr><td>名称</td><td>Server-X</td></tr></table>'
        comp_html = '<table><tr><td>名称</td><td>Server-Y</td></tr></table>'
        ocr_bbox = BBox(x0=60, y0=200, x1=120, y1=220)
        layout_bbox = BBox(x0=50, y0=180, x1=500, y1=400)
        orig_block = _make_table_block("o1", 3, orig_html, ocr_bbox)
        orig_block.layout_bbox = layout_bbox
        comp_block = _make_table_block("c1", 3, comp_html, ocr_bbox)
        comp_block.layout_bbox = layout_bbox
        orig = _make_doc([orig_block])
        comp = _make_doc([comp_block])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert len(diffs) == 1
        assert diffs[0].original_evidence[0].bbox == layout_bbox
        assert diffs[0].compare_evidence[0].bbox == layout_bbox

    def test_row_added_detected(self):
        orig_html = '<table><tr><td>A</td></tr><tr><td>B</td></tr></table>'
        comp_html = '<table><tr><td>A</td></tr><tr><td>B</td></tr><tr><td>C</td></tr></table>'
        orig = _make_doc([_make_table_block("o1", 1, orig_html)])
        comp = _make_doc([_make_table_block("c1", 1, comp_html)])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert len(diffs) >= 1
        has_add = any("C" in d.compare_text for d in diffs if d.diff_type == "ADD")
        assert has_add

    def test_row_deleted_detected(self):
        orig_html = '<table><tr><td>A</td></tr><tr><td>B</td></tr><tr><td>C</td></tr></table>'
        comp_html = '<table><tr><td>A</td></tr><tr><td>B</td></tr></table>'
        orig = _make_doc([_make_table_block("o1", 1, orig_html)])
        comp = _make_doc([_make_table_block("c1", 1, comp_html)])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert len(diffs) >= 1
        has_delete = any("C" in d.original_text for d in diffs if d.diff_type == "DELETE")
        assert has_delete

    def test_noise_rows_filtered(self):
        orig_html = '<table><tr><td>小计</td><td>12000</td></tr></table>'
        comp_html = '<table><tr><td>小计</td><td>12000</td></tr></table>'
        orig = _make_doc([_make_table_block("o1", 1, orig_html)])
        comp = _make_doc([_make_table_block("c1", 1, comp_html)])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert diffs == []

    def test_multiple_tables_matched_correctly(self):
        cover_html = '<table><tr><td>甲方</td><td>公司A</td></tr></table>'
        product_html = '<table><tr><td>序号</td><td>产品</td></tr><tr><td>1</td><td>服务器</td></tr></table>'
        cover_html2 = '<table><tr><td>甲方</td><td>公司A</td></tr></table>'
        product_html2 = '<table><tr><td>序号</td><td>产品</td></tr><tr><td>1</td><td>工作站</td></tr></table>'
        orig = _make_doc([
            _make_table_block("o1", 1, cover_html),
            _make_table_block("o2", 2, product_html),
        ])
        comp = _make_doc([
            _make_table_block("c1", 1, cover_html2),
            _make_table_block("c2", 2, product_html2),
        ])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert len(diffs) == 1
        assert "服务器" in diffs[0].original_text
        assert "工作站" in diffs[0].compare_text

    def test_real_ppstructure_format(self):
        comp_html = CONTRACT_TABLE_HTML.replace("2026年4月 日", "2026年4月21日")
        orig = _make_doc([_make_table_block("o1", 1, CONTRACT_TABLE_HTML)])
        comp = _make_doc([_make_table_block("c1", 1, comp_html)])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert len(diffs) == 1
        assert "2026年4月21日" in diffs[0].compare_text

    def test_evidence_uses_cell_bbox_from_table_cell_bboxes(self):
        orig_html = '<table><tr><td>名称</td><td>Server-X</td></tr></table>'
        comp_html = '<table><tr><td>名称</td><td>Server-Y</td></tr></table>'
        ocr_bbox = BBox(x0=60, y0=200, x1=120, y1=220)
        cell_bboxes = [
            [0, 200, 50, 220],   # "名称"
            [50, 200, 500, 220],  # "Server-X" / "Server-Y"
        ]
        orig_block = TextBlock(
            block_id="o1", page_no=3, text=orig_html, bbox=ocr_bbox,
            block_type="table", table_cell_bboxes=cell_bboxes,
        )
        comp_block = TextBlock(
            block_id="c1", page_no=3, text=comp_html, bbox=ocr_bbox,
            block_type="table", table_cell_bboxes=cell_bboxes,
        )
        orig = _make_doc([orig_block])
        comp = _make_doc([comp_block])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert len(diffs) == 1
        # Evidence bbox should be the changed character inside the specific cell.
        assert diffs[0].original_evidence[0].bbox == BBox(x0=443.75, y0=200, x1=500, y1=220)
        assert diffs[0].compare_evidence[0].bbox == BBox(x0=443.75, y0=200, x1=500, y1=220)
        assert diffs[0].original_evidence[0].text == "X"
        assert diffs[0].compare_evidence[0].text == "Y"

    def test_similarity_threshold_filters_punctuation_ocr_noise(self):
        orig_html = '<table><tr><td>配置</td><td>2TSATA;</td></tr></table>'
        comp_html = '<table><tr><td>配置</td><td>2T SATA：</td></tr></table>'
        orig = _make_doc([_make_table_block("o1", 1, orig_html)])
        comp = _make_doc([_make_table_block("c1", 1, comp_html)])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert diffs == []

    def test_similarity_threshold_filters_single_ambiguous_ocr_character(self):
        orig_html = '<table><tr><td>型号</td><td>StoneWall-2000BF</td></tr></table>'
        comp_html = '<table><tr><td>型号</td><td>StoneWall-200BF</td></tr></table>'
        orig = _make_doc([_make_table_block("o1", 1, orig_html)])
        comp = _make_doc([_make_table_block("c1", 1, comp_html)])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert diffs == []

    def test_high_similarity_business_change_is_not_filtered(self):
        original_html = '<table><tr><td>签订日期</td><td>2026年4月 日</td></tr></table>'
        compare_html = '<table><tr><td>签订日期</td><td>2026年4月21日</td></tr></table>'
        cell_bboxes = [
            [0, 200, 100, 220],
            [100, 200, 300, 220],
        ]
        orig_block = TextBlock(
            block_id="o1", page_no=1, text=original_html, bbox=BBox(x0=0, y0=200, x1=300, y1=220),
            block_type="table", table_cell_bboxes=cell_bboxes,
        )
        comp_block = TextBlock(
            block_id="c1", page_no=1, text=compare_html, bbox=BBox(x0=0, y0=200, x1=300, y1=220),
            block_type="table", table_cell_bboxes=cell_bboxes,
        )
        orig = _make_doc([orig_block])
        comp = _make_doc([comp_block])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert len(diffs) == 1
        assert diffs[0].compare_evidence[0].text == "21"
        assert diffs[0].compare_evidence[0].bbox.x0 > 200
        assert diffs[0].compare_evidence[0].bbox.x1 < 300

    def test_table_evidence_prefers_ocr_char_boxes_for_inserted_text(self):
        original_html = '<table><tr><td>签订日期</td><td>2026年4月 日</td></tr></table>'
        compare_html = '<table><tr><td>签订日期</td><td>2026年4月21日</td></tr></table>'
        cell_bboxes = [
            [0, 200, 100, 220],
            [100, 200, 300, 220],
        ]
        compare_text = "签订日期\n2026年4月21日"
        date_start = compare_text.index("2026")
        comp_block = TextBlock(
            block_id="c1",
            page_no=1,
            text=compare_text,
            bbox=BBox(x0=0, y0=200, x1=300, y1=220),
            block_type="table",
            raw_html=compare_html,
            table_cell_bboxes=cell_bboxes,
            char_boxes=[
                CharBox(
                    char=char,
                    page_no=1,
                    bbox=BBox(x0=170 + offset * 10, y0=202, x1=180 + offset * 10, y1=218),
                    text_index=date_start + offset,
                )
                for offset, char in enumerate("2026年4月21日")
            ],
        )
        orig_block = TextBlock(
            block_id="o1",
            page_no=1,
            text="签订日期\n2026年4月 日",
            bbox=BBox(x0=0, y0=200, x1=300, y1=220),
            block_type="table",
            raw_html=original_html,
            table_cell_bboxes=cell_bboxes,
        )

        diffs, warnings = TableComparator().build_diffs(_make_doc([orig_block]), _make_doc([comp_block]))

        assert len(diffs) == 1
        assert diffs[0].original_evidence == []
        assert diffs[0].compare_evidence[0].highlight_type == "ADD"
        assert diffs[0].compare_evidence[0].text == "21"
        assert diffs[0].compare_evidence[0].bbox == BBox(x0=240, y0=202, x1=260, y1=218)

    def test_cross_page_product_tables_are_stitched_before_row_matching(self):
        orig = _make_doc([
            _make_table_block("o1", 1, _product_table([
                _product_row("1", "预测服务器"),
                _product_row("2", "气象服务器"),
            ])),
            _make_table_block("o2", 2, _product_table([
                _product_row("3", "agent软件"),
            ])),
        ])
        comp = _make_doc([
            _make_table_block("c1", 1, _product_table([
                _product_row("1", "预测服务器"),
            ])),
            _make_table_block("c2", 2, _product_table([
                _product_row("2", "气象服务器"),
                _product_row("3", "agent软件"),
            ])),
        ])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        assert diffs == []

    def test_unreliable_tall_numeric_cell_bbox_is_not_used_as_evidence(self):
        orig_html = _product_table([_product_row("1", "功率预测软件", "21000")])
        comp_html = _product_table([_product_row("1", "功率预测软件", "")])
        cell_bboxes = [[0, 0, 20, 20] for _ in range(18)]
        cell_bboxes[7] = [100, 0, 130, 320]
        orig_block = TextBlock(
            block_id="o1", page_no=1, text=orig_html, bbox=BBox(x0=0, y0=0, x1=200, y1=330),
            block_type="table", table_cell_bboxes=cell_bboxes,
        )
        comp_block = TextBlock(
            block_id="c1", page_no=1, text=comp_html, bbox=BBox(x0=0, y0=0, x1=200, y1=330),
            block_type="table", table_cell_bboxes=cell_bboxes,
        )
        diffs, warnings = TableComparator().build_diffs(_make_doc([orig_block]), _make_doc([comp_block]))
        assert len(diffs) == 1
        assert all((e.bbox.y1 - e.bbox.y0) < 100 for e in diffs[0].original_evidence)

    def test_sparse_cross_page_ocr_fragment_row_is_filtered(self):
        fragment_row = "<tr><td></td><td></td><td>机。</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>"
        normal_row = (
            "<tr><td>5</td><td>反病毒软件</td><td>Y20H</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>100</td><td>100</td><td></td></tr>"
        )
        orig_html = _product_table([fragment_row, normal_row])
        comp_html = _product_table([normal_row])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 4, orig_html)]),
            _make_doc([_make_table_block("c1", 4, comp_html)]),
        )

        assert warnings == []
        assert diffs == []

    def test_short_quantity_change_in_full_row_is_not_filtered(self):
        orig_html = _product_table([
            "<tr><td>5</td><td>反病毒软件</td><td>Y20H</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>100</td><td>100</td><td></td></tr>"
        ])
        comp_html = _product_table([
            "<tr><td>5</td><td>反病毒软件</td><td>Y20H</td><td>国能日新</td>"
            "<td>套</td><td>3</td><td>100</td><td>300</td><td></td></tr>"
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, orig_html)]),
            _make_doc([_make_table_block("c1", 1, comp_html)]),
        )

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "'1' -> '3'" in combined

    def test_short_yes_no_change_in_full_row_is_not_filtered(self):
        orig_html = '<table><tr><td>验收项</td><td>是否通过</td></tr><tr><td>联调</td><td>是</td></tr></table>'
        comp_html = '<table><tr><td>验收项</td><td>是否通过</td></tr><tr><td>联调</td><td>否</td></tr></table>'

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, orig_html)]),
            _make_doc([_make_table_block("c1", 1, comp_html)]),
        )

        assert warnings == []
        assert len(diffs) == 1
        assert diffs[0].original_text == "是"
        assert diffs[0].compare_text == "否"

    def test_numeric_sparse_row_is_not_treated_as_ocr_fragment(self):
        orig_html = _product_table([
            "<tr><td>5</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>",
            _product_row("6", "预测服务器"),
        ])
        comp_html = _product_table([_product_row("6", "预测服务器")])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, orig_html)]),
            _make_doc([_make_table_block("c1", 1, comp_html)]),
        )

        assert warnings == []
        assert any("5" in diff.original_text for diff in diffs)

    def test_d006_style_shifted_row_uses_plain_ocr_text_to_avoid_false_diff(self):
        original_html = (
            "<table>"
            "<tr><td>A</td><td>B</td><td>C</td><td>D</td><td>E</td><td>F</td><td>G</td><td>H</td><td>I</td></tr>"
            "<tr><td>9</td><td>机柜</td><td>国能配套</td><td>台</td><td>1</td><td>4000</td><td>4000</td><td></td>"
            "<td>宽*深*高 (800*600*2260)及机 柜电源线</td></tr>"
            "</table>"
        )
        compare_html = (
            "<table>"
            "<tr><td>A</td><td>B</td><td>C</td><td>D</td><td>E</td><td>F</td><td>G</td><td>H</td><td>I</td></tr>"
            "<tr><td>9</td><td>机柜</td><td>通用型机柜 宽*深*高 （800*600*2260）及 机柜电源线</td>"
            "<td>国能配套</td><td>台</td><td>1</td><td>4000</td><td>4000</td><td></td></tr>"
            "</table>"
        )
        original_block = TextBlock(
            block_id="o1",
            page_no=1,
            text="9 机柜 通用型机柜 宽*深*高 (800*600*2260)及机柜电源线 国能配套 台 1 4000 4000",
            raw_html=original_html,
            bbox=BBox(x0=50, y0=100, x1=540, y1=500),
            block_type="table",
        )
        compare_block = TextBlock(
            block_id="c1",
            page_no=1,
            text="9 机柜 通用型机柜 宽*深*高 (800*600*2260)及机柜电源线 国能配套 台 1 4000 4000",
            raw_html=compare_html,
            bbox=BBox(x0=50, y0=100, x1=540, y1=500),
            block_type="table",
        )

        diffs, warnings = TableComparator().build_diffs(_make_doc([original_block]), _make_doc([compare_block]))

        assert warnings == []
        assert diffs == []

    def test_header_agnostic_shifted_cells_do_not_produce_diffs(self):
        original_html = (
            "<table><tr><td>A</td><td>B</td><td>C</td><td>D</td><td>E</td></tr>"
            "<tr><td>R1</td><td>Alpha</td><td>Beta</td><td>Gamma</td><td>Delta</td></tr></table>"
        )
        compare_html = (
            "<table><tr><td>A</td><td>B</td><td>C</td><td>D</td><td>E</td></tr>"
            "<tr><td>R1</td><td>Alpha</td><td>Gamma</td><td>Delta</td><td>Beta</td></tr></table>"
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        assert diffs == []

    def test_shifted_row_still_reports_real_supplier_change(self):
        original_html = (
            "<table><tr><td>A</td><td>B</td><td>C</td><td>D</td><td>E</td><td>F</td></tr>"
            "<tr><td>R1</td><td>机柜</td><td>国能配套</td><td>台</td><td>1</td><td>宽*深*高</td></tr></table>"
        )
        compare_html = (
            "<table><tr><td>A</td><td>B</td><td>C</td><td>D</td><td>E</td><td>F</td></tr>"
            "<tr><td>R1</td><td>机柜</td><td>宽*深*高</td><td>国产优质</td><td>台</td><td>1</td></tr></table>"
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        combined = " ".join(diff.original_text + " " + diff.compare_text for diff in diffs)
        assert "国能配套" in combined
        assert "国产优质" in combined

    def test_shifted_row_still_reports_real_quantity_and_amount_change(self):
        original_html = (
            "<table><tr><td>A</td><td>B</td><td>C</td><td>D</td><td>E</td><td>F</td><td>G</td></tr>"
            "<tr><td>R1</td><td>机柜</td><td>国能配套</td><td>台</td><td>1</td><td>4000</td><td>宽*深*高</td></tr></table>"
        )
        compare_html = (
            "<table><tr><td>A</td><td>B</td><td>C</td><td>D</td><td>E</td><td>F</td><td>G</td></tr>"
            "<tr><td>R1</td><td>机柜</td><td>宽*深*高</td><td>国能配套</td><td>台</td><td>2</td><td>8000</td></tr></table>"
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "'1' -> '2'" in combined
        assert "'4000' -> '8000'" in combined

    def test_shifted_row_still_reports_real_long_text_change(self):
        original_html = (
            "<table><tr><td>A</td><td>B</td><td>C</td><td>D</td><td>E</td></tr>"
            "<tr><td>R1</td><td>机柜</td><td>国能配套</td><td>台</td><td>宽*深*高 (800*600*2260)</td></tr></table>"
        )
        compare_html = (
            "<table><tr><td>A</td><td>B</td><td>C</td><td>D</td><td>E</td></tr>"
            "<tr><td>R1</td><td>机柜</td><td>宽*深*高 (800*800*2260)</td><td>国能配套</td><td>台</td></tr></table>"
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        combined = " ".join(diff.original_text + " " + diff.compare_text for diff in diffs)
        assert "800*600*2260" in combined
        assert "800*800*2260" in combined

    def test_shifted_row_reports_prefix_when_original_ocr_text_lacks_evidence(self):
        original_html = (
            "<table><tr><td>A</td><td>B</td><td>C</td><td>D</td><td>E</td></tr>"
            "<tr><td>R1</td><td>机柜</td><td>国能配套</td><td>台</td><td>宽*深*高 (800*600*2260)</td></tr></table>"
        )
        compare_html = (
            "<table><tr><td>A</td><td>B</td><td>C</td><td>D</td><td>E</td></tr>"
            "<tr><td>R1</td><td>机柜</td><td>通用型机柜 宽*深*高 (800*600*2260)</td><td>国能配套</td><td>台</td></tr></table>"
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        combined = " ".join(diff.compare_text for diff in diffs)
        assert "通用型机柜" in combined

    def test_merged_consecutive_sequence_rows_are_split_before_comparison(self):
        original_html = _product_table([
            "<tr><td>2</td><td>中期模型</td><td>风电场中期功率预报 模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>3</td><td>短期模型</td><td>风电场短期功率预报 模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>2 3</td><td>中期模型 短期模型</td><td>风电场中期功率预报 模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>风电场短期功率预报 模型开发。</td><td>国能日新</td><td>套</td><td>1</td>"
            "<td></td><td></td><td></td><td></td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        assert diffs == []

    def test_merged_sequence_row_split_is_generic_not_model_name_specific(self):
        original_html = _product_table([
            "<tr><td>5</td><td>通信接口</td><td>接口开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>6</td><td>理论功率</td><td>理论功率计算。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>5 6</td><td>通信接口 理论功率</td><td>接口开发。 理论功率计算。</td><td>国能日新 国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        assert diffs == []

    def test_merged_sequence_row_still_reports_real_quantity_change(self):
        original_html = _product_table([
            "<tr><td>2</td><td>中期模型</td><td>风电场中期功率预报 模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>3</td><td>短期模型</td><td>风电场短期功率预报 模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>2 3</td><td>中期模型 短期模型</td><td>风电场中期功率预报 模型开发。 风电场短期功率预报 模型开发。</td>"
            "<td>国能日新 国能日新</td><td>套</td><td>2</td><td></td><td></td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "'1' -> '2'" in combined

    def test_merged_summary_row_is_split_before_comparison(self):
        original_html = _product_table([
            _product_row("1", "国产操作系统", "12000"),
            "<tr><td colspan='7'>小计</td><td>12000</td><td></td></tr>",
            "<tr><td colspan='7'>1套总计 2套合计 6套总合计</td><td>99000 198000 594000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            _product_row("1", "国产操作系统", "12000"),
            "<tr><td>小计</td><td>12000</td><td></td></tr>",
            "<tr><td>1套总计</td><td>99000</td><td></td></tr>",
            "<tr><td>2套合计</td><td>198000</td><td></td></tr>",
            "<tr><td>6套总合计</td><td>594000</td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        assert diffs == []

    def test_split_summary_rows_match_merged_compare_row(self):
        original_html = _product_table([
            _product_row("1", "国产操作系统", "12000"),
            "<tr><td>1套总计</td><td>99000</td><td></td></tr>",
            "<tr><td>2套合计</td><td>198000</td><td></td></tr>",
            "<tr><td>6套总合计</td><td>594000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            _product_row("1", "国产操作系统", "12000"),
            "<tr><td colspan='7'>1套总计 2套合计 6套总合计</td><td>99000 198000 594000</td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        assert diffs == []

    def test_cross_page_summary_table_is_stitched_to_product_table(self):
        original_html = _product_table([
            _product_row("1", "国产操作系统", "12000"),
            "<tr><td colspan='7'>小计</td><td>12000</td><td></td></tr>",
            "<tr><td colspan='7'>1套总计 2套合计 6套总合计</td><td>99000 198000</td><td></td></tr>",
        ])
        original_block = TextBlock(
            block_id="o1",
            page_no=1,
            text="小计\n12000\n1套总计\n99000\n2套合计\n198000\n6套总合计\n594000",
            raw_html=original_html,
            bbox=BBox(x0=50, y0=100, x1=540, y1=500),
            block_type="table",
        )
        compare_product = _product_table([_product_row("1", "国产操作系统", "12000")])
        compare_summary = (
            "<table><tr><td>小计</td><td>12000</td><td></td></tr>"
            "<tr><td>1套总计</td><td>99000</td><td></td></tr>"
            "<tr><td>2套合计</td><td>198000</td><td></td></tr>"
            "<tr><td>6套总合计</td><td>594000</td><td></td></tr></table>"
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([original_block]),
            _make_doc([
                _make_table_block("c1", 1, compare_product),
                _make_table_block("c2", 2, compare_summary),
            ]),
        )

        assert warnings == []
        assert diffs == []

    def test_label_only_summary_row_uses_plain_ocr_amount_before_comparison(self):
        original_html = _product_table([
            _product_row("9", "国能日新探针系统V3.0", "1500"),
            "<tr><td colspan='7'>小计</td><td>22500</td><td></td></tr>",
        ])
        compare_html = _product_table([
            _product_row("9", "国能日新探针系统V3.0", "1500"),
            "<tr><td colspan='8'>小计</td><td></td></tr>",
            "<tr><td colspan='9'>风电功率预测系统V1.0-配套软件</td></tr>",
        ])
        compare_block = _make_raw_table_block(
            "c1",
            2,
            compare_html,
            "国能日新探针系统V3.0\nagent 软件\n1500\n小计\n22500\n风电功率预测系统V1.0-配套软件",
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([compare_block]),
        )

        assert warnings == []
        assert diffs == []

    def test_product_detail_containing_summary_label_is_not_split_into_summary_row(self):
        original_html = _product_table([
            "<tr><td>10</td><td>附件</td><td>耗材，线缆等 小计</td><td>国产优质</td>"
            "<td>套</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>10</td><td>附件</td><td>耗材，线缆等</td><td>国产优质</td>"
            "<td>套</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        combined = " ".join(diff.original_text + " " + diff.compare_text for diff in diffs)
        assert "小计 | 10" not in combined
        assert "附件" not in combined

    def test_product_row_with_embedded_summary_transition_is_repaired_from_plain_ocr(self):
        original_html = _product_table([
            "<tr><td>10</td><td>附件</td><td>耗材，线缆等</td><td>国产优质</td>"
            "<td>套</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
            "<tr><td colspan='7'>小计</td><td>64500</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>10</td><td>附件</td><td>耗材，线缆等 小计</td><td>国产优质</td>"
            "<td>套</td><td>1</td><td rowspan='9'>500 500 64500</td>"
            "<td rowspan='9'>风电功率预测系统V1.0</td><td></td></tr>",
        ])
        source_text = "附件\n耗材，线缆等\n国产优质\n套\n1\n500\n500\n小计\n64500\n风电功率预测系统V1.0"

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_raw_table_block("o1", 1, original_html, source_text)]),
            _make_doc([_make_raw_table_block("c1", 1, compare_html, source_text)]),
        )

        assert warnings == []
        assert diffs == []

    def test_summary_amount_and_next_section_fragment_is_repaired_from_plain_ocr(self):
        original_html = _product_table([
            "<tr><td>10</td><td>附件</td><td>耗材，线缆等 小计</td><td>国产优质</td>"
            "<td>套</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
            "<tr><td colspan='7'>64500</td><td>风电功率预测系统V1.0</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>10</td><td>附件</td><td>耗材，线缆等</td><td>国产优质</td>"
            "<td>套</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
            "<tr><td colspan='7'>小计</td><td>64500</td><td></td></tr>",
        ])
        source_text = "附件\n耗材，线缆等\n国产优质\n套\n1\n500\n500\n小计\n64500\n风电功率预测系统V1.0"

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_raw_table_block("o1", 1, original_html, source_text)]),
            _make_doc([_make_raw_table_block("c1", 1, compare_html, source_text)]),
        )

        assert warnings == []
        assert diffs == []

    def test_embedded_summary_transition_without_plain_ocr_evidence_still_reports(self):
        original_html = _product_table([
            "<tr><td>10</td><td>附件</td><td>耗材，线缆等</td><td>国产优质</td>"
            "<td>套</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
            "<tr><td colspan='7'>小计</td><td>64500</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>10</td><td>附件</td><td>耗材，线缆等 小计</td><td>国产优质</td>"
            "<td>套</td><td>1</td><td>500 500 64500</td><td>风电功率预测系统V1.0</td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "500 500 64500" in combined

    def test_embedded_summary_transition_amount_conflict_still_reports(self):
        original_html = _product_table([
            "<tr><td>10</td><td>附件</td><td>耗材，线缆等</td><td>国产优质</td>"
            "<td>套</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
            "<tr><td colspan='7'>小计</td><td>64500</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>10</td><td>附件</td><td>耗材，线缆等 小计</td><td>国产优质</td>"
            "<td>套</td><td>1</td><td>500 500 64500</td><td>风电功率预测系统V1.0</td><td></td></tr>",
        ])
        compare_source = "附件\n耗材，线缆等\n国产优质\n套\n1\n500\n500\n小计\n64000\n风电功率预测系统V1.0"

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_raw_table_block("c1", 1, compare_html, compare_source)]),
        )

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "500 500 64500" in combined

    def test_missing_summary_before_section_restart_is_repaired_from_plain_ocr(self):
        original_html = _product_table([
            "<tr><td>10</td><td>附件</td><td>耗材，线缆等</td><td>国产优质</td>"
            "<td>套</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
            "<tr><td>1</td><td>光伏功率预 测系统V2.0</td><td>包括：系统软件。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>21000</td><td>21000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>10</td><td>附件</td><td>耗材，线缆等</td><td>国产优质</td>"
            "<td>套</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
            "<tr><td colspan='7'>小计</td><td>64500</td><td></td></tr>",
            "<tr><td colspan='9'>光伏功率预测系统V2.0</td></tr>",
            "<tr><td>1</td><td>光伏功率预 测系统V2.0</td><td>包括：系统软件。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>21000</td><td>21000</td><td></td></tr>",
        ])
        source_text = "附件\n耗材，线缆等\n国产优质\n套\n1\n500\n500\n小计\n64500\n光伏功率预测系统V2.0"

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_raw_table_block("o1", 1, original_html, source_text)]),
            _make_doc([_make_raw_table_block("c1", 1, compare_html, source_text)]),
        )

        assert warnings == []
        assert diffs == []

    def test_product_continuation_row_is_merged_with_plain_ocr_evidence(self):
        original_html = _product_table([
            "<tr><td>2</td><td>接口模块</td><td>型号X-100 支持AB</td><td>厂商甲</td>"
            "<td>台</td><td>1</td><td>200</td><td>200</td><td></td></tr>",
            "<tr><td></td><td></td><td>CD接口</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>2</td><td>接口模块</td><td>型号X-100 支持AB CD接口</td><td>厂商甲</td>"
            "<td>台</td><td>1</td><td>200</td><td>200</td><td></td></tr>",
        ])
        source_text = "2\n接口模块\n型号X-100 支持AB\nCD接口\n厂商甲\n台\n1\n200\n200"

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_raw_table_block("o1", 1, original_html, source_text)]),
            _make_doc([_make_raw_table_block("c1", 1, compare_html, source_text)]),
        )

        assert warnings == []
        assert diffs == []

    def test_product_continuation_row_without_plain_ocr_join_still_reports(self):
        original_html = _product_table([
            "<tr><td>2</td><td>接口模块</td><td>型号X-100 支持AB</td><td>厂商甲</td>"
            "<td>台</td><td>1</td><td>200</td><td>200</td><td></td></tr>",
            "<tr><td></td><td></td><td>CD接口</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>2</td><td>接口模块</td><td>型号X-100 支持AB CD接口</td><td>厂商甲</td>"
            "<td>台</td><td>1</td><td>200</td><td>200</td><td></td></tr>",
        ])
        source_text = "2\n接口模块\n型号X-100 支持AB\n厂商甲\n台\n1\n200\n200"

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_raw_table_block("o1", 1, original_html, source_text)]),
            _make_doc([_make_raw_table_block("c1", 1, compare_html, source_text)]),
        )

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "CD接口" in combined

    def test_shifted_product_detail_column_is_repaired_from_plain_ocr_evidence(self):
        original_html = _product_table([
            "<tr><td>3</td><td>控制柜</td><td>厂商甲</td><td>台</td>"
            "<td>1</td><td>200</td><td>200</td><td></td><td>防护等级IP54 适用户外</td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>3</td><td>控制柜</td><td>一体化控制柜 防护等级IP54 适用户外</td><td>厂商甲</td>"
            "<td>台</td><td>1</td><td>200</td><td>200</td><td></td></tr>",
        ])
        source_text = "一体化控制柜\n防护等级IP54\n控制柜\n台\n厂商甲\n1\n200\n200\n3\n适用户外"

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_raw_table_block("o1", 1, original_html, source_text)]),
            _make_doc([_make_raw_table_block("c1", 1, compare_html, source_text)]),
        )

        assert warnings == []
        assert diffs == []

    def test_shifted_product_detail_with_real_amount_change_still_reports(self):
        original_html = _product_table([
            "<tr><td>3</td><td>控制柜</td><td>厂商甲</td><td>台</td>"
            "<td>1</td><td>200</td><td>200</td><td></td><td>防护等级IP54 适用户外</td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>3</td><td>控制柜</td><td>一体化控制柜 防护等级IP54 适用户外</td><td>厂商甲</td>"
            "<td>台</td><td>1</td><td>200</td><td>250</td><td></td></tr>",
        ])
        source_text = "一体化控制柜\n防护等级IP54\n控制柜\n台\n厂商甲\n1\n200\n200\n3\n适用户外"

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_raw_table_block("o1", 1, original_html, source_text)]),
            _make_doc([_make_raw_table_block("c1", 1, compare_html, source_text)]),
        )

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "200' -> '250" in combined

    def test_repeated_summary_labels_use_source_amounts_in_order(self):
        original_html = _product_table([
            _product_row("1", "硬件", "64500"),
            "<tr><td colspan='7'>小计</td><td>64500</td><td></td></tr>",
            _product_row("9", "国能日新探针系统V3.0", "1500"),
            "<tr><td colspan='7'>小计</td><td>22500</td><td></td></tr>",
        ])
        compare_html = _product_table([
            _product_row("1", "硬件", "64500"),
            "<tr><td colspan='7'>小计</td><td></td><td></td></tr>",
            _product_row("9", "国能日新探针系统V3.0", "1500"),
            "<tr><td colspan='7'>小计</td><td></td><td></td></tr>",
        ])
        compare_block = _make_raw_table_block(
            "c1",
            1,
            compare_html,
            "硬件\n小计\n64500\n国能日新探针系统V3.0\n小计\n22500",
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([compare_block]),
        )

        assert warnings == []
        assert diffs == []

    def test_one_sided_summary_row_is_suppressed_when_plain_ocr_contains_same_pair(self):
        original_html = _product_table([
            "<tr><td colspan='9'>风电功率预测系统V1.0</td></tr>",
            _product_row("9", "国能日新探针系统V3.0", "1500"),
            "<tr><td colspan='7'>小计</td><td>22500</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td colspan='9'>风电功率预测系统V1.0</td></tr>",
            _product_row("9", "国能日新探针系统V3.0", "1500"),
            "<tr><td colspan='9'>风电功率预测系统V1.0-配套软件</td></tr>",
        ])
        compare_block = _make_raw_table_block(
            "c1",
            2,
            compare_html,
            "国能日新探针系统V3.0\nagent 软件\n1500\n小计\n22500\n风电功率预测系统V1.0-配套软件",
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([compare_block]),
        )

        assert warnings == []
        assert diffs == []

    def test_one_sided_summary_row_without_section_context_is_not_suppressed(self):
        original_html = _product_table([
            _product_row("9", "国能日新探针系统V3.0", "1500"),
            "<tr><td colspan='7'>小计</td><td>22500</td><td></td></tr>",
        ])
        compare_html = _product_table([
            _product_row("9", "国能日新探针系统V3.0", "1500"),
            "<tr><td colspan='9'>风电功率预测系统V1.0-配套软件</td></tr>",
        ])
        compare_block = _make_raw_table_block(
            "c1",
            2,
            compare_html,
            "国能日新探针系统V3.0\nagent 软件\n1500\n小计\n22500\n风电功率预测系统V1.0-配套软件",
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([compare_block]),
        )

        assert warnings == []
        combined = " ".join(diff.original_text for diff in diffs)
        assert "小计" in combined
        assert "22500" in combined

    def test_one_sided_summary_row_in_different_section_is_not_suppressed(self):
        original_html = _product_table([
            "<tr><td colspan='9'>风电功率预测系统V1.0</td></tr>",
            _product_row("9", "国能日新探针系统V3.0", "1500"),
            "<tr><td colspan='7'>小计</td><td>22500</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td colspan='9'>光伏功率预测系统V2.0</td></tr>",
            _product_row("9", "国能日新探针系统V3.0", "1500"),
            "<tr><td colspan='9'>光伏功率预测系统V2.0-配套软件</td></tr>",
        ])
        compare_block = _make_raw_table_block(
            "c1",
            2,
            compare_html,
            "光伏功率预测系统V2.0\n国能日新探针系统V3.0\nagent 软件\n1500\n小计\n22500",
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([compare_block]),
        )

        assert warnings == []
        combined = " ".join(diff.original_text for diff in diffs)
        assert "小计" in combined
        assert "22500" in combined

    def test_summary_row_without_plain_ocr_amount_still_reports_delete(self):
        original_html = _product_table([
            _product_row("9", "国能日新探针系统V3.0", "1500"),
            "<tr><td colspan='7'>小计</td><td>22500</td><td></td></tr>",
        ])
        compare_html = _product_table([
            _product_row("9", "国能日新探针系统V3.0", "1500"),
            "<tr><td colspan='8'>小计</td><td></td></tr>",
            "<tr><td colspan='9'>风电功率预测系统V1.0-配套软件</td></tr>",
        ])
        compare_block = _make_raw_table_block(
            "c1",
            2,
            compare_html,
            "国能日新探针系统V3.0\nagent 软件\n1500\n风电功率预测系统V1.0-配套软件",
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([compare_block]),
        )

        assert warnings == []
        combined = " ".join(diff.original_text for diff in diffs)
        assert "小计" in combined
        assert "22500" in combined

    def test_summary_amount_change_is_still_reported_after_plain_ocr_repair(self):
        original_html = _product_table([
            _product_row("9", "国能日新探针系统V3.0", "1500"),
            "<tr><td colspan='7'>小计</td><td>22500</td><td></td></tr>",
        ])
        compare_html = _product_table([
            _product_row("9", "国能日新探针系统V3.0", "1500"),
            "<tr><td colspan='8'>小计</td><td></td></tr>",
        ])
        compare_block = _make_raw_table_block(
            "c1",
            2,
            compare_html,
            "国能日新探针系统V3.0\nagent 软件\n1500\n小计\n22000",
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([compare_block]),
        )

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "'22500' -> '22000'" in combined

    def test_summary_amount_change_is_still_reported(self):
        original_html = _product_table([
            _product_row("1", "国产操作系统", "12000"),
            "<tr><td>6套总合计</td><td>594000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            _product_row("1", "国产操作系统", "12000"),
            "<tr><td>6套总合计</td><td>590000</td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "'594000' -> '590000'" in combined

    def test_label_only_summary_row_is_filtered(self):
        original_html = _product_table([
            _product_row("1", "国产操作系统", "12000"),
            "<tr><td colspan='7'>小计</td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([_product_row("1", "国产操作系统", "12000")])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        assert diffs == []

    def test_sparse_html_row_covered_by_plain_ocr_text_is_not_reported(self):
        original_html = _product_table([
            "<tr><td>1</td><td>国产操作系 统</td><td>国产操作系统</td><td>凝思</td>"
            "<td>套</td><td>3</td><td>4000</td><td>12000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td colspan='8'>1</td><td>国产操作系 统</td></tr>",
        ])
        compare_block = TextBlock(
            block_id="c1",
            page_no=1,
            text="国产操作系\n凝思\n套\n4000\n1\n国产操作系统\n3\n12000\n统",
            raw_html=compare_html,
            bbox=BBox(x0=50, y0=100, x1=540, y1=500),
            block_type="table",
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([compare_block]),
        )

        assert warnings == []
        assert diffs == []

    def test_sparse_html_row_without_plain_text_coverage_still_reports(self):
        original_html = _product_table([
            "<tr><td>1</td><td>国产操作系 统</td><td>国产操作系统</td><td>凝思</td>"
            "<td>套</td><td>3</td><td>4000</td><td>12000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td colspan='8'>1</td><td>国产操作系 统</td></tr>",
        ])
        compare_block = TextBlock(
            block_id="c1",
            page_no=1,
            text="国产操作系\n统",
            raw_html=compare_html,
            bbox=BBox(x0=50, y0=100, x1=540, y1=500),
            block_type="table",
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([compare_block]),
        )

        assert warnings == []
        assert diffs

    def test_one_sided_product_row_covered_by_plain_ocr_text_is_not_reported(self):
        original_html = _product_table([
            "<tr><td>5</td><td>显示器</td><td>X20H 19寸放机柜带背板安装螺丝</td><td>方大极视</td>"
            "<td>台</td><td>1</td><td>500</td><td>500</td><td>6</td></tr>",
            "<tr><td>7</td><td>防火墙</td><td>SG-8000 S8330 8口/接网监/双电源</td><td>安博通</td>"
            "<td>台</td><td>2</td><td>3000</td><td>6000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>5</td><td>显示器</td><td>X20H 19寸放机柜带背板安装螺丝</td><td>方大极视</td>"
            "<td>台</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
            "<tr><td>6</td><td>反向隔离装 置</td><td>StoneWal1-2000BF 百兆</td><td>科东</td>"
            "<td>台</td><td>1</td><td>30500</td><td>30500</td><td></td></tr>",
            "<tr><td>7</td><td>防火墙</td><td>SG-8000 S8330 8口/接网监/双电源</td><td>安博通</td>"
            "<td>台</td><td>2</td><td>3000</td><td>6000</td><td></td></tr>",
        ])
        original_text = (
            "显示器\n方大极视\n500\n500\n5\n"
            "反向隔离装\nStoneWall-2000BF\n科东\n台\n30500\n30500\n6\n1\n置\n百兆\n"
            "防火墙\n安博通\n3000\n6000\n7\n"
        )
        original_block = _make_raw_table_block("o1", 1, original_html, original_text)
        compare_block = _make_raw_table_block("c1", 1, compare_html, compare_html)

        diffs, warnings = TableComparator().build_diffs(_make_doc([original_block]), _make_doc([compare_block]))

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "反向隔离" not in combined
        assert "30500" not in combined

    def test_one_sided_product_row_without_name_coverage_still_reports(self):
        original_html = _product_table([
            "<tr><td>5</td><td>显示器</td><td>X20H 19寸放机柜带背板安装螺丝</td><td>方大极视</td>"
            "<td>台</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
            "<tr><td>7</td><td>防火墙</td><td>SG-8000 S8330 8口/接网监/双电源</td><td>安博通</td>"
            "<td>台</td><td>2</td><td>3000</td><td>6000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>5</td><td>显示器</td><td>X20H 19寸放机柜带背板安装螺丝</td><td>方大极视</td>"
            "<td>台</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
            "<tr><td>6</td><td>反向隔离装置</td><td>StoneWall-2000BF 百兆</td><td>科东</td>"
            "<td>台</td><td>1</td><td>30500</td><td>30500</td><td></td></tr>",
            "<tr><td>7</td><td>防火墙</td><td>SG-8000 S8330 8口/接网监/双电源</td><td>安博通</td>"
            "<td>台</td><td>2</td><td>3000</td><td>6000</td><td></td></tr>",
        ])
        original_block = _make_raw_table_block("o1", 1, original_html, "科东 台 30500 30500")
        compare_block = _make_raw_table_block("c1", 1, compare_html, compare_html)

        diffs, warnings = TableComparator().build_diffs(_make_doc([original_block]), _make_doc([compare_block]))

        assert warnings == []
        assert any("反向隔离" in diff.compare_text for diff in diffs)

    def test_one_sided_product_row_without_amount_coverage_still_reports(self):
        original_html = _product_table([
            "<tr><td>5</td><td>显示器</td><td>X20H 19寸放机柜带背板安装螺丝</td><td>方大极视</td>"
            "<td>台</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
            "<tr><td>7</td><td>防火墙</td><td>SG-8000 S8330 8口/接网监/双电源</td><td>安博通</td>"
            "<td>台</td><td>2</td><td>3000</td><td>6000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>5</td><td>显示器</td><td>X20H 19寸放机柜带背板安装螺丝</td><td>方大极视</td>"
            "<td>台</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
            "<tr><td>6</td><td>反向隔离装置</td><td>StoneWall-2000BF 百兆</td><td>科东</td>"
            "<td>台</td><td>1</td><td>31500</td><td>31500</td><td></td></tr>",
            "<tr><td>7</td><td>防火墙</td><td>SG-8000 S8330 8口/接网监/双电源</td><td>安博通</td>"
            "<td>台</td><td>2</td><td>3000</td><td>6000</td><td></td></tr>",
        ])
        original_text = "反向隔离装置 StoneWall-2000BF 科东 台 30500 30500"
        original_block = _make_raw_table_block("o1", 1, original_html, original_text)
        compare_block = _make_raw_table_block("c1", 1, compare_html, compare_html)

        diffs, warnings = TableComparator().build_diffs(_make_doc([original_block]), _make_doc([compare_block]))

        assert warnings == []
        assert any("31500" in diff.compare_text for diff in diffs)

    def test_adjacent_sequence_row_merged_by_structure_is_repaired_from_plain_ocr(self):
        original_html = _product_table([
            "<tr><td>6</td><td>理论可用功 率计算</td><td>理论可用功率计算</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>7</td><td>接口开放及 系统开发</td><td>接口开放及系统开发</td><td>国能日新</td>"
            "<td>年</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>8</td><td>技术维护服 务费</td><td>数值天气预报、系统维护、备份等服务。</td><td>国能日新</td>"
            "<td>年</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>6</td><td>理论可用功 率计算 接口开放及 系统开发</td><td>理论可用功率计算</td>"
            "<td>国能日新 国能日新</td><td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>8</td><td>技术维护服 务费</td><td>数值天气预报、系统维护、备份等服务。</td><td>国能日新</td>"
            "<td>年</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_text = (
            "理论可用功\n6\n理论可用功率计算\n国能日新\n率计算\n套\n1\n"
            "接口开放及\n7\n接口开放及系统开发\n国能日新\n年\n系统开发\n1\n"
            "技术维护服\n8\n数值天气预报、系统维护、备份等服务。\n国能日新\n年\n1\n务费"
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_raw_table_block("c1", 1, compare_html, compare_text)]),
        )

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "理论可用功" not in combined
        assert "接口开放" not in combined

    def test_adjacent_sequence_row_merge_without_plain_ocr_sequence_still_reports(self):
        original_html = _product_table([
            "<tr><td>6</td><td>理论可用功 率计算</td><td>理论可用功率计算</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>7</td><td>接口开放及 系统开发</td><td>接口开放及系统开发</td><td>国能日新</td>"
            "<td>年</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>8</td><td>技术维护服 务费</td><td>数值天气预报、系统维护、备份等服务。</td><td>国能日新</td>"
            "<td>年</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>6</td><td>理论可用功 率计算 接口开放及 系统开发</td><td>理论可用功率计算</td>"
            "<td>国能日新 国能日新</td><td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>8</td><td>技术维护服 务费</td><td>数值天气预报、系统维护、备份等服务。</td><td>国能日新</td>"
            "<td>年</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_text = "理论可用功率计算 国能日新 套 1 接口开放及系统开发 国能日新 年 1"

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_raw_table_block("c1", 1, compare_html, compare_text)]),
        )

        assert warnings == []
        combined = " ".join(diff.original_text + diff.compare_text for diff in diffs)
        assert "接口开放" in combined

    def test_adjacent_sequence_row_merge_with_unit_conflict_still_reports(self):
        original_html = _product_table([
            "<tr><td>6</td><td>理论可用功 率计算</td><td>理论可用功率计算</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>7</td><td>接口开放及 系统开发</td><td>接口开放及系统开发</td><td>国能日新</td>"
            "<td>年</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>8</td><td>技术维护服 务费</td><td>数值天气预报、系统维护、备份等服务。</td><td>国能日新</td>"
            "<td>年</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>6</td><td>理论可用功 率计算 接口开放及 系统开发</td><td>理论可用功率计算</td>"
            "<td>国能日新 国能日新</td><td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>8</td><td>技术维护服 务费</td><td>数值天气预报、系统维护、备份等服务。</td><td>国能日新</td>"
            "<td>年</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_text = (
            "理论可用功\n6\n理论可用功率计算\n国能日新\n率计算\n套\n1\n"
            "接口开放及\n7\n接口开放及系统开发\n国能日新\n套\n系统开发\n1\n"
            "技术维护服\n8\n数值天气预报、系统维护、备份等服务。\n国能日新\n年\n1\n务费"
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_raw_table_block("c1", 1, compare_html, compare_text)]),
        )

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "'年' -> '套'" in combined

    def test_duplicate_amount_cell_covered_by_plain_ocr_is_not_reported(self):
        original_html = _product_table([
            "<tr><td>1</td><td>光伏功率预测系统V2.0</td><td>系统开发</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>21000</td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>1</td><td>光伏功率预测系统V2.0</td><td>系统开发</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>21000</td><td>21000</td><td></td></tr>",
        ])
        original_block = _make_raw_table_block("o1", 1, original_html, "光伏功率预测系统V2.0 21000 21000")
        compare_block = _make_raw_table_block("c1", 1, compare_html, "光伏功率预测系统V2.0 21000 21000")

        diffs, warnings = TableComparator().build_diffs(_make_doc([original_block]), _make_doc([compare_block]))

        assert warnings == []
        assert diffs == []

    def test_shifted_duplicate_amount_cell_covered_by_plain_ocr_is_not_reported(self):
        original_html = _product_table([
            "<tr><td>1</td><td>风电功率预测系统V1.0</td><td>系统开发</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>21000</td><td>21000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>1</td><td>风电功率预测系统V1.0</td><td>系统开发</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td>21000</td></tr>",
        ])
        original_block = _make_raw_table_block("o1", 1, original_html, "风电功率预测系统V1.0 21000 21000")
        compare_block = _make_raw_table_block("c1", 1, compare_html, "风电功率预测系统V1.0 21000 21000")

        diffs, warnings = TableComparator().build_diffs(_make_doc([original_block]), _make_doc([compare_block]))

        assert warnings == []
        assert diffs == []

    def test_duplicate_amount_cell_without_plain_ocr_coverage_still_reports(self):
        original_html = _product_table([
            "<tr><td>1</td><td>光伏功率预测系统V2.0</td><td>系统开发</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>21000</td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>1</td><td>光伏功率预测系统V2.0</td><td>系统开发</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>21000</td><td>21000</td><td></td></tr>",
        ])
        original_block = _make_raw_table_block("o1", 1, original_html, "光伏功率预测系统V2.0 21000")
        compare_block = _make_raw_table_block("c1", 1, compare_html, "光伏功率预测系统V2.0 21000 21000")

        diffs, warnings = TableComparator().build_diffs(_make_doc([original_block]), _make_doc([compare_block]))

        assert warnings == []
        assert len(diffs) == 1
        assert diffs[0].compare_text == "21000"

    def test_real_amount_change_is_not_hidden_by_duplicate_amount_filter(self):
        original_html = _product_table([
            "<tr><td>1</td><td>光伏功率预测系统V2.0</td><td>系统开发</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>21000</td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>1</td><td>光伏功率预测系统V2.0</td><td>系统开发</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>21000</td><td>22000</td><td></td></tr>",
        ])
        original_block = _make_raw_table_block("o1", 1, original_html, "光伏功率预测系统V2.0 21000 21000")
        compare_block = _make_raw_table_block("c1", 1, compare_html, "光伏功率预测系统V2.0 21000 22000")

        diffs, warnings = TableComparator().build_diffs(_make_doc([original_block]), _make_doc([compare_block]))

        assert warnings == []
        assert len(diffs) == 1
        assert diffs[0].compare_text == "22000"

    def test_misaligned_amount_cell_bbox_is_not_used_as_evidence(self):
        original_html = _product_table([
            "<tr><td>1</td><td>光伏功率预测系统V2.0</td><td>系统开发</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>21000</td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>1</td><td>光伏功率预测系统V2.0</td><td>系统开发</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>21000</td><td>21000</td><td></td></tr>",
        ])
        header_bboxes = [[10 + i * 50, 100, 45 + i * 50, 120] for i in range(9)]
        row_bboxes = [
            [50, 130, 80, 160],
            [90, 130, 150, 160],
            [160, 130, 220, 160],
            [230, 130, 280, 160],
            [290, 130, 320, 160],
            [330, 130, 360, 160],
            [410, 130, 455, 160],
            [60, 130, 100, 160],
            [500, 130, 530, 160],
        ]
        original_block = _make_raw_table_block("o1", 1, original_html, "光伏功率预测系统V2.0 21000")
        compare_block = _make_raw_table_block(
            "c1",
            1,
            compare_html,
            "光伏功率预测系统V2.0 21000 21000",
            header_bboxes + row_bboxes,
        )

        diffs, warnings = TableComparator().build_diffs(_make_doc([original_block]), _make_doc([compare_block]))

        assert warnings == []
        assert len(diffs) == 1
        assert diffs[0].compare_text == "21000"
        assert diffs[0].compare_evidence == []

    def test_amount_format_normalization_does_not_report_false_change(self):
        orig_html = '<table><tr><td>合同金额</td><td>10,000.00元</td></tr></table>'
        comp_html = '<table><tr><td>合同金额</td><td>人民币10000元</td></tr></table>'
        orig = _make_doc([_make_table_block("o1", 1, orig_html)])
        comp = _make_doc([_make_table_block("c1", 1, comp_html)])

        diffs, warnings = TableComparator().build_diffs(orig, comp)

        assert diffs == []

    def test_date_format_normalization_does_not_report_false_change(self):
        orig_html = '<table><tr><td>签订日期</td><td>2026年4月21日</td></tr></table>'
        comp_html = '<table><tr><td>签订日期</td><td>2026-04-21</td></tr></table>'
        orig = _make_doc([_make_table_block("o1", 1, orig_html)])
        comp = _make_doc([_make_table_block("c1", 1, comp_html)])

        diffs, warnings = TableComparator().build_diffs(orig, comp)

        assert diffs == []
