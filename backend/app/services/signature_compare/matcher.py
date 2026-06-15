from __future__ import annotations

from difflib import SequenceMatcher

from app.services.signature_compare import normalizer
from app.services.signature_compare.types import SignatureField, SignatureMatch


class SignatureFieldMatcher:
    def match(self, original: list[SignatureField], compare: list[SignatureField]) -> list[SignatureMatch]:
        matches: list[SignatureMatch] = []
        used_compare: set[int] = set()
        for orig in original:
            best_idx = None
            best_score = 0.0
            best_method = "unmatched"
            for idx, comp in enumerate(compare):
                if idx in used_compare:
                    continue
                score, method = self._field_match_score(orig, comp)
                if score > best_score:
                    best_idx = idx
                    best_score = score
                    best_method = method
            if best_idx is not None and best_score >= 0.68:
                used_compare.add(best_idx)
                matches.append(SignatureMatch(orig, compare[best_idx], best_score, best_method))
            else:
                matches.append(SignatureMatch(orig, None, 0.0, "unmatched_original"))
        for idx, comp in enumerate(compare):
            if idx not in used_compare:
                matches.append(SignatureMatch(None, comp, 0.0, "unmatched_compare"))
        return matches

    @staticmethod
    def _field_match_score(left: SignatureField, right: SignatureField) -> tuple[float, str]:
        if left.party_role == right.party_role and left.field_key == right.field_key:
            if SignatureFieldMatcher._risky_role(left) or SignatureFieldMatcher._risky_role(right):
                if not SignatureFieldMatcher._same_column_zone(left, right):
                    return 0.52, "party_field_column_conflict"
                return 0.82, "party_field_low_confidence"
            return 1.0, "party_field_exact"
        if left.field_key == right.field_key:
            if not SignatureFieldMatcher._same_column_zone(left, right):
                return 0.0, "field_column_mismatch"
            page_score = 1.0 if left.page_no == right.page_no else 0.85
            position_score = normalizer.position_similarity(left.bbox, right.bbox)
            return 0.72 + 0.14 * page_score + 0.14 * position_score, "field_position"
        label_score = SequenceMatcher(None, normalizer.compact(left.field_label), normalizer.compact(right.field_label)).ratio()
        if label_score >= 0.8:
            return 0.68 + 0.2 * label_score + 0.12 * normalizer.position_similarity(left.bbox, right.bbox), "label_similarity"
        return 0.0, "unmatched"

    @staticmethod
    def _risky_role(field: SignatureField) -> bool:
        return bool(
            field.party_role.startswith("unknown")
            or any(
                flag in field.review_flags
                for flag in {
                    "SIGNATURE_ROLE_INFERRED_BY_POSITION",
                    "SIGNATURE_COLUMN_ROLE_CONFLICT",
                    "SIGNATURE_FIELD_SOURCE_CONFLICT",
                }
            )
        )

    @staticmethod
    def _same_column_zone(left: SignatureField, right: SignatureField) -> bool:
        if left.column_zone == "unknown" or right.column_zone == "unknown":
            return True
        return left.column_zone == right.column_zone
