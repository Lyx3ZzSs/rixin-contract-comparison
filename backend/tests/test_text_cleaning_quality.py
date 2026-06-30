from __future__ import annotations

from app.models import BBox, Clause, ClausePair, DiffItem, Document, EvidenceBox, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter
from app.services.diff_engine import DiffEngine
from app.services.diff_quality import DiffQualityProcessor
from app.services.document_preparation import DocumentPreparer
from app.services.normalizer import TextNormalizer


def test_normalize_for_diff_preserves_decimal_and_version_tokens() -> None:
    normalizer = TextNormalizer()

    assert normalizer.normalize_for_diff("违约金 1.5%") != normalizer.normalize_for_diff("违约金 15%")
    assert normalizer.normalize_for_diff("系统 V1.0") != normalizer.normalize_for_diff("系统 V10")
    assert normalizer.normalize_for_diff("金额 1,000 元") == normalizer.normalize_for_diff("金额 1000 元")
    assert normalizer.normalize_for_diff("日期") == normalizer.normalize_for_diff("日期：")


def test_clause_splitter_sets_match_text_separately_from_diff_text() -> None:
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
                        block_id="b1",
                        page_no=1,
                        text="1. 违约金为 1.5%",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=110),
                    )
                ],
            )
        ],
    )

    clause = ClauseSplitter().split(document, "O")[0]

    assert "." in clause.normalized_text
    assert "." not in clause.match_text


def test_diff_engine_reports_decimal_and_version_changes() -> None:
    diffs = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(
                    clause_id="O001",
                    text="违约金为1.5%，系统V1.0",
                    normalized_text=TextNormalizer().normalize_for_diff("违约金为1.5%，系统V1.0"),
                ),
                compare=Clause(
                    clause_id="N001",
                    text="违约金为15%，系统V10",
                    normalized_text=TextNormalizer().normalize_for_diff("违约金为15%，系统V10"),
                ),
            )
        ]
    )

    assert len(diffs) == 1
    assert "1.5" in diffs[0].original_snippet
    assert "15%" in diffs[0].compare_text
    assert "V10" in diffs[0].compare_text


def test_document_preparation_keeps_signing_text_as_regular_clause_text() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="body",
                        page_no=1,
                        text="1. 正文条款",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=110),
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(block_id="sig1", page_no=2, text="签字页 此页无正文", bbox=BBox(x0=50, y0=80, x1=500, y1=110)),
                    TextBlock(block_id="sig2", page_no=2, text="甲方（盖章） 乙方（盖章） 日期", bbox=BBox(x0=50, y0=130, x1=500, y1=180)),
                ],
            ),
        ],
    )

    result = DocumentPreparer().prepare(document, "original")
    clauses = ClauseSplitter().split(document, "O")

    assert result.decisions == []
    assert len(clauses) == 1
    assert clauses[0].section_type == "main_contract"
    assert clauses[0].source_block_ids == ["body", "sig1", "sig2"]
    assert "签字页 此页无正文" in clauses[0].text
    assert "甲方(盖章) 乙方(盖章) 日期" in clauses[0].text


def test_document_preparation_keeps_real_clause_with_signing_terms_in_main_contract() -> None:
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
                        block_id="body",
                        page_no=1,
                        text="13.1本合同经双方盖章后生效，合同中未尽事宜由双方协商解决。",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=110),
                    ),
                    TextBlock(
                        block_id="next",
                        page_no=1,
                        text="13.2对本合同的修改以双方签章的书面协议为准。",
                        bbox=BBox(x0=50, y0=120, x1=500, y1=150),
                    ),
                ],
            )
        ],
    )

    DocumentPreparer().prepare(document, "original")
    clauses = ClauseSplitter().split(document, "O")

    assert [clause.section_type for clause in clauses] == ["main_contract", "main_contract"]


