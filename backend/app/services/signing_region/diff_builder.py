from __future__ import annotations

from app.models import DiffItem, EvidenceBox, TextRange
from app.services.signing_region.models import SigningElementType, SigningRegion, SigningRegionComparison
from app.utils.id_utils import generate_diff_id


READABLE_CHANGE_FIELDS = (
    ("party_changes", "签署主体变化"),
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
        original_party_snippet = self._party_snippet(comparison, side="original")
        compare_party_snippet = self._party_snippet(comparison, side="compare")
        original_evidence = self._party_evidence(comparison, side="original")
        compare_evidence = self._party_evidence(comparison, side="compare")
        has_region_level_changes = self._has_region_level_changes(comparison)
        region_highlight_type = (
            comparison.diff_type
            if has_region_level_changes or not comparison.party_changes
            else None
        )
        region_evidence_method = (
            "signing_region_visual"
            if comparison.party_changes and has_region_level_changes
            else "signing_region"
        )
        if comparison.original_region is not None:
            original_evidence.append(
                self._evidence(
                    comparison.original_region,
                    region_highlight_type,
                    method=region_evidence_method,
                )
            )
        if comparison.compare_region is not None:
            compare_evidence.append(
                self._evidence(
                    comparison.compare_region,
                    region_highlight_type,
                    method=region_evidence_method,
                )
            )
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type=comparison.diff_type or "MODIFY",
            title=self._title(comparison),
            original_text=original_text,
            compare_text=compare_text,
            original_snippet=original_party_snippet or original_text,
            compare_snippet=compare_party_snippet or compare_text,
            readable_change=changed_text,
            source_type="signing_region",
            section_type="signature",
            review_flags=list(dict.fromkeys(comparison.review_flags)),
            original_evidence=original_evidence,
            compare_evidence=compare_evidence,
            original_change_ranges=[TextRange(start=0, end=len(original_text), highlight_type=comparison.diff_type or "MODIFY")] if original_text else [],
            compare_change_ranges=[TextRange(start=0, end=len(compare_text), highlight_type=comparison.diff_type or "MODIFY")] if compare_text else [],
        )

    @staticmethod
    def _summary(region: SigningRegion | None) -> str:
        if region is None:
            return ""
        parts = [
            element.text.strip()
            for element in region.elements
            if element.element_type != SigningElementType.PARTY_FIELD and element.text.strip()
        ]
        return "；".join(parts)

    @staticmethod
    def _party_snippet(comparison: SigningRegionComparison, *, side: str) -> str:
        field_name = f"{side}_text"
        return "\n".join(
            str(change.get(field_name) or "").strip()
            for change in comparison.party_changes
            if str(change.get(field_name) or "").strip()
        )

    @staticmethod
    def _party_evidence(
        comparison: SigningRegionComparison,
        *,
        side: str,
    ) -> list[EvidenceBox]:
        region = comparison.original_region if side == "original" else comparison.compare_region
        if region is None:
            return []
        elements_by_role = {
            str(element.raw_ref.get("party_role") or ""): element
            for element in region.elements
            if element.element_type == SigningElementType.PARTY_FIELD
        }
        evidences: list[EvidenceBox] = []
        for change in comparison.party_changes:
            role = str(change.get("party_role") or "")
            element = elements_by_role.get(role)
            text = str(change.get(f"{side}_text") or "").strip()
            if element is None or not text:
                continue
            change_type = str(change.get("type") or "MODIFY")
            if change_type == "ADD" and side == "original":
                continue
            if change_type == "DELETE" and side == "compare":
                continue
            evidences.append(
                EvidenceBox(
                    page_no=element.page_no,
                    bbox=element.bbox,
                    method="signing_region",
                    text=text,
                    highlight_type=change_type,
                    confidence=element.confidence,
                    evidence_quality="HIGH" if element.confidence >= 0.75 else "MEDIUM",
                )
            )
        return evidences

    @staticmethod
    def _has_region_level_changes(comparison: SigningRegionComparison) -> bool:
        return bool(
            comparison.seal_changes
            or comparison.date_changes
            or comparison.signature_changes
            or comparison.label_changes
            or comparison.table_changes
            or comparison.visual_changes
        )

    @staticmethod
    def _title(comparison: SigningRegionComparison) -> str:
        original = comparison.original_region
        compare = comparison.compare_region
        if original is not None and compare is not None and original.page_no != compare.page_no:
            return f"签署区（原第{original.page_no}页 / 新第{compare.page_no}页）"
        region = original or compare
        page_no = region.page_no if region is not None else 0
        return f"签署区（第{page_no}页）"

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
    def _evidence(
        region: SigningRegion | None,
        highlight_type: str | None,
        *,
        method: str = "signing_region",
    ) -> EvidenceBox:
        assert region is not None
        return EvidenceBox(
            page_no=region.page_no,
            bbox=region.bbox,
            method=method,
            text=SigningRegionDiffBuilder._summary(region)[:300],
            highlight_type=highlight_type,
            confidence=region.confidence,
            evidence_quality="HIGH" if region.confidence >= 0.75 else "MEDIUM",
        )
