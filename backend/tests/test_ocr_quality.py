import pytest
from pydantic import ValidationError

from app.models import CompareTask, PageOcrQualityProfile, TaskOcrQualitySummary


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
