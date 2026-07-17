from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from collections.abc import Iterable
from typing import Literal, TypeVar

from app.models import AuditItemReview, CompareTask, DiffItem, DiffType, EvidenceBox, ReviewStatus

_T = TypeVar("_T")


@dataclass(frozen=True)
class AuditItemOcrContext:
    affected: bool = False
    statuses: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    sides: tuple[str, ...] = ()
    page_numbers: tuple[int, ...] = ()


@dataclass(frozen=True)
class AuditItemRemediationContext:
    action_ids: tuple[str, ...] = ()
    action_types: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    changed_evidence: bool = False
    changed_diff_text: bool = False
    requires_manual_review: bool = False


@dataclass(frozen=True)
class AuditItem:
    item_id: str
    diff: DiffItem
    diff_type: DiffType
    diff_id: str
    source_type: str
    section_type: str
    section_path: list[str]
    title: str
    summary: str
    original_text: str
    compare_text: str
    original_evidence: list[EvidenceBox]
    compare_evidence: list[EvidenceBox]
    evidence_state: Literal["LOCATED", "UNLOCATED"]
    quality_status: str
    structural_flags: list[str]
    review_flags: list[str]
    text_confidence: float | None
    match_confidence: str
    ocr_context: AuditItemOcrContext = AuditItemOcrContext()
    remediation_context: AuditItemRemediationContext = AuditItemRemediationContext()
    review_status: ReviewStatus = "UNREVIEWED"
    review_comment: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""


@dataclass(frozen=True)
class AuditStats:
    total: int
    add: int
    delete: int
    modify: int
    raw_diff_count: int


def build_audit_items(
    diffs: list[DiffItem],
    reviews: dict[str, AuditItemReview] | None = None,
    *,
    broadcast_legacy: bool = True,
) -> list[AuditItem]:
    items: list[AuditItem] = []
    for diff in diffs:
        items.extend(_audit_items_for_diff(diff))
    normalized_reviews = normalized_audit_item_reviews(
        diffs,
        reviews or {},
        items=items,
        broadcast_legacy=broadcast_legacy,
    )
    if normalized_reviews:
        items = [_apply_review(item, normalized_reviews.get(item.item_id)) for item in items]
    return items


def build_task_audit_items(task: CompareTask) -> list[AuditItem]:
    items = build_audit_items(
        task.diffs,
        task.audit_item_reviews,
        broadcast_legacy=not task.audit_item_reviews_normalized,
    )
    return [_apply_task_context(item, task) for item in items]


def project_diff_reviews(diffs: list[DiffItem], items: list[AuditItem]) -> list[DiffItem]:
    items_by_diff: dict[str, list[AuditItem]] = {}
    for item in items:
        items_by_diff.setdefault(item.diff_id, []).append(item)
    return [_project_diff_review(diff, items_by_diff.get(diff.diff_id, [])) for diff in diffs]


def normalized_audit_item_reviews(
    diffs: list[DiffItem],
    reviews: dict[str, AuditItemReview],
    *,
    items: list[AuditItem] | None = None,
    broadcast_legacy: bool = True,
) -> dict[str, AuditItemReview]:
    generated_items = items if items is not None else [item for diff in diffs for item in _audit_items_for_diff(diff)]
    valid_ids = {item.item_id for item in generated_items}
    normalized = {
        item_id: review.model_copy(deep=True)
        for item_id, review in reviews.items()
        if item_id in valid_ids and review.review_status != "UNREVIEWED"
    }
    items_by_diff: dict[str, list[AuditItem]] = {}
    for item in generated_items:
        items_by_diff.setdefault(item.diff_id, []).append(item)
    if not broadcast_legacy:
        return normalized
    for diff in diffs:
        if diff.review_status == "UNREVIEWED":
            continue
        legacy_review = AuditItemReview(
            review_status=diff.review_status,
            review_comment=diff.review_comment,
            reviewed_by=diff.reviewed_by,
            reviewed_at=diff.reviewed_at,
        )
        for item in items_by_diff.get(diff.diff_id, []):
            normalized.setdefault(item.item_id, legacy_review.model_copy(deep=True))
    return normalized


