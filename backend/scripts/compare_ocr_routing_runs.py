"""Compare two OCR-routing audit runs contract by contract and write a markdown diff.

    .venv/bin/python backend/scripts/compare_ocr_routing_runs.py \
        --before .../regression/before --after .../regression/auto \
        --output .../regression/auto-vs-before.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

KEY_FIELDS = ("type", "status", "source_type", "title", "original", "compare")


def failure_key(failure: str) -> str:
    """Oracle failure text is multi-line; the stable identity is contract + case id."""
    contract = case = failure_type = ""
    for line in failure.splitlines():
        if line.startswith("Contract:"):
            contract = line.split(":", 1)[1].strip()
        elif line.startswith("Case ID:"):
            case = line.split(":", 1)[1].strip()
        elif line.startswith("Failure Type:"):
            failure_type = line.split(":", 1)[1].strip()
    return f"{contract}::{case}::{failure_type}"


def load(run: Path) -> dict:
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    contracts = {}
    for path in sorted(run.glob("*.json")):
        if path.name == "summary.json":
            continue
        entry = json.loads(path.read_text(encoding="utf-8"))
        contracts[entry["contract_key"]] = entry
    return {"summary": summary, "contracts": contracts}


def fingerprint(row: dict) -> tuple:
    return tuple(row.get(field, "") for field in KEY_FIELDS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    before = load(args.before)
    after = load(args.after)
    lines = [f"# OCR routing regression: `{after['summary']['label']}` vs `{before['summary']['label']}`", ""]
    lines.append(
        "> `wall seconds` is only comparable when both runs were the sole load on the "
        "shared OCR service; it is NOT used as the benchmark (see `benchmark.md`)."
    )
    lines.append("")

    def row(name, b, a):
        delta = ""
        if isinstance(b, (int, float)) and isinstance(a, (int, float)):
            delta = f"{a - b:+g}"
        lines.append(f"| {name} | {b} | {a} | {delta} |")

    lines += ["## Corpus totals", "", "| metric | before | after | delta |", "|---|---:|---:|---:|"]
    row("document pages", before["summary"]["document_pages"], after["summary"]["document_pages"])
    row("OCR pages", before["summary"]["ocr_pages"], after["summary"]["ocr_pages"])
    row("diffs", before["summary"]["diffs"], after["summary"]["diffs"])
    row("oracle failures", before["summary"]["oracle_failures"], after["summary"]["oracle_failures"])
    row("wall seconds", before["summary"]["wall_seconds"], after["summary"]["wall_seconds"])
    for status in sorted(set(before["summary"]["by_status"]) | set(after["summary"]["by_status"])):
        row(
            f"diffs status={status}",
            before["summary"]["by_status"].get(status, 0),
            after["summary"]["by_status"].get(status, 0),
        )
    for dtype in sorted(set(before["summary"]["by_type"]) | set(after["summary"]["by_type"])):
        row(
            f"diffs type={dtype}",
            before["summary"]["by_type"].get(dtype, 0),
            after["summary"]["by_type"].get(dtype, 0),
        )
    for stype in sorted(set(before["summary"]["by_source_type"]) | set(after["summary"]["by_source_type"])):
        row(
            f"diffs source_type={stype}",
            before["summary"]["by_source_type"].get(stype, 0),
            after["summary"]["by_source_type"].get(stype, 0),
        )

    lines += ["", "## Per contract", ""]
    lines.append("| contract | pages | OCR before | OCR after | diffs before | diffs after | oracle fails before | oracle fails after |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    rows = []
    for key in sorted(set(before["contracts"]) | set(after["contracts"])):
        b = before["contracts"].get(key, {})
        a = after["contracts"].get(key, {})
        rec = {
            "key": key,
            "doc_pages": b.get("original_pages") or a.get("original_pages") or 0,
            "ocr_before": sum((b.get(s, {}) or {}).get("ocr_pages") or 0 for s in ("original", "compare")),
            "ocr_after": sum((a.get(s, {}) or {}).get("ocr_pages") or 0 for s in ("original", "compare")),
            "diffs_before": b.get("diff_count", 0),
            "diffs_after": a.get("diff_count", 0),
            "fail_before": len((b.get("oracle") or {}).get("failures", [])),
            "fail_after": len((a.get("oracle") or {}).get("failures", [])),
        }
        rows.append(rec)
        lines.append(
            f"| {key} | {rec['doc_pages']} | {rec['ocr_before']} | {rec['ocr_after']} | "
            f"{rec['diffs_before']} | {rec['diffs_after']} | {rec['fail_before']} | {rec['fail_after']} |"
        )

    lines += ["", "## Why pages went to OCR (the `after` run)", ""]
    lines.append("| contract | side | document pages | native | OCR | reasons |")
    lines.append("|---|---|---:|---:|---:|---|")
    for key in sorted(after["contracts"]):
        entry = after["contracts"][key]
        for side in ("original", "compare"):
            data = entry.get(side) or {}
            if not data.get("routing_active"):
                lines.append(
                    f"| {key} | {side} | {data.get('document_pages')} | - | "
                    f"{data.get('ocr_pages')} | no page routing (whole-document OCR) |"
                )
                continue
            reasons = ", ".join(f"{k}={v}" for k, v in sorted((data.get("ocr_reasons") or {}).items()))
            lines.append(
                f"| {key} | {side} | {data.get('document_pages')} | {data.get('native_pages')} | "
                f"{data.get('ocr_pages')} | {reasons or '-'} |"
            )

    new_failures, fixed_failures, changed = [], [], []
    for key in sorted(set(before["contracts"]) | set(after["contracts"])):
        b = before["contracts"].get(key, {})
        a = after["contracts"].get(key, {})
        b_asserts = {failure_key(f) for f in (b.get("oracle") or {}).get("failures", [])}
        a_asserts = {failure_key(f) for f in (a.get("oracle") or {}).get("failures", [])}
        new_failures.extend(sorted(a_asserts - b_asserts))
        fixed_failures.extend(sorted(b_asserts - a_asserts))
        if (b.get("diff_count", 0) != a.get("diff_count", 0)) or (b.get("by_type") != a.get("by_type")):
            b_fp = {fingerprint(r) for r in b.get("diffs", [])}
            a_fp = {fingerprint(r) for r in a.get("diffs", [])}
            added = a_fp - b_fp
            removed = b_fp - a_fp
            if added or removed:
                changed.append((key, b.get("diff_count", 0), a.get("diff_count", 0), added, removed))

    lines += ["", "## Oracle transitions", "", f"- new failures: **{len(new_failures)}**", f"- fixed: **{len(fixed_failures)}**"]
    for item in new_failures:
        lines.append(f"  - NEW FAIL: {item}")
    for item in fixed_failures:
        lines.append(f"  - fixed: {item}")

    lines += ["", "## Diff-level changes", ""]
    if not changed:
        lines.append("No per-diff changes: the two runs produced identical diff sets.")
    for key, nb, na, added, removed in changed:
        lines.append(f"### {key} ({nb} -> {na})")
        for fingerprint_row in sorted(added):
            lines.append(f"- ADDED   `{fingerprint_row[0]}`/{fingerprint_row[1]}/{fingerprint_row[2]} p{fingerprint_row[3]} :: {fingerprint_row[4][:80]!r} -> {fingerprint_row[5][:80]!r}")
        for fingerprint_row in sorted(removed):
            lines.append(f"- REMOVED `{fingerprint_row[0]}`/{fingerprint_row[1]}/{fingerprint_row[2]} p{fingerprint_row[3]} :: {fingerprint_row[4][:80]!r} -> {fingerprint_row[5][:80]!r}")
        lines.append("")

    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {args.output}")
    print(f"new oracle failures: {len(new_failures)}  fixed: {len(fixed_failures)}  changed contracts: {len(changed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
