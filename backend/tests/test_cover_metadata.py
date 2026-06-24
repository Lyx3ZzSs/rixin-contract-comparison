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
