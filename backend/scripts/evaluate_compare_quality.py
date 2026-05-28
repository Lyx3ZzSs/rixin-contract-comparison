from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models import CompareTask, DiffItem  # noqa: E402
from app.services.compare_service import CompareService  # noqa: E402
from app.utils.json_utils import to_jsonable  # noqa: E402


@dataclass
class CaseResult:
    case_id: str
    expected_count: int
    actual_count: int
    true_positive_count: int
    false_positive_count: int
    false_negative_count: int
    evidence_hit_count: int
    low_confidence_count: int


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate contract comparison quality against golden cases.")
    parser.add_argument("case_root", type=Path, help="Directory containing case subdirectories.")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path.")
    args = parser.parse_args()

    report = evaluate_case_root(args.case_root)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def evaluate_case_root(case_root: Path) -> dict[str, Any]:
    cases = [path for path in sorted(case_root.iterdir()) if path.is_dir() and (path / "expected.json").exists()]
    results = [evaluate_case(path) for path in cases]
    aggregate = _aggregate(results)
    return {
        "case_root": str(case_root),
        "case_count": len(results),
        "aggregate": aggregate,
        "cases": [result.__dict__ | _rates(result) for result in results],
    }


def evaluate_case(case_dir: Path) -> CaseResult:
    expected_payload = _read_json(case_dir / "expected.json")
    actual_task = _load_or_run_actual(case_dir)
    expected = expected_payload.get("expected_diffs", [])
    actual = [to_jsonable(diff) for diff in actual_task.diffs]
    matches = _match_expected_diffs(expected, actual)
    matched_actual_indexes = {actual_index for _, actual_index in matches}
    evidence_hit_count = sum(1 for diff in actual if _has_evidence(diff))
    low_confidence_count = sum(1 for diff in actual if _is_low_confidence(diff))
    return CaseResult(
        case_id=case_dir.name,
        expected_count=len(expected),
        actual_count=len(actual),
        true_positive_count=len(matches),
        false_positive_count=len(actual) - len(matched_actual_indexes),
        false_negative_count=len(expected) - len(matches),
        evidence_hit_count=evidence_hit_count,
        low_confidence_count=low_confidence_count,
    )


def _load_or_run_actual(case_dir: Path) -> CompareTask:
    actual_json = case_dir / "actual.json"
    if actual_json.exists():
        return CompareTask(**_read_json(actual_json))

    original_pdf = case_dir / "original.pdf"
    compare_pdf = case_dir / "compare.pdf"
    if not original_pdf.exists() or not compare_pdf.exists():
        raise FileNotFoundError(f"{case_dir} must contain actual.json or original.pdf + compare.pdf")
    return CompareService().compare(original_pdf, compare_pdf, task_id=f"EVAL_{case_dir.name.upper()}")


def _match_expected_diffs(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> list[tuple[int, int]]:
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
        if best_index is not None and best_score >= 0.72:
            used_actual.add(best_index)
            matches.append((expected_index, best_index))
    return matches


def _diff_match_score(expected: dict[str, Any], actual: dict[str, Any]) -> float:
    score = 0.0
    if expected.get("diff_type") == actual.get("diff_type"):
        score += 0.28
    if expected.get("source_type") and expected.get("source_type") == actual.get("source_type"):
        score += 0.16
    if _contains(actual.get("title", ""), expected.get("title_contains", "")):
        score += 0.18
    if _contains(actual.get("original_text", "") + actual.get("original_snippet", ""), expected.get("original_contains", "")):
        score += 0.19
    if _contains(actual.get("compare_text", "") + actual.get("compare_snippet", ""), expected.get("compare_contains", "")):
        score += 0.19
    return score


def _contains(text: str, needle: str) -> bool:
    return not needle or needle in (text or "")


def _has_evidence(diff: dict[str, Any]) -> bool:
    return bool(diff.get("original_evidence") or diff.get("compare_evidence"))


def _is_low_confidence(diff: dict[str, Any]) -> bool:
    evidences = [*diff.get("original_evidence", []), *diff.get("compare_evidence", [])]
    if not evidences:
        return True
    return any(evidence.get("evidence_quality") == "LOW" or float(evidence.get("confidence", 1.0)) < 0.6 for evidence in evidences)


def _aggregate(results: list[CaseResult]) -> dict[str, Any]:
    totals = CaseResult(
        case_id="TOTAL",
        expected_count=sum(item.expected_count for item in results),
        actual_count=sum(item.actual_count for item in results),
        true_positive_count=sum(item.true_positive_count for item in results),
        false_positive_count=sum(item.false_positive_count for item in results),
        false_negative_count=sum(item.false_negative_count for item in results),
        evidence_hit_count=sum(item.evidence_hit_count for item in results),
        low_confidence_count=sum(item.low_confidence_count for item in results),
    )
    return totals.__dict__ | _rates(totals)


def _rates(result: CaseResult) -> dict[str, float]:
    precision_denominator = result.true_positive_count + result.false_positive_count
    recall_denominator = result.true_positive_count + result.false_negative_count
    return {
        "precision": _safe_div(result.true_positive_count, precision_denominator),
        "recall": _safe_div(result.true_positive_count, recall_denominator),
        "evidence_hit_rate": _safe_div(result.evidence_hit_count, result.actual_count),
        "low_confidence_ratio": _safe_div(result.low_confidence_count, result.actual_count),
    }


def _safe_div(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
