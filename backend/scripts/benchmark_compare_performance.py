"""Isolated comparison benchmark retaining configured semantic/visual models.

Live runs capture HTTP responses; replay fails on every missing response even
when a model provider would otherwise silently fall back. No production task is
updated. Use --code-root to execute a frozen backend snapshot.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_support import (  # noqa: E402
    HttpRecorder, build_service, code_hashes, configure_runtime, digest,
    http_failures, input_paths, save_task, write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--code-root", type=Path, help="backend directory containing app/")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--cache-by-task", action="store_true", help="Isolate captured responses by source task")
    parser.add_argument("--http-mode", choices=("live", "replay"), required=True)
    parser.add_argument("--extraction", choices=("live", "saved"), default="live")
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--manifest", type=Path, help="Baseline inputs.json containing task_id entries")
    parser.add_argument("--overrides", type=Path, help="JSON Settings overrides (no credentials)")
    parser.add_argument("--capture-preparation-input", action="store_true",
                        help="Save input documents for isolated preparation profiling")
    parser.add_argument("--wait-for-run", type=Path, help="Replay only: await each task's completed capture in this run")
    args = parser.parse_args()
    if args.wait_for_run and args.http_mode != "replay":
        parser.error("--wait-for-run requires replay mode")
    root = args.source_root.resolve()
    code = (args.code_root or root / "backend").resolve()
    output = args.output.resolve()
    cache = args.cache.resolve()
    overrides = json.loads(args.overrides.read_text()) if args.overrides else {}
    runtime = configure_runtime(root, code, output, overrides)

    import psutil
    from app.infrastructure.task_runner import QueuedTaskRunner
    from app.models import CompareTask
    from scripts.replay_reviewed_tasks import _ComponentReplayExtractor

    task_ids = args.task_id or [x["task_id"] for x in json.loads(args.manifest.read_text())]
    write_json(output / "code-hashes.json", code_hashes(code))
    safe_keys = [k for k in type(runtime).model_fields
                 if k.startswith(("compare_", "hybrid_", "match_", "task_runner_"))
                 and not any(s in k for s in ("key", "url", "token", "path"))]
    config_record = {k: getattr(runtime, k) for k in safe_keys}
    write_json(output / "run-config.json", {
        "code_root": str(code), "http_mode": args.http_mode, "extraction": args.extraction,
        "settings": config_record, "server_cache_state": "unknown",
        "settings_hash": digest(runtime.model_dump_json().encode()),
    })

    reports = []
    with HttpRecorder(mode=args.http_mode, cache=cache, cache_by_task=args.cache_by_task) as recorder:
        for task_id in task_ids:
            if args.wait_for_run:
                deadline = time.monotonic() + 1800
                captured_result = args.wait_for_run / task_id / "result.json"
                while not captured_result.exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Capture did not finish: {task_id}")
                    time.sleep(0.5)
            recorder.task_id = task_id
            target = output / task_id
            target.mkdir()
            source = root / "storage/tasks" / task_id
            inputs = list(input_paths(root, task_id).values())
            extractor = None
            if args.extraction == "saved":
                raw = {}
                for path in (source / "diagnostics/ocr").glob("*hybrid_raw.json"):
                    raw[json.loads(path.read_text())["document"]["filename"]] = path
                extractor = _ComponentReplayExtractor(raw)
            service = build_service(runtime, extractor)
            pipeline = service._build_pipeline()
            for stage in pipeline.stages:
                execute = stage.execute
                name = type(stage).__name__

                def capture(ctx, execute=execute, name=name):
                    if name == "DocumentPreparationStage" and args.capture_preparation_input:
                        (target / "preparation-input.json").write_text(json.dumps({
                            side: getattr(ctx, side + "_extraction").document.model_dump(mode="json")
                            for side in ("original", "compare")}, ensure_ascii=False))
                    if name == "PageAlignmentStage":
                        (target / "prepared-documents.json").write_text(json.dumps({
                            side: getattr(ctx, side + "_extraction").document.model_dump(mode="json")
                            for side in ("original", "compare")}, ensure_ascii=False))
                    if name == "MatchStage":
                        (target / "matching-input.json").write_text(json.dumps({
                            "original": [x.model_dump(mode="json") for x in ctx.original_clauses],
                            "compare": [x.model_dump(mode="json") for x in ctx.compare_clauses],
                            "page_alignment": ctx.page_alignment.to_debug_payload(),
                        }, ensure_ascii=False))
                    execute(ctx)

                stage.execute = capture
            service._build_pipeline = lambda: pipeline
            replay_id = "bench-" + task_id
            service.repository.save_compare_task(CompareTask(task_id=replay_id))
            runner = QueuedTaskRunner(repository=service.repository, app_settings=runtime, max_workers=1)
            runner.register_handler("compare", lambda context, payload: service.compare(
                *inputs, task_id=replay_id, execution_context=context))
            start, cpu = time.perf_counter(), time.process_time()
            process = psutil.Process()
            initial_rss = process.memory_info().rss
            peak_rss = [initial_rss]
            sampling_done = threading.Event()

            def sample_memory():
                while not sampling_done.wait(0.05):
                    peak_rss[0] = max(peak_rss[0], process.memory_info().rss)

            sampler = threading.Thread(target=sample_memory, daemon=True)
            sampler.start()
            print(f"START {task_id} {args.extraction}/{args.http_mode}", flush=True)
            try:
                runner.submit(task_type="compare", task_id=replay_id, payload={})
                while True:
                    task = service.repository.load_compare_task(replay_id)
                    if task.execution_status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                        break
                    time.sleep(0.1)
            finally:
                runner.stop()
                sampling_done.set()
                sampler.join()
            save_task(target, task)
            write_json(target / "input-hashes.json", {str(p.relative_to(root)): digest(p.read_bytes()) for p in inputs})
            task_calls = [c for c in recorder.calls if c["task_id"] == task_id]
            report = {"task_id": task_id, "status": task.status, "wall_seconds": time.perf_counter() - start,
                      "cpu_seconds": time.process_time() - cpu, "diffs": len(task.diffs),
                      "rss_peak_mb": peak_rss[0] / 1024**2,
                      "rss_increment_mb": max(0, peak_rss[0] - initial_rss) / 1024**2,
                      "http_calls": len(task_calls), "http_failures": http_failures(task_calls),
                      "cache_misses": sum(bool(c.get("cache_miss")) for c in task_calls)}
            reports.append(report)
            write_json(output / "http-calls.json", recorder.calls)
            write_json(output / "summary.json", reports)
            print(json.dumps(report), flush=True)
    return int(any(r["status"] != "COMPLETED" or r["cache_misses"] or r["http_failures"] for r in reports))


if __name__ == "__main__":
    raise SystemExit(main())
