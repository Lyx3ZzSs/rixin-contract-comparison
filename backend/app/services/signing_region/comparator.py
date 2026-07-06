from __future__ import annotations

from app.services.signing_region.models import (
    SigningElementType,
    SigningRegion,
    SigningRegionComparison,
)


ElementChangeConfig = tuple[str, str]

ELEMENT_CHANGE_CONFIGS: dict[SigningElementType, ElementChangeConfig] = {
    SigningElementType.SEAL: ("seal_changes", "SIGNING_SEAL_CHANGE"),
    SigningElementType.DATE_FIELD: ("date_changes", "SIGNING_DATE_CHANGE"),
    SigningElementType.SIGNATURE: ("signature_changes", "SIGNING_SIGNATURE_CHANGE"),
    SigningElementType.LABEL: ("label_changes", "SIGNING_LABEL_CHANGE"),
    SigningElementType.SIGNING_TABLE: ("table_changes", "SIGNING_TABLE_CHANGE"),
    SigningElementType.VISUAL_AREA: ("visual_changes", "SIGNING_VISUAL_CHANGE"),
}


class SigningRegionComparator:
    def compare(
        self,
        original: SigningRegion | None,
        compare: SigningRegion | None,
        *,
        match_confidence: float = 0.0,
    ) -> SigningRegionComparison:
        comparison = SigningRegionComparison(
            comparison_id=self._comparison_id(original, compare),
            original_region=original,
            compare_region=compare,
            match_confidence=match_confidence,
        )
        if original is None and compare is not None:
            comparison.diff_type = "ADD"
            comparison.signature_changes.append({"type": "ADD", "detail": "新增签章区"})
            comparison.review_flags.append("SIGNING_VISUAL_CHANGE")
            return comparison
        if compare is None and original is not None:
            comparison.diff_type = "DELETE"
            comparison.signature_changes.append({"type": "DELETE", "detail": "删除签章区"})
            comparison.review_flags.append("SIGNING_VISUAL_CHANGE")
            return comparison
        if original is None or compare is None:
            return comparison
        self._compare_elements(original, compare, comparison)
        if self._has_changes(comparison):
            comparison.diff_type = "MODIFY"
        return comparison

    def _compare_elements(self, original: SigningRegion, compare: SigningRegion, comparison: SigningRegionComparison) -> None:
        for element_type, (changes_attr, review_flag) in ELEMENT_CHANGE_CONFIGS.items():
            original_text = self._element_text(original, element_type)
            compare_text = self._element_text(compare, element_type)
            if original_text == compare_text:
                continue
            getattr(comparison, changes_attr).append(
                {
                    "type": "MODIFY",
                    "element_type": element_type.value,
                    "original_text": original_text,
                    "compare_text": compare_text,
                }
            )
            self._add_review_flag(comparison, review_flag)

    @staticmethod
    def _element_text(region: SigningRegion, element_type: SigningElementType) -> str:
        return " ".join(element.text.strip() for element in region.elements if element.element_type == element_type and element.text.strip())

    @staticmethod
    def _add_review_flag(comparison: SigningRegionComparison, review_flag: str) -> None:
        if review_flag not in comparison.review_flags:
            comparison.review_flags.append(review_flag)

    @staticmethod
    def _has_changes(comparison: SigningRegionComparison) -> bool:
        return bool(
            comparison.seal_changes
            or comparison.signature_changes
            or comparison.date_changes
            or comparison.label_changes
            or comparison.table_changes
            or comparison.visual_changes
        )

    @staticmethod
    def _comparison_id(original: SigningRegion | None, compare: SigningRegion | None) -> str:
        left = original.region_id if original is not None else "NONE"
        right = compare.region_id if compare is not None else "NONE"
        return f"{left}__{right}"
