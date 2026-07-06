from __future__ import annotations

from app.models import BBox, Clause, DiffItem, Document, EvidenceBox, Page, TextBlock
from app.services.diff_quality import DiffQualityProcessor


def _doc(page_no: int, text: str) -> Document:
    return Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=page_no,
        pages=[
            Page(
                page_no=page_no,
                width=595,
                height=842,
                blocks=[TextBlock(block_id=f"p{page_no}_b1", page_no=page_no, text=text, bbox=BBox(x0=50, y0=80, x1=500, y1=120))],
            )
        ],
    )


def test_diff_quality_keeps_short_scanned_footer_handwriting_fragments() -> None:
    diffs = [
        DiffItem(
            diff_id="D002",
            diff_type="ADD",
            source_type="header_footer",
            title="页脚",
            compare_text="怀意",
            compare_snippet="怀意",
            review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
            compare_evidence=[
                EvidenceBox(page_no=10, bbox=BBox(x0=420, y0=780, x1=500, y1=830), method="header_footer", text="怀意")
            ],
        ),
        DiffItem(
            diff_id="D008",
            diff_type="ADD",
            source_type="header_footer",
            title="页脚",
            compare_text="S",
            compare_snippet="S",
            review_flags=["LAYOUT_MISMATCH_RISK", "OCR_LOW_CONFIDENCE", "PAGE_UNRELIABLE"],
            compare_evidence=[
                EvidenceBox(page_no=44, bbox=BBox(x0=8, y0=802, x1=30, y1=842), method="header_footer", text="S")
            ],
        ),
    ]

    result = DiffQualityProcessor().process(diffs)

    by_id = {diff.diff_id: diff for diff in result.diffs}

    assert set(by_id) == {"D002", "D008"}
    assert by_id["D002"].quality_status == "NEEDS_REVIEW"
    assert by_id["D008"].quality_status == "NEEDS_REVIEW"
    assert "HEADER_FOOTER_REVIEW" in by_id["D002"].review_flags
    assert "HEADER_FOOTER_REVIEW" in by_id["D008"].review_flags
    assert not any(
        decision.action == "suppressed_low_value_noise"
        and decision.detail.get("reason") == "header_footer_noise"
        for decision in result.decisions
    )


def test_diff_quality_keeps_repeated_cover_extra_footer_signature_name() -> None:
    diff = DiffItem(
        diff_id="D018",
        diff_type="ADD",
        source_type="metadata",
        title="封面额外文本",
        compare_text="黄科",
        compare_snippet="黄科",
        review_flags=[
            "EVIDENCE_UNRELIABLE",
            "OCR_REMEDIATION_UNRESOLVED",
            "LAYOUT_MISMATCH_RISK",
            "PAGE_UNRELIABLE",
            "POSSIBLE_COVER_OCR_FRAGMENT",
        ],
        compare_evidence=[
            EvidenceBox(page_no=3, bbox=BBox(x0=430, y0=785, x1=505, y1=830), method="cover_extra", text="黄科"),
            EvidenceBox(page_no=13, bbox=BBox(x0=430, y0=785, x1=505, y1=830), method="cover_extra", text="黄科"),
            EvidenceBox(page_no=21, bbox=BBox(x0=430, y0=785, x1=505, y1=830), method="cover_extra", text="黄科"),
        ],
    )

    result = DiffQualityProcessor().process([diff])

    assert [item.diff_id for item in result.diffs] == ["D018"]
    assert result.diffs[0].quality_status == "NEEDS_REVIEW"
    assert not any(
        decision.action == "suppressed_low_value_noise"
        and decision.diff_id == "D018"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_signing_table_label_noise_but_reclassifies_signing_date() -> None:
    label_noise = DiffItem(
        diff_id="D021",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：联系人",
        original_text="法定代表人（负责人）或 授权代表（签字）： | 法定代表人（负责人）或 授权代表（签字）：",
        compare_text="法定负责人 授权代表300240 | 法定代表负责人 或 各商专用 授权代表 22号3",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
    )
    signing_date = DiffItem(
        diff_id="D022",
        diff_type="MODIFY",
        source_type="table",
        title="表格字段：联系人",
        original_text="签订时间: | 签订时间:",
        compare_text="签订时间：2026年5月15日 | 签订时间：2026年5150",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"],
    )

    result = DiffQualityProcessor().process([label_noise, signing_date])
    by_id = {diff.diff_id: diff for diff in result.diffs}

    assert "D021" not in by_id
    assert by_id["D022"].source_type == "metadata"
    assert by_id["D022"].title == "签署日期"
    assert by_id["D022"].section_type == "signature"
    assert "SIGNING_DATE_FIELD_CHANGE" in by_id["D022"].review_flags


def test_diff_quality_suppresses_add_delete_heading_already_present_on_opposite_page() -> None:
    delete_heading = DiffItem(
        diff_id="D099",
        diff_type="DELETE",
        source_type="clause",
        title="安全目标",
        original_text="1 安全目标",
        original_snippet="1 安全目标",
        review_flags=["SHORT_CLAUSE_MATCH_REVIEW", "CRITICAL_VALUE_CHANGE"],
        original_evidence=[
            EvidenceBox(page_no=14, bbox=BBox(x0=64, y0=503, x1=145, y1=520), method="block", text="1 安全目标")
        ],
    )
    add_heading = DiffItem(
        diff_id="D125",
        diff_type="ADD",
        source_type="clause",
        title="争议解决",
        compare_text="15. 争议解决",
        compare_snippet="15. 争议解决",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        compare_evidence=[
            EvidenceBox(page_no=34, bbox=BBox(x0=65, y0=253, x1=126, y1=273), method="block", text="15. 争议解决")
        ],
    )
    original = Document(
        filename="original.pdf",
        path="original.pdf",
        page_count=2,
        pages=[
            _doc(14, "1 安全目标").pages[0],
            _doc(34, "15. 争议解决").pages[0],
        ],
    )
    compare = Document(
        filename="compare.pdf",
        path="compare.pdf",
        page_count=2,
        pages=[
            _doc(14, "1 安全目标").pages[0],
            _doc(34, "15. 争议解决").pages[0],
        ],
    )

    result = DiffQualityProcessor().process(
        [delete_heading, add_heading],
        original_document=original,
        compare_document=compare,
        original_clauses=[
            Clause(clause_id="OC099", clause_no="1", title="安全目标", text="1 安全目标", normalized_text="1安全目标", page_numbers=[14]),
            Clause(clause_id="OC125", clause_no="15", title="争议解决", text="15. 争议解决", normalized_text="15争议解决", page_numbers=[34]),
        ],
        compare_clauses=[
            Clause(clause_id="NC099", clause_no="1", title="安全目标", text="1 安全目标", normalized_text="1安全目标", page_numbers=[14]),
            Clause(clause_id="NC125", clause_no="15", title="争议解决", text="15. 争议解决", normalized_text="15争议解决", page_numbers=[34]),
        ],
    )

    assert result.diffs == []
    assert {
        decision.diff_id
        for decision in result.decisions
        if decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.detail["reason"] == "heading_text_present_on_opposite_page"
    } == {"D099", "D125"}
