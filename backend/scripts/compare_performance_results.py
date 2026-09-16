"""Compare performance runs without treating pre-existing oracle failures as passes."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from tests.golden_contract.semantic_matcher import SemanticGoldenMatcher  # noqa: E402


BUSINESS_FIELDS = ("diff_type", "source_type", "title", "original_text", "compare_text",
                   "original_snippet", "compare_snippet", "original_evidence", "compare_evidence",
                   "quality_status", "content_recognition_status", "evidence_location_status")


def signature(diff):
    return json.dumps({key: diff.get(key) for key in BUSINESS_FIELDS}, sort_keys=True, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-result-changes", action="store_true",
                        help="Strategy experiments only: report changed diffs without accepting them as equivalent")
    args = parser.parse_args()
    fixture = ROOT / "backend/tests/fixtures/contract_golden"
    manifest = json.loads((fixture / "manifest.json").read_text())
    corpus = {c["source_run_id"]: c for c in manifest["contracts"]}
    rows = []
    runs = [json.loads((directory / "summary.json").read_text()) for directory in (args.before, args.after)]
    transport_valid = all(r["status"] == "COMPLETED" and not r["http_failures"] and not r["cache_misses"]
                          for run in runs for r in run)
    for item in json.loads(args.inputs.read_text()):
        task_id = item["task_id"]
        before = json.loads((args.before / task_id / "result.json").read_text())
        after = json.loads((args.after / task_id / "result.json").read_text())
        left_hashes = json.loads((args.before / task_id / "input-hashes.json").read_text())
        right_hashes = json.loads((args.after / task_id / "input-hashes.json").read_text())
        old, new = Counter(map(signature, before["diffs"])), Counter(map(signature, after["diffs"]))
        row = {"task_id": task_id, "input_hashes_equal": left_hashes == right_hashes == item["pdfs"],
               "statuses": [before["status"], after["status"]],
               "counts": [len(before["diffs"]), len(after["diffs"])],
               "strict_diff_equal": before["diffs"] == after["diffs"],
               "business_equal": old == new,
               "removed_business_items": [json.loads(x) for x in (old - new).elements()],
               "added_business_items": [json.loads(x) for x in (new - old).elements()]}
        if task_id in corpus:
            c = corpus[task_id]
            oracle = json.loads((fixture / c["oracle"]).read_text())["assertions"]
            reports = [SemanticGoldenMatcher().match(c["contract_key"], oracle, x["diffs"]) for x in (before, after)]
            failures = [{(f.case_id, f.failure_type): asdict(f) for f in r.failures} for r in reports]
            row.update(contract=c["contract_key"], oracle_failure_counts=[len(x) for x in failures],
                       new_oracle_failures=[failures[1][k] for k in failures[1].keys() - failures[0].keys()],
                       resolved_oracle_failures=[failures[0][k] for k in failures[0].keys() - failures[1].keys()])
        rows.append(row)
    result = {"contracts": rows, "executed": len(rows),
              "transport_valid": transport_valid,
              "hashes_equal": all(r["input_hashes_equal"] for r in rows),
              "business_equal_count": sum(r["business_equal"] for r in rows),
              "strict_equal_count": sum(r["strict_diff_equal"] for r in rows),
              "new_oracle_failures": sum(len(r.get("new_oracle_failures", [])) for r in rows),
              "before_oracle_failures": sum(r.get("oracle_failure_counts", [0, 0])[0] for r in rows),
              "after_oracle_failures": sum(r.get("oracle_failure_counts", [0, 0])[1] for r in rows)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "contracts"}, indent=2))
    changed_output = result["strict_equal_count"] != len(rows) and not args.allow_result_changes
    return int(not transport_valid or not result["hashes_equal"] or result["new_oracle_failures"]
               or changed_output or any(r["statuses"] != ["COMPLETED", "COMPLETED"] for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
