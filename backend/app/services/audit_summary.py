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
    return [_audit_item(diff, _audit_diff_type(diff), _diff_summary(diff)) for diff in diffs]


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


def _audit_item(diff: DiffItem, diff_type: DiffType, summary: str) -> AuditItem:
    return AuditItem(
        item_id=f"{diff.diff_id}:{diff_type}",
        diff=diff,
        diff_type=diff_type,
        title=diff.title or diff.clause_no or diff.diff_id,
        summary=summary or _diff_summary(diff),
    )


def _audit_diff_type(diff: DiffItem) -> DiffType:
    if diff.diff_type != "MODIFY":
        return diff.diff_type

    evidence_types = _evidence_types([*diff.original_evidence, *diff.compare_evidence])
    if evidence_types == {"ADD"}:
        return "ADD"
    if evidence_types == {"DELETE"}:
        return "DELETE"
    return "MODIFY"


def _evidence_types(evidence_list: list[EvidenceBox]) -> set[DiffType]:
    return {evidence.highlight_type for evidence in evidence_list if evidence.highlight_type is not None}


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
