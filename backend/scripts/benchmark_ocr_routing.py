"""Before/after benchmark for page-level OCR routing.

Runs the real comparison pipeline over a fixed set of contract pairs and records
wall time, per-side extraction metrics (native vs OCR pages), OCR request counts
and the resulting diffs, so two OCR routing modes can be compared directly.

    .venv/bin/python backend/scripts/benchmark_ocr_routing.py \
        --mode always --output output/analysis/ocr-routing-optimization-20260920/bench/before
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.benchmark_support import HttpRecorder, build_service, configure_runtime  # noqa: E402

CORPUS = Path("storage/golden-intake/contract-golden-v3")

SUITE = {
    # label: (original relative path, compare relative path)
    # A contract compared with itself must produce zero diffs, so any diff here is a
    # cross-source artifact. That makes it the sharpest routing regression probe.
    "text_text_self": (
        "HUANENG_DINGBIAN/original.pdf",
        "HUANENG_DINGBIAN/original.pdf",
    ),
    "text_text": (
        "HUANENG_DINGBIAN/original.pdf",
        "STATE_GRID_NORTH_CHINA/original.pdf",
    ),
    "scan_scan": (
        "HUANENG_DINGBIAN/compare.pdf",
        "HUANENG_DINGBIAN/compare.pdf",
    ),
    "mixed_mixed": (
        "LIANJIANG_PINGPO/compare.pdf",
        "LIANJIANG_PINGPO/compare.pdf",
    ),
    "text_scan_real": (
        "HUANENG_DINGBIAN/original.pdf",
        "HUANENG_DINGBIAN/compare.pdf",
    ),
}


def side_routing(task, side: str) -> dict:
    extraction = (task.metrics.get("performance") or {}).get("extraction") or {}
    metric = (extraction.get("sides") or {}).get(side) or {}
    details = metric.get("details") or {}
    routing = details.get("ocr_routing") or {}
    page_count = metric.get("page_count")
    if routing:
        document_pages = routing.get("document_pages")
        native_pages = routing.get("native_pages")
        ocr_pages = routing.get("ocr_pages")
    else:
        # Routing inactive (always mode, or a pair pinned to whole-document OCR):
        # every page went through OCR.
        document_pages = page_count
        native_pages = 0
        ocr_pages = page_count
    return {
        "extractor_used": metric.get("extractor_used"),
        "duration_seconds": metric.get("duration_seconds"),
        "document_pages": document_pages,
        "native_pages": native_pages,
        "ocr_pages": ocr_pages,
        "ocr_ratio": routing.get("ocr_ratio"),
        "ocr_reasons": routing.get("ocr_reasons"),
        "native_extraction_ms": routing.get("native_extraction_ms"),
        "ocr_extraction_ms": routing.get("ocr_extraction_ms"),
        "total_extraction_ms": routing.get("total_extraction_ms"),
        "ocr_segments": routing.get("ocr_segments"),
        "routing_active": bool(routing),
    }


def summarize_diffs(task) -> dict:
    diffs = list(getattr(task, "diffs", []) or [])
    return {
        "total": len(diffs),
        "by_type": dict(Counter(getattr(diff, "diff_type", "?") for diff in diffs)),
        "by_status": dict(Counter(getattr(diff, "quality_status", "?") for diff in diffs)),
        "needs_review": sum(1 for d in diffs if getattr(d, "quality_status", "") == "NEEDS_REVIEW"),
        "by_source_type": dict(Counter(getattr(diff, "source_type", "?") or "text" for diff in diffs)),
    }


def diff_fingerprints(task) -> list[dict]:
    rows = []
    for diff in getattr(task, "diffs", []) or []:
        rows.append(
            {
                "diff_id": getattr(diff, "diff_id", ""),
                "type": getattr(diff, "diff_type", ""),
                "status": getattr(diff, "quality_status", ""),
                "source_type": getattr(diff, "source_type", ""),
                "title": getattr(diff, "title", ""),
                "original": (getattr(diff, "original_text", "") or "")[:400],
                "compare": (getattr(diff, "compare_text", "") or "")[:400],
                "flags": sorted(getattr(diff, "review_flags", []) or []),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["always", "auto", "off"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--pair", action="append", default=[], help="label=orig_rel:compare_rel")
    parser.add_argument("--overrides", default="{}", help="extra Settings overrides as JSON")
    parser.add_argument("--only", default="", help="comma separated scenario labels")
    args = parser.parse_args()

    root = args.root.resolve()
    corpus = (root / args.corpus).resolve()
    # configure_runtime creates the output directory itself and refuses to reuse one.
    if args.output.exists():
        shutil.rmtree(args.output)

    scenarios = dict(SUITE)
    for item in args.pair:
        label, paths = item.split("=", 1)
        original, compare = paths.split(":", 1)
        scenarios[label] = (original, compare)
    if args.only:
        wanted = {part.strip() for part in args.only.split(",") if part.strip()}
        scenarios = {k: v for k, v in scenarios.items() if k in wanted}

    overrides = json.loads(args.overrides)
    overrides["ocr_routing_mode"] = args.mode
    runtime = configure_runtime(root, root / "backend", args.output, overrides)
    service = build_service(runtime)
    print(f"mode={args.mode} overrides={overrides}", flush=True)

    results = []
    for label, (original_rel, compare_rel) in scenarios.items():
        original = corpus / original_rel
        compare = corpus / compare_rel
        for path in (original, compare):
            if not path.exists():
                raise SystemExit(f"missing fixture: {path}")
        print(f"[{label}] {original_rel} vs {compare_rel}", flush=True)
        with HttpRecorder(mode="live") as recorder:
            started = time.perf_counter()
            task = service.compare(original, compare, task_id=f"B{label[:8].upper()}{args.mode[:2].upper()}")
            wall = time.perf_counter() - started
        calls = Counter(record["endpoint"] for record in recorder.calls)
        entry = {
            "label": label,
            "mode": args.mode,
            "original": original_rel,
            "compare": compare_rel,
            "wall_seconds": round(wall, 3),
            "extraction_seconds": (
                (task.metrics.get("performance") or {}).get("extraction") or {}
            ).get("duration_seconds"),
            "total_duration_seconds": (task.metrics or {}).get("total_duration_seconds"),
            "sides": {
                "original": side_routing(task, "original"),
                "compare": side_routing(task, "compare"),
            },
            "ocr_requests": {k: v for k, v in calls.items() if "ocr" in k or "layout" in k},
            "all_requests": dict(calls),
            "diffs": summarize_diffs(task),
            "warnings": len(task.parse_warnings or []),
        }
        entry["ocr_pages_total"] = sum(
            (entry["sides"][side]["ocr_pages"] or 0) for side in ("original", "compare")
        )
        entry["missing_routing_metrics"] = [
            side for side in ("original", "compare") if entry["sides"][side]["document_pages"] is None
        ]
        results.append(entry)
        (args.output / f"{label}.diffs.json").write_text(
            json.dumps(diff_fingerprints(task), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"  wall={entry['wall_seconds']:.1f}s ocr_pages={entry['ocr_pages_total']} "
            f"diffs={entry['diffs']['total']} {entry['diffs']['by_type']}",
            flush=True,
        )

    (args.output / "summary.json").write_text(
        json.dumps({"mode": args.mode, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nwrote {args.output/'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
