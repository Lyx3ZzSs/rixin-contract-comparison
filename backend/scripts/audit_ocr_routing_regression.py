"""Run the golden corpus once and dump everything needed for a before/after diff.

Combines the golden oracle check with a full dump of diffs and extraction routing
metrics, so two OCR routing configurations can be compared without paying for two
full OCR passes per configuration.

    GOLDEN_CONTRACT_DATA_ROOT=storage/golden-intake/contract-golden-v3 \
    .venv/bin/python backend/scripts/audit_ocr_routing_regression.py \
        --label before --output output/analysis/ocr-routing-optimization-20260920/regression/before
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))


def side_metrics(task, side: str) -> dict:
    extraction = (task.metrics.get("performance") or {}).get("extraction") or {}
    metric = (extraction.get("sides") or {}).get(side) or {}
    routing = (metric.get("details") or {}).get("ocr_routing") or {}
    page_count = metric.get("page_count")
    if routing:
        document_pages = routing.get("document_pages")
        native_pages = routing.get("native_pages")
        ocr_pages = routing.get("ocr_pages")
    else:
        # Routing disabled (OCR_ROUTING_MODE=always): the whole document was OCR-ed.
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
        "ocr_segments": routing.get("ocr_segments"),
        "coalesced_native_pages": routing.get("coalesced_native_pages"),
        "routing_active": bool(routing),
    }


def diff_rows(task) -> list[dict]:
    rows = []
    for diff in getattr(task, "diffs", []) or []:
        rows.append(
            {
                "type": diff.diff_type,
                "status": diff.quality_status,
                "source_type": getattr(diff, "source_type", "") or "text",
                "page": getattr(diff, "page_no", None),
                "title": getattr(diff, "title", ""),
                "original": (getattr(diff, "original_text", "") or "")[:300],
                "compare": (getattr(diff, "compare_text", "") or "")[:300],
                "flags": sorted(getattr(diff, "review_flags", []) or []),
                "region": getattr(diff, "source_type", ""),
            }
        )
    return rows


def run_contract(service, matcher, contract, label: str, output: Path) -> dict:
    started = time.perf_counter()
    task = service.compare(
        contract.original_pdf,
        contract.compare_pdf,
        task_id=f"audit-{contract.contract_key.lower()}-{uuid4().hex[:6]}",
        original_filename=contract.original_filename,
        compare_filename=contract.compare_filename,
    )
    wall = time.perf_counter() - started
    actual = [item.model_dump(mode="json") for item in task.diffs]
    report = matcher.match(contract.contract_key, contract.oracle["assertions"], actual)
    rows = diff_rows(task)
    entry = {
        "label": label,
        "contract_key": contract.contract_key,
        "wall_seconds": round(wall, 3),
        "original_pages": side_metrics(task, "original")["document_pages"],
        "original": side_metrics(task, "original"),
        "compare": side_metrics(task, "compare"),
        "pair_policy": ((task.metrics.get("performance") or {}).get("extraction") or {}).get(
            "ocr_routing_pair"
        ),
        "diffs": rows,
        "diff_count": len(rows),
        "by_type": dict(Counter(row["type"] for row in rows)),
        "by_status": dict(Counter(row["status"] for row in rows)),
        "by_source_type": dict(Counter(row["source_type"] for row in rows)),
        "oracle": {
            "failures": [failure.render() for failure in report.failures],
            "matched_positive": report.matched_positive,
            "matched_negative": report.matched_negative,
            "matched_review": report.matched_review,
            "warnings": list(report.warnings),
        },
    }
    (output / f"{contract.contract_key}.json").write_text(
        json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return entry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--contract", action="append", default=[])
    parser.add_argument("--resume", action="store_true", help="reuse per-contract results already on disk")
    parser.add_argument("--retries", type=int, default=3, help="retries per contract on transient errors")
    parser.add_argument("--retry-delay", type=float, default=20.0)
    args = parser.parse_args()

    os.environ.setdefault(
        "GOLDEN_CONTRACT_DATA_ROOT", str(REPO / "storage/golden-intake/contract-golden-v3")
    )

    from app.config import Settings
    from app.infrastructure.artifact_store import ArtifactStore
    from app.infrastructure.task_repository import SQLiteTaskRepository
    from app.services.compare_service import CompareService
    from tests.golden_contract.golden_loader import load_corpus, oracle_statistics
    from tests.golden_contract.semantic_matcher import SemanticGoldenMatcher

    args.output.mkdir(parents=True, exist_ok=True)
    corpus = load_corpus()
    if args.contract:
        wanted = set(args.contract)
        corpus = [item for item in corpus if item.contract_key in wanted]
    stats = oracle_statistics(load_corpus())

    runtime = Settings(
        _env_file=(REPO / ".env", REPO / "backend/.env"),
        base_dir=REPO,
        storage_dir=args.output / "runtime",
    )
    service = CompareService(
        repository=SQLiteTaskRepository(runtime), artifact_store=ArtifactStore(runtime)
    )
    matcher = SemanticGoldenMatcher()
    results = []
    for index, contract in enumerate(corpus, start=1):
        cached = args.output / f"{contract.contract_key}.json"
        if args.resume and cached.exists():
            entry = json.loads(cached.read_text(encoding="utf-8"))
            results.append(entry)
            print(
                f"[{index}/{len(corpus)}] {contract.contract_key:32s} RESUMED "
                f"ocr_pages={sum((entry[s]['ocr_pages'] or 0) for s in ('original','compare'))} "
                f"diffs={entry['diff_count']} oracle_failures={len(entry['oracle']['failures'])}",
                flush=True,
            )
            continue
        entry = None
        for attempt in range(1, args.retries + 1):
            try:
                entry = run_contract(service, matcher, contract, args.label, args.output)
                break
            except Exception as exc:
                print(
                    f"[{index}/{len(corpus)}] {contract.contract_key:32s} "
                    f"attempt {attempt}/{args.retries} failed: {type(exc).__name__}: {str(exc)[:160]}",
                    flush=True,
                )
                if attempt == args.retries:
                    raise
                time.sleep(args.retry_delay)
        results.append(entry)
        ocr_total = sum((entry[side]["ocr_pages"] or 0) for side in ("original", "compare"))
        print(
            f"[{index}/{len(corpus)}] {contract.contract_key:32s} wall={entry['wall_seconds']:6.1f}s "
            f"ocr_pages={ocr_total:3d} diffs={entry['diff_count']:4d} "
            f"oracle_failures={len(entry['oracle']['failures'])}",
            flush=True,
        )

    summary = {
        "label": args.label,
        "contracts": len(results),
        "wall_seconds": round(sum(item["wall_seconds"] for item in results), 1),
        "ocr_pages": sum(
            (item[side]["ocr_pages"] or 0)
            for item in results
            for side in ("original", "compare")
        ),
        "document_pages": sum(
            (item[side]["document_pages"] or 0)
            for item in results
            for side in ("original", "compare")
        ),
        "diffs": sum(item["diff_count"] for item in results),
        "by_type": dict(Counter(k for item in results for k, v in item["by_type"].items() for _ in range(v))),
        "by_status": dict(Counter(k for item in results for k, v in item["by_status"].items() for _ in range(v))),
        "by_source_type": dict(
            Counter(k for item in results for k, v in item["by_source_type"].items() for _ in range(v))
        ),
        "oracle_failures": sum(len(item["oracle"]["failures"]) for item in results),
        "oracle_expected": {
            "positive": stats["must_exist"] + stats["must_review"],
            "negative": stats["must_not_exist"] + stats["must_not_confirm"],
            "review": stats["must_review"],
        },
        "oracle_matched": {
            "positive": sum(item["oracle"]["matched_positive"] for item in results),
            "negative": sum(item["oracle"]["matched_negative"] for item in results),
            "review": sum(item["oracle"]["matched_review"] for item in results),
        },
        "ocr_reasons": dict(
            Counter(
                reason
                for item in results
                for side in ("original", "compare")
                for reason, count in (item[side]["ocr_reasons"] or {}).items()
                for _ in range(count)
            )
        ),
        "per_contract": [
            {
                "contract_key": item["contract_key"],
                "wall_seconds": item["wall_seconds"],
                "ocr_pages": sum((item[s]["ocr_pages"] or 0) for s in ("original", "compare")),
                "document_pages": sum((item[s]["document_pages"] or 0) for s in ("original", "compare")),
                "extractor_used": {
                    s: item[s]["extractor_used"] for s in ("original", "compare")
                },
                "diffs": item["diff_count"],
                "by_type": item["by_type"],
                "by_status": item["by_status"],
                "oracle_failures": len(item["oracle"]["failures"]),
            }
            for item in results
        ],
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "per_contract"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
