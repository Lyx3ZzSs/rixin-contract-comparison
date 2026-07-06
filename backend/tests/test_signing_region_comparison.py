from app.models import BBox
from app.services.signing_region.comparator import SigningRegionComparator
from app.services.signing_region.diff_builder import SigningRegionDiffBuilder
from app.services.signing_region.matcher import SigningRegionMatcher
from app.services.signing_region.models import SigningElement, SigningElementType, SigningRegion, SigningRegionRole


def _region(region_id: str, text: str, *, page_no: int = 1, x0: float = 60) -> SigningRegion:
    return SigningRegion(
        region_id=region_id,
        page_no=page_no,
        bbox=BBox(x0=x0, y0=650, x1=x0 + 160, y1=780),
        region_role=SigningRegionRole.PARTY_A,
        confidence=0.9,
        confidence_reasons=["test"],
        elements=[
            SigningElement(
                element_id=f"{region_id}-seal",
                element_type=SigningElementType.SEAL,
                page_no=page_no,
                bbox=BBox(x0=x0 + 20, y0=680, x1=x0 + 120, y1=760),
                text=text,
                confidence=0.9,
                source="layout",
            )
        ],
    )


def test_matcher_pairs_regions_by_page_and_role() -> None:
    original = [_region("O1", "A公司")]
    compare = [_region("C1", "A公司")]

    pairs = SigningRegionMatcher().match(original, compare)

    assert pairs == [(original[0], compare[0], 1.0)]


def test_comparator_detects_seal_text_change() -> None:
    comparison = SigningRegionComparator().compare(_region("O1", "A公司"), _region("C1", "B公司"), match_confidence=0.9)

    assert comparison.diff_type == "MODIFY"
    assert comparison.seal_changes[0]["original_text"] == "A公司"
    assert comparison.seal_changes[0]["compare_text"] == "B公司"
    assert "SIGNING_SEAL_CHANGE" in comparison.review_flags


def test_diff_builder_outputs_signing_region_diff() -> None:
    comparison = SigningRegionComparator().compare(_region("O1", "A公司"), _region("C1", "B公司"), match_confidence=0.9)

    diffs = SigningRegionDiffBuilder().build_diffs([comparison], start_index=3)

    assert len(diffs) == 1
    assert diffs[0].diff_id == "D003"
    assert diffs[0].source_type == "signing_region"
    assert "A公司" in diffs[0].original_text
    assert "B公司" in diffs[0].compare_text
    assert diffs[0].original_evidence[0].method == "signing_region"
