from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

import fitz
import psutil
from paddleocr import PaddleOCRVL

from run_offline_benchmark import discover_corpus


ROOT = Path(__file__).resolve().parents[2]
CACHE = Path(__file__).resolve().parent / "cache" / "paddle-full"


def related_rss(port: str) -> int:
    total = psutil.Process().memory_info().rss
    for process in psutil.process_iter(["pid", "cmdline", "memory_info"]):
        if process.info["pid"] == psutil.Process().pid:
            continue
        try:
            command = " ".join(process.info["cmdline"] or [])
            if "mlx_vlm.server" in command and port in command:
                total += process.info["memory_info"].rss
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return total


class MemorySampler:
    def __init__(self, port: str) -> None:
        self.port = port
        self.peak = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.wait(0.1):
            self.peak = max(self.peak, related_rss(self.port))

    def __enter__(self) -> "MemorySampler":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, related_rss(self.port))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", default="http://127.0.0.1:18080/v1")
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    port = args.server_url.split(":")[-1].split("/")[0]
    layout = Path.home() / ".paddlex" / "official_models" / "PP-DocLayoutV3"
    model_path = next(
        Path.home().glob(
            ".cache/huggingface/hub/models--PaddlePaddle--PaddleOCR-VL-1.6/snapshots/*"
        )
    )
    pipeline = PaddleOCRVL(
        pipeline_version="v1.6",
        layout_detection_model_dir=str(layout),
        vl_rec_backend="mlx-vlm-server",
        vl_rec_server_url=args.server_url,
        vl_rec_api_model_name=str(model_path),
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_chart_recognition=False,
        use_seal_recognition=True,
    )
    rows: list[dict[str, object]] = []
    started_all = time.perf_counter()
    with MemorySampler(port) as memory:
        for item in sorted(discover_corpus()[0], key=lambda value: value.case_id):
            case_dir = CACHE / item.case_id
            case_dir.mkdir(exist_ok=True)
            with fitz.open(item.pdf) as pdf:
                for page_index, page in enumerate(pdf):
                    output_path = case_dir / f"page-{page_index + 1:04d}.json"
                    if output_path.exists():
                        rows.append(
                            {
                                "case_id": item.case_id,
                                "page_no": page_index + 1,
                                "status": "CACHED",
                                "seconds": 0.0,
                            }
                        )
                        continue
                    image_path = case_dir / ".current-page.png"
                    page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(image_path)
                    started = time.perf_counter()
                    try:
                        outputs = pipeline.predict(
                            str(image_path),
                            use_doc_orientation_classify=False,
                            use_doc_unwarping=False,
                            use_seal_recognition=True,
                        )
                        raw = outputs[0].json if outputs else {"res": {}}
                        output_path.write_text(
                            json.dumps(raw, ensure_ascii=False), encoding="utf-8"
                        )
                        status = "OK"
                        error_type = None
                    except Exception as exc:
                        status = "FAILED"
                        error_type = type(exc).__name__
                    finally:
                        image_path.unlink(missing_ok=True)
                    row = {
                        "case_id": item.case_id,
                        "page_no": page_index + 1,
                        "status": status,
                        "seconds": round(time.perf_counter() - started, 4),
                    }
                    if error_type:
                        row["error_type"] = error_type
                    rows.append(row)
                    print(
                        f"{item.case_id} page {page_index + 1}/{len(pdf)} "
                        f"{status} {row['seconds']}s",
                        flush=True,
                    )
    summary = {
        "backend": "PaddleOCR-VL-1.6",
        "runtime": "MLX-VLM server + PP-DocLayoutV3",
        "documents": len({row["case_id"] for row in rows}),
        "pages": len(rows),
        "ok_or_cached": sum(row["status"] in {"OK", "CACHED"} for row in rows),
        "failed": sum(row["status"] == "FAILED" for row in rows),
        "run_seconds": round(time.perf_counter() - started_all, 4),
        "measured_inference_seconds": round(
            sum(float(row["seconds"]) for row in rows if row["status"] == "OK"), 4
        ),
        "combined_process_rss_peak_bytes": memory.peak,
        "vram": "UNIFIED_MEMORY_NOT_SEPARATELY_MEASURABLE",
        "rows": rows,
    }
    durations = sorted(float(row["seconds"]) for row in rows if row["status"] == "OK")
    summary["page_latency_seconds_median"] = (
        durations[len(durations) // 2] if durations else None
    )
    summary["page_latency_seconds_p95"] = (
        durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else None
    )
    (CACHE / "run-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