def test_document_preparation_does_not_split_party_line_as_special_section() -> None:
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
                        block_id="c1",
                        page_no=1,
                        text="14.1本合同正本一式肆份，双方各执贰份。",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=110),
                    ),
                    TextBlock(
                        block_id="c2",
                        page_no=1,
                        text="14.2补充采购应签订书面补充协议。",
                        bbox=BBox(x0=50, y0=120, x1=500, y1=150),
                    ),
                    TextBlock(
                        block_id="sig-party",
                        page_no=1,
                        text="甲方：江苏东大金智信息系统有限公司乙方：国能日新科技股份有限公司",
                        bbox=BBox(x0=50, y0=180, x1=500, y1=210),
                    ),
                    TextBlock(
                        block_id="sig-date",
                        page_no=1,
                        text="日期：",
                        bbox=BBox(x0=50, y0=220, x1=120, y1=250),
                    ),
                ],
            )
        ],
    )

    DocumentPreparer().prepare(document, "original")
    clauses = ClauseSplitter().split(document, "O")

    assert all(clause.section_type == "main_contract" for clause in clauses)
    assert clauses[1].source_block_ids == ["c2", "sig-party", "sig-date"]


def test_clause_splitter_does_not_treat_amount_range_as_clause_number() -> None:
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
                        block_id="body",
                        page_no=1,
                        text="11.2.4 未经审批擅自作业,每次考核。\n1000~5000元,拒不整改加倍处罚并清退人员。",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=140),
                    )
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["11.2.4"]
    assert "1000~5000元" in clauses[0].text


def test_clause_splitter_keeps_deep_decimal_clause_number() -> None:
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
                        block_id="deep",
                        page_no=1,
                        text="1.2.3.4.5 五级标题",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=110),
                    ),
                    TextBlock(
                        block_id="body",
                        page_no=1,
                        text="五级正文内容。",
                        bbox=BBox(x0=70, y0=120, x1=500, y1=150),
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["1.2.3.4.5"]
    assert clauses[0].title == "五级标题"
    assert "n1_2_3_4_5" in clauses[0].clause_key
    assert "五级正文内容" in clauses[0].text


def test_clause_splitter_writes_stable_clause_alignment_key() -> None:
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
                        block_id="payment",
                        page_no=1,
                        text="3.1 付款 条款：甲方应在 2026 年 06 月 30 日前支付人民币 1,000.00 元。",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=110),
                    )
                ],
            )
        ],
    )

    clause = ClauseSplitter().split(document, "O")[0]

    assert clause.clause_key.startswith("main_contract/")
    assert "n3_1" in clause.clause_key
    assert "amount:1000.00" in clause.clause_key
    assert "date:2026-06-30" in clause.clause_key

    article_document = Document(
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
                        block_id="payment-article",
                        page_no=1,
                        text="第3.1条 付款 条款：甲方应在 2026 年 06 月 30 日前支付人民币 1,000.00 元。",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=110),
                    )
                ],
            )
        ],
    )

    article_clause = ClauseSplitter().split(article_document, "O")[0]

    assert "main_contract/n3_1" in clause.clause_key
    assert "main_contract/n3_1" in article_clause.clause_key
    assert "第31条" not in article_clause.clause_key

    for token in ("n3_1", "amount:1000.00", "date:2026-06-30"):
        assert token in article_clause.clause_key
        assert token in clause.clause_key


def test_clause_splitter_keeps_single_numeric_inline_body_clause_number() -> None:
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
                        block_id="body",
                        page_no=1,
                        text="1. 服务范围 甲方提供服务。2. 付款方式 乙方付款。",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=120),
                    )
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["1", "2"]
    assert "服务范围" in clauses[0].title
    assert "付款方式" in clauses[1].title


def test_clause_splitter_merges_ocr_split_paragraph_with_evidence() -> None:
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
                    TextBlock(block_id="h1", page_no=1, text="1. 服务范围", bbox=BBox(x0=50, y0=80, x1=500, y1=110)),
                    TextBlock(block_id="p1", page_no=1, text="甲方提供风功率预测服务", bbox=BBox(x0=70, y0=116, x1=500, y1=146)),
                    TextBlock(block_id="p2", page_no=1, text="并负责系统日常维护。", bbox=BBox(x0=70, y0=152, x1=500, y1=182)),
                    TextBlock(block_id="h2", page_no=1, text="2. 付款", bbox=BBox(x0=50, y0=220, x1=500, y1=250)),
                    TextBlock(block_id="p3", page_no=1, text="乙方按月付款。", bbox=BBox(x0=70, y0=256, x1=500, y1=286)),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["1", "2"]
    assert clauses[0].source_block_ids == ["h1", "p1", "p2"]
    assert clauses[0].page_numbers == [1]
    assert len(clauses[0].bboxes) == 3
    assert "PARAGRAPH_MERGED" in clauses[0].split_flags
    assert "2. 付款" not in clauses[0].text
    assert clauses[1].source_block_ids == ["h2", "p3"]


