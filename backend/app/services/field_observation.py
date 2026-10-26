from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ObservationState(str, Enum):
    OBSERVED_VALUE = "OBSERVED_VALUE"
    OBSERVED_BLANK = "OBSERVED_BLANK"
    UNOBSERVED = "UNOBSERVED"


class ObservationReason(str, Enum):
    LOCALIZED_VALUE_EVIDENCE = "LOCALIZED_VALUE_EVIDENCE"
    LOCALIZED_PRIMARY_VALUE = "LOCALIZED_PRIMARY_VALUE"
    RELIABLE_VISUAL_OBJECT = "RELIABLE_VISUAL_OBJECT"
    CLEAR_LOCALIZED_VALUE_ROI = "CLEAR_LOCALIZED_VALUE_ROI"
    CLEAR_LOCALIZED_CELL_ROI = "CLEAR_LOCALIZED_CELL_ROI"
    INFERRED_CLEAR_CELL_ROI = "INFERRED_CLEAR_CELL_ROI"
    CLEAR_VISUAL_OBJECT_ROI = "CLEAR_VISUAL_OBJECT_ROI"
    FIELD_ELEMENT_MISSING = "FIELD_ELEMENT_MISSING"
    FIELD_ROI_NOT_FOUND = "FIELD_ROI_NOT_FOUND"
    SEAL_OCCLUSION = "SEAL_OCCLUSION"
    HANDWRITING_OCCLUSION = "HANDWRITING_OCCLUSION"
    CROSS_COLUMN_EVIDENCE_REJECTED = "CROSS_COLUMN_EVIDENCE_REJECTED"
    LOW_VISUAL_COVERAGE = "LOW_VISUAL_COVERAGE"
    OCR_EXTRACTION_FAILED = "OCR_EXTRACTION_FAILED"
    PARTY_ATTRIBUTION_UNRELIABLE = "PARTY_ATTRIBUTION_UNRELIABLE"
    COLUMN_OCR_MISSING = "COLUMN_OCR_MISSING"
    VISUAL_OBJECT_NOT_DETECTED = "VISUAL_OBJECT_NOT_DETECTED"
    VISUAL_OBJECT_CONFLICT = "VISUAL_OBJECT_CONFLICT"
    BUSINESS_CONTENT_REMOVED_OR_COVERED = "BUSINESS_CONTENT_REMOVED_OR_COVERED"


@dataclass(frozen=True)
class FieldObservation:
    state: ObservationState
    reason: str

    def as_details(self, side: str) -> dict[str, str]:
        return {
            f"{side}_observation_state": self.state.value,
            f"{side}_observation_reason": self.reason,
        }


@dataclass(frozen=True)
class ObservationTransition:
    change_decision: str
    reason: str
    fill_state_change: str = ""


def decide_observation_transition(
    original: FieldObservation,
    compare: FieldObservation,
    *,
    values_equal: bool,
    content_reliable: bool = True,
) -> ObservationTransition:
    """Apply the business observation matrix without discarding candidates."""
    if (
        original.state == ObservationState.OBSERVED_VALUE
        and compare.state == ObservationState.UNOBSERVED
        and compare.reason == ObservationReason.BUSINESS_CONTENT_REMOVED_OR_COVERED.value
        and content_reliable
    ):
        return ObservationTransition(
            "CONFIRMED",
            ObservationReason.BUSINESS_CONTENT_REMOVED_OR_COVERED.value,
            "cleared",
        )
    if ObservationState.UNOBSERVED in {original.state, compare.state}:
        if original.state == ObservationState.UNOBSERVED and compare.state == ObservationState.UNOBSERVED:
            reason = "BOTH_SIDES_UNOBSERVED"
        elif original.state == ObservationState.UNOBSERVED:
            reason = "ORIGINAL_UNOBSERVED"
        else:
            reason = "COMPARE_UNOBSERVED"
        return ObservationTransition("NEEDS_REVIEW", reason)

    if original.state == ObservationState.OBSERVED_BLANK and compare.state == ObservationState.OBSERVED_VALUE:
        return ObservationTransition("CONFIRMED", "OBSERVED_BLANK_TO_VALUE", "filled")
    if original.state == ObservationState.OBSERVED_VALUE and compare.state == ObservationState.OBSERVED_BLANK:
        return ObservationTransition("CONFIRMED", "OBSERVED_VALUE_TO_BLANK", "cleared")
    if original.state == ObservationState.OBSERVED_BLANK and compare.state == ObservationState.OBSERVED_BLANK:
        return ObservationTransition("CONFIRMED", "BOTH_SIDES_OBSERVED_BLANK")
    if original.state == ObservationState.OBSERVED_VALUE and compare.state == ObservationState.OBSERVED_VALUE:
        if values_equal:
            return ObservationTransition("CONFIRMED", "OBSERVED_VALUES_EQUAL")
        if content_reliable:
            return ObservationTransition("CONFIRMED", "OBSERVED_VALUES_DIFFER")
        return ObservationTransition("NEEDS_REVIEW", "OBSERVED_VALUE_CONTENT_UNCERTAIN")
    return ObservationTransition("NEEDS_REVIEW", "OBSERVATION_STATE_MATRIX_FALLBACK")
