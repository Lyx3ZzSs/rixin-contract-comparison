from __future__ import annotations

import re

from app.models import BBox, Clause, ClausePair, DiffItem, Document, EvidenceBox, Page, TextBlock, TextRange
from app.services.clause_splitter import ClauseSplitter
from app.services.diff_engine import DiffEngine
from app.services.diff.boundary_coverage import contact_field_coverage_sequences
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


def test_clause_splitter_does_not_promote_article_reference_as_clause_number() -> None:
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
                        block_id="quality-31",
                        page_no=1,
                        text=(
                            "3.1 乙方应于本合同签订后【】日内提供给甲方一套样品，"
                            "本合同项下产品应与乙方提供并经甲方书面确认合格的样品及"
                        ),
                        bbox=BBox(x0=50, y0=80, x1=500, y1=110),
                    ),
                    TextBlock(
                        block_id="article-reference",
                        page_no=1,
                        text="第1条所列明的\n货物规格等要求一致。[适用有样品的情形]",
                        bbox=BBox(x0=70, y0=112, x1=500, y1=145),
                    ),
                    TextBlock(
                        block_id="quality-32",
                        page_no=1,
                        text=(
                            "3.2 本合同项下产品应符合其产品说明书或包装上注明采用的产品质量标准。"
                        ),
                        bbox=BBox(x0=50, y0=150, x1=500, y1=180),
                    )
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["3.1", "3.2"]
    assert "第1条所列明的货物规格等要求一致" in clauses[0].text.replace("\n", "")


def test_clause_splitter_keeps_payment_method_value_tail_inside_parent_clause() -> None:
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
                        block_id="payment-start",
                        page_no=1,
                        text="9.1 本合同项下所有款项均以人民币支付。付款方式：【",
                        bbox=BBox(x0=70, y0=230, x1=353.5, y1=242),
                    ),
                    TextBlock(
                        block_id="payment-tail-symbol",
                        page_no=1,
                        text="_】",
                        bbox=BBox(x0=143, y0=245.5, x1=194, y1=259.5),
                    ),
                    TextBlock(
                        block_id="payment-tail-value",
                        page_no=1,
                        text="35,质保金5",
                        bbox=BBox(x0=85.5, y0=246, x1=148, y1=259.5),
                    ),
                    TextBlock(
                        block_id="payment-options",
                        page_no=1,
                        text="A. 滚动付款方式。付款条件为乙方将产品送至我方指定地点。",
                        bbox=BBox(x0=70, y0=263.5, x1=530, y1=275),
                    ),
                    TextBlock(
                        block_id="next",
                        page_no=1,
                        text="9.2 若为款到发货或预付款金额超过50%及以上，则乙方应至少提前日开具发票。",
                        bbox=BBox(x0=70, y0=420, x1=530, y1=435),
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "N")

    assert [clause.clause_no for clause in clauses] == ["9.1", "9.2"]
    assert "35,质保金5" in clauses[0].text
    assert "_】" in clauses[0].text


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


def test_clause_splitter_merges_normalized_bbox_cross_page_continuation() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=1,
                height=1,
                blocks=[
                    TextBlock(
                        block_id="p1-heading",
                        page_no=1,
                        text="5.5 服务交付",
                        bbox=BBox(x0=0.08, y0=0.88, x1=0.86, y1=0.92),
                    ),
                    TextBlock(
                        block_id="p1-body",
                        page_no=1,
                        text="乙方应完成系统部署并提供",
                        bbox=BBox(x0=0.12, y0=0.94, x1=0.88, y1=0.98),
                    ),
                ],
            ),
            Page(
                page_no=2,
                width=1,
                height=1,
                blocks=[
                    TextBlock(
                        block_id="p2-body",
                        page_no=2,
                        text="不少于三十日的试运行支持。",
                        bbox=BBox(x0=0.12, y0=0.06, x1=0.88, y1=0.10),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["5.5"]
    assert "试运行支持" in clauses[0].text
    assert clauses[0].page_numbers == [1, 2]
    assert "CROSS_PAGE_CONTINUATION_MERGED" in clauses[0].split_flags


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


def test_clause_splitter_keeps_short_business_word_cross_page_continuation() -> None:
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
                        text="5.2 乙方应保证服务",
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
                        block_id="p2-word",
                        page_no=2,
                        text="质量",
                        bbox=BBox(x0=70, y0=72, x1=120, y1=102),
                    ),
                    TextBlock(
                        block_id="p2-body",
                        page_no=2,
                        text="符合合同约定。",
                        bbox=BBox(x0=70, y0=116, x1=520, y1=146),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 1
    assert "服务\n质量\n符合合同约定" in clauses[0].text
    assert clauses[0].source_block_ids == ["p1", "p2-word", "p2-body"]
    assert "CROSS_PAGE_CONTINUATION_MERGED" not in clauses[0].split_flags


def test_clause_splitter_does_not_promote_short_word_after_cross_page_merge() -> None:
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
                        text="5.2 乙方应保证",
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
                        block_id="p2-line1",
                        page_no=2,
                        text="服务质量，",
                        bbox=BBox(x0=70, y0=72, x1=520, y1=102),
                    ),
                    TextBlock(
                        block_id="p2-word",
                        page_no=2,
                        text="安全",
                        bbox=BBox(x0=70, y0=116, x1=120, y1=146),
                    ),
                    TextBlock(
                        block_id="p2-body",
                        page_no=2,
                        text="符合合同约定。",
                        bbox=BBox(x0=90, y0=160, x1=520, y1=190),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 1
    assert "服务质量" in clauses[0].text
    assert "安全\n符合合同约定" in clauses[0].text
    assert clauses[0].source_block_ids == ["p1", "p2-line1", "p2-word", "p2-body"]
    assert "CROSS_PAGE_CONTINUATION_MERGED" in clauses[0].split_flags


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


def test_clause_splitter_keeps_signature_boundary_after_cross_page_merge() -> None:
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
                        text="5.2 乙方应保证服务",
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
                        block_id="p2-body",
                        page_no=2,
                        text="服务质量，",
                        bbox=BBox(x0=70, y0=72, x1=520, y1=102),
                    ),
                    TextBlock(
                        block_id="sign-none",
                        page_no=2,
                        text="以下无正文",
                        bbox=BBox(x0=50, y0=130, x1=220, y1=160),
                    ),
                    TextBlock(
                        block_id="sign-party",
                        page_no=2,
                        text="甲方（盖章）：",
                        bbox=BBox(x0=50, y0=190, x1=220, y1=220),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) >= 2
    assert "服务质量" in clauses[0].text
    assert "以下无正文" not in clauses[0].text
    assert clauses[0].source_block_ids == ["p1", "p2-body"]
    assert "CROSS_PAGE_CONTINUATION_MERGED" in clauses[0].split_flags
    assert clauses[1].source_block_ids == ["sign-none", "sign-party"]


def test_clause_splitter_does_not_merge_normalized_bbox_short_title_boundary() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=1,
                height=1,
                blocks=[
                    TextBlock(
                        block_id="p1",
                        page_no=1,
                        text="5.2 乙方应持续提供服务",
                        bbox=BBox(x0=0.08, y0=0.88, x1=0.86, y1=0.92),
                    )
                ],
            ),
            Page(
                page_no=2,
                width=1,
                height=1,
                blocks=[
                    TextBlock(
                        block_id="p2-title",
                        page_no=2,
                        text="付款",
                        bbox=BBox(x0=0.08, y0=0.06, x1=0.22, y1=0.10),
                    ),
                    TextBlock(
                        block_id="p2-body",
                        page_no=2,
                        text="乙方应按月付款。",
                        bbox=BBox(x0=0.20, y0=0.12, x1=0.88, y1=0.16),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 2
    assert "付款" not in clauses[0].text
    assert clauses[1].source_block_ids == ["p2-title", "p2-body"]


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


def _quality_clause(
    clause_id: str,
    text: str,
    *,
    side_prefix: str = "O",
    order_index: int = 1,
    page_no: int = 1,
    section_type: str = "main_contract",
    split_flags: list[str] | None = None,
) -> Clause:
    return Clause(
        clause_id=clause_id,
        clause_no="",
        title=text.splitlines()[0] if text else "",
        text=text,
        normalized_text=re.sub(r"\s+", "", text),
        page_numbers=[page_no],
        bboxes=[
            EvidenceBox(
                page_no=page_no,
                bbox=BBox(x0=10, y0=10, x1=120, y1=30),
                text=text[:80],
            )
        ],
        source_block_ids=[f"{side_prefix.lower()}_block_{order_index}"],
        section_type=section_type,
        section_path=[text] if section_type == "appendix" else [],
        order_index=order_index,
        split_flags=split_flags or [],
    )


def _quality_document(page_no: int, text: str) -> Document:
    return Document(
        filename="quality.pdf",
        path="quality.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=page_no,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id=f"p{page_no}_b1",
                        page_no=page_no,
                        text=text,
                        bbox=BBox(x0=40, y0=80, x1=540, y1=140),
                        block_type="text",
                    )
                ],
            )
        ],
    )


