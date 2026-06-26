from app.models import (
    OcrRemediationAction,
    TaskOcrRemediationSummary,
    CompareTask,
)


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
