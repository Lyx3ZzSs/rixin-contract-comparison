from __future__ import annotations

from app.models import DiffItem, EvidenceBox, TextRange
from app.services.signing_region.models import SigningRegion, SigningRegionComparison
from app.utils.id_utils import generate_diff_id


READABLE_CHANGE_FIELDS = (
    ("seal_changes", "印章文字变化"),
    ("date_changes", "签署日期变化"),
    ("signature_changes", "签字文字变化"),
    ("label_changes", "签章标签变化"),
    ("table_changes", "签章表格变化"),
    ("visual_changes", "签章视觉区域变化"),
)


class SigningRegionDiffBuilder:
    def build_diffs(self, comparisons: list[SigningRegionComparison], start_index: int = 1) -> list[DiffItem]:
        diffs: list[DiffItem] = []
        next_index = start_index
        for comparison in comparisons:
            if comparison.diff_type is None:
                continue
            diff = self._to_diff(comparison, next_index)
            diffs.append(diff)
            next_index += 1
        return diffs

    def _to_diff(self, comparison: SigningRegionComparison, index: int) -> DiffItem:
        original_text = self._summary(comparison.original_region)
        compare_text = self._summary(comparison.compare_region)
        changed_text = self._readable_change(comparison)
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type=comparison.diff_type or "MODIFY",
            title=self._title(comparison),
            original_text=original_text,
            compare_text=compare_text,
            original_snippet=original_text,
            compare_snippet=compare_text,
            readable_change=changed_text,
            source_type="signing_region",
            section_type="signature",
            review_flags=list(dict.fromkeys(comparison.review_flags)),
            original_evidence=[self._evidence(comparison.original_region, comparison.diff_type)] if comparison.original_region else [],
            compare_evidence=[self._evidence(comparison.compare_region, comparison.diff_type)] if comparison.compare_region else [],
            original_change_ranges=[TextRange(start=0, end=len(original_text), highlight_type=comparison.diff_type or "MODIFY")] if original_text else [],
            compare_change_ranges=[TextRange(start=0, end=len(compare_text), highlight_type=comparison.diff_type or "MODIFY")] if compare_text else [],
        )

    @staticmethod
    def _summary(region: SigningRegion | None) -> str:
        if region is None:
            return ""
        parts = [element.text.strip() for element in region.elements if element.text.strip()]
        return "；".join(parts)

    @staticmethod
    def _title(comparison: SigningRegionComparison) -> str:
        original = comparison.original_region
        compare = comparison.compare_region
        if original is not None and compare is not None and original.page_no != compare.page_no:
            return f"签署区（原第{original.page_no}页 / 新第{compare.page_no}页）"
        region = original or compare
        page_no = region.page_no if region is not None else 0
        return f"签章区（第{page_no}页）"

    @staticmethod
    def _readable_change(comparison: SigningRegionComparison) -> str:
        messages: list[str] = []
        for field_name, label in READABLE_CHANGE_FIELDS:
            for change in getattr(comparison, field_name):
                if change.get("detail"):
                    messages.append(str(change["detail"]))
                else:
                    messages.append(f"{label}：{change.get('original_text', '')} → {change.get('compare_text', '')}")
        return "；".join(messages) or "签章区发生变化"

    @staticmethod
    def _evidence(region: SigningRegion | None, highlight_type: str | None) -> EvidenceBox:
        assert region is not None
        return EvidenceBox(
            page_no=region.page_no,
            bbox=region.bbox,
            method="signing_region",
            text=SigningRegionDiffBuilder._summary(region)[:300],
            highlight_type=highlight_type,
            confidence=region.confidence,
            evidence_quality="HIGH" if region.confidence >= 0.75 else "MEDIUM",
        )
