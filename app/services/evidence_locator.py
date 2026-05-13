from __future__ import annotations

from app.models import DiffItem


class EvidenceLocator:
    def locate(self, diffs: list[DiffItem]) -> list[DiffItem]:
        for diff in diffs:
            snippet_pairs = [
                (diff.original_evidence, diff.original_snippet),
                (diff.compare_evidence, diff.compare_snippet),
            ]
            for evidence_list, snippet in snippet_pairs:
                if not evidence_list:
                    continue
                for evidence in evidence_list:
                    if snippet and snippet in evidence.text:
                        evidence.method = "exact_text"
                    elif evidence.method == "block_fallback":
                        evidence.method = "block_fallback"
                    else:
                        evidence.method = "clause_fallback"
        return diffs

