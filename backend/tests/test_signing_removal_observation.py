from __future__ import annotations

from pathlib import Path

import fitz

from app.models import BBox
from app.services.field_observation import ObservationReason
from app.services.signing_region.comparator import SigningRegionComparator
from app.services.signing_region.models import SigningElement, SigningElementType, SigningRegion
from app.services.signing_region.removal_observation import SigningRemovalObservationDetector


def _write_compare_pdf(path: Path, *, fill: tuple[float, float, float]) -> None:
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        page.draw_rect(fitz.Rect(70, 430, 310, 510), color=None, fill=fill)
        document.save(path)


def _regions() -> tuple[SigningRegion, SigningRegion]:
    original_field = SigningElement(
        element_id="original-legal-representative",
        element_type=SigningElementType.FIELD,
        page_no=1,
        bbox=BBox(x0=70, y0=460, x1=180, y1=480),
        text="雍正",
        confidence=0.99,
        source="inferred",
        raw_ref={
            "party_role": "甲方",
            "field_key": "legal_representative",
            "field_label": "法人代表",
            "source_bbox": BBox(x0=70, y0=450, x1=300, y1=490).model_dump(),
            "value_bbox": BBox(x0=150, y0=460, x1=180, y1=480).model_dump(),
        },
    )
    original_anchor = SigningElement(
        element_id="original-anchor",
        element_type=SigningElementType.FIELD,
        page_no=1,
        bbox=BBox(x0=70, y0=650, x1=200, y1=670),
        text="100096",
        confidence=0.99,
        source="inferred",
        raw_ref={"party_role": "甲方", "field_key": "postal_code", "field_label": "邮政编码"},
    )
    compare_anchor = original_anchor.model_copy(deep=True, update={"element_id": "compare-anchor"})
    original = SigningRegion(
        region_id="original",
        page_no=1,
        bbox=BBox(x0=40, y0=300, x1=560, y1=720),
        confidence=0.95,
        elements=[original_field, original_anchor],
    )
    compare = SigningRegion(
        region_id="compare",
        page_no=1,
        bbox=BBox(x0=40, y0=300, x1=560, y1=720),
        confidence=0.95,
        elements=[compare_anchor],
    )
    return original, compare


def test_tinted_opaque_cover_marks_business_removal_and_confirms_delete(tmp_path: Path) -> None:
    compare_pdf = tmp_path / "compare.pdf"
    _write_compare_pdf(compare_pdf, fill=(244 / 255, 252 / 255, 1.0))
    original, compare = _regions()

    diagnostics = SigningRemovalObservationDetector().enrich(compare_pdf, [(original, compare, 0.95)])
    comparison = SigningRegionComparator().compare(original, compare, match_confidence=0.95)
    change = next(item for item in comparison.field_changes if item["field_key"] == "legal_representative")

    assert diagnostics[0]["observation"]["evidence"]["method"] == "UNIFORM_TINTED_OPAQUE_COVER"
    assert change["compare_observation_reason"] == ObservationReason.BUSINESS_CONTENT_REMOVED_OR_COVERED.value
    assert change["change_decision"] == "CONFIRMED"
    assert change["fill_state_change"] == "cleared"


def test_plain_white_missing_roi_is_not_promoted_to_business_removal(tmp_path: Path) -> None:
    compare_pdf = tmp_path / "compare.pdf"
    _write_compare_pdf(compare_pdf, fill=(1.0, 1.0, 1.0))
    original, compare = _regions()

    diagnostics = SigningRemovalObservationDetector().enrich(compare_pdf, [(original, compare, 0.95)])
    comparison = SigningRegionComparator().compare(original, compare, match_confidence=0.95)
    change = next(item for item in comparison.field_changes if item["field_key"] == "legal_representative")

    assert diagnostics == []
    assert change["compare_observation_reason"] == ObservationReason.FIELD_ELEMENT_MISSING.value
    assert change["change_decision"] == "CANDIDATE"


def test_opaque_cover_on_low_confidence_region_match_is_not_confirmed(tmp_path: Path) -> None:
    compare_pdf = tmp_path / "compare.pdf"
    _write_compare_pdf(compare_pdf, fill=(244 / 255, 252 / 255, 1.0))
    original, compare = _regions()

    diagnostics = SigningRemovalObservationDetector().enrich(compare_pdf, [(original, compare, 0.69)])
    comparison = SigningRegionComparator().compare(original, compare, match_confidence=0.69)
    change = next(item for item in comparison.field_changes if item["field_key"] == "legal_representative")

    assert diagnostics == []
    assert change["compare_observation_reason"] == ObservationReason.FIELD_ELEMENT_MISSING.value
    assert change["change_decision"] == "CANDIDATE"
