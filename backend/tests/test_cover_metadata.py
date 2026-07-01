from __future__ import annotations

from app.models import BBox, Document, Page, TextBlock
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
