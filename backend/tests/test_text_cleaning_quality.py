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


def test_document_preparation_marks_final_signature_area_and_splitter_skips_it() -> None:
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

    assert {decision.block_id for decision in result.decisions} == {"sig1", "sig2"}
    assert len(clauses) == 1
    assert clauses[0].source_block_ids == ["body"]


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
