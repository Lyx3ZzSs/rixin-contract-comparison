import signal

import pytest

from app.models import BBox, CharBox, Document, Page, TextBlock
from app.models_table import StructuredTable, TableCell, TableRow
from app.services.table_compare import TableComparator
from app.services.table_compare.parser import LogicalTableParser
from app.services.table_compare.repair import TableRepairContext, TableRepairService
from app.services.table_compare.types import _LogicalCell, _LogicalRow


def _make_table_block(block_id: str, page_no: int, html: str, bbox: BBox | None = None) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=page_no,
        text=html,
        bbox=bbox or BBox(x0=50, y0=100, x1=540, y1=500),
        block_type="table",
    )


def _make_text_block(block_id: str, page_no: int, text: str, bbox: BBox) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=page_no,
        text=text,
        bbox=bbox,
        block_type="text",
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


def _structured_table(
    rows: list[list[str]],
    *,
    source_text: str = "",
    geometry_status: str = "not_available",
) -> StructuredTable:
    return StructuredTable(
        page_no=13,
        source_block_id="table_1",
        source_text=source_text,
        geometry_status=geometry_status,
        rows=[
            TableRow(
                row_index=row_index,
                cells=[
                    TableCell(row_index=row_index, col_index=col_index, text=text)
                    for col_index, text in enumerate(row)
                ],
            )
            for row_index, row in enumerate(rows)
        ],
        col_count=max((len(row) for row in rows), default=0),
    )


@pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="requires SIGALRM")
def test_block_range_bbox_returns_when_prefix_empty_and_suffix_missing() -> None:
    builder = TableComparator()._diff_builder
    block_text = "目标单元格后面的整块表格文本不包含期望后缀"
    block = _make_text_block(
        "b1",
        1,
        block_text,
        BBox(x0=10, y0=10, x1=300, y1=30),
    ).model_copy(
        update={
            "char_boxes": [
                CharBox(
                    char=char,
                    bbox=BBox(x0=10 + index * 8, y0=10, x1=16 + index * 8, y1=20),
                    page_no=1,
                    text_index=index,
                )
                for index, char in enumerate(block_text)
            ]
        }
    )

    def timeout_handler(signum, frame):  # noqa: ANN001
        raise TimeoutError("range lookup did not terminate")

    previous = signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, 0.2)
    try:
        bbox = builder._block_char_bbox_for_range(
            block,
            BBox(x0=10, y0=10, x1=80, y1=30),
            "务。.5项预报,并根据电场地形地貌条件进行天气预报的降尺度",
            0,
            2,
            None,
        )
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)

    assert bbox is None


def test_severe_conflict_table_does_not_add_value_already_present_in_original_source() -> None:
    source_text = (
        "周玲 国能日新 科技股份 有限公司 女 1981.11 工程师 "
        "计算机 科学与 技术 项目经理 6个月"
    )
    original = _structured_table(
        [["周玲", "国能日新科技股份有限公司", "女", "1981.11", "工程师", "计算机科学与技术", "项目经理", ""]],
        source_text=source_text,
        geometry_status="severe_conflict",
    )
    compare = _structured_table(
        [["周玲", "国能日新科技股份有限公司", "女", "1981.11", "工程师", "计算机科学与技术", "项目经理", "6个月"]],
        source_text=source_text,
        geometry_status="severe_conflict",
    )

    diffs = TableComparator()._diff_builder.diff_cells(original, compare)

    assert not any(diff.compare_text == "6个月" and diff.diff_type == "ADD" for diff in diffs)


def test_low_quality_table_does_not_add_isolated_single_character_fragment() -> None:
    original = _structured_table(
        [["序号", "标的物", "备注"], ["1", "AGCAVC系统", ""]],
        source_text="序号 标的物 备注 1 AGCAVC系统",
        geometry_status="low_confidence",
    )
    compare = _structured_table(
        [["序号", "标的物", "备注"], ["1", "AGCAVC系统", "川"]],
        source_text="序号 标的物 备注 1 AGCAVC系统",
        geometry_status="low_confidence",
    )

    diffs = TableComparator()._diff_builder.diff_cells(original, compare)

    assert not any(diff.compare_text == "川" and diff.diff_type == "ADD" for diff in diffs)


def test_severe_conflict_table_does_not_delete_cross_page_continuation_covered_by_compare_source() -> None:
    compare_source = (
        "李江城 国能日新 科技股份 有限公司 男 1991.9 工程师 "
        "信息科学与技术 技术执行 3个月"
    )
    original = _structured_table(
        [
            ["李江城", "国能日新", "", "", "工程师", "信息科", "技术执行", "3个月"],
            ["", "", "科技股份 有限公司", "", "", "", "学与技 术", ""],
        ],
        source_text=compare_source,
        geometry_status="severe_conflict",
    )
    compare = _structured_table(
        [["李江城", "国能日新科技股份有限公司", "男", "1991.9", "工程师", "信息科学与技术", "技术执行", "3个月"]],
        source_text=compare_source,
        geometry_status="severe_conflict",
    )

    diffs = TableComparator()._diff_builder.diff_cells(original, compare)

    deleted_text = " | ".join(diff.original_text for diff in diffs if diff.diff_type == "DELETE")
    assert "科技股份" not in deleted_text
    assert "学与技" not in deleted_text


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


def _quote_table(rows: list[str]) -> str:
    header = (
        "<tr><td>序号</td><td>名称</td><td>型号规格</td><td>单位</td><td>数量</td>"
        "<td>单价</td><td>总金额</td><td>税率</td><td>备注</td><td>质保期</td><td>交付期</td></tr>"
    )
    return "<table>" + header + "".join(rows) + "</table>"


def _service_table() -> str:
    return (
        "<table>"
        "<tr><td>序号</td><td>项目</td><td>内容</td><td>数量</td><td>单位</td>"
        "<td>生产厂家</td><td>单价</td><td>总价</td><td>备注</td></tr>"
        "<tr><td>1</td><td>数值气象服务</td><td>每日提供高精度数值天气预报。</td>"
        "<td>0.5</td><td>项</td><td>国能日新</td><td>60000</td><td>60000</td>"
        "<td>以后每年服务费5万圆整</td></tr>"
        "<tr><td>2</td><td>功率预测服务</td><td>提供后期数据库更新升级服务。</td>"
        "<td>0.5</td><td>项</td><td></td><td></td><td></td><td></td></tr>"
        "<tr><td>3</td><td>日常维护服务</td><td>每天都有专人负责，提供实时更新服务。</td>"
        "<td>0.5</td><td>项</td><td></td><td></td><td></td><td></td></tr>"
        "<tr><td></td><td></td><td>提供7×24小时日常维护及售后服务。</td>"
        "<td>0.5</td><td>项</td><td></td><td></td><td></td><td></td></tr>"
        "<tr><td>总价</td><td colspan='8'>大写：陆万元整 (¥60000元)</td></tr>"
        "</table>"
    )


def _product_restart_continuation_table() -> str:
    header = (
        "<tr><td>序号</td><td>产品名称</td><td>详细配置</td><td>品牌</td>"
        "<td>单位</td><td>数量</td><td>单价</td><td>金额</td><td>备注</td></tr>"
    )
    subtotal_rows = (
        "<tr><td>1</td><td>国产操作系统</td><td>国产操作系统</td><td>凝思</td>"
        "<td>套</td><td>3</td><td>4000</td><td>12000</td><td></td></tr>"
        "<tr><td>小计</td><td colspan='6'></td><td>12000</td><td></td></tr>"
        "<tr><td>1套总计</td><td colspan='6'></td><td>99000</td><td></td></tr>"
        "<tr><td>4套合计</td><td colspan='6'></td><td>396000</td><td></td></tr>"
    )
    restarted_section = (
        "<tr><td>1</td><td>预测服务器</td><td>14020R双电</td><td>航天联志</td>"
        "<td>台</td><td>1</td><td>8500</td><td>8500</td><td></td></tr>"
        "<tr><td>2</td><td>气象服务器</td><td>14020R双电</td><td>航天联志</td>"
        "<td>台</td><td>1</td><td>8500</td><td>8500</td><td></td></tr>"
    )
    return "<table>" + subtotal_rows + header + restarted_section + "</table>"


