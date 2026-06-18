from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.models import Clause
from app.services.matcher import ClauseMatcher
from app.services.normalizer import TextNormalizer


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate clause matching fixtures.")
    parser.add_argument("fixtures", nargs="+", type=Path, help="JSON fixture files to evaluate.")
    parser.add_argument("--threshold", type=int, default=85)
    parser.add_argument("--assignment-strategy", choices=["greedy", "optimal"], default="optimal")
    args = parser.parse_args()

    results = [
        evaluate_fixture(path, threshold=args.threshold, assignment_strategy=args.assignment_strategy)
        for path in args.fixtures
    ]
    summary = summarize(results)
    print(json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=2))
    return 0 if summary["pair_recall"] >= 1.0 and summary["false_pair_count"] == 0 else 1


def evaluate_fixture(path: Path, *, threshold: int, assignment_strategy: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    original = [_clause(item, "O", index) for index, item in enumerate(_required_list(payload, "original"), start=1)]
    compare = [_clause(item, "N", index) for index, item in enumerate(_required_list(payload, "compare"), start=1)]
    expected_pairs = {
        (str(item["original_clause_id"]), str(item["compare_clause_id"]))
        for item in _required_list(payload, "expected_pairs")
    }

    matcher = ClauseMatcher(threshold=threshold, assignment_strategy=assignment_strategy)
    pairs = matcher.match(original, compare)
    actual_pairs = {
        (pair.original.clause_id, pair.compare.clause_id)
        for pair in pairs
        if pair.original is not None and pair.compare is not None
    }
    missing = sorted(expected_pairs - actual_pairs)
    false_pairs = sorted(actual_pairs - expected_pairs)
    expected_original_ids = {left for left, _ in expected_pairs}
    expected_compare_ids = {right for _, right in expected_pairs}
    false_deletes = sorted(
        pair.original.clause_id
        for pair in pairs
        if pair.original is not None
        and pair.compare is None
        and pair.original.clause_id in expected_original_ids
    )
    false_adds = sorted(
        pair.compare.clause_id
        for pair in pairs
        if pair.compare is not None
        and pair.original is None
        and pair.compare.clause_id in expected_compare_ids
    )
    precision = _ratio(len(actual_pairs & expected_pairs), len(actual_pairs))
    recall = _ratio(len(actual_pairs & expected_pairs), len(expected_pairs))
    return {
        "fixture": str(path),
        "expected_pair_count": len(expected_pairs),
        "actual_pair_count": len(actual_pairs),
        "pair_precision": precision,
        "pair_recall": recall,
        "missing_pairs": [{"original_clause_id": left, "compare_clause_id": right} for left, right in missing],
        "false_pairs": [{"original_clause_id": left, "compare_clause_id": right} for left, right in false_pairs],
        "false_deletes": false_deletes,
        "false_adds": false_adds,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    expected = sum(item["expected_pair_count"] for item in results)
    actual = sum(item["actual_pair_count"] for item in results)
    missing_count = sum(len(item["missing_pairs"]) for item in results)
    false_pair_count = sum(len(item["false_pairs"]) for item in results)
    false_delete_count = sum(len(item["false_deletes"]) for item in results)
    false_add_count = sum(len(item["false_adds"]) for item in results)
    correct = expected - missing_count
    return {
        "case_count": len(results),
        "expected_pair_count": expected,
        "actual_pair_count": actual,
        "pair_precision": _ratio(correct, actual),
        "pair_recall": _ratio(correct, expected),
        "missing_pair_count": missing_count,
        "false_pair_count": false_pair_count,
        "false_delete_count": false_delete_count,
        "false_add_count": false_add_count,
    }


def _clause(item: dict[str, Any], prefix: str, index: int) -> Clause:
    normalizer = TextNormalizer()
    clause_id = str(item.get("clause_id") or f"{prefix}{index:03d}")
    clause_no = str(item.get("clause_no") or "")
    title = str(item.get("title") or "")
    text = str(item.get("text") or "")
    if not text:
        body = str(item.get("body") or "")
        text = f"{clause_no}. {title}\n{body}" if clause_no else f"{title}\n{body}"
    return Clause(
        clause_id=clause_id,
        clause_no=clause_no,
        title=title,
        text=text,
        normalized_text=str(item.get("normalized_text") or normalizer.normalize_for_diff(text)),
        match_text=str(item.get("match_text") or normalizer.normalize_for_match(text)),
        section_type=str(item.get("section_type") or "main_contract"),
        section_path=[str(value) for value in item.get("section_path", [])],
        clause_key=str(item.get("clause_key") or ""),
        split_flags=[str(value) for value in item.get("split_flags", [])],
    )


def _required_list(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"Fixture field '{key}' must be a list.")
    return value


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


if __name__ == "__main__":
    raise SystemExit(main())