def test_clause_splitter_merges_bare_number_with_following_heading() -> None:
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
                    TextBlock(block_id="number", page_no=1, text="1.", bbox=BBox(x0=50, y0=80, x1=72, y1=110)),
                    TextBlock(block_id="title", page_no=1, text="服务范围", bbox=BBox(x0=80, y0=80, x1=180, y1=110)),
                    TextBlock(block_id="body", page_no=1, text="甲方提供服务。", bbox=BBox(x0=70, y0=120, x1=500, y1=150)),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 1
    assert clauses[0].clause_no == "1"
    assert clauses[0].title == "服务范围"
    assert clauses[0].source_block_ids == ["number", "title", "body"]


def test_clause_splitter_filters_toc_dot_leaders_from_body_clauses() -> None:
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
                    TextBlock(block_id="t0", page_no=1, text="目录", bbox=BBox(x0=50, y0=60, x1=200, y1=80)),
                    TextBlock(block_id="t1", page_no=1, text="1. 技术服务项目概要......3", bbox=BBox(x0=50, y0=90, x1=500, y1=110)),
                    TextBlock(block_id="t2", page_no=1, text="2. 技术服务具体要求..3", bbox=BBox(x0=50, y0=120, x1=500, y1=140)),
                    TextBlock(block_id="t3", page_no=1, text="12.1/。", bbox=BBox(x0=50, y0=150, x1=500, y1=170)),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert clauses == []


def test_clause_splitter_merges_decimal_amount_continuation_into_previous_clause() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1",
                        page_no=1,
                        text="5.1 技术服务报酬总额为人民币341850.00元，其中增值税税率6%，增值税税额",
                        bbox=BBox(x0=50, y0=760, x1=500, y1=790),
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p2",
                        page_no=2,
                        text="19350.00元。当合同约定的税率与国家税法规定不一致时，以国家税法规定为准。",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=110),
                    )
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["5.1"]
    assert "19350.00元" in clauses[0].text


def test_clause_splitter_merges_cross_page_clause_continuation_with_evidence() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1-heading",
                        page_no=1,
                        text="5.2 服务期限",
                        bbox=BBox(x0=50, y0=720, x1=500, y1=748),
                    ),
                    TextBlock(
                        block_id="p1-body",
                        page_no=1,
                        text="乙方应在收到甲方书面通知后提供连续运维服务",
                        bbox=BBox(x0=70, y0=760, x1=520, y1=790),
                    ),
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p2-body",
                        page_no=2,
                        text="并按照本合同约定提交服务报告。",
                        bbox=BBox(x0=70, y0=72, x1=520, y1=102),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["5.2"]
    assert "连续运维服务" in clauses[0].text
    assert "提交服务报告" in clauses[0].text
    assert clauses[0].page_numbers == [1, 2]
    assert clauses[0].source_block_ids == ["p1-heading", "p1-body", "p2-body"]
    assert "PARAGRAPH_MERGED" in clauses[0].split_flags
    assert "CROSS_PAGE_CONTINUATION_MERGED" in clauses[0].split_flags
    assert [box.page_no for box in clauses[0].bboxes] == [1, 1, 2]


def test_clause_splitter_does_not_merge_cross_page_explicit_new_clause() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1",
                        page_no=1,
                        text="5.2 乙方应持续提供服务",
                        bbox=BBox(x0=50, y0=760, x1=520, y1=790),
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p2",
                        page_no=2,
                        text="第六条 违约责任",
                        bbox=BBox(x0=50, y0=72, x1=520, y1=102),
                    )
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["5.2", "第六条"]
    assert "违约责任" not in clauses[0].text
    assert "CROSS_PAGE_CONTINUATION_MERGED" not in clauses[0].split_flags


