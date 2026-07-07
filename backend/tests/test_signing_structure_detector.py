from app.models import BBox
from app.services.signing_region.models import (
    SigningBlock,
    SigningBlockConfidenceLevel,
    SigningBlockRole,
    SigningPage,
    SigningPageType,
    SigningVisualFeatures,
)


def test_signing_structure_models_hold_block_page_and_visual_features() -> None:
    visual = SigningVisualFeatures(
        status="ok",
        has_red_seal=True,
        has_handwriting=False,
        visual_hash="abc123",
        confidence=0.8,
        reasons=["red_connected_component"],
    )
    block = SigningBlock(
        block_id="SB-10-1",
        page_no=10,
        bbox=BBox(x0=60, y0=610, x1=520, y1=740),
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["paired_parties", "seal_signature_date_cluster"],
        source_block_ids=["p10_b24", "p10_b25"],
        text="甲方：A 乙方：B\n(盖章)\n(签字)\n日期：",
        visual_features=visual,
        exclude_from_clause_diff=True,
    )
    page = SigningPage(
        page_no=10,
        bbox=BBox(x0=0, y0=0, x1=595, y1=842),
        signing_page_type=SigningPageType.MIXED_PAGE,
        confidence=0.72,
        confidence_reasons=["contains_high_confidence_signing_block"],
        block_ids=[block.block_id],
        exclude_full_page_from_clause_diff=False,
    )

    assert page.signing_page_type == SigningPageType.MIXED_PAGE
    assert block.block_role == SigningBlockRole.BOTH_PARTIES
    assert block.visual_features.visual_hash == "abc123"
    assert block.exclude_from_clause_diff is True
