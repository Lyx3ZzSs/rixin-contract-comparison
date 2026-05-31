from __future__ import annotations

import sys
from dataclasses import dataclass, field


def get_process_memory_mb() -> float:
    """Return current process RSS in MB using resource module."""
    import resource

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return rss / (1024 * 1024)
    return rss / 1024


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