def test_clause_splitter_does_not_merge_non_adjacent_page_bare_marker_continuation() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=3,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1-marker",
                        page_no=1,
                        text="5.3",
                        bbox=BBox(x0=50, y0=760, x1=120, y1=790),
                    )
                ],
            ),
            Page(page_no=2, width=595, height=842, blocks=[]),
            Page(
                page_no=3,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p3-body",
                        page_no=3,
                        text="乙方应持续提供服务。",
                        bbox=BBox(x0=70, y0=72, x1=520, y1=102),
                    )
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 2
    assert clauses[0].page_numbers == [1]
    assert clauses[1].page_numbers == [3]
    assert all("CROSS_PAGE_CONTINUATION_MERGED" not in clause.split_flags for clause in clauses)


def test_clause_splitter_does_not_merge_cross_page_standalone_title() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1",
                        page_no=1,
                        text="5.2 乙方应持续提供服务",
                        bbox=BBox(x0=50, y0=760, x1=520, y1=790),
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p2-title",
                        page_no=2,
                        text="违约责任",
                        bbox=BBox(x0=50, y0=72, x1=180, y1=102),
                    ),
                    TextBlock(
                        block_id="p2-body",
                        page_no=2,
                        text="任何一方违约均应承担赔偿责任。",
                        bbox=BBox(x0=70, y0=116, x1=520, y1=146),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 2
    assert "违约责任" not in clauses[0].text
    assert "CROSS_PAGE_CONTINUATION_MERGED" not in clauses[0].split_flags


def test_clause_splitter_does_not_merge_cross_page_short_standalone_title() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1",
                        page_no=1,
                        text="5.2 乙方应持续提供服务",
                        bbox=BBox(x0=50, y0=760, x1=520, y1=790),
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p2-title",
                        page_no=2,
                        text="付款",
                        bbox=BBox(x0=50, y0=72, x1=120, y1=102),
                    ),
                    TextBlock(
                        block_id="p2-body",
                        page_no=2,
                        text="乙方应按月付款。",
                        bbox=BBox(x0=70, y0=116, x1=520, y1=146),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 2
    assert "付款" not in clauses[0].text
    assert clauses[1].source_block_ids == ["p2-title", "p2-body"]
    assert "CROSS_PAGE_CONTINUATION_MERGED" not in clauses[0].split_flags


def test_clause_splitter_merges_cross_page_amount_value_continuation() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1",
                        page_no=1,
                        text="5.3 合同总价为人民币",
                        bbox=BBox(x0=50, y0=760, x1=520, y1=790),
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p2",
                        page_no=2,
                        text="100000元整，包含税费及安装调试费用。",
                        bbox=BBox(x0=70, y0=72, x1=520, y1=102),
                    )
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["5.3"]
    assert "100000元整" in clauses[0].text
    assert clauses[0].page_numbers == [1, 2]
    assert "CROSS_PAGE_CONTINUATION_MERGED" in clauses[0].split_flags


def test_clause_splitter_does_not_merge_cross_page_no正文_signing_boundary() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1",
                        page_no=1,
                        text="5.4 本合同附件与正文具有同等法律效力",
                        bbox=BBox(x0=50, y0=760, x1=520, y1=790),
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="sign-none",
                        page_no=2,
                        text="以下无正文",
                        bbox=BBox(x0=50, y0=72, x1=220, y1=102),
                    ),
                    TextBlock(
                        block_id="sign-party",
                        page_no=2,
                        text="甲方（盖章）：",
                        bbox=BBox(x0=50, y0=130, x1=220, y1=160),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) >= 2
    assert "以下无正文" not in clauses[0].text
    assert "CROSS_PAGE_CONTINUATION_MERGED" not in clauses[0].split_flags


def test_clause_splitter_does_not_merge_cross_page_signing_page_variant() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1",
                        page_no=1,
                        text="5.4 本合同附件与正文具有同等法律效力",
                        bbox=BBox(x0=50, y0=760, x1=520, y1=790),
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="sign-title",
                        page_no=2,
                        text="签字页（此页无正文）",
                        bbox=BBox(x0=50, y0=72, x1=260, y1=102),
                    ),
                    TextBlock(
                        block_id="sign-party",
                        page_no=2,
                        text="甲方（盖章）：",
                        bbox=BBox(x0=50, y0=130, x1=220, y1=160),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) >= 2
    assert "签字页" not in clauses[0].text
    assert "CROSS_PAGE_CONTINUATION_MERGED" not in clauses[0].split_flags


