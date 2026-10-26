from __future__ import annotations

from copy import deepcopy

from app.models import (
    DiffItem,
    OcrRemediationAction,
    OcrRemediationActionType,
    PageOcrQualityProfile,
    TaskOcrQualitySummary,
    TaskOcrRemediationSummary,
)


_FLAG_ACTION_PRIORITY: list[tuple[str, OcrRemediationActionType, str]] = [
    ("PAGE_UNRELIABLE", "ESCALATE_MANUAL_REVIEW", "PAGE_UNRELIABLE"),
    ("EVIDENCE_UNRELIABLE", "RELOCATE_EVIDENCE", "EVIDENCE_UNRELIABLE"),
    ("TABLE_STRUCTURE_UNRELIABLE", "REPAIR_TABLE", "TABLE_STRUCTURE_UNRELIABLE"),
    ("OCR_LOW_CONFIDENCE", "RETRY_OCR_PAGE", "OCR_LOW_CONFIDENCE"),
    ("LAYOUT_MISMATCH_RISK", "MARK_REVIEW", "LAYOUT_MISMATCH_RISK"),
    ("READING_ORDER_RISK", "MARK_REVIEW", "READING_ORDER_RISK"),
    ("SEAL_OR_SIGNATURE_RISK", "MARK_REVIEW", "SEAL_OR_SIGNATURE_RISK"),
]


class OcrRemediationPlanner:
    """Create deterministic remediation actions from OCR quality risk signals."""

    def plan(
        self,
        ocr_quality_summary: TaskOcrQualitySummary | None,
        diffs: list[DiffItem],
    ) -> TaskOcrRemediationSummary:
        if ocr_quality_summary is None or not ocr_quality_summary.requires_review:
            return TaskOcrRemediationSummary()

        profiles_by_diff = self._profiles_by_diff(ocr_quality_summary.profiles)
        actions: list[OcrRemediationAction] = []
        seen: set[str] = set()

        for diff in sorted(diffs, key=lambda item: item.diff_id):
            profiles = profiles_by_diff.get(diff.diff_id, [])
            action_type, reason = self._action_for_diff(diff)
            if action_type is None:
                continue
            if action_type == "ESCALATE_MANUAL_REVIEW":
                profiles = profiles[:1] or [None]
            elif action_type == "RETRY_OCR_PAGE":
                profiles = self._retry_profiles(diff, profiles) or [None]
            elif not profiles:
                profiles = [None]

            for profile in profiles:
                action = self._build_action(diff, profile, action_type, reason)
                if action.action_id in seen:
                    continue
                seen.add(action.action_id)
                actions.append(action)

        return self._summary(actions)

    @staticmethod
    def _profiles_by_diff(
        profiles: list[PageOcrQualityProfile],
    ) -> dict[str, list[PageOcrQualityProfile]]:
        result: dict[str, list[PageOcrQualityProfile]] = {}
        for profile in sorted(profiles, key=lambda item: (item.side, item.page_no)):
            if profile.status == "OK":
                continue
            for diff_id in profile.affected_diff_ids:
                result.setdefault(diff_id, []).append(profile)
        return result

    @staticmethod
    def _retry_profiles(
        diff: DiffItem,
        profiles: list[PageOcrQualityProfile],
    ) -> list[PageOcrQualityProfile]:
        if diff.original_evidence and not diff.compare_snippet:
            source_pages = {item.page_no for item in diff.original_evidence}
            target_side = "compare"
        elif diff.compare_evidence and not diff.original_snippet:
            source_pages = {item.page_no for item in diff.compare_evidence}
            target_side = "original"
        else:
            return []
        return [profile for profile in profiles if profile.side == target_side and profile.page_no in source_pages]

    @staticmethod
    def _action_for_diff(diff: DiffItem) -> tuple[OcrRemediationActionType | None, str]:
        flags = set(diff.review_flags)
        if OcrRemediationPlanner._can_retry_missing_counterpart_text(diff, flags):
            return "RETRY_OCR_PAGE", "MISSING_COUNTERPART_CLAUSE_TEXT"
        for flag, action_type, reason in _FLAG_ACTION_PRIORITY:
            if flag in flags:
                return action_type, reason
        if diff.quality_status == "NEEDS_REVIEW" and any(flag.startswith("OCR_") for flag in flags):
            return "MARK_REVIEW", "OCR_NEEDS_REVIEW"
        return None, ""

    @staticmethod
    def _can_retry_missing_counterpart_text(diff: DiffItem, flags: set[str]) -> bool:
        return (
            diff.source_type == "clause"
            and diff.diff_type == "MODIFY"
            and "PAGE_UNRELIABLE" in flags
            and ((bool(diff.original_snippet or diff.original_text) and not diff.compare_snippet and bool(diff.original_evidence))
                 or (bool(diff.compare_snippet or diff.compare_text) and not diff.original_snippet and bool(diff.compare_evidence)))
        )

    @staticmethod
    def _build_action(
        diff: DiffItem,
        profile: PageOcrQualityProfile | None,
        action_type: OcrRemediationActionType,
        reason: str,
    ) -> OcrRemediationAction:
        side = profile.side if profile is not None else None
        page_no = profile.page_no if profile is not None else None
        status = "MANUAL_REVIEW_REQUIRED" if action_type == "ESCALATE_MANUAL_REVIEW" else "PLANNED"
        action_id = ":".join(
            [
                side or "unknown",
                str(page_no) if page_no is not None else "unknown",
                diff.diff_id,
                action_type,
            ]
        )
        before_quality = {}
        if profile is not None:
            before_quality = {
                "ocr_status": profile.status,
                "ocr_score": profile.score,
                "ocr_reasons": list(profile.reasons),
                "ocr_metrics": deepcopy(profile.metrics),
            }
        return OcrRemediationAction(
            action_id=action_id,
            action_type=action_type,
            reason=reason,
            status=status,
            side=side,
            page_no=page_no,
            diff_id=diff.diff_id,
            before_quality=before_quality,
            changed_diff_text=False,
            review_flags_added=["OCR_REMEDIATION_PLANNED"],
            notes=[f"Planning-only action for {reason}."],
        )

    @staticmethod
    def _summary(actions: list[OcrRemediationAction]) -> TaskOcrRemediationSummary:
        manual_count = sum(1 for action in actions if action.status == "MANUAL_REVIEW_REQUIRED")
        unresolved_count = sum(1 for action in actions if action.status in {"PLANNED", "MANUAL_REVIEW_REQUIRED"})
        status = "OK"
        if manual_count:
            status = "MANUAL_REVIEW_REQUIRED"
        elif actions:
            status = "ACTIONS_PLANNED"
        return TaskOcrRemediationSummary(
            status=status,
            requires_manual_review=bool(manual_count),
            attempted_action_count=len(actions),
            successful_action_count=sum(1 for action in actions if action.status == "SUCCEEDED"),
            unresolved_action_count=unresolved_count,
            manual_review_required_count=manual_count,
            actions=actions,
        )
