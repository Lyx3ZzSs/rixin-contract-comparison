import pytest
from pydantic import ValidationError

from app.models import (
    BBox,
    CompareTask,
    DiffItem,
    Document,
    DocumentProfile,
    EvidenceBox,
    LayoutQualityReport,
    Page,
    PageLayoutQualityReport,
    PageOcrQualityProfile,
    PageProfile,
    ParseWarningDetail,
    TaskOcrQualitySummary,
    TextBlock,
)
from app.services.ocr_quality import OcrQualityProfiler


def _document_with_blocks(blocks: list[TextBlock]) -> Document:
    return Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[Page(page_no=1, width=600, height=800, blocks=blocks)],
    )


def _block(block_id: str, text: str, confidence: float | None, block_type: str = "text") -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=1,
        text=text,
        bbox=BBox(x0=10, y0=10, x1=100, y1=30),
        block_type=block_type,
        confidence=confidence,
    )


def _evidence(page_no: int) -> EvidenceBox:
    return EvidenceBox(page_no=page_no, bbox=BBox(x0=1, y0=2, x1=3, y1=4), confidence=0.9)


def test_task_ocr_quality_summary_defaults_and_counts() -> None:
    profile = PageOcrQualityProfile(
        side="original",
        page_no=1,
        status="LOW_TEXT_CONFIDENCE",
        score=0.75,
        reasons=["LOW_AVG_CONFIDENCE"],
        metrics={"avg_confidence": 0.7},
        affected_diff_ids=["D001"],
    )

    summary = TaskOcrQualitySummary(
        status="LOW_TEXT_CONFIDENCE",
        requires_review=True,
        page_count_by_status={"LOW_TEXT_CONFIDENCE": 1},
        risk_page_count=1,
        affected_diff_count=1,
        profiles=[profile],
    )

    assert summary.status == "LOW_TEXT_CONFIDENCE"
    assert summary.profiles[0].side == "original"
    assert summary.profiles[0].metrics["avg_confidence"] == 0.7


def test_compare_task_ocr_quality_summary_defaults_to_none() -> None:
    task = CompareTask(task_id="task-ocr-default")

    assert task.ocr_quality_summary is None


def test_compare_task_ocr_quality_summary_round_trips_json_dump() -> None:
    summary = TaskOcrQualitySummary(
        status="TABLE_RISK",
        requires_review=True,
        page_count_by_status={"TABLE_RISK": 1},
        risk_page_count=1,
        affected_diff_count=1,
        profiles=[
            PageOcrQualityProfile(
                side="compare",
                page_no=2,
                status="TABLE_RISK",
                score=0.5,
                reasons=["TABLE_ALIGNMENT_RISK"],
                metrics={"table_area_ratio": 0.6},
                affected_diff_ids=["D002"],
            )
        ],
    )

    task = CompareTask(task_id="task-ocr-roundtrip", ocr_quality_summary=summary)
    dumped = task.model_dump(mode="json")
    rehydrated = CompareTask(**dumped)

    assert rehydrated.ocr_quality_summary is not None
    assert rehydrated.ocr_quality_summary.status == "TABLE_RISK"
    assert rehydrated.ocr_quality_summary.profiles[0].side == "compare"
    assert rehydrated.ocr_quality_summary.profiles[0].affected_diff_ids == ["D002"]


def test_ocr_quality_models_validate_status_and_counts() -> None:
    with pytest.raises(ValidationError):
        PageOcrQualityProfile(side="original", page_no=1, status="UNKNOWN")

    with pytest.raises(ValidationError):
        TaskOcrQualitySummary(page_count_by_status={"UNKNOWN": 1})


def test_profiler_flags_low_text_confidence_profile() -> None:
    document = _document_with_blocks(
        [
            _block("b1", "low confidence text", 0.6),
            _block("b2", "more low confidence text", 0.7),
        ]
    )
    profile = DocumentProfile(
        filename="sample.pdf",
        page_count=1,
        page_profiles=[
            PageProfile(
                page_no=1,
                avg_confidence=0.65,
                text_block_count=2,
            )
        ],
    )

    profiles = OcrQualityProfiler().profile_side(
        side="original",
        document=document,
        document_profile=profile,
        layout_quality=None,
        warnings=[],
    )

    assert len(profiles) == 1
    assert profiles[0].status == "LOW_TEXT_CONFIDENCE"
    assert profiles[0].reasons == ["LOW_AVG_CONFIDENCE"]
    assert profiles[0].score < 1.0
    assert profiles[0].metrics["avg_confidence"] == 0.65
    assert profiles[0].metrics["text_block_count"] == 2


