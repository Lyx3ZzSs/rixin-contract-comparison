from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_THRESHOLDS = {
    "min_recall": 1.0,
    "max_false_positive_count": 0,
    "max_task_failure_count": 0,
    "min_evidence_hit_rate": 1.0,
}

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import CompareTask  # noqa: E402
from app.services.compare_service import CompareService  # noqa: E402
from app.services.model_routing import ModelRoutingAnalyzer  # noqa: E402
from app.utils.json_utils import to_jsonable  # noqa: E402


@dataclass(frozen=True)
class OcrCompareCase:
    case_id: str
    case_dir: Path


def discover_cases(case_root: Path) -> list[OcrCompareCase]:
    return [
        OcrCompareCase(case_id=path.name, case_dir=path)
        for path in sorted(case_root.iterdir())
        if path.is_dir() and (path / "expected.json").exists() and _has_actual_source(path)
    ]


def load_case_inputs(case: OcrCompareCase) -> tuple[dict[str, Any], CompareTask]:
    expected = _read_json(case.case_dir / "expected.json")
    return expected, _load_or_run_actual(case)


def _load_or_run_actual(case: OcrCompareCase) -> CompareTask:
    actual_json = case.case_dir / "actual.json"
    if actual_json.exists():
        return CompareTask(**_read_json(actual_json))

    original_pdf = case.case_dir / "original.pdf"
    compare_pdf = case.case_dir / "compare.pdf"
    if not original_pdf.exists() or not compare_pdf.exists():
        raise FileNotFoundError(
            f"{case.case_dir} must contain actual.json or original.pdf plus compare.pdf"
        )
    return CompareService().compare(
        original_pdf,
        compare_pdf,
        task_id=f"EVAL_OCR_{case.case_id.upper()}",
        original_filename=original_pdf.name,
        compare_filename=compare_pdf.name,
    )


def _has_actual_source(case_dir: Path) -> bool:
    return (case_dir / "actual.json").exists() or (
        (case_dir / "original.pdf").exists() and (case_dir / "compare.pdf").exists()
    )


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class OcrCompareCaseResult:
    case_id: str
    status: str
    expected_count: int
    actual_count: int
    true_positive_count: int
    false_positive_count: int
    false_negative_count: int
    evidence_hit_count: int
    low_confidence_count: int
    ocr_warning_count: int
    task_failure_count: int
    model_routing: dict[str, Any]
    route_metrics: dict[str, Any]
    annotation_summary: dict[str, int]
    matches: list[dict[str, Any]]
    missed_expected_diffs: list[dict[str, Any]]
    unexpected_actual_diffs: list[dict[str, Any]]
    evidence_drift_diffs: list[dict[str, Any]]
    issues: list[str]

    @classmethod
    def failure(cls, case_id: str, error: Exception) -> OcrCompareCaseResult:
        return cls(
            case_id=case_id,
            status="FAILED",
            expected_count=0,
            actual_count=0,
            true_positive_count=0,
            false_positive_count=0,
            false_negative_count=0,
            evidence_hit_count=0,
            low_confidence_count=0,
            ocr_warning_count=0,
            task_failure_count=1,
            model_routing={"status": "OK", "route_count": 0, "routes": []},
            route_metrics=_empty_route_metrics(),
            annotation_summary=_empty_annotation_summary(),
            matches=[],
            missed_expected_diffs=[],
            unexpected_actual_diffs=[],
            evidence_drift_diffs=[],
            issues=[str(error)],
        )

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__ | self.rates()

    def rates(self) -> dict[str, float]:
        precision_denominator = self.true_positive_count + self.false_positive_count
        recall_denominator = self.true_positive_count + self.false_negative_count
        return {
            "precision": _safe_div(self.true_positive_count, precision_denominator),
            "recall": _safe_div(self.true_positive_count, recall_denominator),
            "evidence_hit_rate": _safe_div(
                self.evidence_hit_count, self.true_positive_count
            ),
            "low_confidence_ratio": _safe_div(
                self.low_confidence_count, self.actual_count
            ),
        }


def evaluate_case_root(case_root: Path) -> dict[str, Any]:
    cases = discover_cases(case_root)
    results = []
    for case in cases:
        try:
            results.append(evaluate_case(case))
        except Exception as error:  # noqa: BLE001
            results.append(OcrCompareCaseResult.failure(case.case_id, error))
    aggregate = _aggregate(results)
    report = {
        "case_root": str(case_root),
        "case_count": len(results),
        "thresholds": DEFAULT_THRESHOLDS,
        "aggregate": aggregate,
        "cases": [result.to_dict() for result in results],
    }
    report["threshold_failures"] = threshold_failures(report)
    return report


