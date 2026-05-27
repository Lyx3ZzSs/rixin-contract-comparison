from __future__ import annotations

from app.models import BBox, CharBox, Clause, ClausePair, DiffItem, Document, EvidenceBox, Page, TextBlock, TextRange
from app.services.clause_splitter import ClauseSplitter
from app.services.cover_metadata import CoverMetadataComparator
from app.services.diff_engine import DiffEngine
from app.services.evidence_locator import EvidenceLocator
from app.services.matcher import ClauseMatcher
from app.services.normalizer import TextNormalizer
from app.services.table_compare import TableComparator


def test_text_normalizer_removes_page_number_and_compacts_text() -> None:
    text = " 合同编号：ABC-1 \n 第 1 页 \n 共15页第3页 \n付款　期限 为  30 天。\n\n\n"
    normalized = TextNormalizer().normalize(text)
    assert "第 1 页" not in normalized
    assert "共15页第3页" not in normalized
    assert "付款 期限 为 30 天。" in normalized


def test_clause_splitter_detects_numbered_clauses() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_b1",
                        page_no=1,
                        text="1. Payment\nBuyer shall pay within 30 days.\n2. Delivery\nSeller shall deliver goods.",
                        bbox=BBox(x0=10, y0=10, x1=500, y1=120),
                    )
                ],
            )
        ],
    )
    clauses = ClauseSplitter().split(document, "O")
    assert len(clauses) == 2
    assert clauses[0].clause_no == "1"
    assert "Buyer shall pay" in clauses[0].text


def test_clause_splitter_keeps_amount_numbered_clause_separate() -> None:
    document = Document(
        filename="scan.pdf",
        path="scan.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_b1",
                        page_no=1,
                        text="3.7负责提供现场被采集设备的点表与通讯协议。",
                        bbox=BBox(x0=88, y0=612, x1=346, y1=625),
                        block_type="text",
                    ),
                    TextBlock(
                        block_id="p1_b2",
                        page_no=1,
                        text="3.8按照合同规定的时间、方式、金额向乙方付款。",
                        bbox=BBox(x0=88, y0=639, x1=361, y1=652),
                        block_type="text",
                    ),
                    TextBlock(
                        block_id="p1_b3",
                        page_no=1,
                        text="3.9负责在项目实施过程中的现场阻工、征地等协调工作。",
                        bbox=BBox(x0=88, y0=661, x1=395, y1=674),
                        block_type="text",
                    ),
                    TextBlock(
                        block_id="p1_b4",
                        page_no=1,
                        text="四、合同金额",
                        bbox=BBox(x0=93, y0=680, x1=179, y1=697),
                        block_type="paragraph_title",
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["3.7", "3.8", "3.9", "四"]
    assert clauses[1].text == "3.8按照合同规定的时间、方式、金额向乙方付款。"
    assert "合同金额" not in clauses[2].text


def test_clause_splitter_preserves_char_boxes_for_split_clauses() -> None:
    text = "1. Payment\nBuyer shall pay.\n2. Delivery\nSeller shall deliver."
    char_boxes = [
        CharBox(
            char=char,
            page_no=1,
            bbox=BBox(x0=10 + index * 4, y0=20, x1=13 + index * 4, y1=30),
            text_index=index,
        )
        for index, char in enumerate(text)
    ]
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_b1",
                        page_no=1,
                        text=text,
                        bbox=BBox(x0=10, y0=20, x1=300, y1=80),
                        char_boxes=char_boxes,
                    )
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["1", "2"]
    assert clauses[0].char_boxes[0] is not None
    assert clauses[0].char_boxes[0].char == "1"
    assert clauses[0].char_boxes[-1] is not None
    assert clauses[0].char_boxes[-1].char == "."
    assert clauses[1].char_boxes[0] is not None
    assert clauses[1].char_boxes[0].char == "2"