def test_clause_splitter_builds_hierarchy_path_from_heading_levels() -> None:
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
                    TextBlock(block_id="chapter", page_no=1, text="第一章 总则", bbox=BBox(x0=50, y0=60, x1=500, y1=90)),
                    TextBlock(block_id="article", page_no=1, text="第一条 服务范围", bbox=BBox(x0=50, y0=100, x1=500, y1=130)),
                    TextBlock(block_id="sub", page_no=1, text="1.1 平台维护服务", bbox=BBox(x0=50, y0=140, x1=500, y1=170)),
                    TextBlock(block_id="body", page_no=1, text="乙方负责平台日常维护。", bbox=BBox(x0=70, y0=180, x1=500, y1=210)),
                    TextBlock(block_id="next", page_no=1, text="第二条 付款方式", bbox=BBox(x0=50, y0=220, x1=500, y1=250)),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["第一章", "第一条", "1.1", "第二条"]
    assert clauses[2].section_path == ["第一条 服务范围", "1.1 平台维护服务"]
    assert "heading_score" in clauses[2].segmentation_reason
    assert "乙方负责平台日常维护" in clauses[2].text


def test_clause_splitter_treats_signing_page_text_as_main_contract() -> None:
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
                    TextBlock(block_id="title", page_no=1, text="签署页", bbox=BBox(x0=50, y0=60, x1=500, y1=90)),
                    TextBlock(
                        block_id="address",
                        page_no=1,
                        text="27号金隅智造工场N6",
                        bbox=BBox(x0=50, y0=100, x1=500, y1=130),
                    ),
                    TextBlock(
                        block_id="contact",
                        page_no=1,
                        text="1 联系人:刘玉良",
                        bbox=BBox(x0=50, y0=140, x1=500, y1=170),
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 3
    assert all(clause.section_type == "main_contract" for clause in clauses)
    assert any("联系人" in clause.text for clause in clauses)


def test_clause_splitter_keeps_signing_numeric_address_as_main_contract_continuation() -> None:
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
                        block_id="sig-title",
                        page_no=1,
                        text="签署页",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=110),
                    ),
                    TextBlock(
                        block_id="sig-address",
                        page_no=1,
                        text="27号金隅智造工场N6",
                        bbox=BBox(x0=50, y0=130, x1=500, y1=160),
                    ),
                    TextBlock(
                        block_id="sig-contact",
                        page_no=1,
                        text="联系人:刘玉良",
                        bbox=BBox(x0=50, y0=170, x1=500, y1=200),
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 2
    assert all(clause.section_type == "main_contract" for clause in clauses)
    assert clauses[0].clause_no == ""
    assert "27号金隅智造工场N6" in clauses[1].text


def test_quote_section_is_split_from_main_contract_clause_flow() -> None:
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
                        block_id="body",
                        page_no=1,
                        text="1. 正文服务范围",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=110),
                    ),
                    TextBlock(
                        block_id="quote-title",
                        page_no=1,
                        text="报价表格式",
                        bbox=BBox(x0=50, y0=150, x1=500, y1=180),
                    ),
                    TextBlock(
                        block_id="quote-body",
                        page_no=1,
                        text="项目名称：风功率预测服务",
                        bbox=BBox(x0=50, y0=190, x1=500, y1=220),
                    ),
                ],
            )
        ],
    )

    DocumentPreparer().prepare(document, "compare")
    clauses = ClauseSplitter().split(document, "N")

    assert [clause.section_type for clause in clauses] == ["main_contract", "quote"]
    assert "SECTION_QUOTE" in clauses[1].split_flags


