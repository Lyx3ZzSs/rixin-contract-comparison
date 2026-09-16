from __future__ import annotations

import html
import re
import unicodedata
from collections import Counter
from datetime import date
from itertools import zip_longest

from app.models import BBox, Document
from app.services.field_observation import (
    FieldObservation,
    ObservationReason,
    ObservationState,
    decide_observation_transition,
)
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
    ) -> SigningRegionComparison:
        comparison = SigningRegionComparison(
            comparison_id=self._comparison_id(original, compare),
            original_region=original,
            compare_region=compare,
            match_confidence=match_confidence,
        )
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
        self._compare_seals(original, compare, comparison)
        self._compare_elements(original, compare, comparison)
        if self._has_changes(comparison):
            comparison.diff_type = "MODIFY"
        return comparison

    def _compare_elements(
        self,
        original: SigningRegion,
        compare: SigningRegion,
        comparison: SigningRegionComparison,
    ) -> None:
        for element_type, (changes_attr, review_flag) in ELEMENT_CHANGE_CONFIGS.items():
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
        original_fields = self._structured_fields(original, compare)
        compare_fields = self._structured_fields(compare, original)
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
                if self._is_overlapping_alternative_field_gap(
                    role,
                    field_key,
                    original_element,
                    compare_element,
                    original_fields,
                    compare_fields,
                ):
                    continue
                original_text = original_element.text.strip() if original_element is not None else ""
                compare_text = compare_element.text.strip() if compare_element is not None else ""
                original_label = self._field_label(original_element)
                compare_label = self._field_label(compare_element)
                original_value = self._normalized_field_value(field_key, original_text)
                compare_value = self._normalized_field_value(field_key, compare_text)
                if field_key == "date" and original_element is not None and compare_element is not None:
                    proof = original_element.raw_ref.get("paired_date_ink") or {}
                    if (proof.get("equivalent") and proof == compare_element.raw_ref.get("paired_date_ink")
                            and proof.get("original_element_id") == original_element.element_id
                            and proof.get("compare_element_id") == compare_element.element_id):
                        continue
                original_content_status = self._content_recognition_status(original_element)
                compare_content_status = self._content_recognition_status(compare_element)
                original_observation = self._field_observation(
                    original_element,
                    original,
                    peer_value=compare_value,
                )
                compare_observation = self._field_observation(
                    compare_element,
                    compare,
                    peer_value=original_value,
                    peer_element=original_element,
                )
                content_status = (
                    "NEEDS_REVIEW"
                    if "NEEDS_REVIEW" in {original_content_status, compare_content_status}
                    else "CONFIRMED"
                )
                content_reason = self._content_recognition_reason(compare_element or original_element)
                content_candidates = self._content_candidates(compare_element or original_element)
                if field_key in {"legal_representative", "authorized_representative", "signature"}:
                    partial_names = [
                        (side, element) for side, element in (("original", original_element), ("compare", compare_element))
                        if element is not None and re.fullmatch(r"[\u4e00-\u9fff]", element.text.strip())
                    ]
                    if partial_names and content_reason != "CONFLICTING_VALUE_CANDIDATES":
                        content_status = "NEEDS_REVIEW"
                        content_reason = "PARTIAL_SIGNATURE_NAME"
                        content_candidates = [*content_candidates, *[
                            {"text": element.text, "side": side, "source": element.source,
                             "element_id": element.element_id, "reason": "PARTIAL_SIGNATURE_NAME"}
                            for side, element in partial_names
                        ]]
                value_changed = field_key != "signature" and original_value != compare_value
                label_changed = not self._field_labels_equivalent(field_key, original_label, compare_label)
                # Keep template diagnostics, but absence of recognized values
                # does not prove both fields are blank. Missing/occluded fields
                # must still go through observation-based candidate admission.
                if not original_value and not compare_value:
                    if label_changed:
                        comparison.template_changes.append({"party_role": role, "field_key": field_key,
                            "original_label": original_label, "compare_label": compare_label,
                            **original_observation.as_details("original"), **compare_observation.as_details("compare"),
                            "reason": "EMPTY_FIELD_TEMPLATE_CHANGE"})
                if label_changed and self._is_seal_occluded_signature_label_conflict(
                    field_key,
                    original_element,
                    compare_element,
                    original,
                    compare,
                ):
                    label_changed = False
                if not value_changed and not label_changed:
                    continue
                observation_transition = decide_observation_transition(
                    original_observation,
                    compare_observation,
                    values_equal=not value_changed,
                    content_reliable=content_status == "CONFIRMED",
                )
                if self._is_seal_occluded_address_ocr_conflict(
                    field_key,
                    original_text,
                    compare_text,
                    original_element,
                    compare_element,
                    original,
                    compare,
                ):
                    continue
                narrow_continuation_noise = self._narrow_ocr_continuation_noise(
                    original_element,
                    compare_element,
                    original_value,
                    compare_value,
                )
                if original_element is None or compare_element is None:
                    change_type = "DELETE" if original_element is not None else "ADD"
                    change_scope = "field"
                    original_change_text = self._full_field_text(original_element)
                    compare_change_text = self._full_field_text(compare_element)
                elif label_changed or (original_value and compare_value):
                    change_type = "MODIFY"
                    change_scope = "value" if value_changed and not label_changed else "label"
                    original_change_text = original_text if value_changed else original_label
                    compare_change_text = compare_text if value_changed else compare_label
                else:
                    change_type = "DELETE" if original_value else "ADD"
                    change_scope = "value" if value_changed and not label_changed else "label"
                    original_change_text = original_text if value_changed else original_label
                    compare_change_text = compare_text if value_changed else compare_label
                if content_reason == "CONFLICTING_VALUE_CANDIDATES" and change_type in {"ADD", "DELETE"}:
                    if change_type == "ADD":
                        compare_change_text = ""
                    else:
                        original_change_text = ""
                if field_key == "date" and value_changed:
                    original_change_text = self._display_date_value(original_text) if original_value else ""
                    compare_change_text = self._display_date_value(compare_text) if compare_value else ""
                changed_date_components = (
                    self._changed_date_components(original_element, compare_element)
                    if field_key == "date" and value_changed
                    else []
                )
                change_decision = observation_transition.change_decision
                observed_states = {original_observation.state, compare_observation.state}
                if change_decision != "CONFIRMED" and (
                    field_key in {
                        "legal_representative",
                        "authorized_representative",
                        "signature",
                    }
                    or (
                        field_key not in {"account", "credit_code", "party", "party_name", "date"}
                        and observed_states == {ObservationState.OBSERVED_BLANK, ObservationState.UNOBSERVED}
                    )
                ):
                    change_decision = "CANDIDATE"
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
                        "changed_date_components": changed_date_components,
                        "fill_state_change": observation_transition.fill_state_change,
                        "change_decision": change_decision,
                        **original_observation.as_details("original"),
                        **compare_observation.as_details("compare"),
                        "content_recognition_status": content_status,
                        "content_recognition_reason": content_reason,
                        "content_candidates": content_candidates,
                        "decision_reason": (
                            "NARROW_OCR_CONTINUATION_NOISE"
                            if narrow_continuation_noise
                            else observation_transition.reason
                        ),
                        "critical": field_key in {"account", "credit_code", "party", "party_name", "date"},
                        "original_element_id": original_element.element_id if original_element is not None else "",
                        "compare_element_id": compare_element.element_id if compare_element is not None else "",
                    }
                )
                self._add_review_flag(comparison, "SIGNING_FIELD_CHANGE")
                if change_decision in {"CANDIDATE", "NEEDS_REVIEW"}:
                    self._add_review_flag(comparison, "OBSERVATION_UNOBSERVED")
                if field_key == "date" and not original_value and compare_value:
                    self._add_review_flag(comparison, "SIGNING_DATE_FILLED")
                if field_key in {"account", "credit_code", "party", "party_name", "date"}:
                    self._add_review_flag(comparison, "CRITICAL_VALUE_CHANGE")

    @staticmethod
    def _content_recognition_status(element: SigningElement | None) -> str:
        if element is None:
            return "CONFIRMED"
        return str(element.raw_ref.get("content_recognition_status") or "CONFIRMED")

    @staticmethod
    def _content_recognition_reason(element: SigningElement | None) -> str:
        if element is None:
            return ""
        return str(element.raw_ref.get("content_recognition_reason") or "")

    @staticmethod
    def _content_candidates(element: SigningElement | None) -> list[dict[str, object]]:
        if element is None:
            return []
        return [
            dict(item)
            for item in [
                *(element.raw_ref.get("value_evidence") or []),
                *(element.raw_ref.get("supporting_unlocalized_evidence") or []),
            ]
            if isinstance(item, dict)
        ]

    @classmethod
    def _field_observation(
        cls,
        element: SigningElement | None,
        region: SigningRegion,
        *,
        peer_value: str = "",
        peer_element: SigningElement | None = None,
    ) -> FieldObservation:
        if element is None:
            counterpart = peer_element.raw_ref.get("missing_compare_observation") if peer_element is not None else None
            if isinstance(counterpart, dict):
                state = ObservationState(str(counterpart.get("state") or ObservationState.UNOBSERVED.value))
                reason = str(counterpart.get("reason") or ObservationReason.FIELD_ELEMENT_MISSING.value)
                return FieldObservation(state, reason)
            return FieldObservation(
                ObservationState.UNOBSERVED,
                ObservationReason.FIELD_ELEMENT_MISSING.value,
            )

        explicit_state = element.observation_state or element.raw_ref.get("observation_state")
        explicit_reason = element.observation_reason or str(element.raw_ref.get("observation_reason") or "")
        if explicit_state:
            state = ObservationState(str(getattr(explicit_state, "value", explicit_state)))
            observation = FieldObservation(state, explicit_reason or "EXPLICIT_OBSERVATION")
            cls._store_observation(element, observation)
            return observation

        field_key = str(element.raw_ref.get("field_key") or "")
        value = cls._normalized_field_value(field_key, element.text)
        roi_payload = element.raw_ref.get("value_roi")
        roi = BBox.model_validate(roi_payload) if roi_payload else None
        if str(element.raw_ref.get("party_role") or "unknown") == "unknown":
            observation = FieldObservation(
                ObservationState.UNOBSERVED,
                ObservationReason.PARTY_ATTRIBUTION_UNRELIABLE.value,
            )
        elif cls._targeted_ocr_failed(element):
            observation = FieldObservation(
                ObservationState.UNOBSERVED,
                ObservationReason.OCR_EXTRACTION_FAILED.value,
            )
        elif cls._has_rejected_cross_column_evidence(element):
            observation = FieldObservation(
                ObservationState.UNOBSERVED,
                ObservationReason.CROSS_COLUMN_EVIDENCE_REJECTED.value,
            )
        elif roi is not None and cls._roi_occlusion(region, roi, page_no=element.page_no):
            occlusion_reason = cls._roi_occlusion(region, roi, page_no=element.page_no)
            if not value or (peer_value and value != peer_value):
                observation = FieldObservation(ObservationState.UNOBSERVED, occlusion_reason)
            else:
                observation = cls._localized_value_observation(element, value)
        elif value:
            observation = cls._localized_value_observation(element, value)
        elif roi is None:
            observation = FieldObservation(
                ObservationState.UNOBSERVED,
                ObservationReason.FIELD_ROI_NOT_FOUND.value,
            )
        elif not cls._roi_is_observable(region, element, roi):
            observation = FieldObservation(
                ObservationState.UNOBSERVED,
                ObservationReason.LOW_VISUAL_COVERAGE.value,
            )
        else:
            observation = FieldObservation(
                ObservationState.OBSERVED_BLANK,
                ObservationReason.CLEAR_LOCALIZED_VALUE_ROI.value,
            )
        cls._store_observation(element, observation)
        return observation

    @classmethod
    def _localized_value_observation(cls, element: SigningElement, value: str) -> FieldObservation:
        accepted = [item for item in element.raw_ref.get("value_evidence") or [] if isinstance(item, dict)]
        if any(item.get("localized") is not False and item.get("provenance_valid") is not False for item in accepted):
            return FieldObservation(
                ObservationState.OBSERVED_VALUE,
                ObservationReason.LOCALIZED_VALUE_EVIDENCE.value,
            )
        if element.raw_ref.get("value_bbox") or element.raw_ref.get("value_bboxes"):
            return FieldObservation(
                ObservationState.OBSERVED_VALUE,
                ObservationReason.LOCALIZED_PRIMARY_VALUE.value,
            )
        return FieldObservation(
            ObservationState.UNOBSERVED,
            ObservationReason.FIELD_ROI_NOT_FOUND.value,
        )

    @staticmethod
    def _store_observation(element: SigningElement, observation: FieldObservation) -> None:
        element.observation_state = observation.state
        element.observation_reason = observation.reason
        element.raw_ref["observation_state"] = observation.state.value
        element.raw_ref["observation_reason"] = observation.reason

    @staticmethod
    def _targeted_ocr_failed(element: SigningElement) -> bool:
        status = str((element.raw_ref.get("targeted_roi_ocr") or {}).get("status") or "")
        return status in {"INVALID_PAGE", "INVALID_ROI", "OCR_ERROR"}

    @staticmethod
    def _has_rejected_cross_column_evidence(element: SigningElement) -> bool:
        reasons = {
            str(item.get("rejection_reason") or "")
            for item in element.raw_ref.get("rejected_contaminated_evidence") or []
            if isinstance(item, dict)
        }
        return bool(
            reasons.intersection(
                {"CROSS_COLUMN_SOURCE_BLOCK", "AMBIGUOUS_MULTI_LABEL_BLOCK", "UNLOCALIZED_MULTI_ROW_BLOCK"}
            )
        )

    @classmethod
    def _roi_occlusion(cls, region: SigningRegion, roi: BBox, *, page_no: int | None = None) -> str:
        for element in region.elements:
            if page_no is not None and element.page_no != page_no:
                continue
            if element.element_type not in {SigningElementType.SEAL, SigningElementType.SIGNATURE}:
                continue
            if cls._bbox_overlap_ratio(roi, element.bbox) < 0.08:
                continue
            if element.element_type == SigningElementType.SEAL:
                return ObservationReason.SEAL_OCCLUSION.value
            return ObservationReason.HANDWRITING_OCCLUSION.value
        return ""

    @staticmethod
    def _roi_is_observable(region: SigningRegion, element: SigningElement, roi: BBox) -> bool:
        roi_area = max(0.0, roi.x1 - roi.x0) * max(0.0, roi.y1 - roi.y0)
        if roi_area <= 4.0 or element.confidence < 0.5:
            return False
        intersection = SigningRegionComparator._bbox_intersection_area(region.bbox, roi)
        return intersection / roi_area >= 0.9

    @classmethod
    def _signature_observations(
        cls,
        region: SigningRegion,
    ) -> dict[tuple[str, str], tuple[FieldObservation, bool, str]]:
        slots: dict[tuple[str, str], tuple[FieldObservation, bool, str]] = {}
        for element in region.elements:
            if element.element_type != SigningElementType.FIELD:
                continue
            key = str(element.raw_ref.get("field_key") or "")
            label = str(element.raw_ref.get("field_label") or "")
            if key != "signature" and not (
                key in {"legal_representative", "authorized_representative"} and ("签字" in label or "签名" in label)
            ):
                continue
            role = str(element.raw_ref.get("party_role") or "unknown")
            observation = cls._field_observation(element, region)
            slots[(role, "signature")] = (observation, False, element.element_id)

        for element in region.elements:
            if element.element_type != SigningElementType.SIGNATURE:
                continue
            role = str(element.raw_ref.get("party_role") or cls.role_for_bbox(region, element.bbox))
            observation = cls._visual_signature_observation(element, region)
            current = slots.get((role, "signature"))
            if (
                current is None
                or observation.state == ObservationState.OBSERVED_VALUE
                or current[0].state == ObservationState.OBSERVED_BLANK
                or (
                    observation.state == ObservationState.UNOBSERVED
                    and current[0].state == ObservationState.UNOBSERVED
                    and not current[1]
                )
            ):
                slots[(role, "signature")] = (observation, True, element.element_id)
        return slots

    @classmethod
    def _visual_signature_observation(
        cls,
        element: SigningElement,
        region: SigningRegion,
    ) -> FieldObservation:
        overlapping_seal = any(
            candidate.element_type == SigningElementType.SEAL
            and cls._bbox_overlap_ratio(element.bbox, candidate.bbox) >= 0.2
            for candidate in region.elements
        )
        if overlapping_seal:
            return FieldObservation(
                ObservationState.UNOBSERVED,
                ObservationReason.VISUAL_OBJECT_CONFLICT.value,
            )
        if element.confidence >= 0.75 and element.source in {"visual_model", "ocr"}:
            return FieldObservation(
                ObservationState.OBSERVED_VALUE,
                ObservationReason.RELIABLE_VISUAL_OBJECT.value,
            )
        return FieldObservation(
            ObservationState.UNOBSERVED,
            ObservationReason.LOW_VISUAL_COVERAGE.value,
        )

    @staticmethod
    def _signature_observation_text(observation: FieldObservation, candidate_present: bool) -> str:
        if observation.state == ObservationState.OBSERVED_VALUE:
            return "已签"
        if observation.state == ObservationState.OBSERVED_BLANK:
            return "未签"
        return "疑似已签（待确认）" if candidate_present else "未观察"

    @classmethod
    def _visual_object_observation(
        cls,
        element: SigningElement | None,
        region: SigningRegion,
        *,
        peer_region: SigningRegion,
        peer_element: SigningElement | None,
    ) -> FieldObservation:
        if element is not None:
            if element.confidence >= 0.75 and element.source == "visual_model":
                return FieldObservation(
                    ObservationState.OBSERVED_VALUE,
                    ObservationReason.RELIABLE_VISUAL_OBJECT.value,
                )
            return FieldObservation(
                ObservationState.UNOBSERVED,
                ObservationReason.LOW_VISUAL_COVERAGE.value,
            )
        if peer_element is None:
            return FieldObservation(
                ObservationState.UNOBSERVED,
                ObservationReason.VISUAL_OBJECT_NOT_DETECTED.value,
            )
        return FieldObservation(
            ObservationState.UNOBSERVED,
            ObservationReason.VISUAL_OBJECT_NOT_DETECTED.value,
        )

    @staticmethod
    def _roi_is_inside_region(region: SigningRegion, roi: BBox) -> bool:
        area = max(0.0, roi.x1 - roi.x0) * max(0.0, roi.y1 - roi.y0)
        if area <= 4.0:
            return False
        return SigningRegionComparator._bbox_intersection_area(region.bbox, roi) / area >= 0.75

    @staticmethod
    def _bbox_overlap_ratio(base: BBox, other: BBox) -> float:
        area = max(0.0, base.x1 - base.x0) * max(0.0, base.y1 - base.y0)
        if area <= 0:
            return 0.0
        return SigningRegionComparator._bbox_intersection_area(base, other) / area

    @staticmethod
    def _bbox_intersection_area(left: BBox, right: BBox) -> float:
        return max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0)) * max(
            0.0,
            min(left.y1, right.y1) - max(left.y0, right.y0),
        )

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
        pairs: list[tuple[SigningElement | None, SigningElement | None]] = []

        def nearest_geometry_distance(element: SigningElement) -> float:
            if not remaining_compare:
                return 0.0
            center = ((element.bbox.x0 + element.bbox.x1) / 2, (element.bbox.y0 + element.bbox.y1) / 2)
            return min(
                abs(center[0] - (candidate.bbox.x0 + candidate.bbox.x1) / 2)
                + abs(center[1] - (candidate.bbox.y0 + candidate.bbox.y1) / 2)
                for candidate in remaining_compare
            )

        def claim_priority(element: SigningElement) -> tuple[int, float]:
            label = cls._normalized_field_label(cls._field_label(element))
            has_same_label_peer = any(
                cls._normalized_field_label(cls._field_label(candidate)) == label for candidate in remaining_compare
            )
            return (0 if has_same_label_peer else 1, nearest_geometry_distance(element))

        # When one parser emits two interpretations of the same field, prefer
        # the matching label and use geometry to break ties.  Input order can
        # otherwise create a false MODIFY followed by a false DELETE.
        for original in sorted(original_elements, key=claim_priority):
            if not remaining_compare:
                pairs.append((original, None))
                continue
            original_label = cls._normalized_field_label(cls._field_label(original))
            original_value = cls._normalized_field_value(field_key, original.text)
            original_center = ((original.bbox.x0 + original.bbox.x1) / 2, (original.bbox.y0 + original.bbox.y1) / 2)

            def identity_cost(candidate: SigningElement) -> tuple[int, float, int]:
                candidate_label = cls._normalized_field_label(cls._field_label(candidate))
                candidate_center = (
                    (candidate.bbox.x0 + candidate.bbox.x1) / 2,
                    (candidate.bbox.y0 + candidate.bbox.y1) / 2,
                )
                geometry_distance = abs(original_center[0] - candidate_center[0]) + abs(
                    original_center[1] - candidate_center[1]
                )
                candidate_value = cls._normalized_field_value(field_key, candidate.text)
                return (
                    0 if candidate_label == original_label else 1,
                    geometry_distance,
                    0 if candidate_value == original_value else 1,
                )

            match_index = min(range(len(remaining_compare)), key=lambda index: identity_cost(remaining_compare[index]))
            pairs.append((original, remaining_compare.pop(match_index)))
        pairs.extend((None, candidate) for candidate in remaining_compare)
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
        signature_keys = {"legal_representative", "authorized_representative", "signature"}
        if (field_key in signature_keys
                and cls._seal_overlaps_bbox(missing_region, present.bbox, tolerance=2.0)
                and any(other_role == role and other_key in signature_keys and elements
                        for (other_role, other_key), elements in missing_fields.items())):
            present.raw_ref["label_occlusion_reconciliation"] = "ALTERNATIVE_SIGNATURE_LABEL_VISIBLE"
            return True
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
        original_element: SigningElement | None,
        compare_element: SigningElement | None,
        original_region: SigningRegion,
        compare_region: SigningRegion,
    ) -> bool:
        if field_key != "address" or not original_text or not compare_text:
            return False
        if any(element is not None and element.raw_ref.get("content_recognition_reason") ==
               "CONSISTENT_RED_SUPPRESSED_LOCAL_READINGS" for element in (original_element, compare_element)):
            # The localized value has been re-read independently of the peer.
            # A stamp must not erase a real digit/street change once recovered.
            return False
        if not (cls._region_has_visual_seal(original_region) or cls._region_has_visual_seal(compare_region)):
            return False
        left = cls._normalized_field_value(field_key, original_text)
        right = cls._normalized_field_value(field_key, compare_text)
        if cls._single_character_substitution(left, right):
            return True
        short, long = sorted((left, right), key=len)
        if len(short) < 4:
            return False
        longer_element = original_element if len(left) >= len(right) else compare_element
        segments = longer_element.raw_ref.get("field_segments", []) if longer_element is not None else []
        first_segment = (
            cls._normalized_field_value(
                field_key,
                re.sub(r"^\s*(?:地址|单位地址)\s*[:：]?", "", str(segments[0] or "")),
            )
            if segments
            else ""
        )
        if first_segment == short and long.startswith(short):
            return True
        if not (
            cls._seal_overlaps_element(original_region, original_element) if original_element is not None else False
        ) and not (
            cls._seal_overlaps_element(compare_region, compare_element) if compare_element is not None else False
        ):
            return False
        return (
            len(short) <= max(6, len(long) // 2)
            and short[:2] == long[:2]
            and short[-2:] == long[-2:]
            and cls._is_ordered_subsequence(short, long)
        )

    @classmethod
    def _is_overlapping_alternative_field_gap(
        cls,
        role: str,
        field_key: str,
        original_element: SigningElement | None,
        compare_element: SigningElement | None,
        original_fields: dict[tuple[str, str], list[SigningElement]],
        compare_fields: dict[tuple[str, str], list[SigningElement]],
    ) -> bool:
        if (original_element is None) == (compare_element is None):
            return False
        present = original_element or compare_element
        if present is None:
            return False
        same_side = original_fields if original_element is not None else compare_fields
        opposite = compare_fields if original_element is not None else original_fields
        peers = opposite.get((role, field_key), [])
        siblings = [item for item in same_side.get((role, field_key), []) if item.element_id != present.element_id]
        for sibling in siblings:
            sibling_value = cls._normalized_field_value(field_key, sibling.text)
            peer_matches_sibling = any(
                cls._normalized_field_value(field_key, peer.text) == sibling_value for peer in peers if sibling_value
            )
            if field_key == "date" and not sibling_value:
                peer_matches_sibling = any(
                    bool(cls._normalized_field_value(field_key, peer.text))
                    and cls._field_labels_equivalent(
                        field_key,
                        cls._field_label(sibling),
                        cls._field_label(peer),
                    )
                    for peer in peers
                )
            if not peer_matches_sibling:
                continue
            present_bboxes = cls._field_identity_bboxes(present) if field_key == "date" else [present.bbox]
            sibling_bboxes = cls._field_identity_bboxes(sibling) if field_key == "date" else [sibling.bbox]
            if any(
                cls._bboxes_overlap(left, right, tolerance=4.0) for left in present_bboxes for right in sibling_bboxes
            ):
                return True
        return False

    @staticmethod
    def _field_identity_bboxes(element: SigningElement) -> list[BBox]:
        bboxes = [element.bbox]
        for key in ("display_value_bbox", "value_bbox"):
            raw_bbox = element.raw_ref.get(key)
            if raw_bbox:
                bboxes.append(BBox.model_validate(raw_bbox))
        return bboxes

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
        original_slots = self._signature_observations(original)
        compare_slots = self._signature_observations(compare)
        for slot in sorted(set(original_slots) | set(compare_slots)):
            original_observation, original_candidate, original_element_id = original_slots.get(
                slot,
                (
                    FieldObservation(ObservationState.UNOBSERVED, ObservationReason.FIELD_ELEMENT_MISSING.value),
                    False,
                    "",
                ),
            )
            compare_observation, compare_candidate, compare_element_id = compare_slots.get(
                slot,
                (
                    FieldObservation(ObservationState.UNOBSERVED, ObservationReason.FIELD_ELEMENT_MISSING.value),
                    False,
                    "",
                ),
            )
            original_signed = original_observation.state == ObservationState.OBSERVED_VALUE
            compare_signed = compare_observation.state == ObservationState.OBSERVED_VALUE
            if original_observation.state == compare_observation.state and original_candidate == compare_candidate:
                continue
            role, field_key = slot
            if (
                not original_candidate
                and not compare_candidate
                and ObservationState.OBSERVED_VALUE not in {original_observation.state, compare_observation.state}
            ):
                continue
            transition = decide_observation_transition(
                original_observation,
                compare_observation,
                values_equal=original_signed == compare_signed,
            )
            change_decision = "CANDIDATE"
            comparison.signature_changes.append(
                {
                    "type": "ADD" if compare_signed or compare_candidate else "DELETE",
                    "element_type": SigningElementType.SIGNATURE.value,
                    "party_role": role,
                    "field_key": field_key,
                    "field_label": self._signature_slot_label(original, compare, slot),
                    "original_text": self._signature_observation_text(original_observation, original_candidate),
                    "compare_text": self._signature_observation_text(compare_observation, compare_candidate),
                    "change_decision": change_decision,
                    "decision_reason": transition.reason,
                    **original_observation.as_details("original"),
                    **compare_observation.as_details("compare"),
                    "original_element_id": original_element_id,
                    "compare_element_id": compare_element_id,
                }
            )
            self._add_review_flag(comparison, "SIGNING_SIGNATURE_CHANGE")
            if change_decision in {"CANDIDATE", "NEEDS_REVIEW"}:
                self._add_review_flag(comparison, "OBSERVATION_UNOBSERVED")

    def _compare_seals(
        self,
        original: SigningRegion,
        compare: SigningRegion,
        comparison: SigningRegionComparison,
    ) -> None:
        original_seals = self._role_scoped_elements(original, SigningElementType.SEAL)
        compare_seals = self._role_scoped_elements(compare, SigningElementType.SEAL)
        role_order = {"甲方": 0, "乙方": 1, "丙方": 2, "丁方": 3, "unknown": 4}
        scoped = bool(
            {"two_column_layout", "stacked_party_layout"}
            & set([*original.confidence_reasons, *compare.confidence_reasons])
        )
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
                original_observation = self._visual_object_observation(
                    original_element,
                    original,
                    peer_region=compare,
                    peer_element=compare_element,
                )
                compare_observation = self._visual_object_observation(
                    compare_element,
                    compare,
                    peer_region=original,
                    peer_element=original_element,
                )
                transition = decide_observation_transition(
                    original_observation,
                    compare_observation,
                    values_equal=original_element is not None and compare_element is not None,
                )
                change_decision = "CANDIDATE"
                comparison.seal_changes.append(
                    {
                        "type": change_type,
                        "element_type": SigningElementType.SEAL.value,
                        "original_text": original_text,
                        "compare_text": compare_text,
                        "original_element_id": original_element.element_id if original_element is not None else "",
                        "compare_element_id": compare_element.element_id if compare_element is not None else "",
                        "change_decision": change_decision,
                        "decision_reason": transition.reason,
                        **original_observation.as_details("original"),
                        **compare_observation.as_details("compare"),
                        **({"party_role": role, "field_label": "印章"} if scoped else {}),
                    }
                )
                self._add_review_flag(comparison, "SIGNING_SEAL_CHANGE")
                if change_decision in {"CANDIDATE", "NEEDS_REVIEW"}:
                    self._add_review_flag(comparison, "OBSERVATION_UNOBSERVED")

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
            role = str(element.raw_ref.get("party_role") or cls.role_for_bbox(region, element.bbox))
            elements.setdefault(role, []).append(element)
        return elements

    def _compare_columns(
        self,
        original: SigningRegion,
        compare: SigningRegion,
        comparison: SigningRegionComparison,
    ) -> set[str]:
        original_roles = self._column_roles(original, compare)
        compare_roles = self._column_roles(compare, original)
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
                    "change_decision": "CANDIDATE",
                    "decision_reason": "COMPARE_UNOBSERVED" if deleted else "ORIGINAL_UNOBSERVED",
                    "original_observation_state": (
                        ObservationState.OBSERVED_VALUE.value if deleted else ObservationState.UNOBSERVED.value
                    ),
                    "original_observation_reason": (
                        ObservationReason.LOCALIZED_VALUE_EVIDENCE.value
                        if deleted
                        else ObservationReason.COLUMN_OCR_MISSING.value
                    ),
                    "compare_observation_state": (
                        ObservationState.UNOBSERVED.value if deleted else ObservationState.OBSERVED_VALUE.value
                    ),
                    "compare_observation_reason": (
                        ObservationReason.COLUMN_OCR_MISSING.value
                        if deleted
                        else ObservationReason.LOCALIZED_VALUE_EVIDENCE.value
                    ),
                }
            )
            self._add_review_flag(comparison, "SIGNING_COLUMN_CHANGE")
            self._add_review_flag(comparison, "OBSERVATION_UNOBSERVED")
        return missing_roles

    @classmethod
    def _column_roles(cls, region: SigningRegion, peer: SigningRegion | None = None) -> set[str]:
        roles = {
            str(element.raw_ref.get("party_role") or "")
            for element in region.elements
            if element.element_type in {SigningElementType.FIELD, SigningElementType.PARTY_FIELD}
            and str(element.raw_ref.get("party_role") or "") not in {"", "unknown"}
        }
        roles.update(role for role, _field_key in cls._structured_fields(region, peer) if role != "unknown")
        return roles

    @classmethod
    def _structured_fields(
        cls,
        region: SigningRegion,
        peer: SigningRegion | None = None,
    ) -> dict[tuple[str, str], list[SigningElement]]:
        peer_roles: dict[tuple[str, str], set[str]] = {}
        if peer is not None:
            for element in peer.elements:
                role = str(element.raw_ref.get("party_role") or "unknown")
                field_key = str(element.raw_ref.get("field_key") or "")
                value = cls._normalized_field_value(field_key, element.text)
                if element.element_type == SigningElementType.FIELD and field_key and value and role != "unknown":
                    peer_roles.setdefault((field_key, value), set()).add(role)

        fields: dict[tuple[str, str], list[SigningElement]] = {}
        for element in region.elements:
            if element.element_type != SigningElementType.FIELD:
                continue
            field_key = str(element.raw_ref.get("field_key") or "")
            if not field_key:
                continue
            role = str(element.raw_ref.get("party_role") or "unknown")
            signature_field_keys = {"legal_representative", "authorized_representative", "signature"}
            peer_signature_fields = [
                peer_element
                for peer_element in (peer.elements if peer is not None else [])
                if peer_element.element_type == SigningElementType.FIELD
                and str(peer_element.raw_ref.get("party_role") or "unknown") == role
                and str(peer_element.raw_ref.get("field_key") or "") in signature_field_keys
            ]
            peer_has_same_key = any(
                str(peer_element.raw_ref.get("field_key") or "") == field_key for peer_element in peer_signature_fields
            )
            peer_has_blank_alternative = any(
                str(peer_element.raw_ref.get("field_key") or "") != field_key and not peer_element.text.strip()
                for peer_element in peer_signature_fields
            )
            if (
                field_key in signature_field_keys
                and not element.text.strip()
                and not peer_has_same_key
                and peer_has_blank_alternative
            ):
                # Empty legal/authorized-representative labels describe the same
                # signing slot.  Seal OCR can make one side look like a different
                # label family; pair them first so seal-occlusion filtering can
                # decide whether the label change is real.
                field_key = "signature"
            if role == "unknown":
                matching_roles = peer_roles.get(
                    (field_key, cls._normalized_field_value(field_key, element.text)), set()
                )
                if len(matching_roles) == 1:
                    role = next(iter(matching_roles))
            fields.setdefault((role, field_key), []).append(element)
        return fields

    @staticmethod
    def _narrow_ocr_continuation_noise(
        original_element: SigningElement | None,
        compare_element: SigningElement | None,
        original_value: str,
        compare_value: str,
    ) -> bool:
        if original_element is None or compare_element is None:
            return False
        short, long = sorted((original_value, compare_value), key=len)
        if not short or not long.startswith(short):
            return False
        suffix = long[len(short) :]
        if not re.fullmatch(r"[A-Za-z0-9]{1,2}", suffix):
            return False
        longer = compare_element if len(compare_value) > len(original_value) else original_element
        value_bboxes = longer.raw_ref.get("value_bboxes") or []
        if not value_bboxes:
            return False
        trailing = BBox.model_validate(value_bboxes[-1])
        width = max(0.0, trailing.x1 - trailing.x0)
        height = max(1.0, trailing.y1 - trailing.y0)
        return width / height <= 0.35

    @staticmethod
    def _has_structured_fields(region: SigningRegion) -> bool:
        return any(element.element_type == SigningElementType.FIELD for element in region.elements)

    @staticmethod
    def _normalized_field_value(field_key: str, value: str) -> str:
        decoded = html.unescape(value or "")
        markup = re.search(r"</?(?:html|body|table|thead|tbody|tr|th|td)\b", decoded, re.IGNORECASE)
        if markup is not None:
            decoded = decoded[: markup.start()]
        decoded = re.sub(r"<[^>]+>", "", decoded)
        normalized = unicodedata.normalize("NFKC", decoded)
        normalized = re.sub(r"\s+", "", normalized).replace(":", "：")
        if field_key == "date":
            parts = SigningRegionComparator._date_parts(normalized)
            if parts is not None:
                return "-".join(str(item) for item in parts)
            if normalized and re.fullmatch(r"[年月日]+", normalized):
                return ""
        if field_key in {"account", "phone", "fax", "credit_code"}:
            normalized = re.sub(r"[-‐‑‒–—_]", "", normalized)
        return normalized

    @staticmethod
    def _date_parts(value: str) -> tuple[int, int, int] | None:
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))
        match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", compact)
        if match is None:
            match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})[08oO](?!\d)", compact)
        if match is None:
            match = re.search(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})", compact)
        if match is None:
            return None
        parts = tuple(int(item) for item in match.groups())
        try:
            date(*parts)
        except ValueError:
            return None
        return parts

    @staticmethod
    def _changed_date_components(
        original_element: SigningElement | None,
        compare_element: SigningElement | None,
    ) -> list[str]:
        if original_element is None or compare_element is None:
            return []
        original = original_element.raw_ref.get("date_components") or {}
        compare = compare_element.raw_ref.get("date_components") or {}
        if not isinstance(original, dict) or not isinstance(compare, dict) or not (original or compare):
            return []

        def normalized(value: object) -> str:
            return re.sub(r"[\s_＿]", "", unicodedata.normalize("NFKC", str(value or "")))

        return [
            name for name in ("year", "month", "day") if normalized(original.get(name)) != normalized(compare.get(name))
        ]

    @classmethod
    def _display_date_value(cls, value: str) -> str:
        parts = cls._date_parts(value)
        if parts is None:
            return value
        if not re.search(r"[年月日]", value):
            return value
        year, month, day = parts
        return f"{year}年{month}月{day}日"

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
    def _is_seal_occluded_signature_label_conflict(
        cls,
        field_key: str,
        original_element: SigningElement | None,
        compare_element: SigningElement | None,
        original_region: SigningRegion,
        compare_region: SigningRegion,
    ) -> bool:
        if (
            field_key not in {"signature", "legal_representative", "authorized_representative"}
            or original_element is None
            or compare_element is None
        ):
            return False
        if original_element.text.strip() or compare_element.text.strip():
            return False
        labels = (
            cls._normalized_field_label(cls._field_label(original_element)),
            cls._normalized_field_label(cls._field_label(compare_element)),
        )
        if not all(cls._looks_like_signature_label(label) for label in labels) or labels[0] == labels[1]:
            return False
        occluded = (
            cls._seal_overlaps_element(original_region, original_element),
            cls._seal_overlaps_element(compare_region, compare_element),
        )
        if not any(occluded):
            return False
        if field_key == "signature":
            for index, label in enumerate(labels):
                if (occluded[index] and re.fullmatch(r"签字[/\\].{0,3}专用章", label)
                        and labels[1 - index] in {"签字/盖章", "签字\\盖章"}):
                    return True
        shorter_index = 0 if len(labels[0]) < len(labels[1]) else 1
        if labels[1 - shorter_index].startswith(labels[shorter_index]) and occluded[shorter_index]:
            return True
        stems = tuple(cls._signature_label_stem(label) for label in labels)
        return bool(min(map(len, stems)) >= 4 and (stems[0] in labels[1] or stems[1] in labels[0]) and any(occluded))

    @staticmethod
    def _looks_like_signature_label(label: str) -> bool:
        return bool(label) and bool(re.search(r"法定代表人|法人代表|授权代表|负责人|签字|签名", label))

    @staticmethod
    def _signature_label_stem(label: str) -> str:
        return re.sub(r"签字|签名|[/\\_＿—－-]", "", label or "")

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
            slots[(role, "signature")] = False

        visual_signatures = [
            element for element in region.elements if element.element_type == SigningElementType.SIGNATURE
        ]
        for element in visual_signatures:
            role = str(element.raw_ref.get("party_role") or cls.role_for_bbox(region, element.bbox))
            slots[(role, "signature")] = True
        return slots

    @classmethod
    def role_for_bbox(cls, region: SigningRegion, bbox: BBox) -> str:
        if "two_column_layout" in region.confidence_reasons:
            midpoint = cls._column_midpoint(region)
            return "甲方" if (bbox.x0 + bbox.x1) / 2 < midpoint else "乙方"
        if "stacked_party_layout" in region.confidence_reasons:
            midpoint = cls._stacked_midpoint(region)
            return "甲方" if (bbox.y0 + bbox.y1) / 2 < midpoint else "乙方"
        if region.region_role.value == "party_a":
            return "甲方"
        if region.region_role.value == "party_b":
            return "乙方"
        if region.region_role.value == "both_parties":
            midpoint = (region.bbox.x0 + region.bbox.x1) / 2
            return "甲方" if (bbox.x0 + bbox.x1) / 2 < midpoint else "乙方"
        return "unknown"

    @staticmethod
    def _stacked_midpoint(region: SigningRegion) -> float:
        fallback = (region.bbox.y0 + region.bbox.y1) / 2
        fields = [
            element.bbox
            for element in region.elements
            if element.element_type == SigningElementType.FIELD
            and str(element.raw_ref.get("field_key") or "")
            in {"legal_representative", "authorized_representative", "signature", "date"}
        ]
        centers = sorted((bbox.y0 + bbox.y1) / 2 for bbox in fields)
        if len(centers) < 2:
            return fallback
        left, right = max(zip(centers, centers[1:]), key=lambda pair: pair[1] - pair[0])
        return (left + right) / 2

    @staticmethod
    def _column_midpoint(region: SigningRegion) -> float:
        fallback = (region.bbox.x0 + region.bbox.x1) / 2
        explicit = [float(element.raw_ref["column_midpoint"]) for element in region.elements
                    if element.raw_ref.get("column_midpoint") is not None and element.page_no == region.page_no]
        if explicit:
            return sorted(explicit)[len(explicit)//2]
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
        role, _field_key = slot
        candidates: list[SigningElement] = []
        visual_signatures: list[SigningElement] = []
        for region in (original, compare):
            for element in region.elements:
                element_role = str(element.raw_ref.get("party_role") or "unknown")
                if element_role != role:
                    continue
                if element.element_type == SigningElementType.SIGNATURE:
                    visual_signatures.append(element)
                elif element.element_type == SigningElementType.FIELD:
                    key = str(element.raw_ref.get("field_key") or "")
                    label = str(element.raw_ref.get("field_label") or "")
                    if key in {"legal_representative", "authorized_representative", "signature"} or any(
                        marker in label for marker in ("签字", "签名")
                    ):
                        candidates.append(element)
        if not candidates:
            return "签字"
        if visual_signatures:
            visual = visual_signatures[0]
            candidates.sort(
                key=lambda element: max(
                    element.bbox.y0 - visual.bbox.y1,
                    visual.bbox.y0 - element.bbox.y1,
                    0.0,
                )
            )
        else:
            candidates.sort(key=lambda element: str(element.raw_ref.get("field_key") or "") == "signature")
        return str(candidates[0].raw_ref.get("field_label") or "签字")

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

    @classmethod
    def _seal_overlaps_element(cls, region: SigningRegion, element: SigningElement) -> bool:
        bboxes = [element.bbox]
        source_bbox = element.raw_ref.get("source_bbox")
        if source_bbox:
            bboxes.append(BBox.model_validate(source_bbox))
        bboxes.extend(BBox.model_validate(item) for item in element.raw_ref.get("field_bboxes", []))
        return any(cls._seal_overlaps_bbox(region, bbox, tolerance=8.0) for bbox in bboxes)

    @staticmethod
    def _bboxes_overlap(left: BBox, right: BBox, *, tolerance: float = 0.0) -> bool:
        return min(left.x1, right.x1) + tolerance > max(left.x0, right.x0) and min(left.y1, right.y1) + tolerance > max(
            left.y0,
            right.y0,
        )

    @staticmethod
    def _normalized_party_text(text: str) -> str:
        return SigningRegionComparator._normalized_party_name(text)

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
        if any(element.element_type == SigningElementType.PARTY_FIELD
               and str(element.raw_ref.get("party_role") or "") == role
               and element.raw_ref.get("content_recognition_reason") == "CONSISTENT_RED_SUPPRESSED_LOCAL_READINGS"
               for region in (original_region, compare_region) for element in region.elements):
            return False
        original_reference = cls._normalized_party_name(original_references.get(role, ""))
        compare_reference = cls._normalized_party_name(compare_references.get(role, ""))
        if original_reference and compare_reference and original_reference != compare_reference:
            return False
        reference = original_reference or compare_reference
        if not reference:
            return False
        original_name = cls._normalized_party_name(original_text)
        compare_name = cls._normalized_party_name(compare_text)
        if not (cls._region_has_visual_seal(original_region) or cls._region_has_visual_seal(compare_region)):
            return False
        if not (original_reference and compare_reference) and reference not in {original_name, compare_name}:
            return False
        return cls._party_name_matches_reference(original_name, reference) and cls._party_name_matches_reference(
            compare_name, reference
        )

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