def test_clause_splitter_does_not_guess_unnumbered_ocr_lines_as_titles() -> None:
    document = Document(
        filename="scan.pdf",
        path="scan.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_b1",
                        page_no=1,
                        text="14.2其他约定",
                        bbox=BBox(x0=80, y0=80, x1=180, y1=96),
                        block_type="ocr_line",
                    ),
                    TextBlock(
                        block_id="p1_b2",
                        page_no=1,
                        text="系统开放性与可配置性要求",
                        bbox=BBox(x0=110, y0=114, x1=236, y1=127),
                        block_type="ocr_line",
                    ),
                    TextBlock(
                        block_id="p1_b3",
                        page_no=1,
                        text="预测文件上报接口开放",
                        bbox=BBox(x0=130, y0=138, x1=235, y1=150),
                        block_type="ocr_line",
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 1
    assert "系统开放性与可配置性要求" in clauses[0].text
    assert "预测文件上报接口开放" in clauses[0].text


def test_clause_splitter_uses_geometry_when_any_layout_order_is_missing() -> None:
    document = Document(
        filename="scan.pdf",
        path="scan.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_b1",
                        page_no=1,
                        text="7.2在质保期内，乙方有权按照成本价收",
                        bbox=BBox(x0=86, y0=700, x1=530, y1=716),
                        block_type="text",
                        layout_order=20,
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p2_b1",
                        page_no=2,
                        text="取维护费。",
                        bbox=BBox(x0=66, y0=64, x1=121, y1=78),
                        block_type="text",
                        layout_order=1,
                    ),
                    TextBlock(
                        block_id="p2_b2",
                        page_no=2,
                        text="7.3验收标准如下。",
                        bbox=BBox(x0=89, y0=88, x1=230, y1=101),
                        block_type="text",
                        layout_order=2,
                    ),
                    TextBlock(
                        block_id="p2_noise",
                        page_no=2,
                        text="心",
                        bbox=BBox(x0=0, y0=533, x1=7, y1=604),
                        block_type="ocr_line",
                        layout_order=None,
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    clause_72 = next(clause for clause in clauses if clause.clause_no == "7.2")
    assert "取维护费。" in clause_72.text
    assert "心" not in clause_72.text


def test_clause_splitter_keeps_product_table_cells_in_parent_clause() -> None:
    document = Document(
        filename="table.pdf",
        path="table.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_b1",
                        page_no=1,
                        text="第一条产品名称、数量、单价及合计",
                        bbox=BBox(x0=10, y0=10, x1=500, y1=30),
                    ),
                    TextBlock(
                        block_id="p1_b2",
                        page_no=1,
                        text="产品名称\n规格型号\n数量\n单价（元）\n合计（元）\n智能电表采集终",
                        bbox=BBox(x0=10, y0=35, x1=500, y1=65),
                        block_type="table",
                    ),
                    TextBlock(
                        block_id="p1_b3",
                        page_no=1,
                        text="端\nHY-EM300\n120 台\n1,230.00\n147,600.00",
                        bbox=BBox(x0=10, y0=70, x1=500, y1=100),
                        block_type="table",
                    ),
                    TextBlock(
                        block_id="p1_b4",
                        page_no=1,
                        text="边缘网关\nHY-GW200\n20 台\n2,500.00\n50,000.00",
                        bbox=BBox(x0=10, y0=105, x1=500, y1=135),
                        block_type="table",
                    ),
                    TextBlock(
                        block_id="p1_b5",
                        page_no=1,
                        text="第二条质量要求",
                        bbox=BBox(x0=10, y0=150, x1=500, y1=170),
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 2
    assert clauses[0].clause_no == "第一条"
    assert "120 台" not in clauses[0].text
    assert "1,230.00" not in clauses[0].text
    assert "边缘网关" not in clauses[0].text
    assert clauses[1].clause_no == "第二条"


def test_clause_splitter_skips_cover_title_and_header_footer() -> None:
    document = Document(
        filename="scan.pdf",
        path="scan.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_title",
                        page_no=1,
                        text="江苏中广核采购合同",
                        bbox=BBox(x0=100, y0=80, x1=500, y1=120),
                        block_type="doc_title",
                    ),
                    TextBlock(
                        block_id="p1_header",
                        page_no=1,
                        text="合同编号：XX-C-260520",
                        bbox=BBox(x0=80, y0=20, x1=260, y1=40),
                        block_type="page_header",
                    ),
                    TextBlock(
                        block_id="p1_clause",
                        page_no=1,
                        text="第一条服务内容\n乙方应提供功率预测系统。",
                        bbox=BBox(x0=60, y0=180, x1=520, y1=230),
                        block_type="text",
                    ),
                    TextBlock(
                        block_id="p1_footer",
                        page_no=1,
                        text="第 1 页",
                        bbox=BBox(x0=280, y0=800, x1=330, y1=820),
                        block_type="footer",
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 1
    assert clauses[0].clause_no == "第一条"
    assert "江苏中广核" not in clauses[0].text
    assert "合同编号" not in clauses[0].text
    assert "第 1 页" not in clauses[0].text


def test_clause_splitter_attaches_table_to_previous_clause_without_new_clause() -> None:
    document = Document(
        filename="scan.pdf",
        path="scan.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_clause",
                        page_no=1,
                        text="第一条采购内容",
                        bbox=BBox(x0=60, y0=120, x1=500, y1=145),
                        block_type="text",
                    ),
                    TextBlock(
                        block_id="p1_table",
                        page_no=1,
                        text="1 套风电功率预测系统\n2 套光伏功率预测系统\n合计 8,000.00",
                        bbox=BBox(x0=60, y0=150, x1=500, y1=220),
                        block_type="table",
                    ),
                    TextBlock(
                        block_id="p1_clause2",
                        page_no=1,
                        text="第二条付款方式",
                        bbox=BBox(x0=60, y0=240, x1=500, y1=265),
                        block_type="text",
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 2
    assert clauses[0].clause_no == "第一条"
    assert "2 套光伏功率预测系统" not in clauses[0].text
    assert clauses[1].clause_no == "第二条"


def test_clause_splitter_excludes_listing_table_from_subject_clause() -> None:
    document = Document(
        filename="subject.pdf",
        path="subject.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_title",
                        page_no=1,
                        text="一、标的物",
                        bbox=BBox(x0=80, y0=120, x1=160, y1=145),
                    ),
                    TextBlock(
                        block_id="p1_intro",
                        page_no=1,
                        text="乙方应向甲方合格地提供以下设备：",
                        bbox=BBox(x0=80, y0=150, x1=360, y1=170),
                    ),
                    TextBlock(
                        block_id="p1_header",
                        page_no=1,
                        text="序号\n产品名称\n详细配置\n品牌\n单位\n数量\n单价\n金额\n备注",
                        bbox=BBox(x0=60, y0=180, x1=520, y1=210),
                        block_type="table",
                    ),
                    TextBlock(
                        block_id="p1_row",
                        page_no=1,
                        text="1\n预测服务器\n14020R 双电\nCPU:1*4 核;内存:\n16G;硬盘:2T SATA;\n航天联志\n台\n1\n8500\n8500",
                        bbox=BBox(x0=60, y0=220, x1=520, y1=320),
                        block_type="table",
                    ),
                    TextBlock(
                        block_id="p1_price",
                        page_no=1,
                        text="上述价格为含13%增值税价格。",
                        bbox=BBox(x0=80, y0=340, x1=500, y1=360),
                    ),
                    TextBlock(
                        block_id="p1_next",
                        page_no=1,
                        text="二、乙方义务",
                        bbox=BBox(x0=80, y0=380, x1=180, y1=400),
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["一", "二"]
    assert "预测服务器" not in clauses[0].text
    assert "14020R" not in clauses[0].text
    assert "上述价格" in clauses[0].text


def test_clause_splitter_keeps_price_explanation_after_listing_table() -> None:
    document = Document(
        filename="subject.pdf",
        path="subject.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_title",
                        page_no=1,
                        text="一、标的物",
                        bbox=BBox(x0=80, y0=120, x1=160, y1=145),
                    ),
                    TextBlock(
                        block_id="p1_intro",
                        page_no=1,
                        text="乙方应向甲方合格地提供以下设备：",
                        bbox=BBox(x0=80, y0=150, x1=360, y1=170),
                    ),
                    TextBlock(
                        block_id="p1_model",
                        page_no=1,
                        text="288G9E/A2202AS",
                        bbox=BBox(x0=170, y0=190, x1=250, y1=210),
                        block_type="table",
                    ),
                    TextBlock(
                        block_id="p1_config",
                        page_no=1,
                        text="StoneWall-200BF",
                        bbox=BBox(x0=170, y0=220, x1=250, y1=240),
                        block_type="table",
                    ),
                    TextBlock(
                        block_id="p1_price1",
                        page_no=1,
                        text="上述价格为含13%增值税价格，总金额包括光伏功率预测系统V2.0软件部分价格，支",
                        bbox=BBox(x0=80, y0=280, x1=540, y1=300),
                    ),
                    TextBlock(
                        block_id="p1_price2",
                        page_no=1,
                        text="持该系统所需的硬件设备价格。该价格为固定不变价，包括设备及随机附件的设计、采购、",
                        bbox=BBox(x0=60, y0=310, x1=540, y1=330),
                    ),
                    TextBlock(
                        block_id="p1_price3",
                        page_no=1,
                        text="制造、税类（包含关税）、包装、运输、保险费用：还包含安装调试、技术服务（包含技术",
                        bbox=BBox(x0=60, y0=340, x1=540, y1=360),
                    ),
                    TextBlock(
                        block_id="p1_price4",
                        page_no=1,
                        text="资料、图纸的提供）、质保期内服务的费用。",
                        bbox=BBox(x0=60, y0=370, x1=360, y1=390),
                    ),
                    TextBlock(
                        block_id="p1_price5",
                        page_no=1,
                        text="上述价格不包含二次搬运、征地、阻工、爆破、通信接口协调等费用。",
                        bbox=BBox(x0=80, y0=400, x1=500, y1=420),
                    ),
                    TextBlock(
                        block_id="p1_next",
                        page_no=1,
                        text="二、乙方义务",
                        bbox=BBox(x0=80, y0=450, x1=180, y1=470),
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["一", "二"]
    assert "288G9E" not in clauses[0].text
    assert "StoneWall" not in clauses[0].text
    assert "上述价格为含13%" in clauses[0].text
    assert "持该系统所需的硬件设备价格" in clauses[0].text
    assert "制造、税类" in clauses[0].text


def test_table_comparator_does_not_infer_table_from_unstructured_text() -> None:
    original = table_document(
        [
            ("o1", "序号", "table"),
            ("o2", "产品名称", "table"),
            ("o3", "预测服务器", "table"),
            ("o4", "14020R双电", "table"),
            ("o5", "CPU:1*4核;内存:", "table"),
            ("o6", "16G;硬盘:2T", "table"),
            ("o7", "SATA:网口:6个", "table"),
            ("o8", "航天联志", "table"),
            ("o9", "台", "table"),
        ]
    )
    compare = table_document(
        [
            ("n1", "序号\n产品名称\n详细配置\n品牌\n单位\n数量\n单价\n金额\n备注", "text"),
            ("n2", "1\n预测服务器", "text"),
            ("n3", "14020R 双电\nCPU:1*4 核;内存:\n16G;硬盘:2T SATA;\n网口:6 个", "text"),
            ("n4", "航天联志\n台\n1\n8500\n8500", "text"),
        ]
    )

    diffs, warnings = TableComparator().build_diffs(original, compare)

    assert diffs == []
    assert warnings == ["部分表格文本无法可靠按行/单元格配对，已跳过大范围表格字符级高亮。"]


def test_table_comparator_warns_when_no_structured_table_regions() -> None:
    original = table_document([("o1", "序号\n产品名称\n预测服务器", "text")])
    compare = table_document([("n1", "序号\n产品名称\n预测服务器", "text")])

    diffs, warnings = TableComparator().build_diffs(original, compare)

    assert diffs == []
    assert warnings == ["未获得结构化表格区域，已跳过表格比对。"]


def test_table_comparator_reports_small_cell_change() -> None:
    original = table_document([("o1", "StoneWall-200BF", "table")])
    compare = table_document([("n1", "StoneWall-2000BF", "table")])

    diffs, warnings = TableComparator().build_diffs(original, compare)

    assert warnings == []
    assert len(diffs) == 1
    assert diffs[0].title == "表格字段：标的物"
    assert diffs[0].diff_type == "MODIFY"
    assert diffs[0].original_evidence[0].method == "table_cell"


def test_clause_splitter_starts_after_cover_and_ignores_cover_quantities() -> None:
    document = Document(
        filename="cover.pdf",
        path="cover.pdf",
        page_count=3,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_no",
                        page_no=1,
                        text="合同编号：XX-C-260520",
                        bbox=BBox(x0=80, y0=60, x1=240, y1=80),
                    ),
                    TextBlock(
                        block_id="p1_title",
                        page_no=1,
                        text="江苏中广核\n4套风电功率预测系统V1.0\n2套光伏功率预测系统V2.0\n采购合同",
                        bbox=BBox(x0=150, y0=160, x1=480, y1=310),
                    ),
                    TextBlock(
                        block_id="p1_date",
                        page_no=1,
                        text="签订日期\n2026年4月21日",
                        bbox=BBox(x0=140, y0=640, x1=380, y1=670),
                    ),
                    TextBlock(
                        block_id="p1_page",
                        page_no=1,
                        text="共15页第1页",
                        bbox=BBox(x0=280, y0=760, x1=340, y1=775),
                    ),
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p2_body",
                        page_no=2,
                        text="正文",
                        bbox=BBox(x0=280, y0=80, x1=340, y1=110),
                    ),
                    TextBlock(
                        block_id="p2_intro",
                        page_no=2,
                        text="甲乙双方经友好协商达成合同如下：",
                        bbox=BBox(x0=80, y0=120, x1=520, y1=145),
                    ),
                    TextBlock(
                        block_id="p2_clause",
                        page_no=2,
                        text="一、标的物\n乙方应向甲方提供系统。",
                        bbox=BBox(x0=80, y0=160, x1=520, y1=210),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["", "一"]
    assert "江苏中广核" not in "\n".join(clause.text for clause in clauses)
    assert "4套风电" not in "\n".join(clause.text for clause in clauses)
    assert "共15页第1页" not in "\n".join(clause.text for clause in clauses)
    assert "达成合同如下" in clauses[0].text
    assert "标的物" in clauses[1].text


def test_cover_metadata_compares_cover_fields_without_title_false_positive() -> None:
    original = cover_document("XX-C-260520", "2026年4月21日")
    compare = cover_document("", "2026 年4 月\n日")

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    assert {diff.title for diff in diffs} == {"封面字段：合同编号", "封面字段：签订日期"}
    assert all("项目名称" not in diff.title for diff in diffs)
    assert any(diff.diff_type == "DELETE" and diff.original_text == "XX-C-260520" for diff in diffs)
    date_diff = next(diff for diff in diffs if diff.title == "封面字段：签订日期")
    assert date_diff.diff_type == "MODIFY"
    assert date_diff.original_evidence[0].highlight_type == "DELETE"
    assert date_diff.compare_evidence == []


def test_cover_metadata_joins_split_same_line_date_and_marks_deleted_day() -> None:
    original = Document(
        filename="original.pdf",
        path="original.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="o_label",
                        page_no=1,
                        text="签订日期",
                        bbox=BBox(x0=100, y0=120, x1=160, y1=140),
                        block_type="table",
                    ),
                    TextBlock(
                        block_id="o_value",
                        page_no=1,
                        text="2026年4月21日",
                        bbox=BBox(x0=200, y0=120, x1=310, y1=140),
                        block_type="table",
                        char_boxes=[
                            CharBox(
                                char=char,
                                page_no=1,
                                bbox=BBox(x0=200 + index * 10, y0=120, x1=208 + index * 10, y1=140),
                                text_index=index,
                            )
                            for index, char in enumerate("2026年4月21日")
                        ],
                    ),
                ],
            ),
            Page(page_no=2, width=595, height=842, blocks=[TextBlock(block_id="o_body", page_no=2, text="正文", bbox=BBox(x0=280, y0=80, x1=340, y1=110))]),
        ],
    )
    compare = Document(
        filename="compare.pdf",
        path="compare.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="n_label",
                        page_no=1,
                        text="签订日期",
                        bbox=BBox(x0=100, y0=120, x1=160, y1=140),
                        block_type="table",
                    ),
                    TextBlock(
                        block_id="n_value_month",
                        page_no=1,
                        text="2026年4月",
                        bbox=BBox(x0=200, y0=120, x1=270, y1=140),
                        block_type="table",
                    ),
                    TextBlock(
                        block_id="n_value_day",
                        page_no=1,
                        text="日",
                        bbox=BBox(x0=300, y0=120, x1=310, y1=140),
                        block_type="table",
                    ),
                ],
            ),
            Page(page_no=2, width=595, height=842, blocks=[TextBlock(block_id="n_body", page_no=2, text="正文", bbox=BBox(x0=280, y0=80, x1=340, y1=110))]),
        ],
    )

    fields = CoverMetadataComparator().extract(compare)
    diffs = CoverMetadataComparator().build_diffs(original, compare)

    assert fields["sign_date"].value == "2026年4月日"
    date_diff = next(diff for diff in diffs if diff.title == "封面字段：签订日期")
    assert date_diff.diff_type == "MODIFY"
    assert date_diff.original_text == "2026年4月21日"
    assert date_diff.compare_text == "2026年4月日"
    assert date_diff.original_snippet == "21"
    assert date_diff.compare_snippet == ""
    assert [(item.start, item.end, item.highlight_type) for item in date_diff.original_change_ranges] == [(7, 9, "DELETE")]
    assert date_diff.compare_change_ranges == []
    assert [(evidence.text, evidence.highlight_type) for evidence in date_diff.original_evidence] == [("21", "DELETE")]
    assert date_diff.compare_evidence == []


