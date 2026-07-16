from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.models import Clause, DiffItem, DiffType, Document, EvidenceBox
from app.services.diff.boundary_coverage import BoundaryCoverageContext, ClauseBoundaryCoverageFilter
from app.services.diff.range_refiner import layout_punctuation_equivalent
from app.services.red_seal_visual import RedSealVisualInspector


def _is_signing_contact_table_label_loss(diff: DiffItem, flags: set[str]) -> bool:
    if diff.diff_type != "MODIFY":
        return False
    if "联系人" not in (diff.title or ""):
        return False
    original = diff.original_text or diff.original_snippet
    compare = diff.compare_text or diff.compare_snippet
    if not original or not compare:
        return False
    signing_markers = ("盖章", "法定代表", "负责人", "授权代表", "签订时间")
    if not any(marker in original or marker in compare for marker in signing_markers):
        return False
    original_companies = _company_name_set(original)
    compare_companies = _company_name_set(compare)
    if original_companies and compare_companies and original_companies != compare_companies:
        return False
    if flags.intersection({"TABLE_STRUCTURE_UNRELIABLE", "READING_ORDER_RISK", "OCR_REMEDIATION_PLANNED"}):
        return True
    return bool(original_companies and original_companies == compare_companies)


def _company_name_set(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text or "")
    return {
        re.sub(r"\s+", "", match.group(0))
        for match in re.finditer(r"[\u4e00-\u9fffA-Za-z0-9（）()·\-]{2,80}?(?:分公司|公司)", normalized)
    }


