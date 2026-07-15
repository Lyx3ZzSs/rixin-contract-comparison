from __future__ import annotations

from pathlib import Path

import fitz

from app.models import BBox, Document, Page, TextBlock
from app.services.audit_summary import build_audit_items
from app.services.cover.facade import CoverMetadataComparator


def _block(
    block_id: str,
    text: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    block_type: str = "text",
    *,
    source: str = "",
    layout_match_status: str = "not_applicable",
    layout_block_id: str = "",
) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=1,
        text=text,
        bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        block_type=block_type,
        source=source,
        layout_match_status=layout_match_status,
        layout_block_id=layout_block_id,
    )


def _document(blocks: list[TextBlock]) -> Document:
    return Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[Page(page_no=1, width=597, height=819, blocks=blocks)],
    )


def _document_at_path(blocks: list[TextBlock], path: Path) -> Document:
    return Document(
        filename=path.name,
        path=str(path),
        page_count=1,
        pages=[Page(page_no=1, width=597, height=819, blocks=blocks)],
    )


def _multi_page_document(pages: list[list[TextBlock]]) -> Document:
    built_pages = [
        Page(page_no=index, width=597, height=819, blocks=[block.model_copy(update={"page_no": index}) for block in blocks])
        for index, blocks in enumerate(pages, start=1)
    ]
    return Document(filename="sample.pdf", path="sample.pdf", page_count=len(built_pages), pages=built_pages)


def test_cover_extra_keeps_unmatched_header_and_single_char_apart_from_title() -> None:
    original = _document(
        [
            _block("o_title", "国能日新科技股份有限公司", 217, 70, 385, 84, "doc_title", layout_block_id="title"),
            _block("o_project", "供货合同", 270, 99, 330, 118, "paragraph_title"),
            _block("o_place", "签订地点：青海西宁", 380, 149, 501, 161),
        ]
    )
    compare = _document(
        [
            _block(
                "c_header",
                "GNNYN-20260521-0013",
                317,
                17,
                526,
                64,
                "header",
                source="ppocrv5_unmatched",
                layout_match_status="meaningful_unmatched",
            ),
            _block(
                "c_yong",
                "永",
                436,
                50,
                476,
                82,
                "ocr_line",
                source="ppocrv5_unmatched",
                layout_match_status="meaningful_unmatched",
            ),
            _block("c_title", "国能日新科技股份有限公司", 224, 69, 392, 83, "doc_title", layout_block_id="title"),
            _block("c_project", "供货合同", 278, 98, 337, 116, "paragraph_title"),
            _block("c_place", "签订地点：青海西宁", 387, 145, 508, 158),
        ]
    )

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    added = [diff.compare_text for diff in diffs if diff.title == "封面额外文本" and diff.diff_type == "ADD"]
    deleted = [diff.original_text for diff in diffs if diff.title == "封面额外文本" and diff.diff_type == "DELETE"]
    assert "GNNYN-20260521-0013" in added
    assert "永" in added
    assert "国能日新科技股份有限公司永" not in added
    assert "国能日新科技股份有限公司" not in deleted


def test_cover_title_excludes_contract_number_role_labels() -> None:
    original = _document(
        [
            _block("o_title", "技术服务合同", 185, 213, 408, 241, "doc_title"),
            _block("o_buyer_no", "合同编号（甲方）：", 122, 440, 247, 458),
            _block("o_seller_no", "合同编号（乙方）：", 121, 475, 248, 494),
            _block("o_project", "项目名称：新能源预测分析业务支撑", 122, 513, 504, 529),
        ]
    )
    compare = _document(
        [
            _block("c_title", "技术服务合同", 190, 206, 415, 237, "doc_title"),
            _block("c_buyer_no", "合同编号（甲方）：", 126, 431, 255, 451),
            _block("c_seller_no", "合同编号（乙方）：GNXNYN-20140604-000024", 123, 463, 464, 497),
            _block("c_project", "项目名称：新能源预测分析业务支撑", 125, 511, 506, 528),
        ]
    )

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    assert all(diff.title != "封面字段：项目名称" for diff in diffs)
    contract_no_diffs = [diff for diff in diffs if diff.title == "封面字段：合同编号（乙方）"]
    assert len(contract_no_diffs) == 1
    assert "GNXNYN-20140604-000024" in contract_no_diffs[0].compare_text


