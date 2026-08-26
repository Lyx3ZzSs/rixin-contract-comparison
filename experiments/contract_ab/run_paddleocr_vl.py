from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import time
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import fitz
import psutil
from paddleocr import PaddleOCRVL

from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter


ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(__file__).resolve().parent / "results"


def file_id(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest[:12]


def compact(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))


def recall(reference: str, candidate: str) -> float:
    left, right = Counter(compact(reference)), Counter(compact(candidate))
    total = sum(left.values())
    return round(sum(min(value, right.get(key, 0)) for key, value in left.items()) / total, 4) if total else 1.0


def ratio(reference: str, candidate: str) -> float:
    left, right = compact(reference), compact(candidate)
    return round(SequenceMatcher(None, left, right, autojunk=False).ratio(), 4) if left or right else 1.0


def baseline_docs() -> dict[str, Document]:
    result = {}
    for task in (ROOT / "storage" / "tasks").glob("*"):
        pdfs = list((task / "input" / "original").glob("*.pdf")) + list((task / "input" / "compare").glob("*.pdf"))
        raw = list((task / "diagnostics" / "ocr").glob("*ppstructure_ocr_hybrid_raw.json"))
        for pdf in pdfs:
            case_id = file_id(pdf)
            if case_id in result:
                continue
            best = None
            for candidate in raw:
                payload = json.loads(candidate.read_text(encoding="utf-8"))["document"]
                score = SequenceMatcher(None, compact(pdf.stem), compact(Path(payload.get("filename", "")).stem)).ratio()
                if best is None or score > best[0]:
                    best = (score, payload)
            if best:
                result[case_id] = Document.model_validate(best[1])
    return result


def server_rss(server_url: str) -> int | None:
    port = server_url.split(":")[-1].split("/")[0]
    values = []
    for process in psutil.process_iter(["cmdline", "memory_info"]):
        try:
            command = " ".join(process.info["cmdline"] or [])
            if "mlx_vlm.server" in command and port in command:
                values.append(process.info["memory_info"].rss)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    return sum(values) if values else None