def test_profiler_flags_layout_and_reading_order_risks_as_unreliable() -> None:
    document = _document_with_blocks([_block("b1", "text", 0.95)])
    profile = DocumentProfile(filename="sample.pdf", page_count=1, page_profiles=[PageProfile(page_no=1)])
    layout_quality = LayoutQualityReport(
        page_count=1,
        ocr_block_count=4,
        matched_ocr_block_count=2,
        meaningful_unmatched_count=1,
        reading_order_conflict_count=1,
        page_quality=[
            PageLayoutQualityReport(
                page_no=1,
                ocr_block_count=4,
                matched_ocr_block_count=2,
                meaningful_unmatched_count=1,
                reading_order_conflict_count=1,
            )
        ],
    )

    profiles = OcrQualityProfiler().profile_side(
        side="original",
        document=document,
        document_profile=profile,
        layout_quality=layout_quality,
        warnings=[],
    )

    assert profiles[0].status == "UNRELIABLE"
    assert profiles[0].reasons == [
        "LOW_LAYOUT_MATCH_RATE",
        "MEANINGFUL_UNMATCHED_OCR",
        "READING_ORDER_CONFLICT",
    ]
    assert profiles[0].metrics["layout_match_rate"] == 0.5
    assert profiles[0].metrics["ocr_block_count"] == 4
    assert profiles[0].metrics["matched_ocr_block_count"] == 2
    assert profiles[0].metrics["meaningful_unmatched_count"] == 1
    assert profiles[0].metrics["reading_order_conflict_count"] == 1


def test_profiler_uses_single_page_aggregate_layout_quality_when_page_quality_is_missing() -> None:
    document = _document_with_blocks([_block("b1", "text", 0.95)])
    profile = DocumentProfile(filename="sample.pdf", page_count=1, page_profiles=[PageProfile(page_no=1)])
    layout_quality = LayoutQualityReport(
        page_count=1,
        ocr_block_count=4,
        matched_ocr_block_count=2,
        meaningful_unmatched_count=1,
        reading_order_conflict_count=1,
        page_quality=[],
    )

    profiles = OcrQualityProfiler().profile_side(
        side="original",
        document=document,
        document_profile=profile,
        layout_quality=layout_quality,
        warnings=[],
    )

    assert profiles[0].status == "UNRELIABLE"
    assert profiles[0].reasons == [
        "LOW_LAYOUT_MATCH_RATE",
        "MEANINGFUL_UNMATCHED_OCR",
        "READING_ORDER_CONFLICT",
    ]
    assert profiles[0].metrics["layout_match_rate"] == 0.5
    assert profiles[0].metrics["ocr_block_count"] == 4
    assert profiles[0].metrics["matched_ocr_block_count"] == 2
    assert profiles[0].metrics["meaningful_unmatched_count"] == 1
    assert profiles[0].metrics["reading_order_conflict_count"] == 1


def test_profiler_flags_table_risk_from_unmatched_cells() -> None:
    document = _document_with_blocks([_block("t1", "table text", 0.95, block_type="table")])
    profile = DocumentProfile(
        filename="sample.pdf",
        page_count=1,
        table_block_count=1,
        table_heavy_page_count=1,
        page_profiles=[
            PageProfile(
                page_no=1,
                table_heavy=True,
                table_block_count=1,
            )
        ],
    )
    layout_quality = LayoutQualityReport(
        page_count=1,
        table_cell_unmatched_count=2,
        page_quality=[PageLayoutQualityReport(page_no=1)],
    )

    profiles = OcrQualityProfiler().profile_side(
        side="original",
        document=document,
        document_profile=profile,
        layout_quality=layout_quality,
        warnings=[],
    )

    assert profiles[0].status == "TABLE_RISK"
    assert profiles[0].reasons == ["TABLE_CELL_UNMATCHED"]
    assert profiles[0].metrics["table_block_count"] == 1
    assert profiles[0].metrics["table_cell_unmatched_count"] == 2


