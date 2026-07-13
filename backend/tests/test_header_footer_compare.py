from __future__ import annotations

from app.models import BBox, Document, Page, TextBlock
from app.services.header_footer_compare import HeaderFooterComparator


def _block(
    block_id: str,
    text: str,
    *,
    y0: float,
    y1: float,
    x0: float = 50,
    x1: float = 500,
    block_type: str = "text",
    page_no: int = 1,
    source: str = "",
    layout_match_status: str = "not_applicable",
    layout_match_score: float | None = None,
) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=page_no,
        text=text,
        bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
        block_type=block_type,
        source=source,
        layout_match_status=layout_match_status,
        layout_match_score=layout_match_score,
    )


def _document(blocks_by_page: list[list[TextBlock]]) -> Document:
    pages = [
        Page(page_no=index + 1, width=595, height=842, blocks=blocks) for index, blocks in enumerate(blocks_by_page)
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


def test_header_footer_detects_repeated_pymupdf_margin_text_blocks() -> None:
    original = _document(
        [
            [_block("o1", "CONFIDENTIAL-A", y0=22, y1=38, page_no=1)],
            [_block("o2", "CONFIDENTIAL-A", y0=22, y1=38, page_no=2)],
        ]
    )
    compare = _document(
        [
            [_block("c1", "CONFIDENTIAL-B", y0=22, y1=38, page_no=1)],
            [_block("c2", "CONFIDENTIAL-B", y0=22, y1=38, page_no=2)],
        ]
    )

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].diff_type == "MODIFY"
    assert diffs[0].title == "页眉"
    assert diffs[0].original_text == "CONFIDENTIAL-A"
    assert diffs[0].compare_text == "CONFIDENTIAL-B"


def test_header_footer_does_not_treat_one_off_top_text_as_header() -> None:
    original = _document([[_block("o1", "合同编号：A-001", y0=22, y1=38)]])
    compare = _document([[_block("c1", "合同编号：B-002", y0=22, y1=38)]])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_deduplicates_repeated_headers() -> None:
    original = _document(
        [[_block(f"o{page_no}", "CONFIDENTIAL-A", y0=20, y1=36, page_no=page_no)] for page_no in range(1, 5)]
    )
    compare = _document(
        [[_block(f"c{page_no}", "CONFIDENTIAL-B", y0=20, y1=36, page_no=page_no)] for page_no in range(1, 5)]
    )

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].diff_type == "MODIFY"
    assert [evidence.page_no for evidence in diffs[0].original_evidence] == [1, 2, 3, 4]
    assert [evidence.page_no for evidence in diffs[0].compare_evidence] == [1, 2, 3, 4]


def test_header_footer_fuzzy_matches_one_page_ocr_variant_to_repeated_header() -> None:
    original = _document(
        [
            [
                _block(
                    "o1",
                    "南京国电南自电动化有限公司",
                    x0=354,
                    y0=98,
                    x1=521,
                    y1=112,
                    block_type="header",
                    page_no=1,
                )
            ],
            [
                _block(
                    "o2",
                    "南京国电南自电网自动化有限公司",
                    x0=354,
                    y0=98,
                    x1=521,
                    y1=112,
                    block_type="header",
                    page_no=2,
                )
            ],
        ]
    )
    compare = _document(
        [
            [
                _block(
                    "c1",
                    "南京国电南自电网自动化有限公司",
                    x0=360,
                    y0=96,
                    x1=527,
                    y1=110,
                    block_type="header",
                    page_no=1,
                )
            ],
            [
                _block(
                    "c2",
                    "南京国电南自电网自动化有限公司",
                    x0=360,
                    y0=96,
                    x1=527,
                    y1=110,
                    block_type="header",
                    page_no=2,
                )
            ],
        ]
    )

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].diff_type == "MODIFY"
    assert diffs[0].title == "页眉"
    assert diffs[0].original_text == "南京国电南自电动化有限公司"
    assert diffs[0].compare_text == "南京国电南自电网自动化有限公司"
    assert diffs[0].match_method == "fuzzy_position_header_footer"
    assert "FUZZY_HEADER_FOOTER_MATCH" in diffs[0].review_flags
    assert [evidence.page_no for evidence in diffs[0].original_evidence] == [1]
    assert [evidence.page_no for evidence in diffs[0].compare_evidence] == [1]