def test_diff_quality_flags_critical_changes_and_minor_ocr_noise() -> None:
    diffs = [
        DiffItem(
            diff_id="D001",
            diff_type="MODIFY",
            source_type="clause",
            original_snippet="1.5%",
            compare_snippet="15%",
            match_score=99,
        ),
        DiffItem(
            diff_id="D002",
            diff_type="MODIFY",
            source_type="clause",
            original_snippet="曰",
            compare_snippet="日",
            match_score=99,
        ),
    ]

    result = DiffQualityProcessor().process(diffs)
    by_id = {diff.diff_id: diff for diff in result.diffs}

    assert by_id["D001"].quality_status == "NORMAL"
    assert "CRITICAL_VALUE_CHANGE" in by_id["D001"].review_flags
    assert by_id["D002"].quality_status == "NEEDS_REVIEW"
    assert "POSSIBLE_OCR_NOISE" in by_id["D002"].review_flags


def test_diff_quality_suppresses_short_symbol_noise_without_business_tokens() -> None:
    diffs = [
        DiffItem(
            diff_id="D001",
            diff_type="MODIFY",
            source_type="clause",
            original_snippet="/",
            compare_snippet="∠",
            match_score=99,
        ),
        DiffItem(
            diff_id="D002",
            diff_type="MODIFY",
            source_type="clause",
            original_snippet="1.5%",
            compare_snippet="15%",
            match_score=99,
        ),
    ]

    result = DiffQualityProcessor().process(diffs)

    assert [diff.diff_id for diff in result.diffs] == ["D002"]
    assert any(decision.action == "suppressed_low_value_noise" and decision.diff_id == "D001" for decision in result.decisions)


def test_diff_quality_preserves_critical_field_change_from_low_value_suppression() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="clause",
        original_snippet="/",
        compare_snippet="∠",
        match_score=99,
        review_flags=["CRITICAL_FIELD_CHANGE", "CRITICAL_FIELD_AMOUNT_CHANGE"],
        match_score_details={
            "critical_field_diff_types": ["AMOUNT"],
            "critical_field_guard_applied": 1.0,
        },
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D001"]
    assert "CRITICAL_FIELD_CHANGE" in result.diffs[0].review_flags
    assert "CRITICAL_VALUE_CHANGE" in result.diffs[0].review_flags
    assert not any(decision.action == "suppressed_low_value_noise" for decision in result.decisions)


def test_diff_quality_keeps_ocr_review_status_on_critical_field_change() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="clause",
        original_snippet="1000元",
        compare_snippet="5000元",
        review_flags=[
            "CRITICAL_FIELD_CHANGE",
            "CRITICAL_FIELD_AMOUNT_CHANGE",
            "EVIDENCE_UNRELIABLE",
        ],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process([diff]).diffs[0]

    assert result.quality_status == "NEEDS_REVIEW"
    assert "CRITICAL_VALUE_CHANGE" in result.review_flags
    assert "EVIDENCE_UNRELIABLE" in result.review_flags


def test_diff_quality_preserves_ocr_marked_low_value_diff() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="clause",
        original_snippet="/",
        compare_snippet="∠",
        match_score=99,
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D001"]
    assert "EVIDENCE_UNRELIABLE" in result.diffs[0].review_flags
    assert result.diffs[0].quality_status == "NEEDS_REVIEW"


def test_diff_quality_suppresses_layout_reflow_punctuation_equivalent_clause_change() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="clause",
        original_text="4我方提供6%增值税专用发票,需方支付全部\n款项.",
        compare_text="4我方提供6%增值税专用发票,需方支\n付全部款项。",
        original_snippet="付全部款项.",
        compare_snippet="付全部款项。",
        match_score=100,
        match_score_details={
            "body_score": 100.0,
            "business_token_mismatch": 0.0,
        },
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D001"
        and decision.detail["reason"] == "layout_punctuation_equivalent"
        for decision in result.decisions
    )


def test_diff_quality_classifies_modify_by_changed_snippets_not_full_context() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="clause",
        original_text="甲方公司地址：北京。备注颜色：红色。",
        compare_text="甲方公司地址：北京。备注颜色：蓝色。",
        original_snippet="红色",
        compare_snippet="蓝色",
    )

    result = DiffQualityProcessor().process([diff]).diffs[0]

    assert "CRITICAL_VALUE_CHANGE" not in result.review_flags


