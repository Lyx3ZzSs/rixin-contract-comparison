from __future__ import annotations

import re

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

VISUAL_COMPARE_TYPES = {
    SigningElementType.SEAL,
    SigningElementType.SIGNATURE,
    SigningElementType.SIGNING_TABLE,
    SigningElementType.VISUAL_AREA,
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
        self._compare_party_fields(original, compare, comparison)
        self._compare_elements(original, compare, comparison)
        if self._has_changes(comparison):
            comparison.diff_type = "MODIFY"
        return comparison

    def _compare_elements(self, original: SigningRegion, compare: SigningRegion, comparison: SigningRegionComparison) -> None:
        for element_type, (changes_attr, review_flag) in ELEMENT_CHANGE_CONFIGS.items():
            original_text = self._element_text(original, element_type)
            compare_text = self._element_text(compare, element_type)
            original_visual = self._element_visual_signature(original, element_type)
            compare_visual = self._element_visual_signature(compare, element_type)
            if original_text == compare_text and original_visual == compare_visual:
                continue
            getattr(comparison, changes_attr).append(
                {
                    "type": "MODIFY",
                    "element_type": element_type.value,
                    "original_text": original_text,
                    "compare_text": compare_text,
                    "original_visual": original_visual,
                    "compare_visual": compare_visual,
                }
            )
            self._add_review_flag(comparison, review_flag)

    def _compare_party_fields(
        self,
        original: SigningRegion,
        compare: SigningRegion,
        comparison: SigningRegionComparison,
    ) -> None:
        original_fields = self._party_fields(original)
        compare_fields = self._party_fields(compare)
        for role in sorted(set(original_fields) | set(compare_fields)):
            original_text = original_fields.get(role, "")
            compare_text = compare_fields.get(role, "")
            if self._normalized_party_text(original_text) == self._normalized_party_text(compare_text):
                continue
            if original_text and compare_text:
                change_type = "MODIFY"
            elif original_text:
                change_type = "DELETE"
            else:
                change_type = "ADD"
            comparison.party_changes.append(
                {
                    "type": change_type,
                    "element_type": SigningElementType.PARTY_FIELD.value,
                    "party_role": role,
                    "original_text": original_text,
                    "compare_text": compare_text,
                }
            )
            self._add_review_flag(comparison, "SIGNING_PARTY_CHANGE")
            self._add_review_flag(comparison, "CRITICAL_VALUE_CHANGE")

    @staticmethod
    def _party_fields(region: SigningRegion) -> dict[str, str]:
        fields: dict[str, str] = {}
        for element in region.elements:
            if element.element_type != SigningElementType.PARTY_FIELD:
                continue
            role = str(element.raw_ref.get("party_role") or "").strip()
            if role and element.text.strip():
                fields.setdefault(role, element.text.strip())
        return fields

    @staticmethod
    def _normalized_party_text(text: str) -> str:
        return re.sub(r"\s+", "", text or "").replace(":", "：")

    @staticmethod
    def _element_text(region: SigningRegion, element_type: SigningElementType) -> str:
        return " ".join(element.text.strip() for element in region.elements if element.element_type == element_type and element.text.strip())

    @staticmethod
    def _element_visual_signature(region: SigningRegion, element_type: SigningElementType) -> str:
        if element_type not in VISUAL_COMPARE_TYPES:
            return ""
        tokens: list[str] = []
        for element in region.elements:
            if element.element_type != element_type:
                continue
            if element.visual_hash:
                tokens.append(f"hash:{element.visual_hash}")
                continue
            if element.text.strip():
                continue
            bbox = element.bbox
            tokens.append(
                "bbox:"
                f"{round(bbox.x0)}:{round(bbox.y0)}:{round(bbox.x1)}:{round(bbox.y1)}:"
                f"{element.source}:{element.model_name}"
            )
        return " ".join(sorted(tokens))

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
            or comparison.party_changes
            or comparison.table_changes
            or comparison.visual_changes
        )

    @staticmethod
    def _comparison_id(original: SigningRegion | None, compare: SigningRegion | None) -> str:
        left = original.region_id if original is not None else "NONE"
        right = compare.region_id if compare is not None else "NONE"
        return f"{left}__{right}"
