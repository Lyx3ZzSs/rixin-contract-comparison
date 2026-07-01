from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.models import Clause, DiffItem, Document, EvidenceBox
from app.services.diff.boundary_coverage import BoundaryCoverageContext, ClauseBoundaryCoverageFilter
from app.services.diff.range_refiner import layout_punctuation_equivalent


@dataclass
class DiffQualityDecision:
    action: str
    diff_id: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiffQualityResult:
    diffs: list[DiffItem]
    decisions: list[DiffQualityDecision] = field(default_factory=list)

    def to_debug_payload(self) -> list[dict[str, Any]]:
        return [
            {"action": item.action, "diff_id": item.diff_id, "detail": item.detail}
            for item in self.decisions
        ]


class DiffQualityProcessor:
    mergeable_sources = {"metadata", "table", "header_footer"}
    source_priority = {"metadata": 0, "table": 1, "header_footer": 2}
    ocr_quality_review_flags = {
        "PAGE_UNRELIABLE",
        "OCR_LOW_CONFIDENCE",
        "LAYOUT_MISMATCH_RISK",
        "READING_ORDER_RISK",
        "TABLE_STRUCTURE_UNRELIABLE",
        "SEAL_OR_SIGNATURE_RISK",
        "EVIDENCE_UNRELIABLE",
    }
    critical_field_flag = "CRITICAL_FIELD_CHANGE"
    critical_pattern = re.compile(
        r"(\d|%|‰|元|万元|v\d|V\d|公司|甲方|乙方|不得|不承担|违约|免责|终止|不可抗力)"
    )
    business_token_pattern = re.compile(
        r"(%|‰|元|万元|亿元|v\d|V\d|公司|甲方|乙方|不得|不承担|违约|免责|终止|不可抗力|"
        r"\d+(?:\.\d+)?\s*(?:%|‰|元|万元|亿元|天|日|月|年|个月)|"
        r"\d+(?:\.\d+)?\s*(?:days?|months?|years?)|"
        r"\d{4}\s*年|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|pay|payment|invoice|buyer|supplier)"
    )
    style_punct_pattern = re.compile(r"[\s，。；：、”“‘’（）()\[\]【】《》!?:;\"']+")
    low_value_symbol_pattern = re.compile(r"^[\d/\\∠_.,，。·•\-—~～…\sLIl|\[\]【】（）()]+$", re.IGNORECASE)
    single_latin_layout_glyphs = {"i", "l", "|"}
    header_footer_pattern = re.compile(r"(?:页眉|页脚|页码|第\s*\d+\s*页|共\s*\d+\s*页)")
    directly_suppressible_review_reasons = {
        "single_latin_layout_glyph_noise",
    }

    def __init__(self) -> None:
        self.boundary_coverage_filter = ClauseBoundaryCoverageFilter()

    def process(
        self,
        diffs: list[DiffItem],
        *,
        original_clauses: list[Clause] | None = None,
        compare_clauses: list[Clause] | None = None,
        original_document: Document | None = None,
        compare_document: Document | None = None,
    ) -> DiffQualityResult:
        working = [diff.model_copy(deep=True) for diff in diffs]
        decisions: list[DiffQualityDecision] = []
        working = self._dedupe_cross_source(working, decisions)
        self._classify(working, decisions)
        working = self._suppress_low_value_noise(working, decisions)
        self._flag_structural_risks(working, decisions)
        self._flag_boundary_drift(working, decisions)
        self._flag_cross_source_structural_misclassification(working, decisions)
        working, boundary_decisions = self.boundary_coverage_filter.filter(
            working,
            BoundaryCoverageContext(
                original_clauses=original_clauses or [],
                compare_clauses=compare_clauses or [],
                original_document=original_document,
                compare_document=compare_document,
            ),
        )
        decisions.extend(
            DiffQualityDecision(action=decision.action, diff_id=decision.diff_id, detail=decision.detail)
            for decision in boundary_decisions
        )
        self._propagate_text_confidence(working)
        return DiffQualityResult(diffs=working, decisions=decisions)

    def _dedupe_cross_source(
        self,
        diffs: list[DiffItem],
        decisions: list[DiffQualityDecision],
    ) -> list[DiffItem]:
        groups: dict[tuple[str, str, str], list[DiffItem]] = {}
        for diff in diffs:
            if diff.source_type not in self.mergeable_sources:
                continue
            key = (
                diff.diff_type,
                self._dedupe_key(diff.original_text or diff.original_snippet),
                self._dedupe_key(diff.compare_text or diff.compare_snippet),
            )
            if not key[1] and not key[2]:
                continue
            groups.setdefault(key, []).append(diff)

        remove_ids: set[str] = set()
        by_id = {diff.diff_id: diff for diff in diffs}
        for group in groups.values():
            if len(group) < 2:
                continue
            winner = sorted(
                group,
                key=lambda item: (
                    self.source_priority.get(item.source_type, 99),
                    item.diff_id,
                ),
            )[0]
            merged_sources = set(winner.merged_sources)
            for duplicate in group:
                if duplicate.diff_id == winner.diff_id:
                    continue
                remove_ids.add(duplicate.diff_id)
                merged_sources.add(duplicate.source_type)
                winner.original_evidence = self._merge_evidence(winner.original_evidence, duplicate.original_evidence)
                winner.compare_evidence = self._merge_evidence(winner.compare_evidence, duplicate.compare_evidence)
                winner.review_flags = self._merge_flags(winner.review_flags, duplicate.review_flags)
                if duplicate.quality_status == "NEEDS_REVIEW":
                    winner.quality_status = "NEEDS_REVIEW"
                decisions.append(
                    DiffQualityDecision(
                        action="cross_source_merged",
                        diff_id=winner.diff_id,
                        detail={"merged_diff_id": duplicate.diff_id, "source_type": duplicate.source_type},
                    )
                )
            winner.merged_sources = sorted(merged_sources)
            self._add_flag(winner, "CROSS_SOURCE_MERGED")

        return [by_id[diff.diff_id] for diff in diffs if diff.diff_id not in remove_ids]

    @staticmethod
    def _merge_flags(winner_flags: list[str], duplicate_flags: list[str]) -> list[str]:
        return list(dict.fromkeys([*winner_flags, *duplicate_flags]))

    def _classify(self, diffs: list[DiffItem], decisions: list[DiffQualityDecision]) -> None:
        for diff in diffs:
            if self._trim_signing_form_ocr_noise_from_date_change(diff):
                decisions.append(
                    DiffQualityDecision(
                        action="trimmed_signing_form_ocr_noise",
                        diff_id=diff.diff_id,
                        detail={"reason": "seal_occluded_signing_form_text"},
                    )
                )
            if self._reclassify_signing_date_field_change(diff):
                decisions.append(
                    DiffQualityDecision(
                        action="signing_date_field_reclassified",
                        diff_id=diff.diff_id,
                        detail={"source_type": "metadata", "reason": "signing_date_field_fill"},
                    )
                )
                continue
            if self._should_downgrade_non_body_change(diff):
                diff.quality_status = "NEEDS_REVIEW"
                self._remove_flag(diff, "CRITICAL_VALUE_CHANGE")
                self._add_flag(diff, self._non_body_review_flag(diff))
                decisions.append(
                    DiffQualityDecision(
                        action="non_body_change_review",
                        diff_id=diff.diff_id,
                        detail={"source_type": diff.source_type, "section_type": diff.section_type},
                    )
                )
                continue
            if self._has_critical_field_change(diff):
                self._add_flag(diff, "CRITICAL_VALUE_CHANGE")
                decisions.append(DiffQualityDecision(action="critical_field_change", diff_id=diff.diff_id))
                continue
            if self._looks_like_minor_ocr_noise(diff) or self._looks_like_short_symbol_noise(diff):
                self._add_flag(diff, "POSSIBLE_OCR_NOISE")
                diff.quality_status = "NEEDS_REVIEW"
                decisions.append(DiffQualityDecision(action="possible_ocr_noise", diff_id=diff.diff_id))
                continue
            if self._is_row_level_table_diff(diff) and not self._row_level_table_has_protected_change(diff):
                self._remove_flag(diff, "CRITICAL_VALUE_CHANGE")
                self._add_flag(diff, "ROW_LEVEL_TABLE_REVIEW")
                diff.quality_status = "NEEDS_REVIEW"
                decisions.append(DiffQualityDecision(action="row_level_table_review", diff_id=diff.diff_id))
                continue
            if self._is_table_region_review_diff(diff):
                self._remove_flag(diff, "CRITICAL_VALUE_CHANGE")
                self._add_flag(diff, "TABLE_REGION_REVIEW")
                diff.quality_status = "NEEDS_REVIEW"
                decisions.append(
                    DiffQualityDecision(
                        action="table_region_review",
                        diff_id=diff.diff_id,
                        detail={"flags": sorted(set(diff.structural_flags) | set(diff.review_flags))},
                    )
                )
                continue
            if self._is_visible_cover_annotation_review(diff):
                self._add_flag(diff, "COVER_ANNOTATION_REVIEW")
                diff.quality_status = "NEEDS_REVIEW"
                decisions.append(DiffQualityDecision(action="cover_annotation_review", diff_id=diff.diff_id))
                continue
            if self._is_critical_change(diff):
                self._add_flag(diff, "CRITICAL_VALUE_CHANGE")
                decisions.append(DiffQualityDecision(action="critical_change", diff_id=diff.diff_id))
                continue
            if self._looks_like_cover_fragment(diff):
                self._add_flag(diff, "POSSIBLE_COVER_OCR_FRAGMENT")
                diff.quality_status = "NEEDS_REVIEW"
                decisions.append(DiffQualityDecision(action="possible_cover_ocr_fragment", diff_id=diff.diff_id))

    def _suppress_low_value_noise(
        self,
        diffs: list[DiffItem],
        decisions: list[DiffQualityDecision],
    ) -> list[DiffItem]:
        kept: list[DiffItem] = []
        for diff in diffs:
            reason = self._suppression_reason(diff)
            if reason:
                if (
                    reason not in self.directly_suppressible_review_reasons
                    and self.ocr_quality_review_flags.intersection(diff.review_flags)
                    and not self._is_confirmed_clause_ocr_noise(diff)
                    and not self._is_planned_short_symbol_ocr_noise(diff, reason)
                ):
                    kept.append(diff)
                    continue
                decisions.append(DiffQualityDecision(action="suppressed_low_value_noise", diff_id=diff.diff_id, detail={"reason": reason}))
                continue
            kept.append(diff)
        return kept

    def _suppression_reason(self, diff: DiffItem) -> str:
        if self._looks_like_cover_annotation_noise(diff):
            return "cover_annotation_noise"
        if self._looks_like_header_footer_noise(diff):
            return "header_footer_noise"
        if self._has_critical_field_change(diff):
            return ""
        if self._is_range_connector_equivalent_clause_change(diff):
            return "range_connector_equivalent"
        if self._is_layout_punctuation_equivalent_clause_change(diff):
            return "layout_punctuation_equivalent"
        if self._looks_like_single_cjk_ocr_substitution(diff):
            return "single_cjk_ocr_substitution"
        if self._changed_text_has_business_token(diff):
            return ""
        changed = self._changed_text(diff)
        compact = self._compact(changed)
        if not compact:
            return "empty_change"
        if self._looks_like_single_latin_layout_glyph_noise(diff):
            return "single_latin_layout_glyph_noise"
        if diff.source_type == "clause" and "POSSIBLE_OCR_NOISE" in diff.review_flags and self._looks_like_short_symbol_noise(diff):
            return "clause_ocr_noise"
        if diff.source_type in {"header_footer", "metadata"} and len(compact) <= 4:
            return "short_non_body_fragment"
        if diff.source_type == "clause" and self._looks_like_short_symbol_noise(diff):
            return "short_symbol_noise"
        return ""

    @staticmethod
    def _is_layout_punctuation_equivalent_clause_change(diff: DiffItem) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        if (diff.match_score or 0) < 96:
            return False
        if diff.match_score_details.get("body_score", 100.0) < 96:
            return False
        if diff.match_score_details.get("business_token_mismatch", 0.0) >= 1:
            return False
        return layout_punctuation_equivalent(diff.original_text, diff.compare_text)

    def _is_range_connector_equivalent_clause_change(self, diff: DiffItem) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        if not diff.original_text or not diff.compare_text:
            return False
        if (diff.match_score or 0) < 96:
            return False
        if diff.match_score_details and diff.match_score_details.get("body_score", 100.0) < 96:
            return False
        if diff.match_score_details.get("business_token_mismatch", 0.0) >= 1:
            return False
        if not (
            self._has_range_connector(diff.original_text)
            or self._has_range_connector(diff.compare_text)
        ):
            return False
        normalized_original = self._normalize_range_connectors(diff.original_text)
        normalized_compare = self._normalize_range_connectors(diff.compare_text)
        return bool(normalized_original and normalized_original == normalized_compare)

    @staticmethod
    def _normalize_range_connectors(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text or "")
        normalized = re.sub(r"(?<=[0-9%％])(?:一|﹣|－|–|—|-|~|～|至)(?=[0-9])", "-", normalized)
        return re.sub(r"\s+", "", normalized)

    @staticmethod
    def _has_range_connector(text: str) -> bool:
        normalized = unicodedata.normalize("NFKC", text or "")
        return bool(re.search(r"(?<=[0-9%％])(?:一|﹣|－|–|—|-|~|～|至)(?=[0-9])", normalized))

    def _flag_boundary_drift(self, diffs: list[DiffItem], decisions: list[DiffQualityDecision]) -> None:
        clause_diffs = [diff for diff in diffs if diff.source_type == "clause"]
        deletes = [diff for diff in clause_diffs if self._compact(diff.original_snippet) and not self._compact(diff.compare_snippet)]
        adds = [diff for diff in clause_diffs if self._compact(diff.compare_snippet) and not self._compact(diff.original_snippet)]
        for delete in deletes:
            delete_text = self._compact(delete.original_snippet)
            if not 2 <= len(delete_text) <= 40:
                continue
            for add in adds:
                if delete.diff_id == add.diff_id:
                    continue
                if delete_text != self._compact(add.compare_snippet):
                    continue
                self._add_flag(delete, "POSSIBLE_BOUNDARY_DRIFT")
                self._add_flag(add, "POSSIBLE_BOUNDARY_DRIFT")
                delete.quality_status = "NEEDS_REVIEW"
                add.quality_status = "NEEDS_REVIEW"
                decisions.append(
                    DiffQualityDecision(
                        action="possible_boundary_drift",
                        diff_id=delete.diff_id,
                        detail={"paired_diff_id": add.diff_id, "fragment": delete_text[:80]},
                    )
                )

    def _flag_structural_risks(self, diffs: list[DiffItem], decisions: list[DiffQualityDecision]) -> None:
        for diff in diffs:
            if diff.source_type != "clause":
                continue
            if diff.section_type and diff.section_type != "main_contract":
                self._add_flag(diff, "NON_MAIN_CONTRACT_SECTION")
                decisions.append(
                    DiffQualityDecision(
                        action="non_main_contract_section",
                        diff_id=diff.diff_id,
                        detail={"section_type": diff.section_type, "section_path": diff.section_path},
                    )
                )
            risk_flags = set(diff.structural_flags) | set(diff.review_flags)
            if (
                "POSSIBLE_SPLIT_DRIFT" in risk_flags
                or "LOW_CONFIDENCE_MATCH" in risk_flags
                or "LOW_COVERAGE_CLAUSE_KEY_MATCH" in risk_flags
            ):
                diff.quality_status = "NEEDS_REVIEW"
                decisions.append(
                    DiffQualityDecision(
                        action="structural_risk_review",
                        diff_id=diff.diff_id,
                        detail={"flags": sorted(risk_flags)},
                    )
                )

    def _flag_cross_source_structural_misclassification(
        self,
        diffs: list[DiffItem],
        decisions: list[DiffQualityDecision],
    ) -> None:
        non_clause = [diff for diff in diffs if diff.source_type != "clause"]
        for diff in diffs:
            if diff.source_type != "clause" or diff.diff_type not in {"ADD", "DELETE"}:
                continue
            clause_text = self._structural_key(diff.compare_text or diff.compare_snippet)
            if not clause_text:
                continue
            for other in non_clause:
                opposite_text = (
                    other.original_text or other.original_snippet
                    if diff.diff_type == "ADD"
                    else other.compare_text or other.compare_snippet
                )
                other_key = self._structural_key(opposite_text)
                if not other_key:
                    continue
                if clause_text.startswith(other_key) or other_key.startswith(clause_text[: max(8, len(other_key))]):
                    self._add_flag(diff, "POSSIBLE_STRUCTURAL_MISCLASSIFICATION")
                    diff.quality_status = "NEEDS_REVIEW"
                    decisions.append(
                        DiffQualityDecision(
                            action="possible_structural_misclassification",
                            diff_id=diff.diff_id,
                            detail={"paired_diff_id": other.diff_id, "source_type": other.source_type},
                        )
                    )
                    break

    def _propagate_text_confidence(self, diffs: list[DiffItem]) -> None:
        for diff in diffs:
            confidences = [
                evidence.text_confidence
                for evidence in [*diff.original_evidence, *diff.compare_evidence]
                if evidence.text_confidence is not None
            ]
            if confidences:
                diff.text_confidence = min(confidences)

    def _looks_like_minor_ocr_noise(self, diff: DiffItem) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        if self._changed_text_has_business_token(diff):
            return False
        if (diff.match_score or 0) < 96:
            return False
        changed_len = len(self._compact(diff.original_snippet)) + len(self._compact(diff.compare_snippet))
        return 0 < changed_len <= 3

    def _looks_like_short_symbol_noise(self, diff: DiffItem) -> bool:
        if diff.source_type != "clause":
            return False
        if self._changed_text_has_business_token(diff):
            return False
        changed = self._changed_text(diff)
        compact = self._compact(changed)
        if not compact:
            return False
        return len(compact) <= 12 and bool(self.low_value_symbol_pattern.fullmatch(changed.strip()))

    def _looks_like_single_latin_layout_glyph_noise(self, diff: DiffItem) -> bool:
        if diff.source_type != "clause":
            return False
        flags = set(diff.structural_flags) | set(diff.review_flags)
        if not flags.intersection({"LAYOUT_MISMATCH_RISK", "SEAL_OR_SIGNATURE_RISK"}):
            return False
        compact = self._compact(self._changed_text(diff))
        if len(compact) != 1:
            return False
        if compact in self.single_latin_layout_glyphs:
            return True
        if not compact.isalpha() or not compact.isascii():
            return False
        return self._changed_evidence_is_near_page_edge(diff)

    @staticmethod
    def _changed_evidence_is_near_page_edge(diff: DiffItem) -> bool:
        changed = {unicodedata.normalize("NFKC", item) for item in [diff.original_snippet, diff.compare_snippet] if item}
        for evidence in [*diff.original_evidence, *diff.compare_evidence]:
            text = unicodedata.normalize("NFKC", evidence.text or "")
            if changed and text not in changed:
                continue
            if evidence.bbox.x0 <= 36.0 or evidence.bbox.x1 <= 40.0:
                return True
        return False

    def _looks_like_single_cjk_ocr_substitution(self, diff: DiffItem) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        if not diff.original_text or not diff.compare_text:
            return False
        if (diff.match_score or 0) < 96:
            return False
        flags = set(diff.review_flags)
        if "POSSIBLE_OCR_NOISE" not in flags:
            return False
        original = self._compact(diff.original_snippet)
        compare = self._compact(diff.compare_snippet)
        if not original or not compare:
            return False
        return bool(re.fullmatch(r"[\u4e00-\u9fff]", original) and re.fullmatch(r"[\u4e00-\u9fff]", compare))

    def _looks_like_cover_annotation_noise(self, diff: DiffItem) -> bool:
        if diff.source_type != "metadata" or diff.title != "封面额外文本":
            return False
        flags = set(diff.review_flags)
        if not flags.intersection({"EVIDENCE_UNRELIABLE", "OCR_REMEDIATION_UNRESOLVED", "POSSIBLE_COVER_OCR_FRAGMENT"}):
            return False
        compact = self._compact(self._changed_text(diff))
        if not compact:
            return False
        evidences = [*diff.original_evidence, *diff.compare_evidence]
        if not evidences:
            return False
        if self._looks_like_single_cjk_cover_stamp_fragment(compact, evidences, flags):
            return True
        if re.search(r"[\u4e00-\u9fff]", compact) or not re.search(r"[a-z]", compact):
            return False
        return any(
            evidence.method in {"header_footer", "cover_extra"}
            and (
                evidence.confidence < 0.7
                or evidence.evidence_quality == "LOW"
                or evidence.bbox.y0 <= 72.0
            )
            for evidence in evidences
        )

    def _is_visible_cover_annotation_review(self, diff: DiffItem) -> bool:
        if diff.source_type != "metadata" or diff.title != "封面额外文本":
            return False
        flags = set(diff.review_flags)
        if not flags.intersection(self.ocr_quality_review_flags | {"POSSIBLE_COVER_OCR_FRAGMENT"}):
            return False
        evidences = [*diff.original_evidence, *diff.compare_evidence]
        return any(evidence.method in {"header_footer", "cover_extra"} and evidence.bbox.y0 <= 96.0 for evidence in evidences)

    @staticmethod
    def _looks_like_single_cjk_cover_stamp_fragment(
        compact: str,
        evidences: list[EvidenceBox],
        flags: set[str],
    ) -> bool:
        if not re.fullmatch(r"[\u4e00-\u9fff]", compact):
            return False
        if "SEAL_OR_SIGNATURE_RISK" not in flags:
            return False
        return any(evidence.method == "cover_extra" and evidence.bbox.y0 <= 96.0 for evidence in evidences)

    def _reclassify_signing_date_field_change(self, diff: DiffItem) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        flags = set(diff.review_flags)
        if "CRITICAL_FIELD_CHANGE" not in flags:
            return False
        if not flags.intersection({"CRITICAL_FIELD_DATE_CHANGE", "CRITICAL_FIELD_DURATION_CHANGE"}):
            return False
        context = f"{diff.original_text}\n{diff.compare_text}"
        if not self._has_signing_date_context(context):
            return False
        if not self._changed_text_is_signing_date_fill(diff):
            return False
        diff.source_type = "metadata"
        diff.title = "签署日期"
        diff.section_type = "signature"
        diff.original_snippet = self._strip_signing_date_placeholder(diff.original_snippet)
        diff.compare_snippet = self._strip_signing_date_placeholder(diff.compare_snippet)
        diff.original_evidence = self._keep_evidence_covered_by_snippet(diff.original_evidence, diff.original_snippet)
        diff.compare_evidence = self._keep_evidence_covered_by_snippet(diff.compare_evidence, diff.compare_snippet)
        diff.original_change_ranges = self._keep_ranges_covered_by_snippet(
            diff.original_text,
            diff.original_change_ranges,
            diff.original_snippet,
        )
        diff.compare_change_ranges = self._keep_ranges_covered_by_snippet(
            diff.compare_text,
            diff.compare_change_ranges,
            diff.compare_snippet,
        )
        self._add_flag(diff, "SIGNING_DATE_FIELD_CHANGE")
        for flag in (
            "CRITICAL_FIELD_CHANGE",
            "CRITICAL_FIELD_DATE_CHANGE",
            "CRITICAL_FIELD_DURATION_CHANGE",
            "CRITICAL_VALUE_CHANGE",
        ):
            self._remove_flag(diff, flag)
        return True

    def _trim_signing_form_ocr_noise_from_date_change(self, diff: DiffItem) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        flags = set(diff.review_flags)
        if "SEAL_OR_SIGNATURE_RISK" not in flags:
            return False
        context = f"{diff.original_text}\n{diff.compare_text}"
        if not self._has_signing_date_context(context):
            return False
        if not self._text_contains_filled_date(self._changed_text(diff)):
            return False

        original_snippet = self._strip_signing_form_noise(diff.original_snippet)
        compare_snippet = self._strip_signing_form_noise(diff.compare_snippet)
        if original_snippet == diff.original_snippet and compare_snippet == diff.compare_snippet:
            return False

        diff.original_snippet = original_snippet
        diff.compare_snippet = compare_snippet
        diff.original_evidence = self._keep_evidence_covered_by_snippet(diff.original_evidence, original_snippet)
        diff.compare_evidence = self._keep_evidence_covered_by_snippet(diff.compare_evidence, compare_snippet)
        diff.original_change_ranges = self._keep_ranges_covered_by_snippet(
            diff.original_text,
            diff.original_change_ranges,
            original_snippet,
        )
        diff.compare_change_ranges = self._keep_ranges_covered_by_snippet(
            diff.compare_text,
            diff.compare_change_ranges,
            compare_snippet,
        )
        return True

    @staticmethod
    def _text_contains_filled_date(text: str) -> bool:
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
        return bool(
            re.search(r"\d{4}年\d{1,2}月\d{1,2}日", compact)
            or re.search(r"\d{4}年\d{3,4}日", compact)
            or re.search(r"\d{8}", compact)
        )

    @staticmethod
    def _strip_signing_form_noise(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text or "")
        normalized = re.sub(r"单位名称[:：][^()（）\n]{0,60}", "", normalized)
        normalized = re.sub(r"[（(]\s*章\s*[）)]", "", normalized)
        normalized = re.sub(r"(?<![\u4e00-\u9fff])公司(?![\u4e00-\u9fff])", "", normalized)
        return re.sub(r"\s+", "", normalized)

    def _keep_evidence_covered_by_snippet(self, evidences: list[EvidenceBox], snippet: str) -> list[EvidenceBox]:
        snippet_key = self._compact(snippet)
        if not snippet_key:
            return []
        return [evidence for evidence in evidences if self._compact(evidence.text) in snippet_key]

    def _keep_ranges_covered_by_snippet(self, text: str, ranges: list, snippet: str) -> list:
        snippet_key = self._compact(snippet)
        if not snippet_key:
            return []
        kept = []
        for range_ in ranges:
            fragment = text[range_.start : range_.end]
            if self._compact(fragment) in snippet_key:
                kept.append(range_)
        return kept

    @staticmethod
    def _has_signing_date_context(text: str) -> bool:
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
        if not compact:
            return False
        has_parties = "甲方" in compact and "乙方" in compact
        has_signature_label = bool(re.search(r"(签字日期|签订日期|法定代表人|授权代表|年月日)", compact))
        return has_parties and has_signature_label

    def _changed_text_is_signing_date_fill(self, diff: DiffItem) -> bool:
        changed = unicodedata.normalize("NFKC", self._changed_text(diff) or "")
        compact = re.sub(r"\s+", "", changed)
        if not compact:
            return False
        has_filled_date = bool(
            re.search(r"\d{4}年\d{1,2}月\d{1,2}日", compact)
            or re.search(r"\d{4}年\d{3,4}日", compact)
            or re.search(r"\d{8}", compact)
        )
        if not has_filled_date:
            return False
        residue = re.sub(r"\d{4}年\d{1,2}月\d{1,2}日", "", compact)
        residue = re.sub(r"\d{4}年\d{3,4}日", "", residue)
        residue = re.sub(r"\d{8}", "", residue)
        residue = residue.replace("年月日", "").replace("年月", "").replace("月日", "").replace("月", "")
        return residue == ""

    @staticmethod
    def _strip_signing_date_placeholder(text: str) -> str:
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
        if compact and re.fullmatch(r"[年月日]+", compact):
            return ""
        return compact

    def _looks_like_cover_fragment(self, diff: DiffItem) -> bool:
        if diff.source_type != "metadata" or diff.diff_type not in {"ADD", "DELETE"}:
            return False
        return len(self._compact(diff.original_snippet or diff.compare_snippet)) <= 3

    def _is_critical_change(self, diff: DiffItem) -> bool:
        if self._has_critical_field_change(diff):
            return True
        if self._looks_like_short_symbol_noise(diff):
            return False
        if self._should_downgrade_non_body_change(diff):
            return False
        changed = self._changed_text(diff)
        return bool(self.critical_pattern.search(changed or ""))

    def _has_critical_field_change(self, diff: DiffItem) -> bool:
        return diff.source_type == "clause" and self.critical_field_flag in diff.review_flags

    def _has_business_token(self, diff: DiffItem) -> bool:
        text = f"{self._changed_text(diff)} {diff.original_text} {diff.compare_text}"
        return bool(self.business_token_pattern.search(text or ""))

    def _changed_text_has_business_token(self, diff: DiffItem) -> bool:
        return bool(self.business_token_pattern.search(self._changed_text(diff) or ""))

    @staticmethod
    def _is_confirmed_clause_ocr_noise(diff: DiffItem) -> bool:
        flags = set(diff.review_flags)
        return "POSSIBLE_OCR_NOISE" in flags and bool(
            flags.intersection({"SHORT_CLAUSE_MATCH_REVIEW", "OCR_REMEDIATION_UNRESOLVED"})
        )

    @staticmethod
    def _is_planned_short_symbol_ocr_noise(diff: DiffItem, reason: str) -> bool:
        flags = set(diff.review_flags)
        return (
            reason == "clause_ocr_noise"
            and "POSSIBLE_OCR_NOISE" in flags
            and "OCR_REMEDIATION_PLANNED" in flags
            and "EVIDENCE_UNRELIABLE" not in flags
        )

    @staticmethod
    def _is_row_level_table_diff(diff: DiffItem) -> bool:
        if diff.source_type != "table":
            return False
        if "row_level_business_field_check" in diff.structural_flags:
            return False
        evidences = [*diff.original_evidence, *diff.compare_evidence]
        return any(evidence.method == "table_row" for evidence in evidences)

    @staticmethod
    def _is_table_region_review_diff(diff: DiffItem) -> bool:
        if diff.source_type != "table":
            return False
        flags = set(diff.structural_flags) | set(diff.review_flags)
        return "table_region_coverage_gap" in flags

    def _row_level_table_has_protected_change(self, diff: DiffItem) -> bool:
        if diff.diff_type != "MODIFY":
            return False
        original_parts = self._split_table_row_parts(diff.original_text or diff.original_snippet)
        compare_parts = self._split_table_row_parts(diff.compare_text or diff.compare_snippet)
        if len(original_parts) >= 8 and len(compare_parts) >= 8:
            for index in (5, 6, 7):
                if self._protected_value(original_parts[index]) != self._protected_value(compare_parts[index]):
                    if self._protected_value(original_parts[index]) or self._protected_value(compare_parts[index]):
                        return True
        original_date = self._canonical_date(diff.original_text or diff.original_snippet)
        compare_date = self._canonical_date(diff.compare_text or diff.compare_snippet)
        return bool(original_date and compare_date and original_date != compare_date)

    @staticmethod
    def _split_table_row_parts(text: str) -> list[str]:
        return [part.strip() for part in (text or "").split("|")]

    @staticmethod
    def _protected_value(text: str) -> str:
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
        if not compact:
            return ""
        amount = re.fullmatch(r"(?:人民币|¥|￥)?([+-]?\d[\d,]*(?:\.\d+)?)(万)?元?", compact)
        if amount:
            value = float(amount.group(1).replace(",", ""))
            if amount.group(2) or "万元" in compact:
                value *= 10000
            return f"amount:{value:.2f}"
        quantity = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)(套|台|个|项|批|份|件|人天|天|月|年)?", compact)
        if quantity:
            unit = quantity.group(2) or ""
            return f"quantity:{float(quantity.group(1)):.4f}{unit}"
        return ""

    @staticmethod
    def _canonical_date(text: str) -> str:
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
        match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", compact)
        if match:
            return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        match = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", compact)
        if match:
            return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        return ""

    def _changed_text(self, diff: DiffItem) -> str:
        if diff.diff_type == "MODIFY":
            snippet_text = f"{diff.original_snippet} {diff.compare_snippet}".strip()
            if snippet_text:
                return snippet_text
            return f"{diff.original_text} {diff.compare_text}"
        if diff.diff_type == "ADD":
            return diff.compare_snippet or diff.compare_text
        if diff.diff_type == "DELETE":
            return diff.original_snippet or diff.original_text
        return f"{diff.original_snippet} {diff.compare_snippet}"

    def _add_flag(self, diff: DiffItem, flag: str) -> None:
        if flag not in diff.review_flags:
            diff.review_flags.append(flag)

    def _remove_flag(self, diff: DiffItem, flag: str) -> None:
        diff.review_flags = [item for item in diff.review_flags if item != flag]

    def _should_downgrade_non_body_change(self, diff: DiffItem) -> bool:
        if diff.source_type == "header_footer":
            return True
        if diff.source_type == "seal":
            return True
        return False

    def _non_body_review_flag(self, diff: DiffItem) -> str:
        if diff.source_type == "header_footer":
            return "HEADER_FOOTER_REVIEW"
        if diff.source_type == "seal":
            return "SEAL_REVIEW"
        return "NON_BODY_SECTION_REVIEW"

    def _looks_like_header_footer_noise(self, diff: DiffItem) -> bool:
        if diff.source_type != "header_footer":
            return False
        text = f"{diff.title} {self._changed_text(diff)}"
        if self.header_footer_pattern.search(text):
            return True
        return len(self._compact(self._changed_text(diff))) <= 12

    def _dedupe_key(self, text: str) -> str:
        return self._compact(text)

    def _compact(self, text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text or "")
        return self.style_punct_pattern.sub("", normalized).lower()

    def _structural_key(self, text: str) -> str:
        compact = self._compact(text)
        compact = re.sub(r"^[一二三四五六七八九十]+", "", compact)
        return compact

    def _merge_evidence(self, left: list[EvidenceBox], right: list[EvidenceBox]) -> list[EvidenceBox]:
        result = list(left)
        seen = {(item.page_no, item.bbox.x0, item.bbox.y0, item.bbox.x1, item.bbox.y1, item.text) for item in result}
        for item in right:
            key = (item.page_no, item.bbox.x0, item.bbox.y0, item.bbox.x1, item.bbox.y1, item.text)
            if key in seen:
                continue
            result.append(item)
            seen.add(key)
        return result