def test_cover_metadata_uses_html_table_cells_and_add_highlight_for_inserted_day() -> None:
    buyer = "江苏东大金智信息系统有限公司"
    seller = "国能日新科技股份有限公司"
    original_html = (
        "<table>"
        f"<tr><td>甲方</td><td>{buyer}</td></tr>"
        f"<tr><td>乙方</td><td>{seller}</td></tr>"
        "<tr><td>签订地点</td><td>北京</td></tr>"
        "<tr><td>签订日期</td><td>2026年4月 日</td></tr>"
        "</table>"
    )
    compare_html = original_html.replace("2026年4月 日", "2026年4月21日")
    cell_bboxes = [
        [140, 550, 200, 575], [240, 550, 445, 575],
        [140, 580, 200, 605], [240, 580, 420, 605],
        [140, 610, 200, 635], [240, 610, 300, 635],
        [140, 640, 200, 665], [240, 640, 390, 665],
    ]

    original_text = f"甲方\n{buyer}\n乙方\n{seller}\n北京\n签订地点\n2026年4月\n签订日期\n日"
    compare_text = f"甲方\n{buyer}\n乙方\n{seller}\n北京\n签订地点\n2026年4月21日\n签订日期"

    def char_boxes(text: str) -> list[CharBox]:
        boxes: list[CharBox] = []
        offset = 0
        for line_no, line in enumerate(text.splitlines()):
            y0 = 550 + line_no * 30
            if line in {"甲方", "乙方", "签订地点", "签订日期"}:
                x0 = 150
            elif line == "2026年4月21日":
                x0 = 246
            else:
                x0 = 240
            for index, char in enumerate(line):
                if line == "2026年4月21日" and char in {"2", "1"} and index in {7, 8}:
                    char_x0 = 338 + (index - 7) * 8
                else:
                    char_x0 = x0 + index * 12
                boxes.append(
                    CharBox(
                        char=char,
                        page_no=1,
                        bbox=BBox(x0=char_x0, y0=y0, x1=char_x0 + 8, y1=y0 + 18),
                        text_index=offset + index,
                    )
                )
            offset += len(line) + 1
        return boxes

    def doc(name: str, text: str, html: str) -> Document:
        return Document(
            filename=f"{name}.pdf",
            path=f"{name}.pdf",
            page_count=2,
            pages=[
                Page(
                    page_no=1,
                    width=595,
                    height=842,
                    blocks=[
                        TextBlock(
                            block_id=f"{name}_cover_table",
                            page_no=1,
                            text=text,
                            bbox=BBox(x0=120, y0=530, x1=470, y1=690),
                            block_type="table",
                            char_boxes=char_boxes(text),
                            raw_html=html,
                            table_cell_bboxes=cell_bboxes,
                        )
                    ],
                ),
                Page(page_no=2, width=595, height=842, blocks=[
                    TextBlock(block_id=f"{name}_body", page_no=2, text="正文", bbox=BBox(x0=280, y0=80, x1=340, y1=110))
                ]),
            ],
        )

    diffs = CoverMetadataComparator().build_diffs(
        doc("original", original_text, original_html),
        doc("compare", compare_text, compare_html),
    )

    assert not any(diff.title == "封面字段：甲方" for diff in diffs)
    date_diff = next(diff for diff in diffs if diff.title == "封面字段：签订日期")
    assert date_diff.original_text == "2026年4月 日"
    assert date_diff.compare_text == "2026年4月21日"
    assert date_diff.original_evidence == []
    assert [(item.start, item.end, item.highlight_type) for item in date_diff.compare_change_ranges] == [(7, 9, "ADD")]
    assert [(evidence.text, evidence.highlight_type) for evidence in date_diff.compare_evidence] == [("21", "ADD")]
    assert date_diff.compare_evidence[0].bbox.x0 > 330
    assert date_diff.compare_evidence[0].bbox.y0 >= 635


