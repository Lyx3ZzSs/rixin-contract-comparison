from app.models import PageOcrQualityProfile, TaskOcrQualitySummary


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
