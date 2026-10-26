"""Build failure-cases.csv for the cross-source regression cases.

Takes the 24 cases observed when the contract-pair gate is disabled (20
UNEXPECTED_CONFIRMED_DIFF + 4 FALSE_NEGATIVE_REGRESSION), re-derives the real
difference after `normalize_for_diff`, and classifies it.

    .venv/bin/python backend/scripts/build_cross_source_failure_cases.py \
        --before  output/analysis/ocr-routing-optimization-20260920/regression/before \
        --ungated output/analysis/ocr-routing-optimization-20260920/regression/auto-ungated \
        --output  output/analysis/cross-source-normalization-20260920
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app.services.normalizer import TextNormalizer  # noqa: E402

# Running header/footer lines seen leaking into native clause text in the corpus.
REPEATED_LINE_RE = re.compile(r"sgtyht/23-js-004技术服务合同|深能奈曼光伏电站设备合同", re.I)
MARKER_ONLY_RE = re.compile(r"^(?:\d+(?:\.\d+)*[.、．]?|[零〇一二两三四五六七八九十百千万]+[、.．]|第[零〇一二两三四五六七八九十百千万0-9]+[章节条])$")
SYMBOL_CLASSES = (
    ("symbol_permille", "‰", "%", "%o"),
    ("symbol_dash", "—", "–", "一"),
    ("symbol_checkbox", "/", "∠", "；", "。"),
)


def failure_type(text: str) -> str:
    m = re.search(r"Failure Type: (\S+)", text)
    return m.group(1) if m else "?"


def case_id(text: str) -> str:
    m = re.search(r"Case ID: (\S+)", text)
    return m.group(1) if m else "?"


def actual_payload(text: str) -> dict:
    m = re.search(r"Actual: (\{.*\})\n", text)
    if not m:
        return {}
    try:
        return eval(m.group(1), {"__builtins__": {}}, {})
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--ungated", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    norm = TextNormalizer()
    before = {f.stem: json.loads(f.read_text()) for f in args.before.glob("*.json") if f.name != "summary.json"}
    ungated = {f.stem: json.loads(f.read_text()) for f in args.ungated.glob("*.json") if f.name != "summary.json"}

    rows = []
    for contract in sorted(ungated):
        baseline_failures = before.get(contract, {}).get("oracle", {}).get("failures", [])
        baseline_types = {failure_type(f) for f in baseline_failures}
        baseline_cases = {case_id(f) for f in baseline_failures}
        for failure in ungated[contract]["oracle"]["failures"]:
            ftype = failure_type(failure)
            if ftype == "UNEXPECTED_CONFIRMED_DIFF":
                # The case id is the literal type string, so only the count matters.
                if "UNEXPECTED_CONFIRMED_DIFF" in baseline_types:
                    continue
            elif case_id(failure) in baseline_cases:
                continue  # pre-existing failure, not part of the 24
            payload = actual_payload(failure)
            left_raw = payload.get("original_text") or ""
            right_raw = payload.get("compare_text") or ""
            left = norm.normalize_for_diff(left_raw)
            right = norm.normalize_for_diff(right_raw)

            ops = []
            if ftype == "UNEXPECTED_CONFIRMED_DIFF":
                matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
                for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                    if tag == "equal":
                        continue
                    ops.append((tag, left[i1:i2], right[j1:j2]))
                reasons, detail = classify_ops(ops)
            else:
                # A miss: an assertion that used to match no longer has a diff.
                expected = re.search(r"Expected: (.*)", failure)
                reasons = "missed_change_no_longer_detected"
                detail = (expected.group(1) if expected else "")[:400]

            rows.append(
                {
                    "contract": contract,
                    "page": payload.get("page") or "",
                    "failure_type": ftype,
                    "root_cause": reasons,
                    "diff_type": payload.get("diff_type", ""),
                    "source_type": payload.get("source_type", ""),
                    "title": payload.get("title", ""),
                    "text_side_before": left_raw[:400],
                    "ocr_side_before": right_raw[:400],
                    "normalized_difference": detail[:400],
                    "fixed_by": "",
                    "status": "open",
                }
            )

    args.output.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with (args.output / "failure-cases.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    counts: dict[str, int] = {}
    for row in rows:
        for reason in row["root_cause"].split("+"):
            counts[reason] = counts.get(reason, 0) + 1
    print(f"wrote {args.output/'failure-cases.csv'}  rows={len(rows)}")
    for reason, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {reason:34s} {count}")
    return 0


def classify_ops(ops: list[tuple[str, str, str]]) -> tuple[str, str]:
    if not ops:
        return "no_difference_after_normalisation", ""
    reasons, details = [], []
    for tag, left, right in ops:
        details.append(f"{tag}:{left!r}->{right!r}")
        blob = (left + right).lower()
        if REPEATED_LINE_RE.search(blob):
            reasons.append("header_footer_leak")
        elif MARKER_ONLY_RE.match(left) or MARKER_ONLY_RE.match(right):
            reasons.append("clause_marker_boundary")
        elif left.endswith("资质管理") or left.endswith("现场安全管理"):
            reasons.append("clause_boundary_merge")
        else:
            for name, *chars in SYMBOL_CLASSES:
                if left in chars and right in chars:
                    reasons.append(name)
                    break
            else:
                reasons.append("text_difference_other")
    return "+".join(sorted(set(reasons))), " | ".join(details[:3])


if __name__ == "__main__":
    raise SystemExit(main())
