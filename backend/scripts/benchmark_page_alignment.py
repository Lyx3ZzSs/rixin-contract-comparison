"""Alternate frozen/current alignment on captured, prepared documents."""
from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.models import Document  # noqa: E402
from app.services.page_alignment import PageSequenceAligner  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    import psutil
    spec = importlib.util.spec_from_file_location("frozen_page_alignment", args.baseline)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    rows = []
    for path in args.input:
        payload = json.loads(path.read_text())
        docs = [Document.model_validate(payload[side]) for side in ("original", "compare")]
        expected = None
        for iteration in range(args.rounds):
            variants = [("baseline", module.PageSequenceAligner), ("candidate", PageSequenceAligner)]
            if iteration % 2:
                variants.reverse()
            for name, cls in variants:
                gc.collect()
                process = psutil.Process()
                initial = process.memory_info().rss
                peak = [initial]
                stop = threading.Event()

                def sample():
                    while not stop.wait(0.02):
                        peak[0] = max(peak[0], process.memory_info().rss)

                sampler = threading.Thread(target=sample)
                sampler.start()
                start, cpu = time.perf_counter(), time.process_time()
                try:
                    result = cls().align(*docs)
                    elapsed, cpu_seconds = time.perf_counter() - start, time.process_time() - cpu
                finally:
                    stop.set()
                    sampler.join()
                actual = result.to_debug_payload()
                if expected is None:
                    expected = actual
                row = {"input": str(path), "round": iteration, "variant": name,
                       "seconds": elapsed, "cpu_seconds": cpu_seconds,
                       "rss_increment_mb": max(0, peak[0] - initial) / 1024**2,
                       "output_equal": actual == expected,
                       "performance": getattr(result, "performance", {})}
                rows.append(row)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
                print(json.dumps({k: v for k, v in row.items() if k != "performance"}), flush=True)
    return int(any(not row["output_equal"] for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
