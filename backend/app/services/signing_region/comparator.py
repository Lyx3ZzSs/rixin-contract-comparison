from __future__ import annotations

import re
import unicodedata
from collections import Counter
from itertools import zip_longest

from app.models import BBox, Document
from app.services.signing_region.models import (
    SigningElement,
    SigningElementType,
    SigningRegion,
    SigningRegionComparison,
)


ElementChangeConfig = tuple[str, str]

ELEMENT_CHANGE_CONFIGS: dict[SigningElementType, ElementChangeConfig] = {
    SigningElementType.DATE_FIELD: ("date_changes", "SIGNING_DATE_CHANGE"),
    SigningElementType.LABEL: ("label_changes", "SIGNING_LABEL_CHANGE"),
    SigningElementType.SIGNING_TABLE: ("table_changes", "SIGNING_TABLE_CHANGE"),
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
        original_party_references: dict[str, str] | None = None,
        compare_party_references: dict[str, str] | None = None,
        ignore_seals: bool = False,
    ) -> SigningRegionComparison:
        comparison = SigningRegionComparison(
            comparison_id=self._comparison_id(original, compare),
            original_region=original,
            compare_region=compare,
            match_confidence=match_confidence,
        )
        if ignore_seals and original is None and compare is not None and self._seal_only_region(compare):
            return comparison
        if ignore_seals and compare is None and original is not None and self._seal_only_region(original):
            return comparison
        if original is None and compare is not None:
            comparison.diff_type = "ADD"
            comparison.region_changes.append({"type": "ADD", "detail": "新增签署栏"})
            comparison.review_flags.append("SIGNING_VISUAL_CHANGE")
            return comparison
        if compare is None and original is not None:
            comparison.diff_type = "DELETE"
            comparison.region_changes.append({"type": "DELETE", "detail": "删除签署栏"})
            comparison.review_flags.append("SIGNING_VISUAL_CHANGE")
            return comparison
        if original is None or compare is None:
            return comparison
        if match_confidence and match_confidence < 0.7:
            self._add_review_flag(comparison, "SIGNING_MATCH_LOW_CONFIDENCE")
        missing_roles = self._compare_columns(original, compare, comparison)
        self._compare_party_fields(
            original,
            compare,
            comparison,
            original_party_references or {},
            compare_party_references or {},
            excluded_roles=missing_roles,
        )
        self._compare_fields(
            original,
            compare,
            comparison,
            original_party_references=original_party_references or {},
            compare_party_references=compare_party_references or {},
            excluded_roles=missing_roles,
        )
        self._compare_signatures(original, compare, comparison, excluded_roles=missing_roles)
        if not ignore_seals:
            self._compare_seals(original, compare, comparison)
        self._compare_elements(original, compare, comparison, ignore_seals=ignore_seals)
        if self._has_changes(comparison):
            comparison.diff_type = "MODIFY"
        return comparison

    @staticmethod
    def _seal_only_region(region: SigningRegion) -> bool:
        return bool(region.elements) and all(
            element.element_type in {SigningElementType.SEAL, SigningElementType.LABEL, SigningElementType.VISUAL_AREA}
            for element in region.elements
        )

    def _compare_elements(
        self,
        original: SigningRegion,
        compare: SigningRegion,
        comparison: SigningRegionComparison,
        *,
        ignore_seals: bool,
    ) -> None:
        for element_type, (changes_attr, review_flag) in ELEMENT_CHANGE_CONFIGS.items():
            if ignore_seals and element_type == SigningElementType.SEAL:
                continue
            if element_type in {
                SigningElementType.DATE_FIELD,
                SigningElementType.LABEL,
                SigningElementType.SIGNING_TABLE,
            } and (self._has_structured_fields(original) or self._has_structured_fields(compare)):
                continue
            original_text = self._element_text(original, element_type)
            compare_text = self._element_text(compare, element_type)
            original_visual = self._element_visual_signature(original, element_type)
            compare_visual = self._element_visual_signature(compare, element_type)
            if original_text == compare_text and original_visual == compare_visual:
                continue
            original_elements = [element for element in original.elements if element.element_type == element_type]
            compare_elements = [element for element in compare.elements if element.element_type == element_type]
            if original_elements and compare_elements:
                change_type = "MODIFY"
            elif original_elements:
                change_type = "DELETE"
            else:
                change_type = "ADD"
            getattr(comparison, changes_attr).append(
                {
                    "type": change_type,
                    "element_type": element_type.value,
                    "original_text": original_text,
                    "compare_text": compare_text,
                    "original_visual": original_visual,
                    "compare_visual": compare_visual,
                    "original_element_id": original_elements[0].element_id if original_elements else "",
                    "compare_element_id": compare_elements[0].element_id if compare_elements else "",
                }
            )
            self._add_review_flag(comparison, review_flag)

    def _compare_fields(
        self,
        original: SigningRegion,
        compare: SigningRegion,
        comparison: SigningRegionComparison,
        *,
        original_party_references: dict[str, str],
        compare_party_references: dict[str, str],
        excluded_roles: set[str],
    ) -> None:
        original_fields = self._structured_fields(original)
        compare_fields = self._structured_fields(compare)
        for slot in sorted(set(original_fields) | set(compare_fields)):
            role, field_key = slot
            if role in excluded_roles:
                continue
            for original_element, compare_element in self._pair_structured_field_elements(
                field_key,
                original_fields.get(slot, []),
                compare_fields.get(slot, []),
            ):
                if self._is_party_name_ocr_gap(
                    role,
                    field_key,
                    original_element,
                    compare_element,
                    original_fields,
                    compare_fields,
                    original_party_references,
                    compare_party_references,
                ):
                    continue
                if self._is_repeated_blank_field_ocr_gap(
                    role,
                    field_key,
                    original_element,
                    compare_element,
                    original_fields,
                    compare_fields,
                    original,
                    compare,
                ):
                    continue
                original_text = original_element.text.strip() if original_element is not None else ""
                compare_text = compare_element.text.strip() if compare_element is not None else ""
                original_label = self._field_label(original_element)
                compare_label = self._field_label(compare_element)
                value_changed = field_key != "signature" and self._normalized_field_value(
                    field_key, original_text
                ) != self._normalized_field_value(field_key, compare_text)
                label_changed = not self._field_labels_equivalent(field_key, original_label, compare_label)
                if not value_changed and not label_changed:
                    continue
                if self._is_seal_occluded_address_ocr_conflict(
                    field_key,
                    original_text,
                    compare_text,
                    original,
                    compare,
                ):
                    continue
                if original_element is None or compare_element is None:
                    change_type = "DELETE" if original_element is not None else "ADD"
                    change_scope = "field"
                    original_change_text = self._full_field_text(original_element)
                    compare_change_text = self._full_field_text(compare_element)
                elif label_changed or (original_text and compare_text):
                    change_type = "MODIFY"
                    change_scope = "value" if value_changed and not label_changed else "label"
                    original_change_text = original_text if value_changed else original_label
                    compare_change_text = compare_text if value_changed else compare_label
                else:
                    change_type = "DELETE" if original_text else "ADD"
                    change_scope = "value" if value_changed and not label_changed else "label"
                    original_change_text = original_text if value_changed else original_label
                    compare_change_text = compare_text if value_changed else compare_label
                element = original_element or compare_element
                comparison.field_changes.append(
                    {
                        "type": change_type,
                        "element_type": SigningElementType.FIELD.value,
                        "party_role": role,
                        "field_key": field_key,
                        "field_label": str(element.raw_ref.get("field_label") or field_key),
                        "change_scope": change_scope,
                        "original_text": original_change_text,
                        "compare_text": compare_change_text,
                        "original_element_id": original_element.element_id if original_element is not None else "",
                        "compare_element_id": compare_element.element_id if compare_element is not None else "",
                    }
                )
                self._add_review_flag(comparison, "SIGNING_FIELD_CHANGE")
                if field_key in {"account", "credit_code", "party", "party_name", "date"}:
                    self._add_review_flag(comparison, "CRITICAL_VALUE_CHANGE")

    @classmethod
    def _is_party_name_ocr_gap(
        cls,
        role: str,
        field_key: str,
        original_element: SigningElement | None,
        compare_element: SigningElement | None,
        original_fields: dict[tuple[str, str], list[SigningElement]],
        compare_fields: dict[tuple[str, str], list[SigningElement]],
        original_references: dict[str, str],
        compare_references: dict[str, str],
    ) -> bool:
        if field_key != "party_name" or (original_element is None) == (compare_element is None):
            return False
        original_reference = cls._normalized_party_name(original_references.get(role, ""))
        compare_reference = cls._normalized_party_name(compare_references.get(role, ""))
        present = original_element or compare_element
        if present is None or not original_reference or original_reference != compare_reference:
            return False
        if not cls._party_name_matches_reference(
            cls._normalized_party_name(present.text),
            original_reference,
        ):
            return False
        shared_peer_fields = 0
        for peer_role, peer_key in set(original_fields) & set(compare_fields):
            if peer_role != role or peer_key == "party_name":
                continue
            original_values = {
                cls._normalized_field_value(peer_key, element.text)
                for element in original_fields[(peer_role, peer_key)]
                if element.text.strip()
            }
            compare_values = {
                cls._normalized_field_value(peer_key, element.text)
                for element in compare_fields[(peer_role, peer_key)]
                if element.text.strip()
            }
            if original_values & compare_values:
                shared_peer_fields += 1
        return shared_peer_fields >= 2

    @classmethod
    def _pair_structured_field_elements(
        cls,
        field_key: str,
        original_elements: list[SigningElement],
        compare_elements: list[SigningElement],
    ) -> list[tuple[SigningElement | None, SigningElement | None]]:
        remaining_compare = list(compare_elements)
        unmatched_original: list[SigningElement] = []
        pairs: list[tuple[SigningElement | None, SigningElement | None]] = []
        for original in original_elements:
            original_value = cls._normalized_field_value(field_key, original.text)
            original_label = cls._normalized_field_label(cls._field_label(original))
            match_index = next(
                (
                    index
                    for index, candidate in enumerate(remaining_compare)
                    if cls._normalized_field_value(field_key, candidate.text) == original_value
                    and cls._normalized_field_label(cls._field_label(candidate)) == original_label
                ),
                None,
            )
            if match_index is None:
                unmatched_original.append(original)
                continue
            pairs.append((original, remaining_compare.pop(match_index)))
        pairs.extend(zip_longest(unmatched_original, remaining_compare))
        return pairs

    @classmethod
    def _is_repeated_blank_field_ocr_gap(
        cls,
        role: str,
        field_key: str,
        original_element: SigningElement | None,
        compare_element: SigningElement | None,
        original_fields: dict[tuple[str, str], list],
        compare_fields: dict[tuple[str, str], list],
        original_region: SigningRegion,
        compare_region: SigningRegion,
    ) -> bool:
        if (original_element is None) == (compare_element is None):
            return False
        present = original_element or compare_element
        if present is None or present.text.strip():
            return False
        missing_fields = original_fields if original_element is None else compare_fields
        missing_region = original_region if original_element is None else compare_region
        return any(
            other_role != role
            and other_key == field_key
            and (
                any(cls._same_horizontal_slot(present.bbox, candidate.bbox) for candidate in elements)
                or cls._seal_overlaps_bbox(missing_region, present.bbox, tolerance=2.0)
            )
            for (other_role, other_key), elements in missing_fields.items()
        )

    @staticmethod
    def _same_horizontal_slot(left: BBox, right: BBox) -> bool:
        overlap = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
        return overlap / max(1.0, min(left.x1 - left.x0, right.x1 - right.x0)) >= 0.5

    @classmethod
    def _is_seal_occluded_address_ocr_conflict(
        cls,
        field_key: str,
        original_text: str,
        compare_text: str,
        original_region: SigningRegion,
        compare_region: SigningRegion,
    ) -> bool:
        if field_key != "address" or not original_text or not compare_text:
            return False
        if not (cls._region_has_visual_seal(original_region) or cls._region_has_visual_seal(compare_region)):
            return False
        left = cls._normalized_field_value(field_key, original_text)
        right = cls._normalized_field_value(field_key, compare_text)
        short, long = sorted((left, right), key=len)
        if len(short) > max(6, len(long) // 2) or len(short) < 4:
            return False
        return short[:2] == long[:2] and short[-2:] == long[-2:] and cls._is_ordered_subsequence(short, long)

    @staticmethod
    def _is_ordered_subsequence(short: str, long: str) -> bool:
        iterator = iter(long)
        return all(character in iterator for character in short)

    def _compare_signatures(
        self,
        original: SigningRegion,
        compare: SigningRegion,
        comparison: SigningRegionComparison,
        *,
        excluded_roles: set[str],
    ) -> None:
        original_slots = self._signature_slots(original)
        compare_slots = self._signature_slots(compare)
        for slot in sorted(set(original_slots) | set(compare_slots)):
            original_signed = original_slots.get(slot, False)
            compare_signed = compare_slots.get(slot, False)
            if original_signed == compare_signed:
                continue
            role, field_key = slot
            if role in excluded_roles:
                continue
            comparison.signature_changes.append(
                {
                    "type": "ADD" if compare_signed else "DELETE",
                    "element_type": SigningElementType.SIGNATURE.value,
                    "party_role": role,
                    "field_key": field_key,
                    "field_label": self._signature_slot_label(original, compare, slot),
                    "original_text": "已签" if original_signed else "未签",
                    "compare_text": "已签" if compare_signed else "未签",
                }
            )
            self._add_review_flag(comparison, "SIGNING_SIGNATURE_CHANGE")

    def _compare_seals(
        self,
        original: SigningRegion,
        compare: SigningRegion,
        comparison: SigningRegionComparison,
    ) -> None:
        original_seals = self._role_scoped_elements(original, SigningElementType.SEAL)
        compare_seals = self._role_scoped_elements(compare, SigningElementType.SEAL)
        role_order = {"甲方": 0, "乙方": 1, "丙方": 2, "丁方": 3, "unknown": 4}
        scoped = "two_column_layout" in original.confidence_reasons or "two_column_layout" in compare.confidence_reasons
        for role in sorted(set(original_seals) | set(compare_seals), key=lambda value: role_order.get(value, 5)):
            for original_element, compare_element in zip_longest(
                original_seals.get(role, []), compare_seals.get(role, [])
            ):
                original_text = original_element.text.strip() if original_element is not None else ""
                compare_text = compare_element.text.strip() if compare_element is not None else ""
                if original_element is not None and compare_element is not None and original_text == compare_text:
                    continue
                change_type = "MODIFY"
                if original_element is None:
                    change_type = "ADD"
                elif compare_element is None:
                    change_type = "DELETE"
                comparison.seal_changes.append(
                    {
                        "type": change_type,
                        "element_type": SigningElementType.SEAL.value,
                        "original_text": original_text,
                        "compare_text": compare_text,
                        "original_element_id": original_element.element_id if original_element is not None else "",
                        "compare_element_id": compare_element.element_id if compare_element is not None else "",
                        **({"party_role": role, "field_label": "印章"} if scoped else {}),
                    }
                )
                self._add_review_flag(comparison, "SIGNING_SEAL_CHANGE")

    @classmethod
    def _role_scoped_elements(
        cls,
        region: SigningRegion,
        element_type: SigningElementType,
    ) -> dict[str, list[SigningElement]]:
        elements: dict[str, list[SigningElement]] = {}
        for element in region.elements:
            if element.element_type != element_type:
                continue
            role = str(element.raw_ref.get("party_role") or cls._role_for_bbox(region, element.bbox))
            elements.setdefault(role, []).append(element)
        return elements

    def _compare_columns(
        self,
        original: SigningRegion,
        compare: SigningRegion,
        comparison: SigningRegionComparison,
    ) -> set[str]:
        original_roles = self._column_roles(original)
        compare_roles = self._column_roles(compare)
        missing_roles = original_roles ^ compare_roles
        for role in sorted(missing_roles):
            deleted = role in original_roles
            comparison.column_changes.append(
                {
                    "type": "DELETE" if deleted else "ADD",
                    "party_role": role,
                    "field_label": "签署栏",
                    "original_text": f"{role}签署栏" if deleted else "",
                    "compare_text": "" if deleted else f"{role}签署栏",
                    "detail": f"{'删除' if deleted else '新增'}{role}签署栏",
                }
            )
            self._add_review_flag(comparison, "SIGNING_COLUMN_CHANGE")
        return missing_roles

    @staticmethod
    def _column_roles(region: SigningRegion) -> set[str]:
        return {
            str(element.raw_ref.get("party_role") or "")
            for element in region.elements
            if element.element_type in {SigningElementType.FIELD, SigningElementType.PARTY_FIELD}
            and str(element.raw_ref.get("party_role") or "") not in {"", "unknown"}
        }

    @staticmethod
    def _structured_fields(region: SigningRegion) -> dict[tuple[str, str], list]:
        fields: dict[tuple[str, str], list] = {}
        for element in region.elements:
            if element.element_type != SigningElementType.FIELD:
                continue
            field_key = str(element.raw_ref.get("field_key") or "")
            if not field_key:
                continue
            role = str(element.raw_ref.get("party_role") or "unknown")
            fields.setdefault((role, field_key), []).append(element)
        return fields

    @staticmethod
    def _has_structured_fields(region: SigningRegion) -> bool:
        return any(element.element_type == SigningElementType.FIELD for element in region.elements)

    @staticmethod
    def _normalized_field_value(field_key: str, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value or "")
        normalized = re.sub(r"\s+", "", normalized).replace(":", "：")
        if field_key in {"account", "phone", "fax", "credit_code"}:
            normalized = re.sub(r"[-‐‑‒–—_]", "", normalized)
        return normalized

    @staticmethod
    def _field_label(element: SigningElement | None) -> str:
        return str(element.raw_ref.get("field_label") or "") if element is not None else ""

    @staticmethod
    def _full_field_text(element: SigningElement | None) -> str:
        if element is None:
            return ""
        prefix = str(element.raw_ref.get("field_prefix") or element.raw_ref.get("field_label") or "")
        return f"{prefix}{element.text.strip()}"

    @staticmethod
    def _normalized_field_label(label: str) -> str:
        return re.sub(r"[\s:：（）()]", "", unicodedata.normalize("NFKC", label or ""))

    @classmethod
    def _field_labels_equivalent(cls, field_key: str, original_label: str, compare_label: str) -> bool:
        original = cls._normalized_field_label(original_label)
        compare = cls._normalized_field_label(compare_label)
        if original == compare:
            return True
        if field_key != "credit_code" or not original or not compare:
            return False
        short, long = sorted((original, compare), key=len)
        return len(short) >= 5 and long.startswith(short)

    @classmethod
    def _signature_slots(cls, region: SigningRegion) -> dict[tuple[str, str], bool]:
        slots: dict[tuple[str, str], bool] = {}
        signature_fields = [
            element
            for element in region.elements
            if element.element_type == SigningElementType.FIELD
            and (
                str(element.raw_ref.get("field_key") or "") == "signature"
                or "签字" in str(element.raw_ref.get("field_label") or "")
                or "签名" in str(element.raw_ref.get("field_label") or "")
            )
        ]
        for element in signature_fields:
            role = str(element.raw_ref.get("party_role") or "unknown")
            field_key = str(element.raw_ref.get("field_key") or "signature")
            slots[(role, field_key)] = False

        visual_signatures = [
            element for element in region.elements if element.element_type == SigningElementType.SIGNATURE
        ]
        for element in visual_signatures:
            role = str(element.raw_ref.get("party_role") or cls._role_for_bbox(region, element.bbox))
            field_key = str(element.raw_ref.get("field_key") or "")
            candidates = [
                field
                for field in signature_fields
                if str(field.raw_ref.get("party_role") or "unknown") == role
            ]
            if field_key:
                slot = (role, field_key)
            elif candidates:
                anchor = min(
                    candidates,
                    key=lambda field: abs(
                        (field.bbox.y0 + field.bbox.y1) - (element.bbox.y0 + element.bbox.y1)
                    ),
                )
                slot = (role, str(anchor.raw_ref.get("field_key") or "signature"))
            else:
                slot = (role, "signature")
            slots[slot] = True
        return slots

    @classmethod
    def _role_for_bbox(cls, region: SigningRegion, bbox) -> str:
        if "two_column_layout" in region.confidence_reasons:
            midpoint = cls._column_midpoint(region)
            return "甲方" if (bbox.x0 + bbox.x1) / 2 < midpoint else "乙方"
        if region.region_role.value == "party_a":
            return "甲方"
        if region.region_role.value == "party_b":
            return "乙方"
        if region.region_role.value == "both_parties":
            midpoint = (region.bbox.x0 + region.bbox.x1) / 2
            return "甲方" if (bbox.x0 + bbox.x1) / 2 < midpoint else "乙方"
        return "unknown"

    @staticmethod
    def _column_midpoint(region: SigningRegion) -> float:
        fallback = (region.bbox.x0 + region.bbox.x1) / 2
        left = [
            element.bbox
            for element in region.elements
            if element.element_type == SigningElementType.FIELD
            and str(element.raw_ref.get("party_role") or "") == "甲方"
        ]
        right = [
            element.bbox
            for element in region.elements
            if element.element_type == SigningElementType.FIELD
            and str(element.raw_ref.get("party_role") or "") == "乙方"
        ]
        if not left or not right:
            return fallback
        left_x1 = max(bbox.x1 for bbox in left)
        right_x0 = min(bbox.x0 for bbox in right)
        return (left_x1 + right_x0) / 2 if left_x1 < right_x0 else fallback

    @staticmethod
    def _signature_slot_label(
        original: SigningRegion,
        compare: SigningRegion,
        slot: tuple[str, str],
    ) -> str:
        role, field_key = slot
        for region in (original, compare):
            for element in region.elements:
                if (
                    element.element_type == SigningElementType.FIELD
                    and str(element.raw_ref.get("party_role") or "unknown") == role
                    and str(element.raw_ref.get("field_key") or "") == field_key
                ):
                    return str(element.raw_ref.get("field_label") or "签字")
        return "签字"

    def _compare_party_fields(
        self,
        original: SigningRegion,
        compare: SigningRegion,
        comparison: SigningRegionComparison,
        original_party_references: dict[str, str],
        compare_party_references: dict[str, str],
        *,
        excluded_roles: set[str],
    ) -> None:
        original_fields = self._party_fields(original)
        compare_fields = self._party_fields(compare)
        for role in sorted(set(original_fields) | set(compare_fields)):
            if role in excluded_roles:
                continue
            original_text = original_fields.get(role, "")
            compare_text = compare_fields.get(role, "")
            if self._normalized_party_text(original_text) == self._normalized_party_text(compare_text):
                continue
            missing_region = compare if not compare_text else original if not original_text else None
            if (
                missing_region is not None
                and "two_column_layout" in missing_region.confidence_reasons
                and role in self._column_roles(missing_region)
                and self._seal_overlaps_party_field(
                    missing_region,
                    original if original_text else compare,
                    role,
                )
            ):
                continue
            if self._is_seal_occluded_party_ocr_conflict(
                role,
                original_text,
                compare_text,
                original,
                compare,
                original_party_references,
                compare_party_references,
            ):
                continue
            original_residual = self._party_residual_element(original, role) if not original_text else None
            compare_residual = self._party_residual_element(compare, role) if not compare_text else None
            original_residual_text = original_residual.text.strip() if original_residual else ""
            compare_residual_text = compare_residual.text.strip() if compare_residual else ""
            if (original_text or original_residual_text) and (compare_text or compare_residual_text):
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
                    "change_scope": "field",
                    "original_text": original_text,
                    "compare_text": compare_text,
                    **({"original_element_id": original_residual.element_id} if original_residual else {}),
                    **({"compare_element_id": compare_residual.element_id} if compare_residual else {}),
                    **({"original_residual_text": original_residual_text} if original_residual_text else {}),
                    **({"compare_residual_text": compare_residual_text} if compare_residual_text else {}),
                }
            )
            self._add_review_flag(comparison, "SIGNING_PARTY_CHANGE")
            self._add_review_flag(comparison, "CRITICAL_VALUE_CHANGE")

    def reconcile_occluded_party_ocr(
        self,
        original: SigningRegion | None,
        compare: SigningRegion | None,
        *,
        original_party_references: dict[str, str],
        compare_party_references: dict[str, str],
    ) -> None:
        if original is None or compare is None:
            return
        original_fields = self._party_fields(original)
        compare_fields = self._party_fields(compare)
        for role in sorted(set(original_fields) & set(compare_fields)):
            original_text = original_fields[role]
            compare_text = compare_fields[role]
            if not self._is_seal_occluded_party_ocr_conflict(
                role,
                original_text,
                compare_text,
                original,
                compare,
                original_party_references,
                compare_party_references,
            ):
                continue
            if self._region_has_visual_seal(compare):
                self._replace_region_party_name(
                    compare,
                    self._normalized_party_name(compare_text),
                    self._normalized_party_name(compare_party_references.get(role, "")),
                )
            elif self._region_has_visual_seal(original):
                self._replace_region_party_name(
                    original,
                    self._normalized_party_name(original_text),
                    self._normalized_party_name(original_party_references.get(role, "")),
                )

    @staticmethod
    def _replace_region_party_name(region: SigningRegion, observed_name: str, reference_name: str) -> None:
        if not observed_name or not reference_name or observed_name == reference_name:
            return
        for element in region.elements:
            is_party_element = element.element_type == SigningElementType.PARTY_FIELD
            is_legacy_summary = element.element_type == SigningElementType.SIGNING_TABLE and len(observed_name) >= 2
            if (is_party_element or is_legacy_summary) and observed_name in element.text:
                element.text = element.text.replace(observed_name, reference_name)

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
    def _party_residual_element(region: SigningRegion, role: str) -> SigningElement | None:
        return next(
            (
                element
                for element in region.elements
                if element.element_type == SigningElementType.LABEL
                and element.raw_ref.get("residual_kind") == "party_suffix"
                and str(element.raw_ref.get("party_role") or "") == role
                and element.text.strip()
            ),
            None,
        )

    @staticmethod
    def _seal_overlaps_party_field(
        sealed_region: SigningRegion,
        party_region: SigningRegion,
        role: str,
    ) -> bool:
        party = next(
            (
                element
                for element in party_region.elements
                if element.element_type == SigningElementType.PARTY_FIELD
                and str(element.raw_ref.get("party_role") or "") == role
            ),
            None,
        )
        if party is None:
            return False
        return SigningRegionComparator._seal_overlaps_bbox(sealed_region, party.bbox)

    @staticmethod
    def _seal_overlaps_bbox(region: SigningRegion, bbox: BBox, *, tolerance: float = 0.0) -> bool:
        return any(
            SigningRegionComparator._bboxes_overlap(bbox, element.bbox, tolerance=tolerance)
            for element in region.elements
            if element.element_type == SigningElementType.SEAL
            and (element.source == "visual_model" or "seal" in element.text.lower())
        )

    @staticmethod
    def _bboxes_overlap(left: BBox, right: BBox, *, tolerance: float = 0.0) -> bool:
        return min(left.x1, right.x1) + tolerance > max(left.x0, right.x0) and min(left.y1, right.y1) + tolerance > max(
            left.y0,
            right.y0,
        )

    @staticmethod
    def _normalized_party_text(text: str) -> str:
        return re.sub(r"\s+", "", text or "").replace(":", "：")

    @classmethod
    def _is_seal_occluded_party_ocr_conflict(
        cls,
        role: str,
        original_text: str,
        compare_text: str,
        original_region: SigningRegion,
        compare_region: SigningRegion,
        original_references: dict[str, str],
        compare_references: dict[str, str],
    ) -> bool:
        original_reference = cls._normalized_party_name(original_references.get(role, ""))
        compare_reference = cls._normalized_party_name(compare_references.get(role, ""))
        if not original_reference or original_reference != compare_reference:
            return False
        original_name = cls._normalized_party_name(original_text)
        compare_name = cls._normalized_party_name(compare_text)
        if not (cls._region_has_visual_seal(original_region) or cls._region_has_visual_seal(compare_region)):
            return False
        return cls._party_name_matches_reference(
            original_name, original_reference
        ) and cls._party_name_matches_reference(compare_name, compare_reference)

    @classmethod
    def _party_name_matches_reference(cls, observed: str, reference: str) -> bool:
        return bool(observed) and (
            observed == reference
            or reference.startswith(observed)
            or cls._single_character_substitution(observed, reference)
        )

    @staticmethod
    def _normalized_party_name(text: str) -> str:
        normalized = re.sub(r"\s+", "", text or "").replace(":", "：")
        normalized = re.sub(r"^(?:甲方|乙方|买方|卖方|采购方|供应商)[:：]?", "", normalized)
        normalized = re.sub(r"(?:[】\]]?[（(]?盖章[）)]?)$", "", normalized)
        return normalized.strip("【】[]（）()：:")

    @staticmethod
    def _single_character_substitution(left: str, right: str) -> bool:
        return bool(left and len(left) == len(right) and sum(a != b for a, b in zip(left, right)) == 1)

    @staticmethod
    def _region_has_visual_seal(region: SigningRegion) -> bool:
        return any(
            element.element_type == SigningElementType.SEAL
            and (element.source == "visual_model" or "seal" in element.text.lower())
            for element in region.elements
        )

    @classmethod
    def party_references(cls, document: Document) -> dict[str, str]:
        candidates: dict[str, Counter[str]] = {}
        pattern = re.compile(
            r"(?P<role>甲方|乙方|买方|卖方|采购方|供应商)(?:\s*[:：]\s*|\s*\n\s*)[【\[]?"
            r"(?P<name>[\u4e00-\u9fffA-Za-z0-9（）()·\-]{2,80}?(?:分公司|有限公司|股份有限公司|公司))[】\]]?"
        )
        for page in document.pages:
            for block in page.blocks:
                for match in pattern.finditer(block.text or ""):
                    role = match.group("role")
                    name = cls._normalized_party_name(match.group("name"))
                    if name:
                        candidates.setdefault(role, Counter())[name] += 1
        return {role: counts.most_common(1)[0][0] for role, counts in candidates.items() if counts}

    @staticmethod
    def _element_text(region: SigningRegion, element_type: SigningElementType) -> str:
        return " ".join(
            element.text.strip()
            for element in region.elements
            if element.element_type == element_type and element.text.strip()
        )

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
            or comparison.region_changes
            or comparison.column_changes
            or comparison.signature_changes
            or comparison.date_changes
            or comparison.label_changes
            or comparison.party_changes
            or comparison.field_changes
            or comparison.table_changes
            or comparison.visual_changes
        )

    @staticmethod
    def _comparison_id(original: SigningRegion | None, compare: SigningRegion | None) -> str:
        left = original.region_id if original is not None else "NONE"
        right = compare.region_id if compare is not None else "NONE"
        return f"{left}__{right}"
