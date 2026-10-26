from app.models import (
    BBox,
    DiffItem,
    EvidenceBox,
    PageOcrQualityProfile,
    ParseWarningDetail,
    TaskOcrQualitySummary,
)
from app.services.model_routing import ModelRoutingAnalyzer


def _summary(*profiles: PageOcrQualityProfile) -> TaskOcrQualitySummary:
    return TaskOcrQualitySummary(
        status=profiles[0].status if profiles else "OK",
        requires_review=any(profile.status != "OK" for profile in profiles),
        profiles=list(profiles),
    )


def _diff(diff_id: str = "D001", source_type: str = "clause", text: str = "付款金额为100元") -> DiffItem:
    return DiffItem(
        diff_id=diff_id,
        diff_type="MODIFY",
        source_type=source_type,
        original_text=text,
        compare_text=text.replace("100", "120"),
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=10, y0=10, x1=100, y1=30),
                confidence=0.46,
                evidence_quality="LOW",
            )
        ],
        review_flags=["OCR_LOW_CONFIDENCE"],
        quality_status="NEEDS_REVIEW",
    )


def test_analyzer_recommends_high_dpi_retry_for_low_confidence_critical_text_page() -> None:
    profile = PageOcrQualityProfile(
        side="original",
        page_no=1,
        status="LOW_TEXT_CONFIDENCE",
        reasons=["LOW_AVG_CONFIDENCE"],
        affected_diff_ids=["D001"],
    )

    summary = ModelRoutingAnalyzer().analyze(_summary(profile), [_diff()])

    route = summary.routes[0]
    assert route.page_type == "scan_low_quality"
    assert route.recommended_route == "HIGH_DPI_PAGE_RETRY"
    assert route.should_execute is False
    assert "LOW_AVG_CONFIDENCE" in route.reason_codes
    assert summary.retry_recommended_count == 1


def test_analyzer_recommends_table_region_retry_for_table_risk() -> None:
    profile = PageOcrQualityProfile(
        side="compare",
        page_no=1,
        status="TABLE_RISK",
        reasons=["TABLE_CELL_UNMATCHED"],
        affected_diff_ids=["D001"],
    )

    summary = ModelRoutingAnalyzer().analyze(_summary(profile), [_diff(source_type="table")])

    route = summary.routes[0]
    assert route.page_type == "table_heavy"
    assert route.recommended_route == "TABLE_REGION_RETRY"
    assert summary.page_count_by_type == {"table_heavy": 1}
    assert summary.route_count_by_recommendation == {"TABLE_REGION_RETRY": 1}


def test_analyzer_recommends_manual_review_for_unreliable_mixed_page() -> None:
    profile = PageOcrQualityProfile(
        side="original",
        page_no=1,
        status="UNRELIABLE",
        reasons=["LOW_AVG_CONFIDENCE", "TABLE_CELL_UNMATCHED"],
        affected_diff_ids=["D001"],
    )

    summary = ModelRoutingAnalyzer().analyze(_summary(profile), [_diff(source_type="table")])

    route = summary.routes[0]
    assert route.page_type == "mixed"
    assert route.recommended_route == "MANUAL_REVIEW"
    assert summary.manual_review_recommended_count == 1


def test_analyzer_recommends_keep_current_for_ok_page_without_affected_diffs() -> None:
    profile = PageOcrQualityProfile(side="original", page_no=1, status="OK")

    summary = ModelRoutingAnalyzer().analyze(_summary(profile), [])

    route = summary.routes[0]
    assert route.page_type == "text_heavy"
    assert route.recommended_route == "KEEP_CURRENT"
    assert summary.retry_recommended_count == 0


def test_analyzer_uses_warnings_for_seal_signature_routing() -> None:
    profile = PageOcrQualityProfile(
        side="compare",
        page_no=2,
        status="SEAL_OR_SIGNATURE_RISK",
        reasons=["SEAL_OR_SIGNATURE_INCOMPLETE"],
        affected_diff_ids=["D009"],
    )
    warning = ParseWarningDetail(
        code="SEAL_REGION_INCOMPLETE",
        message="seal recognition incomplete",
        page_no=2,
        source="ocr_quality:compare",
    )

    summary = ModelRoutingAnalyzer().analyze(_summary(profile), [_diff("D009", source_type="seal")], [warning])

    route = summary.routes[0]
    assert route.page_type == "seal_signature"
    assert route.recommended_route == "MANUAL_REVIEW"
    assert "SEAL_REGION_INCOMPLETE" in route.reason_codes


def test_analyzer_does_not_mutate_diffs() -> None:
    diff = _diff()
    original_flags = list(diff.review_flags)
    original_evidence = list(diff.original_evidence)
    profile = PageOcrQualityProfile(
        side="original",
        page_no=1,
        status="LOW_TEXT_CONFIDENCE",
        reasons=["LOW_AVG_CONFIDENCE"],
        affected_diff_ids=["D001"],
    )

    ModelRoutingAnalyzer().analyze(_summary(profile), [diff])

    assert diff.review_flags == original_flags
    assert diff.original_evidence == original_evidence
    assert diff.quality_status == "NEEDS_REVIEW"
