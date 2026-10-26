"""Generic reading-order contracts retained after layout client replacement."""
from app.models import BBox, Page, TextBlock
from app.services.reading_order import assign_page_reading_order

def _block(block_id, x0, y0, x1, y1, block_type="text", text="text", flow_role=""):
    return TextBlock(block_id=block_id, page_no=1, text=text,
                     bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
                     block_type=block_type, flow_role=flow_role)


def test_reading_order_handles_spanning_title_and_two_columns() -> None:
    page = Page(
        page_no=1,
        width=600,
        height=800,
        blocks=[
            _block("right-2", 340, 200, 560, 230),
            _block("left-2", 40, 200, 260, 230),
            _block("title", 40, 20, 560, 60),
            _block("right-1", 340, 100, 560, 130),
            _block("left-1", 40, 100, 260, 130),
        ],
    )

    assign_page_reading_order(page)

    assert [block.block_id for block in sorted(page.blocks, key=lambda block: block.reading_order or 0)] == [
        "title",
        "left-1",
        "left-2",
        "right-1",
        "right-2",
    ]


def test_v3_reading_order_groups_margin_columns_and_aside() -> None:
    page = Page(
        page_no=1,
        width=600,
        height=800,
        blocks=[
            _block("footer", 200, 760, 400, 780, flow_role="margin"),
            _block("aside", 5, 180, 35, 220, flow_role="aside"),
            _block("right", 340, 100, 560, 130, flow_role="body"),
            _block("left-2", 40, 200, 260, 230, flow_role="body"),
            _block("left-1", 40, 100, 260, 130, flow_role="body"),
            _block("header", 200, 10, 400, 30, flow_role="margin"),
        ],
    )

    assign_page_reading_order(page)

    assert [block.block_id for block in sorted(page.blocks, key=lambda block: block.reading_order or 0)] == [
        "header",
        "left-1",
        "left-2",
        "aside",
        "right",
        "footer",
    ]


def test_v3_reading_order_keeps_figure_with_caption() -> None:
    page = Page(
        page_no=1,
        width=600,
        height=800,
        blocks=[
            _block("body", 40, 500, 560, 540, flow_role="body"),
            _block("figure", 100, 200, 500, 400, flow_role="non_text"),
            _block("caption", 100, 170, 500, 190, flow_role="caption"),
        ],
    )

    assign_page_reading_order(page)

    assert [block.block_id for block in sorted(page.blocks, key=lambda block: block.reading_order or 0)] == [
        "caption",
        "figure",
        "body",
    ]
