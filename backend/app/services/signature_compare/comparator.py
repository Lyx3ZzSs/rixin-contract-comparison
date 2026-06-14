from __future__ import annotations

from app.models import DiffItem, Document
from app.services.signature_compare.deduplicator import SignatureDeduplicator
from app.services.signature_compare.diff_builder import SignatureDiffBuilder
from app.services.signature_compare.extractor import SignatureFieldExtractor
from app.services.signature_compare.matcher import SignatureFieldMatcher
from app.services.signature_compare.types import SignatureCompareResult, SignatureField


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
