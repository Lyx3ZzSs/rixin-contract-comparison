from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.models import DiffItem, DiffType, EvidenceBox


@dataclass(frozen=True)
class AuditItem:
    item_id: str
    diff: DiffItem
    diff_type: DiffType
    title: str
    summary: str


@dataclass(frozen=True)
class AuditStats:
    total: int
    add: int
    delete: int
    modify: int
    raw_diff_count: int


def build_audit_items(diffs: list[DiffItem]) -> list[AuditItem]:
    items: list[AuditItem] = []
    for diff in diffs:
        original_evidence = diff.original_evidence or []
        compare_evidence = diff.compare_evidence or []
        has_typed_evidence = any(evidence.highlight_type for evidence in [*original_evidence, *compare_evidence])
        if not has_typed_evidence:
            items.append(_audit_item(diff, diff.diff_type, _diff_summary(diff)))
            continue

        add_summary = _evidence_text(compare_evidence, "ADD")
        delete_summary = _evidence_text(original_evidence, "DELETE")
        original_modify = _evidence_text(original_evidence, "MODIFY")
        compare_modify = _evidence_text(compare_evidence, "MODIFY")
        if _has_evidence(compare_evidence, "ADD"):
            items.append(_audit_item(diff, "ADD", add_summary or _diff_summary(diff)))
        if _has_evidence(original_evidence, "DELETE"):
            items.append(_audit_item(diff, "DELETE", delete_summary or _diff_summary(diff)))
        if _has_evidence(original_evidence, "MODIFY") or _has_evidence(compare_evidence, "MODIFY"):
            summary = _modify_summary(original_modify, compare_modify) or _diff_summary(diff)
            items.append(_audit_item(diff, "MODIFY", summary))
    return items


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
    return f"本次共识别 {stats.total} 个审计改动点，其中新增 {stats.add} 个、删除 {stats.delete} 个、修改 {stats.modify} 个。"


def _audit_item(diff: DiffItem, diff_type: DiffType, summary: str) -> AuditItem:
    return AuditItem(
        item_id=f"{diff.diff_id}:{diff_type}",
        diff=diff,
        diff_type=diff_type,
        title=diff.title or diff.clause_no or diff.diff_id,
        summary=summary,
    )


def _has_evidence(evidence_list: list[EvidenceBox], diff_type: DiffType) -> bool:
    return any(evidence.highlight_type == diff_type for evidence in evidence_list)


def _evidence_text(evidence_list: list[EvidenceBox], diff_type: DiffType) -> str:
    return _compact_text(" ".join(evidence.text for evidence in evidence_list if evidence.highlight_type == diff_type))


def _modify_summary(original_text: str, compare_text: str) -> str:
    if original_text and compare_text:
        return f"原文：{original_text}；修改后：{compare_text}"
    return original_text or compare_text


def _diff_summary(diff: DiffItem) -> str:
    analysis = diff.ai_analysis
    return _compact_text(
        (analysis.change_summary if analysis else "")
        or diff.readable_change
        or diff.compare_snippet
        or diff.original_snippet
        or "暂无摘要"
    )


def _compact_text(value: str, limit: int = 180) -> str:
    text = " ".join(value.split())
    return f"{text[:limit]}..." if len(text) > limit else text