def test_cover_title_excludes_prefixed_buyer_and_seller_contract_number_labels() -> None:
    original = _document(
        [
            _block("o_buyer_no", "甲方合同编号：", 100, 78, 180, 92),
            _block("o_seller_no", "乙方合同编号：", 100, 122, 180, 138),
            _block("o_title_1", "华能陕西新能源分公司复辉光伏电站功率", 100, 260, 498, 286, "paragraph_title"),
            _block("o_title_2", "预测系统技术服务项目合同", 164, 292, 431, 316, "paragraph_title"),
        ]
    )
    compare = _document(
        [
            _block("c_buyer_no", "甲方合同编号：XNY-RN/SC-FW-2026-00S", 96, 68, 359, 101),
            _block("c_seller_no", "乙方合同编号：", 100, 119, 180, 135),
            _block("c_title_1", "华能陕西新能源分公司复辉光伏电站功率", 98, 264, 498, 288, "paragraph_title"),
            _block("c_title_2", "预测系统技术服务项目合同", 164, 292, 431, 316, "paragraph_title"),
        ]
    )

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    buyer_no_diffs = [diff for diff in diffs if diff.title == "封面字段：合同编号（甲方）"]
    assert len(buyer_no_diffs) == 1
    assert buyer_no_diffs[0].diff_type == "ADD"
    assert buyer_no_diffs[0].compare_text == "XNY-RN/SC-FW-2026-00S"
    assert all(diff.title != "封面字段：项目名称" for diff in diffs)
    assert all(
        not (diff.title == "封面额外文本" and "华能陕西新能源分公司复辉光伏电站功率" in diff.compare_text)
        for diff in diffs
    )


def test_cover_title_missing_from_ocr_is_not_reported_when_present_in_opposite_body_text() -> None:
    original = _multi_page_document(
        [
            [
                _block("o_buyer_no", "甲方合同编号：", 100, 78, 180, 92),
                _block("o_seller_no", "乙方合同编号：", 100, 122, 180, 138),
            ],
            [
                _block(
                    "o_body_title",
                    "1.1 项目名称：华能陕西新能源分公司复辉光伏电站功率预测系统技术服务项目合同。",
                    100,
                    150,
                    520,
                    175,
                )
            ],
        ]
    )
    compare = _multi_page_document(
        [
            [
                _block("c_buyer_no", "甲方合同编号：XNY-RN/SC-FW-2026-00S", 96, 68, 359, 101),
                _block("c_seller_no", "乙方合同编号：", 100, 119, 180, 135),
                _block("c_title_1", "华能陕西新能源分公司复辉光伏电站功率", 98, 264, 498, 288, "paragraph_title"),
                _block("c_title_2", "预测系统技术服务项目合同", 164, 292, 431, 316, "paragraph_title"),
            ],
            [
                _block(
                    "c_body_title",
                    "1.1 项目名称：华能陕西新能源分公司复辉光伏电站功率预测系统技术服务项目合同。",
                    100,
                    150,
                    520,
                    175,
                )
            ],
        ]
    )

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    assert [diff.title for diff in diffs] == ["封面字段：合同编号（甲方）"]


def test_cover_title_missing_from_ocr_is_not_reported_when_present_in_opposite_native_pdf_text(tmp_path: Path) -> None:
    native_pdf = tmp_path / "original.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=597, height=819)
    page.insert_text((130, 280), "华能陕西新能源分公司复辉光伏电站功率", fontsize=14, fontname="china-s")
    page.insert_text((180, 310), "预测系统技术服务项目合同", fontsize=14, fontname="china-s")
    pdf.save(native_pdf)
    pdf.close()

    original = _document_at_path(
        [
            _block("o_buyer_no", "甲方合同编号：", 100, 78, 180, 92),
            _block("o_seller_no", "乙方合同编号：", 100, 122, 180, 138),
        ],
        native_pdf,
    )
    compare = _document(
        [
            _block("c_buyer_no", "甲方合同编号：XNY-RN/SC-FW-2026-00S", 96, 68, 359, 101),
            _block("c_seller_no", "乙方合同编号：", 100, 119, 180, 135),
            _block("c_title_1", "华能陕西新能源分公司复辉光伏电站功率", 98, 264, 498, 288, "paragraph_title"),
            _block("c_title_2", "预测系统技术服务项目合同", 164, 292, 431, 316, "paragraph_title"),
        ]
    )

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    assert [diff.title for diff in diffs] == ["封面字段：合同编号（甲方）"]