def test_diff_quality_keeps_critical_flags_for_changed_business_terms() -> None:
    diffs = [
        DiffItem(
            diff_id="D001",
            diff_type="MODIFY",
            source_type="clause",
            original_snippet="甲方",
            compare_snippet="乙方",
        ),
        DiffItem(
            diff_id="D002",
            diff_type="ADD",
            source_type="clause",
            compare_text="新增违约责任：不得提前终止合同。",
        ),
    ]

    result = DiffQualityProcessor().process(diffs)
    by_id = {diff.diff_id: diff for diff in result.diffs}

    assert "CRITICAL_VALUE_CHANGE" in by_id["D001"].review_flags
    assert "CRITICAL_VALUE_CHANGE" in by_id["D002"].review_flags


def test_diff_quality_treats_party_contact_table_as_regular_table_change() -> None:
    diffs = [
        DiffItem(
            diff_id="D001",
            diff_type="DELETE",
            source_type="table",
            title="表格字段：联系人",
            original_snippet="单位名称（章）：国能日新科技股份有限公司 单位地址：北京市海淀区",
        ),
        DiffItem(
            diff_id="D002",
            diff_type="ADD",
            source_type="seal",
            title="印章区域（第1页）",
            compare_snippet="国能日新科技股份有限公司",
        ),
        DiffItem(
            diff_id="D003",
            diff_type="MODIFY",
            source_type="header_footer",
            title="页眉",
            original_snippet="合同编号A",
            compare_snippet="合同编号B",
        ),
    ]

    result = DiffQualityProcessor().process(diffs)
    by_id = {diff.diff_id: diff for diff in result.diffs}

    assert "CRITICAL_VALUE_CHANGE" in by_id["D001"].review_flags
    assert by_id["D001"].quality_status == "NORMAL"
    assert "CRITICAL_VALUE_CHANGE" not in by_id["D002"].review_flags
    assert "SEAL_REVIEW" in by_id["D002"].review_flags
    assert "D003" not in by_id


def test_diff_quality_marks_row_level_table_noise_for_review_not_critical() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="table",
        title="表格行：行3变更",
        original_text="3 | 工作站 | 2T SATA;网口 | HP/超云 | 台 | 1 | 4000 | 4000",
        compare_text="3 | 工作站 | 2T SATA：网口 | HP/超云 | 台 | 1 | 4000 | 4000",
        original_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=0, y0=0, x1=100, y1=20), method="table_row")],
        compare_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=0, y0=0, x1=100, y1=20), method="table_row")],
    )

    result = DiffQualityProcessor().process([diff])
    processed = result.diffs[0]

    assert processed.quality_status == "NEEDS_REVIEW"
    assert "ROW_LEVEL_TABLE_REVIEW" in processed.review_flags
    assert "CRITICAL_VALUE_CHANGE" not in processed.review_flags


def test_diff_quality_keeps_table_region_coverage_gap_as_review_not_critical() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="table",
        title="表格区域：大范围内容不一致",
        original_text="单位名称：国能日新科技股份有限公司 金额：100",
        compare_text="单位名称：斯美能源科技有限公司 金额：200",
        structural_flags=["table_region_coverage_gap", "large_flat_unmatched"],
        review_flags=["CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process([diff])
    processed = result.diffs[0]

    assert processed.quality_status == "NEEDS_REVIEW"
    assert "TABLE_REGION_REVIEW" in processed.review_flags
    assert "CRITICAL_VALUE_CHANGE" not in processed.review_flags
    assert any(decision.action == "table_region_review" for decision in result.decisions)


def test_diff_quality_does_not_let_critical_field_flag_bypass_table_region_review() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="table",
        title="表格区域：大范围内容不一致",
        original_text="单位名称：国能日新科技股份有限公司 金额：100",
        compare_text="单位名称：斯美能源科技有限公司 金额：200",
        structural_flags=["table_region_coverage_gap", "large_flat_unmatched"],
        review_flags=["CRITICAL_FIELD_CHANGE", "CRITICAL_FIELD_AMOUNT_CHANGE"],
    )

    result = DiffQualityProcessor().process([diff])
    processed = result.diffs[0]

    assert processed.quality_status == "NEEDS_REVIEW"
    assert "TABLE_REGION_REVIEW" in processed.review_flags
    assert "CRITICAL_VALUE_CHANGE" not in processed.review_flags
    assert any(decision.action == "table_region_review" for decision in result.decisions)


