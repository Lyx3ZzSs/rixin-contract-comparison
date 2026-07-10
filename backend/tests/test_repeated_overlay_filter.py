from app.models import BBox, Document, Page, TextBlock
from app.services.repeated_overlay_filter import RepeatedOverlayFilter


def _document(repeated_text: str, *, stable: bool = True) -> Document:
    pages: list[Page] = []
    for page_no in range(1, 11):
        x0 = 500 if stable else 20 + page_no * 35
        blocks = [
            TextBlock(
                block_id=f"body-{page_no}",
                page_no=page_no,
                text=f"第{page_no}页合同正文",
                bbox=BBox(x0=60, y0=100, x1=500, y1=130),
            )
        ]
        if page_no <= 9:
            blocks.append(
                TextBlock(
                    block_id=f"overlay-{page_no}",
                    page_no=page_no,
                    text=repeated_text,
                    bbox=BBox(x0=x0, y0=400, x1=x0 + 40, y1=420),
                    source="ppocrv5",
                )
            )
        pages.append(Page(page_no=page_no, width=595, height=842, blocks=blocks))
    return Document(filename="scan.pdf", path="scan.pdf", page_count=10, pages=pages)


def test_marks_stable_repeated_short_overlay_as_noise() -> None:
    document = _document("黄科")

    result = RepeatedOverlayFilter().apply(document)

    overlays = [block for page in document.pages for block in page.blocks if block.block_id.startswith("overlay-")]
    assert all(block.enter_clause_compare is False for block in overlays)
    assert all(block.flow_role == "noise" for block in overlays)
    assert all("repeated_overlay_filter" in block.source for block in overlays)
    assert result.filtered_block_count == 9


def test_keeps_unstable_or_protected_repeated_text() -> None:
    unstable = _document("黄科", stable=False)
    party_labels = _document("甲方")
    headings = _document("8. 保密")

    assert RepeatedOverlayFilter().apply(unstable).filtered_block_count == 0
    assert RepeatedOverlayFilter().apply(party_labels).filtered_block_count == 0
    assert RepeatedOverlayFilter().apply(headings).filtered_block_count == 0


def test_keeps_single_page_contact_name() -> None:
    document = _document("黄科")
    for page in document.pages[1:]:
        page.blocks = [block for block in page.blocks if not block.block_id.startswith("overlay-")]

    result = RepeatedOverlayFilter().apply(document)

    assert result.filtered_block_count == 0
    assert document.pages[0].blocks[-1].enter_clause_compare is None
