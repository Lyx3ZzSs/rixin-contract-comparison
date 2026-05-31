from __future__ import annotations

from app.models import ClausePair, DiffItem, TextRange

from app.services.diff import builder, overlap_dedup, range_refiner


class DiffEngine:
    """Facade for clause diff computation — delegates to specialized submodules."""

    def build_diffs(self, pairs: list[ClausePair], start_index: int = 1) -> list[DiffItem]:
        return builder.build_diffs(pairs, start_index)

    def deduplicate_overlaps(self, diffs: list[DiffItem]) -> list[DiffItem]:
        return overlap_dedup.deduplicate_overlaps(diffs)

    def _changed_snippets(self, left: str, right: str) -> tuple[str, str, list[TextRange], list[TextRange]]:
        return range_refiner.changed_snippets(left, right)
