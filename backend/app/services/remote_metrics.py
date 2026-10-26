"""Capture explicit service timing evidence without inventing server durations."""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

from app.services.pipeline_metrics import PerformanceRecorder


def record_remote_metrics(recorder: PerformanceRecorder, headers: Mapping[str, str], payload: Any) -> None:
    """Keep numeric Server-Timing durations separate from client wall time.

    Descriptions, arbitrary headers and response text are intentionally omitted.
    Durations may overlap, so their sum is not the request duration.
    """
    timing = next((str(v) for k, v in headers.items() if k.lower() == "server-timing"), "")
    count = 0
    for metric in timing[:4096].split(",")[:32]:
        match = re.fullmatch(r"\s*([A-Za-z][A-Za-z0-9_-]{0,63})\s*((?:;[^,]*)?)", metric)
        if not match:
            continue
        duration = re.search(r'(?:^|;)\s*dur\s*=\s*"?([0-9]+(?:\.[0-9]+)?)"?\s*(?:;|$)', match[2])
        if duration is None:
            continue
        milliseconds = float(duration[1])
        if math.isfinite(milliseconds):
            recorder.record_duration(f"remote_reported_{match[1]}", milliseconds / 1000)
            count += 1
    recorder.set_counter("remote_timing_available", int(count > 0))
    # Paddle services commonly supply a logId; do not retain any other payload.
    request_id = payload.get("logId") if isinstance(payload, dict) else None
    if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id):
        recorder.set_attribute("remote_request_id", request_id)
