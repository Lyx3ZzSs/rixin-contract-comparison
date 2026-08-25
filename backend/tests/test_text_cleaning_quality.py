from __future__ import annotations

import re
from pathlib import Path

import fitz
import pytest

import app.services.diff.boundary_coverage as boundary_coverage_module
from app.models import BBox, Clause, ClausePair, DiffItem, Document, EvidenceBox, Page, TextBlock, TextRange
from app.services.clause_splitter import ClauseSplitter
from app.services.clause_split_settings import ClauseSplitSettings
from app.services.diff.boundary_coverage import (
    BoundaryCoverageContext,
    ClauseBoundaryCoverageFilter,
    contact_field_coverage_sequences,
)
from app.services.diff_engine import DiffEngine
from app.services.diff_quality import DiffQualityProcessor
from app.services.document_preparation import DocumentPreparer
from app.services.native_heading_repair import NativeHeadingIndex
from app.services.normalizer import TextNormalizer


def test_normalize_for_diff_preserves_decimal_and_version_tokens() -> None:
    normalizer = TextNormalizer()

    assert normalizer.normalize_for_diff("违约金 1.5%") != normalizer.normalize_for_diff("违约金 15%")
    assert normalizer.normalize_for_diff("系统 V1.0") != normalizer.normalize_for_diff("系统 V10")
    assert normalizer.normalize_for_diff("金额 1,000 元") == normalizer.normalize_for_diff("金额 1000 元")
    assert normalizer.normalize_for_diff("日期") == normalizer.normalize_for_diff("日期：")


def test_diff_quality_merges_adjacent_top_cover_annotation_fragments() -> None:
    diffs = [
        DiffItem(
            diff_id="D004",
            diff_type="ADD",
            title="封面额外文本",
            compare_text="Cakilu-7020513",
            compare_snippet="Cakilu-7020513",
            source_type="metadata",
            compare_evidence=[
                EvidenceBox(
                    page_no=1,
                    bbox=BBox(x0=344, y0=4.5, x1=493, y1=58),
                    method="header_footer",
                    text="Cakilu-7020513",
                )
            ],
        ),
        DiffItem(
            diff_id="D006",
            diff_type="ADD",
            title="封面额外文本",
            compare_text="N2o",
            compare_snippet="N2o",
            source_type="metadata",
            compare_evidence=[
                EvidenceBox(
                    page_no=1,
                    bbox=BBox(x0=493.5, y0=7, x1=540, y1=33),
                    method="header_footer",
                    text="N2o",
                )
            ],
        ),
    ]

    result = DiffQualityProcessor().process(diffs)

    assert len(result.diffs) == 1
    assert result.diffs[0].compare_text == "Cakilu-7020513N2o"
    assert len(result.diffs[0].compare_evidence) == 2


def test_normalize_for_diff_equates_document_number_year_bracket_styles() -> None:
    normalizer = TextNormalizer()

    canonical = normalizer.normalize_for_diff("电监安全〔2006〕34号")

    assert normalizer.normalize_for_diff("电监安全（2006）34号") == canonical
    assert normalizer.normalize_for_diff("电监安全(2006)34号") == canonical


def test_diff_engine_ignores_document_number_year_bracket_style_change() -> None:
    normalizer = TextNormalizer()
    original_text = "（7）《电力二次系统安全防护总体方案》电监安全（2006）34号。"
    compare_text = "（7）《电力二次系统安全防护总体方案》电监安全〔2006〕34号。"

    diffs = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(
                    clause_id="O001",
                    text=original_text,
                    normalized_text=normalizer.normalize_for_diff(original_text),
                ),
                compare=Clause(
                    clause_id="N001",
                    text=compare_text,
                    normalized_text=normalizer.normalize_for_diff(compare_text),
                ),
            )
        ]
    )

    assert diffs == []


def test_diff_engine_detects_document_number_year_change() -> None:
    normalizer = TextNormalizer()
    original_text = "电监安全〔2006〕34号"
    compare_text = "电监安全〔2007〕34号"

    diffs = DiffEngine().build_diffs(
        [
            ClausePair(
                original=Clause(
                    clause_id="O001",
                    text=original_text,
                    normalized_text=normalizer.normalize_for_diff(original_text),
                ),
                compare=Clause(
                    clause_id="N001",
                    text=compare_text,
                    normalized_text=normalizer.normalize_for_diff(compare_text),
                ),
            )
        ]
    )

    assert len(diffs) == 1
    assert diffs[0].original_snippet == "2006"
    assert diffs[0].compare_snippet == "2007"


def test_document_preparer_excludes_hyphenated_page_number_from_cross_page_clause() -> None:
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
                        block_id="heading",
                        page_no=1,
                        text="5.4 担保范围：包括但不限于以下损失和费用：",
                        bbox=BBox(x0=80, y0=620, x1=530, y1=640),
                    ),
                    TextBlock(
                        block_id="loss",
                        page_no=1,
                        text="甲方因乙方安全事故遭受的直接及间接损失；",
                        bbox=BBox(x0=80, y0=730, x1=530, y1=750),
                    ),
                    TextBlock(
                        block_id="page-number",
                        page_no=1,
                        text="-24-",
                        block_type="number",
                        bbox=BBox(x0=280, y0=785, x1=320, y1=800),
                    ),
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="continuation-one",
                        page_no=2,
                        text="乙方未按约定计提和使用安全生产专项费用导致的甲方额外支出；",
                        bbox=BBox(x0=80, y0=80, x1=530, y1=100),
                    ),
                    TextBlock(
                        block_id="continuation-two",
                        page_no=2,
                        text="乙方违反本协议约定应向甲方支付的违约金、赔偿金等。",
                        bbox=BBox(x0=80, y0=120, x1=530, y1=140),
                    ),
                    TextBlock(
                        block_id="next-heading",
                        page_no=2,
                        text="5.5 索赔条件：",
                        bbox=BBox(x0=80, y0=170, x1=300, y1=190),
                    ),
                ],
            ),
        ],
    )

    DocumentPreparer().prepare(document, "compare")
    clauses = ClauseSplitter().split(document, "N")

    assert document.pages[0].blocks[2].block_role == "page_footer"
    assert [clause.clause_no for clause in clauses] == ["5.4", "5.5"]
    assert "-24-" not in clauses[0].text
    assert "乙方未按约定计提和使用安全生产专项费用导致的甲方额外支出;" in clauses[0].text
    assert "乙方违反本协议约定应向甲方支付的违约金、赔偿金等。" in clauses[0].text


def test_clause_splitter_ignores_low_confidence_bottom_edge_chinese_number_noise() -> None:
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
                        block_id="heading",
                        page_no=1,
                        text="5.4 担保范围：包括但不限于以下损失和费用：",
                        bbox=BBox(x0=80, y0=620, x1=530, y1=640),
                    ),
                    TextBlock(
                        block_id="loss",
                        page_no=1,
                        text="甲方因乙方安全事故遭受的直接及间接损失；",
                        bbox=BBox(x0=80, y0=730, x1=530, y1=750),
                    ),
                    TextBlock(
                        block_id="ocr-noise",
                        page_no=1,
                        text="二",
                        confidence=0.64,
                        bbox=BBox(x0=9, y0=796, x1=79, y1=830),
                    ),
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="continuation-one",
                        page_no=2,
                        text="乙方未按约定计提和使用安全生产专项费用导致的甲方额外支出；",
                        bbox=BBox(x0=80, y0=80, x1=530, y1=100),
                    ),
                    TextBlock(
                        block_id="continuation-two",
                        page_no=2,
                        text="乙方违反本协议约定应向甲方支付的违约金、赔偿金等。",
                        bbox=BBox(x0=80, y0=120, x1=530, y1=140),
                    ),
                    TextBlock(
                        block_id="next-heading",
                        page_no=2,
                        text="5.5 索赔条件：",
                        bbox=BBox(x0=80, y0=170, x1=300, y1=190),
                    ),
                ],
            ),
        ],
    )

    clauses = ClauseSplitter().split(document, "N")

    assert [clause.clause_no for clause in clauses] == ["5.4", "5.5"]
    assert "二" not in clauses[0].text
    assert "乙方未按约定计提和使用安全生产专项费用导致的甲方额外支出;" in clauses[0].text
    assert "乙方违反本协议约定应向甲方支付的违约金、赔偿金等。" in clauses[0].text


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
                    TextBlock(
                        block_id="sig1", page_no=2, text="签字页 此页无正文", bbox=BBox(x0=50, y0=80, x1=500, y1=110)
                    ),
                    TextBlock(
                        block_id="sig2",
                        page_no=2,
                        text="甲方（盖章） 乙方（盖章） 日期",
                        bbox=BBox(x0=50, y0=130, x1=500, y1=180),
                    ),
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


def test_clause_splitter_does_not_treat_numeric_term_as_clause_number() -> None:
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
                        block_id="title",
                        page_no=1,
                        text="服务要求",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=110),
                        block_type="paragraph_title",
                    ),
                    TextBlock(
                        block_id="body",
                        page_no=1,
                        text="15日内完成系统部署并提交验收材料。",
                        bbox=BBox(x0=70, y0=125, x1=520, y1=155),
                    ),
                    TextBlock(
                        block_id="next",
                        page_no=1,
                        text="2. 付款方式",
                        bbox=BBox(x0=50, y0=200, x1=500, y1=230),
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["", "2"]
    assert clauses[0].source_block_ids == ["title", "body"]
    assert "15日内完成系统部署" in clauses[0].text


def test_clause_splitter_keeps_short_top_level_title_blocks_independent() -> None:
    document = Document(
        filename="short-headings.pdf",
        path="short-headings.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="c124",
                        page_no=1,
                        text="12.4 双方协商解决。",
                        bbox=BBox(x0=70, y0=80, x1=500, y1=105),
                    ),
                    TextBlock(
                        block_id="h13",
                        page_no=1,
                        text="13. 索赔",
                        bbox=BBox(x0=70, y0=130, x1=150, y1=154),
                        block_type="paragraph_title",
                    ),
                    TextBlock(
                        block_id="c131",
                        page_no=1,
                        text="13.1 甲方有权提出索赔。",
                        bbox=BBox(x0=72, y0=170, x1=500, y1=195),
                    ),
                    TextBlock(
                        block_id="h17",
                        page_no=1,
                        text="17. 合同生效",
                        bbox=BBox(x0=70, y0=230, x1=180, y1=254),
                        block_type="paragraph_title",
                    ),
                    TextBlock(
                        block_id="c171",
                        page_no=1,
                        text="(1)双方签字盖章。",
                        bbox=BBox(x0=92, y0=270, x1=500, y1=295),
                    ),
                    TextBlock(
                        block_id="h18",
                        page_no=1,
                        text="18. 份数",
                        bbox=BBox(x0=70, y0=330, x1=150, y1=354),
                        block_type="paragraph_title",
                    ),
                    TextBlock(
                        block_id="c18",
                        page_no=1,
                        text="本合同一式伍份。",
                        bbox=BBox(x0=92, y0=370, x1=500, y1=395),
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "N")

    assert "13" in [item.clause_no for item in clauses]
    assert "18" in [item.clause_no for item in clauses]
    assert next(item for item in clauses if item.clause_no == "13").title == "索赔"
    assert next(item for item in clauses if item.clause_no == "18").title == "份数"


def test_clause_splitter_uses_native_repair_semantics_for_ordinary_text_heading() -> None:
    repaired_heading = TextBlock(
        block_id="h18",
        page_no=1,
        text="18. 份数",
        bbox=BBox(x0=70, y0=180, x1=150, y1=204),
        block_type="text",
        semantic_reasons=["native_heading_repair:18"],
    )
    document = Document(
        filename="semantic-heading.pdf",
        path="semantic-heading.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="h17",
                        page_no=1,
                        text="17. 合同生效",
                        bbox=BBox(x0=70, y0=80, x1=180, y1=104),
                        block_type="paragraph_title",
                    ),
                    TextBlock(
                        block_id="b17",
                        page_no=1,
                        text="双方签字盖章后生效。",
                        bbox=BBox(x0=92, y0=120, x1=500, y1=144),
                    ),
                    repaired_heading,
                    TextBlock(
                        block_id="b18",
                        page_no=1,
                        text="本合同一式伍份。",
                        bbox=BBox(x0=92, y0=220, x1=500, y1=244),
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert repaired_heading.block_type == "text"
    assert [item.clause_no for item in clauses] == ["17", "18"]
    assert clauses[1].title == "份数"
    assert "native_heading_repair" in clauses[1].segmentation_reason
    assert "WEAK_NUMERIC_MARKER" in clauses[1].split_flags


@pytest.mark.parametrize(
    ("text", "block_type", "semantic_reason"),
    [
        ("18. 100万元", "text", "native_heading_repair:18"),
        ("18. 100份", "text", "native_heading_repair:18"),
        ("18. 份数", "table", "native_heading_repair:18"),
    ],
)
def test_clause_splitter_native_reason_does_not_bypass_guarded_shapes(
    text: str,
    block_type: str,
    semantic_reason: str,
) -> None:
    document = Document(
        filename="guarded-native-heading.pdf",
        path="guarded-native-heading.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="h17",
                        page_no=1,
                        text="17. 合同生效",
                        bbox=BBox(x0=70, y0=80, x1=180, y1=104),
                        block_type="paragraph_title",
                    ),
                    TextBlock(
                        block_id="guarded",
                        page_no=1,
                        text=text,
                        bbox=BBox(x0=70, y0=130, x1=300, y1=154),
                        block_type=block_type,
                        semantic_reasons=[semantic_reason],
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [item.clause_no for item in clauses] == ["17"]


def test_clause_heading_native_reason_date_like_risk_stays_below_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    splitter = ClauseSplitter()
    unit = TextBlock(
        block_id="date-like",
        page_no=1,
        text="2026/07",
        bbox=BBox(x0=70, y0=80, x1=150, y1=104),
        semantic_reasons=["native_heading_repair:2026"],
    )
    monkeypatch.setattr(
        splitter.heading_detector,
        "is_quantity_or_amount_marker",
        lambda _text, _marker: False,
    )

    candidate = splitter.heading_detector.candidate(unit, ("2026", "07"), None)

    assert candidate is not None
    assert "native_heading_repair" in candidate.signals
    assert "DATE_LIKE_HEADING" in candidate.risk_flags
    assert candidate.score < splitter.heading_accept_score


def test_clause_splitter_native_reason_does_not_bypass_global_acceptance_threshold() -> None:
    document = Document(
        filename="threshold-native-heading.pdf",
        path="threshold-native-heading.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="h17",
                        page_no=1,
                        text="17. 合同生效",
                        bbox=BBox(x0=70, y0=80, x1=180, y1=104),
                        block_type="paragraph_title",
                    ),
                    TextBlock(
                        block_id="h18",
                        page_no=1,
                        text="18. 份数",
                        bbox=BBox(x0=70, y0=130, x1=150, y1=154),
                        semantic_reasons=["native_heading_repair:18"],
                    ),
                ],
            )
        ],
    )
    splitter = ClauseSplitter(split_settings=ClauseSplitSettings(heading_accept_score=0.71))

    clauses = splitter.split(document, "O")

    assert [item.clause_no for item in clauses] == ["17"]


