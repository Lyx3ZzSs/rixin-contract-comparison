from __future__ import annotations

import sys
from dataclasses import dataclass, field


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
