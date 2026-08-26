from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import psutil


def matches(pattern: str) -> list[psutil.Process]:
    result = []
    for process in psutil.process_iter(["cmdline"]):
        try:
            command = " ".join(process.info["cmdline"] or [])
            if (
                process.pid != os.getpid()
                and "monitor_process_rss.py" not in command
                and pattern in command
            ):
                result.append(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return result


def descendants(processes: list[psutil.Process]) -> list[psutil.Process]:
    by_pid = {process.pid: process for process in processes}
    for process in list(processes):
        try:
            for child in process.children(recursive=True):
                by_pid[child.pid] = child
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return list(by_pid.values())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=0.25)
    args = parser.parse_args()
    peak = 0
    samples = 0
    started = time.time()
    while True:
        roots = matches(args.pattern)
        if not roots:
            break
        rss = 0
        for process in descendants(roots):
            try:
                rss += process.memory_info().rss
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        peak = max(peak, rss)
        samples += 1
        time.sleep(args.interval)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "pattern": args.pattern,
                "peak_combined_rss_bytes": peak,
                "samples": samples,
                "monitor_seconds": round(time.time() - started, 4),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