def test_cover_metadata_reports_original_only_extra_cover_text() -> None:
    original = cover_document("XX-C-260520", "2026年4月21日")
    original.pages[0].blocks.insert(
        1,
        TextBlock(
            block_id="p1_extra_no",
            page_no=1,
            text="GxN-26042-00044",
            bbox=BBox(x0=392, y0=7, x1=543, y1=34.5),
            block_type="text",
        ),
    )
    original.pages[0].blocks.insert(
        2,
        TextBlock(
            block_id="p1_extra_char",
            page_no=1,
            text="永",
            bbox=BBox(x0=496.5, y0=38, x1=528.5, y1=71.5),
            block_type="text",
        ),
    )
    compare = cover_document("XX-C-260520", "2026年4月21日")

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    extra_diffs = [diff for diff in diffs if diff.title == "封面额外文本"]
    assert [(diff.diff_type, diff.original_text) for diff in extra_diffs] == [
        ("DELETE", "GxN-26042-00044"),
        ("DELETE", "永"),
    ]
    assert all(diff.original_evidence[0].method == "cover_extra" for diff in extra_diffs)
    assert all(diff.original_evidence[0].highlight_type == "DELETE" for diff in extra_diffs)


def test_cover_metadata_matches_split_account_extra_text() -> None:
    original = cover_document("XX-C-260520", "2026年4月21日")
    original.pages[0].blocks.append(
        TextBlock(
            block_id="p1_original_account",
            page_no=1,
            text="账号：025900108810100",
            bbox=BBox(x0=90, y0=710, x1=250, y1=730),
            block_type="text",
        )
    )
    compare = cover_document("XX-C-260520", "2026年4月21日")
    compare.pages[0].blocks.extend(
        [
            TextBlock(
                block_id="p1_compare_account_label",
                page_no=1,
                text="账号：",
                bbox=BBox(x0=95, y0=710, x1=148, y1=730),
                block_type="text",
                layout_block_id="p1_account_row",
            ),
            TextBlock(
                block_id="p1_compare_account_value",
                page_no=1,
                text="025900108810100",
                bbox=BBox(x0=141, y0=709, x1=252, y1=731),
                block_type="text",
                layout_block_id="p1_account_row",
            ),
        ]
    )

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    account_diffs = [
        diff for diff in diffs
        if diff.title == "封面额外文本" and "账号" in (diff.original_text + diff.compare_text)
    ]
    assert account_diffs == []


def test_cover_metadata_extra_normalizer_preserves_long_numbers() -> None:
    comparator = CoverMetadataComparator()

    assert comparator._normalize_extra("025900108810100") == "025900108810100"
    assert comparator._normalize_extra("账号：025900108810100") == comparator._normalize_extra("账号:025900108810100")


def test_cover_metadata_ignores_short_edge_cover_noise() -> None:
    original = cover_document("XX-C-260520", "2026年4月21日")
    original.pages[0].blocks.extend(
        [
            TextBlock(
                block_id="p1_edge_noise_word",
                page_no=1,
                text="城日限",
                bbox=BBox(x0=566, y0=330, x1=597, y1=402.5),
                block_type="text",
            ),
            TextBlock(
                block_id="p1_edge_noise_char",
                page_no=1,
                text="国",
                bbox=BBox(x0=577.5, y0=393, x1=597, y1=414.5),
                block_type="text",
            ),
        ]
    )
    compare = cover_document("XX-C-260520", "2026年4月21日")

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    assert not any(diff.title == "封面额外文本" for diff in diffs)


