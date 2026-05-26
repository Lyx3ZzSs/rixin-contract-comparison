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
        has_add = any("C" in d.compare_text for d in diffs if d.diff_type == "MODIFY")
        assert has_add

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
