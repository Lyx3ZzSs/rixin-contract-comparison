from app.models import (
    CompareTask,
    DiffItem,
    OcrRemediationAction,
    PageOcrQualityProfile,
    TaskOcrRemediationSummary,
    TaskOcrQualitySummary,
)
from app.services.ocr_remediation import OcrRemediationPlanner


def test_ocr_remediation_action_defaults_are_planning_safe():
    action = OcrRemediationAction(
        action_id="original:1:diff-1:RELOCATE_EVIDENCE",
        action_type="RELOCATE_EVIDENCE",
        reason="EVIDENCE_UNRELIABLE",
        side="original",
        page_no=1,
        diff_id="diff-1",
    )

    assert action.status == "PLANNED"
    assert action.changed_evidence is False
    assert action.changed_diff_text is False
    assert action.review_flags_added == []


def test_task_ocr_remediation_summary_defaults_are_legacy_safe():
    summary = TaskOcrRemediationSummary()

    assert summary.status == "OK"
    assert summary.requires_manual_review is False
    assert summary.attempted_action_count == 0
    assert summary.successful_action_count == 0
    assert summary.unresolved_action_count == 0
    assert summary.manual_review_required_count == 0
    assert summary.actions == []


def test_compare_task_accepts_missing_ocr_remediation_summary():
    task = CompareTask(task_id="task-1")

    assert task.ocr_remediation_summary is None


def test_planner_creates_relocate_action_for_unreliable_evidence():
    diff = DiffItem(
        diff_id="diff-1",
        diff_type="MODIFY",
        source_type="clause",
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )
    summary = TaskOcrQualitySummary(
        status="LAYOUT_MISMATCH",
        requires_review=True,
        risk_page_count=1,
        affected_diff_count=1,
        profiles=[
            PageOcrQualityProfile(
                side="original",
                page_no=2,
                status="LAYOUT_MISMATCH",
                reasons=["LOW_LAYOUT_MATCH_RATE"],
                affected_diff_ids=["diff-1"],
                metrics={"layout_match_rate": 0.5},
            )
        ],
    )

    remediation = OcrRemediationPlanner().plan(summary, [diff])

    assert remediation.status == "ACTIONS_PLANNED"
    assert remediation.attempted_action_count == 1
    assert remediation.unresolved_action_count == 1
    assert remediation.actions[0].action_type == "RELOCATE_EVIDENCE"
    assert remediation.actions[0].side == "original"
    assert remediation.actions[0].page_no == 2
    assert remediation.actions[0].diff_id == "diff-1"
    assert remediation.actions[0].before_quality["ocr_status"] == "LAYOUT_MISMATCH"


def test_planner_snapshots_before_quality_from_profile_data():
    diff = DiffItem(
        diff_id="diff-1",
        diff_type="MODIFY",
        source_type="clause",
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )
    summary = TaskOcrQualitySummary(
        status="LAYOUT_MISMATCH",
        requires_review=True,
        risk_page_count=1,
        affected_diff_count=1,
        profiles=[
            PageOcrQualityProfile(
                side="original",
                page_no=2,
                status="LAYOUT_MISMATCH",
                reasons=["LOW_LAYOUT_MATCH_RATE"],
                affected_diff_ids=["diff-1"],
                metrics={"layout_match_rate": 0.5, "nested": {"score": 0.7}},
            )
        ],
    )

    remediation = OcrRemediationPlanner().plan(summary, [diff])
    action = remediation.actions[0]

    summary.profiles[0].reasons.append("MUTATED_REASON")
    summary.profiles[0].metrics["layout_match_rate"] = 0.1
    summary.profiles[0].metrics["nested"]["score"] = 0.2

    assert action.before_quality["ocr_reasons"] == ["LOW_LAYOUT_MATCH_RATE"]
    assert action.before_quality["ocr_metrics"] == {
        "layout_match_rate": 0.5,
        "nested": {"score": 0.7},
    }


def test_planner_escalates_page_unreliable_once_per_diff():
    diff = DiffItem(
        diff_id="diff-1",
        diff_type="MODIFY",
        source_type="table",
        review_flags=["PAGE_UNRELIABLE", "TABLE_STRUCTURE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )
    summary = TaskOcrQualitySummary(
        status="UNRELIABLE",
        requires_review=True,
        risk_page_count=1,
        affected_diff_count=1,
        profiles=[
            PageOcrQualityProfile(
                side="compare",
                page_no=3,
                status="UNRELIABLE",
                reasons=["TABLE_CELL_UNMATCHED", "LOW_AVG_CONFIDENCE"],
                affected_diff_ids=["diff-1"],
                metrics={"avg_confidence": 0.52},
            )
        ],
    )

    remediation = OcrRemediationPlanner().plan(summary, [diff])
    action_types = [action.action_type for action in remediation.actions]

    assert action_types == ["ESCALATE_MANUAL_REVIEW"]
    assert remediation.status == "MANUAL_REVIEW_REQUIRED"
    assert remediation.requires_manual_review is True
    assert remediation.manual_review_required_count == 1


def test_planner_returns_ok_summary_without_ocr_risk():
    remediation = OcrRemediationPlanner().plan(None, [])

    assert remediation.status == "OK"
    assert remediation.actions == []
