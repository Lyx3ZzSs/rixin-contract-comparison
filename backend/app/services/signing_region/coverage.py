from __future__ import annotations

from dataclasses import dataclass, field

from app.models import BBox, DiffItem, EvidenceBox
from app.services.signing_region.models import SigningCoverageEntry


LEGACY_SOURCES = {"seal", "table", "metadata", "header_footer"}
SIGNING_TEXT_MARKERS = ("签", "章", "甲方", "乙方", "法定代表", "授权代表", "日期")


@dataclass
class SigningCoverageResult:
    entries: list[SigningCoverageEntry] = field(default_factory=list)
    covered_diff_ids: set[str] = field(default_factory=set)


class SigningRegionCoverageBuilder:
    def build(self, signing_region_diffs: list[DiffItem], legacy_diffs: list[DiffItem]) -> SigningCoverageResult:
        result = SigningCoverageResult()
        for signing_diff in signing_region_diffs:
            covered: list[str] = []
            reasons: dict[str, str] = {}
            signing_evidence = [*signing_diff.original_evidence, *signing_diff.compare_evidence]
            for legacy in legacy_diffs:
                if legacy.source_type not in LEGACY_SOURCES:
                    continue
                if not self._looks_signing_related(legacy):
                    continue
                legacy_evidence = [*legacy.original_evidence, *legacy.compare_evidence]
                if self._any_overlap(signing_evidence, legacy_evidence):
                    covered.append(legacy.diff_id)
                    reasons[legacy.diff_id] = "overlaps_confirmed_signing_region"
                    result.covered_diff_ids.add(legacy.diff_id)
            if covered:
                result.entries.append(
                    SigningCoverageEntry(
                        signing_region_diff_id=signing_diff.diff_id,
                        covered_diff_ids=covered,
                        reasons=reasons,
                    )
                )
        return result

    @staticmethod
    def _looks_signing_related(diff: DiffItem) -> bool:
        if diff.source_type == "seal":
            return True
        text = f"{diff.title} {diff.original_text} {diff.compare_text} {' '.join(diff.review_flags)}"
        return any(marker in text for marker in SIGNING_TEXT_MARKERS)

    @staticmethod
    def _any_overlap(left: list[EvidenceBox], right: list[EvidenceBox]) -> bool:
        return any(
            a.page_no == b.page_no and SigningRegionCoverageBuilder._overlap_ratio(a.bbox, b.bbox) >= 0.2
            for a in left
            for b in right
        )

    @staticmethod
    def _overlap_ratio(a: BBox, b: BBox) -> float:
        x0 = max(a.x0, b.x0)
        y0 = max(a.y0, b.y0)
        x1 = min(a.x1, b.x1)
        y1 = min(a.y1, b.y1)
        inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        base = max(1.0, (b.x1 - b.x0) * (b.y1 - b.y0))
        return inter / base