def test_clause_splitter_keeps_short_numeric_values_outside_title_blocks() -> None:
    document = Document(
        filename="values.pdf",
        path="values.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="h17",
                        page_no=1,
                        text="17. 合同生效",
                        bbox=BBox(x0=70, y0=80, x1=180, y1=104),
                        block_type="paragraph_title",
                    ),
                    TextBlock(
                        block_id="v1",
                        page_no=1,
                        text="(2)1",
                        bbox=BBox(x0=92, y0=120, x1=130, y1=144),
                    ),
                    TextBlock(
                        block_id="v2",
                        page_no=1,
                        text="18份",
                        bbox=BBox(x0=92, y0=160, x1=140, y1=184),
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "N")

    assert [item.clause_no for item in clauses] == ["17"]
    assert "18份" in clauses[0].text


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
    assert "amount:1000" in clause.clause_key
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

    for token in ("n3_1", "amount:1000", "date:2026-06-30"):
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
                        text=("3.2 本合同项下产品应符合其产品说明书或包装上注明采用的产品质量标准。"),
                        bbox=BBox(x0=50, y0=150, x1=500, y1=180),
                    ),
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
                    TextBlock(
                        block_id="p1",
                        page_no=1,
                        text="甲方提供风功率预测服务",
                        bbox=BBox(x0=70, y0=116, x1=500, y1=146),
                    ),
                    TextBlock(
                        block_id="p2", page_no=1, text="并负责系统日常维护。", bbox=BBox(x0=70, y0=152, x1=500, y1=182)
                    ),
                    TextBlock(block_id="h2", page_no=1, text="2. 付款", bbox=BBox(x0=50, y0=220, x1=500, y1=250)),
                    TextBlock(
                        block_id="p3", page_no=1, text="乙方按月付款。", bbox=BBox(x0=70, y0=256, x1=500, y1=286)
                    ),
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
                    TextBlock(
                        block_id="body", page_no=1, text="甲方提供服务。", bbox=BBox(x0=70, y0=120, x1=500, y1=150)
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert len(clauses) == 1
    assert clauses[0].clause_no == "1"
    assert clauses[0].title == "服务范围"
    assert clauses[0].source_block_ids == ["number", "title", "body"]


def test_clause_splitter_merges_bare_number_with_following_paragraph_title() -> None:
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
                    TextBlock(
                        block_id="title",
                        page_no=1,
                        text="服务范围",
                        bbox=BBox(x0=80, y0=80, x1=180, y1=110),
                        block_type="paragraph_title",
                    ),
                    TextBlock(
                        block_id="body", page_no=1, text="甲方提供服务。", bbox=BBox(x0=70, y0=120, x1=500, y1=150)
                    ),
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
                    TextBlock(
                        block_id="t1",
                        page_no=1,
                        text="1. 技术服务项目概要......3",
                        bbox=BBox(x0=50, y0=90, x1=500, y1=110),
                    ),
                    TextBlock(
                        block_id="t2",
                        page_no=1,
                        text="2. 技术服务具体要求..3",
                        bbox=BBox(x0=50, y0=120, x1=500, y1=140),
                    ),
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
                    TextBlock(
                        block_id="chapter", page_no=1, text="第一章 总则", bbox=BBox(x0=50, y0=60, x1=500, y1=90)
                    ),
                    TextBlock(
                        block_id="article", page_no=1, text="第一条 服务范围", bbox=BBox(x0=50, y0=100, x1=500, y1=130)
                    ),
                    TextBlock(
                        block_id="sub", page_no=1, text="1.1 平台维护服务", bbox=BBox(x0=50, y0=140, x1=500, y1=170)
                    ),
                    TextBlock(
                        block_id="body",
                        page_no=1,
                        text="乙方负责平台日常维护。",
                        bbox=BBox(x0=70, y0=180, x1=500, y1=210),
                    ),
                    TextBlock(
                        block_id="next", page_no=1, text="第二条 付款方式", bbox=BBox(x0=50, y0=220, x1=500, y1=250)
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.clause_no for clause in clauses] == ["第一章", "第一条", "1.1", "第二条"]
    assert clauses[2].section_path == ["第一章 总则", "第一条 服务范围", "1.1 平台维护服务"]
    assert "heading_score" in clauses[2].segmentation_reason
    assert "乙方负责平台日常维护" in clauses[2].text


def test_clause_splitter_keeps_chapter_context_in_duplicate_article_keys() -> None:
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
                    TextBlock(block_id="c1", page_no=1, text="第一章 总则", bbox=BBox(x0=50, y0=60, x1=500, y1=90)),
                    TextBlock(block_id="a1", page_no=1, text="第一条 定义", bbox=BBox(x0=50, y0=100, x1=500, y1=130)),
                    TextBlock(block_id="s1", page_no=1, text="1.1 服务内容", bbox=BBox(x0=50, y0=140, x1=500, y1=170)),
                    TextBlock(
                        block_id="c2", page_no=1, text="第二章 商务条款", bbox=BBox(x0=50, y0=220, x1=500, y1=250)
                    ),
                    TextBlock(block_id="a2", page_no=1, text="第一条 定义", bbox=BBox(x0=50, y0=260, x1=500, y1=290)),
                    TextBlock(block_id="s2", page_no=1, text="1.1 服务内容", bbox=BBox(x0=50, y0=300, x1=500, y1=330)),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert clauses[2].section_path == ["第一章 总则", "第一条 定义", "1.1 服务内容"]
    assert clauses[5].section_path == ["第二章 商务条款", "第一条 定义", "1.1 服务内容"]
    assert clauses[2].clause_key != clauses[5].clause_key


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

    assert len(clauses) == 2
    assert all(clause.section_type == "main_contract" for clause in clauses)
    assert clauses[0].clause_no == ""
    assert "27号金隅智造工场N6" in clauses[0].text
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
    assert clauses[1].clause_no == ""
    assert "27号金隅智造工场N6" in clauses[1].text


def test_clause_splitter_preserves_substantive_preamble_before_first_article() -> None:
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
                        block_id="preamble",
                        page_no=1,
                        text="鉴于甲方需要采购风功率预测服务，乙方具备相应资质。",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=110),
                    ),
                    TextBlock(
                        block_id="article",
                        page_no=1,
                        text="第一条 服务范围",
                        bbox=BBox(x0=50, y0=130, x1=500, y1=160),
                    ),
                    TextBlock(
                        block_id="body",
                        page_no=1,
                        text="乙方提供系统服务。",
                        bbox=BBox(x0=70, y0=170, x1=500, y1=200),
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert clauses[0].source_block_ids == ["preamble"]
    assert "鉴于甲方需要采购" in clauses[0].text
    assert clauses[1].clause_no == "第一条"


def test_clause_splitter_preserves_substantive_preamble_after_cover_title() -> None:
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
                        block_id="cover-title",
                        page_no=1,
                        text="采购合同",
                        bbox=BBox(x0=180, y0=40, x1=360, y1=70),
                        block_type="doc_title",
                    ),
                    TextBlock(
                        block_id="preamble",
                        page_no=1,
                        text="鉴于甲方需要采购风功率预测服务，乙方具备相应资质。",
                        bbox=BBox(x0=50, y0=90, x1=520, y1=115),
                    ),
                    TextBlock(
                        block_id="article",
                        page_no=1,
                        text="第一条 服务范围",
                        bbox=BBox(x0=50, y0=140, x1=220, y1=160),
                    ),
                    TextBlock(
                        block_id="body",
                        page_no=1,
                        text="乙方提供系统服务。",
                        bbox=BBox(x0=70, y0=180, x1=420, y1=200),
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert clauses[0].source_block_ids == ["preamble"]
    assert "鉴于甲方需要采购" in clauses[0].text
    assert clauses[1].clause_no == "第一条"


def test_clause_splitter_repairs_cross_line_reading_order_backtrack_without_layout_order() -> None:
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
                        block_id="article-1",
                        page_no=1,
                        text="第一条 服务范围",
                        bbox=BBox(x0=50, y0=80, x1=220, y1=100),
                        reading_order=1,
                    ),
                    TextBlock(
                        block_id="article-1-body",
                        page_no=1,
                        text="乙方提供系统服务。",
                        bbox=BBox(x0=70, y0=120, x1=420, y1=140),
                        reading_order=3,
                    ),
                    TextBlock(
                        block_id="article-2",
                        page_no=1,
                        text="第二条 保密义务",
                        bbox=BBox(x0=50, y0=200, x1=220, y1=220),
                        reading_order=2,
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.source_block_ids for clause in clauses] == [
        ["article-1", "article-1-body"],
        ["article-2"],
    ]
    assert "READING_ORDER_REPAIRED" in clauses[0].split_flags
    assert "乙方提供系统服务" in clauses[0].text
    assert "乙方提供系统服务" not in clauses[1].text


def test_clause_splitter_filters_short_bottom_ocr_fragment_from_clause_body() -> None:
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
                        block_id="article",
                        page_no=1,
                        text="第一条 服务范围",
                        bbox=BBox(x0=50, y0=100, x1=220, y1=120),
                        reading_order=1,
                    ),
                    TextBlock(
                        block_id="body",
                        page_no=1,
                        text="乙方提供系统服务。",
                        bbox=BBox(x0=70, y0=140, x1=420, y1=160),
                        reading_order=2,
                    ),
                    TextBlock(
                        block_id="bottom-fragment",
                        page_no=1,
                        text="A1",
                        bbox=BBox(x0=285, y0=815, x1=330, y1=828),
                        block_type="ocr_line",
                        confidence=0.95,
                        reading_order=3,
                    ),
                ],
            )
        ],
    )

    clauses = ClauseSplitter().split(document, "O")

    assert clauses[0].source_block_ids == ["article", "body"]
    assert "A1" not in clauses[0].text


def test_document_preparer_stops_appendix_section_on_formal_main_clause_heading() -> None:
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
                        block_id="appendix-title",
                        page_no=1,
                        text="附件一 技术规范",
                        bbox=BBox(x0=50, y0=80, x1=220, y1=100),
                        layout_order=1,
                    ),
                    TextBlock(
                        block_id="appendix-body",
                        page_no=1,
                        text="设备应满足接口开放要求。",
                        bbox=BBox(x0=70, y0=120, x1=420, y1=140),
                        layout_order=2,
                    ),
                    TextBlock(
                        block_id="main-heading",
                        page_no=1,
                        text="第十五条 争议解决",
                        bbox=BBox(x0=50, y0=180, x1=240, y1=200),
                        layout_order=3,
                    ),
                    TextBlock(
                        block_id="main-body",
                        page_no=1,
                        text="双方协商不成的，提交法院处理。",
                        bbox=BBox(x0=70, y0=220, x1=480, y1=240),
                        layout_order=4,
                    ),
                ],
            )
        ],
    )

    DocumentPreparer().prepare(document, "original")
    clauses = ClauseSplitter().split(document, "O")

    assert [clause.section_type for clause in clauses] == ["appendix", "main_contract"]
    assert clauses[1].source_block_ids == ["main-heading", "main-body"]


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


def _write_quality_heading_pdf(path: Path, heading: str, *, page_no: int = 1, page_count: int | None = None) -> None:
    pdf = fitz.open()
    total_pages = max(page_count or page_no, page_no)
    for current_page in range(1, total_pages + 1):
        page = pdf.new_page(width=595, height=842)
        if current_page == page_no:
            page.insert_text((72, 96), heading, fontname="china-s", fontsize=12)
    pdf.save(path)
    pdf.close()


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
    assert any(
        decision.action == "suppressed_low_value_noise" and decision.diff_id == "D001" for decision in result.decisions
    )


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
        original_text=("35,质保金5\nA. 滚动付款方式。付款条件为乙方将产品送至我方指定地点。"),
        compare_text=("35,质保金5\n_】\nA. 滚动付款方式。付款条件为乙方将产品送至我方指定地点。"),
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