def _party_company_map(text: str, reference: dict[str, str] | None = None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    reference = reference or {}
    columns = [column.strip() for column in re.split(r"\|", text or "") if column.strip()]
    reference_roles = list(reference)
    for index, column in enumerate(columns):
        role_match = re.search(r"(甲方|乙方)", column)
        role = role_match.group(1) if role_match else ""
        companies = sorted(_company_name_set(column), key=len, reverse=True)
        if not companies:
            continue
        company = companies[0]
        if not role and index < len(reference_roles):
            role = reference_roles[index]
        if role:
            mapping[role] = company
    return mapping


def _signing_label_residual(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    columns = re.split(r"[|｜]", normalized)
    residual_columns: list[str] = []
    for column in columns:
        column = column.strip()
        column = re.sub(r"^(甲方|乙方)\s*(?:\(\s*(?:盖章|章|公章)\s*\)|（\s*(?:盖章|章|公章)\s*）)?\s*[:：]?", "", column)
        column = re.sub(r"^(?:盖章|公章|单位名称)\s*[:：]?", "", column)
        column = re.sub(r"^(?:\(\s*章\s*\)|（\s*章\s*）)\s*[:：]?", "", column)
        residual_columns.append(re.sub(r"[\s:：，,。；;、（）()]+", "", column))
    return "".join(residual_columns)


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
        "edge_annotation_clause_noise",
        "form_separator_equivalent",
        "page_number_edge_annotation_noise",
        "seal_occluded_signing_label_covered",
        "signing_contact_table_label_noise",
        "isolated_seal_artifact_text",
        "visual_seal_ocr_fragment",
        "single_latin_layout_glyph_noise",
        "table_header_serialization_equivalent",
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
        working = self._suppress_low_value_noise(
            working,
            decisions,
            original_document=original_document,
            compare_document=compare_document,
        )
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
            if "VISUAL_FOOTER_ANNOTATION" in diff.review_flags:
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
            source_types = {item.source_type for item in group}
            multi_page_footer_diffs = [
                item for item in group if item.source_type == "header_footer" and self._has_multi_page_evidence(item)
            ]
            winner_candidates = (
                multi_page_footer_diffs
                if (
                    multi_page_footer_diffs
                    and {"metadata", "header_footer"}.issubset(source_types)
                    and "table" not in source_types
                )
                else group
            )
            winner = sorted(
                winner_candidates,
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

    @staticmethod
    def _has_multi_page_evidence(diff: DiffItem) -> bool:
        return len({evidence.page_no for evidence in [*diff.original_evidence, *diff.compare_evidence]}) > 1

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
                        detail={
                            "source_type": "metadata",
                            "diff_type": diff.diff_type,
                            "reason": "signing_date_field_change",
                        },
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
            if self._trim_edge_annotation_from_mixed_diff(diff):
                decisions.append(
                    DiffQualityDecision(
                        action="trimmed_edge_annotation_noise",
                        diff_id=diff.diff_id,
                        detail={"reason": "mixed_clause_edge_annotation"},
                    )
                )
            if self._reclassify_unit_separator_change(diff):
                decisions.append(
                    DiffQualityDecision(
                        action="unit_separator_change_reclassified",
                        diff_id=diff.diff_id,
                        detail={"reason": "slash_unit_separator_only"},
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
                pre_review_flags = set(diff.structural_flags) | set(diff.review_flags)
                if (
                    "TABLE_REGION_REVIEW" not in pre_review_flags
                    and _is_signing_contact_table_label_loss(diff, pre_review_flags)
                    and not self._looks_like_table_signing_date_change(diff)
                ):
                    self._add_flag(diff, "SIGNING_TABLE_LABEL_NOISE")
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
        *,
        original_document: Document | None = None,
        compare_document: Document | None = None,
    ) -> list[DiffItem]:
        kept: list[DiffItem] = []
        for diff in diffs:
            reason = self._suppression_reason(
                diff,
                original_document=original_document,
                compare_document=compare_document,
            )
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

    def _suppression_reason(
        self,
        diff: DiffItem,
        *,
        original_document: Document | None = None,
        compare_document: Document | None = None,
    ) -> str:
        if self._looks_like_cover_annotation_noise(diff):
            return "cover_annotation_noise"
        if "VISUAL_FOOTER_ANNOTATION" in diff.review_flags:
            return ""
        if self._is_cross_source_merged_multi_page_footer(diff):
            return ""
        if self._looks_like_header_footer_noise(diff):
            return "header_footer_noise"
        if self._looks_like_edge_annotation_clause_noise(diff):
            return "edge_annotation_clause_noise"
        if self._looks_like_page_number_edge_annotation_noise(diff):
            return "page_number_edge_annotation_noise"
        if self._looks_like_form_separator_equivalent(diff):
            return "form_separator_equivalent"
        if self._looks_like_table_header_serialization_equivalent(diff):
            return "table_header_serialization_equivalent"
        if self._looks_like_seal_occluded_signing_label_covered(diff):
            return "seal_occluded_signing_label_covered"
        if self._looks_like_signing_contact_table_label_noise(diff):
            return "signing_contact_table_label_noise"
        if self._looks_like_isolated_seal_artifact_text(diff):
            return "isolated_seal_artifact_text"
        if self._looks_like_visual_seal_ocr_fragment(
            diff,
            original_document=original_document,
            compare_document=compare_document,
        ):
            return "visual_seal_ocr_fragment"
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

    def _looks_like_form_separator_equivalent(self, diff: DiffItem) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        if self.critical_field_flag in diff.review_flags:
            return False
        original_raw = diff.original_text or diff.original_snippet
        compare_raw = diff.compare_text or diff.compare_snippet
        combined = f"{original_raw}{compare_raw}{diff.original_snippet}{diff.compare_snippet}"
        if not re.search(r"[/\\_＿—－-]{2,}", combined):
            return False
        if self._form_separator_touches_digit(original_raw) or self._form_separator_touches_digit(compare_raw):
            return False
        original = self._compact(re.sub(r"[/\\_＿—－-]+", "", original_raw))
        compare = self._compact(re.sub(r"[/\\_＿—－-]+", "", compare_raw))
        return bool(original and original == compare)

    @staticmethod
    def _form_separator_touches_digit(text: str) -> bool:
        normalized = unicodedata.normalize("NFKC", text or "")
        return bool(re.search(r"\d\s*[/\\_＿—－-]+|[/\\_＿—－-]+\s*\d", normalized))

    def _looks_like_table_header_serialization_equivalent(self, diff: DiffItem) -> bool:
        if diff.source_type != "table" or diff.diff_type != "MODIFY":
            return False
        original = self._compact((diff.original_text or diff.original_snippet).replace("|", ""))
        compare = self._compact((diff.compare_text or diff.compare_snippet).replace("|", ""))
        if not original or original != compare:
            return False
        text = f"{diff.original_text} {diff.compare_text}"
        return "要求" in text and "乙方响应" in text

    def _looks_like_seal_occluded_signing_label_covered(self, diff: DiffItem) -> bool:
        if diff.source_type != "table" or diff.diff_type != "MODIFY":
            return False
        text = f"{diff.title} {diff.original_text} {diff.compare_text}"
        if not re.search(r"盖章|甲方|乙方", text):
            return False
        if self._canonical_date(text) or re.search(r"授权代表|签字|签订时间", diff.compare_text or ""):
            return False
        original_companies = _company_name_set(diff.original_text)
        compare_companies = _company_name_set(diff.compare_text)
        if not (original_companies and compare_companies and original_companies == compare_companies):
            return False
        original_party_companies = _party_company_map(diff.original_text)
        compare_party_companies = _party_company_map(diff.compare_text, original_party_companies)
        if not (
            original_party_companies
            and compare_party_companies
            and original_party_companies == compare_party_companies
        ):
            return False
        return _signing_label_residual(diff.original_text) == _signing_label_residual(diff.compare_text)

    @staticmethod
    def _looks_like_signing_contact_table_label_noise(diff: DiffItem) -> bool:
        if diff.source_type != "table" or diff.diff_type != "MODIFY":
            return False
        if "SIGNING_TABLE_LABEL_NOISE" not in diff.review_flags:
            return False
        text = f"{diff.original_text}\n{diff.compare_text}"
        if re.search(r"\d{4}\s*年|\d{8}|[\d０-９]{1,2}\s*月\s*[\d０-９]{1,2}\s*日", text):
            return False
        return True

    def _looks_like_isolated_seal_artifact_text(self, diff: DiffItem) -> bool:
        if diff.source_type != "seal":
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if "SEAL_OR_SIGNATURE_RISK" not in flags:
            return False
        changed = self._compact(self._changed_text(diff))
        if not changed or len(changed) > 2:
            return False
        text = self._changed_text(diff)
        if self.business_token_pattern.search(text) or self._canonical_date(text):
            return False
        if re.search(r"[\d¥￥]|甲|乙|签|章|[\u4e00-\u9fff]{2}", text or ""):
            return False
        return changed in {"图", "圈", "圆", "印", "红", "点", "口", "o"}

    def _looks_like_visual_seal_ocr_fragment(
        self,
        diff: DiffItem,
        *,
        original_document: Document | None,
        compare_document: Document | None,
    ) -> bool:
        if diff.source_type != "clause":
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if not {"SEAL_OR_SIGNATURE_RISK", "POSSIBLE_OCR_NOISE"}.issubset(flags):
            return False
        changed = self._compact(self._changed_text(diff))
        if not changed or len(changed) > 8:
            return False
        if self.business_token_pattern.search(changed) or self._canonical_date(changed) or re.search(r"\d", changed):
            return False

        sides: list[tuple[Document | None, list[EvidenceBox], str]] = []
        if self._compact(diff.original_snippet):
            sides.append((original_document, diff.original_evidence, diff.original_snippet))
        if self._compact(diff.compare_snippet):
            sides.append((compare_document, diff.compare_evidence, diff.compare_snippet))
        if not sides:
            return False

        inspector = RedSealVisualInspector()
        inspected = 0
        for document, evidences, snippet in sides:
            if document is None:
                return False
            matching = [
                evidence
                for evidence in evidences
                if self._compact(evidence.text) and self._compact(evidence.text) in self._compact(snippet)
            ]
            if not matching:
                return False
            for evidence in matching:
                metrics = inspector.inspect(document, evidence.page_no, evidence.bbox)
                if metrics.red_pixels < 30 or metrics.ratio < 0.08:
                    return False
                inspected += 1
        return inspected > 0

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
            if changed and text not in changed and not any(text and text in item for item in changed):
                continue
            if (
                evidence.bbox.x0 <= 36.0
                or evidence.bbox.x1 <= 40.0
                or evidence.bbox.x0 >= 480.0
                or evidence.bbox.y0 >= 760.0
            ):
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
        if diff.diff_type != "MODIFY":
            return False
        flags = set(diff.review_flags)
        if diff.source_type == "clause":
            if "CRITICAL_FIELD_CHANGE" not in flags:
                return False
            if not flags.intersection({"CRITICAL_FIELD_DATE_CHANGE", "CRITICAL_FIELD_DURATION_CHANGE"}):
                return False
            context = f"{diff.original_text}\n{diff.compare_text}"
            if not self._has_signing_date_context(context):
                return False
            if not self._changed_text_is_signing_date_change(diff):
                return False
        elif diff.source_type == "table":
            if not self._looks_like_table_signing_date_change(diff):
                return False
        else:
            return False
        original_snippet = self._strip_signing_date_placeholder(diff.original_snippet)
        compare_snippet = self._strip_signing_date_placeholder(diff.compare_snippet)
        if diff.source_type == "table":
            original_snippet = original_snippet or self._signing_date_snippet(diff.original_text)
            compare_snippet = compare_snippet or self._signing_date_snippet(diff.compare_text)
        field_diff_type = self._signing_date_diff_type(original_snippet, compare_snippet)
        if field_diff_type is None:
            return False
        diff.source_type = "metadata"
        diff.title = "签署日期"
        diff.section_type = "signature"
        diff.original_snippet = original_snippet
        diff.compare_snippet = compare_snippet
        diff.diff_type = field_diff_type
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
        self._normalize_signing_date_change_payload(diff)
        self._add_flag(diff, "SIGNING_DATE_FIELD_CHANGE")
        for flag in (
            "CRITICAL_FIELD_CHANGE",
            "CRITICAL_FIELD_DATE_CHANGE",
            "CRITICAL_FIELD_DURATION_CHANGE",
            "CRITICAL_VALUE_CHANGE",
        ):
            self._remove_flag(diff, flag)
        return True

    def _normalize_signing_date_change_payload(self, diff: DiffItem) -> None:
        if diff.diff_type == "ADD":
            diff.original_snippet = ""
            diff.original_evidence = []
            diff.original_change_ranges = []
            self._set_highlight_type(diff.compare_evidence, "ADD")
            self._set_highlight_type(diff.compare_change_ranges, "ADD")
            diff.readable_change = f"新增签署日期：{diff.compare_snippet}"
            return
        if diff.diff_type == "DELETE":
            diff.compare_snippet = ""
            diff.compare_evidence = []
            diff.compare_change_ranges = []
            self._set_highlight_type(diff.original_evidence, "DELETE")
            self._set_highlight_type(diff.original_change_ranges, "DELETE")
            diff.readable_change = f"删除签署日期：{diff.original_snippet}"
            return
        self._set_highlight_type(diff.original_evidence, "MODIFY")
        self._set_highlight_type(diff.compare_evidence, "MODIFY")
        self._set_highlight_type(diff.original_change_ranges, "MODIFY")
        self._set_highlight_type(diff.compare_change_ranges, "MODIFY")
        diff.readable_change = f"签署日期变更：{diff.original_snippet} → {diff.compare_snippet}"

    @staticmethod
    def _set_highlight_type(items: list, highlight_type: DiffType) -> None:
        for item in items:
            item.highlight_type = highlight_type

    def _looks_like_table_signing_date_change(self, diff: DiffItem) -> bool:
        if diff.source_type != "table" or diff.diff_type != "MODIFY":
            return False
        flags = set(diff.review_flags)
        if "TABLE_REGION_REVIEW" in flags:
            return False
        context = f"{diff.title}\n{diff.original_text}\n{diff.compare_text}"
        if not re.search(r"签订时间|签署日期|签字日期|签订日期", context):
            return False
        original = self._strip_signing_date_placeholder(diff.original_snippet) or self._signing_date_snippet(
            diff.original_text
        )
        compare = self._strip_signing_date_placeholder(diff.compare_snippet) or self._signing_date_snippet(
            diff.compare_text
        )
        return self._signing_date_diff_type(original, compare) is not None

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
        snippet_date = self._signing_date_value(snippet)
        for range_ in ranges:
            fragment = text[range_.start : range_.end]
            if self._compact(fragment) in snippet_key or (
                snippet_date and self._signing_date_value(fragment) == snippet_date
            ):
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

    def _changed_text_is_signing_date_change(self, diff: DiffItem) -> bool:
        original = self._strip_signing_date_placeholder(diff.original_snippet)
        compare = self._strip_signing_date_placeholder(diff.compare_snippet)
        return self._signing_date_diff_type(original, compare) is not None

    def _signing_date_diff_type(self, original: str, compare: str) -> DiffType | None:
        original_date = self._signing_date_value(original)
        compare_date = self._signing_date_value(compare)
        if not original_date and compare_date:
            return "ADD"
        if original_date and not compare_date:
            return "DELETE"
        if original_date and compare_date and original_date != compare_date:
            return "MODIFY"
        return None

    def _signing_date_snippet(self, text: str) -> str:
        return self._signing_date_value(text).replace("-", "")

    @staticmethod
    def _signing_date_value(text: str) -> str:
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
        match = re.search(r"(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日", compact)
        if match:
            return DiffQualityProcessor._validated_date_value(
                match.group("year"), match.group("month"), match.group("day")
            )
        match = re.search(r"(?P<year>\d{4})年(?P<month_day>\d{3,4})日", compact)
        if match:
            month_day = match.group("month_day")
            candidates = (
                [(month_day[:2], month_day[2:])]
                if len(month_day) == 4
                else [(month_day[:1], month_day[1:]), (month_day[:2], month_day[2:])]
            )
            for month, day in candidates:
                value = DiffQualityProcessor._validated_date_value(match.group("year"), month, day)
                if value:
                    return value
        match = re.search(r"(?<!\d)(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})(?!\d)", compact)
        if match:
            return DiffQualityProcessor._validated_date_value(
                match.group("year"), match.group("month"), match.group("day")
            )
        return ""

    @staticmethod
    def _validated_date_value(year: str, month: str, day: str) -> str:
        try:
            year_value = int(year)
            month_value = int(month)
            day_value = int(day)
        except ValueError:
            return ""
        if not 1900 <= year_value <= 2200:
            return ""
        try:
            return date(year_value, month_value, day_value).isoformat()
        except ValueError:
            return ""

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
        return "table_region_coverage_gap" in flags or _is_signing_contact_table_label_loss(diff, flags)

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

    def _is_cross_source_merged_multi_page_footer(self, diff: DiffItem) -> bool:
        return (
            diff.source_type == "header_footer"
            and "CROSS_SOURCE_MERGED" in diff.review_flags
            and self._has_multi_page_evidence(diff)
        )

    def _looks_like_page_number_edge_annotation_noise(self, diff: DiffItem) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if not flags.intersection({"READING_ORDER_RISK", "READING_ORDER_REPAIRED", "PAGE_UNRELIABLE"}):
            return False
        original = unicodedata.normalize("NFKC", diff.original_snippet or "")
        compare = unicodedata.normalize("NFKC", diff.compare_snippet or "")
        if not (self._contains_page_number_marker(original) or self._contains_page_number_marker(compare)):
            return False
        changed_compact = self._compact(f"{original}{compare}")
        has_edge_evidence = self._changed_evidence_is_near_page_edge(diff)
        if len(changed_compact) > 16 and not has_edge_evidence:
            return False
        residual = self._compact(self._remove_page_number_markers(f"{original}{compare}"))
        if not residual:
            return True
        if has_edge_evidence:
            edge_text = "".join(self._changed_evidence_texts_near_page_edge(diff))
            return self._changed_text_is_footer_annotation_residual(edge_text or f"{original}{compare}")
        if not re.fullmatch(r"[\u4e00-\u9fff]{1,4}", residual):
            return False
        return "OCR_REMEDIATION_PLANNED" in flags and len(residual) <= 2

    def _trim_edge_annotation_from_mixed_diff(self, diff: DiffItem) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        changed = diff.compare_snippet or diff.compare_text
        if not changed:
            return False
        updated = changed
        for text in self._changed_evidence_texts_near_page_edge(diff):
            compact = self._compact(text)
            if compact and len(compact) <= 4 and not self.business_token_pattern.search(compact):
                updated = updated.replace(text, "")
        updated = updated.strip()
        if not updated or updated == changed:
            return False
        if not self._mixed_diff_residual_is_meaningful(updated):
            return False
        diff.compare_snippet = updated
        if changed in diff.compare_text:
            diff.compare_text = diff.compare_text.replace(changed, updated)
        return True

    def _mixed_diff_residual_is_meaningful(self, text: str) -> bool:
        compact = self._compact(text)
        if not compact:
            return False
        return bool(self.business_token_pattern.search(text) or re.search(r"\d", compact))

    def _reclassify_unit_separator_change(self, diff: DiffItem) -> bool:
        if diff.source_type != "clause" or diff.diff_type != "MODIFY":
            return False
        original = unicodedata.normalize("NFKC", diff.original_text or "")
        compare = unicodedata.normalize("NFKC", diff.compare_text or "")
        if not re.search(r"\d+\s*万元\s*/\s*人次", original):
            return False
        if not re.search(r"\d+\s*万元\s*人次", compare):
            return False
        if self._compact(original.replace("/", "")) != self._compact(compare):
            return False
        self._remove_flag(diff, "CRITICAL_FIELD_AMOUNT_CHANGE")
        self._remove_flag(diff, "CRITICAL_VALUE_CHANGE")
        self._add_flag(diff, "UNIT_FORMAT_CHANGE_REVIEW")
        diff.quality_status = "NEEDS_REVIEW"
        return True

    def _looks_like_edge_annotation_clause_noise(self, diff: DiffItem) -> bool:
        if diff.source_type != "clause":
            return False
        flags = set(diff.review_flags) | set(diff.structural_flags)
        if not flags.intersection({"READING_ORDER_RISK", "READING_ORDER_REPAIRED", "PAGE_UNRELIABLE"}):
            return False
        if not flags.intersection(
            {"POSSIBLE_OCR_NOISE", "POSSIBLE_CLAUSE_MISMATCH", "LOW_CONFIDENCE_MATCH", "PUNCTUATED_HEADING"}
        ):
            return False
        changed = self._compact(self._changed_text(diff))
        if self._changed_text_has_business_token(diff):
            return False
        if re.fullmatch(r"[\u4e00-\u9fffA-Za-z#\d]{1,12}", changed):
            return self._changed_evidence_is_near_page_edge(diff)
        edge_text = self._compact("".join(self._changed_evidence_texts_near_page_edge(diff)))
        if edge_text and len(edge_text) <= 12 and self._changed_evidence_is_near_page_edge(diff):
            return True
        return (
            "PUNCTUATED_HEADING" in flags
            and self._compare_evidence_is_short_signing_or_footer_noise(diff)
            and self._changed_evidence_is_near_page_edge(diff)
        )

    def _changed_evidence_texts_near_page_edge(self, diff: DiffItem) -> list[str]:
        texts: list[str] = []
        for evidence in [*diff.original_evidence, *diff.compare_evidence]:
            bbox = evidence.bbox
            if bbox.y0 >= 760.0 or bbox.x0 <= 36.0 or bbox.x0 >= 390.0:
                texts.append(evidence.text or "")
        return texts

    def _changed_text_is_footer_annotation_residual(self, text: str) -> bool:
        compact = self._compact(self._remove_page_number_markers(text))
        if not compact:
            return True
        if len(compact) <= 8 and not self.business_token_pattern.search(compact):
            return True
        return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z#]{1,8}", compact))

    def _compare_evidence_is_short_signing_or_footer_noise(self, diff: DiffItem) -> bool:
        if not diff.compare_evidence:
            return False
        text = self._compact("".join(evidence.text or "" for evidence in diff.compare_evidence))
        if not text or len(text) > 12 or self.business_token_pattern.search(text):
            return False
        return any(
            evidence.bbox.y0 >= 760.0
            or evidence.bbox.x0 <= 36.0
            or evidence.bbox.x0 >= 390.0
            or (evidence.bbox.y1 - evidence.bbox.y0) >= 36.0
            for evidence in diff.compare_evidence
        )

    @staticmethod
    def _contains_page_number_marker(text: str) -> bool:
        normalized = unicodedata.normalize("NFKC", text or "")
        return bool(
            re.search(r"[—－-]\s*\d{1,3}\s*[—－-]", normalized)
            or re.search(r"^\s*\d{1,3}\s*[—－-]\s*$", normalized)
            or re.search(r"^\s*[—－-]\s*\d{1,3}\s*$", normalized)
        )

    @staticmethod
    def _remove_page_number_markers(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text or "")
        normalized = re.sub(r"[—－-]\s*\d{1,3}\s*[—－-]", "", normalized)
        normalized = re.sub(r"^\s*\d{1,3}\s*[—－-]\s*", "", normalized)
        normalized = re.sub(r"^\s*[—－-]\s*\d{1,3}\s*", "", normalized)
        return normalized

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
