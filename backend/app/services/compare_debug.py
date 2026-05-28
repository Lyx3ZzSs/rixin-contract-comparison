from __future__ import annotations

from pathlib import Path
from typing import Any

from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.models import Clause, ClausePair, DiffItem, DocumentProfile
from app.utils.json_utils import to_jsonable


class CompareDebugWriter:
    """Persist compact JSON diagnostics beside task JSON while accuracy is being tuned."""

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.artifact_store = artifact_store

    def write_profiles(
        self,
        task_id: str,
        original: DocumentProfile | None,
        compare: DocumentProfile | None,
    ) -> str:
        return str(
            self._write_json(
                task_id,
                "document_profiles.json",
                {
                    "original": self._dump_model(original),
                    "compare": self._dump_model(compare),
                },
            )
        )

    def write_clauses(self, task_id: str, side: str, clauses: list[Clause]) -> str:
        payload = [self._clause_summary(clause) for clause in clauses]
        return str(self._write_json(task_id, f"clauses_{side}.json", payload))

    def write_matches(self, task_id: str, pairs: list[ClausePair]) -> str:
        payload = []
        for pair in pairs:
            payload.append(
                {
                    "original_clause_id": pair.original.clause_id if pair.original else None,
                    "compare_clause_id": pair.compare.clause_id if pair.compare else None,
                    "original_clause_no": pair.original.clause_no if pair.original else "",
                    "compare_clause_no": pair.compare.clause_no if pair.compare else "",
                    "score": round(pair.score, 2),
                    "match_method": pair.match_method,
                    "score_details": pair.score_details,
                    "match_candidates": pair.match_candidates[:5],
                }
            )
        return str(self._write_json(task_id, "clause_matches.json", payload))

    def write_diffs(self, task_id: str, diffs: list[DiffItem]) -> str:
        payload = []
        for diff in diffs:
            payload.append(
                {
                    "diff_id": diff.diff_id,
                    "diff_type": diff.diff_type,
                    "source_type": diff.source_type,
                    "title": diff.title,
                    "clause_no": diff.clause_no,
                    "match_score": diff.match_score,
                    "match_method": diff.match_method,
                    "match_score_details": diff.match_score_details,
                    "review_flags": diff.review_flags,
                    "original_snippet": diff.original_snippet,
                    "compare_snippet": diff.compare_snippet,
                    "original_evidence": [self._evidence_summary(item) for item in diff.original_evidence],
                    "compare_evidence": [self._evidence_summary(item) for item in diff.compare_evidence],
                }
            )
        return str(self._write_json(task_id, "diff_decisions.json", payload))

    def _write_json(self, task_id: str, filename: str, payload: Any) -> Path:
        path = self.artifact_store.debug_json_path(task_id, filename)
        return self.artifact_store.write_json(path, payload)

    def _debug_root(self, task_id: str) -> Path:
        return self.artifact_store.task_dir("debug", task_id)

    def _dump_model(self, model: Any) -> dict[str, Any] | None:
        if model is None:
            return None
        return to_jsonable(model)

    def _clause_summary(self, clause: Clause) -> dict[str, Any]:
        return {
            "clause_id": clause.clause_id,
            "clause_no": clause.clause_no,
            "title": clause.title,
            "page_numbers": clause.page_numbers,
            "source_block_ids": clause.source_block_ids,
            "segmentation_reason": clause.segmentation_reason,
            "segmentation_confidence": clause.segmentation_confidence,
            "text_length": len(clause.text),
            "normalized_length": len(clause.normalized_text),
            "text_preview": clause.text[:500],
        }

    def _evidence_summary(self, evidence: Any) -> dict[str, Any]:
        return {
            "page_no": evidence.page_no,
            "bbox": to_jsonable(evidence.bbox),
            "method": evidence.method,
            "highlight_type": evidence.highlight_type,
            "confidence": evidence.confidence,
            "evidence_quality": evidence.evidence_quality,
            "text": evidence.text[:160],
        }