def test_diff_quality_suppresses_page_number_with_edge_annotation_from_clause_body() -> None:
    diff = DiffItem(
        diff_id="D054",
        diff_type="MODIFY",
        source_type="clause",
        original_text="2.4 甲方有权通过日常巡查、专项检查、随机抽查等方式,监督。\n—14—",
        compare_text="2.4 甲方有权通过日常巡查、专项检查、随机抽查等方式,监督。\n-14-\n黄科",
        original_snippet="—14—",
        compare_snippet="-14- 黄科",
        match_score=100,
        structural_flags=["PARAGRAPH_MERGED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        quality_status="NEEDS_REVIEW",
        compare_evidence=[
            EvidenceBox(
                page_no=15,
                bbox=BBox(x0=500, y0=790, x1=560, y1=820),
                method="char_exact",
                text="黄科",
                confidence=0.98,
            )
        ],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D054"
        and decision.detail["reason"] == "page_number_edge_annotation_noise"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_standalone_edge_annotation_from_clause_body() -> None:
    diff = DiffItem(
        diff_id="D035",
        diff_type="MODIFY",
        source_type="clause",
        original_text="5.1 甲方义务",
        compare_text="5.1 甲方义务\n黄科",
        original_snippet="",
        compare_snippet="黄科",
        match_score=100,
        review_flags=[
            "LOW_CONFIDENCE_MATCH",
            "POSSIBLE_CLAUSE_MISMATCH",
            "READING_ORDER_RISK",
            "OCR_REMEDIATION_PLANNED",
            "POSSIBLE_OCR_NOISE",
        ],
        quality_status="NEEDS_REVIEW",
        compare_evidence=[
            EvidenceBox(
                page_no=4,
                bbox=BBox(x0=505, y0=792, x1=560, y1=820),
                method="char_exact",
                text="黄科",
                confidence=0.98,
            )
        ],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D035"
        and decision.detail["reason"] == "edge_annotation_clause_noise"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_page_footer_annotation_even_with_long_original_context() -> None:
    diff = DiffItem(
        diff_id="D063",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="4.4",
        title="因乙方未履行安全管理责任",
        original_text="4.4 因乙方未履行安全管理责任。\n—23—\n担全部法律责任。",
        compare_text="4.4 因乙方未履行安全管理责任。\n-23-\n黄科",
        original_snippet="—23—担全部法律责任",
        compare_snippet="-23-黄科",
        structural_flags=["PARAGRAPH_MERGED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        quality_status="NEEDS_REVIEW",
        original_evidence=[
            EvidenceBox(page_no=24, bbox=BBox(x0=288, y0=784, x1=320, y1=800), text="—23—"),
            EvidenceBox(page_no=25, bbox=BBox(x0=65, y0=75, x1=178, y1=91), text="担全部法律责任"),
        ],
        compare_evidence=[
            EvidenceBox(page_no=24, bbox=BBox(x0=258, y0=777, x1=284, y1=793), text="-23-"),
            EvidenceBox(page_no=24, bbox=BBox(x0=448, y0=775, x1=502, y1=813), text="黄科"),
        ],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D063"
        and decision.detail["reason"] == "page_number_edge_annotation_noise"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_incomplete_page_marker_with_edge_annotation() -> None:
    diff = DiffItem(
        diff_id="D071",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="第一条",
        title="和",
        original_text="第一条和\n—30",
        compare_text="第一条和\n-30\n黄科",
        original_snippet="—30",
        compare_snippet="-30黄科",
        structural_flags=["PARAGRAPH_MERGED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        quality_status="NEEDS_REVIEW",
        original_evidence=[
            EvidenceBox(page_no=31, bbox=BBox(x0=283, y0=781, x1=302, y1=795), text="—30"),
        ],
        compare_evidence=[
            EvidenceBox(page_no=31, bbox=BBox(x0=282, y0=781, x1=301, y1=797), text="-30"),
            EvidenceBox(page_no=31, bbox=BBox(x0=395, y0=790, x1=454, y1=830), text="黄科"),
        ],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D071"
        and decision.detail["reason"] == "page_number_edge_annotation_noise"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_signature_footer_noise_inside_clause() -> None:
    diff = DiffItem(
        diff_id="D074",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="15.3",
        title="在争议解决期间,合同中未涉及争议部分的条款仍须履行。",
        original_text="15.3 在争议解决期间,合同中未涉及争议部分的条款仍须履行。\n参与人员:",
        compare_text="15.3 在争议解决期间,合同中未涉及争议部分的条款仍须履行。\n共评静\n44意\n黄科",
        original_snippet="参与人员:",
        compare_snippet="共评静44意黄科",
        structural_flags=["PUNCTUATED_HEADING"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        quality_status="NEEDS_REVIEW",
        compare_evidence=[
            EvidenceBox(page_no=34, bbox=BBox(x0=135, y0=439, x1=244, y1=488), text="共评静"),
            EvidenceBox(page_no=34, bbox=BBox(x0=99, y0=778, x1=154, y1=821), text="44意"),
            EvidenceBox(page_no=34, bbox=BBox(x0=439, y0=783, x1=494, y1=821), text="黄科"),
        ],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D074"
        and decision.detail["reason"] == "edge_annotation_clause_noise"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_short_clause_delete_covered_by_opposite_page_text() -> None:
    original_clause = _quality_clause(
        "OC109",
        "1.1 不发生人身轻伤以上事故。",
        order_index=109,
        page_no=14,
        split_flags=["PUNCTUATED_HEADING"],
    )
    compare_document = _quality_document(
        14,
        "1 安全目标\n1.1 不发生人身轻伤以上事故。\n1.2 不发生一般及以上设备事故。",
    )
    diff = DiffItem(
        diff_id="D100",
        diff_type="DELETE",
        source_type="clause",
        original_clause_id="OC109",
        clause_no="1.1",
        title="不发生人身轻伤以上事故。",
        original_text=original_clause.text,
        original_snippet=original_clause.text,
        structural_flags=["PUNCTUATED_HEADING"],
        review_flags=["SHORT_CLAUSE_MATCH_REVIEW", "CRITICAL_VALUE_CHANGE"],
        original_evidence=original_clause.bboxes,
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_document=compare_document,
    )

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D100"
        and decision.detail["reason"] == "changed_text_covered_by_opposite_page_text"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_heading_add_covered_by_original_page_text() -> None:
    compare_clause = _quality_clause(
        "NC111",
        "服务内容概述",
        side_prefix="N",
        order_index=12,
        page_no=3,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    original_document = _quality_document(
        3,
        "乙方应按合同约定向甲方提供以下技术服务:\n"
        "3. 服务内容概述\n"
        "3.1 乙方提供服务的期限为合同签订后一年。\n"
        "4. 合同价格及支付",
    )
    diff = DiffItem(
        diff_id="D111",
        diff_type="ADD",
        source_type="clause",
        compare_clause_id="NC111",
        title="服务内容概述",
        compare_text="服务内容概述",
        compare_snippet="服务内容概述",
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        compare_evidence=compare_clause.bboxes,
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process(
        [diff],
        compare_clauses=[compare_clause],
        original_document=original_document,
    )

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D111"
        and decision.detail["reason"] == "changed_text_covered_by_opposite_page_text"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_heading_add_when_original_has_bare_number_and_child_clause() -> None:
    compare_heading = _quality_clause(
        "NC111",
        "服务内容概述",
        side_prefix="N",
        order_index=12,
        page_no=3,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    compare_child = _quality_clause(
        "NC112",
        "3.1 乙方提供服务的期限为合同签订后一年。",
        side_prefix="N",
        order_index=13,
        page_no=3,
    )
    compare_child.clause_no = "3.1"
    original_document = _quality_document(
        3,
        "在执行合同过程中如发现有任何漏项和短缺。\n"
        "3.\n"
        "3.1 乙方提供服务的期限为合同签订后一年。\n"
        "3.2 乙方应按以下进度计划开展服务工作。",
    )
    diff = DiffItem(
        diff_id="D111",
        diff_type="ADD",
        source_type="clause",
        compare_clause_id="NC111",
        title="服务内容概述",
        compare_text="服务内容概述",
        compare_snippet="服务内容概述",
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        compare_evidence=compare_heading.bboxes,
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process(
        [diff],
        compare_clauses=[compare_heading, compare_child],
        original_document=original_document,
    )

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D111"
        and decision.detail["reason"] == "heading_add_covered_by_opposite_numbering"
        for decision in result.decisions
    )


def test_diff_quality_keeps_critical_heading_add_with_bare_number_and_child_clause() -> None:
    compare_heading = _quality_clause(
        "NC_CRITICAL_HEADING_ADD",
        "2. 违约责任",
        side_prefix="N",
        order_index=12,
        page_no=3,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    compare_heading.clause_no = "2"
    compare_child = _quality_clause(
        "NC_CRITICAL_HEADING_CHILD",
        "2.1 任何一方均应按合同约定履行义务。",
        side_prefix="N",
        order_index=13,
        page_no=3,
    )
    compare_child.clause_no = "2.1"
    original_document = _quality_document(
        3,
        "1.9 条款正文。\n2.\n2.1 任何一方均应按合同约定履行义务。",
    )
    diff = DiffItem(
        diff_id="D111_CRITICAL_HEADING_ADD",
        diff_type="ADD",
        source_type="clause",
        compare_clause_id="NC_CRITICAL_HEADING_ADD",
        title="违约责任",
        compare_text="2. 违约责任",
        compare_snippet="2. 违约责任",
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        compare_evidence=compare_heading.bboxes,
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process(
        [diff],
        compare_clauses=[compare_heading, compare_child],
        original_document=original_document,
    )

    assert [item.diff_id for item in result.diffs] == ["D111_CRITICAL_HEADING_ADD"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "heading_add_covered_by_opposite_numbering"
        for decision in result.decisions
    )


def test_diff_quality_keeps_critical_flagged_material_heading_add_with_bare_number_and_child_clause() -> None:
    compare_heading = _quality_clause(
        "NC_MATERIAL_HEADING_ADD",
        "保密义务",
        side_prefix="N",
        order_index=12,
        page_no=3,
        split_flags=["READING_ORDER_REPAIRED"],
    )
    compare_heading.clause_no = "2"
    compare_child = _quality_clause(
        "NC_MATERIAL_HEADING_CHILD",
        "2.1 任何一方均应按合同约定履行义务。",
        side_prefix="N",
        order_index=13,
        page_no=3,
    )
    compare_child.clause_no = "2.1"
    original_document = _quality_document(
        3,
        "1.9 条款正文。\n2.\n2.1 任何一方均应按合同约定履行义务。",
    )
    diff = DiffItem(
        diff_id="D111_MATERIAL_HEADING_ADD",
        diff_type="ADD",
        source_type="clause",
        compare_clause_id="NC_MATERIAL_HEADING_ADD",
        title="保密义务",
        compare_text="保密义务",
        compare_snippet="保密义务",
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        compare_evidence=compare_heading.bboxes,
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process(
        [diff],
        compare_clauses=[compare_heading, compare_child],
        original_document=original_document,
    )

    assert [item.diff_id for item in result.diffs] == ["D111_MATERIAL_HEADING_ADD"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "heading_add_covered_by_opposite_numbering"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_high_risk_heading_add_with_exact_native_evidence(tmp_path: Path) -> None:
    path = tmp_path / "original.pdf"
    _write_quality_heading_pdf(path, "8. 知识产权")
    original_document = _quality_document(1, "8.\n8.1 甲方拥有工作成果。")
    original_document.path = str(path)
    original_child = _quality_clause("OC081", "8.1 甲方拥有工作成果。", order_index=2)
    original_child.clause_no = "8.1"
    compare_heading = _quality_clause(
        "NC080", "8. 知识产权", side_prefix="N", order_index=1, split_flags=["READING_ORDER_REPAIRED"]
    )
    compare_heading.clause_no = "8"
    compare_heading.title = "知识产权"
    compare_child = _quality_clause("NC081", "8.1 甲方拥有工作成果。", side_prefix="N", order_index=2)
    compare_child.clause_no = "8.1"
    diff = DiffItem(
        diff_id="D_NATIVE_ADD",
        diff_type="ADD",
        source_type="clause",
        compare_clause_id="NC080",
        clause_no="8",
        title="知识产权",
        compare_text="8. 知识产权",
        compare_snippet="8. 知识产权",
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
        compare_evidence=compare_heading.bboxes,
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_child],
        compare_clauses=[compare_heading, compare_child],
        original_document=original_document,
    )

    assert result.diffs == []
    assert any(item.detail.get("reason") == "heading_add_covered_by_native_heading" for item in result.decisions)


def test_diff_quality_keeps_high_risk_heading_add_without_exact_native_evidence(tmp_path: Path) -> None:
    path = tmp_path / "original.pdf"
    _write_quality_heading_pdf(path, "8. 保密")
    original_document = _quality_document(1, "8.\n8.1 甲方拥有工作成果。")
    original_document.path = str(path)
    original_child = _quality_clause("OC081", "8.1 甲方拥有工作成果。", order_index=2)
    original_child.clause_no = "8.1"
    compare_heading = _quality_clause(
        "NC080", "8. 知识产权", side_prefix="N", order_index=1, split_flags=["READING_ORDER_REPAIRED"]
    )
    compare_heading.clause_no = "8"
    compare_heading.title = "知识产权"
    compare_child = _quality_clause("NC081", "8.1 甲方拥有工作成果。", side_prefix="N", order_index=2)
    compare_child.clause_no = "8.1"
    diff = DiffItem(
        diff_id="D_NATIVE_ADD_KEEP",
        diff_type="ADD",
        source_type="clause",
        compare_clause_id="NC080",
        clause_no="8",
        title="知识产权",
        compare_text="8. 知识产权",
        compare_snippet="8. 知识产权",
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
        compare_evidence=compare_heading.bboxes,
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_child],
        compare_clauses=[compare_heading, compare_child],
        original_document=original_document,
    )

    assert [item.diff_id for item in result.diffs] == ["D_NATIVE_ADD_KEEP"]


def test_diff_quality_suppresses_title_only_modify_with_native_evidence(tmp_path: Path) -> None:
    path = tmp_path / "original.pdf"
    _write_quality_heading_pdf(path, "17. 合同生效")
    original_document = _quality_document(1, "17.\n本合同在双方签章后生效。")
    original_document.path = str(path)
    original_clause = _quality_clause("OC170", "17.\n本合同在双方签章后生效。")
    original_clause.clause_no = "17"
    compare_clause = _quality_clause("NC170", "17. 合同生效\n本合同在双方签章后生效。", side_prefix="N")
    compare_clause.clause_no = "17"
    compare_clause.title = "合同生效"
    diff = DiffItem(
        diff_id="D_NATIVE_MODIFY",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC170",
        compare_clause_id="NC170",
        clause_no="17",
        title="合同生效",
        original_text=original_clause.text,
        compare_text=compare_clause.text,
        original_snippet="",
        compare_snippet="合同生效",
        match_score_details={"alignment": {"body_similarity": 0.98}, "business_token_mismatch": 0.0},
        review_flags=["READING_ORDER_RISK"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
        original_document=original_document,
    )

    assert result.diffs == []
    assert any(item.detail.get("reason") == "native_heading_title_only_covered" for item in result.decisions)


def test_diff_quality_suppresses_visually_present_top_level_marker_ocr_dropout(tmp_path: Path) -> None:
    compare_path = tmp_path / "compare-marker.pdf"
    _write_heading_marker_pdf(compare_path, visible=True)
    diff, original_clause, compare_clause, compare_document = _top_level_marker_dropout_case(compare_path)

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
        compare_document=compare_document,
    )

    assert result.diffs == []
    assert any(item.detail.get("reason") == "visual_heading_marker_ocr_dropout" for item in result.decisions)


def test_diff_quality_keeps_top_level_marker_removal_without_visual_ink(tmp_path: Path) -> None:
    compare_path = tmp_path / "compare-no-marker.pdf"
    _write_heading_marker_pdf(compare_path, visible=False)
    diff, original_clause, compare_clause, compare_document = _top_level_marker_dropout_case(compare_path)

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
        compare_document=compare_document,
    )

    assert [item.diff_id for item in result.diffs] == ["D_MARKER_DROPOUT"]


def _top_level_marker_dropout_case(path: Path) -> tuple[DiffItem, Clause, Clause, Document]:
    title = "系统开放性与可配置性要求"
    original_clause = _quality_clause("OC062", f"一、{title}", section_type="appendix")
    original_clause.clause_no = "一"
    original_clause.title = title
    original_clause.bboxes[0].bbox = BBox(x0=80, y0=80, x1=226, y1=105)
    compare_clause = _quality_clause("NC062", title, side_prefix="N", section_type="appendix")
    compare_clause.title = title
    compare_clause.bboxes[0].bbox = BBox(x0=105, y0=80, x1=231, y1=105)
    compare_document = _quality_document(1, title)
    compare_document.path = str(path)
    compare_document.pages[0].blocks[0].bbox = compare_clause.bboxes[0].bbox
    diff = DiffItem(
        diff_id="D_MARKER_DROPOUT",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id=original_clause.clause_id,
        compare_clause_id=compare_clause.clause_id,
        section_type="appendix",
        title=title,
        original_text=original_clause.text,
        compare_text=compare_clause.text,
        original_snippet="一、",
        compare_snippet="",
        match_score=100,
        match_score_details={
            "title_score": 100.0,
            "business_token_mismatch": 0.0,
            "short_clause_pair": 1.0,
            "alignment": {"title_match": True, "body_similarity": 0.96, "risk_flags": []},
        },
        review_flags=["READING_ORDER_RISK", "SHORT_CLAUSE_MATCH_REVIEW", "POSSIBLE_OCR_NOISE"],
    )
    return diff, original_clause, compare_clause, compare_document


def _write_heading_marker_pdf(path: Path, *, visible: bool) -> None:
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    if visible:
        page.insert_text((82, 100), "一、", fontname="china-s", fontsize=12)
    pdf.save(path)
    pdf.close()


def test_diff_quality_keeps_native_heading_add_when_clause_contains_new_body(tmp_path: Path) -> None:
    path = tmp_path / "original.pdf"
    _write_quality_heading_pdf(path, "8. 知识产权")
    original_document = _quality_document(1, "8.\n8.1 甲方拥有工作成果。")
    original_document.path = str(path)
    original_child = _quality_clause("OC081", "8.1 甲方拥有工作成果。", order_index=2)
    original_child.clause_no = "8.1"
    compare_heading = _quality_clause(
        "NC080", "8. 知识产权\n新增许可限制。", side_prefix="N", order_index=1, split_flags=["READING_ORDER_REPAIRED"]
    )
    compare_heading.clause_no = "8"
    compare_heading.title = "知识产权"
    compare_child = _quality_clause("NC081", "8.1 甲方拥有工作成果。", side_prefix="N", order_index=2)
    compare_child.clause_no = "8.1"
    diff = DiffItem(
        diff_id="D_NATIVE_BODY_ADD",
        diff_type="ADD",
        source_type="clause",
        compare_clause_id="NC080",
        clause_no="8",
        title="知识产权",
        compare_text=compare_heading.text,
        compare_snippet=compare_heading.text,
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
        compare_evidence=compare_heading.bboxes,
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_child],
        compare_clauses=[compare_heading, compare_child],
        original_document=original_document,
    )

    assert [item.diff_id for item in result.diffs] == ["D_NATIVE_BODY_ADD"]


def test_diff_quality_keeps_high_risk_heading_add_when_diff_payload_is_heading_only_but_clause_has_new_body(
    tmp_path: Path,
) -> None:
    path = tmp_path / "original.pdf"
    _write_quality_heading_pdf(path, "8. 知识产权")
    original_document = _quality_document(1, "8.\n8.1 甲方拥有工作成果。")
    original_document.path = str(path)
    original_child = _quality_clause("OC081", "8.1 甲方拥有工作成果。", order_index=2)
    original_child.clause_no = "8.1"
    compare_heading = _quality_clause(
        "NC080", "8. 知识产权\n新增许可限制。", side_prefix="N", order_index=1, split_flags=["READING_ORDER_REPAIRED"]
    )
    compare_heading.clause_no = "8"
    compare_heading.title = "知识产权"
    compare_child = _quality_clause("NC081", "8.1 甲方拥有工作成果。", side_prefix="N", order_index=2)
    compare_child.clause_no = "8.1"
    diff = DiffItem(
        diff_id="D_NATIVE_BODY_PAYLOAD_KEEP",
        diff_type="ADD",
        source_type="clause",
        compare_clause_id="NC080",
        clause_no="8",
        title="知识产权",
        compare_text="8. 知识产权",
        compare_snippet="8. 知识产权",
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "CRITICAL_VALUE_CHANGE"],
        compare_evidence=compare_heading.bboxes,
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_child],
        compare_clauses=[compare_heading, compare_child],
        original_document=original_document,
    )

    assert [item.diff_id for item in result.diffs] == ["D_NATIVE_BODY_PAYLOAD_KEEP"]


def test_boundary_coverage_context_caches_native_failure_and_stays_fail_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "native-failure.pdf"
    _write_quality_heading_pdf(path, "8. 知识产权")
    document = _quality_document(1, "8.\n8.1 甲方拥有工作成果。")
    document.path = str(path)
    calls: list[str] = []

    def _fake_loader(requested_path: str) -> NativeHeadingIndex:
        calls.append(requested_path)
        return NativeHeadingIndex(warning="native extraction failed")

    monkeypatch.setattr(boundary_coverage_module, "load_native_heading_index", _fake_loader)

    context = BoundaryCoverageContext(original_document=document)
    first = context.native_heading_index(document)
    second = context.native_heading_index(document)

    assert first.warning == "native extraction failed"
    assert second.warning == "native extraction failed"
    assert calls == [str(path)]
    assert not ClauseBoundaryCoverageFilter()._native_heading_confirms(document, {1}, "8", "知识产权", context)


def test_native_heading_confirm_uses_adjacent_page_window_only(tmp_path: Path) -> None:
    path = tmp_path / "adjacent-window.pdf"
    _write_quality_heading_pdf(path, "8. 知识产权", page_no=2, page_count=4)
    document = _quality_document(1, "占位")
    document.path = str(path)
    context = BoundaryCoverageContext(original_document=document)
    coverage_filter = ClauseBoundaryCoverageFilter()

    assert coverage_filter._native_heading_confirms(document, {1}, "8", "知识产权", context)
    assert not coverage_filter._native_heading_confirms(document, {4}, "8", "知识产权", context)


def test_diff_quality_keeps_title_only_modify_with_protected_value_even_when_native_confirmation_is_forced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _AlwaysExactNativeIndex:
        @staticmethod
        def contains_exact(number: str, title: str, pages: set[int]) -> bool:
            return number == "17" and title == "金额100元" and pages == {1, 2}

    path = tmp_path / "protected-value-native.pdf"
    _write_quality_heading_pdf(path, "17. 无关标题")
    original_document = _quality_document(1, "17.\n本条款正文保持一致。")
    original_document.path = str(path)
    original_clause = _quality_clause("OC170", "17.\n本条款正文保持一致。")
    original_clause.clause_no = "17"
    compare_clause = _quality_clause("NC170", "17. 金额100元\n本条款正文保持一致。", side_prefix="N")
    compare_clause.clause_no = "17"
    compare_clause.title = "金额100元"
    diff = DiffItem(
        diff_id="D_NATIVE_MODIFY_PROTECTED_VALUE",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC170",
        compare_clause_id="NC170",
        clause_no="17",
        title="金额100元",
        original_text=original_clause.text,
        compare_text=compare_clause.text,
        original_snippet="",
        compare_snippet="金额100元",
        match_score_details={"alignment": {"body_similarity": 0.98}, "business_token_mismatch": 0.0},
        review_flags=["READING_ORDER_RISK"],
    )

    monkeypatch.setattr(boundary_coverage_module, "load_native_heading_index", lambda _path: _AlwaysExactNativeIndex())

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
        original_document=original_document,
    )

    assert [item.diff_id for item in result.diffs] == ["D_NATIVE_MODIFY_PROTECTED_VALUE"]


def test_diff_quality_keeps_title_only_modify_when_clause_numbers_differ_even_with_exact_native_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "clause-number-mismatch.pdf"
    _write_quality_heading_pdf(path, "18. 合同生效")
    original_document = _quality_document(1, "17.\n本条款正文保持一致。")
    original_document.path = str(path)
    original_clause = _quality_clause("OC170", "17.\n本条款正文保持一致。")
    original_clause.clause_no = "17"
    compare_clause = _quality_clause("NC170", "18. 合同生效\n本条款正文保持一致。", side_prefix="N")
    compare_clause.clause_no = "18"
    compare_clause.title = "合同生效"
    diff = DiffItem(
        diff_id="D_NATIVE_MODIFY_CLAUSE_NO_MISMATCH",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC170",
        compare_clause_id="NC170",
        clause_no="17",
        title="合同生效",
        original_text=original_clause.text,
        compare_text=compare_clause.text,
        original_snippet="",
        compare_snippet="合同生效",
        match_score_details={"alignment": {"body_similarity": 0.98}, "business_token_mismatch": 0.0},
        review_flags=["READING_ORDER_RISK"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
        original_document=original_document,
    )

    assert [item.diff_id for item in result.diffs] == ["D_NATIVE_MODIFY_CLAUSE_NO_MISMATCH"]


@pytest.mark.parametrize(
    ("title", "compare_snippet", "body_similarity", "business_token_mismatch"),
    [
        ("合同生效", "合同生效", 0.98, 1.0),
        ("合同生效", "合同生效", 0.89, 0.0),
    ],
)
def test_diff_quality_keeps_title_only_modify_when_native_guard_fails(
    tmp_path: Path,
    title: str,
    compare_snippet: str,
    body_similarity: float,
    business_token_mismatch: float,
) -> None:
    path = tmp_path / f"{title}-{body_similarity}-{business_token_mismatch}.pdf"
    _write_quality_heading_pdf(path, f"17. {title}")
    original_document = _quality_document(1, "17.\n本条款正文保持一致。")
    original_document.path = str(path)
    original_clause = _quality_clause("OC170", "17.\n本条款正文保持一致。")
    original_clause.clause_no = "17"
    compare_clause = _quality_clause("NC170", f"17. {title}\n本条款正文保持一致。", side_prefix="N")
    compare_clause.clause_no = "17"
    compare_clause.title = title
    diff = DiffItem(
        diff_id=f"D_NATIVE_MODIFY_GUARD_{title}_{body_similarity}_{business_token_mismatch}",
        diff_type="MODIFY",
        source_type="clause",
        original_clause_id="OC170",
        compare_clause_id="NC170",
        clause_no="17",
        title=title,
        original_text=original_clause.text,
        compare_text=compare_clause.text,
        original_snippet="",
        compare_snippet=compare_snippet,
        match_score_details={
            "alignment": {"body_similarity": body_similarity},
            "business_token_mismatch": business_token_mismatch,
        },
        review_flags=["READING_ORDER_RISK"],
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_clauses=[original_clause],
        compare_clauses=[compare_clause],
        original_document=original_document,
    )

    assert [item.diff_id for item in result.diffs] == [diff.diff_id]


def test_diff_quality_keeps_critical_heading_add_not_covered_by_larger_numbered_heading() -> None:
    original_document = _quality_document(
        3,
        "12. 违约责任\n12.1 任何一方违约均应承担赔偿责任。",
    )
    diff = DiffItem(
        diff_id="D111_CRITICAL_HEADING_PREFIX",
        diff_type="ADD",
        source_type="clause",
        compare_text="2. 违约责任",
        compare_snippet="2. 违约责任",
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        compare_evidence=[EvidenceBox(page_no=3, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text="2. 违约责任")],
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D111_CRITICAL_HEADING_PREFIX"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "changed_text_covered_by_opposite_page_text"
        for decision in result.decisions
    )


def test_diff_quality_keeps_long_material_heading_add_not_covered_by_larger_numbered_heading() -> None:
    original_document = _quality_document(
        3,
        "12. 合同价格及支付方式\n12.1 甲方按合同约定支付价款。",
    )
    diff = DiffItem(
        diff_id="D111_LONG_MATERIAL_HEADING_PREFIX",
        diff_type="ADD",
        source_type="clause",
        compare_text="2. 合同价格及支付方式",
        compare_snippet="2. 合同价格及支付方式",
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        compare_evidence=[
            EvidenceBox(page_no=3, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text="2. 合同价格及支付方式")
        ],
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D111_LONG_MATERIAL_HEADING_PREFIX"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "changed_text_covered_by_opposite_page_text"
        for decision in result.decisions
    )


def test_diff_quality_keeps_mixed_material_heading_add_not_covered_by_larger_numbered_heading() -> None:
    original_document = _quality_document(
        3,
        "12. 合同价格及支付方式\n12.1 甲方按合同约定支付价款。",
    )
    diff = DiffItem(
        diff_id="D111_MIXED_MATERIAL_HEADING_PREFIX",
        diff_type="ADD",
        source_type="clause",
        compare_text="2. 合同价格及支付方式\n甲方按合同约定支付价款。",
        compare_snippet="合同价格及支付方式\n甲方按合同约定支付价款。",
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        compare_evidence=[
            EvidenceBox(
                page_no=3, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text="合同价格及支付方式\n甲方按合同约定支付价款。"
            )
        ],
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D111_MIXED_MATERIAL_HEADING_PREFIX"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "changed_text_covered_by_opposite_page_text"
        for decision in result.decisions
    )


def test_diff_quality_keeps_mixed_material_heading_delete_not_covered_by_larger_numbered_heading() -> None:
    compare_document = _quality_document(
        3,
        "12. 合同价格及支付方式\n甲方按合同约定支付价款。",
    )
    diff = DiffItem(
        diff_id="D111_MIXED_MATERIAL_HEADING_DELETE_PREFIX",
        diff_type="DELETE",
        source_type="clause",
        original_text="2. 合同价格及支付方式\n甲方按合同约定支付价款。",
        original_snippet="合同价格及支付方式\n甲方按合同约定支付价款。",
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        original_evidence=[
            EvidenceBox(
                page_no=3, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text="合同价格及支付方式\n甲方按合同约定支付价款。"
            )
        ],
    )

    result = DiffQualityProcessor().process([diff], compare_document=compare_document)

    assert [item.diff_id for item in result.diffs] == ["D111_MIXED_MATERIAL_HEADING_DELETE_PREFIX"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "changed_text_covered_by_opposite_page_text"
        for decision in result.decisions
    )


def test_diff_quality_keeps_single_line_material_heading_delete_not_covered_by_larger_numbered_heading() -> None:
    compare_document = _quality_document(
        3,
        "12. 合同价格及支付方式\n12.1 甲方按合同约定支付价款。",
    )
    diff = DiffItem(
        diff_id="D111_SINGLE_MATERIAL_HEADING_DELETE_PREFIX",
        diff_type="DELETE",
        source_type="clause",
        original_text="2. 合同价格及支付方式",
        original_snippet="2. 合同价格及支付方式",
        structural_flags=["READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        original_evidence=[
            EvidenceBox(page_no=3, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text="2. 合同价格及支付方式")
        ],
    )

    result = DiffQualityProcessor().process([diff], compare_document=compare_document)

    assert [item.diff_id for item in result.diffs] == ["D111_SINGLE_MATERIAL_HEADING_DELETE_PREFIX"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "changed_text_covered_by_opposite_page_text"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_one_sided_modify_fragment_covered_by_opposite_page_text() -> None:
    original_document = _quality_document(
        3,
        "在执行合同过程中如发现有任何漏项和短缺。\n"
        "而且确实是乙方服务范围中应该有的,并且是满足合同及附件对本项目性能保证\n"
        "值要求所必须的,均应由乙方按要求补上,发生的费用由乙方承担。\n"
        "3.1 乙方提供服务的期限为合同签订后一年。",
    )
    diff = DiffItem(
        diff_id="D030",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="3.1",
        title="值要求所必须的,均应由乙方按要求补上,发生的费用由乙方承担。",
        original_text="3.1 乙方提供服务的期限为合同签订后一年。",
        compare_text="值要求所必须的,均应由乙方按要求补上,发生的费用由乙方承担。",
        original_snippet="",
        compare_snippet="值要求所必须的,均应由乙方按要求补上,发生的费用由乙方承担。",
        structural_flags=["PARAGRAPH_MERGED", "READING_ORDER_REPAIRED"],
        review_flags=[
            "CRITICAL_FIELD_CHANGE",
            "CRITICAL_FIELD_PARTY_ROLE_CHANGE",
            "LOW_CONFIDENCE_MATCH",
            "POSSIBLE_CLAUSE_MISMATCH",
            "READING_ORDER_RISK",
        ],
        compare_evidence=[
            EvidenceBox(
                page_no=3,
                bbox=BBox(x0=104, y0=177, x1=442, y1=196),
                text="值要求所必须的,均应由乙方按要求补上,发生的费用由乙方承担。",
            ),
        ],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D030"
        and decision.detail["reason"] == "modify_fragment_covered_by_opposite_page_text"
        for decision in result.decisions
    )


def test_diff_quality_keeps_critical_modify_fragment_not_covered_by_larger_numbered_heading() -> None:
    original_document = _quality_document(
        3,
        "12. 违约责任\n12.1 任何一方违约均应承担赔偿责任。",
    )
    diff = DiffItem(
        diff_id="D030_CRITICAL_HEADING_PREFIX",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="2",
        original_text="2.1 任何一方均应按合同约定履行义务。",
        compare_text="2. 违约责任\n2.1 任何一方均应按合同约定履行义务。",
        original_snippet="",
        compare_snippet="违约责任",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        structural_flags=["PARAGRAPH_MERGED"],
        compare_evidence=[EvidenceBox(page_no=3, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text="违约责任")],
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D030_CRITICAL_HEADING_PREFIX"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "modify_fragment_covered_by_opposite_page_text"
        for decision in result.decisions
    )


def test_diff_quality_keeps_long_material_modify_fragment_not_covered_by_larger_numbered_heading() -> None:
    original_document = _quality_document(
        3,
        "12. 合同价格及支付方式\n12.1 甲方按合同约定支付价款。",
    )
    diff = DiffItem(
        diff_id="D030_LONG_MATERIAL_HEADING_PREFIX",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="2",
        original_text="2.1 任何一方均应按合同约定履行义务。",
        compare_text="2. 合同价格及支付方式\n2.1 任何一方均应按合同约定履行义务。",
        original_snippet="",
        compare_snippet="合同价格及支付方式",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        structural_flags=["PARAGRAPH_MERGED"],
        compare_evidence=[EvidenceBox(page_no=3, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text="合同价格及支付方式")],
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D030_LONG_MATERIAL_HEADING_PREFIX"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "modify_fragment_covered_by_opposite_page_text"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_non_contiguous_split_original_fragments_covered_by_opposite_page() -> None:
    original_document = _quality_document(
        2,
        "1.9.除本合同另有约定外,“以上”“以下”“以内”“×日内”“届满”,均包括本数;“不满”“超\n"
        "过”“以外”,不包括本数;“×日前”“×日后”不包括当日。按照日、月、年计算期间\n"
        "的,开始的当日不算入,从下一日开始计算。期间的最后一日法定休假日的,以\n"
        "法定休假日结束的次日为期间的最后一日。\n"
        "2. 服务内容",
    )
    diff = DiffItem(
        diff_id="D093",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="1.9",
        original_clause_id="OC010P01",
        compare_clause_id="NC010",
        original_text="过”“以外”,不包括本数;“×日前”“×日后”不包括当日。按照日、月、年计算期",
        compare_text=(
            "1.9. 除本合同另有约定外,“以上”“以下”“以内”“×日内”“届满”,均包括本数;“不满”“超\n"
            "过”“以外”,不包括本数;“×日前”“×日后”不包括当日。按照日、月、年计算期\n"
            "间的,开始的当日不算入,从下一日开始计算。期间的最后一日法定休假日的,\n"
            "以法定休假日结束的次日为期间的最后一日。"
        ),
        original_snippet="",
        compare_snippet=(
            "1.9. 除本合同另有约定外,“以上”“以下”“以内”“×日内”“届满”,均包括本数;"
            "“不满”“超间的,开始的当日不算入,从下一日开始计算。"
            "期间的最后一日法定休假日的,以法定休假日结束的次日为期间的最后一日。"
        ),
        review_flags=[
            "LOW_COVERAGE_MATCH_REVIEW",
            "PARTIAL_CLAUSE_MATCH",
            "POSSIBLE_SPLIT_CLAUSE",
            "TEXT_FOUND_IN_OTHER_CLAUSE",
            "READING_ORDER_RISK",
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        compare_evidence=[
            EvidenceBox(page_no=2, bbox=BBox(x0=62, y0=626, x1=82, y1=639), text="1.9."),
            EvidenceBox(
                page_no=2,
                bbox=BBox(x0=80, y0=626, x1=350, y1=639),
                text="除本合同另有约定外,“以上”“以下”“以内”“×日内”“届满”,",
            ),
            EvidenceBox(page_no=2, bbox=BBox(x0=95, y0=672, x1=122, y1=686), text="间的,"),
            EvidenceBox(
                page_no=2, bbox=BBox(x0=96, y0=695, x1=300, y1=711), text="以法定休假日结束的次日为期间的最后一日。"
            ),
        ],
        match_score_details={"body_length_coverage": 0.2746, "split_original_clause": 1.0},
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D093"
        and decision.detail["reason"] == "low_coverage_split_page_fragment_covered"
        for decision in result.decisions
    )


def test_diff_quality_keeps_split_fragment_when_snippet_material_is_not_covered() -> None:
    original_document = _quality_document(
        2,
        "1.9.除本合同另有约定外,“以上”“以下”“以内”“×日内”“届满”,均包括本数。",
    )
    diff = DiffItem(
        diff_id="D093_REAL_ADD",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="1.9",
        original_clause_id="OC010P01",
        compare_clause_id="NC010",
        original_text="1.9.除本合同另有约定外,“以上”“以下”“以内”“×日内”“届满”,均包括本数。",
        compare_text="1.9.除本合同另有约定外,“以上”“以下”“以内”“×日内”“届满”,均包括本数。新增真实付款条件。",
        original_snippet="",
        compare_snippet="除本合同另有约定外,“以上”“以下”“以内”“×日内”“届满”,均包括本数。新增真实付款条件。",
        review_flags=[
            "LOW_COVERAGE_MATCH_REVIEW",
            "PARTIAL_CLAUSE_MATCH",
            "POSSIBLE_SPLIT_CLAUSE",
            "TEXT_FOUND_IN_OTHER_CLAUSE",
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        compare_evidence=[
            EvidenceBox(
                page_no=2,
                bbox=BBox(x0=80, y0=626, x1=350, y1=639),
                text="除本合同另有约定外,“以上”“以下”“以内”“×日内”“届满”,",
            ),
        ],
        match_score_details={"body_length_coverage": 0.25, "split_original_clause": 1.0},
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D093_REAL_ADD"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D093_REAL_ADD"
        and decision.detail["reason"] == "low_coverage_split_page_fragment_covered"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_existing_clause_add_cut_by_reading_order() -> None:
    original_document = _quality_document(
        4,
        "4.2.1 双方同意采用以下第（一）、（三）种方式进行付款【注:可多选】:\n（一）转账/电汇;\n（二）信用证;",
    )
    diff = DiffItem(
        diff_id="D113",
        diff_type="ADD",
        source_type="clause",
        clause_no="4.2.1",
        compare_text="4.2.1. 双方同意采用以下第\n(一)、",
        compare_snippet="双方同意采用以下第",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        structural_flags=[],
        compare_evidence=[
            EvidenceBox(page_no=4, bbox=BBox(x0=80, y0=160, x1=115, y1=180), text="4.2.1."),
            EvidenceBox(page_no=4, bbox=BBox(x0=120, y0=160, x1=260, y1=180), text="双方同意采用以下第"),
            EvidenceBox(page_no=4, bbox=BBox(x0=80, y0=180, x1=120, y1=200), text="(一)、"),
        ],
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "changed_text_covered_by_opposite_page_text"
        for decision in result.decisions
    )


def test_diff_quality_keeps_add_when_only_full_clause_text_is_covered() -> None:
    original_document = _quality_document(
        4,
        "4.2.1. 双方同意采用以下第\n(一)、",
    )
    diff = DiffItem(
        diff_id="D113_REAL_ADD",
        diff_type="ADD",
        source_type="clause",
        clause_no="4.2.1",
        compare_text="4.2.1. 双方同意采用以下第\n(一)、",
        compare_snippet="新增付款条件",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        compare_evidence=[EvidenceBox(page_no=4, bbox=BBox(x0=120, y0=160, x1=260, y1=180), text="新增付款条件")],
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D113_REAL_ADD"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage" and decision.diff_id == "D113_REAL_ADD"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_metadata_heading_delete_when_present_on_compare_page() -> None:
    compare_document = _quality_document(2, "1. 定义\n除非另有明确约定,下列词语应具有本条所赋予的含义:")
    diff = DiffItem(
        diff_id="D011",
        diff_type="DELETE",
        source_type="metadata",
        title="封面额外文本",
        original_text="1.",
        original_snippet="1.",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        original_evidence=[EvidenceBox(page_no=2, bbox=BBox(x0=100, y0=500, x1=130, y1=520), text="1.")],
    )

    result = DiffQualityProcessor().process([diff], compare_document=compare_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "changed_text_covered_by_opposite_page_text"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_heading_when_opposite_page_has_number_and_body_continuation() -> None:
    original_document = _quality_document(
        2,
        "1.9 条款正文。\n2.\n乙方应按合同约定向甲方提供以下技术服务:\n长源电力随州公司2026年新能源场站功率预测系统授权服务项目。",
    )
    diff = DiffItem(
        diff_id="D094",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="2",
        original_clause_id="OC010P02",
        compare_clause_id="NC011",
        original_text="乙方应按合同约定向甲方提供以下技术服务:",
        compare_text="2. 服务内容\n乙方应按合同约定向甲方提供以下技术服务:",
        original_snippet="",
        compare_snippet="2. 服务内容",
        review_flags=[
            "LOW_COVERAGE_MATCH_REVIEW",
            "PARTIAL_CLAUSE_MATCH",
            "POSSIBLE_SPLIT_CLAUSE",
            "TEXT_FOUND_IN_OTHER_CLAUSE",
            "READING_ORDER_RISK",
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        compare_evidence=[EvidenceBox(page_no=2, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text="2. 服务内容")],
        match_score_details={"body_length_coverage": 0.1351, "split_original_clause": 1.0},
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "heading_with_bare_number_and_body_covered"
        for decision in result.decisions
    )


def test_diff_quality_keeps_critical_heading_added_to_bare_number_body() -> None:
    original_document = _quality_document(
        2,
        "1.9 条款正文。\n2.\n任何一方均应按合同约定履行义务。",
    )
    diff = DiffItem(
        diff_id="D094_CRITICAL_HEADING",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="2",
        original_clause_id="OC010P02",
        compare_clause_id="NC011",
        original_text="任何一方均应按合同约定履行义务。",
        compare_text="2. 违约责任\n任何一方均应按合同约定履行义务。",
        original_snippet="",
        compare_snippet="2. 违约责任",
        review_flags=[
            "LOW_COVERAGE_MATCH_REVIEW",
            "PARTIAL_CLAUSE_MATCH",
            "POSSIBLE_SPLIT_CLAUSE",
            "TEXT_FOUND_IN_OTHER_CLAUSE",
            "READING_ORDER_RISK",
            "CRITICAL_VALUE_CHANGE",
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        compare_evidence=[EvidenceBox(page_no=2, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text="2. 违约责任")],
        match_score_details={"body_length_coverage": 0.1351, "split_original_clause": 1.0},
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D094_CRITICAL_HEADING"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "heading_with_bare_number_and_body_covered"
        for decision in result.decisions
    )


def test_diff_quality_keeps_critical_flagged_material_heading_added_to_bare_number_body() -> None:
    original_document = _quality_document(
        2,
        "1.9 条款正文。\n2.\n任何一方均应按合同约定履行义务。",
    )
    diff = DiffItem(
        diff_id="D094_MATERIAL_HEADING",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="2",
        original_clause_id="OC010P02",
        compare_clause_id="NC011",
        original_text="任何一方均应按合同约定履行义务。",
        compare_text="2. 保密义务\n任何一方均应按合同约定履行义务。",
        original_snippet="",
        compare_snippet="2. 保密义务",
        review_flags=[
            "LOW_COVERAGE_MATCH_REVIEW",
            "PARTIAL_CLAUSE_MATCH",
            "POSSIBLE_SPLIT_CLAUSE",
            "TEXT_FOUND_IN_OTHER_CLAUSE",
            "READING_ORDER_RISK",
            "CRITICAL_VALUE_CHANGE",
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        compare_evidence=[EvidenceBox(page_no=2, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text="2. 保密义务")],
        match_score_details={"body_length_coverage": 0.1351, "split_original_clause": 1.0},
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D094_MATERIAL_HEADING"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "heading_with_bare_number_and_body_covered"
        for decision in result.decisions
    )


def test_diff_quality_keeps_critical_short_heading_text_with_bare_number() -> None:
    original_document = _quality_document(
        2,
        "2.\n2.1 任何一方均应按合同约定履行义务。",
    )
    diff = DiffItem(
        diff_id="D094_CRITICAL_SHORT_HEADING",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="2",
        original_clause_id="OC010P02",
        compare_clause_id="NC011",
        original_text="2.1 任何一方均应按合同约定履行义务。",
        compare_text="2. 违约责任\n2.1 任何一方均应按合同约定履行义务。",
        original_snippet="",
        compare_snippet="违约责任",
        review_flags=[
            "LOW_CONFIDENCE_MATCH",
            "POSSIBLE_CLAUSE_MISMATCH",
            "READING_ORDER_RISK",
            "CRITICAL_VALUE_CHANGE",
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        compare_evidence=[EvidenceBox(page_no=2, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text="违约责任")],
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D094_CRITICAL_SHORT_HEADING"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "short_heading_text_covered_by_bare_number"
        for decision in result.decisions
    )


def test_diff_quality_keeps_metadata_number_delete_when_only_larger_number_heading_exists() -> None:
    compare_document = _quality_document(2, "10. 付款金额为100万元\n除非另有约定,甲方按合同付款。")
    diff = DiffItem(
        diff_id="D011_NUMBER_BOUNDARY",
        diff_type="DELETE",
        source_type="metadata",
        title="封面额外文本",
        original_text="1.",
        original_snippet="1.",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        original_evidence=[EvidenceBox(page_no=2, bbox=BBox(x0=100, y0=500, x1=130, y1=520), text="1.")],
    )

    result = DiffQualityProcessor().process([diff], compare_document=compare_document)

    assert [item.diff_id for item in result.diffs] == ["D011_NUMBER_BOUNDARY"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "changed_text_covered_by_opposite_page_text"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_false_delete_when_text_exists_on_compare_page() -> None:
    compare_document = _quality_document(12, "本特别约定是对合同其他条款的修改或补充。\n（以下无正文）")
    diff = DiffItem(
        diff_id="D098",
        diff_type="DELETE",
        source_type="clause",
        title="(以下无正文)",
        original_text="(以下无正文)",
        original_snippet="(以下无正文)",
        review_flags=["NON_MAIN_CONTRACT_SECTION", "OCR_LOW_CONFIDENCE", "OCR_REMEDIATION_PLANNED"],
        original_evidence=[EvidenceBox(page_no=12, bbox=BBox(x0=170, y0=215, x1=260, y1=235), text="(以下无正文)")],
    )

    result = DiffQualityProcessor().process([diff], compare_document=compare_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"]
        in {"changed_text_covered_by_opposite_page_text", "short_heading_text_covered_by_opposite_page"}
        for decision in result.decisions
    )


def test_diff_quality_suppresses_large_false_delete_when_compare_page_contains_fragments() -> None:
    compare_document = _quality_document(
        14,
        "项目名称:\n"
        "长源电力随州公司\n"
        "2026\n"
        "年新能源场站功率预测系统授\n"
        "甲\n"
        "方:\n"
        "国能长源随州发电有限公司随县分公司\n"
        "乙\n"
        "方:\n"
        "国能日新科技股份有限公司\n"
        "协议有效期:\n"
        "2026\n"
        "年7月1日\n"
        "至2027\n"
        "年6月30日\n"
        "为贯彻\n"
        "“安全第一,\n"
        "预防为主,\n"
        "综合治理”\n"
        "的方针,\n"
        "明确甲乙双\n"
        "方在项目实施过程中的权利、\n"
        "义务和安全生产责任,\n"
        "加强和规范项目\n"
        "管理工作,\n"
        "根据\n"
        "《中华人民共和国安全生产法》《中华人民共和国职\n"
        "业病防治法》《建设工程安全生产管理条例》《生产安全事故报告和\n"
        "调查处理条例》\n"
        "及相关法律、\n"
        "法规和规章的规定,\n"
        "甲乙双方经协商一\n"
        "致,\n"
        "订立本协议。",
    )
    diff = DiffItem(
        diff_id="D091",
        diff_type="MODIFY",
        source_type="clause",
        original_text=(
            "项目名称:长源电力随州公司2026年新能源场站功率预测系统授权服务单一来源项目\n"
            "甲方:国能长源随州发电有限公司随县分公司\n"
            "乙方:国能日新科技股份有限公司\n"
            "协议有效期:2026年7月1日至2027年6月30日\n"
            "为贯彻“安全第一、预防为主、综合治理”的方针。"
        ),
        compare_text="",
        original_snippet=(
            "项目名称:长源电力随州公司2026年新能源场站功率预测系统授"
            "甲方:国能长源随州发电有限公司随县分公司"
            "乙方:国能日新科技股份有限公司"
            "协议有效期:2026年7月1日至2027年6月30日"
        ),
        compare_snippet="",
        review_flags=[
            "LOW_COVERAGE_MATCH_REVIEW",
            "PARTIAL_CLAUSE_MATCH",
            "POSSIBLE_MERGED_CLAUSE",
            "TEXT_FOUND_IN_OTHER_CLAUSE",
            "READING_ORDER_RISK",
            "CRITICAL_FIELD_CHANGE",
            "CRITICAL_FIELD_DATE_CHANGE",
            "CRITICAL_FIELD_DURATION_CHANGE",
            "CRITICAL_FIELD_PARTY_ROLE_CHANGE",
            "CRITICAL_VALUE_CHANGE",
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        original_evidence=[
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=140, x1=450, y1=170), text="项目名称:"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=170, x1=450, y1=190), text="长源电力随州公司"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=190, x1=450, y1=210), text="2026"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=210, x1=450, y1=230), text="年新能源场站功率预测系统授"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=230, x1=450, y1=250), text="甲"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=250, x1=450, y1=270), text="方:"),
            EvidenceBox(
                page_no=14, bbox=BBox(x0=100, y0=270, x1=450, y1=290), text="国能长源随州发电有限公司随县分公司"
            ),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=290, x1=450, y1=310), text="乙"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=310, x1=450, y1=330), text="方:"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=330, x1=450, y1=350), text="国能日新科技股份有限公司"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=350, x1=450, y1=370), text="协议有效期:"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=370, x1=450, y1=390), text="2026"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=390, x1=450, y1=410), text="年7月1日"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=410, x1=450, y1=430), text="至2027"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=430, x1=450, y1=450), text="年6月30日"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=450, x1=450, y1=470), text="为贯彻"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=470, x1=450, y1=490), text="“安全第一,"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=490, x1=450, y1=510), text="预防为主,"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=510, x1=450, y1=530), text="综合治理”"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=530, x1=450, y1=550), text="的方针,"),
        ],
        match_score_details={"body_length_coverage": 0.35, "merged_compare_clause": 1.0},
    )

    result = DiffQualityProcessor().process([diff], compare_document=compare_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "low_coverage_split_page_fragment_covered"
        for decision in result.decisions
    )


def test_diff_quality_keeps_material_heading_when_only_evidence_preserves_heading_shape() -> None:
    original_document = _quality_document(
        3,
        "12. 合同价格及支付方式\n甲方按合同约定支付价款。",
    )
    diff = DiffItem(
        diff_id="D091_MATERIAL_EVIDENCE",
        diff_type="MODIFY",
        source_type="clause",
        original_text="甲方按合同约定支付价款。",
        compare_text="12. 合同价格及支付方式\n甲方按合同约定支付价款。",
        original_snippet="",
        compare_snippet="合同价格及支付方式甲方按合同约定支付价款",
        review_flags=[
            "LOW_COVERAGE_MATCH_REVIEW",
            "PARTIAL_CLAUSE_MATCH",
            "POSSIBLE_SPLIT_CLAUSE",
            "TEXT_FOUND_IN_OTHER_CLAUSE",
            "READING_ORDER_RISK",
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        compare_evidence=[
            EvidenceBox(page_no=3, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text="合同价格及支付方式"),
            EvidenceBox(page_no=3, bbox=BBox(x0=65, y0=753, x1=151, y1=776), text="甲方按合同约定支付价款。"),
        ],
        match_score_details={"body_length_coverage": 0.25, "split_original_clause": 1.0},
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D091_MATERIAL_EVIDENCE"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage" and decision.diff_id == "D091_MATERIAL_EVIDENCE"
        for decision in result.decisions
    )


def test_diff_quality_keeps_multi_field_change_when_only_labels_are_covered() -> None:
    compare_document = _quality_document(
        14,
        "项目名称:\n甲方:\n乙方:\n协议有效期:",
    )
    diff = DiffItem(
        diff_id="D091_INCOMPLETE_EVIDENCE",
        diff_type="MODIFY",
        source_type="clause",
        original_text="项目名称:长源电力随州公司\n甲方:国能长源随州发电有限公司\n乙方:国能日新科技股份有限公司\n协议有效期:2026年7月1日至2027年6月30日",
        compare_text="",
        original_snippet="项目名称:长源电力随州公司甲方:国能长源随州发电有限公司乙方:国能日新科技股份有限公司协议有效期:2026年7月1日至2027年6月30日",
        compare_snippet="",
        review_flags=[
            "LOW_COVERAGE_MATCH_REVIEW",
            "PARTIAL_CLAUSE_MATCH",
            "POSSIBLE_MERGED_CLAUSE",
            "TEXT_FOUND_IN_OTHER_CLAUSE",
            "READING_ORDER_RISK",
            "CRITICAL_FIELD_CHANGE",
            "CRITICAL_FIELD_DATE_CHANGE",
            "CRITICAL_FIELD_DURATION_CHANGE",
            "CRITICAL_FIELD_PARTY_ROLE_CHANGE",
            "CRITICAL_VALUE_CHANGE",
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        original_evidence=[
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=140, x1=450, y1=170), text="项目名称:"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=180, x1=450, y1=210), text="甲方:"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=220, x1=450, y1=250), text="乙方:"),
            EvidenceBox(page_no=14, bbox=BBox(x0=100, y0=260, x1=450, y1=290), text="协议有效期:"),
        ],
        match_score_details={"body_length_coverage": 0.10, "merged_compare_clause": 1.0},
    )

    result = DiffQualityProcessor().process([diff], compare_document=compare_document)

    assert [item.diff_id for item in result.diffs] == ["D091_INCOMPLETE_EVIDENCE"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage" and decision.diff_id == "D091_INCOMPLETE_EVIDENCE"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_short_heading_text_when_original_has_bare_number() -> None:
    original_document = _quality_document(
        9,
        "12.4 如果不可抗力事件的影响已达60天。\n13.\n13.1 甲方应对服务内容进行说明。",
    )
    diff = DiffItem(
        diff_id="D046",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="12.4",
        title="如果不可抗力事件的影响已达60天",
        original_text="12.4 如果不可抗力事件的影响已达60天。\n13.\n协商解决。",
        compare_text="12.4 如果不可抗力事件的影响已达60天。\n协商解决。\n13. 说明",
        original_snippet="",
        compare_snippet="说明",
        structural_flags=["PARAGRAPH_MERGED"],
        review_flags=["LOW_CONFIDENCE_MATCH", "POSSIBLE_CLAUSE_MISMATCH", "READING_ORDER_RISK"],
        compare_evidence=[EvidenceBox(page_no=9, bbox=BBox(x0=117, y0=468, x1=148, y1=491), text="说明")],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D046"
        and decision.detail["reason"] == "short_heading_text_covered_by_bare_number"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_ocr_stitched_count_heading_fragment() -> None:
    original_document = _quality_document(
        11,
        "17.\n"
        "本合同在以下条件全部满足时生效:\n"
        "18.\n"
        "本合同一式伍份，甲方执肆份，乙方执壹份，经双方共同协商认可使用可靠的电子签名签订的电子合同与纸质合同具有同等法律效力。\n"
        "19.",
    )
    diff = DiffItem(
        diff_id="D053",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="(1)",
        original_text="(1)合同经甲乙双方法定代表人或其授权代表签字并加盖单位公章。\n18.\n本合同一式伍份。",
        compare_text="(1)合同经甲乙双方法定代表人或其授权代表签字并加盖单位公章。\n(2)1\n18. 份数\n本合同一式伍份。",
        original_snippet="",
        compare_snippet="1份数",
        review_flags=[
            "LOW_CONFIDENCE_MATCH",
            "POSSIBLE_CLAUSE_MISMATCH",
            "READING_ORDER_RISK",
            "SPATIAL_SUBSTRING_COVERAGE_REPAIRED",
            "OCR_REMEDIATION_PLANNED",
            "POSSIBLE_OCR_NOISE",
        ],
        structural_flags=["PARAGRAPH_MERGED", "CROSS_PAGE_CONTINUATION_MERGED"],
        compare_evidence=[
            EvidenceBox(page_no=11, bbox=BBox(x0=80, y0=720, x1=90, y1=740), text="1"),
            EvidenceBox(page_no=11, bbox=BBox(x0=110, y0=720, x1=150, y1=740), text="份数"),
        ],
        match_score_details={"body_length_coverage": 0.77},
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D053"
        and decision.detail["reason"] == "ocr_stitched_count_heading_body_covered"
        for decision in result.decisions
    )


def test_diff_quality_keeps_unflagged_critical_short_heading_text_with_bare_number() -> None:
    original_document = _quality_document(
        2,
        "2.\n2.1 任何一方均应按合同约定履行义务。",
    )
    for heading in ["付款", "索赔", "争议解决", "签署页"]:
        diff = DiffItem(
            diff_id=f"D094_UNFLAGGED_{heading}",
            diff_type="MODIFY",
            source_type="clause",
            clause_no="2",
            original_clause_id="OC010P02",
            compare_clause_id="NC011",
            original_text="2.1 任何一方均应按合同约定履行义务。",
            compare_text=f"2. {heading}\n2.1 任何一方均应按合同约定履行义务。",
            original_snippet="",
            compare_snippet=heading,
            review_flags=["LOW_CONFIDENCE_MATCH", "POSSIBLE_CLAUSE_MISMATCH", "READING_ORDER_RISK"],
            structural_flags=["PARAGRAPH_MERGED"],
            compare_evidence=[EvidenceBox(page_no=2, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text=heading)],
        )

        result = DiffQualityProcessor().process([diff], original_document=original_document)

        assert [item.diff_id for item in result.diffs] == [f"D094_UNFLAGGED_{heading}"]
        assert not any(
            decision.action == "suppressed_by_neighbor_clause_coverage"
            and decision.detail["reason"] == "short_heading_text_covered_by_bare_number"
            for decision in result.decisions
        )


def test_diff_quality_keeps_critical_heading_fragment_in_low_coverage_split() -> None:
    original_document = _quality_document(
        2,
        "2.\n2.1 任何一方均应按合同约定履行义务。\n附注:历史条款曾提及违约责任。",
    )
    diff = DiffItem(
        diff_id="D094_CRITICAL_LOW_COVERAGE",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="2",
        original_clause_id="OC010P02",
        compare_clause_id="NC011",
        original_text="2.1 任何一方均应按合同约定履行义务。",
        compare_text="2. 违约责任\n2.1 任何一方均应按合同约定履行义务。",
        original_snippet="",
        compare_snippet="违约责任",
        review_flags=[
            "LOW_COVERAGE_MATCH_REVIEW",
            "PARTIAL_CLAUSE_MATCH",
            "POSSIBLE_SPLIT_CLAUSE",
            "TEXT_FOUND_IN_OTHER_CLAUSE",
            "READING_ORDER_RISK",
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        compare_evidence=[EvidenceBox(page_no=2, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text="违约责任")],
        match_score_details={"body_length_coverage": 0.25, "split_original_clause": 1.0},
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D094_CRITICAL_LOW_COVERAGE"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "low_coverage_split_page_fragment_covered"
        for decision in result.decisions
    )


def test_diff_quality_keeps_mixed_material_heading_fragment_in_low_coverage_split() -> None:
    original_document = _quality_document(
        3,
        "12. 合同价格及支付方式\n12.1 甲方按合同约定支付价款。",
    )
    diff = DiffItem(
        diff_id="D094_MIXED_MATERIAL_LOW_COVERAGE",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="2",
        original_text="2.1 任何一方均应按合同约定履行义务。",
        compare_text="2. 合同价格及支付方式\n甲方按合同约定支付价款。",
        original_snippet="",
        compare_snippet="合同价格及支付方式\n甲方按合同约定支付价款。",
        review_flags=[
            "LOW_COVERAGE_MATCH_REVIEW",
            "PARTIAL_CLAUSE_MATCH",
            "POSSIBLE_SPLIT_CLAUSE",
            "TEXT_FOUND_IN_OTHER_CLAUSE",
            "READING_ORDER_RISK",
        ],
        structural_flags=["PARAGRAPH_MERGED"],
        compare_evidence=[
            EvidenceBox(page_no=3, bbox=BBox(x0=65, y0=729, x1=151, y1=752), text="合同价格及支付方式"),
            EvidenceBox(page_no=3, bbox=BBox(x0=65, y0=753, x1=151, y1=776), text="甲方按合同约定支付价款。"),
        ],
        match_score_details={"body_length_coverage": 0.25, "split_original_clause": 1.0},
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D094_MIXED_MATERIAL_LOW_COVERAGE"]
    assert not any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "low_coverage_split_page_fragment_covered"
        for decision in result.decisions
    )


def test_diff_quality_keeps_real_dispute_method_change_not_heading_coverage() -> None:
    original_document = _quality_document(
        11,
        "15.2 若争议经协商仍无法解决的,按以下第一种方式处理:\n方式一:诉讼。",
    )
    diff = DiffItem(
        diff_id="D050",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="15.2",
        title="若争议经协商仍无法解决的,按以下第二种方式处理:",
        original_text="15.2 若争议经协商仍无法解决的,按以下第一种方式处理:",
        compare_text="15.2 若争议经协商仍无法解决的,按以下第二种方式处理:",
        original_snippet="一",
        compare_snippet="二",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process([diff], original_document=original_document)

    assert [item.diff_id for item in result.diffs] == ["D050"]


def test_diff_quality_suppresses_heading_layer_mismatch_when_both_pages_contain_both_headings() -> None:
    original_document = _quality_document(
        41,
        "第三章 合同范围\n3.1 服务范围\n本合同项目主要服务范围包括:随县鹿鹤光伏电站功率预测系统授权服务。",
    )
    compare_document = _quality_document(
        41,
        "第三章 合同范围\n3.1 服务范围\n本合同项目主要服务范围包括:随县鹿鹤光伏电站功率预测系统授权服务。",
    )
    diff = DiffItem(
        diff_id="D078",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="第三章",
        title="服务范围",
        original_text="第三章 合同范围",
        compare_text="3.1 服务范围",
        original_snippet="第三章 合同范围",
        compare_snippet="3.1 服务范围",
        structural_flags=["PARAGRAPH_MERGED"],
        review_flags=[
            "BUSINESS_TOKEN_MISMATCH_REVIEW",
            "LOW_CONFIDENCE_MATCH",
            "POSSIBLE_CLAUSE_MISMATCH",
            "READING_ORDER_RISK",
            "OCR_REMEDIATION_PLANNED",
            "CRITICAL_VALUE_CHANGE",
        ],
        original_evidence=[EvidenceBox(page_no=41, bbox=BBox(x0=218, y0=123, x1=286, y1=148), text="第三章")],
        compare_evidence=[EvidenceBox(page_no=41, bbox=BBox(x0=87, y0=212, x1=103, y1=236), text="3.1")],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_document=original_document,
        compare_document=compare_document,
    )

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D078"
        and decision.detail["reason"] == "heading_layer_mismatch_covered_by_both_pages"
        for decision in result.decisions
    )


def test_diff_quality_keeps_real_amount_uppercase_change_with_same_numeric_value() -> None:
    page_text = (
        "1、确定费用及支付方式\n4.1 合同价格\n"
        "4.1.1 本合同为固定总价合同，总价为人民币（大写）柒万叁仟元整（￥73000.00元）。"
    )
    diff = DiffItem(
        diff_id="D032",
        diff_type="MODIFY",
        source_type="clause",
        original_text="总价为人民币(大写)柒万捌仟元整(¥73000.00元)。",
        compare_text="总价为人民币(大写)柒万叁仟元整(¥73000.00元)。",
        original_snippet="捌仟",
        compare_snippet="叁仟",
        match_score=100,
        structural_flags=["PARAGRAPH_MERGED", "READING_ORDER_REPAIRED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process(
        [diff],
        original_document=_quality_document(33, page_text),
        compare_document=_quality_document(33, page_text),
    )

    assert [item.diff_id for item in result.diffs] == ["D032"]
    assert "FINANCIAL_UPPERCASE_AMOUNT_CHANGE" in result.diffs[0].review_flags


def test_diff_quality_keeps_reference_punctuation_change_but_trims_edge_annotation() -> None:
    diff = DiffItem(
        diff_id="D082",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="(7)",
        title="《电力二次系统安全防护总体方案》国家电力监管委员会电监安全",
        original_text="会电监安全(2006)34号。",
        compare_text="会电监安全〔2006〕34号。\n黄科",
        original_snippet="(2006)34",
        compare_snippet="〔2006〕34黄科",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        compare_evidence=[
            EvidenceBox(page_no=44, bbox=BBox(x0=141, y0=312, x1=201, y1=335), text="〔2006〕34"),
            EvidenceBox(page_no=44, bbox=BBox(x0=423, y0=783, x1=484, y1=822), text="黄科"),
        ],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D082"]
    assert result.diffs[0].compare_snippet == "〔2006〕34"
    assert any(
        decision.action == "trimmed_edge_annotation_noise" and decision.diff_id == "D082"
        for decision in result.decisions
    )


def test_diff_quality_keeps_slash_unit_format_change_but_not_amount_change() -> None:
    diff = DiffItem(
        diff_id="D068",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="6.3",
        title="乙方发生生产安全人身死亡责任事故的,应按照100万元",
        original_text="应按照100万元/人次的标准向甲方支付违约金。",
        compare_text="应按照100万元\n人次的标准向甲方支付违约金。",
        original_snippet="/",
        compare_snippet="",
        review_flags=[
            "CRITICAL_FIELD_AMOUNT_CHANGE",
            "CRITICAL_FIELD_CHANGE",
            "EVIDENCE_UNRELIABLE",
            "OCR_REMEDIATION_PLANNED",
            "CRITICAL_VALUE_CHANGE",
        ],
        quality_status="NEEDS_REVIEW",
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D068"]
    assert "CRITICAL_FIELD_AMOUNT_CHANGE" not in result.diffs[0].review_flags
    assert "UNIT_FORMAT_CHANGE_REVIEW" in result.diffs[0].review_flags


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
        TextRange(
            start=original_text.index("1。"), end=original_text.index("1。") + len("1。"), highlight_type="DELETE"
        ),
        TextRange(
            start=original_text.index("签署页"),
            end=original_text.index("签署页") + len("签署页"),
            highlight_type="DELETE",
        ),
        TextRange(
            start=original_text.index("甲方:"),
            end=original_text.index("签订日期:") + len("签订日期:"),
            highlight_type="DELETE",
        ),
        TextRange(
            start=original_text.index("联系人:"),
            end=original_text.index("联系人:") + len("联系人:环加飞"),
            highlight_type="DELETE",
        ),
        TextRange(
            start=original_text.index("电话:"),
            end=original_text.index("电话:") + len("电话:010-83582793"),
            highlight_type="DELETE",
        ),
        TextRange(
            start=original_text.index("传真:"),
            end=original_text.index("传真:") + len("传真:010-83582600"),
            highlight_type="DELETE",
        ),
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
            EvidenceBox(
                page_no=25, bbox=BBox(x0=85, y0=70, x1=180, y1=90), text="010-83582793", highlight_type="DELETE"
            ),
            EvidenceBox(page_no=25, bbox=BBox(x0=10, y0=100, x1=80, y1=120), text="传真:", highlight_type="DELETE"),
            EvidenceBox(
                page_no=25, bbox=BBox(x0=85, y0=100, x1=180, y1=120), text="010-83582600", highlight_type="DELETE"
            ),
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
    assert all(
        "010-83582793" not in trimmed.original_text[item.start : item.end] for item in trimmed.original_change_ranges
    )
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
        TextRange(
            start=compare_text.index("司】(盖章"),
            end=compare_text.index("司】(盖章") + len("司】(盖章"),
            highlight_type="ADD",
        ),
        TextRange(
            start=compare_text.index("授权代表签"),
            end=compare_text.index("授权代表签") + len("授权代表签"),
            highlight_type="ADD",
        ),
        TextRange(
            start=compare_text.index("纳税人识别"),
            end=compare_text.index("纳税人识别") + len("纳税人识别"),
            highlight_type="ADD",
        ),
        TextRange(
            start=compare_text.index("日期:2026.4.17"),
            end=compare_text.index("日期:2026.4.17") + len("日期:2026.4.17"),
            highlight_type="ADD",
        ),
        TextRange(
            start=compare_text.index("刘万程"), end=compare_text.index("刘万程") + len("刘万程"), highlight_type="ADD"
        ),
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
            EvidenceBox(
                page_no=7, bbox=BBox(x0=68.2, y0=345.7, x1=126.3, y1=361.3), text="司】(盖章", highlight_type="ADD"
            ),
            EvidenceBox(
                page_no=7, bbox=BBox(x0=68.7, y0=362.7, x1=126.3, y1=378.3), text="授权代表签", highlight_type="ADD"
            ),
            EvidenceBox(
                page_no=7, bbox=BBox(x0=68.7, y0=380.2, x1=125.8, y1=393.8), text="纳税人识别", highlight_type="ADD"
            ),
            EvidenceBox(
                page_no=7, bbox=BBox(x0=337.2, y0=486.2, x1=408.8, y1=522.8), text="2026.4.17", highlight_type="ADD"
            ),
            EvidenceBox(
                page_no=7, bbox=BBox(x0=501.2, y0=349.2, x1=565.8, y1=383.3), text="刘万程", highlight_type="ADD"
            ),
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


def test_diff_quality_keeps_missing_signature_label_when_compare_only_has_authorized_representative_clause_text() -> (
    None
):
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
        "15. 特别约定\n本合同经双方法定代表人(负责人)或其授权代表签署并加盖双方公章后生效。\n(以下无正文)",
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


def test_diff_quality_keeps_visible_cover_annotation_fragments_for_review() -> None:
    diffs = [
        DiffItem(
            diff_id="D005",
            diff_type="ADD",
            source_type="metadata",
            title="封面额外文本",
            compare_text="Cakilu-7020513",
            compare_snippet="Cakilu-7020513",
            review_flags=[
                "PAGE_UNRELIABLE",
                "READING_ORDER_RISK",
                "SEAL_OR_SIGNATURE_RISK",
                "POSSIBLE_COVER_OCR_FRAGMENT",
            ],
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
        ),
    ]

    result = DiffQualityProcessor().process(diffs)

    assert [diff.diff_id for diff in result.diffs] == ["D005", "D007"]
    assert all(diff.quality_status == "NEEDS_REVIEW" for diff in result.diffs)
    assert not any(
        decision.action == "suppressed_low_value_noise" and decision.diff_id in {"D005", "D007"}
        for decision in result.decisions
    )


def test_diff_quality_keeps_single_cjk_cover_stamp_fragment_near_top_for_review() -> None:
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

    assert [item.diff_id for item in result.diffs] == ["D007"]
    assert result.diffs[0].quality_status == "NEEDS_REVIEW"
    assert not any(decision.action == "suppressed_low_value_noise" for decision in result.decisions)


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
    assert reclassified.diff_type == "ADD"
    assert reclassified.source_type == "metadata"
    assert reclassified.title == "签署日期"
    assert reclassified.original_snippet == ""
    assert "SIGNING_DATE_FIELD_CHANGE" in reclassified.review_flags
    assert "CRITICAL_FIELD_CHANGE" not in reclassified.review_flags
    assert "CRITICAL_VALUE_CHANGE" not in reclassified.review_flags
    assert reclassified.readable_change == "新增签署日期：20260522"
    assert {evidence.highlight_type for evidence in reclassified.compare_evidence} == {"ADD"}
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
    assert reclassified.diff_type == "ADD"
    assert reclassified.source_type == "metadata"
    assert reclassified.title == "签署日期"
    assert reclassified.original_snippet == ""
    assert "SIGNING_DATE_FIELD_CHANGE" in reclassified.review_flags
    assert "CRITICAL_FIELD_DURATION_CHANGE" not in reclassified.review_flags
    assert "CRITICAL_VALUE_CHANGE" not in reclassified.review_flags


def test_diff_quality_reclassifies_cleared_signing_date_as_delete() -> None:
    diff = DiffItem(
        diff_id="D020",
        diff_type="MODIFY",
        source_type="clause",
        title="协议的效力和变更",
        original_text=(
            "第六条协议的效力和变更\n"
            "甲方:定边县瑞能新能源科技有限公司乙方:国能日新科技股份有限公司\n"
            "2026年05月22日\n年月日"
        ),
        compare_text=(
            "第六条协议的效力和变更\n甲方:定边县瑞能新能源科技有限公司乙方:国能日新科技股份有限公司\n年月日\n年月日"
        ),
        original_snippet="20260522",
        compare_snippet="月",
        review_flags=["CRITICAL_FIELD_CHANGE", "CRITICAL_FIELD_DATE_CHANGE"],
        original_evidence=[
            EvidenceBox(
                page_no=40,
                bbox=BBox(x0=85, y0=613, x1=185, y1=638),
                text="20260522",
                highlight_type="MODIFY",
            )
        ],
    )

    result = DiffQualityProcessor().process([diff])

    reclassified = result.diffs[0]
    assert reclassified.diff_type == "DELETE"
    assert reclassified.source_type == "metadata"
    assert reclassified.original_snippet == "20260522"
    assert reclassified.compare_snippet == ""
    assert reclassified.readable_change == "删除签署日期：20260522"
    assert {evidence.highlight_type for evidence in reclassified.original_evidence} == {"DELETE"}


def test_diff_quality_keeps_replaced_signing_date_as_modify() -> None:
    diff = DiffItem(
        diff_id="D021",
        diff_type="MODIFY",
        source_type="clause",
        title="协议的效力和变更",
        original_text=(
            "第六条协议的效力和变更\n"
            "甲方:定边县瑞能新能源科技有限公司乙方:国能日新科技股份有限公司\n"
            "2026年05月21日\n年月日"
        ),
        compare_text=(
            "第六条协议的效力和变更\n"
            "甲方:定边县瑞能新能源科技有限公司乙方:国能日新科技股份有限公司\n"
            "2026年05月22日\n年月日"
        ),
        original_snippet="20260521",
        compare_snippet="20260522",
        review_flags=["CRITICAL_FIELD_CHANGE", "CRITICAL_FIELD_DATE_CHANGE"],
    )

    result = DiffQualityProcessor().process([diff])

    reclassified = result.diffs[0]
    assert reclassified.diff_type == "MODIFY"
    assert reclassified.source_type == "metadata"
    assert reclassified.readable_change == "签署日期变更：20260521 → 20260522"


def test_diff_quality_reclassifies_cleared_table_signing_date_as_delete() -> None:
    diff = DiffItem(
        diff_id="D022",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：签订时间",
        original_text="签订时间：2026年05月22日",
        compare_text="签订时间：年月日",
        original_snippet="20260522",
        compare_snippet="年月日",
        original_evidence=[
            EvidenceBox(
                page_no=13,
                bbox=BBox(x0=85, y0=513, x1=185, y1=535),
                text="20260522",
                highlight_type="MODIFY",
            )
        ],
    )

    result = DiffQualityProcessor().process([diff])

    reclassified = result.diffs[0]
    assert reclassified.diff_type == "DELETE"
    assert reclassified.source_type == "metadata"
    assert reclassified.title == "签署日期"
    assert reclassified.readable_change == "删除签署日期：20260522"


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
            TextRange(start=67, end=78, highlight_type="ADD"),
        ],
        original_evidence=[
            EvidenceBox(
                page_no=13, bbox=BBox(x0=83, y0=140, x1=137, y1=154), text="单位名称:", highlight_type="DELETE"
            ),
            EvidenceBox(
                page_no=13,
                bbox=BBox(x0=142, y0=140, x1=286, y1=154),
                text="定边县瑞能新能源科技有限",
                highlight_type="DELETE",
            ),
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
    assert reclassified.diff_type == "ADD"
    assert reclassified.source_type == "metadata"
    assert reclassified.title == "签署日期"
    assert reclassified.original_snippet == ""
    assert reclassified.compare_snippet == "20260522"
    assert {evidence.text for evidence in reclassified.original_evidence} == set()
    assert {evidence.text for evidence in reclassified.compare_evidence} == {"2026", "05", "22"}
    assert {evidence.highlight_type for evidence in reclassified.compare_evidence} == {"ADD"}
    assert {item.highlight_type for item in reclassified.compare_change_ranges} == {"ADD"}
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


def test_diff_quality_suppresses_accented_latin_edge_noise() -> None:
    diff = DiffItem(
        diff_id="D015",
        diff_type="MODIFY",
        source_type="clause",
        original_text="双方应遵守本条款。",
        compare_text="à\n双方应遵守本条款。",
        original_snippet="",
        compare_snippet="à",
        match_score=100,
        review_flags=["LAYOUT_MISMATCH_RISK", "OCR_REMEDIATION_PLANNED"],
        quality_status="NEEDS_REVIEW",
        compare_evidence=[
            EvidenceBox(
                page_no=15,
                bbox=BBox(x0=24.2, y0=175.2, x1=26.8, y1=182.3),
                method="char_exact",
                text="à",
                confidence=0.98,
                evidence_quality="HIGH",
            )
        ],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
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


def test_diff_quality_suppresses_unreliable_signing_contact_table_label_loss_without_prior_review_flag() -> None:
    diff = DiffItem(
        diff_id="D020",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：联系人",
        original_text="甲方（盖章）： 国能长源随州发电有限公司随县分公司 | 乙方（盖章）： 国能日新科技股份有限公司",
        compare_text="甲方 国能长源随州发电有限公司随县分公司 | 国能日新科技股份有限公司",
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(decision.action == "table_region_review" and decision.diff_id == "D020" for decision in result.decisions)
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D020"
        and decision.detail["reason"] == "seal_occluded_signing_label_covered"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_form_separator_only_change() -> None:
    diff = DiffItem(
        diff_id="D036",
        diff_type="MODIFY",
        source_type="clause",
        clause_no="(二)",
        original_text="（三）_____/_____费用由乙方承担;",
        compare_text="（三）//费用由乙方承担;",
        original_snippet="(三)费用由乙方承担:",
        compare_snippet="(三)//费用由乙方承担;",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise" and decision.detail["reason"] == "form_separator_equivalent"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_seal_occluded_signing_label_table_text() -> None:
    diff = DiffItem(
        diff_id="D020",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：联系人",
        original_text="甲方（盖章）： 国能长源随州发电有限公司随县分公司 | 乙方（盖章）： 国能日新科技股份有限公司",
        compare_text="甲方 国能长源随州发电有限公司随县分公司 | 国能日新科技股份有限公司",
        review_flags=["TABLE_REGION_REVIEW"],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.detail["reason"] == "seal_occluded_signing_label_covered"
        for decision in result.decisions
    )


def test_diff_quality_keeps_signing_table_party_role_swap() -> None:
    diff = DiffItem(
        diff_id="D020_ROLE_SWAP",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：联系人",
        original_text="甲方（盖章）： 华北能源有限公司 | 乙方（盖章）： 国能日新科技股份有限公司",
        compare_text="乙方（盖章）： 华北能源有限公司 | 甲方（盖章）： 国能日新科技股份有限公司",
        review_flags=["TABLE_REGION_REVIEW"],
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D020_ROLE_SWAP"]
    assert not any(
        decision.action == "suppressed_low_value_noise"
        and decision.detail["reason"] == "seal_occluded_signing_label_covered"
        for decision in result.decisions
    )


def test_diff_quality_keeps_signing_table_contact_field_change() -> None:
    diff = DiffItem(
        diff_id="D020_CONTACT_CHANGE",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：联系人",
        original_text="甲方（盖章）： 华北能源有限公司 地址：北京市海淀区1号 | 乙方（盖章）： 国能日新科技股份有限公司",
        compare_text="甲方 华北能源有限公司 地址：上海市浦东新区2号 | 国能日新科技股份有限公司",
        review_flags=["TABLE_REGION_REVIEW"],
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D020_CONTACT_CHANGE"]
    assert not any(
        decision.action == "suppressed_low_value_noise"
        and decision.detail["reason"] == "seal_occluded_signing_label_covered"
        for decision in result.decisions
    )


def test_diff_quality_keeps_signing_table_address_change_with_zhang_character() -> None:
    diff = DiffItem(
        diff_id="D020_ADDRESS_ZHANG_CHANGE",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：联系人",
        original_text="甲方（盖章）： 华北能源有限公司 地址：山东省济南市章丘区1号 | 乙方（盖章）： 国能日新科技股份有限公司",
        compare_text="甲方 华北能源有限公司 地址：山东省济南市丘区1号 | 国能日新科技股份有限公司",
        review_flags=["TABLE_REGION_REVIEW"],
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D020_ADDRESS_ZHANG_CHANGE"]
    assert not any(
        decision.action == "suppressed_low_value_noise"
        and decision.detail["reason"] == "seal_occluded_signing_label_covered"
        for decision in result.decisions
    )


def test_diff_quality_keeps_signing_table_contact_role_text_change() -> None:
    diff = DiffItem(
        diff_id="D020_CONTACT_ROLE_TEXT_CHANGE",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：联系人",
        original_text="甲方（盖章）： 华北能源有限公司 联系人：乙方项目经理张三 | 乙方（盖章）： 国能日新科技股份有限公司",
        compare_text="甲方 华北能源有限公司 联系人：甲方项目经理张三 | 国能日新科技股份有限公司",
        review_flags=["TABLE_REGION_REVIEW"],
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D020_CONTACT_ROLE_TEXT_CHANGE"]
    assert not any(
        decision.action == "suppressed_low_value_noise"
        and decision.detail["reason"] == "seal_occluded_signing_label_covered"
        for decision in result.decisions
    )


def test_diff_quality_keeps_signing_table_remark_with_seal_word_change() -> None:
    diff = DiffItem(
        diff_id="D020_REMARK_SEAL_WORD_CHANGE",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：联系人",
        original_text="甲方（盖章）： 华北能源有限公司 备注：公章编号A | 乙方（盖章）： 国能日新科技股份有限公司",
        compare_text="甲方 华北能源有限公司 备注：编号A | 国能日新科技股份有限公司",
        review_flags=["TABLE_REGION_REVIEW"],
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D020_REMARK_SEAL_WORD_CHANGE"]
    assert not any(
        decision.action == "suppressed_low_value_noise"
        and decision.detail["reason"] == "seal_occluded_signing_label_covered"
        for decision in result.decisions
    )


def test_diff_quality_keeps_signing_table_party_qualifier_change() -> None:
    diff = DiffItem(
        diff_id="D020_PARTY_QUALIFIER_CHANGE",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：联系人",
        original_text="甲方（采购方）： 华北能源有限公司 | 乙方（供应商）： 国能日新科技股份有限公司",
        compare_text="甲方（业主方）： 华北能源有限公司 | 乙方（供应商）： 国能日新科技股份有限公司",
        review_flags=["TABLE_REGION_REVIEW"],
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D020_PARTY_QUALIFIER_CHANGE"]
    assert not any(
        decision.action == "suppressed_low_value_noise"
        and decision.detail["reason"] == "seal_occluded_signing_label_covered"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_table_header_serialization_equivalent() -> None:
    diff = DiffItem(
        diff_id="D023",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：封面信息",
        original_text="要求（甲方填写） | 乙方响应",
        compare_text="要求（甲方填写）乙方响应",
        original_snippet="要求（甲方填写） | 乙方响应",
        compare_snippet="要求（甲方填写）乙方响应",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise"
        and decision.detail["reason"] == "table_header_serialization_equivalent"
        for decision in result.decisions
    )


def test_diff_quality_keeps_signing_table_signature_and_date_additions() -> None:
    signature_diff = DiffItem(
        diff_id="D021",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：联系人",
        original_text="法定代表人（负责人）或 授权代表（签字）： | 法定代表人（负责人）或 授权代表（签字）：",
        compare_text="法定负责人 授权代表300240 | 法定代表负责人 或 各商专用 授权代表 22号3",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "TABLE_REGION_REVIEW"],
    )
    date_diff = DiffItem(
        diff_id="D022",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：联系人",
        original_text="签订时间: | 签订时间:",
        compare_text="签订时间：2026年5月15日 | 签订时间：2026年5月15日",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "TABLE_REGION_REVIEW"],
    )

    result = DiffQualityProcessor().process([signature_diff, date_diff])

    assert [item.diff_id for item in result.diffs] == ["D021", "D022"]


def test_diff_quality_suppresses_isolated_seal_region_ocr_fragment() -> None:
    diff = DiffItem(
        diff_id="D027",
        diff_type="ADD",
        source_type="seal",
        title="印章区域（第29页）",
        compare_text="图",
        compare_snippet="图",
        review_flags=[
            "OCR_LOW_CONFIDENCE",
            "PAGE_UNRELIABLE",
            "READING_ORDER_RISK",
            "SEAL_OR_SIGNATURE_RISK",
            "OCR_REMEDIATION_MANUAL_REVIEW",
            "OCR_REMEDIATION_PLANNED",
        ],
        compare_evidence=[EvidenceBox(page_no=29, bbox=BBox(x0=420, y0=300, x1=440, y1=330), text="图")],
    )

    result = DiffQualityProcessor().process([diff])

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise" and decision.detail["reason"] == "isolated_seal_artifact_text"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_short_clause_fragment_rendered_as_red_seal(tmp_path: Path) -> None:
    compare_path = tmp_path / "compare.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    page.draw_rect(fitz.Rect(380, 290, 470, 320), color=(1, 0, 0), fill=(1, 0, 0))
    pdf.save(compare_path)
    pdf.close()
    compare_document = Document(
        filename=compare_path.name,
        path=str(compare_path),
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=[])],
    )
    diff = DiffItem(
        diff_id="D009",
        diff_type="MODIFY",
        source_type="clause",
        original_text="22.5 本合同一式两份。",
        compare_text="22.5 本合同一式两份。\n公技股",
        compare_snippet="公技股",
        review_flags=["SEAL_OR_SIGNATURE_RISK", "POSSIBLE_OCR_NOISE"],
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=390, y0=297, x1=454, y1=313),
                method="char_exact",
                text="公技股",
            )
        ],
    )

    result = DiffQualityProcessor().process([diff], compare_document=compare_document)

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_low_value_noise" and decision.detail["reason"] == "visual_seal_ocr_fragment"
        for decision in result.decisions
    )


def test_diff_quality_keeps_short_clause_fragment_without_red_seal_pixels(tmp_path: Path) -> None:
    compare_path = tmp_path / "compare.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((390, 310), "ABC", color=(0, 0, 0))
    pdf.save(compare_path)
    pdf.close()
    compare_document = Document(
        filename=compare_path.name,
        path=str(compare_path),
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=[])],
    )
    diff = DiffItem(
        diff_id="D010",
        diff_type="MODIFY",
        source_type="clause",
        original_text="22.5 本合同一式两份。",
        compare_text="22.5 本合同一式两份。\n补充项",
        compare_snippet="补充项",
        review_flags=["SEAL_OR_SIGNATURE_RISK", "POSSIBLE_OCR_NOISE"],
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=385, y0=295, x1=455, y1=315),
                method="char_exact",
                text="补充项",
            )
        ],
    )

    result = DiffQualityProcessor().process([diff], compare_document=compare_document)

    assert [item.diff_id for item in result.diffs] == ["D010"]


def test_diff_quality_keeps_real_seal_or_signature_region_addition() -> None:
    diff = DiffItem(
        diff_id="D026",
        diff_type="ADD",
        source_type="seal",
        title="印章区域（第29页）",
        compare_text="甲方（盖章） 法定代表人（负责人）/授权代表（签字） 42130130002406",
        compare_snippet="甲方（盖章） 法定代表人（负责人）/授权代表（签字） 42130130002406",
        review_flags=["SEAL_OR_SIGNATURE_RISK", "OCR_REMEDIATION_MANUAL_REVIEW", "OCR_REMEDIATION_PLANNED"],
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D026"]


def test_diff_quality_downgrades_unreliable_signing_table_visual_change() -> None:
    diff = DiffItem(
        diff_id="D012",
        diff_type="MODIFY",
        source_type="signing_region",
        section_type="signature",
        title="签署区（第2页）",
        original_text="单位地址：北京市海淀区；法人代表：雍正",
        compare_text="",
        review_flags=[
            "PAGE_UNRELIABLE",
            "SIGNING_TABLE_CHANGE",
            "SIGNING_VISUAL_CHANGE",
            "CRITICAL_VALUE_CHANGE",
        ],
    )

    result = DiffQualityProcessor().process([diff])
    processed = result.diffs[0]

    assert processed.quality_status == "NEEDS_REVIEW"
    assert "SIGNING_REGION_REVIEW" in processed.review_flags
    assert "CRITICAL_VALUE_CHANGE" not in processed.review_flags


def test_diff_quality_keeps_short_meaningful_seal_region_text() -> None:
    diffs = [
        DiffItem(
            diff_id=f"D026_{index}",
            diff_type="ADD",
            source_type="seal",
            title="印章区域（第29页）",
            compare_text=text,
            compare_snippet=text,
            review_flags=["SEAL_OR_SIGNATURE_RISK", "OCR_REMEDIATION_MANUAL_REVIEW"],
        )
        for index, text in enumerate(["张三", "签字", "公章", "甲", "10", "￥5"], start=1)
    ]

    result = DiffQualityProcessor().process(diffs)

    assert [item.diff_id for item in result.diffs] == [item.diff_id for item in diffs]


def test_diff_quality_keeps_e9_real_visual_differences() -> None:
    diffs = [
        DiffItem(
            diff_id="D050",
            diff_type="MODIFY",
            source_type="clause",
            original_text="按以下第一种方式处理:",
            compare_text="按以下第二种方式处理:",
            original_snippet="一",
            compare_snippet="二",
            review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        ),
        DiffItem(
            diff_id="D032",
            diff_type="MODIFY",
            source_type="clause",
            original_text="人民币(大写)柒万捌仟元整(¥73000.00元)",
            compare_text="人民币(大写)柒万叁仟元整(¥73000.00元)",
            original_snippet="捌仟",
            compare_snippet="叁仟",
            review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
        ),
        DiffItem(
            diff_id="D082",
            diff_type="MODIFY",
            source_type="clause",
            original_text="电监安全(2006)34号",
            compare_text="电监安全〔2006〕34号",
            original_snippet="(2006)34",
            compare_snippet="〔2006〕34",
            review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        ),
        DiffItem(
            diff_id="D073",
            diff_type="MODIFY",
            source_type="clause",
            original_text="按以下第一种方式处理:",
            compare_text="按以下第二种方式处理:",
            original_snippet="一/",
            compare_snippet="二",
            review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        ),
    ]

    result = DiffQualityProcessor().process(diffs)

    assert [item.diff_id for item in result.diffs] == ["D050", "D032", "D082", "D073"]


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


def test_diff_quality_does_not_merge_evidence_from_unchanged_side() -> None:
    original_text = "2026年4月 日"
    compare_text = "2026年4月21日"
    original_evidence = EvidenceBox(
        page_no=1,
        bbox=BBox(x0=240, y0=640, x1=380, y1=680),
        method="text_exact",
        text=original_text,
        highlight_type="MODIFY",
    )
    diffs = [
        DiffItem(
            diff_id="D001_TABLE",
            diff_type="MODIFY",
            source_type="table",
            original_text=original_text,
            compare_text=compare_text,
            original_snippet=original_text,
            compare_snippet="21",
            original_evidence=[original_evidence],
        ),
        DiffItem(
            diff_id="D002_METADATA",
            diff_type="MODIFY",
            source_type="metadata",
            title="封面字段：签订日期",
            original_text=original_text,
            compare_text=compare_text,
            original_snippet="",
            compare_snippet="21",
            compare_evidence=[
                EvidenceBox(
                    page_no=1,
                    bbox=BBox(x0=337, y0=643, x1=365, y1=666),
                    method="cover_metadata",
                    text="21",
                    highlight_type="ADD",
                )
            ],
            compare_change_ranges=[TextRange(start=7, end=9, highlight_type="ADD")],
            review_flags=["CRITICAL_VALUE_CHANGE", "EVIDENCE_UNRELIABLE", "OCR_REMEDIATION_PLANNED"],
        ),
    ]

    result = DiffQualityProcessor().process(diffs)

    assert [diff.diff_id for diff in result.diffs] == ["D002_METADATA"]
    assert result.diffs[0].original_evidence == []


def test_diff_quality_prefers_multi_page_footer_over_metadata_duplicate() -> None:
    diffs = [
        DiffItem(
            diff_id="D001_METADATA",
            diff_type="ADD",
            source_type="metadata",
            title="封面额外文本",
            compare_text="经办人：李四",
            compare_snippet="经办人：李四",
            compare_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=420, y0=780, x1=520, y1=810), text="经办人：李四")],
        ),
        DiffItem(
            diff_id="D002_FOOTER",
            diff_type="ADD",
            source_type="header_footer",
            title="页脚",
            compare_text="经办人：李四",
            compare_snippet="经办人：李四",
            compare_evidence=[
                EvidenceBox(
                    page_no=5, bbox=BBox(x0=420, y0=780, x1=520, y1=810), method="header_footer", text="经办人：李四"
                ),
                EvidenceBox(
                    page_no=6, bbox=BBox(x0=420, y0=780, x1=520, y1=810), method="header_footer", text="经办人：李四"
                ),
                EvidenceBox(
                    page_no=7, bbox=BBox(x0=420, y0=780, x1=520, y1=810), method="header_footer", text="经办人：李四"
                ),
            ],
        ),
    ]

    result = DiffQualityProcessor().process(diffs)

    assert len(result.diffs) == 1
    processed = result.diffs[0]
    assert processed.diff_id == "D002_FOOTER"
    assert processed.source_type == "header_footer"
    assert processed.title == "页脚"
    assert {evidence.page_no for evidence in processed.compare_evidence} == {1, 5, 6, 7}
    assert "metadata" in processed.merged_sources
    assert "CROSS_SOURCE_MERGED" in processed.review_flags
    assert processed.quality_status == "NEEDS_REVIEW"


def test_diff_quality_keeps_table_winner_over_multi_page_footer_duplicate() -> None:
    diffs = [
        DiffItem(
            diff_id="D001_TABLE",
            diff_type="ADD",
            source_type="table",
            title="经办人",
            compare_text="经办人：李四",
            compare_snippet="经办人：李四",
            compare_evidence=[
                EvidenceBox(
                    page_no=1, bbox=BBox(x0=420, y0=780, x1=520, y1=810), method="table_cell", text="经办人：李四"
                )
            ],
        ),
        DiffItem(
            diff_id="D002_FOOTER",
            diff_type="ADD",
            source_type="header_footer",
            title="页脚",
            compare_text="经办人：李四",
            compare_snippet="经办人：李四",
            compare_evidence=[
                EvidenceBox(
                    page_no=5, bbox=BBox(x0=420, y0=780, x1=520, y1=810), method="header_footer", text="经办人：李四"
                ),
                EvidenceBox(
                    page_no=6, bbox=BBox(x0=420, y0=780, x1=520, y1=810), method="header_footer", text="经办人：李四"
                ),
            ],
        ),
    ]

    result = DiffQualityProcessor().process(diffs)

    assert len(result.diffs) == 1
    processed = result.diffs[0]
    assert processed.diff_id == "D001_TABLE"
    assert processed.source_type == "table"
    assert {evidence.page_no for evidence in processed.compare_evidence} == {1, 5, 6}
    assert processed.merged_sources == ["header_footer"]


def test_diff_quality_keeps_metadata_for_single_page_footer_duplicate() -> None:
    diffs = [
        DiffItem(
            diff_id="D001_METADATA",
            diff_type="ADD",
            source_type="metadata",
            title="封面额外文本",
            compare_text="经办人：李四",
            compare_snippet="经办人：李四",
            compare_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=420, y0=780, x1=520, y1=810), text="经办人：李四")],
        ),
        DiffItem(
            diff_id="D002_FOOTER",
            diff_type="ADD",
            source_type="header_footer",
            title="页脚",
            compare_text="经办人：李四",
            compare_snippet="经办人：李四",
            review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
            compare_evidence=[
                EvidenceBox(
                    page_no=5, bbox=BBox(x0=420, y0=780, x1=520, y1=810), method="header_footer", text="经办人：李四"
                )
            ],
        ),
    ]

    result = DiffQualityProcessor().process(diffs)

    assert len(result.diffs) == 1
    processed = result.diffs[0]
    assert processed.diff_id == "D001_METADATA"
    assert processed.source_type == "metadata"
    assert processed.title == "封面额外文本"
    assert "header_footer" in processed.merged_sources
    assert "CROSS_SOURCE_MERGED" in processed.review_flags


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


