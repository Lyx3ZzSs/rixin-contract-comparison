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
                    ),
                    TextBlock(
                        block_id="p1_b3",
                        page_no=1,
                        text="端\nHY-EM300\n120 台\n1,230.00\n147,600.00",
                        bbox=BBox(x0=10, y0=70, x1=500, y1=100),
                    ),
                    TextBlock(
                        block_id="p1_b4",
                        page_no=1,
                        text="边缘网关\nHY-GW200\n20 台\n2,500.00\n50,000.00",
                        bbox=BBox(x0=10, y0=105, x1=500, y1=135),
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
                    ),
                    TextBlock(
                        block_id="p1_row",
                        page_no=1,
                        text="1\n预测服务器\n14020R 双电\nCPU:1*4 核;内存:\n16G;硬盘:2T SATA;\n航天联志\n台\n1\n8500\n8500",
                        bbox=BBox(x0=60, y0=220, x1=520, y1=320),
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


def test_table_comparator_skips_large_unreliable_table_reordering() -> None:
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
    assert warnings == ["表格抽取顺序差异较大，已跳过大范围表格字符级高亮。"]


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
    assert date_diff.original_evidence[0].highlight_type == "MODIFY"
    assert date_diff.compare_evidence[0].highlight_type == "MODIFY"


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
    assert pairs[0].match_method == "clause_no"
    assert pairs[0].score == 100


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
