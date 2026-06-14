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


def test_document_preparation_keeps_signature_text_in_separate_section() -> None:
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

    assert [decision.reason for decision in result.decisions] == ["section_classifier", "section_classifier"]
    assert len(clauses) == 2
    assert clauses[0].source_block_ids == ["body"]
    assert clauses[1].section_type == "signature"
    assert clauses[1].source_block_ids == ["sig1", "sig2"]
    assert "签字页 此页无正文" in clauses[1].text
    assert "甲方(盖章) 乙方(盖章) 日期" in clauses[1].text


def test_document_preparation_does_not_mark_real_clause_with_signature_terms_as_signature() -> None:
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


def test_document_preparation_splits_signature_party_line_from_trailing_body_clause() -> None:
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

    assert [clause.section_type for clause in clauses] == ["main_contract", "main_contract", "signature"]
    assert clauses[1].source_block_ids == ["c2"]
    assert clauses[2].source_block_ids == ["sig-party", "sig-date"]


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


def test_clause_splitter_keeps_signature_numeric_address_as_signature_continuation() -> None:
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
                        block_role="signature",
                    ),
                    TextBlock(
                        block_id="sig-address",
                        page_no=1,
                        text="27号金隅智造工场N6",
                        bbox=BBox(x0=50, y0=130, x1=500, y1=160),
                        block_role="signature",
                    ),
                    TextBlock(
                        block_id="sig-contact",
                        page_no=1,
                        text="联系人:刘玉良",
                        bbox=BBox(x0=50, y0=170, x1=500, y1=200),
                        block_role="signature",
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 1
    assert clauses[0].section_type == "signature"
    assert clauses[0].clause_no == ""
    assert "27号金隅智造工场N6" in clauses[0].text


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


def test_diff_quality_suppresses_signature_template_delete() -> None:
    diffs = [
        DiffItem(
            diff_id="D001",
            diff_type="DELETE",
            source_type="clause",
            section_type="signature",
            original_text="法人代表或授权委托人:\n(签字)\n日期:",
            original_snippet="法人代表或授权委托人: (签字) 日期:",
            review_flags=["NON_MAIN_CONTRACT_SECTION"],
        ),
        DiffItem(
            diff_id="D002",
            diff_type="MODIFY",
            source_type="clause",
            section_type="main_contract",
            original_snippet="3%",
            compare_snippet="3‰",
        ),
    ]

    result = DiffQualityProcessor().process(diffs)

    assert [diff.diff_id for diff in result.diffs] == ["D002"]
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D001"
        and decision.detail["reason"] == "signature_template_noise"
        for decision in result.decisions
    )


def test_diff_quality_downgrades_party_contact_table_and_seal_changes() -> None:
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

    assert "CRITICAL_VALUE_CHANGE" not in by_id["D001"].review_flags
    assert by_id["D001"].quality_status == "NEEDS_REVIEW"
    assert "PARTY_INFO_REVIEW" in by_id["D001"].review_flags
    assert "CRITICAL_VALUE_CHANGE" not in by_id["D002"].review_flags
    assert "SEAL_REVIEW" in by_id["D002"].review_flags
    assert "D003" not in by_id


def test_diff_quality_suppresses_non_body_party_line_and_downgrades_body_contact_info() -> None:
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

    assert "D001" not in by_id
    assert by_id["D002"].quality_status == "NEEDS_REVIEW"
    assert "CRITICAL_VALUE_CHANGE" not in by_id["D002"].review_flags
    assert "PARTY_INFO_REVIEW" in by_id["D002"].review_flags


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