def test_diff_quality_suppresses_scan_only_unit_separator_loss() -> None:
    diff = DiffItem(
        diff_id="D068",
        diff_type="MODIFY",
        source_type="clause",
        original_text="应按照100万元/人次的标准向甲方支付违约金。",
        compare_text="应按照100万元人次的标准向甲方支付违约金。",
        original_snippet="/",
        review_flags=["CRITICAL_FIELD_AMOUNT_CHANGE", "CRITICAL_FIELD_CHANGE", "OCR_REMEDIATION_PLANNED"],
    )
    compare_document = Document(
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
                        block_id="scan",
                        page_no=1,
                        text=diff.compare_text,
                        bbox=BBox(x0=60, y0=100, x1=520, y1=140),
                        source="ppocrv5",
                    )
                ],
            )
        ],
    )

    result = DiffQualityProcessor().process([diff], compare_document=compare_document)

    assert result.diffs == []
    assert result.decisions[-1].detail["reason"] == "unit_separator_ocr_loss"


def test_diff_quality_reconciles_attachment_catalog_boundary_drift() -> None:
    catalog = "附件一:安全生产管理协议\n附件二:廉洁协议书\n附件三:谈判纪要\n附件一"
    body = "安全生产管理协议(模板)\n项目名称:新能源项目\n双方应遵守安全生产管理要求。"
    diffs = [
        DiffItem(
            diff_id="D101",
            diff_type="MODIFY",
            source_type="clause",
            section_type="appendix",
            original_text=f"{catalog}\n{body}\n项目管理工作。",
            compare_text=f"{body}\n项目管理工作。",
        ),
        DiffItem(
            diff_id="D118",
            diff_type="ADD",
            source_type="clause",
            section_type="appendix",
            compare_text=catalog,
        ),
    ]

    result = DiffQualityProcessor().process(diffs)

    assert result.diffs == []
    assert result.decisions[0].action == "attachment_boundary_drift_reconciled"


