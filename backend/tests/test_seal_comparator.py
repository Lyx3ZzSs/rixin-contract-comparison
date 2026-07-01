from __future__ import annotations

from app.models import BBox, Document, Page, TextBlock
from app.services.seal_comparator import build_seal_diffs


def _block(
    block_id: str,
    text: str,
    bbox: BBox,
    *,
    layout_bbox: BBox | None = None,
    page_no: int = 1,
) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=page_no,
        text=text,
        bbox=bbox,
        layout_bbox=layout_bbox,
        block_type="seal",
    )


def _document(blocks: list[TextBlock]) -> Document:
    return Document(
        filename="test.pdf",
        path="test.pdf",
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=blocks)],
    )


def test_seal_diffs_merge_fragments_with_same_layout_bbox() -> None:
    seal_bbox = BBox(x0=208, y0=121, x1=322, y1=232)
    compare = _document([
        _block("c1", "京", BBox(x0=211, y0=153, x1=239, y1=175), layout_bbox=seal_bbox),
        _block("c2", "限公司", BBox(x0=283, y0=156, x1=322, y1=210), layout_bbox=seal_bbox),
        _block("c3", "合同专用章", BBox(x0=216, y0=178, x1=299, y1=225), layout_bbox=seal_bbox),
    ])

    diffs = build_seal_diffs(_document([]), compare)

    assert len(diffs) == 1
    diff = diffs[0]
    assert diff.diff_type == "ADD"
    assert diff.compare_evidence[0].bbox == seal_bbox
    assert diff.compare_evidence[0].method == "seal_region"
    assert diff.compare_text == "京 限公司 合同专用章"


def test_seal_diffs_keep_distinct_layout_regions_separate() -> None:
    first_bbox = BBox(x0=100, y0=100, x1=180, y1=180)
    second_bbox = BBox(x0=300, y0=300, x1=380, y1=380)
    compare = _document([
        _block("c1", "合同专用章", BBox(x0=110, y0=120, x1=170, y1=150), layout_bbox=first_bbox),
        _block("c2", "财务专用章", BBox(x0=310, y0=320, x1=370, y1=350), layout_bbox=second_bbox),
    ])

    diffs = build_seal_diffs(_document([]), compare)

    assert len(diffs) == 2
    assert [diff.compare_evidence[0].bbox for diff in diffs] == [first_bbox, second_bbox]


def test_seal_diffs_fall_back_to_block_bbox_without_layout_bbox() -> None:
    block_bbox = BBox(x0=350, y0=600, x1=430, y1=680)
    compare = _document([_block("c1", "合同专用章", block_bbox)])

    diffs = build_seal_diffs(_document([]), compare)

    assert len(diffs) == 1
    assert diffs[0].compare_evidence[0].bbox == block_bbox


def test_seal_diffs_ignore_text_or_html_changes_inside_matched_regions() -> None:
    seal_bbox = BBox(x0=208, y0=121, x1=322, y1=232)
    original = _document([_block("o1", "司", BBox(x0=211, y0=153, x1=239, y1=175), layout_bbox=seal_bbox)])
    compare = _document([
        _block(
            "c1",
            '<div style="text-align: center;"><img src="imgs/img_in_seal_box.jpg" alt="Image" /></div>',
            BBox(x0=216, y0=178, x1=299, y1=225),
            layout_bbox=seal_bbox,
        )
    ])

    diffs = build_seal_diffs(original, compare)

    assert diffs == []