def to_document(result: dict, case_id: str, page_no: int) -> Document:
    payload = result.get("res", result)
    width, height = float(payload.get("width") or 0), float(payload.get("height") or 0)
    blocks = []
    for index, item in enumerate(payload.get("parsing_res_list") or []):
        content = str(item.get("block_content") or "")
        box = item.get("block_bbox") or [0, 0, 0, 0]
        char_boxes = []
        plain = re.sub(r"<[^>]+>", "", content)
        if plain and box[2] > box[0] and box[3] > box[1]:
            step = (box[2] - box[0]) / max(1, len(plain))
            char_boxes = [CharBox(char=char, page_no=page_no, bbox=BBox(x0=box[0] + offset * step, y0=box[1], x1=box[0] + (offset + 1) * step, y1=box[3]), text_index=None) for offset, char in enumerate(plain)]
        blocks.append(TextBlock(block_id=f"p{page_no}_vl_{index}", page_no=page_no, text=plain, bbox=BBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]), block_type=str(item.get("block_label") or "text"), confidence=None, reading_order=item.get("block_order"), raw_html=content if "<table" in content.lower() else "", source="paddleocr_vl_experiment", char_boxes=char_boxes))
    return Document(filename=case_id, path="", page_count=1, pages=[Page(page_no=page_no, width=width, height=height, blocks=blocks)])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", default="http://127.0.0.1:18080/v1")
    args = parser.parse_args()
    manifest = json.loads((RESULTS / "corpus_manifest.json").read_text(encoding="utf-8"))
    by_id = {}
    for pdf in (ROOT / "storage" / "tasks").glob("*/input/*/*.pdf"):
        by_id.setdefault(file_id(pdf), pdf)
    baselines = baseline_docs()
    layout = Path.home() / ".paddlex" / "official_models" / "PP-DocLayoutV3"
    model_path = next(Path.home().glob(".cache/huggingface/hub/models--PaddlePaddle--PaddleOCR-VL-1.6/snapshots/*"))
    pipeline = PaddleOCRVL(pipeline_version="v1.6", layout_detection_model_dir=str(layout), vl_rec_backend="mlx-vlm-server", vl_rec_server_url=args.server_url, vl_rec_api_model_name=str(model_path), use_doc_orientation_classify=False, use_doc_unwarping=False, use_chart_recognition=False, use_seal_recognition=True)
    rows = []
    rss_values = [server_rss(args.server_url)]
    for sample in manifest["representative_pages"]:
        pdf_path = by_id[sample["case_id"]]
        page_no = sample["page_no"]
        with tempfile.TemporaryDirectory(prefix="contract-ab-") as directory, fitz.open(pdf_path) as pdf:
            image = Path(directory) / "page.png"
            pdf[page_no - 1].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).save(image)
            started = time.perf_counter()
            try:
                outputs = pipeline.predict(str(image), use_doc_orientation_classify=False, use_doc_unwarping=False, use_seal_recognition=True, max_new_tokens=2048)
                elapsed = time.perf_counter() - started
                raw = outputs[0].json if outputs else {"res": {}}
                doc = to_document(raw, sample["case_id"], page_no)
                predicted = "\n".join(block.text for block in doc.pages[0].blocks)
                baseline = baselines.get(sample["case_id"])
                reference = "\n".join(block.text for block in baseline.pages[page_no - 1].blocks) if baseline and page_no <= len(baseline.pages) else ""
                payload = raw.get("res", raw)
                parsing = payload.get("parsing_res_list") or []
                layout_boxes = (payload.get("layout_det_res") or {}).get("boxes") or []
                clauses = ClauseSplitter().split(doc, f"vl-{sample['case_id']}-")
                rows.append({"category": sample["category"], "case_id": sample["case_id"], "page_no": page_no, "status": "OK", "seconds": round(elapsed, 4), "e0_character_multiset_recall": recall(reference, predicted), "e0_sequence_ratio": ratio(reference, predicted), "blocks": len(parsing), "blocks_with_order": sum(item.get("block_order") is not None for item in parsing), "layout_confidence_count": sum(isinstance(item.get("score"), (int, float)) for item in layout_boxes), "table_blocks": sum(item.get("block_label") == "table" for item in parsing), "image_blocks": sum(item.get("block_label") == "image" for item in parsing), "seal_blocks": sum(item.get("block_label") == "seal" for item in parsing), "formula_blocks": sum("formula" in str(item.get("block_label")) for item in parsing), "raw_html_tables": sum("<table" in str(item.get("block_content", "")).lower() for item in parsing), "cell_bboxes": 0, "word_char_geometry": 0, "adapted_estimated_char_boxes": sum(len(block.char_boxes) for block in doc.pages[0].blocks), "clauses": len(clauses)})
            except Exception as exc:
                rows.append({"category": sample["category"], "case_id": sample["case_id"], "page_no": page_no, "status": "FAILED", "seconds": round(time.perf_counter() - started, 4), "error_type": type(exc).__name__})
            rss_values.append(server_rss(args.server_url))
    ok = [row for row in rows if row["status"] == "OK"]
    result = {"backend": "PaddleOCR-VL-1.6", "runtime": "MLX-VLM server + Paddle layout detector", "samples": len(rows), "ok": len(ok), "failure_rate": round((len(rows) - len(ok)) / len(rows), 4) if rows else None, "latency_seconds_total": round(sum(row["seconds"] for row in rows), 4), "latency_seconds_median": sorted(row["seconds"] for row in rows)[len(rows) // 2] if rows else None, "mean_e0_character_multiset_recall": round(sum(row["e0_character_multiset_recall"] for row in ok) / len(ok), 4) if ok else None, "mean_e0_sequence_ratio": round(sum(row["e0_sequence_ratio"] for row in ok) / len(ok), 4) if ok else None, "server_rss_bytes_observed_min": min(value for value in rss_values if value is not None) if any(value is not None for value in rss_values) else None, "server_rss_bytes_observed_max": max(value for value in rss_values if value is not None) if any(value is not None for value in rss_values) else None, "vram": "UNIFIED_MEMORY_NOT_SEPARATELY_MEASURABLE", "rows": rows, "contract": {"text": "EXACT", "page": "ADAPTABLE", "bbox": "EXACT", "reading_order": "EXACT", "block_type": "EXACT", "confidence": "DEGRADED", "char_word_geometry": "MISSING", "table_structure": "EXACT", "raw_html": "EXACT", "cell_bbox": "MISSING"}}
    (RESULTS / "paddleocr_vl_benchmark.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("samples", "ok", "failure_rate", "latency_seconds_total", "latency_seconds_median", "mean_e0_character_multiset_recall", "mean_e0_sequence_ratio", "server_rss_bytes_observed_min", "server_rss_bytes_observed_max")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
