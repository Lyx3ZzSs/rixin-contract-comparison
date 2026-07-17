from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.errors import ConflictError, NotFoundError
from app.infrastructure.task_repository import TaskRepository, default_task_repository, to_jsonable
from app.models import AuditItemReview, CompareTask, DiffItem, EvidenceBox, ReviewStatus
from app.services.audit_summary import AuditItem, build_audit_items, normalized_audit_item_reviews


class DiffNotFoundError(NotFoundError):
    pass


class AuditItemNotFoundError(NotFoundError):
    pass


class InvalidReviewStateError(ConflictError):
    pass


class CompareReviewService:
    def __init__(self, repository: TaskRepository = default_task_repository) -> None:
        self.repository = repository

    def update_diff_review(
        self,
        task: CompareTask,
        diff_id: str,
        review_status: ReviewStatus,
        review_comment: str = "",
        reviewed_by: str = "",
    ) -> tuple[CompareTask, DiffItem]:
        updated_diff: DiffItem | None = None

        def mutate(persisted: CompareTask) -> None:
            nonlocal updated_diff
            if persisted.status != "COMPLETED":
                raise InvalidReviewStateError("任务尚未完成，不能提交复核结果。")

            for diff in persisted.diffs:
                if diff.diff_id != diff_id:
                    continue
                child_ids = {item.item_id for item in build_audit_items(persisted.diffs) if item.diff_id == diff_id}
                normalized = self._normalized_reviews(persisted)
                review = self._review(
                    review_status,
                    review_comment,
                    reviewed_by,
                )
                for item_id in child_ids:
                    self._set_review(normalized, item_id, review)
                persisted.audit_item_reviews = normalized
                persisted.audit_item_reviews_normalized = True
                self._project_diff_reviews(persisted)
                self.refresh_review_stats(persisted)
                persisted.report_revision += 1
                updated_diff = next(item for item in persisted.diffs if item.diff_id == diff_id)
                return

            raise DiffNotFoundError(f"差异不存在: {diff_id}")

        updated_task = self.repository.update_compare_task(task.task_id, mutate)
        assert updated_diff is not None
        return updated_task, updated_diff

    def update_audit_item_review(
        self,
        task: CompareTask,
        audit_item_id: str,
        review_status: ReviewStatus,
        review_comment: str = "",
        reviewed_by: str = "",
    ) -> tuple[CompareTask, AuditItem]:
        updated_item: AuditItem | None = None

        def mutate(persisted: CompareTask) -> None:
            nonlocal updated_item
            if persisted.status != "COMPLETED":
                raise InvalidReviewStateError("任务尚未完成，不能提交复核结果。")
            generated_items = build_audit_items(persisted.diffs)
            if audit_item_id not in {item.item_id for item in generated_items}:
                raise AuditItemNotFoundError(f"审计点不存在: {audit_item_id}")
            normalized = self._normalized_reviews(persisted)
            self._set_review(
                normalized,
                audit_item_id,
                self._review(review_status, review_comment, reviewed_by),
            )
            persisted.audit_item_reviews = normalized
            persisted.audit_item_reviews_normalized = True
            self._project_diff_reviews(persisted)
            self.refresh_review_stats(persisted)
            persisted.report_revision += 1
            updated_item = next(
                item
                for item in build_audit_items(
                    persisted.diffs,
                    persisted.audit_item_reviews,
                    broadcast_legacy=False,
                )
                if item.item_id == audit_item_id
            )

        updated_task = self.repository.update_compare_task(task.task_id, mutate)
        assert updated_item is not None
        return updated_task, updated_item

    def refresh_review_stats(self, task: CompareTask) -> CompareTask:
        items = build_audit_items(
            task.diffs,
            task.audit_item_reviews,
            broadcast_legacy=not task.audit_item_reviews_normalized,
        )
        counts = Counter(item.review_status for item in items)
        task.reviewed_count = len([item for item in items if item.review_status != "UNREVIEWED"])
        task.confirmed_count = counts["CONFIRMED"]
        task.false_positive_count = counts["FALSE_POSITIVE"]
        task.manual_review_count = counts["NEEDS_REVIEW"]
        task.ignored_count = counts["IGNORED"]
        return task

    def _normalized_reviews(self, task: CompareTask) -> dict[str, AuditItemReview]:
        return normalized_audit_item_reviews(
            task.diffs,
            task.audit_item_reviews,
            broadcast_legacy=not task.audit_item_reviews_normalized,
        )

    @staticmethod
    def _review(
        review_status: ReviewStatus,
        review_comment: str,
        reviewed_by: str,
    ) -> AuditItemReview:
        return AuditItemReview(
            review_status=review_status,
            review_comment=review_comment.strip(),
            reviewed_by=reviewed_by.strip(),
            reviewed_at=datetime.now(UTC).isoformat(),
        )

    @staticmethod
    def _set_review(
        reviews: dict[str, AuditItemReview],
        item_id: str,
        review: AuditItemReview,
    ) -> None:
        if review.review_status == "UNREVIEWED":
            reviews.pop(item_id, None)
            return
        reviews[item_id] = review

    @staticmethod
    def _project_diff_reviews(task: CompareTask) -> None:
        item_ids_by_diff: dict[str, list[str]] = {}
        for item in build_audit_items(task.diffs):
            item_ids_by_diff.setdefault(item.diff_id, []).append(item.item_id)
        for index, diff in enumerate(task.diffs):
            child_reviews = [
                task.audit_item_reviews.get(item_id, AuditItemReview())
                for item_id in item_ids_by_diff.get(diff.diff_id, [])
            ]
            statuses = {review.review_status for review in child_reviews}
            projected_status: ReviewStatus
            if not child_reviews or statuses == {"UNREVIEWED"}:
                projected_status = "UNREVIEWED"
            elif len(statuses) == 1:
                projected_status = child_reviews[0].review_status
            else:
                projected_status = "NEEDS_REVIEW"
            identical_details = (
                len(
                    {
                        (
                            review.review_status,
                            review.review_comment,
                            review.reviewed_by,
                            review.reviewed_at,
                        )
                        for review in child_reviews
                    }
                )
                == 1
            )
            projected = child_reviews[0] if identical_details and child_reviews else AuditItemReview()
            task.diffs[index] = diff.model_copy(
                update={
                    "review_status": projected_status,
                    "review_comment": projected.review_comment if projected_status != "UNREVIEWED" else "",
                    "reviewed_by": projected.reviewed_by if projected_status != "UNREVIEWED" else "",
                    "reviewed_at": projected.reviewed_at if projected_status != "UNREVIEWED" else "",
                }
            )


