from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.errors import ConflictError, NotFoundError
from app.infrastructure.task_repository import TaskRepository, default_task_repository, to_jsonable
from app.models import AuditItemReview, CompareTask, DiffItem, EvidenceBox, ReviewStatus
from app.services.audit_summary import build_audit_items


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

            for index, diff in enumerate(persisted.diffs):
                if diff.diff_id != diff_id:
                    continue
                updated = diff.model_copy(
                    update={
                        "review_status": review_status,
                        "review_comment": review_comment.strip(),
                        "reviewed_by": reviewed_by.strip(),
                        "reviewed_at": datetime.now(UTC).isoformat(),
                    }
                )
                persisted.diffs[index] = updated
                self.refresh_review_stats(persisted)
                updated_diff = updated
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
    ) -> tuple[CompareTask, AuditItemReview]:
        updated_review: AuditItemReview | None = None

        def mutate(persisted: CompareTask) -> None:
            nonlocal updated_review
            if persisted.status != "COMPLETED":
                raise InvalidReviewStateError("任务尚未完成，不能提交复核结果。")
            valid_item_ids = {item.item_id for item in build_audit_items(persisted.diffs)}
            if audit_item_id not in valid_item_ids:
                raise AuditItemNotFoundError(f"审计点不存在: {audit_item_id}")
            updated_review = AuditItemReview(
                review_status=review_status,
                review_comment=review_comment.strip(),
                reviewed_by=reviewed_by.strip(),
                reviewed_at=datetime.now(UTC).isoformat(),
            )
            persisted.audit_item_reviews[audit_item_id] = updated_review
            self.refresh_review_stats(persisted)

        updated_task = self.repository.update_compare_task(task.task_id, mutate)
        assert updated_review is not None
        return updated_task, updated_review

    def refresh_review_stats(self, task: CompareTask) -> CompareTask:
        if task.audit_item_reviews:
            counts = Counter(review.review_status for review in task.audit_item_reviews.values())
            task.reviewed_count = len(
                [
                    review
                    for review in task.audit_item_reviews.values()
                    if review.review_status != "UNREVIEWED"
                ]
            )
        else:
            counts = Counter(diff.review_status for diff in task.diffs)
            task.reviewed_count = len([diff for diff in task.diffs if diff.review_status != "UNREVIEWED"])
        task.confirmed_count = counts["CONFIRMED"]
        task.false_positive_count = counts["FALSE_POSITIVE"]
        task.manual_review_count = counts["NEEDS_REVIEW"]
        task.ignored_count = counts["IGNORED"]
        return task


class CompareQualityService:
    low_confidence_threshold = 0.6
    low_match_threshold = 70.0

    def build_summary(self, task: CompareTask) -> dict[str, Any]:
        review_service = CompareReviewService()
        review_service.refresh_review_stats(task)
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
                "reviewed_count": task.reviewed_count,
                "confirmed_count": task.confirmed_count,
                "false_positive_count": task.false_positive_count,
                "manual_review_count": task.manual_review_count,
                "ignored_count": task.ignored_count,
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