def test_cover_metadata_treats_split_tax_number_as_field() -> None:
    original = cover_document("XX-C-260520", "2026年4月21日")
    original.pages[0].blocks.append(
        TextBlock(
            block_id="p1_tax",
            page_no=1,
            text="税号:91320000720580314W",
            bbox=BBox(x0=140, y0=700, x1=380, y1=720),
            block_type="text",
        )
    )
    compare = cover_document("XX-C-260520", "2026年4月21日")
    compare.pages[0].blocks.extend(
        [
            TextBlock(
                block_id="p1_tax_label",
                page_no=1,
                text="税号:",
                bbox=BBox(x0=140, y0=700, x1=190, y1=720),
                block_type="text",
            ),
            TextBlock(
                block_id="p1_tax_value",
                page_no=1,
                text="91320000720580314W",
                bbox=BBox(x0=200, y0=700, x1=380, y1=720),
                block_type="text",
            ),
        ]
    )

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    assert not any(diff.title in {"封面字段：税号", "封面额外文本"} for diff in diffs)


def test_clause_matcher_matches_by_clause_number() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_b1",
                        page_no=1,
                        text="1. Payment\nBuyer shall pay within 30 days.",
                        bbox=BBox(x0=10, y0=10, x1=500, y1=80),
                    )
                ],
            )
        ],
    )
    splitter = ClauseSplitter()
    left = splitter.split(document, "O")
    right = splitter.split(document, "N")
    pairs = ClauseMatcher().match(left, right)
    assert pairs[0].match_method == "same_clause_no_weighted"
    assert pairs[0].score == 100
    assert pairs[0].score_details["clause_no_score"] == 100


def test_clause_matcher_flags_same_clause_number_low_similarity() -> None:
    left = [
        Clause(
            clause_id="O001",
            clause_no="1",
            title="付款",
            text="1. 付款\n买方应在30日内支付全部货款。",
            normalized_text="付款买方应在30日内支付全部货款",
        )
    ]
    right = [
        Clause(
            clause_id="N001",
            clause_no="1",
            title="保密",
            text="1. 保密\n双方应对所有技术资料承担保密义务。",
            normalized_text="保密双方应对所有技术资料承担保密义务",
        )
    ]

    pairs = ClauseMatcher().match(left, right)
    diff = DiffEngine().build_diffs(pairs)[0]

    assert pairs[0].match_method == "same_clause_no_low_similarity"
    assert "SAME_CLAUSE_NO_LOW_SIMILARITY" in diff.review_flags
    assert diff.match_score_details["clause_no_score"] == 100


def test_clause_matcher_detects_renumbered_clause_by_body_similarity() -> None:
    left = [
        Clause(
            clause_id="O001",
            clause_no="1",
            title="付款",
            text="1. 付款\n买方应在30日内支付全部货款。",
            normalized_text="付款买方应在30日内支付全部货款",
        )
    ]
    right = [
        Clause(
            clause_id="N001",
            clause_no="2",
            title="付款",
            text="2. 付款\n买方应在45日内支付全部货款。",
            normalized_text="付款买方应在45日内支付全部货款",
        )
    ]

    pairs = ClauseMatcher().match(left, right)

    assert pairs[0].match_method == "renumbered_similarity"
    assert pairs[0].match_candidates[0]["compare_clause_id"] == "N001"


def test_clause_matcher_reconciles_numbered_compare_clause_inside_original_parent() -> None:
    shared_intro = "14.2其他约定\n附件一技术服务条款"
    original_parent_text = (
        f"{shared_intro}\n"
        "出支\n"
        "系统开放性与可配置性要求\n"
        "预测文件上报接口开放\n"
        "乙方须提供功率预测系统完整的文件上报接口，包括但不限于：中短期预测文件、超短期预测文件、\n"
        "理论可用功率文件、测风塔/光伏气象文件的上报方式、上报路径、文件命名规则、字段结构、文件格式\n"
        "规范（含字段定义、编码标准、时间戳格式）。接口文档须以书面形式交付甲方，并确保甲方技术人员\n"
        "可独立完成对接配置。供方系统须具备充分的开放性、透明性与可对接性，支持与需方相关业务系统进\n"
        "行双向数据互通，确保上下游数据可自由流转、无缝交互。"
    )
    compare_body_text = (
        "1.预测文件上报接口开放\n"
        "乙方须提供功率预测系统完整的文件上报接口，包括但不限于：中短期预测文件、超短期预测文件、\n"
        "理论可用功率文件、测风塔/光伏气象文件的上报方式、上报路径、文件命名规则、字段结构、文件格式\n"
        "规范（含字段定义、编码标准、时间截格式）。接口文档须以书面形式交付甲方，并确保甲方技术人员\n"
        "可独立完成对接配置。供方系统须具备充分的开放性、透明性与可对接性，支持与需方相关业务系统进\n"
        "行双向数据互通，确保上下游数据可自由流转、无缝交互。"
    )
    original = [
        clause_with_boxes("O060", original_parent_text).model_copy(
            update={"clause_no": "14.2", "title": "其他约定", "normalized_text": original_parent_text}
        )
    ]
    compare = [
        clause_with_boxes("N060", shared_intro).model_copy(
            update={"clause_no": "14.2", "title": "其他约定", "normalized_text": shared_intro}
        ),
        clause_with_boxes("N061", "一、系统开放性与可配置性要求").model_copy(
            update={"clause_no": "一", "title": "系统开放性与可配置性要求"}
        ),
        clause_with_boxes("N062", compare_body_text).model_copy(
            update={"clause_no": "1", "title": "预测文件上报接口开放"}
        ),
    ]

    pairs = ClauseMatcher().match(original, compare)
    diffs = DiffEngine().build_diffs(pairs)

    assert not any(diff.diff_type == "ADD" and diff.compare_clause_id in {"N061", "N062"} for diff in diffs)

    title_diff = next(diff for diff in diffs if diff.compare_clause_id == "N061")
    assert title_diff.diff_type == "MODIFY"
    assert title_diff.original_change_ranges == []
    assert [title_diff.compare_text[r.start:r.end] for r in title_diff.compare_change_ranges] == ["一、"]

    body_diff = next(diff for diff in diffs if diff.compare_clause_id == "N062")
    original_fragments = [body_diff.original_text[r.start:r.end] for r in body_diff.original_change_ranges]
    compare_fragments = [body_diff.compare_text[r.start:r.end] for r in body_diff.compare_change_ranges]

    assert original_fragments == ["戳"]
    assert compare_fragments == ["1.", "截"]
    assert [r.highlight_type for r in body_diff.compare_change_ranges] == ["ADD", "MODIFY"]
    for unchanged in ["上报路径、", "文件命名规则、", "字段结构、", "文件格式", "规范", "编码标准", "无缝交互。"]:
        assert unchanged not in "".join(compare_fragments)


def test_diff_engine_reports_character_level_ranges() -> None:
    left = Clause(
        clause_id="O001",
        text="数量：10000",
        normalized_text="数量10000",
    )
    right = Clause(
        clause_id="N001",
        text="数量：200",
        normalized_text="数量200",
    )

    diff = DiffEngine().build_diffs([ClausePair(original=left, compare=right)])[0]

    assert diff.original_snippet == "10000"
    assert diff.compare_snippet == "200"
    assert [(item.start, item.end) for item in diff.original_change_ranges] == [(3, 8)]
    assert [(item.start, item.end) for item in diff.compare_change_ranges] == [(3, 6)]
    assert [item.highlight_type for item in diff.original_change_ranges] == ["MODIFY"]
    assert [item.highlight_type for item in diff.compare_change_ranges] == ["MODIFY"]