def test_diff_quality_keeps_row_level_table_protected_value_changes_critical() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="table",
        title="表格行：行1变更",
        original_text="1 | 服务器 | 配置 | 国能日新 | 套 | 1 | 100 | 100",
        compare_text="1 | 服务器 | 配置 | 国能日新 | 套 | 2 | 100 | 200",
        original_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=0, y0=0, x1=100, y1=20), method="table_row")],
        compare_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=0, y0=0, x1=100, y1=20), method="table_row")],
    )

    result = DiffQualityProcessor().process([diff])
    processed = result.diffs[0]

    assert processed.quality_status == "NORMAL"
    assert "CRITICAL_VALUE_CHANGE" in processed.review_flags


def test_diff_quality_treats_party_line_and_body_contact_info_as_regular_clause_changes() -> None:
    diffs = [
        DiffItem(
            diff_id="D001",
            diff_type="MODIFY",
            source_type="clause",
            section_type="quote",
            original_snippet="甲方:江苏东大金智信息系统有限公司乙方:国能日新科技股份有限公司",
            compare_snippet="",
            review_flags=["NON_MAIN_CONTRACT_SECTION", "CRITICAL_VALUE_CHANGE"],
        ),
        DiffItem(
            diff_id="D002",
            diff_type="ADD",
            source_type="clause",
            section_type="main_contract",
            clause_no="27",
            compare_text="27号金隅智造工场N6\n联系人:刘玉良\n电话:18811089109\nEmail:yuliang.liu@sprixin.com",
            compare_snippet="27号金隅智造工场N6 联系人:刘玉良 电话:18811089109 Email:yuliang.liu@sprixin.com",
        ),
    ]

    result = DiffQualityProcessor().process(diffs)
    by_id = {diff.diff_id: diff for diff in result.diffs}

    assert "D001" in by_id
    assert "CRITICAL_VALUE_CHANGE" in by_id["D001"].review_flags
    assert "CRITICAL_VALUE_CHANGE" in by_id["D002"].review_flags


def test_diff_quality_dedupes_exact_cross_source_duplicates() -> None:
    evidence = EvidenceBox(page_no=1, bbox=BBox(x0=1, y0=1, x1=2, y1=2), text="签订日期：2026年6月7日")
    diffs = [
        DiffItem(
            diff_id="D001",
            diff_type="MODIFY",
            source_type="table",
            original_text="签订日期：2026年6月6日",
            compare_text="签订日期：2026年6月7日",
            compare_evidence=[evidence],
        ),
        DiffItem(
            diff_id="D002",
            diff_type="MODIFY",
            source_type="metadata",
            original_text="签订日期 2026年6月6日",
            compare_text="签订日期：2026年6月7日",
        ),
    ]

    result = DiffQualityProcessor().process(diffs)

    assert [diff.diff_id for diff in result.diffs] == ["D002"]
    assert "CROSS_SOURCE_MERGED" in result.diffs[0].review_flags
    assert result.diffs[0].merged_sources == ["table"]


def test_diff_quality_flags_clause_add_matching_opposite_cross_source_text() -> None:
    diffs = [
        DiffItem(
            diff_id="D001",
            diff_type="MODIFY",
            source_type="header_footer",
            original_text="一、产品名称、型号、数量、金额、供货时间：",
            compare_text="单位：元（人民币）",
        ),
        DiffItem(
            diff_id="D002",
            diff_type="ADD",
            source_type="clause",
            compare_text="一、产品名称、型号、数量、金额、供货时间：\n合同编号：",
            compare_snippet="一、产品名称、型号、数量、金额、供货时间： 合同编号：",
        ),
    ]

    result = DiffQualityProcessor().process(diffs)
    clause = next(diff for diff in result.diffs if diff.diff_id == "D002")

    assert clause.quality_status == "NEEDS_REVIEW"
    assert "POSSIBLE_STRUCTURAL_MISCLASSIFICATION" in clause.review_flags