def test_build_summary_orders_profiles_and_counts_highest_priority_risks() -> None:
    profiles = [
        PageOcrQualityProfile(
            side="compare",
            page_no=2,
            status="LOW_TEXT_CONFIDENCE",
            affected_diff_ids=["D1"],
        ),
        PageOcrQualityProfile(
            side="original",
            page_no=2,
            status="OK",
            affected_diff_ids=["D1"],
        ),
        PageOcrQualityProfile(
            side="compare",
            page_no=1,
            status="TABLE_RISK",
            affected_diff_ids=["D2", "D1"],
        ),
        PageOcrQualityProfile(
            side="original",
            page_no=1,
            status="UNRELIABLE",
        ),
    ]

    summary = OcrQualityProfiler().build_summary(profiles)

    assert summary.status == "UNRELIABLE"
    assert summary.requires_review is True
    assert summary.risk_page_count == 3
    assert summary.affected_diff_count == 2
    assert summary.page_count_by_status == {
        "LOW_TEXT_CONFIDENCE": 1,
        "OK": 1,
        "TABLE_RISK": 1,
        "UNRELIABLE": 1,
    }
    assert [(profile.side, profile.page_no) for profile in summary.profiles] == [
        ("original", 1),
        ("original", 2),
        ("compare", 1),
        ("compare", 2),
    ]


def test_profiler_flags_error_warning_as_unreliable() -> None:
    document = _document_with_blocks([_block("b1", "text", 0.95)])
    profile = DocumentProfile(filename="sample.pdf", page_count=1, page_profiles=[PageProfile(page_no=1)])
    warnings = [
        ParseWarningDetail(
            code="OCR_ENGINE_ERROR",
            message="OCR engine failed",
            severity="ERROR",
            page_no=1,
            source="ocr",
        )
    ]

    profiles = OcrQualityProfiler().profile_side(
        side="original",
        document=document,
        document_profile=profile,
        layout_quality=None,
        warnings=warnings,
    )

    assert profiles[0].status == "UNRELIABLE"
    assert profiles[0].reasons == ["EXTRACTION_ERROR_WARNING"]


def test_profiler_accepts_non_empty_string_warning_as_informational() -> None:
    document = _document_with_blocks([_block("b1", "clean text", 0.95)])
    profile = DocumentProfile(
        filename="sample.pdf",
        page_count=1,
        page_profiles=[
            PageProfile(
                page_no=1,
                avg_confidence=0.95,
                text_block_count=1,
            )
        ],
    )

    profiles = OcrQualityProfiler().profile_side(
        side="original",
        document=document,
        document_profile=profile,
        layout_quality=None,
        warnings=["legacy warning"],
    )

    assert profiles[0].status == "OK"
    assert profiles[0].reasons == []


def test_profiler_treats_missing_warnings_as_empty() -> None:
    document = _document_with_blocks([_block("b1", "clean text", 0.95)])
    profile = DocumentProfile(
        filename="sample.pdf",
        page_count=1,
        page_profiles=[
            PageProfile(
                page_no=1,
                avg_confidence=0.95,
                text_block_count=1,
            )
        ],
    )

    profiles = OcrQualityProfiler().profile_side(
        side="original",
        document=document,
        document_profile=profile,
        layout_quality=None,
        warnings=None,
    )

    assert profiles[0].status == "OK"
    assert profiles[0].reasons == []


def test_profiler_uses_table_block_confidence_without_document_profile() -> None:
    document = _document_with_blocks([_block("t1", "low confidence table", 0.6, block_type="table")])

    profiles = OcrQualityProfiler().profile_side(
        side="original",
        document=document,
        document_profile=None,
        layout_quality=None,
        warnings=[],
    )

    assert profiles[0].status == "LOW_TEXT_CONFIDENCE"
    assert profiles[0].reasons == ["LOW_AVG_CONFIDENCE"]
    assert profiles[0].metrics["avg_confidence"] == 0.6
    assert profiles[0].metrics["table_block_count"] == 1


