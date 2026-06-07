from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.models import DiffItem, EvidenceBox


@dataclass
class DiffQualityDecision:
    action: str
    diff_id: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiffQualityResult:
    diffs: list[DiffItem]
    decisions: list[DiffQualityDecision] = field(default_factory=list)

    def to_debug_payload(self) -> list[dict[str, Any]]:
        return [
            {"action": item.action, "diff_id": item.diff_id, "detail": item.detail}
            for item in self.decisions
        ]


class DiffQualityProcessor:
    mergeable_sources = {"metadata", "table", "header_footer"}
    source_priority = {"metadata": 0, "table": 1, "header_footer": 2}
    critical_pattern = re.compile(
        r"(\d|%|‰|元|万元|v\d|V\d|公司|甲方|乙方|不得|不承担|违约|免责|终止|不可抗力)"
    )
    style_punct_pattern = re.compile(r"[\s，。；：、”“‘’（）()\[\]【】《》!?:;\"']+")

    def process(self, diffs: list[DiffItem]) -> DiffQualityResult:
        working = [diff.model_copy(deep=True) for diff in diffs]
        decisions: list[DiffQualityDecision] = []
        working = self._dedupe_cross_source(working, decisions)
        self._classify(working, decisions)
        self._flag_boundary_drift(working, decisions)
        self._propagate_text_confidence(working)
        return DiffQualityResult(diffs=working, decisions=decisions)

    def _dedupe_cross_source(
        self,
        diffs: list[DiffItem],
        decisions: list[DiffQualityDecision],
    ) -> list[DiffItem]:
        groups: dict[tuple[str, str, str], list[DiffItem]] = {}
        for diff in diffs:
            if diff.source_type not in self.mergeable_sources:
                continue
            key = (
                diff.diff_type,
                self._dedupe_key(diff.original_text or diff.original_snippet),
                self._dedupe_key(diff.compare_text or diff.compare_snippet),
            )
            if not key[1] and not key[2]:
                continue
            groups.setdefault(key, []).append(diff)

        remove_ids: set[str] = set()
        by_id = {diff.diff_id: diff for diff in diffs}
        for group in groups.values():
            if len(group) < 2:
                continue
            winner = sorted(
                group,
                key=lambda item: (
                    self.source_priority.get(item.source_type, 99),
                    item.diff_id,
                ),
            )[0]
            merged_sources = set(winner.merged_sources)
            for duplicate in group:
                if duplicate.diff_id == winner.diff_id:
                    continue
                remove_ids.add(duplicate.diff_id)
                merged_sources.add(duplicate.source_type)
                winner.original_evidence = self._merge_evidence(winner.original_evidence, duplicate.original_evidence)
                winner.compare_evidence = self._merge_evidence(winner.compare_evidence, duplicate.compare_evidence)
                decisions.append(
                    DiffQualityDecision(
                        action="cross_source_merged",
                        diff_id=winner.diff_id,
                        detail={"merged_diff_id": duplicate.diff_id, "source_type": duplicate.source_type},
                    )
                )
            winner.merged_sources = sorted(merged_sources)
            self._add_flag(winner, "CROSS_SOURCE_MERGED")

        return [by_id[diff.diff_id] for diff in diffs if diff.diff_id not in remove_ids]

    def _classify(self, diffs: list[DiffItem], decisions: list[DiffQualityDecision]) -> None:
        for diff in diffs:
            if self._is_critical_change(diff):
                self._add_flag(diff, "CRITICAL_VALUE_CHANGE")
                decisions.append(DiffQualityDecision(action="critical_change", diff_id=diff.diff_id))
                continue
            if self._looks_like_minor_ocr_noise(diff):
                self._add_flag(diff, "POSSIBLE_OCR_NOISE")
                diff.quality_status = "NEEDS_REVIEW"
                decisions.append(DiffQualityDecision(action="possible_ocr_noise", diff_id=diff.diff_id))
            elif self._looks_like_cover_fragment(diff):
                self._add_flag(diff, "POSSIBLE_COVER_OCR_FRAGMENT")
                diff.quality_status = "NEEDS_REVIEW"
                decisions.append(DiffQualityDecision(action="possible_cover_ocr_fragment", diff_id=diff.diff_id))

    def _flag_boundary_drift(self, diffs: list[DiffItem], decisions: list[DiffQualityDecision]) -> None:
        clause_diffs = [diff for diff in diffs if diff.source_type == "clause"]
        deletes = [diff for diff in clause_diffs if self._compact(diff.original_snippet) and not self._compact(diff.compare_snippet)]
        adds = [diff for diff in clause_diffs if self._compact(diff.compare_snippet) and not self._compact(diff.original_snippet)]
        for delete in deletes:
            delete_text = self._compact(delete.original_snippet)
            if not 2 <= len(delete_text) <= 40:
                continue
            for add in adds:
                if delete.diff_id == add.diff_id:
                    continue
                if delete_text != self._compact(add.compare_snippet):
                    continue
                self._add_flag(delete, "POSSIBLE_BOUNDARY_DRIFT")
                self._add_flag(add, "POSSIBLE_BOUNDARY_DRIFT")
                delete.quality_status = "NEEDS_REVIEW"
                add.quality_status = "NEEDS_REVIEW"
                decisions.append(
                    DiffQualityDecision(
                        action="possible_boundary_drift",
                        diff_id=delete.diff_id,
                        detail={"paired_diff_id": add.diff_id, "fragment": delete_text[:80]},
                    )
                )

    def _propagate_text_confidence(self, diffs: list[DiffItem]) -> None:
        for diff in diffs:
            confidences = [
                evidence.text_confidence
                for evidence in [*diff.original_evidence, *diff.compare_evidence]
                if evidence.text_confidence is not None
            ]
            if confidences:
                diff.text_confidence = min(confidences)

    def _looks_like_minor_ocr_noise(self, diff: DiffItem) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        if (diff.match_score or 0) < 96:
            return False
        changed_len = len(self._compact(diff.original_snippet)) + len(self._compact(diff.compare_snippet))
        return 0 < changed_len <= 3

    def _looks_like_cover_fragment(self, diff: DiffItem) -> bool:
        if diff.source_type != "metadata" or diff.diff_type not in {"ADD", "DELETE"}:
            return False
        return len(self._compact(diff.original_snippet or diff.compare_snippet)) <= 3

    def _is_critical_change(self, diff: DiffItem) -> bool:
        changed = self._changed_text(diff)
        return bool(self.critical_pattern.search(changed or ""))

    def _changed_text(self, diff: DiffItem) -> str:
        if diff.diff_type == "MODIFY":
            return f"{diff.original_snippet} {diff.compare_snippet}"
        if diff.diff_type == "ADD":
            return diff.compare_snippet or diff.compare_text
        if diff.diff_type == "DELETE":
            return diff.original_snippet or diff.original_text
        return f"{diff.original_snippet} {diff.compare_snippet}"

    def _add_flag(self, diff: DiffItem, flag: str) -> None:
        if flag not in diff.review_flags:
            diff.review_flags.append(flag)

    def _dedupe_key(self, text: str) -> str:
        return self._compact(text)

    def _compact(self, text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text or "")
        return self.style_punct_pattern.sub("", normalized).lower()

    def _merge_evidence(self, left: list[EvidenceBox], right: list[EvidenceBox]) -> list[EvidenceBox]:
        result = list(left)
        seen = {(item.page_no, item.bbox.x0, item.bbox.y0, item.bbox.x1, item.bbox.y1, item.text) for item in result}
        for item in right:
            key = (item.page_no, item.bbox.x0, item.bbox.y0, item.bbox.x1, item.bbox.y1, item.text)
            if key in seen:
                continue
            result.append(item)
            seen.add(key)
        return result