def test_diff_quality_suppresses_short_character_omission_on_scanned_page() -> None:
    original = "5.9 若项目提前竣工，乙方可向甲方提交书面申请，经甲方核查确认后退还保函原件。"
    compare = "5.9 若项目提前竣工，乙方可甲方提交书面申请，经甲方查确认后退还保函原件。"
    diff = DiffItem(
        diff_id="D104",
        diff_type="MODIFY",
        source_type="clause",
        original_text=original,
        compare_text=compare,
        match_score=100,
        match_score_details={"body_score": 98.6},
        original_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=60, y0=100, x1=520, y1=160), text="向核")],
        review_flags=["CRITICAL_FIELD_CHANGE"],
    )
    compare_document = Document(
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
                        block_id="scan",
                        page_no=1,
                        text=compare,
                        bbox=BBox(x0=60, y0=100, x1=520, y1=160),
                        source="ppocrv5",
                    )
                ],
            )
        ],
    )

    result = DiffQualityProcessor().process([diff], compare_document=compare_document)

    assert result.diffs == []
    assert result.decisions[-1].detail["reason"] == "scan_ocr_character_omission"


def test_diff_quality_reclassifies_appendix_signing_date_and_drops_footer_pollution() -> None:
    diff = DiffItem(
        diff_id="D111",
        diff_type="MODIFY",
        source_type="clause",
        original_text="附件五:技术协议\n甲方:A公司\n乙方:B公司\n签订日期:2026年 月日",
        compare_text="附件五:技术协议\n甲方:A公司\n乙方:B公司\n签订日期:2026年5月6日\n奇绿",
        original_snippet="附件五",
        compare_snippet="56奇绿",
        review_flags=["CRITICAL_FIELD_CHANGE", "CRITICAL_FIELD_DATE_CHANGE", "OCR_REMEDIATION_PLANNED"],
    )

    result = DiffQualityProcessor().process([diff])

    assert len(result.diffs) == 1
    processed = result.diffs[0]
    assert processed.source_type == "metadata"
    assert processed.diff_type == "ADD"
    assert processed.compare_snippet == "20260506"
    assert "奇绿" not in processed.readable_change