def build_audit_stats(diffs: list[DiffItem]) -> AuditStats:
    items = build_audit_items(diffs)
    counter = Counter(item.diff_type for item in items)
    return AuditStats(
        total=len(items),
        add=counter["ADD"],
        delete=counter["DELETE"],
        modify=counter["MODIFY"],
        raw_diff_count=len(diffs),
    )


def audit_stats_summary(stats: AuditStats) -> str:
    return (
        f"本次共识别 {stats.total} 个审计点，其中新增 {stats.add} 个、"
        f"删除 {stats.delete} 个、修改 {stats.modify} 个；"
        f"对应原始差异记录 {stats.raw_diff_count} 条。"
    )


def _audit_items_for_diff(diff: DiffItem) -> list[AuditItem]:
    original_evidence = diff.original_evidence or []
    compare_evidence = diff.compare_evidence or []
    has_typed_evidence = any(
        evidence.highlight_type is not None for evidence in [*original_evidence, *compare_evidence]
    )
    if not has_typed_evidence:
        return [
            _audit_item(
                diff,
                diff.diff_type,
                _diff_summary(diff),
                original_evidence,
                compare_evidence,
            )
        ]

    items: list[AuditItem] = []
    add_evidence = _typed_evidence(compare_evidence, "ADD")
    delete_evidence = _typed_evidence(original_evidence, "DELETE")
    original_modify_evidence = _typed_evidence(original_evidence, "MODIFY")
    compare_modify_evidence = _typed_evidence(compare_evidence, "MODIFY")

    if add_evidence:
        items.append(_audit_item(diff, "ADD", _evidence_text(add_evidence), [], add_evidence))
    if delete_evidence:
        items.append(_audit_item(diff, "DELETE", _evidence_text(delete_evidence), delete_evidence, []))
    if original_modify_evidence or compare_modify_evidence:
        summary = _modify_summary(
            _evidence_text(original_modify_evidence),
            _evidence_text(compare_modify_evidence),
        )
        items.append(_audit_item(diff, "MODIFY", summary, original_modify_evidence, compare_modify_evidence))
    return items or [_audit_item(diff, diff.diff_type, _diff_summary(diff), original_evidence, compare_evidence)]


def _audit_item(
    diff: DiffItem,
    diff_type: DiffType,
    summary: str,
    original_evidence: list[EvidenceBox],
    compare_evidence: list[EvidenceBox],
) -> AuditItem:
    evidence_state = (
        "LOCATED" if any(_is_located(evidence) for evidence in [*original_evidence, *compare_evidence]) else "UNLOCATED"
    )
    review_flags = list(dict.fromkeys(diff.review_flags))
    quality_status = diff.quality_status
    if evidence_state == "UNLOCATED":
        quality_status = "NEEDS_REVIEW"
        if "EVIDENCE_UNLOCATED" not in review_flags:
            review_flags.append("EVIDENCE_UNLOCATED")
    return AuditItem(
        item_id=f"{diff.diff_id}:{diff_type}",
        diff=diff,
        diff_type=diff_type,
        diff_id=diff.diff_id,
        source_type=diff.source_type,
        section_type=diff.section_type,
        section_path=list(diff.section_path),
        title=diff.title or diff.clause_no or diff.diff_id,
        summary=summary or _diff_summary(diff),
        original_text=diff.original_text or diff.original_snippet,
        compare_text=diff.compare_text or diff.compare_snippet,
        original_evidence=original_evidence,
        compare_evidence=compare_evidence,
        evidence_state=evidence_state,
        quality_status=quality_status,
        structural_flags=list(diff.structural_flags),
        review_flags=review_flags,
        text_confidence=diff.text_confidence,
        match_confidence=diff.match_confidence,
    )


