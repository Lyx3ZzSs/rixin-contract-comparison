from __future__ import annotations

from app.models import BBox, Document, Page, TextBlock
from app.services.header_footer_compare import HeaderFooterComparator


def _block(
    block_id: str,
    text: str,
    *,
    y0: float,
    y1: float,
    block_type: str = "text",
    page_no: int = 1,
) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=page_no,
        text=text,
        bbox=BBox(x0=50, y0=y0, x1=500, y1=y1),
        block_type=block_type,
    )


def _document(blocks_by_page: list[list[TextBlock]]) -> Document:
    pages = [
        Page(page_no=index + 1, width=595, height=842, blocks=blocks)
        for index, blocks in enumerate(blocks_by_page)
    ]
    return Document(filename="test.pdf", path="test.pdf", page_count=len(pages), pages=pages)


def test_header_footer_compares_labeled_ocr_header_as_modify() -> None:
    original = _document([[_block("o1", "HTXTJC260065", y0=20, y1=36, block_type="header")]])
    compare = _document([[_block("c1", "GNXNYN-20260421-00044", y0=20, y1=36, block_type="header")]])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].diff_id == "D001"
    assert diffs[0].diff_type == "MODIFY"
    assert diffs[0].source_type == "header_footer"
    assert diffs[0].title == "页眉"
    assert diffs[0].original_evidence[0].method == "header_footer"
    assert diffs[0].original_evidence[0].highlight_type == "MODIFY"
    assert diffs[0].compare_evidence[0].highlight_type == "MODIFY"


def test_header_footer_detects_pymupdf_margin_text_blocks() -> None:
    original = _document([[_block("o1", "合同编号：A-001", y0=22, y1=38)]])
    compare = _document([[_block("c1", "合同编号：B-002", y0=22, y1=38)]])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].diff_type == "MODIFY"
    assert diffs[0].title == "页眉"
    assert diffs[0].original_text == "合同编号：A-001"
    assert diffs[0].compare_text == "合同编号：B-002"


def test_header_footer_deduplicates_repeated_headers() -> None:
    original = _document([
        [_block(f"o{page_no}", "CONFIDENTIAL-A", y0=20, y1=36, page_no=page_no)]
        for page_no in range(1, 5)
    ])
    compare = _document([
        [_block(f"c{page_no}", "CONFIDENTIAL-B", y0=20, y1=36, page_no=page_no)]
        for page_no in range(1, 5)
    ])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].diff_type == "MODIFY"
    assert [evidence.page_no for evidence in diffs[0].original_evidence] == [1, 2, 3, 4]
    assert [evidence.page_no for evidence in diffs[0].compare_evidence] == [1, 2, 3, 4]


def test_header_footer_does_not_treat_top_clause_as_header() -> None:
    original = _document([[_block("o1", "第一条 付款方式", y0=20, y1=38)]])
    compare = _document([[_block("c1", "第一条 付款方式变更", y0=20, y1=38)]])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_summarizes_page_numbers_without_per_page_noise() -> None:
    original = _document([
        [_block("o1", "第 1 页", y0=810, y1=826, page_no=1)],
        [_block("o2", "第 2 页", y0=810, y1=826, page_no=2)],
    ])
    compare = _document([
        [_block("c1", "第 1 页", y0=810, y1=826, page_no=1)],
        [_block("c2", "第 2 页", y0=810, y1=826, page_no=2)],
    ])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_matches_page_numbers_slightly_above_footer_margin() -> None:
    original = _document([
        [_block(f"o{page_no}", f"共9页第{page_no}页", y0=780, y1=792, block_type="number", page_no=page_no)]
        for page_no in range(1, 10)
    ])
    compare = _document([
        [
            _block(
                f"c{page_no}",
                f"共9页第{page_no}页",
                y0=770,
                y1=784,
                block_type="vision_footnote" if page_no == 3 else "number",
                page_no=page_no,
            )
        ]
        for page_no in range(1, 10)
    ])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_does_not_treat_mid_page_page_number_text_as_footer() -> None:
    original = _document([[_block("o1", "第 1 页", y0=360, y1=374)]])
    compare = _document([[_block("c1", "第 2 页", y0=360, y1=374)]])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_reports_page_number_total_change_once() -> None:
    original = _document([
        [_block(f"o{page_no}", f"共 14 页第 {page_no} 页", y0=810, y1=826, page_no=page_no)]
        for page_no in range(1, 15)
    ])
    compare = _document([
        [_block(f"c{page_no}", f"共 15 页第 {page_no} 页", y0=810, y1=826, page_no=page_no)]
        for page_no in range(1, 16)
    ])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].diff_type == "MODIFY"
    assert diffs[0].title == "页脚页码"
    assert "共 14 页第 N 页" in diffs[0].original_text
    assert "共 15 页第 N 页" in diffs[0].compare_text
    assert [evidence.text for evidence in diffs[0].original_evidence] == [
        f"共 14 页第 {page_no} 页" for page_no in range(1, 15)
    ]
    assert [evidence.text for evidence in diffs[0].compare_evidence] == [
        f"共 15 页第 {page_no} 页" for page_no in range(1, 16)
    ]
