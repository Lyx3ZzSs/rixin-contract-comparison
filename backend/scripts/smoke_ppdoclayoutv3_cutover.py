"""Isolated live pipeline smoke for the production V3 extraction path."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import threading
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("original", type=Path)
    parser.add_argument("compare", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["STORAGE_DIR"] = str(args.output / "storage")
    os.environ["AUTH_MODE"] = "disabled"
    os.environ["BENCHMARK_DIAGNOSTICS"] = "off"

    import httpx
    from app.services.compare_service import CompareService

    calls: Counter[str] = Counter()
    lock = threading.Lock()
    original_send = httpx.Client.send

    def guarded_send(self, request, *positional, **keyword):
        path = request.url.path
        if "/layout-parsing" in path:
            raise AssertionError("Removed PP-Structure endpoint was called")
        with lock:
            calls[path] += 1
        return original_send(self, request, *positional, **keyword)

    httpx.Client.send = guarded_send
    stages: list[dict] = []

    class MeasuredService(CompareService):
        def _build_pipeline(self):
            pipeline = super()._build_pipeline()
            for stage in pipeline.stages:
                original_execute = stage.execute
                name = type(stage).__name__

                def measured(ctx, *, _execute=original_execute, _name=name):
                    started = time.perf_counter()
                    try:
                        return _execute(ctx)
                    finally:
                        stages.append({"stage": _name, "seconds": round(time.perf_counter() - started, 3)})

                stage.execute = measured
            return pipeline

    started = time.perf_counter()
    try:
        task = MeasuredService().compare(args.original, args.compare, task_id="cutover-" + str(time.time_ns()))
        extraction = task.metrics.get("performance", {}).get("extraction", {})
        sides = extraction.get("sides", {})
        routes = Counter()
        table_calls = 0
        for side in sides.values():
            routes.update(side.get("details", {}).get("routes", {}))
            table_calls += side.get("details", {}).get("table_calls", 0)
        result = {
            "status": "COMPLETE" if stages and stages[-1]["stage"] == "SummaryStage" and not task.errors else "ERROR",
            "task_status": task.status,
            "errors": list(task.errors),
            "pipeline_seconds": round(time.perf_counter() - started, 3),
            "extraction_seconds": next(
                (stage["seconds"] for stage in stages if stage["stage"] == "ExtractionStage"), None
            ),
            "stages": stages,
            "routes": dict(routes),
            "requests": dict(calls),
            "layout_calls": calls["/layout-detection"],
            "ocr_calls": calls["/ocr"],
            "table_calls": table_calls,
            "layout_parsing_calls": calls["/layout-parsing"],
            "diff_count": task.diff_count,
            "extractor_used": task.extractor_used,
        }
    except Exception as exc:
        result = {
            "status": "ERROR",
            "exception_type": type(exc).__name__,
            "exception": str(exc)[:300],
            "stages": stages,
            "requests": dict(calls),
            "pipeline_seconds": round(time.perf_counter() - started, 3),
        }
        raise
    finally:
        (args.output / "smoke.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "status",
                    "pipeline_seconds",
                    "extraction_seconds",
                    "routes",
                    "layout_calls",
                    "ocr_calls",
                    "diff_count",
                )
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