def test_header_footer_fuzzy_does_not_pair_short_noise_entries() -> None:
    original = _document(
        [
            [
                _block("o1", "南京国电南自电动化有限公司", y0=98, y1=112, block_type="header", page_no=1),
                _block("o-noise", "R", y0=20, y1=34, block_type="header", page_no=1),
            ],
            [_block("o2", "南京国电南自电网自动化有限公司", y0=98, y1=112, block_type="header", page_no=2)],
        ]
    )
    compare = _document(
        [
            [
                _block("c1", "南京国电南自电网自动化有限公司", y0=98, y1=112, block_type="header", page_no=1),
                _block("c-noise", "司", y0=20, y1=34, block_type="header", page_no=1),
            ],
            [_block("c2", "南京国电南自电网自动化有限公司", y0=98, y1=112, block_type="header", page_no=2)],
        ]
    )

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    fuzzy_diffs = [diff for diff in diffs if diff.match_method == "fuzzy_position_header_footer"]
    assert len(fuzzy_diffs) == 1
    assert fuzzy_diffs[0].original_text == "南京国电南自电动化有限公司"
    assert fuzzy_diffs[0].compare_text == "南京国电南自电网自动化有限公司"
    assert all(diff.diff_type != "MODIFY" for diff in diffs if diff.original_text == "R" or diff.compare_text == "司")


def test_header_footer_ignores_repeated_short_cjk_edge_noise() -> None:
    original = _document(
        [
            [_block("o1", "回回", x0=28, y0=12, x1=84, y1=35, block_type="header", page_no=1)],
            [_block("o2", "回回", x0=28, y0=12, x1=84, y1=35, block_type="header", page_no=2)],
            [_block("o3", "回回", x0=28, y0=12, x1=84, y1=35, block_type="header", page_no=3)],
        ]
    )
    compare = _document([[], [], []])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_ignores_short_ascii_and_page_number_edge_noise() -> None:
    original = _document(
        [
            [
                _block("o-logo", "AC", x0=66, y0=78, x1=139, y1=112, block_type="header", page_no=1),
                _block("o-page", "3", x0=462, y0=79, x1=488, y1=101, block_type="header", page_no=1),
            ]
        ]
    )
    compare = _document(
        [
            [
                _block("c-noise", "j", x0=23, y0=9, x1=41, y1=29, block_type="header", page_no=1),
                _block("c-page", "3", x0=462, y0=79, x1=488, y1=101, block_type="header", page_no=1),
            ]
        ]
    )

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_ignores_one_page_short_cjk_edge_noise() -> None:
    original = _document([[_block("o1", "理", x0=26, y0=24, x1=87, y1=102, block_type="header")]])
    compare = _document([[]])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_ignores_noise_unmatched_header_fragment() -> None:
    original = _document([[]])
    compare = _document(
        [
            [
                _block(
                    "c1",
                    "e1",
                    x0=544,
                    y0=11,
                    x1=559,
                    y1=21,
                    block_type="header",
                    source="ppocrv5_noise_unmatched",
                    layout_match_status="noise_unmatched",
                    layout_match_score=0.0,
                )
            ]
        ]
    )

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_accepts_lower_explicit_footer_despite_noise_status() -> None:
    original = _document([[]])
    compare = _document(
        [
            [
                _block(
                    "c1",
                    "李四",
                    x0=64,
                    y0=764,
                    x1=108,
                    y1=772,
                    block_type="footer",
                    source="ppocrv5_noise_unmatched",
                    layout_match_status="noise_unmatched",
                    layout_match_score=0.0,
                )
            ]
        ]
    )

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].diff_type == "ADD"
    assert diffs[0].title == "页脚"
    assert diffs[0].compare_text == "李四"
    assert [evidence.page_no for evidence in diffs[0].compare_evidence] == [1]


def test_header_footer_groups_repeated_lower_band_annotation_with_all_evidence() -> None:
    original = _document([[], [], []])
    compare = _document(
        [
            [
                _block(
                    f"c{page_no}",
                    "经办人：李四",
                    x0=440,
                    y0=764,
                    x1=532,
                    y1=772,
                    page_no=page_no,
                )
            ]
            for page_no in range(1, 4)
        ]
    )

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].diff_type == "ADD"
    assert diffs[0].title == "页脚"
    assert diffs[0].compare_text == "经办人：李四"
    assert [evidence.page_no for evidence in diffs[0].compare_evidence] == [1, 2, 3]