def test_diff_quality_normalizes_financial_uppercase_ocr_glyphs() -> None:
    diff = DiffItem(
        diff_id="D091",
        diff_type="MODIFY",
        source_type="clause",
        original_text="总价为人民币(大写)柒万捌仟元整(￥73000.00元)",
        compare_text="总价为人民币(大写)柒万叁任元整(￥73000.00元)",
        original_snippet="捌仟仟",
        compare_snippet="叁任任",
        match_score=100,
    )

    result = DiffQualityProcessor().process([diff])

    assert [(item.original_snippet, item.compare_snippet) for item in result.diffs] == [("捌", "叁")]
    assert result.diffs[0].title == "合同总价（大写）"
    assert result.diffs[0].readable_change == "原文：捌\n修改后：叁"


def test_diff_quality_reclassifies_scanned_participant_handwriting_without_ocr_names() -> None:
    diff = DiffItem(
        diff_id="D107",
        diff_type="MODIFY",
        source_type="clause",
        section_type="main_contract",
        original_text="15.3 在争议解决期间，未涉及争议的条款仍须履行。\n参与人员：",
        compare_text="15.3 在争议解决期间，未涉及争议的条款仍须履行。\n参与人员：共 评静\n44意",
        compare_snippet="共 评静44意",
        readable_change="原文：\n修改后：共 评静44意",
        review_flags=["OCR_REMEDIATION_PLANNED", "CRITICAL_FIELD_CHANGE"],
        compare_evidence=[
            EvidenceBox(
                page_no=34,
                bbox=BBox(x0=139.7, y0=438.7, x1=244.3, y1=488.3),
                method="char_exact",
                text="共 评静",
            )
        ],
    )

    result = DiffQualityProcessor().process([diff])

    processed = result.diffs[0]
    assert (processed.source_type, processed.section_type, processed.diff_type) == ("metadata", "signature", "ADD")
    assert processed.title == "参与人员手写签名"
    assert processed.readable_change == "参与人员手写签名：未签 → 已签"
    assert "共 评静44意" not in processed.readable_change
    assert "HANDWRITTEN_PARTICIPANT_SIGNATURE_CHANGE" in processed.review_flags


