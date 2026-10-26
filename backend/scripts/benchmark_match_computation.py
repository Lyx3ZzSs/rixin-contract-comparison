"""Measure saved matching inputs with live/replayed model responses and RSS sampling."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_support import HttpRecorder, configure_runtime, http_failures, write_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--source-task-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--code-root", type=Path)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    parser.add_argument("--http-mode", choices=("live", "replay"), default="replay")
    parser.add_argument("--performance-strategy", choices=("off", "shadow", "enabled"), default="off")
    parser.add_argument("--deduplicate-model-inputs", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    code = (args.code_root or root / "backend").resolve()
    configure_runtime(root, code, args.output.resolve(), {
        "match_performance_strategy": args.performance_strategy,
        "match_deduplicate_model_inputs": args.deduplicate_model_inputs,
    })

    import psutil
    from app.models import Clause
    from app.services.page_alignment import PageAlignmentEntry, PageAlignmentResult, PageAlignmentStatus
    from app.services.pipeline_stages import MatchStage

    payload = json.loads(args.input.read_text())
    alignment = PageAlignmentResult(tuple(PageAlignmentEntry(
        status=PageAlignmentStatus(entry["status"]),
        original_pages=tuple(entry["original_pages"]), compare_pages=tuple(entry["compare_pages"]),
        alignment_score=entry["alignment_score"], anchors=tuple(entry["anchors"]), reason=entry["reason"],
    ) for entry in payload["page_alignment"]["entries"]))
    inputs = [[[Clause.model_validate(c) for c in payload[side]] for side in ("original", "compare")]
              for _ in range(args.workers)]
    matchers = [MatchStage().matcher for _ in range(args.workers)]
    process = psutil.Process()
    initial_rss = process.memory_info().rss
    peak = [initial_rss]
    stop = threading.Event()

    def sample():
        while not stop.wait(0.02):
            peak[0] = max(peak[0], process.memory_info().rss)

    def execute(index):
        started = time.perf_counter()
        pairs = matchers[index].match(*inputs[index], page_alignment=alignment)
        return pairs, time.perf_counter() - started

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    with HttpRecorder(mode=args.http_mode, cache=args.cache.resolve(), cache_by_task=True) as recorder:
        recorder.task_id = args.source_task_id
        started, cpu = time.perf_counter(), time.process_time()
        try:
            if args.workers == 1:
                results = [execute(0)]
            else:
                with ThreadPoolExecutor(max_workers=args.workers) as executor:
                    results = list(executor.map(execute, range(args.workers)))
            elapsed, cpu_seconds = time.perf_counter() - started, time.process_time() - cpu
        finally:
            peak[0] = max(peak[0], process.memory_info().rss)
            stop.set()
            sampler.join()

    hashes = []
    for index, (pairs, duration) in enumerate(results):
        data = [pair.model_dump(mode="json") for pair in pairs]
        hashes.append(hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
        write_json(args.output / f"pairs-{index}.json", data)
    result = {
        "code_root": str(code), "workers": args.workers, "input": str(args.input),
        "http_mode": args.http_mode, "performance_strategy": args.performance_strategy,
        "deduplicate_model_inputs": args.deduplicate_model_inputs,
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "seconds": elapsed, "cpu_seconds": cpu_seconds,
        "worker_seconds": [duration for _, duration in results],
        "rss_initial_mb": initial_rss / 1024**2, "rss_peak_mb": peak[0] / 1024**2,
        "rss_increment_mb": (peak[0] - initial_rss) / 1024**2,
        "http_calls": len(recorder.calls), "http_failures": http_failures(recorder.calls),
        "cache_misses": sum(bool(call.get("cache_miss")) for call in recorder.calls),
        "pairs_sha256": hashes,
        "performance": [matcher.last_performance_metrics for matcher in matchers],
    }
    write_json(args.output / "result.json", result)
    write_json(args.output / "http-calls.json", recorder.calls)
    print(json.dumps({k: v for k, v in result.items() if k != "performance"}), flush=True)
    return int(result["http_failures"] > 0 or result["cache_misses"] > 0 or len(set(hashes)) != 1)


if __name__ == "__main__":
    raise SystemExit(main())