def test_header_footer_merges_single_page_ocr_variants_into_repeated_footer_annotation() -> None:
    original = _document([[], [], [], [], [], []])
    compare = _document(
        [
            [_block("c1", "黄科", x0=420, y0=780, x1=500, y1=822, block_type="footer", page_no=1)],
            [_block("c2", "黄科", x0=420, y0=780, x1=500, y1=822, page_no=2)],
            [_block("c3", "黄科", x0=420, y0=780, x1=500, y1=822, page_no=3)],
            [_block("c4", "黄科土科", x0=352, y0=786, x1=480, y1=821, page_no=4)],
            [_block("c5", "奇科", x0=400, y0=785, x1=468, y1=827, page_no=5)],
            [_block("c6", "李四", x0=400, y0=785, x1=468, y1=827, page_no=6)],
        ]
    )

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].diff_type == "ADD"
    assert diffs[0].compare_text == "黄科"
    assert [evidence.page_no for evidence in diffs[0].compare_evidence] == [1, 2, 3, 4, 5]
    assert [evidence.text for evidence in diffs[0].compare_evidence[-2:]] == ["黄科土科", "奇科"]


def test_header_footer_ignores_one_off_lower_body_text() -> None:
    original = _document([[]])
    compare = _document([[_block("c1", "本页备注", x0=440, y0=764, x1=532, y1=772)]])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_ignores_one_page_label_fragment_header() -> None:
    original = _document([[]])
    compare = _document([[_block("c1", "合同编号", x0=362, y0=53.5, x1=421, y1=61.5, block_type="header")]])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_ignores_top_unmatched_single_digit_as_page_number() -> None:
    original = _document([[]])
    compare = _document(
        [
            [
                _block(
                    "c1",
                    "3",
                    x0=563,
                    y0=42,
                    x1=570,
                    y1=48,
                    block_type="header",
                    source="ppocrv5_unmatched",
                    layout_match_status="meaningful_unmatched",
                    layout_match_score=0.0,
                )
            ]
        ]
    )

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_fuzzy_requires_close_position_for_supplemental_match() -> None:
    original = _document(
        [
            [_block("o1", "南京国电南自电动化有限公司", y0=98, y1=112, block_type="header", page_no=1)],
            [_block("o2", "南京国电南自电网自动化有限公司", y0=98, y1=112, block_type="header", page_no=2)],
        ]
    )
    compare = _document(
        [
            [_block("c1", "南京国电南自电网自动化有限公司", y0=300, y1=314, block_type="header", page_no=1)],
            [_block("c2", "南京国电南自电网自动化有限公司", y0=98, y1=112, block_type="header", page_no=2)],
        ]
    )

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert len(diffs) == 1
    assert diffs[0].diff_type == "DELETE"
    assert diffs[0].original_text == "南京国电南自电动化有限公司"


def test_header_footer_does_not_treat_top_clause_as_header() -> None:
    original = _document([[_block("o1", "第一条 付款方式", y0=20, y1=38)]])
    compare = _document([[_block("c1", "第一条 付款方式变更", y0=20, y1=38)]])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_does_not_trust_mid_page_footer_clause_label() -> None:
    original = _document(
        [[_block("o1", "一、产品名称、型号、数量、金额、供货时间：", y0=215, y1=228, block_type="footer")]]
    )
    compare = _document([[_block("c1", "单位：元（人民币）", y0=215, y1=228, block_type="footnote")]])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_summarizes_page_numbers_without_per_page_noise() -> None:
    original = _document(
        [
            [_block("o1", "第 1 页", y0=810, y1=826, page_no=1)],
            [_block("o2", "第 2 页", y0=810, y1=826, page_no=2)],
        ]
    )
    compare = _document(
        [
            [_block("c1", "第 1 页", y0=810, y1=826, page_no=1)],
            [_block("c2", "第 2 页", y0=810, y1=826, page_no=2)],
        ]
    )

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_matches_page_numbers_slightly_above_footer_margin() -> None:
    original = _document(
        [
            [_block(f"o{page_no}", f"共9页第{page_no}页", y0=780, y1=792, block_type="number", page_no=page_no)]
            for page_no in range(1, 10)
        ]
    )
    compare = _document(
        [
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
        ]
    )

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_does_not_treat_mid_page_page_number_text_as_footer() -> None:
    original = _document([[_block("o1", "第 1 页", y0=360, y1=374)]])
    compare = _document([[_block("c1", "第 2 页", y0=360, y1=374)]])

    diffs = HeaderFooterComparator().build_diffs(original, compare)

    assert diffs == []


def test_header_footer_reports_page_number_total_change_once() -> None:
    original = _document(
        [
            [_block(f"o{page_no}", f"共 14 页第 {page_no} 页", y0=810, y1=826, page_no=page_no)]
            for page_no in range(1, 15)
        ]
    )
    compare = _document(
        [
            [_block(f"c{page_no}", f"共 15 页第 {page_no} 页", y0=810, y1=826, page_no=page_no)]
            for page_no in range(1, 16)
        ]
    )

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
