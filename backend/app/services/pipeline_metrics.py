from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator


def get_process_memory_mb() -> float:
    """Return peak process memory usage in MB, or zero when metrics are unavailable."""
    try:
        if sys.platform == "win32":
            raise ImportError("resource is not available on Windows")

        import resource

        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return rss / (1024 * 1024)
        return rss / 1024
    except Exception:
        pass

    try:
        import psutil

        memory_info = psutil.Process().memory_info()
        memory_bytes = getattr(memory_info, "peak_wset", memory_info.rss)
        return memory_bytes / (1024 * 1024)
    except Exception:
        return 0.0


@dataclass
class StageMetrics:
    name: str
    duration_seconds: float = 0.0
    memory_mb_start: float = 0.0
    memory_mb_end: float = 0.0
    error: str | None = None


@dataclass
class PipelineMetrics:
    task_id: str
    started_at: str = ""
    finished_at: str = ""
    total_duration_seconds: float = 0.0
    stages: list[StageMetrics] = field(default_factory=list)
    peak_memory_mb: float = 0.0


class PerformanceRecorder:
    """Thread-safe recorder for nested pipeline operation timings and counts."""

    def __init__(self) -> None:
        self._started_at = time.perf_counter()
        self._operations: dict[str, dict[str, float | int]] = {}
        self._counters: dict[str, int | float] = {}
        self._lock = threading.Lock()

    @contextmanager
    def measure(self, operation: str) -> Iterator[None]:
        started_at = time.perf_counter()
        try:
            yield
        finally:
            self.record_duration(operation, time.perf_counter() - started_at)

    def record_duration(self, operation: str, duration_seconds: float) -> None:
        duration = max(0.0, float(duration_seconds))
        with self._lock:
            metric = self._operations.setdefault(
                operation,
                {
                    "count": 0,
                    "duration_seconds": 0.0,
                    "max_duration_seconds": 0.0,
                },
            )
            metric["count"] = int(metric["count"]) + 1
            metric["duration_seconds"] = float(metric["duration_seconds"]) + duration
            metric["max_duration_seconds"] = max(float(metric["max_duration_seconds"]), duration)

    def increment(self, counter: str, value: int | float = 1) -> None:
        with self._lock:
            self._counters[counter] = self._counters.get(counter, 0) + value

    def set_counter(self, counter: str, value: int | float) -> None:
        with self._lock:
            self._counters[counter] = value

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            operations = {
                name: {
                    "count": int(metric["count"]),
                    "duration_seconds": round(float(metric["duration_seconds"]), 6),
                    "max_duration_seconds": round(float(metric["max_duration_seconds"]), 6),
                }
                for name, metric in sorted(self._operations.items())
            }
            counters = dict(sorted(self._counters.items()))
        return {
            "duration_seconds": round(time.perf_counter() - self._started_at, 6),
            "operations": operations,
            "counters": counters,
        }