def _product_table_with_quote_remarks(days: str = "30") -> str:
    return (
        "<table>"
        "<tr><td>序号</td><td>产品名称</td><td>详细配置</td><td>品牌</td>"
        "<td>单位</td><td>数量</td><td>单价</td><td>金额</td><td>备注</td></tr>"
        "<tr><td>1</td><td>功率预测服务器</td><td>H540-G30</td><td>中科可控</td>"
        "<td>台</td><td>1</td><td>26000</td><td>26000</td><td>国产芯片</td></tr>"
        "<tr><td>合计</td><td colspan='7'>26000</td><td></td></tr>"
        f"<tr><td>备注：①本报价有效期为{days}天；税率13%</td>"
        "<td colspan='8'>②本报价设备质保期为12个月；</td></tr>"
        "<tr><td colspan='9'>③我公司保留对于报价和产品的最终解释权。</td></tr>"
        "</table>"
    )


def _product_table_without_quote_remarks() -> str:
    return (
        "<table>"
        "<tr><td>序号</td><td>产品名称</td><td>详细配置</td><td>品牌</td>"
        "<td>单位</td><td>数量</td><td>单价</td><td>金额</td><td>备注</td></tr>"
        "<tr><td>1</td><td>功率预测服务器</td><td>H540-G30</td><td>中科可控</td>"
        "<td>台</td><td>1</td><td>26000</td><td>26000</td><td>国产芯片</td></tr>"
        "<tr><td>合计</td><td colspan='7'>26000</td><td></td></tr>"
        "</table>"
    )


def _standalone_quote_remarks_table(days: str = "30") -> str:
    return (
        "<table>"
        f"<tr><td>备注：①本报价有效期为{days}天；税率13%</td></tr>"
        "<tr><td>②本报价设备质保期为12个月；</td></tr>"
        "<tr><td>③我公司保留对于报价和产品的最终解释权。</td></tr>"
        "</table>"
    )


def _product_table_with_split_quote_remarks() -> str:
    return (
        "<table>"
        "<tr><td>序号</td><td>产品名称</td><td>详细配置</td><td>品牌</td>"
        "<td>单位</td><td>数量</td><td>单价</td><td>金额</td><td>备注</td></tr>"
        "<tr><td>1</td><td>技术维护服务费</td><td>数值天气预报</td><td>国能日新</td>"
        "<td>套</td><td>1</td><td>29500</td><td>29500</td><td></td></tr>"
        "<tr><td>1套总计 四套合计</td><td>备注</td>"
        "<td colspan='7'>1、本报价有效期为30天； 2、本报价设备质保期为12个月；</td></tr>"
        "<tr><td colspan='9'>3、我公司保留对于报价和产品的最终解释权。</td></tr>"
        "</table>"
    )


def _product_table_with_merged_quote_remarks() -> str:
    return (
        "<table>"
        "<tr><td>序号</td><td>产品名称</td><td>详细配置</td><td>品牌</td>"
        "<td>单位</td><td>数量</td><td>单价</td><td>金额</td><td>备注</td></tr>"
        "<tr><td>1</td><td>技术维护服务费</td><td>数值天气预报</td><td>国能日新</td>"
        "<td>套</td><td>1</td><td>29500</td><td>29500</td><td></td></tr>"
        "<tr><td>1套总计</td><td colspan='7'>29500</td><td></td></tr>"
        "<tr><td>四套合计</td><td colspan='7'>118000</td><td></td></tr>"
        "<tr><td colspan='9'>备注 1、本报价有效期为30天； "
        "2、本报价设备质保期为12个月； 3、我公司保留对于报价和产品的最终解释权。</td></tr>"
        "</table>"
    )


def _hardware_table_with_remark(remark: str = "国产芯片", amount: str = "20000") -> str:
    return (
        "<table>"
        "<tr><td>序号</td><td>名称</td><td>型号</td><td>单位</td><td>数量</td>"
        "<td>产地</td><td>生产厂家</td><td>单价</td><td>总价</td><td>备注</td></tr>"
        f"<tr><td>3</td><td>工作站</td><td>CPU:HG3350</td><td>台</td><td>1</td>"
        f"<td>中国</td><td>中科可控</td><td>{amount}</td><td>{amount}</td><td>{remark}</td></tr>"
        "</table>"
    )


def _hardware_table_with_shifted_amount_missing_remark(amount: str = "20000") -> str:
    return (
        "<table>"
        "<tr><td>序号</td><td>名称</td><td>型号</td><td>单位</td><td>数量</td>"
        "<td>产地</td><td>生产厂家</td><td>单价</td><td>总价</td><td>备注</td></tr>"
        f"<tr><td>3</td><td>工作站</td><td>CPU:HG3350</td><td>台</td><td>1</td>"
        f"<td>中国</td><td>中科可控</td><td>{amount}</td><td></td><td>{amount}</td></tr>"
        "</table>"
    )


def _contract_product_table(summary_colspan: int, summary_text: str) -> str:
    empty_cells = "".join("<td></td>" for _ in range(9 - summary_colspan))
    return (
        "<table>"
        "<tr><td>序号</td><td>名称</td><td>型号规格</td><td>单位</td><td>数量</td>"
        "<td>单价</td><td>总金额</td><td>税率</td><td>备注</td></tr>"
        "<tr><td>1</td><td>光伏功率预测拓展 系统V1.0</td><td>光功率国产化改造</td>"
        "<td>套</td><td>1</td><td>140000</td><td>140000</td><td>13%</td><td></td></tr>"
        "<tr><td>2</td><td>数值天气预报</td><td>光功率预测系统技 术服务合同</td>"
        "<td>年</td><td>0.5</td><td>60000</td><td>60000</td><td>6%</td>"
        "<td>以后每年服务 费5万圆整</td></tr>"
        f'<tr><td colspan="{summary_colspan}">{summary_text}</td>{empty_cells}</tr>'
        "</table>"
    )