def test_contact_field_coverage_sequences_pairs_columnar_contacts_after_prior_field() -> None:
    sequences = contact_field_coverage_sequences(
        "传真:010-83458100\n"
        "联系人:环加飞\n"
        "联系人:刘玉良\n"
        "电话:010-83582793\n"
        "电话:18811089109\n"
        "传真:010-83582600\n"
        "传真:010-83458100"
    )

    assert "联系人环加飞电话01083582793传真01083582600" in sequences


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


def test_diff_quality_suppresses_short_symbol_noise_even_when_clause_context_has_numbers() -> None:
    diff = DiffItem(
        diff_id="D005",
        diff_type="MODIFY",
        source_type="clause",
        original_text="10.2 有下列情形之一的。\n(1) 因对方违约使合同不能继续履行或没有必要继续履行;\n(2) 1。",
        compare_text="10.2 有下列情形之一的。\n(1) 因对方违约使合同不能继续履行或没有必要继续履行;\n(2) ∠。",
        original_snippet="1",
        compare_snippet="∠",
        match_score=100,
        match_score_details={
            "body_score": 98.0,
            "business_token_mismatch": 0.0,
        },
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D005"
        and decision.detail["reason"] in {"clause_ocr_noise", "short_symbol_noise", "layout_punctuation_equivalent"}
        for decision in result.decisions
    )