def test_reports_changed_preamble_title_on_second_page_before_first_clause() -> None:
    original = _multi_page_document(
        [
            [_block("o_cover_title", "新能源场站功率预测系统授权服务合同", 120, 220, 480, 250, "doc_title")],
            [
                _block("o_title_1", "国能长源随州发电有限公司随县分公司", 130, 80, 470, 102, "doc_title"),
                _block("o_title_2", "2026年新能源场站功率预测系统授权服务合同", 115, 112, 485, 136, "doc_title"),
                _block(
                    "o_preamble",
                    "鉴于甲方拟委托乙方提供新能源场站功率预测系统授权服务项目，且乙方同意接受",
                    72,
                    190,
                    520,
                    212,
                ),
                _block("o_clause", "1. 定义", 72, 240, 145, 260, "paragraph_title"),
            ],
        ]
    )
    compare = _multi_page_document(
        [
            [_block("c_cover_title", "新能源场站功率预测系统授权服务合同", 120, 220, 480, 250, "doc_title")],
            [
                _block("c_title_1", "长源电力随州公司2026年新能源场站", 138, 82, 375, 103, "doc_title"),
                _block("c_title_2", "功率预测系统授权服务单一来源项目合同", 127, 115, 386, 136, "doc_title"),
                _block(
                    "c_preamble",
                    "鉴于甲方拟委托乙方提供新能源场站功率预测系统授权服务项目，且乙方同意接受",
                    72,
                    190,
                    520,
                    212,
                ),
                _block("c_clause", "1. 定义", 72, 240, 145, 260, "paragraph_title"),
            ],
        ]
    )

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    preamble_diffs = [diff for diff in diffs if diff.title == "前置标题（第2页）"]
    assert len(preamble_diffs) == 1
    diff = preamble_diffs[0]
    assert diff.diff_type == "MODIFY"
    assert diff.original_text == (
        "国能长源随州发电有限公司随县分公司\n2026年新能源场站功率预测系统授权服务合同"
    )
    assert diff.compare_text == (
        "长源电力随州公司2026年新能源场站\n功率预测系统授权服务单一来源项目合同"
    )
    assert diff.original_snippet == diff.original_text
    assert diff.compare_snippet == diff.compare_text
    assert [
        (text_range.start, text_range.end, text_range.highlight_type)
        for text_range in diff.original_change_ranges
    ] == [(0, len(diff.original_text), "MODIFY")]
    assert [
        (text_range.start, text_range.end, text_range.highlight_type)
        for text_range in diff.compare_change_ranges
    ] == [(0, len(diff.compare_text), "MODIFY")]
    assert [evidence.text for evidence in diff.original_evidence] == [
        "国能长源随州发电有限公司随县分公司",
        "2026年新能源场站功率预测系统授权服务合同",
    ]
    assert [evidence.text for evidence in diff.compare_evidence] == [
        "长源电力随州公司2026年新能源场站",
        "功率预测系统授权服务单一来源项目合同",
    ]
    assert {evidence.page_no for evidence in diff.original_evidence} == {2}
    assert {evidence.page_no for evidence in diff.compare_evidence} == {2}
    assert {evidence.highlight_type for evidence in diff.original_evidence} == {"MODIFY"}
    assert {evidence.highlight_type for evidence in diff.compare_evidence} == {"MODIFY"}

    audit_items = build_audit_items([diff])
    assert [item.item_id for item in audit_items] == [f"{diff.diff_id}:MODIFY"]
    assert audit_items[0].original_evidence == diff.original_evidence
    assert audit_items[0].compare_evidence == diff.compare_evidence


def test_cover_extra_pairs_blank_year_month_placeholder_with_filled_cover_date() -> None:
    original = _document(
        [
            _block("o_buyer_no", "甲方合同编号：", 100, 78, 180, 92),
            _block("o_title", "技术服务项目合同", 164, 292, 431, 316, "paragraph_title"),
            _block("o_date", "年月", 265, 695, 328, 715),
        ]
    )
    compare = _document(
        [
            _block("c_buyer_no", "甲方合同编号：XNY-RN/SC-FW-2026-00S", 96, 68, 359, 101),
            _block("c_title", "技术服务项目合同", 164, 292, 431, 316, "paragraph_title"),
            _block("c_date", "2026年05月", 236, 678, 329, 704),
        ]
    )

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    extra_deletes = [diff.original_text for diff in diffs if diff.title == "封面额外文本" and diff.diff_type == "DELETE"]
    extra_adds = [diff.compare_text for diff in diffs if diff.title == "封面额外文本" and diff.diff_type == "ADD"]
    assert "年月" not in extra_deletes
    assert "2026年05月" in extra_adds


