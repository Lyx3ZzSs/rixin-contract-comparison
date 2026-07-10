from __future__ import annotations

from pathlib import Path
from typing import Any
from collections import Counter

from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.models import Clause, ClausePair, DiffItem, DocumentProfile, LayoutQualityReport
from app.services.clause_numbering import ClauseNumberParser
from app.utils.json_utils import to_jsonable


class CompareDebugWriter:
    """Persist compact JSON diagnostics beside task JSON while accuracy is being tuned."""

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.artifact_store = artifact_store
        self.number_parser = ClauseNumberParser()

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

    def write_native_heading_repair(
        self,
        task_id: str,
        original: dict[str, Any],
        compare: dict[str, Any],
    ) -> str:
        return str(
            self._write_json(
                task_id,
                "native_heading_repair.json",
                {"original": original, "compare": compare},
            )
        )

    def write_repeated_overlay_filter(
        self,
        task_id: str,
        original: dict[str, Any],
        compare: dict[str, Any],
    ) -> str:
        return str(
            self._write_json(
                task_id,
                "repeated_overlay_filter.json",
                {"original": original, "compare": compare},
            )
        )

    def write_clauses(self, task_id: str, side: str, clauses: list[Clause]) -> str:
        payload = [self._clause_summary(clause) for clause in clauses]
        return str(self._write_json(task_id, f"clauses_{side}.json", payload))

    def write_layout_quality(
        self,
        task_id: str,
        original: LayoutQualityReport | None,
        compare: LayoutQualityReport | None,
    ) -> str:
        return str(
            self._write_json(
                task_id,
                "layout_quality.json",
                {
                    "original": self._dump_model(original),
                    "compare": self._dump_model(compare),
                },
            )
        )

    def write_document_preparation(self, task_id: str, decisions: list[dict[str, Any]]) -> str:
        return str(self._write_json(task_id, "document_preparation.json", {"decisions": decisions}))

    def write_document_understanding(self, task_id: str, payload: dict[str, Any]) -> str:
        return str(self._write_json(task_id, "document_understanding.json", payload))

    def write_section_outline(self, task_id: str, original: list[Clause], compare: list[Clause]) -> str:
        return str(
            self._write_json(
                task_id,
                "section_outline.json",
                {
                    "original": self._section_outline(original),
                    "compare": self._section_outline(compare),
                },
            )
        )

    def write_clause_split_quality(self, task_id: str, original: list[Clause], compare: list[Clause]) -> str:
        return str(
            self._write_json(
                task_id,
                "clause_split_quality.json",
                {
                    "original": self._clause_split_quality(original),
                    "compare": self._clause_split_quality(compare),
                },
            )
        )

    def write_diff_quality(self, task_id: str, decisions: list[dict[str, Any]]) -> str:
        return str(self._write_json(task_id, "diff_quality.json", {"decisions": decisions}))

    def write_ocr_quality(self, task_id: str, summary) -> str:
        return str(self._write_json(task_id, "ocr_quality.json", to_jsonable(summary)))

    def write_ocr_remediation(self, task_id: str, summary) -> str:
        return str(self._write_json(task_id, "ocr_remediation.json", to_jsonable(summary)))

    def write_model_routing(self, task_id: str, summary) -> str:
        return str(self._write_json(task_id, "ocr_model_routing.json", to_jsonable(summary)))

    def write_table_repair(self, task_id: str, payload: dict[str, Any]) -> str:
        return str(self._write_json(task_id, "table_repair.json", payload))

    def write_signing_region(self, task_id: str, payload: dict[str, Any]) -> str:
        return str(self._write_json(task_id, "signing_region.json", payload))

    def write_matches(self, task_id: str, pairs: list[ClausePair]) -> str:
        payload = []
        for pair in pairs:
            payload.append(
                {
                    "original_clause_id": pair.original.clause_id if pair.original else None,
                    "compare_clause_id": pair.compare.clause_id if pair.compare else None,
                    "original_clause_no": pair.original.clause_no if pair.original else "",
                    "compare_clause_no": pair.compare.clause_no if pair.compare else "",
                    "original_clause_key": pair.original.clause_key if pair.original else "",
                    "compare_clause_key": pair.compare.clause_key if pair.compare else "",
                    "original_section_type": pair.original.section_type if pair.original else "",
                    "compare_section_type": pair.compare.section_type if pair.compare else "",
                    "original_section_path": pair.original.section_path if pair.original else [],
                    "compare_section_path": pair.compare.section_path if pair.compare else [],
                    "score": round(pair.score, 2),
                    "match_method": pair.match_method,
                    "match_confidence": pair.match_confidence,
                    "score_details": pair.score_details,
                    "match_candidates": pair.match_candidates[:5],
                }
            )
        return str(self._write_json(task_id, "clause_matches.json", payload))

    def write_match_matrix_summary(self, task_id: str, pairs: list[ClausePair]) -> str:
        method_counts: Counter[str] = Counter(pair.match_method for pair in pairs)
        confidence_counts: Counter[str] = Counter(pair.match_confidence for pair in pairs if pair.match_confidence)
        section_counts: Counter[str] = Counter()
        alignment_risk_flag_counts: Counter[str] = Counter()
        low_confidence_alignment_count = 0
        low_confidence = []
        for pair in pairs:
            section_type = (
                pair.compare.section_type
                if pair.compare is not None
                else pair.original.section_type if pair.original is not None else ""
            )
            if section_type:
                section_counts[section_type] += 1
            alignment_risk_flags = self._alignment_risk_flags(pair.score_details)
            if alignment_risk_flags:
                low_confidence_alignment_count += 1
                alignment_risk_flag_counts.update(alignment_risk_flags)
            if pair.match_confidence == "LOW":
                low_confidence.append(
                    {
                        "original_clause_id": pair.original.clause_id if pair.original else None,
                        "compare_clause_id": pair.compare.clause_id if pair.compare else None,
                        "score": round(pair.score, 2),
                        "match_method": pair.match_method,
                        "score_details": pair.score_details,
                    }
                )
        return str(
            self._write_json(
                task_id,
                "match_matrix_summary.json",
                {
                    "pair_count": len(pairs),
                    "method_counts": dict(method_counts),
                    "confidence_counts": dict(confidence_counts),
                    "section_counts": dict(section_counts),
                    "low_confidence_alignment_count": low_confidence_alignment_count,
                    "alignment_risk_flag_counts": dict(alignment_risk_flag_counts),
                    "low_confidence_pairs": low_confidence[:50],
                },
            )
        )

    def _alignment_risk_flags(self, score_details: Any) -> list[str]:
        if not isinstance(score_details, dict):
            return []
        alignment = score_details.get("alignment")
        if not isinstance(alignment, dict):
            return []
        risk_flags = alignment.get("risk_flags")
        if not isinstance(risk_flags, (list, tuple, set)):
            return []
        return [flag for flag in risk_flags if isinstance(flag, str) and flag]

    def write_diffs(self, task_id: str, diffs: list[DiffItem]) -> str:
        payload = []
        for diff in diffs:
            payload.append(
                {
                    "diff_id": diff.diff_id,
                    "diff_type": diff.diff_type,
                    "source_type": diff.source_type,
                    "section_type": diff.section_type,
                    "section_path": diff.section_path,
                    "title": diff.title,
                    "clause_no": diff.clause_no,
                    "match_score": diff.match_score,
                    "match_method": diff.match_method,
                    "match_confidence": diff.match_confidence,
                    "match_score_details": diff.match_score_details,
                    "review_flags": diff.review_flags,
                    "structural_flags": diff.structural_flags,
                    "quality_status": diff.quality_status,
                    "text_confidence": diff.text_confidence,
                    "merged_sources": diff.merged_sources,
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
        parsed = self.number_parser.parse_line(clause.clause_no) if clause.clause_no else None
        return {
            "clause_id": clause.clause_id,
            "clause_no": clause.clause_no,
            "canonical_clause_no": parsed.canonical_number if parsed else self.number_parser.normalize_number(clause.clause_no),
            "number_style": parsed.style if parsed else "",
            "title": clause.title,
            "section_type": clause.section_type,
            "section_path": clause.section_path,
            "clause_key": clause.clause_key,
            "order_index": clause.order_index,
            "split_flags": clause.split_flags,
            "page_numbers": clause.page_numbers,
            "source_block_ids": clause.source_block_ids,
            "segmentation_reason": clause.segmentation_reason,
            "segmentation_confidence": clause.segmentation_confidence,
            "text_length": len(clause.text),
            "normalized_length": len(clause.normalized_text),
            "text_preview": clause.text[:500],
        }

    def _clause_split_quality(self, clauses: list[Clause]) -> dict[str, Any]:
        key_counts: Counter[str] = Counter(clause.clause_key for clause in clauses if clause.clause_key)
        duplicate_keys = [key for key, count in key_counts.items() if count > 1]
        short_clauses = [clause for clause in clauses if len(clause.normalized_text) < 8]
        long_clauses = [clause for clause in clauses if len(clause.normalized_text) > 1500]
        low_confidence = [clause for clause in clauses if clause.segmentation_confidence < 0.68]
        weak_numbered = [
            clause
            for clause in clauses
            if "WEAK_NUMERIC_MARKER" in clause.split_flags or "WEAK_HEADING" in clause.split_flags
        ]
        paragraph_merged = [clause for clause in clauses if "PARAGRAPH_MERGED" in clause.split_flags]
        section_counts: Counter[str] = Counter(clause.section_type for clause in clauses)
        cross_page_merged = [clause for clause in clauses if "CROSS_PAGE_CONTINUATION_MERGED" in clause.split_flags]
        reading_order_repaired = [clause for clause in clauses if "READING_ORDER_REPAIRED" in clause.split_flags]
        return {
            "clause_count": len(clauses),
            "section_counts": dict(section_counts),
            "length_stats": self._length_stats(clauses),
            "short_clause_count": len(short_clauses),
            "long_clause_count": len(long_clauses),
            "low_confidence_count": len(low_confidence),
            "weak_numbered_count": len(weak_numbered),
            "paragraph_merged_count": len(paragraph_merged),
            "cross_page_merged_count": len(cross_page_merged),
            "reading_order_repaired_count": len(reading_order_repaired),
            "duplicate_clause_key_count": len(duplicate_keys),
            "duplicate_clause_keys": duplicate_keys[:50],
            "duplicate_clause_key_groups": self._duplicate_clause_key_groups(clauses, duplicate_keys),
            "split_flag_counts": self._split_flag_counts(clauses),
            "segmentation_diagnostics": self._segmentation_diagnostics(clauses),
            "section_path_diagnostics": self._section_path_diagnostics(clauses),
            "source_evidence_diagnostics": self._source_evidence_diagnostics(clauses),
            "result_stage_summary": self._result_stage_summary(clauses),
            "short_clauses": [self._quality_clause_ref(clause) for clause in short_clauses[:50]],
            "long_clauses": [self._quality_clause_ref(clause) for clause in long_clauses[:50]],
            "low_confidence_clauses": [self._quality_clause_ref(clause) for clause in low_confidence[:50]],
            "weak_numbered_clauses": [self._quality_clause_ref(clause) for clause in weak_numbered[:50]],
            "paragraph_merged_clauses": [self._quality_clause_ref(clause) for clause in paragraph_merged[:50]],
            "cross_page_merged_clauses": [self._quality_clause_ref(clause) for clause in cross_page_merged[:50]],
            "reading_order_repaired_clauses": [self._quality_clause_ref(clause) for clause in reading_order_repaired[:50]],
        }

    def _length_stats(self, clauses: list[Clause]) -> dict[str, Any]:
        lengths = [len(clause.normalized_text) for clause in clauses]
        if not lengths:
            return {"min": 0, "max": 0, "avg": 0.0}
        return {
            "min": min(lengths),
            "max": max(lengths),
            "avg": round(sum(lengths) / len(lengths), 2),
        }

    def _split_flag_counts(self, clauses: list[Clause]) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for clause in clauses:
            counts.update(flag for flag in clause.split_flags if flag)
        return dict(counts)

    def _segmentation_diagnostics(self, clauses: list[Clause]) -> dict[str, Any]:
        base_counts: Counter[str] = Counter()
        order_reason_counts: Counter[str] = Counter()
        heading_signal_counts: Counter[str] = Counter()
        heading_risk_counts: Counter[str] = Counter()
        heading_score_buckets: Counter[str] = Counter()
        heading_scores: list[float] = []
        for clause in clauses:
            parts = self._segmentation_reason_parts(clause.segmentation_reason)
            if not parts:
                base_counts["missing"] += 1
                continue
            base_counts[self._segmentation_base(parts[0])] += 1
            for part in parts[1:]:
                if part.startswith("order:"):
                    order_reason_counts[part.removeprefix("order:") or "unknown"] += 1
                elif part.startswith("signals:"):
                    heading_signal_counts.update(self._csv_values(part.removeprefix("signals:")))
                elif part.startswith("risks:"):
                    heading_risk_counts.update(self._csv_values(part.removeprefix("risks:")))
                elif part.startswith("heading_score:"):
                    score = self._parse_float(part.removeprefix("heading_score:"))
                    if score is not None:
                        heading_scores.append(score)
                        heading_score_buckets[self._heading_score_bucket(score)] += 1
        return {
            "base_counts": dict(base_counts),
            "order_reason_counts": dict(order_reason_counts),
            "heading_signal_counts": dict(heading_signal_counts),
            "heading_risk_counts": dict(heading_risk_counts),
            "heading_score_buckets": dict(heading_score_buckets),
            "heading_score_stats": self._score_stats(heading_scores),
            "missing_reason_count": base_counts.get("missing", 0),
        }

    @staticmethod
    def _segmentation_reason_parts(reason: str) -> list[str]:
        return [part.strip() for part in (reason or "").split("|") if part.strip()]

    @staticmethod
    def _segmentation_base(first_part: str) -> str:
        if first_part.startswith("marker:"):
            return "marker"
        if first_part == "unnumbered_heading":
            return "unnumbered_heading"
        if first_part.startswith("fallback"):
            return "fallback"
        return first_part or "unknown"

    @staticmethod
    def _csv_values(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    @staticmethod
    def _parse_float(value: str) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _heading_score_bucket(score: float) -> str:
        if score < 0.55:
            return "very_low_lt_0_55"
        if score < 0.68:
            return "review_0_55_to_0_68"
        if score < 0.85:
            return "normal_0_68_to_0_85"
        return "strong_gte_0_85"

    def _score_stats(self, scores: list[float]) -> dict[str, Any]:
        if not scores:
            return {"count": 0, "min": 0.0, "max": 0.0, "avg": 0.0}
        return {
            "count": len(scores),
            "min": round(min(scores), 2),
            "max": round(max(scores), 2),
            "avg": round(sum(scores) / len(scores), 2),
        }

    def _section_path_diagnostics(self, clauses: list[Clause]) -> dict[str, Any]:
        path_counts: Counter[str] = Counter()
        transitions: list[dict[str, Any]] = []
        previous_key: tuple[str, tuple[str, ...]] | None = None
        section_path_change_count = 0
        for index, clause in enumerate(clauses):
            key = (clause.section_type, tuple(clause.section_path))
            path_label = self._section_path_label(clause)
            path_counts[path_label] += 1
            if previous_key != key:
                if previous_key is not None:
                    section_path_change_count += 1
                transitions.append(
                    {
                        "index": index,
                        "clause_id": clause.clause_id,
                        "clause_no": clause.clause_no,
                        "title": clause.title,
                        "section_type": clause.section_type,
                        "section_path": clause.section_path,
                        "page_numbers": clause.page_numbers,
                    }
                )
            previous_key = key
        return {
            "unique_section_path_count": len(path_counts),
            "section_path_change_count": section_path_change_count,
            "section_path_counts": dict(path_counts),
            "section_path_transitions": transitions[:100],
        }

    @staticmethod
    def _section_path_label(clause: Clause) -> str:
        path = " / ".join(clause.section_path)
        return f"{clause.section_type}:{path}" if path else f"{clause.section_type}:"

    def _source_evidence_diagnostics(self, clauses: list[Clause]) -> dict[str, Any]:
        source_counts = [len(clause.source_block_ids) for clause in clauses]
        evidence_counts = [len(clause.bboxes) for clause in clauses]
        multi_page = [clause for clause in clauses if len(set(clause.page_numbers)) > 1]
        missing_evidence = [clause for clause in clauses if not clause.bboxes]
        return {
            "page_span_counts": {
                "missing_page": sum(1 for clause in clauses if not clause.page_numbers),
                "single_page": sum(1 for clause in clauses if len(set(clause.page_numbers)) == 1),
                "multi_page": len(multi_page),
            },
            "source_block_stats": self._count_stats(source_counts),
            "evidence_stats": self._count_stats(evidence_counts),
            "missing_evidence_count": len(missing_evidence),
            "multi_page_clause_count": len(multi_page),
            "multi_page_clauses": [self._quality_clause_ref(clause) for clause in multi_page[:50]],
            "missing_evidence_clauses": [self._quality_clause_ref(clause) for clause in missing_evidence[:50]],
        }

    @staticmethod
    def _count_stats(values: list[int]) -> dict[str, Any]:
        if not values:
            return {"min": 0, "max": 0, "avg": 0.0}
        return {
            "min": min(values),
            "max": max(values),
            "avg": round(sum(values) / len(values), 2),
        }

    def _result_stage_summary(self, clauses: list[Clause]) -> dict[str, Any]:
        return {
            "final_clause_count": len(clauses),
            "clauses_with_paragraph_merge": sum(1 for clause in clauses if "PARAGRAPH_MERGED" in clause.split_flags),
            "clauses_with_cross_page_merge": sum(1 for clause in clauses if "CROSS_PAGE_CONTINUATION_MERGED" in clause.split_flags),
            "clauses_with_reading_order_repair": sum(1 for clause in clauses if "READING_ORDER_REPAIRED" in clause.split_flags),
            "clauses_with_weak_heading_or_marker": sum(
                1
                for clause in clauses
                if "WEAK_NUMERIC_MARKER" in clause.split_flags or "WEAK_HEADING" in clause.split_flags
            ),
            "clauses_with_section_flags": sum(1 for clause in clauses if any(flag.startswith("SECTION_") for flag in clause.split_flags)),
        }

    def _duplicate_clause_key_groups(self, clauses: list[Clause], duplicate_keys: list[str]) -> list[dict[str, Any]]:
        duplicate_key_set = set(duplicate_keys)
        if not duplicate_key_set:
            return []
        groups: dict[str, list[Clause]] = {}
        for clause in clauses:
            if clause.clause_key in duplicate_key_set:
                groups.setdefault(clause.clause_key, []).append(clause)
        return [
            {
                "clause_key": key,
                "count": len(group),
                "clauses": [self._quality_clause_ref(clause) for clause in group[:20]],
            }
            for key, group in list(groups.items())[:50]
        ]

    def _quality_clause_ref(self, clause: Clause) -> dict[str, Any]:
        return {
            "clause_id": clause.clause_id,
            "clause_no": clause.clause_no,
            "title": clause.title,
            "section_type": clause.section_type,
            "section_path": clause.section_path,
            "clause_key": clause.clause_key,
            "split_flags": clause.split_flags,
            "segmentation_reason": clause.segmentation_reason,
            "segmentation_confidence": clause.segmentation_confidence,
            "normalized_length": len(clause.normalized_text),
            "page_numbers": clause.page_numbers,
            "page_span": self._page_span(clause.page_numbers),
            "source_block_count": len(clause.source_block_ids),
            "evidence_count": len(clause.bboxes),
            "text_preview": clause.text[:160],
        }

    @staticmethod
    def _page_span(page_numbers: list[int]) -> dict[str, Any]:
        if not page_numbers:
            return {"start": None, "end": None, "count": 0}
        unique_pages = sorted(set(page_numbers))
        return {"start": unique_pages[0], "end": unique_pages[-1], "count": len(unique_pages)}

    def _section_outline(self, clauses: list[Clause]) -> list[dict[str, Any]]:
        outline = []
        seen: set[tuple[str, tuple[str, ...]]] = set()
        for clause in clauses:
            key = (clause.section_type, tuple(clause.section_path))
            if key in seen:
                continue
            seen.add(key)
            outline.append(
                {
                    "section_type": clause.section_type,
                    "section_path": clause.section_path,
                    "first_clause_id": clause.clause_id,
                    "page_numbers": clause.page_numbers,
                }
            )
        return outline

    def _evidence_summary(self, evidence: Any) -> dict[str, Any]:
        return {
            "page_no": evidence.page_no,
            "bbox": to_jsonable(evidence.bbox),
            "method": evidence.method,
            "highlight_type": evidence.highlight_type,
            "confidence": evidence.confidence,
            "evidence_quality": evidence.evidence_quality,
            "text_confidence": evidence.text_confidence,
            "text": evidence.text[:160],
        }
