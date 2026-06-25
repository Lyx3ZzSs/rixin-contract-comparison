from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import CompareTask  # noqa: E402
from app.services.compare_service import CompareService  # noqa: E402


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
    issues: list[str]

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__ | self.rates()

    def rates(self) -> dict[str, float]:
        precision_denominator = self.true_positive_count + self.false_positive_count
        recall_denominator = self.true_positive_count + self.false_negative_count
        return {
            "precision": _safe_div(self.true_positive_count, precision_denominator),
            "recall": _safe_div(self.true_positive_count, recall_denominator),
            "evidence_hit_rate": _safe_div(self.evidence_hit_count, self.actual_count),
            "low_confidence_ratio": _safe_div(
                self.low_confidence_count, self.actual_count
            ),
        }


def evaluate_case_root(case_root: Path) -> dict[str, Any]:
    cases = discover_cases(case_root)
    results = [evaluate_case(case) for case in cases]
    aggregate = _aggregate(results)
    return {
        "case_root": str(case_root),
        "case_count": len(results),
        "aggregate": aggregate,
        "cases": [result.to_dict() for result in results],
    }


def evaluate_case(case: OcrCompareCase) -> OcrCompareCaseResult:
    expected_payload, actual_task = load_case_inputs(case)
    expected_diffs = expected_payload.get("expected_diffs", [])
    actual_diffs = [diff.model_dump(mode="json") for diff in actual_task.diffs]
    matches = _match_expected_diffs(expected_diffs, actual_diffs)
    matched_actual_indexes = {actual_index for _, actual_index in matches}
    issues = _case_issues(expected_diffs, actual_diffs, matches)
    return OcrCompareCaseResult(
        case_id=case.case_id,
        status=actual_task.status,
        expected_count=len(expected_diffs),
        actual_count=len(actual_diffs),
        true_positive_count=len(matches),
        false_positive_count=len(actual_diffs) - len(matched_actual_indexes),
        false_negative_count=len(expected_diffs) - len(matches),
        evidence_hit_count=sum(
            1 for diff in actual_diffs if _has_high_quality_evidence(diff)
        ),
        low_confidence_count=sum(1 for diff in actual_diffs if _is_low_confidence(diff)),
        ocr_warning_count=sum(
            1
            for warning in actual_task.parse_warning_details
            if _is_ocr_warning(warning.model_dump(mode="json"))
        ),
        task_failure_count=0 if actual_task.status == "COMPLETED" else 1,
        issues=issues,
    )


def _match_expected_diffs(
    expected: list[dict[str, Any]], actual: list[dict[str, Any]]
) -> list[tuple[int, int]]:
    matches: list[tuple[int, int]] = []
    used_actual: set[int] = set()
    for expected_index, expected_diff in enumerate(expected):
        best_index = None
        best_score = 0.0
        for actual_index, actual_diff in enumerate(actual):
            if actual_index in used_actual:
                continue
            score = _diff_match_score(expected_diff, actual_diff)
            if score > best_score:
                best_index = actual_index
                best_score = score
        if (
            best_index is not None
            and best_score >= 0.72
            and _has_matching_signal(expected_diff, actual[best_index])
        ):
            used_actual.add(best_index)
            matches.append((expected_index, best_index))
    return matches


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


def _has_high_quality_evidence(diff: dict[str, Any]) -> bool:
    evidences = [*diff.get("original_evidence", []), *diff.get("compare_evidence", [])]
    return bool(evidences) and any(
        evidence.get("evidence_quality") in {"MEDIUM", "HIGH"}
        and float(evidence.get("confidence", 0.0)) >= 0.6
        for evidence in evidences
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
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    matches: list[tuple[int, int]],
) -> list[str]:
    matched_expected = {expected_index for expected_index, _ in matches}
    matched_actual = {actual_index for _, actual_index in matches}
    issues = [
        f"missed expected diff {index + 1}: {item.get('title_contains') or item.get('source_type') or item.get('diff_type')}"
        for index, item in enumerate(expected)
        if index not in matched_expected
    ]
    issues.extend(
        f"unexpected actual diff {index + 1}: {item.get('title') or item.get('source_type') or item.get('diff_type')}"
        for index, item in enumerate(actual)
        if index not in matched_actual
    )
    return issues


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
        issues=[issue for item in results for issue in item.issues],
    )
    return aggregate.to_dict()


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