def test_diff_quality_suppresses_evidence_unreliable_short_symbol_noise() -> None:
    diff = DiffItem(
        diff_id="D007",
        diff_type="MODIFY",
        source_type="clause",
        original_text="13.2 可行性论证报告:/;",
        compare_text="13.2 可行性论证报告:;",
        original_snippet="/",
        compare_snippet="",
        match_score=100,
        review_flags=[
            "EVIDENCE_UNRELIABLE",
            "SHORT_CLAUSE_MATCH_REVIEW",
            "OCR_REMEDIATION_PLANNED",
            "OCR_REMEDIATION_UNRESOLVED",
            "POSSIBLE_OCR_NOISE",
        ],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D007"
        and decision.detail["reason"] == "clause_ocr_noise"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_single_sided_payment_blank_tail_symbol_noise() -> None:
    diff = DiffItem(
        diff_id="D004",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="35",
        title=",质保金5",
        original_text=(
            "35,质保金5\n"
            "A. 滚动付款方式。付款条件为乙方将产品送至我方指定地点。"
        ),
        compare_text=(
            "35,质保金5\n"
            "_】\n"
            "A. 滚动付款方式。付款条件为乙方将产品送至我方指定地点。"
        ),
        original_snippet="",
        compare_snippet="_】",
        match_score=100,
        match_score_details={
            "body_score": 99.93,
            "business_token_mismatch": 0.0,
        },
        structural_flags=["PARAGRAPH_MERGED", "READING_ORDER_REPAIRED"],
        review_flags=[
            "READING_ORDER_REPAIRED",
            "READING_ORDER_RISK",
            "OCR_REMEDIATION_PLANNED",
            "POSSIBLE_OCR_NOISE",
        ],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D004"
        and decision.detail["reason"] == "clause_ocr_noise"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_reading_order_contact_fields_covered_by_neighbor_clause() -> None:
    original_previous = _quality_clause(
        "OC146",
        "15.特别约定\n地址:北京市西城区广安门内大街\n482号\n联系人:环加飞\n电话:010-83582793\n传真:010-83582600",
        order_index=146,
        page_no=25,
        split_flags=["PARAGRAPH_MERGED"],
    )
    original_current = _quality_clause(
        "OC147",
        "27号金隅智造工场N6\n联系人:刘玉良\n电话:18811089109\n传真:010-83458100\nEmail: huan.jiafei@nc.sgcc.com.cn Email: yuliang.liu@sprixin.com\n统一社会信用代码:911100000536 统一社会信用代码:9111010867\n21038D",
        order_index=147,
        page_no=25,
        split_flags=["PARAGRAPH_MERGED"],
    )
    compare_current = _quality_clause(
        "NC147",
        "27号金隅智造工场N6\n联系人:环加飞\n联系人:刘玉良\n电话:010-83582793\n电话:18811089109\n传真:010-83582600\n传真:010-83458100\nEmail: huan.jiafei@nc.sgcc.com.cn\nEmail: yuliang.liu@sprixin.com\n统一社会信用代码:911100000536\n统一社会信用代码:91110108672\n21038D",
        side_prefix="N",
        order_index=147,
        page_no=24,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    diff = DiffItem(
        diff_id="D015",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC147",
        compare_clause_id="NC147",
        original_text=original_current.text,
        compare_text=compare_current.text,
        original_snippet="Email: yuliang.liu@sprixin.com统一社会信用代码:9111010867",
        compare_snippet="联系人:环加飞电话:010-83582793传真:010-83582600Email: yuliang.liu@sprixin.com统一社会信用代码:91110108672",
        structural_flags=["PARAGRAPH_MERGED", "READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_REPAIRED", "READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_previous, original_current],
        compare_clauses=[compare_current],
    )

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D015"
        and decision.detail["reason"] == "changed_fragments_covered_by_neighbor_clauses"
        for decision in result.decisions
    )


def test_diff_quality_trims_covered_contact_fields_from_mixed_signing_page_diff() -> None:
    original_clause = _quality_clause(
        "OC146",
        "15.特别约定\n"
        "本特别约定是合同各方经协商后对合同其他条款的修改或补充。\n"
        "1。\n"
        "(以下无正文)\n"
        "签署页\n"
        "甲方:国家电网有限公司华北分部乙方:国能日新科技股份有限公\n"
        "(盖章)\n"
        "法定代表人(负责人)或\n"
        "授权代表(签字):\n"
        "签订日期:\n"
        "地址:北京市西城区广安门内大街地址:北京市海淀区建材城中路\n"
        "482号\n"
        "联系人:环加飞\n"
        "电话:010-83582793\n"
        "传真:010-83582600",
        order_index=146,
        page_no=25,
        split_flags=["PARAGRAPH_MERGED"],
    )
    compare_clause = _quality_clause(
        "NC146",
        "15. 特别约定\n"
        "本特别约定是合同各方经协商后对合同其他条款的修改或补充。\n"
        "∠。\n"
        "(以下无正文)\n"
        "地址:北京市西城区广安门内大街地址:北京市海淀区建材城中路\n"
        "482号",
        side_prefix="N",
        order_index=146,
        page_no=23,
        split_flags=["PARAGRAPH_MERGED"],
    )
    compare_neighbor = _quality_clause(
        "NC147",
        "27号金隅智造工场N6\n"
        "联系人:环加飞\n"
        "联系人:刘玉良\n"
        "电话:010-83582793\n"
        "电话:18811089109\n"
        "传真:010-83582600\n"
        "传真:010-83458100",
        side_prefix="N",
        order_index=147,
        page_no=24,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    original_text = original_clause.text
    compare_text = compare_clause.text
    original_ranges = [
        TextRange(start=original_text.index("1。"), end=original_text.index("1。") + len("1。"), highlight_type="DELETE"),
        TextRange(start=original_text.index("签署页"), end=original_text.index("签署页") + len("签署页"), highlight_type="DELETE"),
        TextRange(start=original_text.index("甲方:"), end=original_text.index("签订日期:") + len("签订日期:"), highlight_type="DELETE"),
        TextRange(start=original_text.index("联系人:"), end=original_text.index("联系人:") + len("联系人:环加飞"), highlight_type="DELETE"),
        TextRange(start=original_text.index("电话:"), end=original_text.index("电话:") + len("电话:010-83582793"), highlight_type="DELETE"),
        TextRange(start=original_text.index("传真:"), end=original_text.index("传真:") + len("传真:010-83582600"), highlight_type="DELETE"),
    ]
    diff = DiffItem(
        diff_id="D014",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC146",
        compare_clause_id="NC146",
        original_text=original_text,
        compare_text=compare_text,
        original_snippet="1。签署页甲方:国家电网有限公司华北分部乙方:国能日新科技股份有限公"
        "(盖章)法定代表人(负责人)或授权代表(签字):签订日期:"
        "联系人:环加飞电话:010-83582793传真:010-83582600",
        compare_snippet="∠。",
        original_change_ranges=original_ranges,
        compare_change_ranges=[
            TextRange(start=compare_text.index("∠。"), end=compare_text.index("∠。") + len("∠。"), highlight_type="ADD")
        ],
        original_evidence=[
            EvidenceBox(page_no=25, bbox=BBox(x0=10, y0=10, x1=80, y1=30), text="签署页", highlight_type="DELETE"),
            EvidenceBox(page_no=25, bbox=BBox(x0=10, y0=40, x1=80, y1=60), text="联系人:", highlight_type="DELETE"),
            EvidenceBox(page_no=25, bbox=BBox(x0=85, y0=40, x1=140, y1=60), text="环加飞", highlight_type="DELETE"),
            EvidenceBox(page_no=25, bbox=BBox(x0=10, y0=70, x1=80, y1=90), text="电话:", highlight_type="DELETE"),
            EvidenceBox(page_no=25, bbox=BBox(x0=85, y0=70, x1=180, y1=90), text="010-83582793", highlight_type="DELETE"),
            EvidenceBox(page_no=25, bbox=BBox(x0=10, y0=100, x1=80, y1=120), text="传真:", highlight_type="DELETE"),
            EvidenceBox(page_no=25, bbox=BBox(x0=85, y0=100, x1=180, y1=120), text="010-83582600", highlight_type="DELETE"),
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        review_flags=["READING_ORDER_RISK", "LOW_CONFIDENCE_MATCH", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause, compare_neighbor],
    )

    assert [item.diff_id for item in result.diffs] == ["D014"]
    trimmed = result.diffs[0]
    assert "签署页" in trimmed.original_snippet
    assert "联系人" not in trimmed.original_snippet
    assert "010-83582793" not in trimmed.original_snippet
    assert "010-83582600" not in trimmed.original_snippet
    assert {evidence.text for evidence in trimmed.original_evidence} == {"签署页"}
    assert all("联系人" not in trimmed.original_text[item.start : item.end] for item in trimmed.original_change_ranges)
    assert all("010-83582793" not in trimmed.original_text[item.start : item.end] for item in trimmed.original_change_ranges)
    assert any(
        decision.action == "trimmed_by_neighbor_clause_coverage"
        and decision.diff_id == "D014"
        and decision.detail["removed_original_fields"] == ["联系人:环加飞", "电话:010-83582793", "传真:010-83582600"]
        for decision in result.decisions
    )


def test_diff_quality_trims_covered_signing_form_labels_from_mixed_signature_diff() -> None:
    original_clause = _quality_clause(
        "OC068",
        "22.5本合同一式两份,双方各持一份,传真件有效。\n"
        "甲方:【南京国电南自电网自动化有限公\n"
        "授权代表签字:\n"
        "纳税人识别号:\n"
        "日期:\n"
        "2026.4.17\n"
        "乙方:【国能日新科技股份有限公司】(盖章)\n"
        "授权代表签字:\n"
        "纳税人识别号:",
        order_index=68,
        page_no=7,
        split_flags=["PARAGRAPH_MERGED"],
    )
    compare_clause = _quality_clause(
        "NC068",
        "22.5本合同一式两份,双方各持一份,传真件有效。\n"
        "甲方:【南\n"
        "司】(盖章\n"
        "授权代表签\n"
        "纳税人识别\n"
        "日期:\n"
        "2026.4.17\n"
        "日期:2026.4.17\n"
        "刘万程",
        side_prefix="N",
        order_index=68,
        page_no=7,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    compare_text = compare_clause.text
    compare_ranges = [
        TextRange(start=compare_text.index("司】(盖章"), end=compare_text.index("司】(盖章") + len("司】(盖章"), highlight_type="ADD"),
        TextRange(start=compare_text.index("授权代表签"), end=compare_text.index("授权代表签") + len("授权代表签"), highlight_type="ADD"),
        TextRange(start=compare_text.index("纳税人识别"), end=compare_text.index("纳税人识别") + len("纳税人识别"), highlight_type="ADD"),
        TextRange(start=compare_text.index("日期:2026.4.17"), end=compare_text.index("日期:2026.4.17") + len("日期:2026.4.17"), highlight_type="ADD"),
        TextRange(start=compare_text.index("刘万程"), end=compare_text.index("刘万程") + len("刘万程"), highlight_type="ADD"),
    ]
    diff = DiffItem(
        diff_id="D005",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC068",
        compare_clause_id="NC068",
        original_text=original_clause.text,
        compare_text=compare_text,
        original_snippet="乙方:【国能日新科技股份有限公司】(授权代表签字:纳税人识别号:",
        compare_snippet="司】(盖章授权代表签纳税人识别日期:2026.4.17刘万程",
        compare_change_ranges=compare_ranges,
        compare_evidence=[
            EvidenceBox(page_no=7, bbox=BBox(x0=68.2, y0=345.7, x1=126.3, y1=361.3), text="司】(盖章", highlight_type="ADD"),
            EvidenceBox(page_no=7, bbox=BBox(x0=68.7, y0=362.7, x1=126.3, y1=378.3), text="授权代表签", highlight_type="ADD"),
            EvidenceBox(page_no=7, bbox=BBox(x0=68.7, y0=380.2, x1=125.8, y1=393.8), text="纳税人识别", highlight_type="ADD"),
            EvidenceBox(page_no=7, bbox=BBox(x0=337.2, y0=486.2, x1=408.8, y1=522.8), text="2026.4.17", highlight_type="ADD"),
            EvidenceBox(page_no=7, bbox=BBox(x0=501.2, y0=349.2, x1=565.8, y1=383.3), text="刘万程", highlight_type="ADD"),
        ],
        structural_flags=["PUNCTUATED_HEADING", "PARAGRAPH_MERGED", "READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "SEAL_OR_SIGNATURE_RISK", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
    )

    assert [item.diff_id for item in result.diffs] == ["D005"]
    trimmed = result.diffs[0]
    assert "司】(盖章" not in trimmed.compare_snippet
    assert "授权代表签" not in trimmed.compare_snippet
    assert "纳税人识别" not in trimmed.compare_snippet
    assert "2026.4.17" in trimmed.compare_snippet
    assert "刘万程" in trimmed.compare_snippet
    assert {evidence.text for evidence in trimmed.compare_evidence} == {"2026.4.17", "刘万程"}
    assert any(
        decision.action == "trimmed_by_neighbor_clause_coverage"
        and decision.diff_id == "D005"
        and decision.detail["removed_compare_fields"] == ["盖章", "授权代表签字", "纳税人识别号"]
        for decision in result.decisions
    )


def test_diff_quality_keeps_missing_signature_label_when_compare_only_has_authorized_representative_clause_text() -> None:
    original_clause = _quality_clause(
        "OC146",
        "15.特别约定\n"
        "(以下无正文)\n"
        "签署页\n"
        "甲方:国家电网有限公司华北分部乙方:国能日新科技股份有限公\n"
        "(盖章)\n"
        "法定代表人(负责人)或\n"
        "授权代表(签字):\n"
        "签订日期:",
        order_index=146,
        page_no=25,
        split_flags=["PARAGRAPH_MERGED"],
    )
    compare_clause = _quality_clause(
        "NC146",
        "15. 特别约定\n"
        "本合同经双方法定代表人(负责人)或其授权代表签署并加盖双方公章后生效。\n"
        "(以下无正文)",
        side_prefix="N",
        order_index=146,
        page_no=23,
        split_flags=["PARAGRAPH_MERGED"],
    )
    original_text = original_clause.text
    target = "授权代表(签字):"
    diff = DiffItem(
        diff_id="D014",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC146",
        compare_clause_id="NC146",
        original_text=original_text,
        compare_text=compare_clause.text,
        original_snippet=target,
        compare_snippet="",
        original_change_ranges=[
            TextRange(
                start=original_text.index(target),
                end=original_text.index(target) + len(target),
                highlight_type="DELETE",
            )
        ],
        original_evidence=[
            EvidenceBox(page_no=25, bbox=BBox(x0=91, y0=229, x1=180, y1=245), text=target, highlight_type="DELETE")
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
    )

    assert [item.diff_id for item in result.diffs] == ["D014"]
    kept = result.diffs[0]
    assert "授权代表" in kept.original_snippet
    assert [evidence.text for evidence in kept.original_evidence] == [target]
    assert not any(
        decision.action == "trimmed_by_neighbor_clause_coverage"
        and decision.diff_id == "D014"
        and "授权代表签字" in decision.detail.get("removed_original_fields", [])
        for decision in result.decisions
    )


def test_diff_quality_keeps_boundary_risk_diff_with_uncovered_amount_change() -> None:
    original_clause = _quality_clause(
        "OC201",
        "联系人:王五\n电话:13800000000\n合同金额100元",
        order_index=201,
        page_no=30,
        split_flags=["PARAGRAPH_MERGED"],
    )
    compare_clause = _quality_clause(
        "NC201",
        "联系人:王五\n电话:13800000000\n合同金额200元",
        side_prefix="N",
        order_index=201,
        page_no=30,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    diff = DiffItem(
        diff_id="D020",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC201",
        compare_clause_id="NC201",
        original_text=original_clause.text,
        compare_text=compare_clause.text,
        original_snippet="电话:13800000000 合同金额100元",
        compare_snippet="电话:13800000000 合同金额200元",
        structural_flags=["PARAGRAPH_MERGED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
    )

    assert [item.diff_id for item in result.diffs] == ["D020"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage" and decision.diff_id == "D020"
        for decision in result.decisions
    )


def test_diff_quality_keeps_contact_phone_reassignment_even_when_values_exist_in_windows() -> None:
    original_clause = _quality_clause(
        "OC202",
        "联系人:张三\n电话:13800000001\n联系人:李四\n电话:13800000002",
        order_index=202,
        page_no=31,
        split_flags=["PARAGRAPH_MERGED"],
    )
    compare_clause = _quality_clause(
        "NC202",
        "联系人:张三\n电话:13800000002\n联系人:李四\n电话:13800000001",
        side_prefix="N",
        order_index=202,
        page_no=31,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    diff = DiffItem(
        diff_id="D021",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC202",
        compare_clause_id="NC202",
        original_text=original_clause.text,
        compare_text=compare_clause.text,
        original_snippet="联系人:张三电话:13800000001",
        compare_snippet="联系人:张三电话:13800000002",
        structural_flags=["PARAGRAPH_MERGED", "READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
    )

    assert [item.diff_id for item in result.diffs] == ["D021"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage" and decision.diff_id == "D021"
        for decision in result.decisions
    )


def test_diff_quality_keeps_bare_phone_reassignment_when_values_exist_in_windows() -> None:
    original_clause = _quality_clause(
        "OC204",
        "联系人:张三\n电话:13800000001\n联系人:李四\n电话:13800000002",
        order_index=204,
        page_no=33,
        split_flags=["PARAGRAPH_MERGED"],
    )
    compare_clause = _quality_clause(
        "NC204",
        "联系人:张三\n电话:13800000002\n联系人:李四\n电话:13800000001",
        side_prefix="N",
        order_index=204,
        page_no=33,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    diff = DiffItem(
        diff_id="D023",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC204",
        compare_clause_id="NC204",
        original_text=original_clause.text,
        compare_text=compare_clause.text,
        original_snippet="13800000001",
        compare_snippet="13800000002",
        structural_flags=["PARAGRAPH_MERGED", "READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
    )

    assert [item.diff_id for item in result.diffs] == ["D023"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage" and decision.diff_id == "D023"
        for decision in result.decisions
    )


def test_diff_quality_keeps_partial_credit_code_change_even_with_email_coverage() -> None:
    original_clause = _quality_clause(
        "OC203",
        "Email: same@example.com\n统一社会信用代码:12345678",
        order_index=203,
        page_no=32,
        split_flags=["PARAGRAPH_MERGED"],
    )
    compare_clause = _quality_clause(
        "NC203",
        "Email: same@example.com\n统一社会信用代码:87654321",
        side_prefix="N",
        order_index=203,
        page_no=32,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    diff = DiffItem(
        diff_id="D022",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC203",
        compare_clause_id="NC203",
        original_text=original_clause.text,
        compare_text=compare_clause.text,
        original_snippet="Email: same@example.com统一社会信用代码:12345678",
        compare_snippet="Email: same@example.com统一社会信用代码:87654321",
        structural_flags=["PARAGRAPH_MERGED", "READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
    )

    assert [item.diff_id for item in result.diffs] == ["D022"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage" and decision.diff_id == "D022"
        for decision in result.decisions
    )


def test_diff_quality_keeps_true_phone_change_even_with_boundary_risk() -> None:
    original_clause = _quality_clause(
        "OC010",
        "联系人:刘玉良\n电话:18811089109",
        order_index=10,
        page_no=2,
        split_flags=["PARAGRAPH_MERGED"],
    )
    compare_clause = _quality_clause(
        "NC010",
        "联系人:刘玉良\n电话:18811089110",
        side_prefix="N",
        order_index=10,
        page_no=2,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    diff = DiffItem(
        diff_id="D900",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC010",
        compare_clause_id="NC010",
        original_text=original_clause.text,
        compare_text=compare_clause.text,
        original_snippet="电话:18811089109",
        compare_snippet="电话:18811089110",
        structural_flags=["PARAGRAPH_MERGED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
    )

    assert [item.diff_id for item in result.diffs] == ["D900"]


def test_diff_quality_suppresses_equal_credit_code_with_different_line_break() -> None:
    original_clause = _quality_clause(
        "OC020",
        "统一社会信用代码:9111010867\n23891430\nEmail: yuliang.liu@sprixin.com",
        order_index=20,
        page_no=3,
        split_flags=["PARAGRAPH_MERGED"],
    )
    compare_clause = _quality_clause(
        "NC020",
        "统一社会信用代码:911101086723891430\nEmail: yuliang.liu@sprixin.com",
        side_prefix="N",
        order_index=20,
        page_no=3,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    diff = DiffItem(
        diff_id="D901",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC020",
        compare_clause_id="NC020",
        original_text=original_clause.text,
        compare_text=compare_clause.text,
        original_snippet="统一社会信用代码:9111010867\n23891430Email: yuliang.liu@sprixin.com",
        compare_snippet="统一社会信用代码:911101086723891430Email: yuliang.liu@sprixin.com",
        structural_flags=["PARAGRAPH_MERGED", "READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
    )

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D901"
        and decision.detail["reason"] == "changed_fragments_covered_by_neighbor_clauses"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_appendix_heading_delete_covered_by_compare_page_text() -> None:
    original_clause = _quality_clause(
        "OC093",
        "附件一:",
        order_index=93,
        page_no=13,
        section_type="appendix",
        split_flags=["SECTION_APPENDIX"],
    )
    compare_document = _quality_document(13, "附件一：\n技术服务人员表\n姓名 单位 性别")
    diff = DiffItem(
        diff_id="D016",
        diff_type="DELETE",
        source_type="clause",
        original_clause_id="OC093",
        section_type="appendix",
        section_path=["附件一:"],
        original_text="附件一:",
        original_snippet="附件一:",
        structural_flags=["SECTION_APPENDIX"],
        review_flags=["NON_MAIN_CONTRACT_SECTION"],
        original_evidence=original_clause.bboxes,
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[],
        compare_document=compare_document,
    )

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D016"
        and decision.detail["reason"] == "short_appendix_heading_covered"
        for decision in result.decisions
    )


def test_diff_quality_keeps_appendix_heading_delete_when_opposite_page_only_references_appendix() -> None:
    original_clause = _quality_clause(
        "OC093",
        "附件一:",
        order_index=93,
        page_no=13,
        section_type="appendix",
        split_flags=["SECTION_APPENDIX"],
    )
    compare_document = _quality_document(13, "服务范围详见附件一的人员安排，双方按正文约定执行。")
    diff = DiffItem(
        diff_id="D016",
        diff_type="DELETE",
        source_type="clause",
        original_clause_id="OC093",
        section_type="appendix",
        section_path=["附件一:"],
        original_text="附件一:",
        original_snippet="附件一:",
        structural_flags=["SECTION_APPENDIX"],
        review_flags=["NON_MAIN_CONTRACT_SECTION"],
        original_evidence=original_clause.bboxes,
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[],
        compare_document=compare_document,
    )

    assert [item.diff_id for item in result.diffs] == ["D016"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage" and decision.diff_id == "D016"
        for decision in result.decisions
    )


def test_diff_quality_keeps_appendix_heading_delete_without_evidence_pages() -> None:
    original_clause = _quality_clause(
        "OC093",
        "附件一:",
        order_index=93,
        page_no=13,
        section_type="appendix",
        split_flags=["SECTION_APPENDIX"],
    )
    compare_document = _quality_document(13, "附件一：\n技术服务人员表\n姓名 单位 性别")
    diff = DiffItem(
        diff_id="D016",
        diff_type="DELETE",
        source_type="clause",
        original_clause_id="OC093",
        section_type="appendix",
        section_path=["附件一:"],
        original_text="附件一:",
        original_snippet="附件一:",
        structural_flags=["SECTION_APPENDIX"],
        review_flags=["NON_MAIN_CONTRACT_SECTION"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[],
        compare_document=compare_document,
    )

    assert [item.diff_id for item in result.diffs] == ["D016"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage" and decision.diff_id == "D016"
        for decision in result.decisions
    )


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


def test_diff_quality_suppresses_single_latin_glyph_layout_artifact() -> None:
    diff = DiffItem(
        diff_id="D013",
        diff_type="MODIFY",
        source_type="clause",
        original_text="14. 份数\n本合同一式捌份，甲乙双方各执肆份。",
        compare_text="14. 份数\nI\n本合同一式捌份，甲乙双方各执肆份。",
        original_snippet="",
        compare_snippet="I",
        match_score=100,
        structural_flags=["PARAGRAPH_MERGED", "CROSS_PAGE_CONTINUATION_MERGED"],
        review_flags=["LAYOUT_MISMATCH_RISK", "OCR_REMEDIATION_PLANNED"],
        quality_status="NEEDS_REVIEW",
        compare_evidence=[
            EvidenceBox(
                page_no=23,
                bbox=BBox(x0=188, y0=162, x1=191, y1=171),
                method="char_exact",
                text="I",
                highlight_type="MODIFY",
                confidence=0.98,
                evidence_quality="HIGH",
            )
        ],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D013"
        and decision.detail["reason"] == "single_latin_layout_glyph_noise"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_low_confidence_cover_annotation_fragments() -> None:
    diffs = [
        DiffItem(
            diff_id="D005",
            diff_type="ADD",
            source_type="metadata",
            title="封面额外文本",
            compare_text="Cakilu-7020513",
            compare_snippet="Cakilu-7020513",
            review_flags=["EVIDENCE_UNRELIABLE", "OCR_REMEDIATION_UNRESOLVED"],
            compare_evidence=[
                EvidenceBox(
                    page_no=1,
                    bbox=BBox(x0=344, y0=4.5, x1=493, y1=58),
                    method="header_footer",
                    text="Cakilu-7020513",
                    confidence=0.55,
                    evidence_quality="LOW",
                )
            ],
        ),
        DiffItem(
            diff_id="D007",
            diff_type="ADD",
            source_type="metadata",
            title="封面额外文本",
            compare_text="N2o",
            compare_snippet="N2o",
            review_flags=["EVIDENCE_UNRELIABLE", "OCR_REMEDIATION_UNRESOLVED"],
            compare_evidence=[
                EvidenceBox(
                    page_no=1,
                    bbox=BBox(x0=493.5, y0=7, x1=540, y1=33),
                    method="header_footer",
                    text="N2o",
                    confidence=0.55,
                    evidence_quality="LOW",
                )
            ],
        ),
    ]

    result = DiffQualityProcessor().process(diffs)

    assert result.diffs == []
    assert {
        (decision.diff_id, decision.detail.get("reason"))
        for decision in result.decisions
        if decision.action == "suppressed_low_value_noise"
    } == {
        ("D005", "cover_annotation_noise"),
        ("D007", "cover_annotation_noise"),
    }


def test_diff_quality_suppresses_single_cjk_cover_stamp_fragment_near_top() -> None:
    diff = DiffItem(
        diff_id="D007",
        diff_type="ADD",
        source_type="metadata",
        title="封面额外文本",
        compare_text="正",
        compare_snippet="正",
        review_flags=[
            "LAYOUT_MISMATCH_RISK",
            "PAGE_UNRELIABLE",
            "READING_ORDER_RISK",
            "SEAL_OR_SIGNATURE_RISK",
            "OCR_REMEDIATION_MANUAL_REVIEW",
            "POSSIBLE_COVER_OCR_FRAGMENT",
        ],
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=451.5, y0=14.5, x1=545.0, y1=75.0),
                method="cover_extra",
                text="正",
                confidence=0.68,
                evidence_quality="MEDIUM",
            )
        ],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D007"
        and decision.detail["reason"] == "cover_annotation_noise"
        for decision in result.decisions
    )


def test_diff_quality_reclassifies_clause_mixed_signing_date_fill_as_signature_date_change() -> None:
    diff = DiffItem(
        diff_id="D016",
        diff_type="MODIFY",
        source_type="clause",
        title="本项目范围内风电场与国网陕西省电力调度中心及西安集控中心保",
        original_text=(
            "10.6.8本项目范围内风电场与国网陕西省电力调度中心及西安集控中心保持网络通讯正常。\n"
            "甲方:定边县瑞能新能源科技有限公司乙方:国能日新科技股份有限公司\n"
            "年月日\n年月日"
        ),
        compare_text=(
            "10.6.8本项目范围内风电场与国网陕西省电力调度中心及西安集控中心保持网络通讯正常。\n"
            "甲方:定边县瑞能新能源科技有限公司乙方:国能日新科技股份有限公司\n"
            "2026年05月22日\n年月日"
        ),
        original_snippet="",
        compare_snippet="20260522",
        review_flags=[
            "CRITICAL_FIELD_CHANGE",
            "CRITICAL_FIELD_DATE_CHANGE",
            "READING_ORDER_RISK",
            "CRITICAL_VALUE_CHANGE",
        ],
        compare_evidence=[
            EvidenceBox(page_no=28, bbox=BBox(x0=83.7, y0=509.2, x1=113.3, y1=536.3), text="2026"),
            EvidenceBox(page_no=28, bbox=BBox(x0=135.7, y0=509.2, x1=149.3, y1=536.3), text="05"),
            EvidenceBox(page_no=28, bbox=BBox(x0=171.7, y0=509.2, x1=185.3, y1=536.3), text="22"),
        ],
    )

    result = DiffQualityProcessor().process([diff])

    assert len(result.diffs) == 1
    reclassified = result.diffs[0]
    assert reclassified.source_type == "metadata"
    assert reclassified.title == "签署日期"
    assert reclassified.original_snippet == ""
    assert "SIGNING_DATE_FIELD_CHANGE" in reclassified.review_flags
    assert "CRITICAL_FIELD_CHANGE" not in reclassified.review_flags
    assert "CRITICAL_VALUE_CHANGE" not in reclassified.review_flags
    assert any(decision.action == "signing_date_field_reclassified" for decision in result.decisions)


def test_diff_quality_reclassifies_ocr_split_signing_date_without_duration_flag() -> None:
    diff = DiffItem(
        diff_id="D019",
        diff_type="MODIFY",
        source_type="clause",
        title="协议的效力和变更",
        original_text=(
            "第六条协议的效力和变更\n"
            "本协议自双方签字起生效。\n"
            "甲方:定边县瑞能新能源科技有限公司乙方:国能日新科技股份有限公司\n"
            "年月日\n年月日"
        ),
        compare_text=(
            "第六条协议的效力和变更\n"
            "本协议自双方签字起生效。\n"
            "甲方:定边县瑞能新能源科技有限公司乙方:国能日新科技股份有限公司\n"
            "2026年0522日\n年月日"
        ),
        original_snippet="月",
        compare_snippet="20260522",
        review_flags=[
            "CRITICAL_FIELD_CHANGE",
            "CRITICAL_FIELD_DURATION_CHANGE",
            "LAYOUT_MISMATCH_RISK",
            "READING_ORDER_RISK",
            "CRITICAL_VALUE_CHANGE",
        ],
    )

    result = DiffQualityProcessor().process([diff])

    assert len(result.diffs) == 1
    reclassified = result.diffs[0]
    assert reclassified.source_type == "metadata"
    assert reclassified.title == "签署日期"
    assert reclassified.original_snippet == ""
    assert "SIGNING_DATE_FIELD_CHANGE" in reclassified.review_flags
    assert "CRITICAL_FIELD_DURATION_CHANGE" not in reclassified.review_flags
    assert "CRITICAL_VALUE_CHANGE" not in reclassified.review_flags


def test_diff_quality_trims_seal_occluded_signing_form_text_before_date_reclassification() -> None:
    diff = DiffItem(
        diff_id="D013",
        diff_type="MODIFY",
        source_type="clause",
        title="合同执行过程中如发生争议或未尽事项,双方应本着公平、公正的原",
        original_text=(
            "11.4合同执行过程中如发生争议或未尽事项,双方应本着公平、公正的原则协商解决。\n"
            "签字页\n甲方\n单位名称:定边县瑞能新能源科技有限公司(章)\n法定代表人或授权代表签字:\n"
            "签字日期:年月日\n乙方\n单位名称:国能日新科技股份有限公司\n(章)\n签字日期:年月日"
        ),
        compare_text=(
            "11.4合同执行过程中如发生争议或未尽事项,双方应本着公平、公正的原则协商解决。\n"
            "签字页\n甲方\n法定代表人或授权代表签字:\n"
            "签字日期:2026年05月22日\n乙方\n单位名称:国能日新科技股份有限公司\n(章)\n签字日期:年月日"
        ),
        original_snippet="单位名称:定边县瑞能新能源科技有限公司(章)(章)",
        compare_snippet="20260522(章)",
        original_change_ranges=[
            TextRange(start=67, end=94, highlight_type="DELETE"),
        ],
        compare_change_ranges=[
            TextRange(start=72, end=83, highlight_type="ADD"),
        ],
        original_evidence=[
            EvidenceBox(page_no=13, bbox=BBox(x0=83, y0=140, x1=137, y1=154), text="单位名称:", highlight_type="DELETE"),
            EvidenceBox(page_no=13, bbox=BBox(x0=142, y0=140, x1=286, y1=154), text="定边县瑞能新能源科技有限", highlight_type="DELETE"),
            EvidenceBox(page_no=13, bbox=BBox(x0=83, y0=167, x1=110, y1=183), text="公司", highlight_type="DELETE"),
            EvidenceBox(page_no=13, bbox=BBox(x0=115, y0=167, x1=137, y1=183), text="(章)", highlight_type="DELETE"),
            EvidenceBox(page_no=13, bbox=BBox(x0=328, y0=165, x1=353, y1=184), text="(章)", highlight_type="DELETE"),
        ],
        compare_evidence=[
            EvidenceBox(page_no=13, bbox=BBox(x0=137, y0=513, x1=161, y1=535), text="2026", highlight_type="ADD"),
            EvidenceBox(page_no=13, bbox=BBox(x0=174, y0=513, x1=187, y1=535), text="05", highlight_type="ADD"),
            EvidenceBox(page_no=13, bbox=BBox(x0=203, y0=513, x1=219, y1=535), text="22", highlight_type="ADD"),
            EvidenceBox(page_no=13, bbox=BBox(x0=330, y0=161, x1=353, y1=179), text="(章)", highlight_type="ADD"),
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        review_flags=[
            "CRITICAL_FIELD_CHANGE",
            "CRITICAL_FIELD_DATE_CHANGE",
            "PAGE_UNRELIABLE",
            "READING_ORDER_RISK",
            "SEAL_OR_SIGNATURE_RISK",
            "CRITICAL_VALUE_CHANGE",
        ],
    )

    result = DiffQualityProcessor().process([diff])

    assert len(result.diffs) == 1
    reclassified = result.diffs[0]
    assert reclassified.source_type == "metadata"
    assert reclassified.title == "签署日期"
    assert reclassified.original_snippet == ""
    assert reclassified.compare_snippet == "20260522"
    assert {evidence.text for evidence in reclassified.original_evidence} == set()
    assert {evidence.text for evidence in reclassified.compare_evidence} == {"2026", "05", "22"}
    assert any(decision.action == "trimmed_signing_form_ocr_noise" for decision in result.decisions)


def test_diff_quality_suppresses_single_cjk_ocr_substitution_without_business_change() -> None:
    diff = DiffItem(
        diff_id="D012",
        diff_type="MODIFY",
        source_type="clause",
        original_text="大写:人民币陆万陆仟零叁拾柒元柒角肆分",
        compare_text="大写:人民币陆万陆任零叁拾柒元柒角肆分",
        original_snippet="仟",
        compare_snippet="任",
        match_score=100,
        review_flags=["POSSIBLE_OCR_NOISE"],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D012"
        and decision.detail["reason"] == "single_cjk_ocr_substitution"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_equivalent_range_connector_change() -> None:
    diff = DiffItem(
        diff_id="D014",
        diff_type="MODIFY",
        source_type="clause",
        original_text="根据严重程度扣罚中标方合同价的10%一20%。",
        compare_text="根据严重程度扣罚中标方合同价的10%—20%。",
        original_snippet="一20",
        compare_snippet="—20",
        match_score=100,
        match_score_details={"body_score": 100.0, "business_token_mismatch": 0.0},
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D014"
        and decision.detail["reason"] == "range_connector_equivalent"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_single_latin_edge_noise_but_not_body_letter() -> None:
    edge_noise = DiffItem(
        diff_id="D015",
        diff_type="MODIFY",
        source_type="clause",
        original_text="72小时内提交解决方案。",
        compare_text="72小时内\nB\n提交解决方案。",
        original_snippet="",
        compare_snippet="B",
        match_score=100,
        review_flags=["LAYOUT_MISMATCH_RISK", "OCR_REMEDIATION_PLANNED"],
        quality_status="NEEDS_REVIEW",
        compare_evidence=[
            EvidenceBox(
                page_no=27,
                bbox=BBox(x0=24.7, y0=173.2, x1=27.3, y1=180.3),
                method="char_exact",
                text="B",
                confidence=0.98,
                evidence_quality="HIGH",
            )
        ],
    )
    body_letter = DiffItem(
        diff_id="D020",
        diff_type="ADD",
        source_type="clause",
        compare_text="B. 按进度付款方式。",
        compare_snippet="B",
        match_score=100,
        review_flags=["LAYOUT_MISMATCH_RISK"],
        compare_evidence=[
            EvidenceBox(
                page_no=3,
                bbox=BBox(x0=100, y0=173, x1=110, y1=185),
                method="char_exact",
                text="B",
            )
        ],
    )

    result = DiffQualityProcessor().process([edge_noise, body_letter])

    assert [diff.diff_id for diff in result.diffs] == ["D020"]
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D015"
        and decision.detail["reason"] == "single_latin_layout_glyph_noise"
        for decision in result.decisions
    )


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