def test_diff_engine_reports_percent_to_per_mille_as_modify_with_number_context() -> None:
    left = Clause(
        clause_id="O001",
        text="物价款的3%作为违约金",
        normalized_text="物价款的3%作为违约金",
    )
    right = Clause(
        clause_id="N001",
        text="物价款的3‰作为违约金",
        normalized_text="物价款的3‰作为违约金",
    )

    diff = DiffEngine().build_diffs([ClausePair(original=left, compare=right)])[0]

    assert diff.original_snippet == "3%"
    assert diff.compare_snippet == "3‰"
    assert [left.text[item.start:item.end] for item in diff.original_change_ranges] == ["3%"]
    assert [right.text[item.start:item.end] for item in diff.compare_change_ranges] == ["3‰"]
    assert [item.highlight_type for item in diff.original_change_ranges] == ["MODIFY"]
    assert [item.highlight_type for item in diff.compare_change_ranges] == ["MODIFY"]


def test_diff_engine_marks_added_colon_without_deleting_shared_date_label() -> None:
    diff = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(clause_id="O001", text="日期", normalized_text="日期"),
                compare=Clause(clause_id="N001", text="日期:", normalized_text="日期:"),
            )
        ]
    )[0]

    assert diff.original_change_ranges == []
    assert [diff.compare_text[item.start:item.end] for item in diff.compare_change_ranges] == [":"]
    assert [item.highlight_type for item in diff.compare_change_ranges] == ["ADD"]


def test_diff_engine_refines_repeated_date_label_in_large_modify_hunk() -> None:
    left_text = "法人\n法人代表或:\n日期:2026.\n日期\n附件一技术服务条款\n出支"
    right_text = "法人代表或授权委托人:\n法人代表或授权委托人:\n(签字)\n(签字)\n日期:\n日期:\n附件一技术服务条款"

    diff = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(clause_id="O001", text=left_text, normalized_text="left"),
                compare=Clause(clause_id="N001", text=right_text, normalized_text="right"),
            )
        ]
    )[0]

    original_fragments = [diff.original_text[item.start:item.end] for item in diff.original_change_ranges]
    compare_fragments = [diff.compare_text[item.start:item.end] for item in diff.compare_change_ranges]

    assert not any(fragment == "日期" for fragment in original_fragments)
    assert "2026." in original_fragments
    assert "出支" in original_fragments
    assert ":" in compare_fragments


def test_diff_engine_classifies_insert_and_delete_ranges() -> None:
    inserted = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(clause_id="O001", text="付款方式：现金", normalized_text="付款方式现金"),
                compare=Clause(clause_id="N001", text="付款方式：现金或转账", normalized_text="付款方式现金或转账"),
            )
        ]
    )[0]
    deleted = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(clause_id="O001", text="付款方式：现金或转账", normalized_text="付款方式现金或转账"),
                compare=Clause(clause_id="N001", text="付款方式：现金", normalized_text="付款方式现金"),
            )
        ]
    )[0]

    assert inserted.original_change_ranges == []
    assert [item.highlight_type for item in inserted.compare_change_ranges] == ["ADD"]
    assert inserted.compare_snippet == "或转账"
    assert [item.highlight_type for item in deleted.original_change_ranges] == ["DELETE"]
    assert deleted.compare_change_ranges == []
    assert deleted.original_snippet == "或转账"


def test_diff_engine_ignores_newline_only_difference() -> None:
    left = Clause(
        clause_id="O001",
        text="广核射阳湖光伏\n扬州市铜山区A项目",
        normalized_text="广核射阳湖光伏扬州市铜山区a项目",
    )
    right = Clause(
        clause_id="N001",
        text="广核射阳湖光伏扬州市铜山区B项目",
        normalized_text="广核射阳湖光伏扬州市铜山区b项目",
    )
    diff = DiffEngine().build_diffs([ClausePair(original=left, compare=right)])[0]

    for r in diff.original_change_ranges:
        snippet = left.text[r.start : r.end]
        assert snippet.strip(), f"Whitespace-only range should be filtered: {repr(snippet)}"


def test_diff_engine_does_not_over_expand_for_space_insertion() -> None:
    left = Clause(
        clause_id="O001",
        text="9.2适用范围甲方",
        normalized_text="9.2适用范围甲方",
    )
    right = Clause(
        clause_id="N001",
        text="9.2 适用范围乙方",
        normalized_text="9.2适用范围乙方",
    )
    diff = DiffEngine().build_diffs([ClausePair(original=left, compare=right)])[0]

    for r in diff.compare_change_ranges:
        snippet = right.text[r.start : r.end]
        assert "9.2" not in snippet or "乙方" not in snippet, (
            f"Range over-expanded, should not include '9.2': {repr(snippet)}"
        )


def test_diff_engine_skips_whitespace_only_ranges() -> None:
    left = Clause(
        clause_id="O001",
        text="数量：100\n金额：200元",
        normalized_text="数量100金额200元",
    )
    right = Clause(
        clause_id="N001",
        text="数量：100 金额：200万元",
        normalized_text="数量100金额200万元",
    )
    diff = DiffEngine().build_diffs([ClausePair(original=left, compare=right)])[0]

    for r in diff.original_change_ranges:
        snippet = left.text[r.start : r.end]
        assert snippet.strip(), f"Whitespace-only range should be filtered: {repr(snippet)}"


def test_diff_engine_ignores_line_wrap_movement_in_contract_paragraph() -> None:
    left_text = (
        "上述价格为含13%增值税价格,总金额包括光伏功率预测系统V2.0软件部分价格,支\n"
        "持该系统所需的硬件设备价格。该价格为固定不变价,包括设备及随机附件的设计、采购、\n"
        "制造、税类(包含关税)、包装、运输、保险费用:还包含安装调试、技术服务(包含技术\n"
        "资料、图纸的提供)、质保期内服务的费用。"
    )
    right_text = (
        "上述价格为含13%增值税价格,总金额包括光伏功率预测系统V2.0软件部分价格,\n"
        "支持该系统所需的硬件设备价格。该价格为固定不变价,包括设备及随机附件的设计、采\n"
        "购、制造、税类(包含关税)、包装、运输、保险费用;还包含调试、技术服务(包含技\n"
        "术资料、图纸的提供)、质保期内服务的费用。"
    )

    diff = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(clause_id="O001", text=left_text, normalized_text="left"),
                compare=Clause(clause_id="N001", text=right_text, normalized_text="right"),
            )
        ]
    )[0]

    original_snippets = [left_text[item.start : item.end] for item in diff.original_change_ranges]
    compare_snippets = [right_text[item.start : item.end] for item in diff.compare_change_ranges]

    assert "支" not in original_snippets
    assert "支" not in compare_snippets
    assert "购、" not in original_snippets
    assert "购、" not in compare_snippets
    assert "术" not in original_snippets
    assert "术" not in compare_snippets
    assert ":" in original_snippets
    assert ";" in compare_snippets
    assert "安装" in original_snippets
    assert "安装" not in compare_snippets


def test_evidence_locator_maps_ranges_to_character_boxes() -> None:
    text = "数量：10000"
    char_boxes = [
        CharBox(
            char=char,
            page_no=1,
            bbox=BBox(x0=10 + index * 8, y0=20, x1=16 + index * 8, y1=30),
            text_index=index,
        )
        for index, char in enumerate(text)
    ]
    clause = Clause(
        clause_id="O001",
        text=text,
        normalized_text="数量10000",
        char_boxes=char_boxes,
    )
    diff = DiffEngine().build_diffs(
        [
            ClausePair(
                original=clause,
                compare=Clause(clause_id="N001", text="数量：200", normalized_text="数量200"),
            )
        ]
    )[0]

    located = EvidenceLocator().locate([diff], [clause], [])[0]

    assert located.original_evidence[0].method == "char_exact"
    assert located.original_evidence[0].highlight_type == "MODIFY"
    assert located.original_evidence[0].text == "10000"
    assert located.original_evidence[0].bbox.x0 < char_boxes[3].bbox.x0
    assert located.original_evidence[0].bbox.x1 > char_boxes[-1].bbox.x1
    assert located.original_evidence[0].bbox.x1 - located.original_evidence[0].bbox.x0 < 50


