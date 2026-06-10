from __future__ import annotations

from app.models import DiffItem, Document
from app.services.normalizer import TextNormalizer

from .types import CoverField


class CoverMetadataComparator:
    def __init__(self, normalizer: TextNormalizer | None = None) -> None:
        self.normalizer = normalizer or TextNormalizer()

    def build_diffs(self, original: Document, compare: Document, start_index: int = 1) -> list[DiffItem]:
        from .comparator import build_diffs
        return build_diffs(self.normalizer, original, compare, start_index)

    def extract(self, document: Document) -> dict[str, CoverField]:
        from .extractor import extract
        return extract(self.normalizer, document)

    def consumed_block_ids(self, document: Document) -> set[str]:
        from .extractor import _extract_cover
        return set(_extract_cover(self.normalizer, document).consumed_block_ids)

    # Exposed for test compatibility
    def _normalize_extra(self, value: str) -> str:
        from .patterns import normalize_extra
        return normalize_extra(value)
