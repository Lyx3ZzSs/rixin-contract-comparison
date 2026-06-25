import pytest
from pydantic import ValidationError

from app.models import (
    BBox,
    CompareTask,
    Document,
    DocumentProfile,
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
