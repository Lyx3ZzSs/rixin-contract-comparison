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
    assert by_id["D022"].diff_type == "ADD"
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


def test_diff_quality_suppresses_compare_heading_add_when_original_has_bare_parent_and_children() -> None:
    heading_add = DiffItem(
        diff_id="D113",
        diff_type="ADD",
        source_type="clause",
        title="服务期限与进度要求",
        compare_text="3. 服务期限与进度要求",
        compare_snippet="3. 服务期限与进度要求",
        structural_flags=["PARAGRAPH_MERGED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        compare_clause_id="NC012",
        compare_evidence=[
            EvidenceBox(
                page_no=3,
                bbox=BBox(x0=61, y0=83, x1=182, y1=102),
                method="block",
                text="3. 服务期限与进度要求",
            )
        ],
    )
    original = _doc(
        3,
        "2.\n"
        "乙方应按合同约定向甲方提供以下技术服务:\n"
        "3.\n"
        "3.1 服务期限: 自2026年1月1日至2026年12月31日。",
    )
    compare = _doc(
        3,
        "2. 服务内容\n"
        "乙方应按合同约定向甲方提供以下技术服务:\n"
        "3. 服务期限与进度要求\n"
        "3.1 服务期限: 自2026年1月1日至2026年12月31日。",
    )

    result = DiffQualityProcessor().process(
        [heading_add],
        original_document=original,
        compare_document=compare,
        original_clauses=[
            Clause(
                clause_id="OC011",
                clause_no="2",
                title="乙方应按合同约定向甲方提供以下技术服务:",
                text="2.\n乙方应按合同约定向甲方提供以下技术服务:\n3.",
                normalized_text="2乙方应按合同约定向甲方提供以下技术服务3",
                page_numbers=[3],
                split_flags=["PARAGRAPH_MERGED"],
            ),
            Clause(
                clause_id="OC012",
                clause_no="3.1",
                title="服务期限",
                text="3.1 服务期限: 自2026年1月1日至2026年12月31日。",
                normalized_text="31服务期限自2026年1月1日至2026年12月31日",
                page_numbers=[3],
            ),
        ],
        compare_clauses=[
            Clause(
                clause_id="NC012",
                clause_no="3",
                title="服务期限与进度要求",
                text="3. 服务期限与进度要求",
                normalized_text="3服务期限与进度要求",
                page_numbers=[3],
                split_flags=["PARAGRAPH_MERGED"],
            ),
            Clause(
                clause_id="NC013",
                clause_no="3.1",
                title="服务期限",
                text="3.1 服务期限: 自2026年1月1日至2026年12月31日。",
                normalized_text="31服务期限自2026年1月1日至2026年12月31日",
                page_numbers=[3],
            ),
        ],
    )

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D113"
        and decision.detail["reason"] == "heading_add_covered_by_opposite_numbering"
        for decision in result.decisions
    )


def test_diff_quality_suppresses_material_parent_heading_add_when_children_exist_on_both_sides() -> None:
    heading_add = DiffItem(
        diff_id="D114",
        diff_type="ADD",
        source_type="clause",
        title="合同价格及支付",
        compare_text="4. 合同价格及支付",
        compare_snippet="4. 合同价格及支付",
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        compare_clause_id="NC016",
        compare_evidence=[
            EvidenceBox(
                page_no=3,
                bbox=BBox(x0=61, y0=330, x1=168, y1=349),
                method="block",
                text="4. 合同价格及支付",
            )
        ],
    )
    original = _doc(3, "4.\n4.1 合同价格为人民币柒万叁仟元整。")
    compare = _doc(3, "4. 合同价格及支付\n4.1 合同价格为人民币柒万叁仟元整。")

    result = DiffQualityProcessor().process(
        [heading_add],
        original_document=original,
        compare_document=compare,
        original_clauses=[
            Clause(
                clause_id="OC015",
                clause_no="4.1",
                title="合同价格",
                text="4.1 合同价格为人民币柒万叁仟元整。",
                normalized_text="41合同价格为人民币柒万叁仟元整",
                page_numbers=[3],
            ),
        ],
        compare_clauses=[
            Clause(
                clause_id="NC016",
                clause_no="4",
                title="合同价格及支付",
                text="4. 合同价格及支付",
                normalized_text="4合同价格及支付",
                page_numbers=[3],
            ),
            Clause(
                clause_id="NC017",
                clause_no="4.1",
                title="合同价格",
                text="4.1 合同价格为人民币柒万叁仟元整。",
                normalized_text="41合同价格为人民币柒万叁仟元整",
                page_numbers=[3],
            ),
        ],
    )

    assert result.diffs == []
    assert any(
        decision.action == "suppressed_by_neighbor_clause_coverage"
        and decision.diff_id == "D114"
        and decision.detail["reason"] == "heading_add_covered_by_opposite_numbering"
        for decision in result.decisions
    )


def test_diff_quality_keeps_compare_heading_add_when_heading_clause_contains_body() -> None:
    heading_with_body = DiffItem(
        diff_id="D200",
        diff_type="ADD",
        source_type="clause",
        title="服务期限与进度要求",
        compare_text="3. 服务期限与进度要求\n新增要求: 乙方需提前10天提交进度计划。",
        compare_snippet="3. 服务期限与进度要求\n新增要求: 乙方需提前10天提交进度计划。",
        structural_flags=["PARAGRAPH_MERGED"],
        review_flags=["READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED", "CRITICAL_VALUE_CHANGE"],
        compare_clause_id="NC200",
        compare_evidence=[
            EvidenceBox(
                page_no=3,
                bbox=BBox(x0=61, y0=83, x1=420, y1=126),
                method="block",
                text="3. 服务期限与进度要求\n新增要求: 乙方需提前10天提交进度计划。",
            )
        ],
    )
    original = _doc(3, "3.\n3.1 服务期限: 自2026年1月1日至2026年12月31日。")
    compare = _doc(
        3,
        "3. 服务期限与进度要求\n"
        "新增要求: 乙方需提前10天提交进度计划。\n"
        "3.1 服务期限: 自2026年1月1日至2026年12月31日。",
    )

    result = DiffQualityProcessor().process(
        [heading_with_body],
        original_document=original,
        compare_document=compare,
        original_clauses=[
            Clause(
                clause_id="OC200",
                clause_no="3.1",
                title="服务期限",
                text="3.1 服务期限: 自2026年1月1日至2026年12月31日。",
                normalized_text="31服务期限自2026年1月1日至2026年12月31日",
                page_numbers=[3],
            ),
        ],
        compare_clauses=[
            Clause(
                clause_id="NC200",
                clause_no="3",
                title="服务期限与进度要求",
                text="3. 服务期限与进度要求\n新增要求: 乙方需提前10天提交进度计划。",
                normalized_text="3服务期限与进度要求新增要求乙方需提前10天提交进度计划",
                page_numbers=[3],
                split_flags=["PARAGRAPH_MERGED"],
            ),
        ],
    )

    assert [diff.diff_id for diff in result.diffs] == ["D200"]