def evaluate_case(case: OcrCompareCase) -> OcrCompareCaseResult:
    expected_payload, actual_task = load_case_inputs(case)
    all_expected_diffs = expected_payload.get("expected_diffs", [])
    expected_diffs = _approved_expected_diffs(all_expected_diffs)
    actual_diffs = [diff.model_dump(mode="json") for diff in actual_task.diffs]
    matches = _match_expected_diffs(expected_diffs, actual_diffs)
    matched_actual_indexes = {match.actual_index for match in matches}
    issues = _case_issues(expected_diffs, actual_diffs, matches)
    routing_summary = ModelRoutingAnalyzer().analyze(
        actual_task.ocr_quality_summary,
        actual_task.diffs,
        actual_task.parse_warning_details,
    )
    routing_payload = to_jsonable(routing_summary)
    route_metrics = _route_metrics(routing_payload)
    return OcrCompareCaseResult(
        case_id=case.case_id,
        status=actual_task.status,
        expected_count=len(expected_diffs),
        actual_count=len(actual_diffs),
        true_positive_count=len(matches),
        false_positive_count=len(actual_diffs) - len(matched_actual_indexes),
        false_negative_count=len(expected_diffs) - len(matches),
        evidence_hit_count=_matched_evidence_hit_count(
            matches, expected_diffs, actual_diffs
        ),
        low_confidence_count=sum(1 for diff in actual_diffs if _is_low_confidence(diff)),
        ocr_warning_count=sum(
            1
            for warning in actual_task.parse_warning_details
            if _is_ocr_warning(warning.model_dump(mode="json"))
        ),
        task_failure_count=0 if actual_task.status == "COMPLETED" else 1,
        model_routing=routing_payload,
        route_metrics=route_metrics,
        annotation_summary=_annotation_summary(all_expected_diffs),
        matches=_match_details(matches, expected_diffs, actual_diffs),
        missed_expected_diffs=_missed_expected_details(expected_diffs, matches),
        unexpected_actual_diffs=_unexpected_actual_details(actual_diffs, matches),
        evidence_drift_diffs=_evidence_drift_details(
            matches, expected_diffs, actual_diffs
        ),
        issues=issues,
    )


@dataclass(frozen=True)
class ApprovedExpectedDiff:
    source_index: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class DiffMatch:
    expected_index: int
    actual_index: int
    score: float