def test_diff_quality_suppresses_choice_numeral_ocr_error_when_rendered_glyphs_match(tmp_path: Path) -> None:
    original_document = _choice_stroke_document(tmp_path / "original.pdf", 2)
    compare_document = _choice_stroke_document(tmp_path / "compare.pdf", 2)
    diff = _choice_numeral_diff("一/", "二")

    result = DiffQualityProcessor().process(
        [diff],
        original_document=original_document,
        compare_document=compare_document,
    )

    assert result.diffs == []
    assert any(decision.detail.get("reason") == "rendered_choice_numeral_equivalent" for decision in result.decisions)


def test_diff_quality_keeps_real_choice_numeral_change_when_rendered_glyphs_differ(tmp_path: Path) -> None:
    original_document = _choice_stroke_document(tmp_path / "original.pdf", 1)
    compare_document = _choice_stroke_document(tmp_path / "compare.pdf", 2)
    diff = _choice_numeral_diff("一", "二")

    result = DiffQualityProcessor().process(
        [diff],
        original_document=original_document,
        compare_document=compare_document,
    )

    assert [item.diff_id for item in result.diffs] == ["D104"]


def _choice_stroke_document(path: Path, stroke_count: int) -> Document:
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    y_positions = {1: [115], 2: [108, 122], 3: [105, 115, 125]}[stroke_count]
    for y in y_positions:
        page.draw_line(fitz.Point(102, y), fitz.Point(118, y), color=(0, 0, 0), width=1.5)
    pdf.save(path)
    pdf.close()
    return Document(
        filename=path.name,
        path=str(path),
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=[])],
    )


def _choice_numeral_diff(original_snippet: str, compare_snippet: str) -> DiffItem:
    original_numeral = next(character for character in original_snippet if character in "一二三")
    compare_numeral = next(character for character in compare_snippet if character in "一二三")
    bbox = BBox(x0=100, y0=100, x1=120, y1=130)
    return DiffItem(
        diff_id="D104",
        diff_type="MODIFY",
        source_type="clause",
        original_text=f"15.2 若争议仍无法解决，按以下第{original_numeral}种方式处理。",
        compare_text=f"15.2 若争议仍无法解决，按以下第{compare_numeral}种方式处理。",
        original_snippet=original_snippet,
        compare_snippet=compare_snippet,
        review_flags=["CRITICAL_FIELD_CHANGE", "READING_ORDER_RISK"],
        original_evidence=[EvidenceBox(page_no=1, bbox=bbox, text=original_numeral)],
        compare_evidence=[EvidenceBox(page_no=1, bbox=bbox, text=compare_numeral)],
    )
