from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from app.models import DiffItem, PageOcrQualityProfile, ParseWarningDetail, TaskOcrQualitySummary


PageRouteType = Literal["text_heavy", "table_heavy", "scan_low_quality", "seal_signature", "mixed"]
RouteRecommendation = Literal[
    "KEEP_CURRENT",
    "HIGH_DPI_PAGE_RETRY",
    "TABLE_REGION_RETRY",
    "CRITICAL_FIELD_RETRY",
    "MANUAL_REVIEW",
    "NO_ROUTE",
]
RouteSummaryStatus = Literal["OK", "RETRY_RECOMMENDED", "MANUAL_REVIEW_RECOMMENDED"]
CostLevel = Literal["none", "low", "medium", "high"]

CRITICAL_TOKENS = (
    "金额",
    "价款",
    "付款",
    "支付",
    "日期",
    "期限",
    "交付",
    "验收",
    "违约",
    "责任",
    "终止",
    "解除",
    "数量",
    "单价",
    "总价",
    "party",
    "payment",
    "delivery",
    "liability",
    "termination",
    "breach",
)
RETRY_RECOMMENDATIONS = {"HIGH_DPI_PAGE_RETRY", "TABLE_REGION_RETRY", "CRITICAL_FIELD_RETRY"}


class PageModelRoute(BaseModel):
    side: Literal["original", "compare"]
    page_no: int
    page_type: PageRouteType
    recommended_route: RouteRecommendation
    reason_codes: list[str] = Field(default_factory=list)
    affected_diff_ids: list[str] = Field(default_factory=list)
    quality_status: str = "OK"
    estimated_cost_level: CostLevel = "none"
    should_execute: bool = False
    notes: list[str] = Field(default_factory=list)


class TaskModelRoutingSummary(BaseModel):
    status: RouteSummaryStatus = "OK"
    route_count: int = 0
    retry_recommended_count: int = 0
    manual_review_recommended_count: int = 0
    page_count_by_type: dict[str, int] = Field(default_factory=dict)
    route_count_by_recommendation: dict[str, int] = Field(default_factory=dict)
    routes: list[PageModelRoute] = Field(default_factory=list)