def _apply_review(item: AuditItem, review: AuditItemReview | None) -> AuditItem:
    if review is None:
        return item
    return replace(
        item,
        review_status=review.review_status,
        review_comment=review.review_comment,
        reviewed_by=review.reviewed_by,
        reviewed_at=review.reviewed_at,
    )


def _apply_task_context(item: AuditItem, task: CompareTask) -> AuditItem:
    profiles = sorted(
        [
            profile
            for profile in (task.ocr_quality_summary.profiles if task.ocr_quality_summary else [])
            if item.diff_id in profile.affected_diff_ids
        ],
        key=lambda profile: (profile.side, profile.page_no, profile.status),
    )
    actions = sorted(
        [
            action
            for action in (task.ocr_remediation_summary.actions if task.ocr_remediation_summary else [])
            if action.diff_id == item.diff_id
        ],
        key=lambda action: action.action_id,
    )
    return replace(
        item,
        ocr_context=AuditItemOcrContext(
            affected=bool(profiles),
            statuses=_ordered_unique(profile.status for profile in profiles),
            reasons=_ordered_unique(reason for profile in profiles for reason in profile.reasons),
            sides=_ordered_unique(profile.side for profile in profiles),
            page_numbers=tuple(sorted({profile.page_no for profile in profiles})),
        ),
        remediation_context=AuditItemRemediationContext(
            action_ids=tuple(action.action_id for action in actions),
            action_types=_ordered_unique(action.action_type for action in actions),
            statuses=_ordered_unique(action.status for action in actions),
            changed_evidence=any(action.changed_evidence for action in actions),
            changed_diff_text=any(action.changed_diff_text for action in actions),
            requires_manual_review=any(action.status == "MANUAL_REVIEW_REQUIRED" for action in actions),
        ),
    )


def _project_diff_review(diff: DiffItem, items: list[AuditItem]) -> DiffItem:
    statuses = {item.review_status for item in items}
    projected_status: ReviewStatus
    if not items or statuses == {"UNREVIEWED"}:
        projected_status = "UNREVIEWED"
    elif len(statuses) == 1:
        projected_status = items[0].review_status
    else:
        projected_status = "NEEDS_REVIEW"
    details = {(item.review_status, item.review_comment, item.reviewed_by, item.reviewed_at) for item in items}
    identical_details = len(details) == 1
    source = items[0] if identical_details and items else None
    return diff.model_copy(
        update={
            "review_status": projected_status,
            "review_comment": source.review_comment if source and projected_status != "UNREVIEWED" else "",
            "reviewed_by": source.reviewed_by if source and projected_status != "UNREVIEWED" else "",
            "reviewed_at": source.reviewed_at if source and projected_status != "UNREVIEWED" else "",
        },
        deep=True,
    )


def _ordered_unique(values: Iterable[_T]) -> tuple[_T, ...]:
    return tuple(dict.fromkeys(values))


def _is_located(evidence: EvidenceBox) -> bool:
    return evidence.page_no > 0 and evidence.bbox.x1 > evidence.bbox.x0 and evidence.bbox.y1 > evidence.bbox.y0


def _typed_evidence(evidence_list: list[EvidenceBox], diff_type: DiffType) -> list[EvidenceBox]:
    return [evidence for evidence in evidence_list if evidence.highlight_type == diff_type]


def _evidence_text(evidence_list: list[EvidenceBox]) -> str:
    return _compact_text(" ".join(evidence.text for evidence in evidence_list if evidence.text.strip()), limit=92)


def _modify_summary(original_text: str, compare_text: str) -> str:
    if original_text and compare_text:
        return f"原文：{original_text} 修改后：{compare_text}"
    return original_text or compare_text


def _diff_summary(diff: DiffItem) -> str:
    return _compact_text(diff.readable_change or diff.compare_snippet or diff.original_snippet or "暂无摘要")


def _compact_text(value: str, limit: int = 180) -> str:
    text = " ".join(value.split())
    return f"{text[:limit]}..." if len(text) > limit else text
