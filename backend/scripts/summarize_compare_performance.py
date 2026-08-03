from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402


def summarize_tasks(tasks_dir: Path) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for task_path in sorted(tasks_dir.glob("*/task.json")):
        try:
            payload = json.loads(task_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            skipped.append({"path": str(task_path), "reason": type(exc).__name__})
            continue
        if not _is_compare_task(payload):
            skipped.append({"path": str(task_path), "reason": "not_compare_task"})
            continue
        sample = _task_sample(payload)
        if sample is None:
            skipped.append({"path": str(task_path), "reason": "missing_performance_metrics"})
            continue
        samples.append(sample)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        grouped[_page_bucket(int(sample["total_pages"]))].append(sample)
    return {
        "tasks_dir": str(tasks_dir),
        "sample_count": len(samples),
        "skipped_count": len(skipped),
        "overall": _aggregate(samples),
        "page_buckets": {bucket: _aggregate(grouped.get(bucket, [])) for bucket in ("0-30", "31-100", "101+")},
        "samples": samples,
        "skipped": skipped,
    }


def _is_compare_task(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("task_id"), str)
        and "original_filename" in payload
        and "compare_filename" in payload
    )


def _task_sample(payload: dict[str, Any]) -> dict[str, Any] | None:
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        return None
    total_duration = _number(metrics.get("total_duration_seconds"))
    stages = metrics.get("stages")
    if total_duration is None or not isinstance(stages, list):
        return None

    extraction_duration = _stage_duration(stages, "文档解析中")
    matching_duration = _stage_duration(stages, "条款匹配中")
    profiles = payload.get("document_profiles") if isinstance(payload.get("document_profiles"), dict) else {}
    original_profile = profiles.get("original") if isinstance(profiles.get("original"), dict) else {}
    compare_profile = profiles.get("compare") if isinstance(profiles.get("compare"), dict) else {}
    total_pages = int(_number(original_profile.get("page_count")) or 0) + int(
        _number(compare_profile.get("page_count")) or 0
    )
    performance = metrics.get("performance") if isinstance(metrics.get("performance"), dict) else {}
    extraction_performance = performance.get("extraction") if isinstance(performance.get("extraction"), dict) else {}
    extraction_sides = (
        extraction_performance.get("sides") if isinstance(extraction_performance.get("sides"), dict) else {}
    )
    native_fast_path = (
        extraction_performance.get("native_fast_path")
        if isinstance(extraction_performance.get("native_fast_path"), dict)
        else {}
    )
    matching_performance = performance.get("matching") if isinstance(performance.get("matching"), dict) else {}
    matching_counters = (
        matching_performance.get("counters") if isinstance(matching_performance.get("counters"), dict) else {}
    )
    return {
        "task_id": payload["task_id"],
        "status": str(payload.get("status") or ""),
        "total_pages": total_pages,
        "total_duration_seconds": round(total_duration, 6),
        "extraction_duration_seconds": round(extraction_duration, 6),
        "matching_duration_seconds": round(matching_duration, 6),
        "hotspot_ratio": round((extraction_duration + matching_duration) / total_duration, 6)
        if total_duration > 0
        else 0.0,
        "peak_memory_mb": _number(metrics.get("peak_memory_mb")) or 0.0,
        "extraction_mode": str(extraction_performance.get("mode") or ""),
        "native_fast_path_decision": str(native_fast_path.get("decision") or ""),
        "extraction_queue_wait_seconds": round(
            sum(
                _number(side.get("queue_wait_duration_seconds")) or 0.0
                for side in extraction_sides.values()
                if isinstance(side, dict)
            ),
            6,
        ),
        "embedding_http_request_count": int(_number(matching_counters.get("embedding_http_request_count")) or 0),
        "rerank_http_request_count": int(_number(matching_counters.get("rerank_http_request_count")) or 0),
    }


def _stage_duration(stages: list[Any], name: str) -> float:
    return sum(
        _number(stage.get("duration_seconds")) or 0.0
        for stage in stages
        if isinstance(stage, dict) and stage.get("name") == name
    )


def _number(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _page_bucket(total_pages: int) -> str:
    if total_pages <= 30:
        return "0-30"
    if total_pages <= 100:
        return "31-100"
    return "101+"


def _aggregate(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {
            "sample_count": 0,
            "p50_duration_seconds": 0.0,
            "p95_duration_seconds": 0.0,
            "mean_duration_seconds": 0.0,
            "mean_extraction_ratio": 0.0,
            "mean_matching_ratio": 0.0,
            "parallel_sample_count": 0,
            "serial_sample_count": 0,
            "mean_extraction_queue_wait_seconds": 0.0,
            "native_fast_path_hit_count": 0,
            "native_fast_path_shadow_eligible_count": 0,
            "native_fast_path_rejected_count": 0,
        }
    durations = [float(sample["total_duration_seconds"]) for sample in samples]
    extraction_ratios = [
        float(sample["extraction_duration_seconds"]) / duration
        for sample, duration in zip(samples, durations, strict=True)
        if duration > 0
    ]
    matching_ratios = [
        float(sample["matching_duration_seconds"]) / duration
        for sample, duration in zip(samples, durations, strict=True)
        if duration > 0
    ]
    return {
        "sample_count": len(samples),
        "p50_duration_seconds": round(_nearest_rank_percentile(durations, 0.50), 6),
        "p95_duration_seconds": round(_nearest_rank_percentile(durations, 0.95), 6),
        "mean_duration_seconds": round(statistics.fmean(durations), 6),
        "mean_extraction_ratio": round(statistics.fmean(extraction_ratios), 6) if extraction_ratios else 0.0,
        "mean_matching_ratio": round(statistics.fmean(matching_ratios), 6) if matching_ratios else 0.0,
        "parallel_sample_count": sum(sample.get("extraction_mode") == "parallel" for sample in samples),
        "serial_sample_count": sum(sample.get("extraction_mode") == "serial" for sample in samples),
        "mean_extraction_queue_wait_seconds": round(
            statistics.fmean(float(sample.get("extraction_queue_wait_seconds") or 0.0) for sample in samples),
            6,
        ),
        "native_fast_path_hit_count": sum(sample.get("native_fast_path_decision") == "accepted" for sample in samples),
        "native_fast_path_shadow_eligible_count": sum(
            sample.get("native_fast_path_decision") == "shadow_eligible" for sample in samples
        ),
        "native_fast_path_rejected_count": sum(
            sample.get("native_fast_path_decision") in {"rejected", "shadow_rejected", "evaluation_failed"}
            for sample in samples
        ),
    }


def _nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize persisted contract-comparison performance metrics.")
    parser.add_argument(
        "--tasks-dir",
        type=Path,
        default=settings.tasks_dir or settings.storage_dir / "tasks",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    report = summarize_tasks(args.tasks_dir.expanduser().resolve())
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is None:
        print(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