def _match_expected_diffs(
    expected: list[ApprovedExpectedDiff] | list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> list[DiffMatch]:
    matches: list[DiffMatch] = []
    used_actual: set[int] = set()
    for expected_record in _expected_records(expected):
        best_index = None
        best_score = 0.0
        for actual_index, actual_diff in enumerate(actual):
            if actual_index in used_actual:
                continue
            score = _diff_match_score(expected_record.payload, actual_diff)
            if score > best_score:
                best_index = actual_index
                best_score = score
        if (
            best_index is not None
            and best_score >= 0.72
            and _has_matching_signal(expected_record.payload, actual[best_index])
        ):
            used_actual.add(best_index)
            matches.append(
                DiffMatch(
                    expected_index=expected_record.source_index,
                    actual_index=best_index,
                    score=round(best_score, 4),
                )
            )
    return matches


def _expected_records(
    expected: list[ApprovedExpectedDiff] | list[dict[str, Any]],
) -> list[ApprovedExpectedDiff]:
    return [
        item
        if isinstance(item, ApprovedExpectedDiff)
        else ApprovedExpectedDiff(source_index=index, payload=item)
        for index, item in enumerate(expected)
    ]


def _approved_expected_diffs(
    expected: list[dict[str, Any]],
) -> list[ApprovedExpectedDiff]:
    return [
        ApprovedExpectedDiff(source_index=index, payload=item)
        for index, item in enumerate(expected)
        if item.get("review_status") in (None, "", "APPROVED")
    ]


def _annotation_summary(expected: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "approved_expected_count": sum(
            1 for item in expected if item.get("review_status") in (None, "", "APPROVED")
        ),
        "draft_expected_count": sum(
            1 for item in expected if item.get("review_status") == "DRAFT"
        ),
        "rejected_expected_count": sum(
            1 for item in expected if item.get("review_status") == "REJECTED"
        ),
    }


def _empty_annotation_summary() -> dict[str, int]:
    return {
        "approved_expected_count": 0,
        "draft_expected_count": 0,
        "rejected_expected_count": 0,
    }


def _match_details(
    matches: list[DiffMatch],
    expected: list[ApprovedExpectedDiff],
    actual: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected_by_source_index = _expected_by_source_index(expected)
    return [
        {
            "expected_index": match.expected_index,
            "actual_index": match.actual_index,
            "actual_diff_id": str(actual[match.actual_index].get("diff_id", "")),
            "score": match.score,
            "evidence_hit": _expected_evidence_hit_or_not_required(
                expected_by_source_index[match.expected_index].payload,
                actual[match.actual_index],
            ),
        }
        for match in matches
    ]


def _missed_expected_details(
    expected: list[ApprovedExpectedDiff], matches: list[DiffMatch]
) -> list[dict[str, Any]]:
    matched_expected = {match.expected_index for match in matches}
    return [
        {
            "expected_index": item.source_index,
            "label": _diff_label(item.payload),
            "diff_type": str(item.payload.get("diff_type", "")),
            "source_type": str(item.payload.get("source_type", "")),
        }
        for item in expected
        if item.source_index not in matched_expected
    ]


def _unexpected_actual_details(
    actual: list[dict[str, Any]], matches: list[DiffMatch]
) -> list[dict[str, Any]]:
    matched_actual = {match.actual_index for match in matches}
    return [
        {
            "actual_index": index,
            "diff_id": str(item.get("diff_id", "")),
            "title": str(item.get("title", "")),
            "source_type": str(item.get("source_type", "")),
            "quality_status": str(item.get("quality_status", "")),
            "review_flags": list(item.get("review_flags", [])),
        }
        for index, item in enumerate(actual)
        if index not in matched_actual
    ]


def _evidence_drift_details(
    matches: list[DiffMatch],
    expected: list[ApprovedExpectedDiff],
    actual: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected_by_source_index = _expected_by_source_index(expected)
    return [
        {
            "expected_index": match.expected_index,
            "actual_diff_id": str(actual[match.actual_index].get("diff_id", "")),
            "label": _diff_label(expected_by_source_index[match.expected_index].payload),
        }
        for match in matches
        if expected_by_source_index[match.expected_index].payload.get(
            "expected_evidence", []
        )
        and not _expected_evidence_hits(
            expected_by_source_index[match.expected_index].payload,
            actual[match.actual_index],
        )
    ]


def _expected_by_source_index(
    expected: list[ApprovedExpectedDiff],
) -> dict[int, ApprovedExpectedDiff]:
    return {item.source_index: item for item in expected}


def _diff_match_score(expected: dict[str, Any], actual: dict[str, Any]) -> float:
    score = 0.0
    if expected.get("diff_type") == actual.get("diff_type"):
        score += 0.25
    if expected.get("source_type") and expected.get("source_type") == actual.get(
        "source_type"
    ):
        score += 0.15
    if _contains(actual.get("title", ""), expected.get("title_contains", "")):
        score += 0.15
    if _contains(
        actual.get("original_text", "") + actual.get("original_snippet", ""),
        expected.get("original_contains", ""),
    ):
        score += 0.20
    if _contains(
        actual.get("compare_text", "") + actual.get("compare_snippet", ""),
        expected.get("compare_contains", ""),
    ):
        score += 0.20
    if _expected_evidence_hits(expected, actual):
        score += 0.05
    return score


def _contains(text: str, needle: str) -> bool:
    return bool(needle) and needle in (text or "")


def _has_matching_signal(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return (
        _contains(actual.get("title", ""), expected.get("title_contains", ""))
        or _contains(
            actual.get("original_text", "") + actual.get("original_snippet", ""),
            expected.get("original_contains", ""),
        )
        or _contains(
            actual.get("compare_text", "") + actual.get("compare_snippet", ""),
            expected.get("compare_contains", ""),
        )
        or _expected_evidence_hits(expected, actual)
    )


def _expected_evidence_hits(
    expected: dict[str, Any], actual: dict[str, Any]
) -> bool:
    expected_evidence = expected.get("expected_evidence", [])
    if not expected_evidence:
        return False
    actual_by_side = {
        "original": actual.get("original_evidence", []),
        "compare": actual.get("compare_evidence", []),
    }
    return all(
        any(
            evidence.get("page_no") == expected_item.get("page_no")
            and _bbox_iou(evidence.get("bbox"), expected_item.get("bbox")) >= 0.5
            for evidence in actual_by_side.get(expected_item.get("side", ""), [])
        )
        for expected_item in expected_evidence
    )


def _matched_evidence_hit_count(
    matches: list[DiffMatch],
    expected: list[ApprovedExpectedDiff],
    actual: list[dict[str, Any]],
) -> int:
    expected_by_source_index = _expected_by_source_index(expected)
    return sum(
        1
        for match in matches
        if _expected_evidence_hit_or_not_required(
            expected_by_source_index[match.expected_index].payload,
            actual[match.actual_index],
        )
    )


def _expected_evidence_hit_or_not_required(
    expected: dict[str, Any], actual: dict[str, Any]
) -> bool:
    return not expected.get("expected_evidence", []) or _expected_evidence_hits(
        expected, actual
    )


def _is_low_confidence(diff: dict[str, Any]) -> bool:
    if diff.get("quality_status") == "NEEDS_REVIEW":
        return True
    if any(
        "OCR" in flag or "LOW_CONFIDENCE" in flag for flag in diff.get("review_flags", [])
    ):
        return True
    evidences = [*diff.get("original_evidence", []), *diff.get("compare_evidence", [])]
    if not evidences:
        return True
    return any(
        evidence.get("evidence_quality") == "LOW"
        or float(evidence.get("confidence", 1.0)) < 0.6
        for evidence in evidences
    )


def _case_issues(
    expected: list[ApprovedExpectedDiff],
    actual: list[dict[str, Any]],
    matches: list[DiffMatch],
) -> list[str]:
    matched_expected = {match.expected_index for match in matches}
    matched_actual = {match.actual_index for match in matches}
    issues = [
        f"missed expected diff {item.source_index + 1}: {item.payload.get('title_contains') or item.payload.get('source_type') or item.payload.get('diff_type')}"
        for item in expected
        if item.source_index not in matched_expected
    ]
    issues.extend(
        f"unexpected actual diff {index + 1}: {item.get('title') or item.get('source_type') or item.get('diff_type')}"
        for index, item in enumerate(actual)
        if index not in matched_actual
    )
    issues.extend(_evidence_drift_issues(matches, expected, actual))
    return issues


def _evidence_drift_issues(
    matches: list[DiffMatch],
    expected: list[ApprovedExpectedDiff],
    actual: list[dict[str, Any]],
) -> list[str]:
    expected_by_source_index = _expected_by_source_index(expected)
    return [
        f"evidence drift for expected diff {match.expected_index + 1}: {_diff_label(expected_diff)}"
        for match in matches
        if (
            expected_diff := expected_by_source_index[match.expected_index].payload
        ).get("expected_evidence", [])
        and not _expected_evidence_hits(expected_diff, actual[match.actual_index])
    ]


def _diff_label(diff: dict[str, Any]) -> str:
    return (
        diff.get("title_contains")
        or diff.get("source_type")
        or diff.get("diff_type")
        or "unknown"
    )


def _is_ocr_warning(warning: dict[str, Any]) -> bool:
    return any(
        "ocr" in str(warning.get(field, "")).lower()
        for field in ("source", "code", "message")
    )


def _aggregate(results: list[OcrCompareCaseResult]) -> dict[str, Any]:
    aggregate = OcrCompareCaseResult(
        case_id="TOTAL",
        status=_aggregate_status(results),
        expected_count=sum(item.expected_count for item in results),
        actual_count=sum(item.actual_count for item in results),
        true_positive_count=sum(item.true_positive_count for item in results),
        false_positive_count=sum(item.false_positive_count for item in results),
        false_negative_count=sum(item.false_negative_count for item in results),
        evidence_hit_count=sum(item.evidence_hit_count for item in results),
        low_confidence_count=sum(item.low_confidence_count for item in results),
        ocr_warning_count=sum(item.ocr_warning_count for item in results),
        task_failure_count=sum(item.task_failure_count for item in results),
        model_routing={"status": "OK", "route_count": 0, "routes": []},
        route_metrics=_empty_route_metrics(),
        annotation_summary=_empty_annotation_summary(),
        matches=[],
        missed_expected_diffs=[],
        unexpected_actual_diffs=[],
        evidence_drift_diffs=[],
        issues=[issue for item in results for issue in item.issues],
    )
    payload = aggregate.to_dict()
    payload["route_metrics"] = _aggregate_route_metrics(results)
    payload["annotation_summary"] = _aggregate_annotation_summary(results)
    return payload


def _empty_route_metrics() -> dict[str, Any]:
    return {
        "route_count_by_recommendation": {},
        "page_count_by_type": {},
        "retry_recommended_count": 0,
        "manual_review_recommended_count": 0,
        "precision_by_recommendation": {},
        "recall_by_recommendation": {},
        "evidence_hit_rate_by_recommendation": {},
        "low_confidence_ratio_by_recommendation": {},
    }


def _route_metrics(model_routing: dict[str, Any]) -> dict[str, Any]:
    return {
        "route_count_by_recommendation": dict(
            model_routing.get("route_count_by_recommendation", {})
        ),
        "page_count_by_type": dict(model_routing.get("page_count_by_type", {})),
        "retry_recommended_count": int(
            model_routing.get("retry_recommended_count", 0)
        ),
        "manual_review_recommended_count": int(
            model_routing.get("manual_review_recommended_count", 0)
        ),
        "precision_by_recommendation": {},
        "recall_by_recommendation": {},
        "evidence_hit_rate_by_recommendation": {},
        "low_confidence_ratio_by_recommendation": {},
    }


def _aggregate_route_metrics(results: list[OcrCompareCaseResult]) -> dict[str, Any]:
    route_counts: Counter[str] = Counter()
    page_counts: Counter[str] = Counter()
    retry_count = 0
    manual_count = 0
    for result in results:
        route_metrics = result.route_metrics
        route_counts.update(route_metrics.get("route_count_by_recommendation", {}))
        page_counts.update(route_metrics.get("page_count_by_type", {}))
        retry_count += int(route_metrics.get("retry_recommended_count", 0))
        manual_count += int(route_metrics.get("manual_review_recommended_count", 0))
    return {
        "route_count_by_recommendation": dict(route_counts),
        "page_count_by_type": dict(page_counts),
        "retry_recommended_count": retry_count,
        "manual_review_recommended_count": manual_count,
        "precision_by_recommendation": {},
        "recall_by_recommendation": {},
        "evidence_hit_rate_by_recommendation": {},
        "low_confidence_ratio_by_recommendation": {},
    }


def _aggregate_annotation_summary(
    results: list[OcrCompareCaseResult],
) -> dict[str, int]:
    summary: Counter[str] = Counter()
    for result in results:
        summary.update(result.annotation_summary)
    return {
        "approved_expected_count": int(summary.get("approved_expected_count", 0)),
        "draft_expected_count": int(summary.get("draft_expected_count", 0)),
        "rejected_expected_count": int(summary.get("rejected_expected_count", 0)),
    }


def _aggregate_status(results: list[OcrCompareCaseResult]) -> str:
    if not results:
        return "NO_CASES"
    if all(item.status == "COMPLETED" for item in results):
        return "COMPLETED"
    return "FAILED"


def _bbox_iou(left: dict[str, Any] | None, right: dict[str, Any] | None) -> float:
    left_box = _parse_bbox(left)
    right_box = _parse_bbox(right)
    if left_box is None or right_box is None:
        return 0.0
    left_x0, left_y0, left_x1, left_y1 = left_box
    right_x0, right_y0, right_x1, right_y1 = right_box
    x0 = max(left_x0, right_x0)
    y0 = max(left_y0, right_y0)
    x1 = min(left_x1, right_x1)
    y1 = min(left_y1, right_y1)
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    left_area = (left_x1 - left_x0) * (left_y1 - left_y0)
    right_area = (right_x1 - right_x0) * (right_y1 - right_y0)
    denominator = left_area + right_area - intersection
    if denominator <= 0:
        return 0.0
    return intersection / denominator


def _parse_bbox(bbox: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    if not bbox:
        return None
    try:
        x0 = float(bbox["x0"])
        y0 = float(bbox["y0"])
        x1 = float(bbox["x1"])
        y1 = float(bbox["y1"])
    except (KeyError, TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def threshold_failures(report: dict[str, Any]) -> list[str]:
    aggregate = report["aggregate"]
    failures: list[str] = []
    if aggregate["recall"] < DEFAULT_THRESHOLDS["min_recall"]:
        failures.append(
            f"recall={aggregate['recall']:.4f} < {DEFAULT_THRESHOLDS['min_recall']:.4f}"
        )
    if (
        aggregate["false_positive_count"]
        > DEFAULT_THRESHOLDS["max_false_positive_count"]
    ):
        failures.append(
            f"false_positive_count={aggregate['false_positive_count']} > {DEFAULT_THRESHOLDS['max_false_positive_count']}"
        )
    if aggregate["task_failure_count"] > DEFAULT_THRESHOLDS["max_task_failure_count"]:
        failures.append(
            f"task_failure_count={aggregate['task_failure_count']} > {DEFAULT_THRESHOLDS['max_task_failure_count']}"
        )
    if aggregate["evidence_hit_rate"] < DEFAULT_THRESHOLDS["min_evidence_hit_rate"]:
        failures.append(
            f"evidence_hit_rate={aggregate['evidence_hit_rate']:.4f} < {DEFAULT_THRESHOLDS['min_evidence_hit_rate']:.4f}"
        )
    return failures


def write_html_report(path: Path, report: dict[str, Any]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in report["cases"]:
        case_filename = _case_page_filename(str(case["case_id"]))
        (path / case_filename).write_text(_case_html(case), encoding="utf-8")
        rows.append(
            "<tr>"
            f"<td><a href='{html.escape(case_filename)}'>{html.escape(str(case['case_id']))}</a></td>"
            f"<td>{html.escape(str(case['status']))}</td>"
            f"<td>{case.get('expected_count', 0)}</td>"
            f"<td>{case.get('actual_count', 0)}</td>"
            f"<td>{case['recall']:.2%}</td>"
            f"<td>{case['precision']:.2%}</td>"
            f"<td>{case['false_positive_count']}</td>"
            f"<td>{case['false_negative_count']}</td>"
            f"<td>{len(case.get('evidence_drift_diffs', []))}</td>"
            f"<td>{case['low_confidence_count']}</td>"
            f"<td>{case['ocr_warning_count']}</td>"
            f"<td>{case.get('route_metrics', {}).get('retry_recommended_count', 0)}</td>"
            f"<td>{case.get('route_metrics', {}).get('manual_review_recommended_count', 0)}</td>"
            "</tr>"
        )
    failures = "<br>".join(
        html.escape(str(item)) for item in report.get("threshold_failures", [])
    ) or "None"
    route_metrics = html.escape(
        json.dumps(
            report.get("aggregate", {}).get("route_metrics", {}),
            ensure_ascii=False,
            indent=2,
        )
    )
    annotation_summary = html.escape(
        json.dumps(
            report.get("aggregate", {}).get("annotation_summary", {}),
            ensure_ascii=False,
            indent=2,
        )
    )
    index = (
        "<!doctype html><meta charset='utf-8'>"
        "<title>OCR comparison quality report</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px;color:#1f2933}"
        "table{border-collapse:collapse;width:100%;margin-top:16px}"
        "td,th{border:1px solid #cbd5e1;padding:6px;text-align:left}"
        ".failures{padding:10px;background:#fff7ed;border:1px solid #fed7aa}</style>"
        "<h1>OCR comparison quality report</h1>"
        f"<p>Case count: {report['case_count']}</p>"
        f"<p class='failures'>Threshold failures: {failures}</p>"
        f"<h2>Route recommendations</h2><pre>{route_metrics}</pre>"
        f"<h2>Annotation summary</h2><pre>{annotation_summary}</pre>"
        "<table><tr><th>Case</th><th>Status</th><th>Expected</th><th>Actual</th>"
        "<th>Recall</th><th>Precision</th>"
        "<th>False positives</th><th>Missed diffs</th><th>Evidence drift</th>"
        "<th>Low confidence</th>"
        "<th>OCR warnings</th><th>Retry routes</th><th>Manual routes</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    (path / "index.html").write_text(index, encoding="utf-8")


def _case_page_filename(case_id: str) -> str:
    stem = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in case_id
    ).lstrip(".")
    return f"{stem or 'case'}.html"


def _case_html(case: dict[str, Any]) -> str:
    issues = "".join(
        f"<li>{html.escape(issue)}</li>" for issue in case["issues"]
    ) or "<li>None</li>"
    routes = case.get("model_routing", {}).get("routes", [])
    route_items = "".join(
        "<li>"
        f"{html.escape(str(route.get('side')))} page {html.escape(str(route.get('page_no')))}: "
        f"{html.escape(str(route.get('page_type')))} -&gt; {html.escape(str(route.get('recommended_route')))}"
        "</li>"
        for route in routes
    ) or "<li>None</li>"
    matches_table = _html_table(
        ["Expected index", "Actual index", "Actual diff", "Score", "Evidence hit"],
        [
            [
                item.get("expected_index", ""),
                item.get("actual_index", ""),
                item.get("actual_diff_id", ""),
                item.get("score", ""),
                item.get("evidence_hit", ""),
            ]
            for item in case.get("matches", [])
        ],
    )
    missed_table = _html_table(
        ["Expected index", "Label", "Diff type", "Source type"],
        [
            [
                item.get("expected_index", ""),
                item.get("label", ""),
                item.get("diff_type", ""),
                item.get("source_type", ""),
            ]
            for item in case.get("missed_expected_diffs", [])
        ],
    )
    unexpected_table = _html_table(
        ["Actual index", "Diff ID", "Title", "Source type", "Quality", "Flags"],
        [
            [
                item.get("actual_index", ""),
                item.get("diff_id", ""),
                item.get("title", ""),
                item.get("source_type", ""),
                item.get("quality_status", ""),
                item.get("review_flags", []),
            ]
            for item in case.get("unexpected_actual_diffs", [])
        ],
    )
    drift_table = _html_table(
        ["Expected index", "Actual diff", "Label"],
        [
            [
                item.get("expected_index", ""),
                item.get("actual_diff_id", ""),
                item.get("label", ""),
            ]
            for item in case.get("evidence_drift_diffs", [])
        ],
    )
    return (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>{html.escape(str(case['case_id']))}</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px;color:#1f2933}"
        "dl{display:grid;grid-template-columns:220px 1fr;gap:6px}"
        "table{border-collapse:collapse;width:100%;margin-top:16px}"
        "td,th{border:1px solid #cbd5e1;padding:6px;text-align:left}"
        "dt{font-weight:700}</style>"
        f"<h1>{html.escape(str(case['case_id']))}</h1>"
        "<dl>"
        f"<dt>Status</dt><dd>{html.escape(str(case['status']))}</dd>"
        f"<dt>Recall</dt><dd>{case['recall']:.2%}</dd>"
        f"<dt>Precision</dt><dd>{case['precision']:.2%}</dd>"
        f"<dt>False positives</dt><dd>{case['false_positive_count']}</dd>"
        f"<dt>Missed diffs</dt><dd>{case['false_negative_count']}</dd>"
        f"<dt>Low-confidence diffs</dt><dd>{case['low_confidence_count']}</dd>"
        f"<dt>OCR warnings</dt><dd>{case['ocr_warning_count']}</dd>"
        f"<dt>Review signal</dt><dd>{'OCR_LOW_CONFIDENCE' if case['low_confidence_count'] else 'None'}</dd>"
        "</dl>"
        f"<h2>Model routing</h2><ul>{route_items}</ul>"
        f"<h2>Matched diffs</h2>{matches_table}"
        f"<h2>Missed expected diffs</h2>{missed_table}"
        f"<h2>Unexpected actual diffs</h2>{unexpected_table}"
        f"<h2>Evidence drift</h2>{drift_table}"
        f"<h2>Issues</h2><ul>{issues}</ul>"
    )


def _html_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    body = "".join(
        "<tr>"
        + "".join(f"<td>{html.escape(_format_cell(cell))}</td>" for cell in row)
        + "</tr>"
        for row in rows
    )
    return f"<table><tr>{head}</tr>{body}</table>"


def _format_cell(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate OCR-driven contract comparison quality."
    )
    parser.add_argument(
        "case_root",
        type=Path,
        help="Directory containing OCR compare case subdirectories.",
    )
    parser.add_argument(
        "--output", type=Path, default=None, help="Optional JSON report path."
    )
    parser.add_argument(
        "--html-output",
        type=Path,
        default=None,
        help="Optional directory for HTML report.",
    )
    parser.add_argument(
        "--fail-on-threshold",
        action="store_true",
        help="Exit 1 if smoke thresholds fail.",
    )
    args = parser.parse_args()

    report = evaluate_case_root(args.case_root)
    content = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content)
    if args.html_output:
        write_html_report(args.html_output, report)
    if args.fail_on_threshold and report["threshold_failures"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
