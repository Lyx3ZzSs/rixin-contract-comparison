from __future__ import annotations

from app.services.signing_region.models import (
    SigningElementType,
    SigningRegion,
    SigningRegionComparison,
)


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
        self._compare_seals(original, compare, comparison)
        if comparison.seal_changes or comparison.signature_changes or comparison.date_changes or comparison.label_changes or comparison.table_changes or comparison.visual_changes:
            comparison.diff_type = "MODIFY"
        return comparison

    def _compare_seals(self, original: SigningRegion, compare: SigningRegion, comparison: SigningRegionComparison) -> None:
        original_text = " ".join(element.text.strip() for element in original.elements if element.element_type == SigningElementType.SEAL and element.text.strip())
        compare_text = " ".join(element.text.strip() for element in compare.elements if element.element_type == SigningElementType.SEAL and element.text.strip())
        if original_text != compare_text:
            comparison.seal_changes.append(
                {
                    "type": "MODIFY",
                    "original_text": original_text,
                    "compare_text": compare_text,
                }
            )
            comparison.review_flags.append("SIGNING_SEAL_CHANGE")

    @staticmethod
    def _comparison_id(original: SigningRegion | None, compare: SigningRegion | None) -> str:
        left = original.region_id if original is not None else "NONE"
        right = compare.region_id if compare is not None else "NONE"
        return f"{left}__{right}"
