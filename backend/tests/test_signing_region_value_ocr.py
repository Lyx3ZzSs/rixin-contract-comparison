from pathlib import Path

import fitz

from app.models import BBox, Document, Page, TextBlock
from app.services.signing_region.models import (
    SigningElement,
    SigningElementType,
    SigningRegion,
    SigningRegionRole,
)
from app.services.signing_region.value_ocr import SigningFieldValueOCR


class _FakeOCRExtractor:
    def __init__(self, texts: list[str]) -> None:
        self.texts = iter(texts)

    def predict(self, path: str | Path, file_type: int = 0) -> dict[str, object]:
        assert file_type == 1
        return {"text": next(self.texts)}

    @staticmethod
    def payload_to_document(payload: dict[str, object], path: str | Path) -> Document:
        text = str(payload["text"])
        return Document(
            filename=Path(path).name,
            path=str(path),
            page_count=1,
            pages=[
                Page(
                    page_no=1,
                    width=400,
                    height=120,
                    blocks=[
                        TextBlock(
                            block_id="roi-ocr",
                            page_no=1,
                            text=text,
                            bbox=BBox(x0=40, y0=20, x1=180, y1=100),
                            confidence=0.95,
                            source="ppocrv5",
                        )
                    ],
                )
            ],
        )


def _pdf(tmp_path: Path) -> Path:
    path = tmp_path / "page.pdf"
    document = fitz.open()
    document.new_page(width=600, height=800)
    document.save(path)
    document.close()
    return path


def _field(
    *,
    party: str = "甲方",
    value: str = "伟",
    supporting: str = "郑伟",
    contaminated: bool = False,
) -> SigningElement:
    roi = BBox(x0=180 if party == "甲方" else 400, y0=300, x1=280 if party == "甲方" else 500, y1=330)
    return SigningElement(
        element_id=f"{party}-legal-representative",
        element_type=SigningElementType.FIELD,
        page_no=1,
        bbox=BBox(x0=80 if party == "甲方" else 320, y0=300, x1=280 if party == "甲方" else 500, y1=330),
        text=value,
        confidence=0.9,
        source="inferred",
        raw_ref={
            "party_role": party,
            "field_key": "legal_representative",
            "value_roi": roi.model_dump(),
            "value_evidence": [
                {
                    "source": "ppocrv5",
                    "text": value,
                    "normalized_text": value,
                    "bbox": BBox(x0=250, y0=300, x1=270, y1=330).model_dump(),
                    "localized": True,
                    "provenance_valid": True,
                }
            ],
            "supporting_unlocalized_evidence": [
                {
                    "source": "ppstructure",
                    "candidate_value": supporting,
                    "rejection_reason": "UNLOCALIZED_BLOCK_VALUE",
                    "localized": False,
                    "provenance_valid": False,
                }
            ],
            "rejected_contaminated_evidence": (
                [{"candidate_value": "2267.28京", "rejection_reason": "CROSS_COLUMN_SOURCE_BLOCK"}]
                if contaminated
                else []
            ),
            "content_recognition_status": "NEEDS_REVIEW",
            "content_recognition_reason": "SINGLE_SOURCE",
        },
    )


def _region(*elements: SigningElement) -> SigningRegion:
    return SigningRegion(
        region_id="SR-1",
        page_no=1,
        bbox=BBox(x0=60, y0=250, x1=540, y1=380),
        region_role=SigningRegionRole.BOTH_PARTIES,
        confidence=0.9,
        elements=list(elements),
    )


def test_targeted_roi_ocr_can_localize_supported_complete_value(tmp_path: Path) -> None:
    field = _field()
    service = SigningFieldValueOCR(extractor=_FakeOCRExtractor(["郑伟"]))

    diagnostics = service.enrich(_pdf(tmp_path), [_region(field)])

    assert diagnostics[0]["status"] == "LOCALIZED"
    assert field.text == "郑伟"
    assert field.raw_ref["content_recognition_status"] == "NEEDS_REVIEW"
    assert field.raw_ref["content_recognition_reason"] == "TARGETED_ROI_OCR_WITH_UNLOCALIZED_SUPPORT"
    assert field.raw_ref["value_evidence"][-1]["source"] == "targeted_roi_ppocrv5"
    assert field.raw_ref["value_evidence"][-1]["localized"] is True
    assert BBox.model_validate(field.raw_ref["display_value_bbox"]).x1 <= 280


def test_targeted_roi_ocr_does_not_assign_other_party_value(tmp_path: Path) -> None:
    left = _field(party="甲方", value="伟", supporting="郑伟")
    service = SigningFieldValueOCR(extractor=_FakeOCRExtractor(["李四"]))

    diagnostics = service.enrich(_pdf(tmp_path), [_region(left)])

    assert diagnostics[0]["status"] == "NO_SUPPORTING_MATCH"
    assert left.text == "伟"
    assert len(left.raw_ref["value_evidence"]) == 1


def test_targeted_roi_ocr_never_uses_contaminated_cross_column_evidence(tmp_path: Path) -> None:
    field = _field(value="伟", supporting="郑伟", contaminated=True)
    service = SigningFieldValueOCR(extractor=_FakeOCRExtractor(["郑伟"]))

    diagnostics = service.enrich(_pdf(tmp_path), [_region(field)])

    assert diagnostics == []
    assert field.text == "伟"
    assert len(field.raw_ref["value_evidence"]) == 1
