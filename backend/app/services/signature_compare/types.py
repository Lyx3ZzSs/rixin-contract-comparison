from __future__ import annotations

from dataclasses import dataclass, field

from app.models import BBox, DiffItem


@dataclass(frozen=True)
class SignatureField:
    side: str
    page_no: int
    party_role: str
    party_label: str
    field_key: str
    field_label: str
    field_value: str
    bbox: BBox
    confidence: float = 0.75
    source_method: str = "signature_field"
    source_block_ids: list[str] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)
    column_zone: str = "unknown"

    @property
    def match_key(self) -> tuple[str, str]:
        return self.party_role, self.field_key


@dataclass(frozen=True)
class SignatureFieldCandidate:
    side: str
    page_no: int
    party_role: str
    party_label: str
    field_key: str
    field_label: str
    raw_value: str
    normalized_value: str
    bbox: BBox | None
    confidence: float
    source_method: str
    source_block_ids: list[str] = field(default_factory=list)
    status: str = "accepted"
    reject_reason: str = ""
    column_zone: str = "unknown"


@dataclass(frozen=True)
class SignatureMatch:
    original: SignatureField | None
    compare: SignatureField | None
    score: float
    match_method: str


@dataclass
class SignatureExtractionQuality:
    side: str
    field_count: int
    critical_field_count: int
    party_roles: list[str]
    inferred_role_count: int
    conflict_count: int
    accepted_candidate_count: int
    rejected_candidate_count: int
    unreliable: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PendingSignatureLabel:
    party_role: str
    party_label: str
    field_key: str
    field_label: str
    bbox: BBox | None
    source_block_ids: list[str]
    column_zone: str = "unknown"


@dataclass
class SignatureCompareResult:
    original_candidates: list[SignatureFieldCandidate] = field(default_factory=list)
    compare_candidates: list[SignatureFieldCandidate] = field(default_factory=list)
    original_fields: list[SignatureField] = field(default_factory=list)
    compare_fields: list[SignatureField] = field(default_factory=list)
    matches: list[SignatureMatch] = field(default_factory=list)
    diffs: list[DiffItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    original_quality: SignatureExtractionQuality | None = None
    compare_quality: SignatureExtractionQuality | None = None
    suppressed_table_diff_ids: list[str] = field(default_factory=list)
    suppressed_seal_diff_ids: list[str] = field(default_factory=list)

    def to_debug_payload(self) -> dict:
        accepted_candidates = [
            candidate
            for candidate in [*self.original_candidates, *self.compare_candidates]
            if candidate.status == "accepted"
        ]
        rejected_candidates = [
            candidate
            for candidate in [*self.original_candidates, *self.compare_candidates]
            if candidate.status != "accepted"
        ]
        low_confidence_diffs = [
            diff.diff_id
            for diff in self.diffs
            if "LOW_CONFIDENCE_SIGNATURE_EVIDENCE" in diff.review_flags
        ]
        return {
            "metrics": {
                "original_candidate_count": len(self.original_candidates),
                "compare_candidate_count": len(self.compare_candidates),
                "accepted_candidate_count": len(accepted_candidates),
                "rejected_candidate_count": len(rejected_candidates),
                "original_field_count": len(self.original_fields),
                "compare_field_count": len(self.compare_fields),
                "signature_diff_count": len(self.diffs),
                "suppressed_table_diff_count": len(self.suppressed_table_diff_ids),
                "suppressed_seal_diff_count": len(self.suppressed_seal_diff_ids),
                "low_confidence_signature_count": len(low_confidence_diffs),
                "original_extraction_unreliable": bool(self.original_quality and self.original_quality.unreliable),
                "compare_extraction_unreliable": bool(self.compare_quality and self.compare_quality.unreliable),
            },
            "original_quality": _quality_debug(self.original_quality),
            "compare_quality": _quality_debug(self.compare_quality),
            "original_candidates": [_candidate_debug(candidate) for candidate in self.original_candidates],
            "compare_candidates": [_candidate_debug(candidate) for candidate in self.compare_candidates],
            "rejected_candidates": [_candidate_debug(candidate) for candidate in rejected_candidates],
            "original_fields": [_field_debug(field) for field in self.original_fields],
            "compare_fields": [_field_debug(field) for field in self.compare_fields],
            "matches": [
                {
                    "original": _field_debug(match.original) if match.original else None,
                    "compare": _field_debug(match.compare) if match.compare else None,
                    "score": round(match.score, 3),
                    "match_method": match.match_method,
                }
                for match in self.matches
            ],
            "diff_ids": [diff.diff_id for diff in self.diffs],
            "low_confidence_diff_ids": low_confidence_diffs,
            "suppressed_table_diff_ids": self.suppressed_table_diff_ids,
            "suppressed_seal_diff_ids": self.suppressed_seal_diff_ids,
            "warnings": self.warnings,
        }


def _field_debug(field: SignatureField) -> dict:
    return {
        "side": field.side,
        "page_no": field.page_no,
        "party_role": field.party_role,
        "party_label": field.party_label,
        "field_key": field.field_key,
        "field_label": field.field_label,
        "field_value": field.field_value,
        "confidence": field.confidence,
        "source_method": field.source_method,
        "source_block_ids": field.source_block_ids,
        "review_flags": field.review_flags,
        "column_zone": field.column_zone,
    }


def _candidate_debug(candidate: SignatureFieldCandidate) -> dict:
    return {
        "side": candidate.side,
        "page_no": candidate.page_no,
        "party_role": candidate.party_role,
        "party_label": candidate.party_label,
        "field_key": candidate.field_key,
        "field_label": candidate.field_label,
        "raw_value": candidate.raw_value,
        "normalized_value": candidate.normalized_value,
        "confidence": candidate.confidence,
        "source_method": candidate.source_method,
        "source_block_ids": candidate.source_block_ids,
        "status": candidate.status,
        "reject_reason": candidate.reject_reason,
        "column_zone": candidate.column_zone,
    }


def _quality_debug(quality: SignatureExtractionQuality | None) -> dict | None:
    if quality is None:
        return None
    return {
        "side": quality.side,
        "field_count": quality.field_count,
        "critical_field_count": quality.critical_field_count,
        "party_roles": quality.party_roles,
        "inferred_role_count": quality.inferred_role_count,
        "conflict_count": quality.conflict_count,
        "accepted_candidate_count": quality.accepted_candidate_count,
        "rejected_candidate_count": quality.rejected_candidate_count,
        "unreliable": quality.unreliable,
        "reasons": quality.reasons,
    }


@dataclass
class SignatureExtractionResult:
    fields: list[SignatureField] = field(default_factory=list)
    candidates: list[SignatureFieldCandidate] = field(default_factory=list)
