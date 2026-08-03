from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.services.native_fast_path import NativeFastPathEvaluator  # noqa: E402


def evaluate_pairs(
    pairs: Sequence[tuple[Path, Path]],
    evaluator: NativeFastPathEvaluator,
) -> dict[str, Any]:
    if not pairs:
        raise ValueError("at least one PDF pair is required")
    started_at = time.perf_counter()
    pair_results: list[dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    durations: list[float] = []
    for index, (original, compare) in enumerate(pairs, start=1):
        side_results: dict[str, dict[str, Any]] = {}
        accepted = True
        for side, path in (("original", original), ("compare", compare)):
            decision = evaluator.evaluate(path)
            duration = float(decision.metrics.get("duration_seconds") or 0.0)
            durations.append(duration)
            reason_counts.update(decision.reasons)
            accepted = accepted and decision.accepted
            counters = decision.metrics.get("counters")
            side_results[side] = {
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "accepted": decision.accepted,
                "reasons": list(decision.reasons),
                "duration_seconds": duration,
                "page_count": counters.get("page_count", 0) if isinstance(counters, dict) else 0,
            }
        pair_results.append(
            {
                "pair_index": index,
                "eligible": accepted,
                "sides": side_results,
            }
        )

    eligible_count = sum(result["eligible"] for result in pair_results)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "pair_count": len(pair_results),
        "eligible_pair_count": eligible_count,
        "rejected_pair_count": len(pair_results) - eligible_count,
        "eligible_pair_rate": round(eligible_count / len(pair_results), 6),
        "wall_duration_seconds": round(time.perf_counter() - started_at, 6),
        "mean_document_evaluation_seconds": round(statistics.fmean(durations), 6) if durations else 0.0,
        "rejection_reason_counts": dict(sorted(reason_counts.items())),
        "pairs": pair_results,
    }


def _validated_pair(values: Sequence[Path]) -> tuple[Path, Path]:
    if len(values) != 2:
        raise ValueError("--pair requires exactly two PDF paths")
    resolved = tuple(path.expanduser().resolve() for path in values)
    for path in resolved:
        if not path.is_file():
            raise FileNotFoundError(f"PDF does not exist: {path}")
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"Only PDF inputs are supported: {path}")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate strict native-PDF fast-path eligibility without running OCR.",
    )
    parser.add_argument(
        "--pair",
        nargs=2,
        action="append",
        type=Path,
        required=True,
        metavar=("ORIGINAL", "COMPARE"),
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    try:
        pairs = [_validated_pair(pair) for pair in args.pair]
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    evaluator = NativeFastPathEvaluator(
        min_chars_per_page=settings.compare_native_fast_path_min_chars_per_page,
        min_char_box_coverage=settings.compare_native_fast_path_min_char_box_coverage,
        max_suspicious_char_ratio=settings.compare_native_fast_path_max_suspicious_char_ratio,
    )
    report = evaluate_pairs(pairs, evaluator)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is None:
        print(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
