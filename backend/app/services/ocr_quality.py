from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.models import (
    DiffItem,
    Document,
    DocumentProfile,
    LayoutQualityReport,
    OcrQualitySide,
    OcrQualityStatus,
    Page,
    PageLayoutQualityReport,
    PageOcrQualityProfile,
    PageProfile,
    ParseWarningDetail,
    TaskOcrQualitySummary,
    TextBlock,
)


STATUS_PRIORITY: dict[OcrQualityStatus, int] = {
    "OK": 0,
    "SEAL_OR_SIGNATURE_RISK": 1,
    "LOW_TEXT_CONFIDENCE": 2,
    "LAYOUT_MISMATCH": 3,
    "READING_ORDER_RISK": 4,
    "TABLE_RISK": 5,
    "UNRELIABLE": 6,
}

_SIDE_ORDER: dict[OcrQualitySide, int] = {"original": 0, "compare": 1}


@dataclass(frozen=True)
class OcrQualityThresholds:
    low_avg_confidence: float = 0.75
    low_block_confidence: float = 0.75
    low_confidence_block_ratio: float = 0.20
    min_layout_match_rate: float = 0.85


class OcrQualityProfiler:
    """Build deterministic page-level OCR quality profiles."""

    business_token_pattern = re.compile(
        r"(\d|%|‰|元|万元|亿元|付款|支付|金额|日期|期限|甲方|乙方|公司|交付|违约|责任|终止|解除)"
    )

    def __init__(self, thresholds: OcrQualityThresholds | None = None) -> None:
        self.thresholds = thresholds or OcrQualityThresholds()

    def profile_side(
        self,
        side: OcrQualitySide,
        document: Document,
        document_profile: DocumentProfile | None,
        layout_quality: LayoutQualityReport | None,
        warnings: list[ParseWarningDetail | str] | None,
    ) -> list[PageOcrQualityProfile]:
        page_profiles = {
            page_profile.page_no: page_profile
            for page_profile in (document_profile.page_profiles if document_profile is not None else [])
        }
        layout_pages = {
            page_quality.page_no: page_quality
            for page_quality in (layout_quality.page_quality if layout_quality is not None else [])
        }
        warning_details = self._coerce_warnings(warnings)

        profiles: list[PageOcrQualityProfile] = []
        for page in sorted(document.pages, key=lambda item: item.page_no):
            page_profile = page_profiles.get(page.page_no)
            layout_page = layout_pages.get(page.page_no) or self._single_page_aggregate_layout_page(
                document,
                layout_quality,
                page.page_no,
            )
            page_warnings = self._warnings_for_page(warning_details, page.page_no)
            metrics = self._metrics(page, page_profile, layout_page, layout_quality)
            reasons, categories = self._reasons(page, page_profile, layout_page, layout_quality, metrics, page_warnings)
            status = self._status(reasons, categories)
            profiles.append(
                PageOcrQualityProfile(
                    side=side,
                    page_no=page.page_no,
                    status=status,
                    score=self._score(reasons, categories),
                    reasons=sorted(reasons),
                    metrics=metrics,
                    affected_diff_ids=[],
                )
            )
        return profiles

    def build_summary(self, profiles: Iterable[PageOcrQualityProfile]) -> TaskOcrQualitySummary:
        ordered_profiles = sorted(
            profiles,
            key=lambda profile: (_SIDE_ORDER.get(profile.side, 99), profile.page_no),
        )
        page_count_by_status: dict[OcrQualityStatus, int] = {}
        affected_diff_ids: set[str] = set()
        highest_status: OcrQualityStatus = "OK"

        for profile in ordered_profiles:
            page_count_by_status[profile.status] = page_count_by_status.get(profile.status, 0) + 1
            affected_diff_ids.update(profile.affected_diff_ids)
            if STATUS_PRIORITY[profile.status] > STATUS_PRIORITY[highest_status]:
                highest_status = profile.status

        risk_page_count = sum(1 for profile in ordered_profiles if profile.status != "OK")
        return TaskOcrQualitySummary(
            status=highest_status,
            requires_review=bool(risk_page_count or affected_diff_ids),
            page_count_by_status=page_count_by_status,
            risk_page_count=risk_page_count,
            affected_diff_count=len(affected_diff_ids),
            profiles=ordered_profiles,
        )

    def apply_to_diffs(
        self,
        diffs: Iterable[DiffItem],
        profiles: Iterable[PageOcrQualityProfile],
    ) -> TaskOcrQualitySummary:
        profile_list = list(profiles)
        risk_profiles_by_key = {
            (profile.side, profile.page_no): profile for profile in profile_list if profile.status != "OK"
        }
        risk_keys = sorted(risk_profiles_by_key, key=lambda key: (_SIDE_ORDER.get(key[0], 99), key[1]))

        for diff in diffs:
            matched_profiles = self._matched_profiles(diff, risk_profiles_by_key)
            if matched_profiles:
                for profile in matched_profiles:
                    flags, needs_review = self._diff_flags_for_profile(diff, profile)
                    self._add_review_flags(diff, flags)
                    if needs_review:
                        diff.quality_status = "NEEDS_REVIEW"
                    self._add_affected_diff_id(profile, diff.diff_id)
                continue

            if (
                not diff.original_evidence
                and not diff.compare_evidence
                and diff.source_type in {"clause", "page", "table", "metadata"}
                and risk_keys
            ):
                self._add_review_flags(diff, ["EVIDENCE_UNRELIABLE"])
                diff.quality_status = "NEEDS_REVIEW"
                for profile in self._missing_evidence_profiles(diff, risk_profiles_by_key, risk_keys):
                    self._add_affected_diff_id(profile, diff.diff_id)

        for profile in profile_list:
            profile.affected_diff_ids = sorted(dict.fromkeys(profile.affected_diff_ids))

        return self.build_summary(profile_list)

    def _metrics(
        self,
        page: Page,
        page_profile: PageProfile | None,
        layout_page: PageLayoutQualityReport | None,
        layout_quality: LayoutQualityReport | None,
    ) -> dict[str, int | float]:
        text_blocks = [block for block in page.blocks if self._block_family(block) == "text"]
        table_blocks = [block for block in page.blocks if self._block_family(block) == "table"]
        confidence_blocks = [block for block in page.blocks if self._has_confidence_content(block)]
        low_confidence_blocks = [
            block
            for block in confidence_blocks
            if block.confidence is not None and block.confidence < self.thresholds.low_block_confidence
        ]

        avg_confidence = page_profile.avg_confidence if page_profile is not None else None
        if avg_confidence is None and confidence_blocks:
            avg_confidence = sum(block.confidence for block in confidence_blocks if block.confidence is not None) / len(
                confidence_blocks
            )

        metrics: dict[str, int | float] = {
            "text_block_count": page_profile.text_block_count if page_profile is not None else len(text_blocks),
            "table_block_count": page_profile.table_block_count if page_profile is not None else len(table_blocks),
            "low_confidence_block_ratio": round(len(low_confidence_blocks) / len(confidence_blocks), 4)
            if confidence_blocks
            else 0.0,
        }
        if avg_confidence is not None:
            metrics["avg_confidence"] = round(avg_confidence, 4)

        if layout_page is not None:
            metrics.update(
                {
                    "ocr_block_count": layout_page.ocr_block_count,
                    "matched_ocr_block_count": layout_page.matched_ocr_block_count,
                    "meaningful_unmatched_count": layout_page.meaningful_unmatched_count,
                    "reading_order_conflict_count": layout_page.reading_order_conflict_count,
                }
            )
            if layout_page.ocr_block_count:
                metrics["layout_match_rate"] = round(
                    layout_page.matched_ocr_block_count / layout_page.ocr_block_count, 4
                )

        if (
            layout_quality is not None
            and self._is_single_page_layout_report(layout_quality)
            and self._is_table_page(page, page_profile)
        ):
            metrics["table_cell_unmatched_count"] = layout_quality.table_cell_unmatched_count

        return metrics

    def _reasons(
        self,
        page: Page,
        page_profile: PageProfile | None,
        layout_page: PageLayoutQualityReport | None,
        layout_quality: LayoutQualityReport | None,
        metrics: dict[str, int | float],
        warnings: list[ParseWarningDetail],
    ) -> tuple[set[str], set[str]]:
        reasons: set[str] = set()
        categories: set[str] = set()

        if any(warning.severity == "ERROR" for warning in warnings):
            reasons.add("EXTRACTION_ERROR_WARNING")
            categories.add("extraction")

        avg_confidence = metrics.get("avg_confidence")
        if isinstance(avg_confidence, int | float) and avg_confidence < self.thresholds.low_avg_confidence:
            reasons.add("LOW_AVG_CONFIDENCE")
            categories.add("low_text")
        elif metrics["low_confidence_block_ratio"] > self.thresholds.low_confidence_block_ratio:
            reasons.add("LOW_CONFIDENCE_BLOCK_RATIO")
            categories.add("low_text")

        if layout_page is not None:
            if layout_page.meaningful_unmatched_count > 0:
                reasons.add("MEANINGFUL_UNMATCHED_OCR")
                categories.add("layout")
            layout_match_rate = metrics.get("layout_match_rate")
            if isinstance(layout_match_rate, int | float) and layout_match_rate < self.thresholds.min_layout_match_rate:
                reasons.add("LOW_LAYOUT_MATCH_RATE")
                categories.add("layout")
            if layout_page.reading_order_conflict_count > 0:
                reasons.add("READING_ORDER_CONFLICT")
                categories.add("reading_order")

        if (
            layout_quality is not None
            and self._is_single_page_layout_report(layout_quality)
            and layout_quality.table_cell_unmatched_count > 0
            and self._is_table_page(page, page_profile)
        ):
            reasons.add("TABLE_CELL_UNMATCHED")
            categories.add("table")

        if any(self._block_family(block) == "seal" for block in page.blocks):
            reasons.add("SEAL_OR_SIGNATURE_DETECTED")
            categories.add("seal")

        return reasons, categories

    def _status(self, reasons: set[str], categories: set[str]) -> OcrQualityStatus:
        if "EXTRACTION_ERROR_WARNING" in reasons or len(categories) >= 2:
            return "UNRELIABLE"
        if "table" in categories:
            return "TABLE_RISK"
        if "reading_order" in categories:
            return "READING_ORDER_RISK"
        if "layout" in categories:
            return "LAYOUT_MISMATCH"
        if "low_text" in categories:
            return "LOW_TEXT_CONFIDENCE"
        if "seal" in categories:
            return "SEAL_OR_SIGNATURE_RISK"
        return "OK"

    def _score(self, reasons: set[str], categories: set[str]) -> float:
        score = 1.0
        score -= 0.2 * len(categories)
        if "EXTRACTION_ERROR_WARNING" in reasons:
            score -= 0.4
        if "LOW_LAYOUT_MATCH_RATE" in reasons:
            score -= 0.1
        if "TABLE_CELL_UNMATCHED" in reasons:
            score -= 0.1
        return round(max(0.0, score), 4)

    def _coerce_warnings(self, warnings: list[ParseWarningDetail | str] | None) -> list[ParseWarningDetail]:
        warning_details: list[ParseWarningDetail] = []
        for warning in warnings or []:
            if isinstance(warning, ParseWarningDetail):
                warning_details.append(warning)
            else:
                message = str(warning).strip()
                if message:
                    warning_details.append(ParseWarningDetail(code="OCR_WARNING", message=message, source="ocr"))
        return warning_details

    def _warnings_for_page(self, warnings: list[ParseWarningDetail], page_no: int) -> list[ParseWarningDetail]:
        return [warning for warning in warnings if warning.page_no in {None, page_no}]

    def _matched_profiles(
        self,
        diff: DiffItem,
        risk_profiles_by_key: dict[tuple[OcrQualitySide, int], PageOcrQualityProfile],
    ) -> list[PageOcrQualityProfile]:
        matched_by_key: dict[tuple[OcrQualitySide, int], PageOcrQualityProfile] = {}
        for evidence in diff.original_evidence:
            key: tuple[OcrQualitySide, int] = ("original", evidence.page_no)
            if key in risk_profiles_by_key:
                matched_by_key[key] = risk_profiles_by_key[key]
        for evidence in diff.compare_evidence:
            key = ("compare", evidence.page_no)
            if key in risk_profiles_by_key:
                matched_by_key[key] = risk_profiles_by_key[key]
        return [
            matched_by_key[key]
            for key in sorted(matched_by_key, key=lambda item: (_SIDE_ORDER.get(item[0], 99), item[1]))
        ]

    def _diff_flags_for_profile(self, diff: DiffItem, profile: PageOcrQualityProfile) -> tuple[list[str], bool]:
        flags: list[str] = []
        needs_review = False

        if profile.status == "UNRELIABLE":
            flags.append("PAGE_UNRELIABLE")
            needs_review = True
        if profile.status in {"LOW_TEXT_CONFIDENCE", "UNRELIABLE"}:
            flags.append("OCR_LOW_CONFIDENCE")
            needs_review = needs_review or self._has_business_token(diff)
        if profile.status in {"LAYOUT_MISMATCH", "UNRELIABLE"}:
            flags.append("LAYOUT_MISMATCH_RISK")
            needs_review = True
        if profile.status in {"READING_ORDER_RISK", "UNRELIABLE"}:
            flags.append("READING_ORDER_RISK")
            needs_review = True
        if profile.status in {"TABLE_RISK", "UNRELIABLE"}:
            flags.append("TABLE_STRUCTURE_UNRELIABLE")
            needs_review = True
        if profile.status in {"SEAL_OR_SIGNATURE_RISK", "UNRELIABLE"}:
            flags.append("SEAL_OR_SIGNATURE_RISK")

        return sorted(dict.fromkeys(flags)), needs_review

    def _missing_evidence_profiles(
        self,
        diff: DiffItem,
        risk_profiles_by_key: dict[tuple[OcrQualitySide, int], PageOcrQualityProfile],
        risk_keys: list[tuple[OcrQualitySide, int]],
    ) -> list[PageOcrQualityProfile]:
        preferred_sides: tuple[OcrQualitySide, ...]
        if diff.diff_type == "ADD":
            preferred_sides = ("compare",)
        elif diff.diff_type == "DELETE":
            preferred_sides = ("original",)
        else:
            preferred_sides = ("original", "compare")

        selected_keys = [key for key in risk_keys if key[0] in preferred_sides]
        if not selected_keys:
            selected_keys = risk_keys
        return [risk_profiles_by_key[key] for key in selected_keys]

    def _has_business_token(self, diff: DiffItem) -> bool:
        text = "\n".join(
            [
                diff.title,
                diff.original_text,
                diff.compare_text,
                diff.original_snippet,
                diff.compare_snippet,
            ]
        )
        return bool(self.business_token_pattern.search(text))

    def _add_review_flags(self, diff: DiffItem, flags: Iterable[str]) -> None:
        diff.review_flags = sorted(dict.fromkeys([*diff.review_flags, *flags]))

    def _add_affected_diff_id(self, profile: PageOcrQualityProfile, diff_id: str) -> None:
        profile.affected_diff_ids = sorted(dict.fromkeys([*profile.affected_diff_ids, diff_id]))

    def _is_table_page(self, page: Page, page_profile: PageProfile | None) -> bool:
        if page_profile is not None and (page_profile.table_heavy or page_profile.table_block_count > 0):
            return True
        return any(self._block_family(block) == "table" for block in page.blocks)

    def _is_single_page_layout_report(self, layout_quality: LayoutQualityReport) -> bool:
        page_count = layout_quality.page_count or len(layout_quality.page_quality)
        return page_count == 1

    def _single_page_aggregate_layout_page(
        self,
        document: Document,
        layout_quality: LayoutQualityReport | None,
        page_no: int,
    ) -> PageLayoutQualityReport | None:
        if layout_quality is None or layout_quality.page_quality or len(document.pages) != 1:
            return None
        if layout_quality.page_count > 1:
            return None
        return PageLayoutQualityReport(
            page_no=page_no,
            ocr_block_count=layout_quality.ocr_block_count,
            matched_ocr_block_count=layout_quality.matched_ocr_block_count,
            meaningful_unmatched_count=layout_quality.meaningful_unmatched_count,
            reading_order_conflict_count=layout_quality.reading_order_conflict_count,
        )

    def _has_confidence_content(self, block: TextBlock) -> bool:
        if block.confidence is None:
            return False
        if self._block_family(block) == "text":
            return bool((block.text or "").strip())
        if self._block_family(block) == "table":
            return bool((block.text or "").strip() or block.raw_html or block.table_cell_bboxes)
        return False

    def _block_family(self, block: TextBlock) -> str:
        block_type = (block.block_type or "").lower()
        if block_type in {"table", "table_title", "table_cell"} or block.raw_html:
            return "table"
        if block_type in {"seal", "signature", "stamp"}:
            return "seal"
        if block_type in {"image", "figure", "chart"}:
            return "image"
        return "text"