class ModelRoutingAnalyzer:
    """Recommend OCR routing strategies from existing quality signals without mutating diffs."""

    def analyze(
        self,
        ocr_quality_summary: TaskOcrQualitySummary | None,
        diffs: list[DiffItem],
        warnings: list[ParseWarningDetail] | None = None,
    ) -> TaskModelRoutingSummary:
        profiles = list(ocr_quality_summary.profiles) if ocr_quality_summary is not None else []
        routes = [
            self._route_for_profile(profile, diffs, warnings or [])
            for profile in sorted(profiles, key=lambda item: (item.side, item.page_no))
        ]
        return self._summary(routes)

    def _route_for_profile(
        self,
        profile: PageOcrQualityProfile,
        diffs: list[DiffItem],
        warnings: list[ParseWarningDetail],
    ) -> PageModelRoute:
        affected_diff_ids = set(profile.affected_diff_ids)
        affected_diffs = [diff for diff in diffs if diff.diff_id in affected_diff_ids]
        warning_codes = self._warning_codes(profile, warnings)
        reason_codes = sorted({*profile.reasons, *warning_codes})
        page_type = self._page_type(profile, affected_diffs, reason_codes)
        recommendation = self._recommendation(profile, affected_diffs, page_type)
        return PageModelRoute(
            side=profile.side,
            page_no=profile.page_no,
            page_type=page_type,
            recommended_route=recommendation,
            reason_codes=reason_codes,
            affected_diff_ids=list(profile.affected_diff_ids),
            quality_status=profile.status,
            estimated_cost_level=self._cost_level(recommendation),
            should_execute=False,
            notes=[f"score={profile.score:.2f}"],
        )

    def _page_type(
        self,
        profile: PageOcrQualityProfile,
        affected_diffs: list[DiffItem],
        reason_codes: list[str],
    ) -> PageRouteType:
        categories: set[PageRouteType] = set()
        if profile.status in {"LOW_TEXT_CONFIDENCE", "UNRELIABLE"} or any(
            reason in reason_codes
            for reason in {"LOW_AVG_CONFIDENCE", "LOW_CONFIDENCE_BLOCK_RATIO", "MISSING_CHAR_CONFIDENCE"}
        ):
            categories.add("scan_low_quality")
        if (
            profile.status == "TABLE_RISK"
            or "TABLE_CELL_UNMATCHED" in reason_codes
            or any(diff.source_type == "table" for diff in affected_diffs)
        ):
            categories.add("table_heavy")
        if (
            profile.status == "SEAL_OR_SIGNATURE_RISK"
            or any("SEAL" in reason or "SIGNATURE" in reason or "STAMP" in reason for reason in reason_codes)
            or any(diff.source_type == "seal" for diff in affected_diffs)
        ):
            categories.add("seal_signature")
        if len(categories) > 1:
            return "mixed"
        if categories:
            return next(iter(categories))
        return "text_heavy"

    def _recommendation(
        self,
        profile: PageOcrQualityProfile,
        affected_diffs: list[DiffItem],
        page_type: PageRouteType,
    ) -> RouteRecommendation:
        if profile.status == "OK" and not affected_diffs:
            return "KEEP_CURRENT"
        if profile.status == "UNRELIABLE" or page_type in {"mixed", "seal_signature"}:
            return "MANUAL_REVIEW"
        if page_type == "table_heavy":
            return "TABLE_REGION_RETRY"
        if page_type == "scan_low_quality" and self._has_critical_text(affected_diffs):
            return "HIGH_DPI_PAGE_RETRY"
        if page_type == "text_heavy" and self._has_critical_text(affected_diffs):
            return "CRITICAL_FIELD_RETRY"
        if affected_diffs:
            return "MANUAL_REVIEW"
        return "NO_ROUTE"

    @staticmethod
    def _has_critical_text(diffs: list[DiffItem]) -> bool:
        text = " ".join(
            f"{diff.title} {diff.original_text} {diff.compare_text} {diff.original_snippet} {diff.compare_snippet}"
            for diff in diffs
        ).lower()
        return any(token.lower() in text for token in CRITICAL_TOKENS)

    @staticmethod
    def _warning_codes(profile: PageOcrQualityProfile, warnings: list[ParseWarningDetail]) -> list[str]:
        return [
            warning.code
            for warning in warnings
            if warning.page_no == profile.page_no and profile.side in warning.source
        ]

    @staticmethod
    def _cost_level(recommendation: RouteRecommendation) -> CostLevel:
        if recommendation in {"KEEP_CURRENT", "NO_ROUTE"}:
            return "none"
        if recommendation == "CRITICAL_FIELD_RETRY":
            return "low"
        if recommendation in {"TABLE_REGION_RETRY", "HIGH_DPI_PAGE_RETRY"}:
            return "medium"
        return "high"

    @staticmethod
    def _summary(routes: list[PageModelRoute]) -> TaskModelRoutingSummary:
        type_counts = Counter(route.page_type for route in routes)
        recommendation_counts = Counter(route.recommended_route for route in routes)
        retry_count = sum(1 for route in routes if route.recommended_route in RETRY_RECOMMENDATIONS)
        manual_count = recommendation_counts["MANUAL_REVIEW"]
        status: RouteSummaryStatus = "OK"
        if manual_count:
            status = "MANUAL_REVIEW_RECOMMENDED"
        elif retry_count:
            status = "RETRY_RECOMMENDED"
        return TaskModelRoutingSummary(
            status=status,
            route_count=len(routes),
            retry_recommended_count=retry_count,
            manual_review_recommended_count=manual_count,
            page_count_by_type=dict(type_counts),
            route_count_by_recommendation=dict(recommendation_counts),
            routes=routes,
        )
