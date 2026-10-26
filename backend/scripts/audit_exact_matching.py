"""Read-only eligibility audit against saved matching inputs and final pairings."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.models import Clause  # noqa: E402
from app.services.matcher import ClauseMatcher  # noqa: E402
from app.services.page_alignment import PageAlignmentEntry, PageAlignmentResult, PageAlignmentStatus  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for path in sorted(args.input_root.glob("*/matching-input.json")):
        data = json.loads(path.read_text())
        left, right = ([Clause.model_validate(c) for c in data[side]] for side in ("original", "compare"))
        matcher = ClauseMatcher(performance_strategy="shadow")
        matcher.page_alignment = PageAlignmentResult(tuple(PageAlignmentEntry(
            status=PageAlignmentStatus(e["status"]), original_pages=tuple(e["original_pages"]),
            compare_pages=tuple(e["compare_pages"]), alignment_score=e["alignment_score"],
            anchors=tuple(e["anchors"]), reason=e["reason"],
        ) for e in data["page_alignment"]["entries"]))
        eligible = matcher._unique_aligned_exact_matches(left, right)
        matches_path = args.input_root / "runtime/tasks" / f"bench-{path.parent.name}" / "diagnostics/debug/clause_matches.json"
        matches = json.loads(matches_path.read_text())
        actual = {m["original_clause_id"]: m["compare_clause_id"] for m in matches}
        disagreements = [{"original_id": left[i].clause_id, "candidate_id": right[j].clause_id,
                          "existing_id": actual.get(left[i].clause_id)}
                         for i, j in eligible.items() if actual.get(left[i].clause_id) != right[j].clause_id]
        rows.append({"task_id": path.parent.name, "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "matches_sha256": hashlib.sha256(matches_path.read_bytes()).hexdigest(),
                     "original_count": len(left), "compare_count": len(right),
                     "eligible_count": len(eligible), "disagreements": disagreements,
                     "reasons": matcher._performance.snapshot()["counters"]})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    print(json.dumps({"tasks": len(rows), "eligible": sum(r["eligible_count"] for r in rows),
                      "disagreements": sum(len(r["disagreements"]) for r in rows)}))


if __name__ == "__main__":
    main()