def test_evidence_locator_splits_whitespace_and_large_gaps() -> None:
    text = "A B"
    positions = [(10, 16), (20, 24), (60, 66)]
    char_boxes = [
        CharBox(
            char=char,
            page_no=1,
            bbox=BBox(x0=x0, y0=20, x1=x1, y1=30),
            text_index=index,
        )
        for index, (char, (x0, x1)) in enumerate(zip(text, positions, strict=True))
    ]
    clause = Clause(
        clause_id="N001",
        text=text,
        normalized_text="AB",
        char_boxes=char_boxes,
    )
    diff = DiffEngine().build_diffs([ClausePair(original=None, compare=clause)])[0]

    located = EvidenceLocator().locate([diff], [], [clause])[0]

    assert [evidence.text for evidence in located.compare_evidence] == ["A", "B"]
    assert all(evidence.highlight_type == "ADD" for evidence in located.compare_evidence)
    assert all(evidence.bbox.x1 - evidence.bbox.x0 < 10 for evidence in located.compare_evidence)


def test_highlight_rule_add_only_marks_compare_side() -> None:
    clause = clause_with_boxes("N001", "新增条款")
    diff = DiffEngine().build_diffs([ClausePair(original=None, compare=clause)])[0]

    located = EvidenceLocator().locate([diff], [], [clause])[0]

    assert located.original_evidence == []
    assert located.compare_evidence
    assert {evidence.highlight_type for evidence in located.compare_evidence} == {"ADD"}


def test_highlight_rule_delete_only_marks_original_side() -> None:
    clause = clause_with_boxes("O001", "删除条款")
    diff = DiffEngine().build_diffs([ClausePair(original=clause, compare=None)])[0]

    located = EvidenceLocator().locate([diff], [clause], [])[0]

    assert located.original_evidence
    assert {evidence.highlight_type for evidence in located.original_evidence} == {"DELETE"}
    assert located.compare_evidence == []


def test_highlight_rule_modify_marks_both_sides_yellow() -> None:
    left = clause_with_boxes("O001", "数量：100")
    right = clause_with_boxes("N001", "数量：200")
    diff = DiffEngine().build_diffs([ClausePair(original=left, compare=right)])[0]

    located = EvidenceLocator().locate([diff], [left], [right])[0]

    assert located.original_evidence
    assert located.compare_evidence
    assert {evidence.highlight_type for evidence in located.original_evidence} == {"MODIFY"}
    assert {evidence.highlight_type for evidence in located.compare_evidence} == {"MODIFY"}


def test_highlight_rule_modify_does_not_mark_side_without_change_ranges() -> None:
    left = clause_with_boxes("O001", "成本价收心取维护费。")
    right = clause_with_boxes("N001", "成本价收取维护费。")
    diff = DiffEngine().build_diffs([ClausePair(original=left, compare=right)])[0]

    located = EvidenceLocator().locate([diff], [left], [right])[0]

    assert [evidence.text for evidence in located.original_evidence] == ["心"]
    assert located.compare_evidence == []


def test_table_cell_short_text_confidence_stays_high_without_fragment_flag() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=100, y0=100, x1=120, y1=120),
                method="table_cell",
                text="否",
                highlight_type="MODIFY",
            )
        ],
    )

    EvidenceLocator().assign_evidence_confidence([diff])

    assert diff.compare_evidence[0].confidence == 0.9
    assert diff.compare_evidence[0].evidence_quality == "HIGH"


def test_table_cell_confidence_is_low_for_possible_ocr_fragment() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        review_flags=["possible_ocr_fragment"],
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=100, y0=100, x1=120, y1=120),
                method="table_cell",
                text="机。",
                highlight_type="DELETE",
            )
        ],
    )

    EvidenceLocator().assign_evidence_confidence([diff])

    assert diff.original_evidence[0].confidence == 0.55
    assert diff.original_evidence[0].evidence_quality == "LOW"


def test_compare_side_add_wins_over_overlapping_modify_evidence() -> None:
    add = DiffItem(
        diff_id="D001",
        diff_type="ADD",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=100, y0=100, x1=150, y1=120),
                text="8,000.00",
                highlight_type="ADD",
            )
        ],
    )
    modify = DiffItem(
        diff_id="D002",
        diff_type="MODIFY",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=98, y0=98, x1=152, y1=122),
                text="8,000.00",
                highlight_type="MODIFY",
            )
        ],
    )

    locator = EvidenceLocator()
    locator._resolve_side_conflicts([add, modify], side="compare")

    assert add.compare_evidence
    assert add.compare_evidence[0].highlight_type == "ADD"
    assert modify.compare_evidence == []


def test_same_type_overlap_keeps_smaller_box() -> None:
    small = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=100, y0=100, x1=120, y1=120),
                text="文本",
                highlight_type="MODIFY",
            )
        ],
    )
    large = DiffItem(
        diff_id="D002",
        diff_type="MODIFY",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=95, y0=95, x1=130, y1=125),
                text="文本",
                highlight_type="MODIFY",
            )
        ],
    )

    locator = EvidenceLocator()
    locator._resolve_side_conflicts([large, small], side="compare")

    assert large.compare_evidence == []
    assert small.compare_evidence


def clause_with_boxes(clause_id: str, text: str) -> Clause:
    return Clause(
        clause_id=clause_id,
        text=text,
        normalized_text=text,
        char_boxes=[
            CharBox(
                char=char,
                page_no=1,
                bbox=BBox(x0=10 + index * 8, y0=20, x1=16 + index * 8, y1=30),
                text_index=index,
            )
            for index, char in enumerate(text)
        ],
    )


def cover_document(contract_no: str, sign_date: str) -> Document:
    contract_text = f"合同编号：{contract_no}" if contract_no else "合同编号："
    return Document(
        filename="cover.pdf",
        path="cover.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_no",
                        page_no=1,
                        text=contract_text,
                        bbox=BBox(x0=80, y0=60, x1=240, y1=80),
                    ),
                    TextBlock(
                        block_id="p1_title",
                        page_no=1,
                        text="江苏中广核\n4 套风电功率预测系统V1.0\n2 套光伏功率预测系统V2.0\n采购合同",
                        bbox=BBox(x0=150, y0=160, x1=480, y1=310),
                    ),
                    TextBlock(
                        block_id="p1_buyer",
                        page_no=1,
                        text="甲方\n江苏东大金智信息系统有限公司",
                        bbox=BBox(x0=140, y0=560, x1=440, y1=590),
                    ),
                    TextBlock(
                        block_id="p1_seller",
                        page_no=1,
                        text="乙方\n国能日新科技股份有限公司",
                        bbox=BBox(x0=140, y0=600, x1=420, y1=630),
                    ),
                    TextBlock(
                        block_id="p1_place",
                        page_no=1,
                        text="签订地点\n北京",
                        bbox=BBox(x0=140, y0=630, x1=280, y1=655),
                    ),
                    TextBlock(
                        block_id="p1_date",
                        page_no=1,
                        text=f"签订日期\n{sign_date}",
                        bbox=BBox(x0=140, y0=660, x1=380, y1=690),
                    ),
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p2_body",
                        page_no=2,
                        text="正文",
                        bbox=BBox(x0=280, y0=80, x1=340, y1=110),
                    )
                ],
            ),
        ],
    )


