from app.models import BBox, EvidenceBox
from app.services.text_coordinate_locator import TextCoordinateLocator


def test_text_coordinate_locator_treats_signing_region_as_precise_evidence() -> None:
    existing = [
        EvidenceBox(
            page_no=2,
            bbox=BBox(x0=37, y0=242, x1=556, y1=687),
            method="signing_region",
            text="甲方：浙江公司",
            highlight_type="MODIFY",
        )
    ]

    assert TextCoordinateLocator()._should_refine(existing) is False
