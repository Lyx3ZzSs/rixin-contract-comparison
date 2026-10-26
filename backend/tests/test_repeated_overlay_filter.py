import pytest

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


def test_filters_only_dominant_position_cluster_and_preserves_spatial_outlier() -> None:
    document = _document("黄科")
    for page in document.pages:
        for block in page.blocks:
            if block.block_id.startswith("overlay-"):
                block.bbox = BBox(x0=500, y0=780, x1=540, y1=800)
    last_page = document.pages[-1]
    last_page.blocks.append(
        TextBlock(
            block_id="overlay-10",
            page_no=10,
            text="黄科",
            bbox=BBox(x0=500, y0=780, x1=540, y1=800),
            source="ppocrv5",
        )
    )
    outlier = TextBlock(
        block_id="contact-value",
        page_no=10,
        text="黄科",
        bbox=BBox(x0=90, y0=400, x1=130, y1=420),
        source="ppocrv5",
        block_role="cover_metadata",
    )
    last_page.blocks.append(outlier)

    result = RepeatedOverlayFilter().apply(document)

    clustered = [
        block
        for page in document.pages
        for block in page.blocks
        if block.block_id.startswith("overlay-")
    ]
    assert len(clustered) == 10
    assert all(block.enter_clause_compare is False for block in clustered)
    assert all(block.flow_role == "noise" for block in clustered)
    assert outlier.enter_clause_compare is None
    assert outlier.flow_role == ""
    assert outlier.source == "ppocrv5"
    assert outlier.semantic_reasons == []
    assert result.filtered_block_count == 10
    assert result.decisions == [
        {
            "action": "filtered",
            "text": "黄科",
            "page_ratio": 1.0,
            "cluster_ratio": 0.9091,
            "filtered_block_count": 10,
            "block_ids": [f"overlay-{page_no}" for page_no in range(1, 11)],
            "preserved_outlier_count": 1,
        }
    ]


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


@pytest.mark.parametrize(
    ("metadata_field", "metadata_value"),
    [
        ("block_type", "paragraph_title"),
        ("block_role", "safety_section"),
        ("semantic_role", "safety_agreement"),
        ("flow_role", "heading"),
        ("block_role", "cover_metadata"),
        ("block_role", "table_note"),
        ("block_role", "table_caption"),
    ],
)
def test_keeps_stable_repeated_meaningful_metadata(
    metadata_field: str, metadata_value: str
) -> None:
    document = _document("条款标题")
    for page in document.pages:
        for block in page.blocks:
            if block.block_id.startswith("overlay-"):
                setattr(block, metadata_field, metadata_value)

    result = RepeatedOverlayFilter().apply(document)

    overlays = [block for page in document.pages for block in page.blocks if block.block_id.startswith("overlay-")]
    assert result.filtered_block_count == 0
    assert all(block.enter_clause_compare is None for block in overlays)


def test_keeps_repeated_generic_field_value_text() -> None:
    document = _document("字段：示例值")

    result = RepeatedOverlayFilter().apply(document)

    overlays = [block for page in document.pages for block in page.blocks if block.block_id.startswith("overlay-")]
    assert result.filtered_block_count == 0
    assert all(block.enter_clause_compare is None for block in overlays)


def test_keeps_group_with_two_occurrences_on_a_page() -> None:
    document = _document("黄科")
    for page in document.pages[:9]:
        original = next(block for block in page.blocks if block.block_id.startswith("overlay-"))
        page.blocks.append(
            original.model_copy(update={"block_id": f"{original.block_id}-duplicate"})
        )

    result = RepeatedOverlayFilter().apply(document)

    overlays = [block for page in document.pages for block in page.blocks if block.block_id.startswith("overlay-")]
    assert result.filtered_block_count == 0
    assert all(block.enter_clause_compare is None for block in overlays)


def test_keeps_repeated_generic_standalone_label() -> None:
    document = _document("标签：")

    result = RepeatedOverlayFilter().apply(document)

    overlays = [block for page in document.pages for block in page.blocks if block.block_id.startswith("overlay-")]
    assert result.filtered_block_count == 0
    assert all(block.enter_clause_compare is None for block in overlays)


def test_main_clause_role_alone_does_not_protect_plain_overlay() -> None:
    document = _document("黄科")
    for page in document.pages:
        for block in page.blocks:
            if block.block_id.startswith("overlay-"):
                block.block_role = "main_clause"

    result = RepeatedOverlayFilter().apply(document)

    overlays = [block for page in document.pages for block in page.blocks if block.block_id.startswith("overlay-")]
    assert result.filtered_block_count == 9
    assert all(block.enter_clause_compare is False for block in overlays)
