from __future__ import annotations

from app.models import DiffItem, Document
from app.services.signature_compare.deduplicator import SignatureDeduplicator
from app.services.signature_compare.diff_builder import SignatureDiffBuilder
from app.services.signature_compare.extractor import SignatureFieldExtractor
from app.services.signature_compare.matcher import SignatureFieldMatcher
from app.services.signature_compare.types import SignatureCompareResult, SignatureExtractionQuality, SignatureField


class SignatureComparator:
    """Facade for signature-page comparison.

    Keep this public API stable; pipeline code imports this class from
    ``app.services.signature_compare``.
    """

    def __init__(self) -> None:
        self._extractor = SignatureFieldExtractor()
        self._matcher = SignatureFieldMatcher()
        self._diff_builder = SignatureDiffBuilder()
        self._deduplicator = SignatureDeduplicator()

    def compare(
        self,
        original: Document,
        compare: Document,
        table_diffs: list[DiffItem] | None = None,
        seal_diffs: list[DiffItem] | None = None,
        start_index: int = 1,
    ) -> SignatureCompareResult:
        original_extraction = self._extractor.extract(original, "original")
        compare_extraction = self._extractor.extract(compare, "compare")
        original_fields = self._deduplicator.deduplicate_fields(original_extraction.fields)
        compare_fields = self._deduplicator.deduplicate_fields(compare_extraction.fields)
        original_candidates = [
            *original_extraction.candidates,
            *self._deduplicator.duplicate_rejection_candidates(original_extraction.fields, original_fields),
        ]
        compare_candidates = [
            *compare_extraction.candidates,
            *self._deduplicator.duplicate_rejection_candidates(compare_extraction.fields, compare_fields),
        ]
        original_quality = self._quality("original", original_fields, original_candidates)
        compare_quality = self._quality("compare", compare_fields, compare_candidates)
        self._mark_pairwise_unreliable(original_quality, compare_quality)
        if original_quality.unreliable or compare_quality.unreliable:
            matches = []
            diffs = [
                self._diff_builder.build_unreliable_extraction_diff(
                    original_quality,
                    compare_quality,
                    start_index,
                )
            ]
        else:
            matches = self._matcher.match(original_fields, compare_fields)
            diffs = self._diff_builder.build_diffs(matches, start_index)
        suppressed = self.suppressed_table_diff_ids(table_diffs or [], diffs)
        suppressed_seals = self.suppressed_seal_diff_ids(seal_diffs or [], diffs)
        return SignatureCompareResult(
            original_candidates=original_candidates,
            compare_candidates=compare_candidates,
            original_fields=original_fields,
            compare_fields=compare_fields,
            matches=matches,
            diffs=diffs,
            original_quality=original_quality,
            compare_quality=compare_quality,
            suppressed_table_diff_ids=suppressed,
            suppressed_seal_diff_ids=suppressed_seals,
        )

    def extract_fields(self, document: Document, side: str) -> list[SignatureField]:
        return self._deduplicator.deduplicate_fields(
            self._extractor.extract_fields(document, side)
        )

    def remove_suppressed_table_diffs(
        self,
        table_diffs: list[DiffItem],
        signature_diffs: list[DiffItem],
    ) -> list[DiffItem]:
        return self._deduplicator.remove_suppressed_table_diffs(table_diffs, signature_diffs)

    def remove_suppressed_non_body_diffs(
        self,
        table_diffs: list[DiffItem],
        seal_diffs: list[DiffItem],
        signature_diffs: list[DiffItem],
    ) -> tuple[list[DiffItem], list[DiffItem]]:
        return self._deduplicator.remove_suppressed_non_body_diffs(table_diffs, seal_diffs, signature_diffs)

    def suppressed_table_diff_ids(
        self,
        table_diffs: list[DiffItem],
        signature_diffs: list[DiffItem],
    ) -> list[str]:
        return self._deduplicator.suppressed_table_diff_ids(table_diffs, signature_diffs)

    def suppressed_seal_diff_ids(
        self,
        seal_diffs: list[DiffItem],
        signature_diffs: list[DiffItem],
    ) -> list[str]:
        return self._deduplicator.suppressed_seal_diff_ids(seal_diffs, signature_diffs)

    @staticmethod
    def _quality(
        side: str,
        fields: list[SignatureField],
        candidates: list,
    ) -> SignatureExtractionQuality:
        critical_keys = {
            "company_name",
            "address",
            "legal_representative",
            "phone",
            "fax",
            "bank",
            "account",
            "tax_no",
            "postcode",
        }
        party_roles = sorted({field.party_role for field in fields if not field.party_role.startswith("unknown")})
        critical_count = len({field.field_key for field in fields if field.field_key in critical_keys})
        inferred_count = sum(
            1
            for field in fields
            if field.party_role.startswith("unknown") or "SIGNATURE_ROLE_INFERRED_BY_POSITION" in field.review_flags
        )
        conflict_count = sum(
            1
            for field in fields
            if any(
                flag in field.review_flags
                for flag in {"SIGNATURE_FIELD_SOURCE_CONFLICT", "SIGNATURE_COLUMN_ROLE_CONFLICT"}
            )
        )
        accepted_count = sum(1 for candidate in candidates if getattr(candidate, "status", "") == "accepted")
        rejected_count = len(candidates) - accepted_count
        reasons: list[str] = []
        if fields and len(fields) >= 8 and critical_count <= 2:
            reasons.append("critical_signature_fields_missing")
        if len(fields) >= 8 and inferred_count / max(len(fields), 1) >= 0.6:
            reasons.append("signature_roles_mostly_inferred")
        if len(fields) >= 8 and conflict_count / max(len(fields), 1) >= 0.35:
            reasons.append("signature_field_source_conflicts")
        return SignatureExtractionQuality(
            side=side,
            field_count=len(fields),
            critical_field_count=critical_count,
            party_roles=party_roles,
            inferred_role_count=inferred_count,
            conflict_count=conflict_count,
            accepted_candidate_count=accepted_count,
            rejected_candidate_count=rejected_count,
            unreliable=False,
            reasons=reasons,
        )

    @staticmethod
    def _mark_pairwise_unreliable(
        original: SignatureExtractionQuality,
        compare: SignatureExtractionQuality,
    ) -> None:
        original_reasons = list(original.reasons)
        compare_reasons = list(compare.reasons)
        if original.field_count >= 5 and compare.field_count < original.field_count * 0.65:
            compare_reasons.append("field_count_much_lower_than_original")
        if compare.field_count >= 5 and original.field_count < compare.field_count * 0.65:
            original_reasons.append("field_count_much_lower_than_compare")
        if original.critical_field_count >= 5 and compare.critical_field_count <= 3:
            compare_reasons.append("critical_field_coverage_much_lower_than_original")
        if compare.critical_field_count >= 5 and original.critical_field_count <= 3:
            original_reasons.append("critical_field_coverage_much_lower_than_compare")
        original.reasons = list(dict.fromkeys(original_reasons))
        compare.reasons = list(dict.fromkeys(compare_reasons))
        original.unreliable = bool(original.reasons and (original.field_count >= 4 or compare.field_count >= 5))
        compare.unreliable = bool(compare.reasons and (compare.field_count >= 4 or original.field_count >= 5))
