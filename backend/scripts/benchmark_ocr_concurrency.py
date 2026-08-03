from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.extractors.base import DocumentExtractor  # noqa: E402
from app.services.extractors.ppocrv5 import PPOCRV5Extractor  # noqa: E402
from app.services.extractors.ppstructure import PPStructureExtractor  # noqa: E402
from app.services.extractors.ppstructure_ocr_hybrid import PPStructureOCRHybridExtractor  # noqa: E402


ExtractorFactory = Callable[[], DocumentExtractor]


@dataclass(frozen=True)
class InvocationResult:
    filename: str
    duration_seconds: float
    status: str
    page_count: int = 0
    extractor_used: str = ""
    error_type: str = ""


def run_concurrency_level(
    extractor_factory: ExtractorFactory,
    pdf_paths: Sequence[Path],
    *,
    concurrency: int,
    repetitions: int = 1,
) -> dict[str, Any]:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    if not pdf_paths:
        raise ValueError("at least one PDF path is required")

    jobs = [path for _ in range(repetitions) for path in pdf_paths]
    start_gate = threading.Event()

    def invoke(path: Path) -> InvocationResult:
        extractor = extractor_factory()
        start_gate.wait()
        started_at = time.perf_counter()
        try:
            result = extractor.extract(path, task_id=None)
        except Exception as exc:
            return InvocationResult(
                filename=path.name,
                duration_seconds=round(time.perf_counter() - started_at, 6),
                status="FAILED",
                error_type=type(exc).__name__,
            )
        return InvocationResult(
            filename=path.name,
            duration_seconds=round(time.perf_counter() - started_at, 6),
            status="SUCCEEDED",
            page_count=result.document.page_count,
            extractor_used=result.extractor_used,
        )

    wall_started_at = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="ocr-benchmark") as executor:
        futures = [executor.submit(invoke, path) for path in jobs]
        start_gate.set()
        invocations = [future.result() for future in futures]
    wall_duration = time.perf_counter() - wall_started_at

    successful_durations = [
        invocation.duration_seconds for invocation in invocations if invocation.status == "SUCCEEDED"
    ]
    return {
        "concurrency": concurrency,
        "job_count": len(jobs),
        "success_count": len(successful_durations),
        "failure_count": len(jobs) - len(successful_durations),
        "wall_duration_seconds": round(wall_duration, 6),
        "throughput_documents_per_second": round(len(successful_durations) / wall_duration, 6)
        if wall_duration > 0
        else 0.0,
        "mean_duration_seconds": round(statistics.fmean(successful_durations), 6) if successful_durations else 0.0,
        "p95_duration_seconds": round(_nearest_rank_percentile(successful_durations, 0.95), 6),
        "invocations": [asdict(invocation) for invocation in invocations],
    }


def _nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def _extractor_factory(name: str) -> ExtractorFactory:
    if name == "ppocrv5":
        return PPOCRV5Extractor
    if name == "ppstructure":
        return PPStructureExtractor
    if name == "ppstructure_ocr_hybrid":
        return PPStructureOCRHybridExtractor
    raise ValueError(f"Unsupported extractor: {name}")


def _validate_pdf_paths(paths: Sequence[Path]) -> list[Path]:
    validated: list[Path] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"PDF does not exist: {resolved}")
        if resolved.suffix.lower() != ".pdf":
            raise ValueError(f"Only PDF inputs are supported: {resolved}")
        validated.append(resolved)
    return validated


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark configured OCR services at controlled concurrency levels.",
    )
    parser.add_argument("--pdf", action="append", type=Path, required=True, help="Input PDF; repeat for a corpus.")
    parser.add_argument(
        "--extractor",
        choices=["ppocrv5", "ppstructure", "ppstructure_ocr_hybrid"],
        default="ppocrv5",
    )
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 2, 4])
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    pdf_paths = _validate_pdf_paths(args.pdf)
    levels = sorted(set(args.concurrency))
    if any(level < 1 for level in levels):
        parser.error("--concurrency values must be at least 1")
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")

    factory = _extractor_factory(args.extractor)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "extractor": args.extractor,
        "pdfs": [
            {
                "filename": path.name,
                "size_bytes": path.stat().st_size,
            }
            for path in pdf_paths
        ],
        "repetitions": args.repetitions,
        "levels": [
            run_concurrency_level(
                factory,
                pdf_paths,
                concurrency=level,
                repetitions=args.repetitions,
            )
            for level in levels
        ],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is None:
        print(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(args.output)
    return 1 if any(level["failure_count"] for level in report["levels"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