def test_profiler_does_not_apply_global_table_cell_unmatched_count_to_every_page() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(page_no=1, width=600, height=800, blocks=[_block("t1", "page one table", 0.95, block_type="table")]),
            Page(page_no=2, width=600, height=800, blocks=[_block("t2", "page two table", 0.95, block_type="table")]),
        ],
    )
    profile = DocumentProfile(
        filename="sample.pdf",
        page_count=2,
        table_block_count=2,
        table_heavy_page_count=2,
        page_profiles=[
            PageProfile(page_no=1, avg_confidence=0.95, table_block_count=1, table_heavy=True),
            PageProfile(page_no=2, avg_confidence=0.95, table_block_count=1, table_heavy=True),
        ],
    )
    layout_quality = LayoutQualityReport(
        page_count=2,
        table_cell_unmatched_count=2,
        page_quality=[PageLayoutQualityReport(page_no=1), PageLayoutQualityReport(page_no=2)],
    )

    profiles = OcrQualityProfiler().profile_side(
        side="original",
        document=document,
        document_profile=profile,
        layout_quality=layout_quality,
        warnings=[],
    )

    assert [profile.status for profile in profiles] == ["OK", "OK"]
    assert all("TABLE_CELL_UNMATCHED" not in profile.reasons for profile in profiles)
    assert all("table_cell_unmatched_count" not in profile.metrics for profile in profiles)


def test_profiler_ignores_empty_string_warnings() -> None:
    profiler = OcrQualityProfiler()
    document = _document_with_blocks([_block("b1", "clean text", 0.95)])
    profile = DocumentProfile(
        filename="sample.pdf",
        page_count=1,
        page_profiles=[
            PageProfile(
                page_no=1,
                avg_confidence=0.95,
                text_block_count=1,
            )
        ],
    )

    profiles = profiler.profile_side(
        side="original",
        document=document,
        document_profile=profile,
        layout_quality=None,
        warnings=[""],
    )

    assert profiles[0].status == "OK"
    assert profiles[0].reasons == []


def test_propagates_unreliable_page_to_diff_review_status() -> None:
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        title="付款",
        original_text="30日付款",
        compare_text="45日付款",
        original_evidence=[_evidence(1)],
    )
    profiles = [
        PageOcrQualityProfile(
            side="original",
            page_no=1,
            status="UNRELIABLE",
            score=0.3,
            reasons=["LOW_AVG_CONFIDENCE", "READING_ORDER_CONFLICT"],
        )
    ]

    summary = OcrQualityProfiler().apply_to_diffs(diffs=[diff], profiles=profiles)

    assert diff.quality_status == "NEEDS_REVIEW"
    assert "PAGE_UNRELIABLE" in diff.review_flags
    assert "OCR_LOW_CONFIDENCE" in diff.review_flags
    assert "READING_ORDER_RISK" in diff.review_flags
    assert profiles[0].affected_diff_ids == ["D001"]
    assert summary.affected_diff_count == 1


def test_low_confidence_business_diff_needs_review() -> None:
    diff = DiffItem(
        diff_id="D002",
        diff_type="MODIFY",
        title="付款金额",
        original_text="付款金额100万元",
        compare_text="付款金额120万元",
        compare_evidence=[_evidence(2)],
    )
    profiles = [
        PageOcrQualityProfile(
            side="compare",
            page_no=2,
            status="LOW_TEXT_CONFIDENCE",
            reasons=["LOW_AVG_CONFIDENCE"],
        )
    ]

    OcrQualityProfiler().apply_to_diffs(diffs=[diff], profiles=profiles)

    assert diff.quality_status == "NEEDS_REVIEW"
    assert "OCR_LOW_CONFIDENCE" in diff.review_flags


def test_missing_evidence_with_risk_marks_evidence_unreliable() -> None:
    diff = DiffItem(
        diff_id="D003",
        diff_type="ADD",
        title="新增条款",
        compare_text="新增付款条款",
        source_type="clause",
    )
    profiles = [
        PageOcrQualityProfile(
            side="compare",
            page_no=1,
            status="TABLE_RISK",
            reasons=["TABLE_CELL_UNMATCHED"],
        )
    ]

    summary = OcrQualityProfiler().apply_to_diffs(diffs=[diff], profiles=profiles)

    assert diff.quality_status == "NEEDS_REVIEW"
    assert diff.review_flags == ["EVIDENCE_UNRELIABLE"]
    assert profiles[0].affected_diff_ids == ["D003"]
    assert summary.affected_diff_count == 1