def test_cover_contract_number_role_label_is_compared_separately_from_common_contract_no() -> None:
    original = _document(
        [
            _block("o_common_no", "合同编号：SGNC0000DKJS2400153", 358, 71, 505, 81, "header"),
            _block("o_title", "技术服务合同", 185, 213, 408, 241, "doc_title"),
            _block("o_buyer_no", "合同编号（甲方）：", 122, 440, 247, 458),
            _block("o_seller_no", "合同编号（乙方）：", 121, 475, 248, 494),
        ]
    )
    compare = _document(
        [
            _block("c_common_no", "合同编号：SGNC0000DKJS2400153", 358, 71, 505, 81, "header"),
            _block("c_title", "技术服务合同", 190, 206, 415, 237, "doc_title"),
            _block("c_buyer_no", "合同编号（甲方）：", 126, 431, 255, 451),
            _block("c_seller_no", "合同编号（乙方）：GNXNYN-20140604-000024", 123, 463, 464, 497),
        ]
    )

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    seller_no_diffs = [diff for diff in diffs if diff.title == "封面字段：合同编号（乙方）"]
    assert len(seller_no_diffs) == 1
    assert seller_no_diffs[0].diff_type == "ADD"
    assert seller_no_diffs[0].compare_text == "GNXNYN-20140604-000024"
    assert all(diff.compare_text != "SGNC0000DKJS2400153" for diff in seller_no_diffs)


def test_empty_sign_date_does_not_consume_validity_period() -> None:
    original = _document(
        [
            _block("o_title", "技术服务合同", 185, 213, 408, 241, "doc_title"),
            _block("o_date", "签订时间：", 120, 655, 193, 675),
            _block("o_place", "签订地点：北京", 121, 691, 234, 709),
            _block("o_validity", "有效期限：2024年7月1日-2025年6月30日", 121, 728, 449, 745),
        ]
    )
    compare = _document(
        [
            _block("c_title", "技术服务合同", 190, 206, 415, 237, "doc_title"),
            _block("c_date", "签订时间：2024.6.3", 121, 637, 286, 677),
            _block("c_place", "签订地点：北京", 125, 686, 240, 704),
            _block("c_validity", "有效期限：2024年7月1日-2025年6月30日", 125, 713, 457, 734),
        ]
    )

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    sign_date_diffs = [diff for diff in diffs if diff.title == "封面字段：签订日期"]
    assert len(sign_date_diffs) == 1
    assert sign_date_diffs[0].diff_type == "ADD"
    assert sign_date_diffs[0].compare_text == "2024.6.3"
    assert "有效期限" not in sign_date_diffs[0].original_text
    assert all(
        not (diff.title == "封面额外文本" and "有效期限" in (diff.original_text or diff.compare_text))
        for diff in diffs
    )


def test_empty_sign_date_does_not_consume_page_number() -> None:
    original = _document(
        [
            _block("o_title", "技术服务合同", 185, 213, 408, 241, "doc_title"),
            _block("o_date", "签订时间：", 120, 655, 193, 675),
            _block("o_page", "1", 294, 783, 301, 791, "number"),
        ]
    )
    compare = _document(
        [
            _block("c_title", "技术服务合同", 190, 206, 415, 237, "doc_title"),
            _block("c_date", "签订时间：2024.6.3", 121, 637, 286, 677),
        ]
    )

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    sign_date_diffs = [diff for diff in diffs if diff.title == "封面字段：签订日期"]
    assert len(sign_date_diffs) == 1
    assert sign_date_diffs[0].diff_type == "ADD"
    assert sign_date_diffs[0].original_text == ""
    assert sign_date_diffs[0].compare_text == "2024.6.3"


def test_cover_extra_ignores_image_html_blocks() -> None:
    original = _document(
        [
            _block("o_title", "技术服务合同", 185, 213, 408, 241, "doc_title"),
        ]
    )
    compare = _document(
        [
            _block("c_title", "技术服务合同", 190, 206, 415, 237, "doc_title"),
            _block(
                "c_image",
                '<div style="text-align: center;"><img src="imgs/logo.jpg" alt="Image" width="8%" /></div>',
                33,
                9,
                87,
                63,
                "image",
            ),
        ]
    )

    diffs = CoverMetadataComparator().build_diffs(original, compare)

    assert all("<img" not in diff.compare_text for diff in diffs)
