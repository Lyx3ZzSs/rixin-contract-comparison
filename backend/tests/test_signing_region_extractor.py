from app.models import BBox, Document, Page, TextBlock
from app.services.signing_region.extractor import SigningRegionExtractor
from app.services.signing_region.models import (
    SigningElement,
    SigningElementType,
    SigningRegion,
    SigningRegionRole,
)


def test_signing_region_model_holds_elements_and_reasons() -> None:
    element = SigningElement(
        element_id="E1",
        element_type=SigningElementType.SEAL,
        page_no=1,
        bbox=BBox(x0=100, y0=650, x1=180, y1=730),
        text="合同专用章",
        confidence=0.9,
        source="layout",
    )
    region = SigningRegion(
        region_id="SR-1-1",
        page_no=1,
        bbox=BBox(x0=80, y0=630, x1=220, y1=760),
        region_role=SigningRegionRole.PARTY_A,
        confidence=0.92,
        confidence_reasons=["seal_block", "signing_label"],
        elements=[element],
    )

    assert region.elements[0].element_type == SigningElementType.SEAL
    assert region.region_role == SigningRegionRole.PARTY_A
    assert "seal_block" in region.confidence_reasons


def _bbox(x0: float, y0: float, x1: float, y1: float) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _block(
    block_id: str,
    text: str,
    bbox: BBox,
    *,
    block_type: str = "text",
    page_no: int = 1,
    layout_bbox: BBox | None = None,
) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=page_no,
        text=text,
        bbox=bbox,
        block_type=block_type,
        layout_bbox=layout_bbox,
    )


def _document(blocks: list[TextBlock], *, page_no: int = 1) -> Document:
    return Document(
        filename="test.pdf",
        path="test.pdf",
        page_count=1,
        pages=[Page(page_no=page_no, width=595, height=842, blocks=blocks)],
    )


def test_extractor_detects_seal_label_and_date_region() -> None:
    doc = _document(
        [
            _block("label", "甲方（盖章）：", _bbox(60, 650, 170, 675)),
            _block("seal", "合同专用章", _bbox(80, 680, 190, 780), block_type="seal"),
            _block("date", "签订日期：2026年5月6日", _bbox(60, 790, 240, 815)),
        ]
    )

    regions = SigningRegionExtractor().extract(doc)

    assert len(regions) == 1
    assert {element.element_type.value for element in regions[0].elements} >= {"seal", "label", "date_field"}
    assert regions[0].confidence >= 0.7


def test_extractor_rejects_keyword_only_body_text() -> None:
    doc = _document(
        [
            _block(
                "body",
                "13.2 对本合同的修改以双方签章的书面协议为准。甲方应当配合乙方履行义务。",
                _bbox(60, 680, 520, 720),
            ),
        ]
    )

    regions = SigningRegionExtractor().extract(doc)

    assert regions == []


def test_extractor_rejects_single_bottom_keyword() -> None:
    doc = _document(
        [
            _block("footer_word", "甲方", _bbox(60, 760, 90, 780)),
        ]
    )

    regions = SigningRegionExtractor().extract(doc)

    assert regions == []


def test_extractor_detects_form_like_signature_page_without_seal_block() -> None:
    doc = _document(
        [
            _block("context", "以下无正文，为签署页", _bbox(60, 520, 240, 545)),
            _block("party_a", "甲方：__________    乙方：__________", _bbox(60, 650, 460, 675)),
            _block("sign", "授权代表（签字）：__________", _bbox(60, 700, 280, 725)),
            _block("date", "日期：____年__月__日", _bbox(60, 750, 260, 775)),
        ]
    )

    regions = SigningRegionExtractor().extract(doc)

    assert len(regions) == 1
    assert regions[0].confidence >= 0.5


def test_extractor_detects_no_seal_signature_form_with_short_date_placeholder() -> None:
    doc = _document(
        [
            _block("party_a", "甲方：__________    乙方：__________", _bbox(60, 650, 460, 675)),
            _block("sign", "授权代表（签字）：__________", _bbox(60, 700, 280, 725)),
            _block("date", "日期：__年__月__日", _bbox(60, 750, 260, 775)),
        ]
    )

    regions = SigningRegionExtractor().extract(doc)

    assert len(regions) == 1
    assert "date_field" in {element.element_type.value for element in regions[0].elements}
    assert regions[0].confidence >= 0.5


def test_extractor_detects_straddling_seal_using_effective_bbox() -> None:
    doc = _document(
        [
            _block("label", "甲方（盖章）：", _bbox(60, 650, 170, 675)),
            _block(
                "seal",
                "合同专用章",
                _bbox(80, 500, 190, 620),
                block_type="seal",
                layout_bbox=_bbox(80, 500, 190, 680),
            ),
            _block("date", "签订日期：2026年5月6日", _bbox(60, 700, 240, 725)),
        ]
    )

    regions = SigningRegionExtractor().extract(doc)

    assert len(regions) == 1
    assert "seal" in {element.element_type.value for element in regions[0].elements}
    assert regions[0].confidence >= 0.7


def test_extractor_normalizes_out_of_page_region_bbox() -> None:
    doc = _document(
        [
            _block("label", "甲方（盖章）：", _bbox(800, 650, 900, 675)),
            _block("seal", "合同专用章", _bbox(820, 680, 920, 780), block_type="seal"),
            _block("date", "签订日期：2026年5月6日", _bbox(800, 790, 980, 815)),
        ]
    )

    regions = SigningRegionExtractor().extract(doc)

    assert len(regions) == 1
    bbox = regions[0].bbox
    page = doc.pages[0]
    assert 0 <= bbox.x0 <= bbox.x1 <= page.width
    assert 0 <= bbox.y0 <= bbox.y1 <= page.height