class CompareQualityService:
    low_confidence_threshold = 0.6
    low_match_threshold = 70.0

    def build_summary(self, task: CompareTask) -> dict[str, Any]:
        review_service = CompareReviewService()
        review_service.refresh_review_stats(task)
        audit_items = build_audit_items(
            task.diffs,
            task.audit_item_reviews,
            broadcast_legacy=not task.audit_item_reviews_normalized,
        )
        source_counts = Counter(diff.source_type or "clause" for diff in task.diffs)
        review_flag_counts = Counter(flag for diff in task.diffs for flag in diff.review_flags)
        evidence_counts = Counter()
        low_confidence_diffs: list[dict[str, Any]] = []
        low_similarity_diffs: list[dict[str, Any]] = []

        for diff in task.diffs:
            evidences = [*diff.original_evidence, *diff.compare_evidence]
            for evidence in evidences:
                evidence_counts[evidence.evidence_quality] += 1
            if self._is_low_confidence(diff, evidences):
                low_confidence_diffs.append(self._diff_quality_item(diff))
            if self._is_low_similarity(diff):
                low_similarity_diffs.append(self._diff_quality_item(diff))

        return {
            "task_id": task.task_id,
            "status": task.status,
            "diff_count": len(task.diffs),
            "review_stats": {
                "total_count": len(audit_items),
                "reviewed_count": task.reviewed_count,
                "confirmed_count": task.confirmed_count,
                "false_positive_count": task.false_positive_count,
                "manual_review_count": task.manual_review_count,
                "ignored_count": task.ignored_count,
                "review_unit": "audit_item",
            },
            "source_counts": dict(source_counts),
            "needs_review_count": len([diff for diff in task.diffs if diff.quality_status == "NEEDS_REVIEW"]),
            "review_flag_counts": dict(review_flag_counts),
            "cross_source_merged_count": review_flag_counts["CROSS_SOURCE_MERGED"],
            "evidence_quality_counts": {
                "HIGH": evidence_counts["HIGH"],
                "MEDIUM": evidence_counts["MEDIUM"],
                "LOW": evidence_counts["LOW"],
            },
            "document_profile_summary": self._document_profile_summary(task),
            "parse_warning_details": [to_jsonable(item) for item in task.parse_warning_details],
            "ocr_quality_summary": to_jsonable(task.ocr_quality_summary) if task.ocr_quality_summary else None,
            "ocr_risk_page_count": task.ocr_quality_summary.risk_page_count if task.ocr_quality_summary else 0,
            "ocr_affected_diff_count": task.ocr_quality_summary.affected_diff_count if task.ocr_quality_summary else 0,
            "ocr_remediation_summary": (
                to_jsonable(task.ocr_remediation_summary) if task.ocr_remediation_summary else None
            ),
            "ocr_remediation_action_count": (
                task.ocr_remediation_summary.attempted_action_count if task.ocr_remediation_summary else 0
            ),
            "ocr_remediation_unresolved_count": (
                task.ocr_remediation_summary.unresolved_action_count if task.ocr_remediation_summary else 0
            ),
            "manual_review_required_count": (
                task.ocr_remediation_summary.manual_review_required_count if task.ocr_remediation_summary else 0
            ),
            "low_confidence_diffs": low_confidence_diffs,
            "low_similarity_diffs": low_similarity_diffs,
            "debug_artifacts": self._debug_artifacts(task),
        }

    def _is_low_confidence(self, diff: DiffItem, evidences: list[EvidenceBox]) -> bool:
        has_low_confidence_flag = any("LOW_CONFIDENCE" in flag for flag in diff.review_flags)
        if not evidences:
            return True
        has_low_confidence_evidence = any(
            evidence.evidence_quality == "LOW" or evidence.confidence < self.low_confidence_threshold
            for evidence in evidences
        )
        return has_low_confidence_flag or has_low_confidence_evidence

    def _is_low_similarity(self, diff: DiffItem) -> bool:
        if "SAME_CLAUSE_NO_LOW_SIMILARITY" in diff.review_flags or "LOW_CONFIDENCE_MATCH" in diff.review_flags:
            return True
        return diff.match_score is not None and diff.match_score < self.low_match_threshold

    def _diff_quality_item(self, diff: DiffItem) -> dict[str, Any]:
        return {
            "diff_id": diff.diff_id,
            "title": diff.title,
            "diff_type": diff.diff_type,
            "source_type": diff.source_type,
            "match_score": diff.match_score,
            "match_method": diff.match_method,
            "review_flags": diff.review_flags,
            "quality_status": diff.quality_status,
            "text_confidence": diff.text_confidence,
            "merged_sources": diff.merged_sources,
            "review_status": diff.review_status,
        }

    def _document_profile_summary(self, task: CompareTask) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for side, profile in task.document_profiles.items():
            result[side] = {
                "filename": profile.filename,
                "page_count": profile.page_count,
                "extractor_used": profile.extractor_used,
                "recommended_strategy": profile.recommended_strategy,
                "total_text_chars": profile.total_text_chars,
                "scanned_page_count": profile.scanned_page_count,
                "table_heavy_page_count": profile.table_heavy_page_count,
            }
        return result

    def _debug_artifacts(self, task: CompareTask) -> dict[str, str]:
        return {name: Path(path).name for name, path in task.debug_artifact_paths.items()}
