"""Measure real queued comparisons in one process with an isolated task database."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time


sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmark_support import (  # noqa: E402
    HttpRecorder, build_service, code_hashes, configure_runtime, digest,
    http_failures, input_paths, save_task, write_json,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--task-id", action="append", required=True)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--code-root", type=Path)
    parser.add_argument("--http-mode", choices=("live", "replay"), default="live")
    parser.add_argument("--cache", type=Path, help="Flat response cache shared by concurrent tasks")
    args = parser.parse_args()
    if args.http_mode == "replay" and args.cache is None:
        parser.error("--http-mode replay requires --cache")
    root = Path(__file__).resolve().parents[2]
    code = (args.code_root or root / "backend").resolve()
    output = args.output.resolve()
    runtime = configure_runtime(root, code, output)

    import psutil
    from app.infrastructure.task_runner import QueuedTaskRunner
    from app.models import CompareTask

    service = build_service(runtime)
    repository = service.repository
    runner = QueuedTaskRunner(repository=repository, app_settings=runtime,
                              max_workers=args.workers, autostart=False)
    services = {}
    runner.register_handler("compare", lambda context, payload: services[context.task_id].compare(
        payload["original"], payload["compare"], task_id=context.task_id, execution_context=context))
    ids = []
    hashes = {}
    for source_id in args.task_id:
        paths = input_paths(root, source_id)
        task_id = "concurrent-" + source_id
        services[task_id] = build_service(runtime)
        ids.append(task_id)
        hashes[source_id] = {side: digest(path.read_bytes()) for side, path in paths.items()}
        repository.save_compare_task(CompareTask(task_id=task_id))
        runner.submit(task_type="compare", task_id=task_id, payload={k: str(v) for k, v in paths.items()})
    started, cpu = time.perf_counter(), time.process_time()
    process = psutil.Process()
    initial = peak = process.memory_info().rss
    observed = {}
    with HttpRecorder(mode=args.http_mode, cache=args.cache) as recorder:
        try:
            runner.start()
            while True:
                peak = max(peak, process.memory_info().rss)
                tasks = [repository.load_compare_task(task_id) for task_id in ids]
                for task in tasks:
                    state = (task.status, task.stage)
                    if observed.get(task.task_id) != state:
                        print(json.dumps({"task_id": task.task_id, "status": task.status, "stage": task.stage},
                                         ensure_ascii=False), flush=True)
                        observed[task.task_id] = state
                if all(task.execution_status in {"SUCCEEDED", "FAILED", "CANCELLED"} for task in tasks):
                    break
                time.sleep(0.1)
        finally:
            runner.stop()
    elapsed = time.perf_counter() - started
    rows = []
    for source_id, task in zip(args.task_id, tasks, strict=True):
        target = output / source_id
        save_task(target, task)
        queued = datetime.fromisoformat(task.execution_queued_at)
        begun = datetime.fromisoformat(task.execution_started_at)
        finished = datetime.fromisoformat(task.execution_finished_at)
        rows.append({"task_id": source_id, "status": task.status, "diffs": len(task.diffs),
                     "queue_seconds": (begun - queued).total_seconds(),
                     "execution_seconds": (finished - begun).total_seconds(),
                     "user_wait_seconds": (finished - queued).total_seconds()})
    failures = http_failures(recorder.calls)
    result = {"workers": args.workers, "http_mode": args.http_mode, "extraction": "live",
              "server_cache_state": "unknown", "wall_seconds": elapsed,
              "cpu_seconds": time.process_time() - cpu, "rss_peak_mb": peak / 1024**2,
              "rss_increment_mb": (peak - initial) / 1024**2,
              "tasks_per_minute": sum(row["status"] == "COMPLETED" for row in rows) * 60 / elapsed,
              "http_calls": len(recorder.calls), "http_failures": failures, "tasks": rows, "input_hashes": hashes,
              "code_hashes": code_hashes(code)}
    write_json(output / "http-calls.json", recorder.calls)
    write_json(output / "summary.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "code_hashes"}, indent=2), flush=True)
    return int(failures > 0 or any(task.status != "COMPLETED" for task in tasks))


if __name__ == "__main__":
    raise SystemExit(main())