def table_document(blocks: list[tuple[str, str, str]]) -> Document:
    return Document(
        filename="table.pdf",
        path="table.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id=block_id,
                        page_no=1,
                        text=text,
                        bbox=BBox(x0=60, y0=100 + index * 24, x1=520, y1=120 + index * 24),
                        block_type=block_type,
                    )
                    for index, (block_id, text, block_type) in enumerate(blocks)
                ],
            )
        ],
    )


# ---------------------------------------------------------------------------
# deduplicate_overlaps: DELETE vs MODIFY overlap
# ---------------------------------------------------------------------------


def test_deduplicate_overlaps_shrinks_delete_when_body_overlaps_modify_compare() -> None:
    """DELETE diff body text overlaps MODIFY diff's compare ADD evidence -> DELETE shrunk to prefix."""
    engine = DiffEngine()
    delete_diff = DiffItem(
        diff_id="D001",
        diff_type="DELETE",
        original_clause_id="OC006",
        title="交货地点及交货方式",
        original_text="六、交货地点及交货方式\n交货地点：广州\n联系电话：13826172988",
        original_snippet="六、交货地点及交货方式\n交货地点：广州\n联系电话：13826172988",
        readable_change="删除条款：六、交货地点及交货方式",
        original_evidence=[
            EvidenceBox(page_no=1, bbox=BBox(x0=80, y0=100, x1=500, y1=200),
                        method="clause_fallback", text="六、交货地点及交货方式", highlight_type="DELETE"),
            EvidenceBox(page_no=1, bbox=BBox(x0=80, y0=200, x1=500, y1=300),
                        method="clause_fallback", text="交货地点：广州\n联系电话：13826172988", highlight_type="DELETE"),
        ],
        original_change_ranges=[TextRange(start=0, end=44, highlight_type="DELETE")],
    )
    modify_diff = DiffItem(
        diff_id="D002",
        diff_type="MODIFY",
        original_clause_id="OC005",
        compare_clause_id="NC005",
        original_text="五、合同价格\n总价：100万元",
        compare_text="五、合同价格\n总价：100万元\n交货地点及交货方式\n交货地点：广州\n联系电话：13826172988",
        original_snippet="总价：100万元",
        compare_snippet="交货地点及交货方式\n交货地点：广州\n联系电话：13826172988",
        readable_change="原文：...\n修改后：交货地点及交货方式...",
        compare_evidence=[
            EvidenceBox(page_no=1, bbox=BBox(x0=80, y0=300, x1=500, y1=400),
                        method="clause_fallback", text="交货地点及交货方式\n交货地点：广州\n联系电话：13826172988", highlight_type="ADD"),
        ],
        compare_change_ranges=[TextRange(start=12, end=56, highlight_type="ADD")],
        original_change_ranges=[TextRange(start=6, end=14, highlight_type="MODIFY")],
    )

    result = engine.deduplicate_overlaps([delete_diff, modify_diff])

    delete_result = next(d for d in result if d.diff_id == "D001")
    assert delete_result.diff_type == "DELETE"
    assert "六、" in delete_result.original_snippet
    assert "交货地点" not in delete_result.original_snippet or len(delete_result.original_snippet) < 10

    modify_result = next(d for d in result if d.diff_id == "D002")
    assert not any("交货地点" in e.text and e.highlight_type == "ADD" for e in modify_result.compare_evidence)


def test_deduplicate_overlaps_shrinks_delete_title_only() -> None:
    """Short DELETE diff title overlaps MODIFY diff's compare evidence -> DELETE shrunk to title."""
    engine = DiffEngine()
    delete_diff = DiffItem(
        diff_id="D003",
        diff_type="DELETE",
        original_clause_id="OC009",
        title="系统开放性与可配置性要求",
        original_text="一、系统开放性与可配置性要求",
        original_snippet="一、系统开放性与可配置性要求",
        readable_change="删除条款：系统开放性与可配置性要求",
        original_evidence=[
            EvidenceBox(page_no=1, bbox=BBox(x0=80, y0=100, x1=500, y1=140),
                        method="clause_fallback", text="一、系统开放性与可配置性要求", highlight_type="DELETE"),
        ],
        original_change_ranges=[TextRange(start=0, end=14, highlight_type="DELETE")],
    )
    modify_diff = DiffItem(
        diff_id="D004",
        diff_type="MODIFY",
        original_clause_id="OC007",
        compare_clause_id="NC006",
        original_text="七、其他条款\n内容A",
        compare_text="七、其他条款\n系统开放性与可配置性要求\n内容B",
        original_snippet="内容A",
        compare_snippet="系统开放性与可配置性要求",
        compare_evidence=[
            EvidenceBox(page_no=1, bbox=BBox(x0=80, y0=200, x1=500, y1=240),
                        method="clause_fallback", text="系统开放性与可配置性要求", highlight_type="MODIFY"),
        ],
        compare_change_ranges=[TextRange(start=6, end=20, highlight_type="MODIFY")],
        original_change_ranges=[TextRange(start=6, end=10, highlight_type="MODIFY")],
    )

    result = engine.deduplicate_overlaps([delete_diff, modify_diff])

    delete_result = next(d for d in result if d.diff_id == "D003")
    assert delete_result.diff_type == "DELETE"
    assert "缺失编号" in delete_result.readable_change


def test_deduplicate_overlaps_no_overlap_returns_unchanged() -> None:
    """No overlap between DELETE and MODIFY -> diffs unchanged."""
    engine = DiffEngine()
    delete_diff = DiffItem(
        diff_id="D005",
        diff_type="DELETE",
        original_clause_id="OC010",
        title="不相关条款",
        original_text="八、不相关条款\n完全不同的内容",
        original_snippet="八、不相关条款",
        original_evidence=[
            EvidenceBox(page_no=1, bbox=BBox(x0=80, y0=100, x1=500, y1=140),
                        method="clause_fallback", text="八、不相关条款", highlight_type="DELETE"),
        ],
        original_change_ranges=[TextRange(start=0, end=13, highlight_type="DELETE")],
    )
    modify_diff = DiffItem(
        diff_id="D006",
        diff_type="MODIFY",
        original_clause_id="OC005",
        compare_clause_id="NC005",
        original_text="五、合同价格\n100万元",
        compare_text="五、合同价格\n200万元",
        original_snippet="100万元",
        compare_snippet="200万元",
        compare_evidence=[
            EvidenceBox(page_no=1, bbox=BBox(x0=80, y0=200, x1=500, y1=240),
                        method="clause_fallback", text="200万元", highlight_type="MODIFY"),
        ],
        compare_change_ranges=[TextRange(start=6, end=12, highlight_type="MODIFY")],
        original_change_ranges=[TextRange(start=6, end=12, highlight_type="MODIFY")],
    )

    result = engine.deduplicate_overlaps([delete_diff, modify_diff])

    assert len(result) == 2
    delete_result = next(d for d in result if d.diff_id == "D005")
    assert delete_result.original_snippet == "八、不相关条款"
    modify_result = next(d for d in result if d.diff_id == "D006")
    assert any("200万元" in e.text for e in modify_result.compare_evidence)
