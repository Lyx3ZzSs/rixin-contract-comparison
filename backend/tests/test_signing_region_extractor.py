from app.models import BBox
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