def _contract_product_source_text(summary_text: str) -> str:
    return (
        "序号\n名称\n单位\n数量\n单价\n税率\n备注\n总金额\n型号规格\n"
        "光伏功率预测拓展\n套\n光功率国产化改造\n13%\n1\n140000\n140000\n1\n系统V1.0\n"
        "以后每年服务\n光功率预测系统技\n年\n60000\n0.5\n数值天气预报\n60000\n6%\n2\n"
        f"费5万圆整\n术服务合同\n{summary_text}"
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

    def test_detail_merge_path_cleans_name_column(self):
        """Bug A: _split_adjacent_sequence_detail_merged_row should clean col 1 (name)."""
        source_text = (
            "6\n理论可用功率计算\n国能日新\n套\n1\n"
            "7\n接口开放及系统开发\n国能日新\n年\n1\n"
            "8\n技术维护服务费\n国能日新\n年\n1"
        )
        original_html = _product_table([
            "<tr><td>6</td><td>理论可用功率计算</td><td>理论可用功率计算</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>7</td><td>接口开放及系统开发</td><td>接口开放及系统开发</td><td>国能日新</td>"
            "<td>年</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>8</td><td>技术维护服务费</td><td>数值天气预报服务。</td><td>国能日新</td>"
            "<td>年</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>6</td><td>理论可用功 率计算 接口开放及 系统开发</td>"
            "<td>理论可用功率计算 接口开放及系统开发</td><td>国能日新 国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>8</td><td>技术维护服务费</td><td>数值天气预报服务。</td><td>国能日新</td>"
            "<td>年</td><td>1</td><td></td><td></td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_raw_table_block("c1", 1, compare_html, source_text)]),
        )

        assert warnings == []
        assert diffs == []

    def test_phantom_merged_name_row_is_repaired(self):
        """Bug B: merged name + phantom row should be split when source_text is available."""
        source_text = (
            "风电场中期功率预报\n中期模型\n国能日新\n2\n套\n1\n模型开发。\n"
            "风电场短期功率预报\n短期模型\n国能日新\n3\n套\n1\n模型开发。\n"
            "风电场超短期\n超短期模型\n4\n国能日新\n套\n1\n预测模型开发。"
        )
        original_html = _product_table([
            "<tr><td>2</td><td>中期模型</td><td>风电场中期功率预报 模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>3</td><td>短期模型</td><td>风电场短期功率预报 模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>4</td><td>超短期模型</td><td>风电场超短期功率预测模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>2</td><td>中期模型 短期模型</td><td>风电场中期功率预报 模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>3</td><td>风电场短期功率预报 模型开发。</td><td>国能日新</td><td>套</td><td>1</td>"
            "<td></td><td></td><td></td><td></td></tr>",
            "<tr><td>4</td><td>超短期模型</td><td>风电场超短期功率预测模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_raw_table_block("c1", 1, compare_html, source_text)]),
        )

        assert warnings == []
        assert diffs == []

    def test_phantom_merged_name_row_preserves_real_change(self):
        """Bug B: real quantity change should still be detected after phantom repair."""
        source_text = (
            "风电场中期功率预报\n中期模型\n国能日新\n2\n套\n1\n模型开发。\n"
            "风电场短期功率预报\n短期模型\n国能日新\n3\n套\n2\n模型开发。"
        )
        original_html = _product_table([
            "<tr><td>2</td><td>中期模型</td><td>风电场中期功率预报 模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>3</td><td>短期模型</td><td>风电场短期功率预报 模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>2</td><td>中期模型 短期模型</td><td>风电场中期功率预报 模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>3</td><td>风电场短期功率预报 模型开发。</td><td>国能日新</td><td>套</td><td>2</td>"
            "<td></td><td></td><td></td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_raw_table_block("c1", 1, compare_html, source_text)]),
        )

        assert warnings == []
        assert len(diffs) == 1
        assert "2" in diffs[0].compare_text
        assert warnings == []

    def test_version_suffix_in_product_name_is_not_split_into_phantom_row(self):
        def row(row_index: int, values: list[str]) -> _LogicalRow:
            return _LogicalRow(
                row_index=row_index,
                cells=[
                    _LogicalCell(
                        row_index=row_index,
                        col_index=col,
                        text=value,
                        bbox=None,
                        page_no=1,
                        source_block_id="p1_ppocrv5_b10",
                        source_row=row_index + 1,
                        source_col=col,
                    )
                    for col, value in enumerate(values)
                    if value
                ],
                page_no=1,
                source_block_id="p1_ppocrv5_b10",
                source_row=row_index + 1,
            )

        rows = [
            row(0, ["1", "光伏功率预测拓展 系统V1.0", "光功率国产化改造", "套", "1", "140000", "140000", "13%", ""]),
            row(1, ["2", "数值天气预报", "光功率预测系统技 术服务合同", "年", "0.5", "60000", "60000", "6%", "以后每年服务 费5万圆整"]),
        ]
        context = TableRepairContext(
            source_text=(
                "光伏功率预测拓展\n套\n140000\n140000\n13%\n1\n"
                "光功率国产化改造\n1\n系统 V1.0\n光功率预测系统技\n以后每年服务\n"
                "年\n60000\n6%\n0.5\n60000\n2\n数值天气预报\n术服务合同\n费5万圆整"
            ),
        )

        repaired = TableRepairService().repair_phantom_merged_name_rows(context, rows, 9)

        assert len(repaired) == 2
        assert repaired[0].cells[1].text == "光伏功率预测拓展 系统V1.0"
        assert repaired[1].cells[1].text == "数值天气预报"

    def test_truncated_service_detail_row_is_covered_by_plain_ocr_text(self):
        original_html = (
            "<table>"
            "<tr><td>序号</td><td>项目</td><td>内容</td><td>数量</td><td>单位</td>"
            "<td>生产厂家</td><td>单价</td><td>总价</td><td>备注</td></tr>"
            "<tr><td>1</td><td>数值气象服务</td><td>务。 .5 项 预报，并根据电场地形地 "
            "貌条件进行天气预报的降 尺度分析计算处理，为光 伏电站功率预测系统提供 精准的数据模型.</td>"
            "<td>0.5</td><td>项</td><td></td><td></td><td></td><td></td></tr>"
            "<tr><td>2</td><td>功率预测服务</td><td>提供后期数据库更新升级 服务。</td>"
            "<td>0.5</td><td>项</td><td></td><td></td><td></td><td></td></tr>"
            "</table>"
        )
        compare_html = (
            "<table>"
            "<tr><td>序号</td><td>项目</td><td>内容</td><td>数量</td><td>单位</td>"
            "<td>生产厂家</td><td>单价</td><td>总价</td><td>备注</td></tr>"
            "<tr><td>1</td><td>数值气象服务</td><td>每日提供高精度数值天气 "
            "预报，并根据电场地形地貌条件进行天气预报 的降尺度分析计算处理，"
            "为光伏电站功率预测系统提供精准的数据模型。</td>"
            "<td>0.5</td><td>项</td><td></td><td></td><td></td><td></td></tr>"
            "<tr><td>2</td><td>功率预测服务</td><td>提供后期数据库更新升级 服务。</td>"
            "<td>0.5</td><td>项</td><td></td><td></td><td></td><td></td></tr>"
            "</table>"
        )
        source_text = (
            "每日提供高精度数值天气\n预报，并根据电场地形地\n貌条件进行天气预报的降\n"
            "1\n0.5\n项\n数值气象服务\n尺度分析计算处理，为光\n伏电站功率预测系统提供\n精准的数据模型。\n"
            "提供后期数据库更新升级\n0.5\n项\n2\n功率预测服务"
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_raw_table_block("o1", 4, original_html, source_text)]),
            _make_doc([_make_raw_table_block("c1", 4, compare_html, source_text)]),
        )

        assert warnings == []
        assert diffs == []

    def test_contact_row_delete_does_not_pair_with_single_character_residual(self):
        original_html = (
            "<table>"
            "<tr><td>供 方</td><td>需 方</td></tr>"
            "<tr><td>单位名称（章）：国能日新科技股份有限公司 单位地址：北京市海淀区西三旗建材城中路</td>"
            "<td>单位名称(章）：斯美能源科技(青海)有限公司 单位地址：青海省西宁市城北区宁张路44号西宁</td></tr>"
            "<tr><td>27号1幢2层227号</td><td>创业孵化基地1号楼0814室</td></tr>"
            "<tr><td>法人代表：雍正</td><td>法人代表或授权委托人：</td></tr>"
            "<tr><td>委托代理人：</td><td>(签字)</td></tr>"
            "<tr><td>电 话：010-83458100</td><td>电话：18097182156</td></tr>"
            "<tr><td>传 真：010-83458107</td><td>传真：</td></tr>"
            "<tr><td>开户银行：招商银行北京大屯路支行</td><td>开户银行：招商银行股份有限公司西宁生物园区</td></tr>"
            "<tr><td></td><td>支行</td></tr>"
            "<tr><td>帐 号：110904199110901</td><td>账 号：972900591810801</td></tr>"
            "<tr><td>税 号：911101086723891430</td><td>税 号：91630000MA7588E76L</td></tr>"
            "<tr><td>政编码：100096</td><td></td></tr>"
            "<tr><td>邮</td><td>邮政编码：813000</td></tr>"
            "</table>"
        )
        compare_html = (
            "<table>"
            "<tr><td>供 方 技照</td><td>需 方 司</td></tr>"
            '<tr><td colspan="2">宁</td></tr>'
            '<tr><td colspan="2">电 话：010-83458100 电话：18097182156 传</td></tr>'
            '<tr><td colspan="2">真：010-83458107 传真： 开户银 帐 税</td></tr>'
            '<tr><td colspan="2">行：招商银行北京大屯路支行 开户银行：招商银行股份有限公司西宁生物园区</td></tr>'
            '<tr><td colspan="2">支行 号：110904199110901 账 号：972900591810801</td></tr>'
            '<tr><td colspan="2">号：911101086723891430 税 号：91630000MA7588E76L</td></tr>'
            '<tr><td colspan="2">邮政编码：100096</td></tr>'
            '<tr><td colspan="2">邮政编 码：813000</td></tr>'
            "</table>"
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 2, original_html)]),
            _make_doc([_make_table_block("c1", 2, compare_html)]),
        )

        assert warnings == []
        assert not any(diff.compare_text == "宁" for diff in diffs)
        contact_diff = next(diff for diff in diffs if "单位名称" in diff.original_text)
        assert contact_diff.diff_type == "DELETE"
        assert contact_diff.compare_text == ""

    def test_contact_row_table_ocr_error_is_covered_by_plain_phone_values(self):
        original_html = (
            "<table><tr><td>电 话：010-83458100</td><td>电话：1809718216</td></tr>"
            "<tr><td>传 真：010-83458107</td><td>传真：</td></tr></table>"
        )
        compare_html = (
            '<table><tr><td colspan="2">电 话：010-83458100 电话：18097182156 传</td></tr>'
            '<tr><td colspan="2">真：010-83458107 传真：</td></tr></table>'
        )
        original_source = "电话：18097182156\n话：010-83458100\n传真：\n真：010-83458107"
        compare_source = "话：010-83458100\n电话：18097182156\n传真：\n真：010-83458107"

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_raw_table_block("o1", 2, original_html, original_source)]),
            _make_doc([_make_raw_table_block("c1", 2, compare_html, compare_source)]),
        )

        assert warnings == []
        assert diffs == []

    def test_contact_row_real_phone_change_is_preserved(self):
        original_html = "<table><tr><td>电话：18097182156</td></tr></table>"
        compare_html = "<table><tr><td>电话：18097182157</td></tr></table>"

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_raw_table_block("o1", 2, original_html, "电话：18097182156")]),
            _make_doc([_make_raw_table_block("c1", 2, compare_html, "电话：18097182157")]),
        )

        assert warnings == []
        assert len(diffs) == 1
        assert diffs[0].diff_type == "MODIFY"

    def test_merged_sequence_with_single_name_and_phantom_continuation(self):
        """OCR merges seq+detail but puts only first name in col 1, pushing
        the second name into phantom row col 0 (e.g. '2 3' | '中期模型' | ... then '短期模型' | '')."""
        original_html = _product_table([
            "<tr><td>2</td><td>中期模型</td><td>光伏场中期功率预报 模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>3</td><td>短期模型</td><td>光伏场短期功率预报 模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>4</td><td>超短期模型</td><td>光伏场超短期功率预测模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>2 3</td><td>中期模型</td><td>光伏场中期功率预报 模型开发。 光伏场短期功率预报 模型开发。</td>"
            "<td>国能日新 国能日新</td><td>套 套</td><td>1 1</td><td></td><td></td><td></td></tr>",
            "<tr><td>短期模型</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>",
            "<tr><td>4</td><td>超短期模型</td><td>光伏场超短期功率预测模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        assert diffs == []

    def test_merged_row_with_sequence_only_phantom_is_split(self):
        original_html = _product_table([
            "<tr><td>2</td><td>中期模型</td><td>光伏场中期功率预报 模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>3</td><td>短期模型</td><td>光伏场短期功率预报 模型开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>2</td><td>中期模型 短期模型</td>"
            "<td>光伏场中期功率预报 模型开发。 光伏场短期功率预报 模型开发。</td>"
            "<td>国能日新 国能日新</td><td>套 套</td><td>1 1</td>"
            "<td></td><td></td><td></td></tr>",
            "<tr><td>3</td><td></td><td></td><td></td><td></td><td></td>"
            "<td></td><td></td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        assert diffs == []

    def test_merged_sequence_with_three_names_from_continuation_rows_is_repaired(self):
        def row(
            row_index: int,
            values: list[str],
            *,
            source_text: str = "",
        ) -> _LogicalRow:
            return _LogicalRow(
                row_index=row_index,
                cells=[
                    _LogicalCell(
                        row_index=row_index,
                        col_index=col,
                        text=value,
                        bbox=None,
                        page_no=1,
                        source_block_id="p1_table",
                        source_row=row_index,
                        source_col=col,
                    )
                    for col, value in enumerate(values)
                    if value
                ],
                page_no=1,
                source_block_id="p1_table",
                source_row=row_index,
                source_text=source_text,
            )

        rows = [
            row(0, ["2 3 4", "中期模型", "配置A 配置B 配置C", "国能日新 国能日新 国能日新", "套 套 套", "1 1 1"]),
            row(1, ["短期模型"]),
            row(2, ["超短期模型"]),
        ]

        repaired = TableRepairService().normalize_merged_sequence_rows(TableRepairContext(), rows, 6)

        assert [[cell.text for cell in repaired_row.cells[:3]] for repaired_row in repaired] == [
            ["2", "中期模型", "配置A"],
            ["3", "短期模型", "配置B"],
            ["4", "超短期模型", "配置C"],
        ]

    def test_cross_page_product_continuation_merge_keeps_continuation_location(self):
        previous_bbox = BBox(x0=10, y0=100, x1=80, y1=120)
        continuation_bbox = BBox(x0=20, y0=130, x1=160, y1=150)

        previous = _LogicalRow(
            row_index=0,
            cells=[
                _LogicalCell(0, 0, "2", previous_bbox, 1, "p1_table", 0, 0),
                _LogicalCell(0, 1, "接口模块", previous_bbox, 1, "p1_table", 0, 1),
                _LogicalCell(0, 2, "型号X-100 支持AB", previous_bbox, 1, "p1_table", 0, 2),
                _LogicalCell(0, 3, "厂商甲", previous_bbox, 1, "p1_table", 0, 3),
                _LogicalCell(0, 4, "台", previous_bbox, 1, "p1_table", 0, 4),
                _LogicalCell(0, 5, "1", previous_bbox, 1, "p1_table", 0, 5),
                _LogicalCell(0, 6, "200", previous_bbox, 1, "p1_table", 0, 6),
                _LogicalCell(0, 7, "200", previous_bbox, 1, "p1_table", 0, 7),
            ],
            page_no=1,
            source_block_id="p1_table",
            source_row=0,
            source_text="2\n接口模块\n型号X-100 支持AB",
        )
        continuation = _LogicalRow(
            row_index=1,
            cells=[
                _LogicalCell(1, 2, "CD接口", continuation_bbox, 2, "p2_table", 0, 2),
            ],
            page_no=2,
            source_block_id="p2_table",
            source_row=0,
            source_text="CD接口\n厂商甲\n台\n1\n200\n200",
        )

        repaired = TableRepairService().repair_product_continuation_rows(
            TableRepairContext(),
            [previous, continuation],
            8,
        )

        assert len(repaired) == 1
        detail_cell = next(cell for cell in repaired[0].cells if cell.col_index == 2)
        assert detail_cell.text == "型号X-100 支持AB CD接口"
        assert detail_cell.page_no == 2
        assert detail_cell.source_block_id == "p2_table"
        assert detail_cell.bbox == continuation_bbox

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
        bbox = BBox(x0=50, y0=120, x1=540, y1=300)
        orig = _make_doc([])
        comp = _make_doc([_make_table_block("c1", 1, html, bbox)])
        diffs, warnings = TableComparator().build_diffs(orig, comp)
        add_diff = next(d for d in diffs if d.diff_type == "ADD")
        assert add_diff.compare_evidence
        assert add_diff.compare_evidence[0].bbox == bbox

    def test_compare_only_stitched_table_has_multi_page_table_evidence_without_context_text(self):
        page8_table_bbox = BBox(x0=60, y0=146, x1=544, y1=522)
        page9_table_bbox = BBox(x0=55, y0=144, x1=542, y1=526)
        page8_context_bbox = BBox(x0=60, y0=50, x1=360, y1=125)
        page9_context_bbox = BBox(x0=55, y0=58, x1=360, y1=126)
        page8_table = _product_table([
            _product_row("1", "预测服务器"),
            _product_row("2", "气象服务器"),
        ])
        page9_table = _product_table([
            _product_row("3", "工作站"),
            _product_row("4", "网络设备"),
        ])
        original = _make_doc([])
        compare = _make_doc([
            _make_text_block("c8_context", 8, "国能日新科技股份有限公司 24小时服务热线 报价标题", page8_context_bbox),
            _make_table_block("c8_table", 8, page8_table, page8_table_bbox),
            _make_text_block("c9_context", 9, "国能日新科技股份有限公司 报价单位 联系人", page9_context_bbox),
            _make_table_block("c9_table", 9, page9_table, page9_table_bbox),
        ])

        diffs, warnings = TableComparator().build_diffs(original, compare)

        add_diffs = [diff for diff in diffs if diff.diff_type == "ADD" and diff.source_type == "table"]
        assert warnings == []
        assert len(add_diffs) == 1
        evidence_bboxes = [evidence.bbox for evidence in add_diffs[0].compare_evidence]
        assert evidence_bboxes == [page8_table_bbox, page9_table_bbox]
        assert page8_context_bbox not in evidence_bboxes
        assert page9_context_bbox not in evidence_bboxes

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

    def test_product_summary_row_colspan_difference_not_reported_as_delete(self):
        original_summary = "合计人民币金额200000.00元（含税价）：贰拾万圆整"
        compare_summary = "合计人民币金额200000.00元(含税价):贰拾万圆整"
        original = _make_doc([
            _make_raw_table_block(
                "original_product",
                1,
                _contract_product_table(6, original_summary),
                _contract_product_source_text(original_summary),
            )
        ])
        compare = _make_doc([
            _make_raw_table_block(
                "compare_product",
                1,
                _contract_product_table(7, compare_summary),
                _contract_product_source_text(compare_summary),
            )
        ])

        diffs, warnings = TableComparator().build_diffs(original, compare)

        assert warnings == []
        assert not any("合计人民币金额200000.00元" in (diff.original_text + diff.compare_text) for diff in diffs)

    def test_product_summary_row_is_not_merged_into_previous_product_row(self):
        summary = "合计人民币金额200000.00元（含税价）：贰拾万圆整"
        doc = _make_doc([
            _make_raw_table_block(
                "compare_product",
                1,
                _contract_product_table(7, summary),
                _contract_product_source_text(summary),
            )
        ])
        parser = LogicalTableParser()
        tables = parser.stitch_logical_tables(
            parser.parse_tables(parser.table_blocks(doc)),
            TableRepairService(),
        )
        product_table = next(table for table in tables if "合计" in table.all_cell_text())

        assert len(product_table.rows) == 3
        second_row_text = " ".join(cell.text for cell in product_table.rows[1].cells)
        summary_cells = {cell.col_index: cell.text for cell in product_table.rows[2].cells}
        assert "合计" not in second_row_text
        assert summary_cells == {0: "合计", 1: "200000.00元"}

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

    def test_severe_bbox_grid_conflict_keeps_structured_compare_with_quality_diagnostics(self):
        html = (
            "<table>"
            "<tr><td>序号</td><td>产品名称</td><td>金额</td></tr>"
            "<tr><td>1</td><td>服务器</td><td>100</td></tr>"
            "<tr><td>2</td><td>工作站</td><td>200</td></tr>"
            "</table>"
        )
        collapsed_bboxes = [
            [0, 0, 40, 20],
            [50, 0, 90, 20],
            [100, 0, 140, 20],
            [0, 0, 40, 20],
            [50, 0, 90, 20],
            [100, 0, 140, 20],
            [0, 0, 40, 20],
            [50, 0, 90, 20],
            [100, 0, 140, 20],
        ]
        comparator = TableComparator()

        diffs, warnings = comparator.build_diffs(
            _make_doc([_make_raw_table_block("o1", 1, html, html, collapsed_bboxes)]),
            _make_doc([_make_raw_table_block("c1", 1, html, html, collapsed_bboxes)]),
        )

        assert warnings == []
        assert diffs == []
        assert comparator.last_debug_payload["tier"] == "cell_level"
        assert comparator.last_debug_payload["quality"]["score_policy"] == "diagnostic_only"
        assert comparator.last_debug_payload["quality"]["route_decision"] == "structured_table_compare"
        assert comparator.last_debug_payload["tables"]["original"][0]["geometry_status"] == "severe_conflict"

    def test_flat_text_large_unmatched_emits_review_diff_instead_of_silent_skip(self):
        original_text = "\n".join(f"原表缺失字段{i}" for i in range(10))
        compare_text = "\n".join(f"对比新增字段{i}" for i in range(10))
        comparator = TableComparator()

        diffs, warnings = comparator.build_diffs(
            _make_doc([_make_table_block("o1", 1, original_text)]),
            _make_doc([_make_table_block("c1", 1, compare_text)]),
        )

        assert warnings
        assert comparator.last_debug_payload["tier"] == "flat_text_no_html"
        assert len(diffs) == 1
        assert diffs[0].match_method == "table_text_large_unmatched"
        assert diffs[0].quality_status == "NEEDS_REVIEW"
        assert "large_flat_unmatched" in diffs[0].structural_flags
        assert "TABLE_REGION_REVIEW" in diffs[0].review_flags

    def test_row_level_degradation_keeps_amount_quantity_field_checks(self, monkeypatch):
        quality = {
            "score": 0.6,
            "grid_score": 0.6,
            "bbox_score": 0.6,
            "text_score": 0.6,
            "continuation_score": 1.0,
            "business_score": 0.8,
            "html_grid_usable": False,
            "reasons": ["forced_row_level_for_test"],
        }
        monkeypatch.setattr(
            TableComparator,
            "_compute_table_quality_detail",
            staticmethod(lambda _tables: quality),
        )
        original_html = _product_table([
            "<tr><td>1</td><td>服务器</td><td>配置</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>100</td><td>100</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>1</td><td>服务器</td><td>配置</td><td>国能日新</td>"
            "<td>套</td><td>2</td><td>100</td><td>200</td><td></td></tr>",
        ])

        comparator = TableComparator()
        diffs, warnings = comparator.build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        assert comparator.last_debug_payload["tier"] == "cell_level"
        assert comparator.last_debug_payload["quality"]["score_policy"] == "diagnostic_only"
        assert not any("row_level_business_field_check" in diff.structural_flags for diff in diffs)
        assert any("1" in diff.original_text and "100" in diff.original_text for diff in diffs)
        assert any("2" in diff.compare_text and "200" in diff.compare_text for diff in diffs)

    def test_bbox_geometry_unusable_does_not_force_row_level_when_html_grid_is_usable(self):
        rows = [
            "<tr><td>1</td><td>预测服务器</td><td>14020R双电</td><td>航天联志</td>"
            "<td>台</td><td>1</td><td>8500</td><td>8500</td><td></td></tr>",
            "<tr><td>2</td><td>气象服务器</td><td>14020R双电</td><td>航天联志</td>"
            "<td>台</td><td>1</td><td>8500</td><td>8500</td><td></td></tr>",
            "<tr><td>3</td><td>工作站</td><td>288G9E</td><td>HP/超云</td>"
            "<td>台</td><td>1</td><td>4000</td><td>4000</td><td></td></tr>",
        ]
        html = _product_table(rows)
        collapsed_bboxes = []
        for row_index in range(4):
            y0 = 100 + row_index * 30
            y1 = y0 + 20
            collapsed_bboxes.extend([
                [0, y0, 90, y1],
                [100, y0, 190, y1],
                [200, y0, 290, y1],
                [0, y0, 90, y1],
                [100, y0, 190, y1],
                [200, y0, 290, y1],
                [0, y0, 90, y1],
                [100, y0, 190, y1],
                [200, y0, 290, y1],
            ])
        comparator = TableComparator()

        diffs, warnings = comparator.build_diffs(
            _make_doc([_make_raw_table_block("o1", 1, html, html, collapsed_bboxes)]),
            _make_doc([_make_raw_table_block("c1", 1, html, html, collapsed_bboxes)]),
        )

        assert diffs == []
        assert warnings == []
        assert comparator.last_debug_payload["tier"] == "cell_level"
        assert comparator.last_debug_payload["tables"]["original"][0]["geometry_status"] == "geometry_unusable"
        assert comparator.last_debug_payload["quality"]["details"]["original"]["html_grid_usable"] is True

    def test_geometry_unusable_merged_product_rows_do_not_emit_false_business_diffs(self):
        original_html = _product_table([
            "<tr><td>2</td><td>中期模型</td><td>光伏场中期功率预报 模型开发。</td>"
            "<td>国能日新</td><td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>3</td><td>短期模型</td><td>光伏场短期功率预报 模型开发。</td>"
            "<td>国能日新</td><td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>6</td><td>理论可用功率计算</td><td>理论可用功率计算</td>"
            "<td>国能日新</td><td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>7</td><td>接口开放及 系统开发</td><td>接口开放及系统开发</td>"
            "<td>国能日新</td><td>年</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>2 3</td><td>中期模型 短期模型</td><td>光伏场中期功率预报 模型开发。</td>"
            "<td>国能日新</td><td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>光伏场短期功率预报 模型开发。</td><td>国能日新</td><td>套</td>"
            "<td>1</td><td></td><td></td><td></td><td></td><td></td></tr>",
            "<tr><td>6</td><td>理论可用功率计算 接口开放及 系统开发</td><td>理论可用功率计算</td>"
            "<td>国能日新 国能日新</td><td>套</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        collapsed_bboxes = []
        for row_index in range(5):
            y0 = 100 + row_index * 30
            y1 = y0 + 20
            collapsed_bboxes.extend([
                [0, y0, 90, y1],
                [100, y0, 190, y1],
                [200, y0, 290, y1],
                [0, y0, 90, y1],
                [100, y0, 190, y1],
                [200, y0, 290, y1],
                [0, y0, 90, y1],
                [100, y0, 190, y1],
                [200, y0, 290, y1],
            ])

        comparator = TableComparator()
        diffs, warnings = comparator.build_diffs(
            _make_doc([_make_raw_table_block("o1", 1, original_html, original_html, collapsed_bboxes)]),
            _make_doc([_make_raw_table_block("c1", 1, compare_html, compare_html, collapsed_bboxes)]),
        )

        assert warnings == []
        assert diffs == []
        assert comparator.last_debug_payload["tables"]["original"][0]["geometry_status"] == "geometry_unusable"
        assert comparator.last_debug_payload["tables"]["compare"][0]["geometry_status"] == "geometry_unusable"

    def test_row_level_degradation_suppresses_high_similarity_ocr_noise(self, monkeypatch):
        quality = {
            "score": 0.6,
            "grid_score": 0.6,
            "bbox_score": 0.6,
            "text_score": 0.6,
            "continuation_score": 1.0,
            "business_score": 0.8,
            "html_grid_usable": False,
            "reasons": ["forced_row_level_for_test"],
        }
        monkeypatch.setattr(
            TableComparator,
            "_compute_table_quality_detail",
            staticmethod(lambda _tables: quality),
        )
        original_html = _product_table([
            "<tr><td>6</td><td>反向隔离装置</td><td>StoneWall-2000BF 百兆</td><td>科东</td>"
            "<td>台</td><td>1</td><td>30500</td><td>30500</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>6</td><td>反向隔离装置</td><td>StoneWal1-2000BF 百兆</td><td>科东</td>"
            "<td>台</td><td>1</td><td>30500</td><td>30500</td><td></td></tr>",
        ])

        comparator = TableComparator()
        diffs, warnings = comparator.build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        assert comparator.last_debug_payload["tier"] == "cell_level"
        assert comparator.last_debug_payload["quality"]["score_policy"] == "diagnostic_only"
        assert diffs == []

    def test_row_level_business_field_check_rejects_number_to_text_misalignment(self, monkeypatch):
        quality = {
            "score": 0.6,
            "grid_score": 0.6,
            "bbox_score": 0.6,
            "text_score": 0.6,
            "continuation_score": 1.0,
            "business_score": 0.8,
            "html_grid_usable": False,
            "reasons": ["forced_row_level_for_test"],
        }
        monkeypatch.setattr(
            TableComparator,
            "_compute_table_quality_detail",
            staticmethod(lambda _tables: quality),
        )
        original_html = _product_table([
            "<tr><td>1</td><td>国产操作系统</td><td>国产操作系统</td><td>凝思</td>"
            "<td>套</td><td>3</td><td>4000</td><td>12000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>1</td><td>国产操作系统</td><td>国产操作系统</td><td>凝思</td>"
            "<td>套</td><td>3</td><td>4000</td><td>国产操作系统</td><td></td></tr>",
        ])

        comparator = TableComparator()
        diffs, warnings = comparator.build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        assert comparator.last_debug_payload["tier"] == "cell_level"
        assert not any("row_level_business_field_check" in diff.structural_flags for diff in diffs)

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

    def test_contextual_box_character_in_interface_is_filtered_as_ocr_noise(self):
        orig_html = _product_table([
            "<tr><td>5</td><td>通信应用接 口</td><td>功率预测系统与其相关联系统通信接口开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        comp_html = _product_table([
            "<tr><td>5</td><td>通信应用接 □</td><td>功率预测系统与其相关联系统通信接口开发。</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, orig_html)]),
            _make_doc([_make_table_block("c1", 1, comp_html)]),
        )

        assert warnings == []
        assert diffs == []

    def test_standalone_box_character_change_is_not_filtered_as_interface_ocr_noise(self):
        orig_html = '<table><tr><td>选项</td><td>□ 是</td></tr></table>'
        comp_html = '<table><tr><td>选项</td><td>口 是</td></tr></table>'

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, orig_html)]),
            _make_doc([_make_table_block("c1", 1, comp_html)]),
        )

        assert warnings == []
        assert len(diffs) == 1
        assert diffs[0].diff_type == "MODIFY"

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

    def test_table_evidence_uses_neighbor_char_boxes_when_inserted_text_is_partially_missing(self):
        original_html = '<table><tr><td>签订日期</td><td>2026年4月 日</td></tr></table>'
        compare_html = '<table><tr><td>签订日期</td><td>2026年4月21日</td></tr></table>'
        cell_bboxes = [
            [0, 200, 100, 220],
            [100, 200, 300, 220],
        ]
        compare_text = "签订日期\n2026年4月2日"
        date_start = compare_text.index("2026")
        date_char_boxes = []
        for offset, char in enumerate("2026年4月2日"):
            x0 = 130 + offset * 10
            if char == "日":
                x0 = 220
            date_char_boxes.append(
                CharBox(
                    char=char,
                    page_no=1,
                    bbox=BBox(x0=x0, y0=202, x1=x0 + 8, y1=218),
                    text_index=date_start + offset,
                )
            )
        comp_block = TextBlock(
            block_id="c1",
            page_no=1,
            text=compare_text,
            bbox=BBox(x0=0, y0=200, x1=300, y1=220),
            block_type="table",
            raw_html=compare_html,
            table_cell_bboxes=cell_bboxes,
            char_boxes=date_char_boxes,
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

        assert warnings == []
        assert len(diffs) == 1
        assert diffs[0].compare_evidence[0].text == "21"
        assert diffs[0].compare_evidence[0].bbox.x0 < 210
        assert diffs[0].compare_evidence[0].bbox.x1 <= 225

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

    def test_product_subsection_restart_on_next_page_stays_stitched(self):
        doc = _make_doc([
            _make_table_block("t1", 1, _product_table([
                _product_row("1", "预测服务器"),
                _product_row("2", "气象服务器"),
                _product_row("3", "工作站"),
            ])),
            _make_table_block("t2", 2, _product_restart_continuation_table()),
        ])
        parser = LogicalTableParser()

        tables = parser.stitch_logical_tables(
            parser.parse_tables(parser.table_blocks(doc)),
            TableRepairService(),
        )

        assert len(tables) == 1
        combined_text = tables[0].all_cell_text()
        assert "国产操作系统" in combined_text
        assert "4套合计" in combined_text
        assert "预测服务器" in combined_text
        assert "气象服务器" in combined_text

    def test_quote_remarks_tail_matches_standalone_remarks_table(self):
        original = _make_doc([
            _make_table_block("o1", 1, _product_table_with_quote_remarks()),
        ])
        compare = _make_doc([
            _make_table_block("c1", 1, _product_table_without_quote_remarks()),
            _make_table_block("c2", 2, _standalone_quote_remarks_table()),
        ])

        diffs, warnings = TableComparator().build_diffs(original, compare)

        assert warnings == []
        assert not any("本报价有效期" in (diff.original_text + diff.compare_text) for diff in diffs)

    def test_standalone_quote_remarks_matches_tail_remarks(self):
        original = _make_doc([
            _make_table_block("o1", 1, _product_table_without_quote_remarks()),
            _make_table_block("o2", 2, _standalone_quote_remarks_table()),
        ])
        compare = _make_doc([
            _make_table_block("c1", 1, _product_table_with_quote_remarks()),
        ])

        diffs, warnings = TableComparator().build_diffs(original, compare)

        assert warnings == []
        assert not any("本报价有效期" in (diff.original_text + diff.compare_text) for diff in diffs)

    def test_changed_quote_remarks_are_reported_as_modify_not_add_delete_pair(self):
        original = _make_doc([
            _make_table_block("o1", 1, _product_table_with_quote_remarks("30")),
        ])
        compare = _make_doc([
            _make_table_block("c1", 1, _product_table_without_quote_remarks()),
            _make_table_block("c2", 2, _standalone_quote_remarks_table("60")),
        ])

        diffs, warnings = TableComparator().build_diffs(original, compare)

        remark_diffs = [diff for diff in diffs if "本报价有效期" in (diff.original_text + diff.compare_text)]
        assert warnings == []
        assert len(remark_diffs) == 1
        assert remark_diffs[0].diff_type == "MODIFY"
        assert "30天" in remark_diffs[0].original_text
        assert "60天" in remark_diffs[0].compare_text

    def test_split_and_merged_quote_remarks_do_not_report_modify(self):
        original = _make_doc([
            _make_table_block("o1", 1, _product_table_with_split_quote_remarks()),
        ])
        compare = _make_doc([
            _make_table_block("c1", 1, _product_table_with_merged_quote_remarks()),
        ])

        diffs, warnings = TableComparator().build_diffs(original, compare)

        assert warnings == []
        assert not any("本报价有效期" in (diff.original_text + diff.compare_text) for diff in diffs)
        assert not any("最终解释权" in (diff.original_text + diff.compare_text) for diff in diffs)

    def test_product_table_with_contact_words_does_not_use_contact_filter(self):
        original = _make_doc([
            _make_table_block("o1", 1, _product_table([
                "<tr><td>1</td><td>电话模块</td><td>开户银行接口 税号校验 邮政编码服务</td>"
                "<td>国能日新</td><td>套</td><td>1</td><td>100</td><td>100</td><td></td></tr>",
            ])),
        ])
        compare = _make_doc([
            _make_table_block("c1", 1, _product_table([
                "<tr><td>1</td><td>电话模块</td><td>开户银行接口 税号校验 邮政编码服务</td>"
                "<td>国能日新</td><td>套</td><td>1</td><td>200</td><td>200</td><td></td></tr>",
            ])),
        ])

        diffs, warnings = TableComparator().build_diffs(original, compare)

        assert warnings == []
        assert any("100" in diff.original_text and "200" in diff.compare_text for diff in diffs)

    def test_short_remark_missing_cell_is_covered_by_compare_source_text(self):
        original = _make_doc([
            _make_raw_table_block(
                "o1",
                1,
                _hardware_table_with_remark(),
                "工作站 CPU:HG3350 中国 中科可控 20000 20000 国产芯片",
            )
        ])
        compare = _make_doc([
            _make_raw_table_block(
                "c1",
                1,
                _hardware_table_with_shifted_amount_missing_remark(),
                "工作站 CPU:HG3350 中国 中科可控 20000 国产芯 片",
            )
        ])

        diffs, warnings = TableComparator().build_diffs(original, compare)

        assert warnings == []
        assert not any("国产芯片" in (diff.original_text + diff.compare_text) for diff in diffs)

    def test_short_remark_missing_cell_without_source_text_still_reports_delete(self):
        original = _make_doc([
            _make_raw_table_block(
                "o1",
                1,
                _hardware_table_with_remark(),
                "工作站 CPU:HG3350 中国 中科可控 20000 20000 国产芯片",
            )
        ])
        compare = _make_doc([
            _make_raw_table_block(
                "c1",
                1,
                _hardware_table_with_shifted_amount_missing_remark(),
                "工作站 CPU:HG3350 中国 中科可控 20000",
            )
        ])

        diffs, warnings = TableComparator().build_diffs(original, compare)

        assert warnings == []
        assert any(diff.diff_type == "DELETE" and "国产芯片" in diff.original_text for diff in diffs)

    def test_amount_missing_cell_is_not_covered_by_source_text_filter(self):
        original = _make_doc([
            _make_raw_table_block(
                "o1",
                1,
                _hardware_table_with_remark(amount="20000"),
                "工作站 CPU:HG3350 中国 中科可控 20000 20000 国产芯片",
            )
        ])
        compare = _make_doc([
            _make_raw_table_block(
                "c1",
                1,
                _hardware_table_with_remark(amount=""),
                "工作站 CPU:HG3350 中国 中科可控 国产芯片",
            )
        ])

        diffs, warnings = TableComparator().build_diffs(original, compare)

        assert warnings == []
        assert any(diff.diff_type == "DELETE" and "20000" in diff.original_text for diff in diffs)

    def test_new_product_like_table_after_quote_table_is_not_stitched_as_continuation(self):
        quote_html = _quote_table([
            "<tr><td>1</td><td>光伏功率预测拓展系统</td><td>国产化改造</td>"
            "<td>套</td><td>1</td><td>140000</td><td>140000</td><td>13%</td>"
            "<td></td><td>12个月</td><td>30天</td></tr>",
            "<tr><td>2</td><td>数值天气预报</td><td>技术服务合同</td>"
            "<td>年</td><td>0.5</td><td>60000</td><td>60000</td><td>6%</td>"
            "<td>以后每年服务费5万圆整</td><td>12个月</td><td>30天</td></tr>",
        ])
        notes_html = (
            "<table>"
            "<tr><td>备注：本报价有效期为30天；税率13%</td></tr>"
            "<tr><td>本报价设备质保期为12个月</td></tr>"
            "<tr><td>我公司保留对于报价和产品的最终解释权。</td></tr>"
            "</table>"
        )
        service_html = _service_table()

        original = _make_doc([
            _make_table_block("o1", 1, quote_html),
            _make_table_block("o2", 2, service_html),
        ])
        compare = _make_doc([
            _make_table_block("c1", 1, quote_html),
            _make_table_block("c2", 2, notes_html),
            _make_table_block("c3", 2, service_html),
        ])

        diffs, warnings = TableComparator().build_diffs(original, compare)

        assert warnings == []
        assert [diff.diff_type for diff in diffs] == ["ADD"]
        assert "备注：本报价有效期为30天" in diffs[0].compare_text
        combined_deleted_text = "\n".join(diff.original_text for diff in diffs if diff.diff_type == "DELETE")
        combined_added_text = "\n".join(diff.compare_text for diff in diffs if diff.diff_type == "ADD")
        assert "数值气象服务" not in combined_deleted_text
        assert "功率预测服务" not in combined_deleted_text
        assert "日常维护服务" not in combined_deleted_text
        assert "大写：陆万元整" not in combined_deleted_text
        assert "数值气象服务" not in combined_added_text

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

    def test_overflow_orphan_sequence_cell_is_filtered(self):
        original_html = _product_table([
            "<tr><td>5</td><td>显示器</td><td>X20H 19寸放机柜带背板安装螺丝</td><td>方大极视</td>"
            "<td>台</td><td>1</td><td>500</td><td>500</td><td></td><td>6</td></tr>",
            "<tr><td>7</td><td>防火墙</td><td>SG-8000 S8330 8口/接网监/双电源</td><td>安博通</td>"
            "<td>台</td><td>2</td><td>3000</td><td>6000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>5</td><td>显示器</td><td>X20H 19寸放机柜带背板安装螺丝</td><td>方大极视</td>"
            "<td>台</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
            "<tr><td>7</td><td>防火墙</td><td>SG-8000 S8330 8口/接网监/双电源</td><td>安博通</td>"
            "<td>台</td><td>2</td><td>3000</td><td>6000</td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        assert not any(diff.diff_type == "DELETE" and diff.original_text == "6" for diff in diffs)

    def test_d006_style_sequence_text_change_still_reports_modify(self):
        original_html = (
            "<table><tr><td>序号</td><td>场站名称</td><td>类型</td></tr>"
            "<tr><td>1</td><td>广核淮阴风电</td><td>风电</td></tr></table>"
        )
        compare_html = (
            "<table><tr><td>序号</td><td>场站名称</td><td>类型</td></tr>"
            "<tr><td>一</td><td>广核淮阴风电</td><td>风电</td></tr></table>"
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        assert len(diffs) == 1
        assert diffs[0].diff_type == "MODIFY"
        assert diffs[0].original_text == "1"
        assert diffs[0].compare_text == "一"

    def test_product_quantity_delete_is_not_filtered_as_overflow_sequence(self):
        original_html = _product_table([
            "<tr><td>5</td><td>显示器</td><td>X20H</td><td>方大极视</td>"
            "<td>台</td><td>1</td><td>500</td><td>500</td><td></td></tr>",
            "<tr><td>6</td><td>防火墙</td><td>SG-8000</td><td>安博通</td>"
            "<td>台</td><td>2</td><td>3000</td><td>6000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>5</td><td>显示器</td><td>X20H</td><td>方大极视</td>"
            "<td>台</td><td></td><td>500</td><td>500</td><td></td></tr>",
            "<tr><td>6</td><td>防火墙</td><td>SG-8000</td><td>安博通</td>"
            "<td>台</td><td>2</td><td>3000</td><td>6000</td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        assert any(diff.diff_type == "DELETE" and diff.original_text == "1" for diff in diffs)

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

    def test_d012_style_split_summary_amounts_use_plain_ocr_pairs(self):
        original_html = _product_table([
            _product_row("1", "国产操作系统", "12000"),
            "<tr><td colspan='7'>小计 1套总计</td><td>12000</td><td></td></tr>",
            "<tr><td colspan='7'>99000 198000</td><td></td><td></td></tr>",
            "<tr><td colspan='7'>2套合计 6套总合计</td><td>594000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            _product_row("1", "国产操作系统", "12000"),
            "<tr><td>小计</td><td>12000</td><td></td></tr>",
            "<tr><td>1套总计</td><td>99000</td><td></td></tr>",
            "<tr><td>2套合计</td><td>198000</td><td></td></tr>",
            "<tr><td>6套总合计</td><td>594000</td><td></td></tr>",
        ])
        source_text = "小计\n12000\n1套总计\n99000\n2套合计\n198000\n6套总合计\n594000"

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_raw_table_block("o1", 1, original_html, source_text)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "594000' -> '198000" not in combined

    def test_split_summary_amount_repair_still_reports_real_change(self):
        original_html = _product_table([
            _product_row("1", "国产操作系统", "12000"),
            "<tr><td colspan='7'>小计 1套总计</td><td>12000</td><td></td></tr>",
            "<tr><td colspan='7'>99000 198000</td><td></td><td></td></tr>",
            "<tr><td colspan='7'>2套合计 6套总合计</td><td>594000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            _product_row("1", "国产操作系统", "12000"),
            "<tr><td>小计</td><td>12000</td><td></td></tr>",
            "<tr><td>1套总计</td><td>99000</td><td></td></tr>",
            "<tr><td>2套合计</td><td>199000</td><td></td></tr>",
            "<tr><td>6套总合计</td><td>594000</td><td></td></tr>",
        ])
        source_text = "小计\n12000\n1套总计\n99000\n2套合计\n198000\n6套总合计\n594000"

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_raw_table_block("o1", 1, original_html, source_text)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "'198000' -> '199000'" in combined

    def test_split_summary_without_plain_ocr_pairs_keeps_structured_amounts(self):
        original_html = _product_table([
            _product_row("1", "国产操作系统", "12000"),
            "<tr><td colspan='7'>2套合计 6套总合计</td><td>594000</td><td></td></tr>",
        ])
        compare_html = _product_table([
            _product_row("1", "国产操作系统", "12000"),
            "<tr><td>2套合计</td><td>198000</td><td></td></tr>",
            "<tr><td>6套总合计</td><td>594000</td><td></td></tr>",
        ])

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "'594000' -> '198000'" in combined

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

        comparator = TableComparator()
        diffs, warnings = comparator.build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([compare_block]),
        )

        assert warnings == []
        assert diffs == []
        compare_decisions = comparator.last_debug_payload["repair"]["compare"]["decisions"]
        source_fill_decision = next(
            decision
            for decision in compare_decisions
            if decision["repair_type"] == "summary_source_fill"
        )
        assert source_fill_decision["source_block_id"] == "c1"
        assert source_fill_decision["signals"]["label"] == "小计"
        assert source_fill_decision["signals"]["amount"] == "22500"
        assert source_fill_decision["before_metrics"]["nonempty_cell_count"] == 1
        assert source_fill_decision["after_metrics"]["nonempty_cell_count"] == 2

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

    def test_malformed_product_and_summary_rows_are_covered_by_plain_ocr_text(self):
        original_html = _product_table([
            "<tr><td>8</td><td colspan='4'></td><td>国能日新探针 系统V3.0</td>"
            "<td></td><td>agent 软件</td><td></td></tr>",
            "<tr><td colspan='5'>1套总计 四套合计</td><td>备注</td><td></td>"
            "<td>1、本报价有效期为30天； 2、本报价设备质保期为12个月；</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>8</td><td>国能日新探针 系统V3.0</td><td>agent 软件</td>"
            "<td>国能 日新</td><td>套</td><td>2</td><td>200</td><td>400</td><td></td></tr>",
            "<tr><td colspan='7'>小计</td><td>12400</td><td></td></tr>",
            "<tr><td colspan='7'>1套总计</td><td>29500</td><td></td></tr>",
            "<tr><td colspan='7'>四套合计</td><td>118000</td><td></td></tr>",
        ])
        source_text = (
            "国" + "其他内容" * 20 + "\n"
            "8\n国能日新探针\n系统V3.0\nagent 软件\n国能 日新\n套\n2\n200\n400\n"
            "小计\n12400\n1套总计\n29500\n四套合计\n118000\n备注\n"
            "1、本报价有效期为30天；\n2、本报价设备质保期为12个月；"
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_raw_table_block("o1", 4, original_html, source_text)]),
            _make_doc([_make_raw_table_block("c1", 4, compare_html, source_text)]),
        )

        assert warnings == []
        assert diffs == []

    def test_malformed_summary_source_amount_conflict_still_reports(self):
        original_html = _product_table([
            "<tr><td>8</td><td colspan='4'></td><td>国能日新探针 系统V3.0</td>"
            "<td></td><td>agent 软件</td><td></td></tr>",
            "<tr><td colspan='5'>1套总计 四套合计</td><td>备注</td><td></td>"
            "<td>1、本报价有效期为30天； 2、本报价设备质保期为12个月；</td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>8</td><td>国能日新探针 系统V3.0</td><td>agent 软件</td>"
            "<td>国能 日新</td><td>套</td><td>2</td><td>200</td><td>400</td><td></td></tr>",
            "<tr><td colspan='7'>小计</td><td>12400</td><td></td></tr>",
            "<tr><td colspan='7'>1套总计</td><td>29500</td><td></td></tr>",
            "<tr><td colspan='7'>四套合计</td><td>119000</td><td></td></tr>",
        ])
        original_source = (
            "国" + "其他内容" * 20 + "\n"
            "8\n国能日新探针\n系统V3.0\nagent 软件\n国能 日新\n套\n2\n200\n400\n"
            "小计\n12400\n1套总计\n29500\n四套合计\n118000\n备注"
        )
        compare_source = original_source.replace("118000", "119000")

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_raw_table_block("o1", 4, original_html, original_source)]),
            _make_doc([_make_raw_table_block("c1", 4, compare_html, compare_source)]),
        )

        assert warnings == []
        combined = " ".join(diff.readable_change for diff in diffs)
        assert "119000" in combined

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

    def test_full_row_colspan_coverage_does_not_create_duplicate_field_diffs(self):
        original_html = (
            "<table>"
            "<tr><td>字段甲：AA-10001</td><td>字段乙：BB-20002</td></tr>"
            "<tr><td>字段丙：CC-30003</td><td>字段丁：</td></tr>"
            "<tr><td>字段戊：长文本甲长文本乙</td><td>字段己：长文本丙长文本丁</td></tr>"
            "<tr><td></td><td>尾缀文本</td></tr>"
            "<tr><td>字段庚：770001</td><td>字段辛：880002</td></tr>"
            "</table>"
        )
        compare_html = (
            "<table>"
            "<tr><td colspan='2'>字段甲：AA-10001 字段乙：BB-20002 字</td></tr>"
            "<tr><td colspan='2'>段丙：CC-30003 字段丁： 字段</td></tr>"
            "<tr><td colspan='2'>戊：长文本甲长文本乙 字段己：长文本丙长文本丁</td></tr>"
            "<tr><td colspan='2'>尾缀文本 字段庚：770001 字段辛：880002</td></tr>"
            "</table>"
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        assert diffs == []

    def test_full_row_colspan_coverage_preserves_real_value_change(self):
        original_html = (
            "<table>"
            "<tr><td>字段甲：AA-10001</td><td>字段乙：BB-20002</td></tr>"
            "<tr><td>字段丙：CC-30003</td><td>字段丁：DD-40004</td></tr>"
            "</table>"
        )
        compare_html = (
            "<table>"
            "<tr><td colspan='2'>字段甲：AA-10001 字段乙：BB-99999</td></tr>"
            "<tr><td colspan='2'>字段丙：CC-30003 字段丁：DD-40004</td></tr>"
            "</table>"
        )

        diffs, warnings = TableComparator().build_diffs(
            _make_doc([_make_table_block("o1", 1, original_html)]),
            _make_doc([_make_table_block("c1", 1, compare_html)]),
        )

        assert warnings == []
        combined = " ".join(diff.original_text + diff.compare_text for diff in diffs)
        assert "BB-20002" in combined
        assert "BB-99999" in combined

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

    def test_adjacent_sequence_detail_cell_merged_by_structure_is_repaired_from_plain_ocr(self):
        original_html = _product_table([
            "<tr><td>6</td><td>理论可用功 率计算</td><td>理论可用功率计算</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>7</td><td>接口开放及 系统开发</td><td>接口开放及系统开发</td><td>国能日新</td>"
            "<td>年</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>8</td><td>技术维护服 务费</td><td>数值天气预报、系统维护、备份等服务。</td><td>国能日新</td>"
            "<td>年</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_html = _product_table([
            "<tr><td>6</td><td>理论可用功 率计算</td><td>理论可用功率计算 接口开放及系统开发</td>"
            "<td>国能日新</td><td>套</td><td>1</td><td></td><td></td><td></td></tr>",
            "<tr><td>8</td><td>技术维护服 务费</td><td>数值天气预报、系统维护、备份等服务。</td><td>国能日新</td>"
            "<td>年</td><td>1</td><td></td><td></td><td></td></tr>",
        ])
        compare_text = (
            "理论可用功\n6\n理论可用功率计算\n国能日新\n套\n1\n率计算\n"
            "接口开放及\n7\n接口开放及系统开发\n国能日新\n年\n1\n系统开发\n"
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
