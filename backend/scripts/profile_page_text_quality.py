"""Per-page native text quality profiler.

Analysis-only helper used to calibrate the OCR routing thresholds. It walks a
corpus of PDFs, computes cheap PyMuPDF-native page metrics, and reports the
distribution so thresholds come from real contract samples instead of guesses.

    .venv/bin/python backend/scripts/profile_page_text_quality.py \
        --root storage/golden-intake/contract-golden-v3 \
        --json output/analysis/ocr-routing-optimization-20260920/page_metrics.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.extractors.base import DocumentExtractionError  # noqa: E402
from app.services.extractors.pymupdf import PyMuPDFExtractor  # noqa: E402
from app.services.table_compare.constants import TABLE_HEADERS  # noqa: E402

SUSPICIOUS_CATEGORIES = {"Cc", "Cf", "Co", "Cs", "Cn"}
IMAGE_GRID = 64


def is_suspicious(char: str) -> bool:
    if char == "�":
        return True
    return unicodedata.category(char) in SUSPICIOUS_CATEGORIES


def is_valid(char: str) -> bool:
    code = ord(char)
    if 0x4E00 <= code <= 0x9FFF:  # CJK unified ideographs
        return True
    if 0x3000 <= code <= 0x303F:  # CJK punctuation
        return True
    if 0xFF00 <= code <= 0xFFEF:  # fullwidth forms
        return True
    if 0x2000 <= code <= 0x206F:  # general punctuation
        return True
    if char.isascii() and char.isprintable():
        return True
    if char.isalpha() or char.isdigit():
        return True
    return False


def image_coverage(page: fitz.Page) -> tuple[float, float, int]:
    """Return (union coverage, largest single image ratio, image count)."""
    rect = page.rect
    area = rect.width * rect.height
    infos = page.get_image_info()
    if area <= 0 or not infos:
        return 0.0, 0.0, len(infos)
    cell_w = rect.width / IMAGE_GRID
    cell_h = rect.height / IMAGE_GRID
    filled = bytearray(IMAGE_GRID * IMAGE_GRID)
    largest = 0.0
    for info in infos:
        raw = info.get("bbox")
        if not raw:
            continue
        box = fitz.Rect(raw)
        largest = max(largest, max(0.0, box.width) * max(0.0, box.height) / area)
        clipped = box & rect
        if clipped.is_empty:
            continue
        col0 = max(0, min(IMAGE_GRID - 1, int((clipped.x0 - rect.x0) / cell_w)))
        col1 = max(0, min(IMAGE_GRID - 1, int((clipped.x1 - rect.x0 - 1e-6) / cell_w)))
        row0 = max(0, min(IMAGE_GRID - 1, int((clipped.y0 - rect.y0) / cell_h)))
        row1 = max(0, min(IMAGE_GRID - 1, int((clipped.y1 - rect.y0 - 1e-6) / cell_h)))
        for row in range(row0, row1 + 1):
            base = row * IMAGE_GRID
            for col in range(col0, col1 + 1):
                filled[base + col] = 1
    return sum(filled) / (IMAGE_GRID * IMAGE_GRID), largest, len(infos)


def looks_like_table_text(text: str) -> bool:
    compact = "".join(text.split())
    return sum(header in compact for header in TABLE_HEADERS) >= 3 and len(
        [token for token in __import__("re").findall(r"\d+(?:\.\d+)?", compact)]
    ) >= 2


def profile_document(path: Path, *, with_tables: bool) -> dict:
    extractor = PyMuPDFExtractor()
    try:
        extraction = extractor.extract(path, task_id=None)
        native_pages = {page.page_no: page for page in extraction.document.pages}
        native_error = ""
    except DocumentExtractionError as exc:
        native_pages = {}
        native_error = str(exc)

    pages: list[dict] = []
    with fitz.open(path) as pdf:
        for index, page in enumerate(pdf, start=1):
            text = page.get_text("text")
            chars = [char for char in text if not char.isspace()]
            native_chars = len(chars)
            valid = sum(1 for char in chars if is_valid(char))
            suspicious = sum(1 for char in chars if is_suspicious(char))

            native_page = native_pages.get(index)
            boxed = (
                sum(
                    1
                    for block in native_page.blocks
                    for box in block.char_boxes
                    if box.char and not box.char.isspace()
                )
                if native_page
                else 0
            )
            coverage, largest, image_count = image_coverage(page)

            entry = {
                "page_no": index,
                "width": round(page.rect.width, 2),
                "height": round(page.rect.height, 2),
                "rotation": page.rotation,
                "native_chars": native_chars,
                "valid_chars": valid,
                "suspicious_chars": suspicious,
                "valid_ratio": round(valid / native_chars, 4) if native_chars else 0.0,
                "suspicious_ratio": round(suspicious / native_chars, 4) if native_chars else 0.0,
                "char_box_coverage": round(boxed / native_chars, 4) if native_chars else 0.0,
                "block_count": len(native_page.blocks) if native_page else 0,
                "image_coverage": round(coverage, 4),
                "largest_image_ratio": round(largest, 4),
                "image_count": image_count,
                "table_like_text": looks_like_table_text(text),
            }
            if with_tables:
                drawings = page.get_drawings()
                entry["drawings"] = len(drawings)
                entry["red_vector"] = any(
                    isinstance(value, (list, tuple))
                    and len(value) >= 3
                    and float(value[0]) >= 0.5
                    and float(value[0]) >= float(value[1]) * 1.5
                    and float(value[0]) >= float(value[2]) * 1.5
                    for drawing in drawings
                    for key in ("color", "fill")
                    if isinstance((value := drawing.get(key)), (list, tuple))
                )
                try:
                    entry["find_tables"] = len(page.find_tables(paths=drawings).tables)
                except Exception as exc:  # pragma: no cover - defensive
                    entry["find_tables"] = -1
                    entry["find_tables_error"] = type(exc).__name__
            pages.append(entry)

    return {
        "path": str(path),
        "name": path.name,
        "parent": path.parent.name,
        "page_count": len(pages),
        "native_error": native_error,
        "pages": pages,
    }


def classify(document: dict) -> str:
    pages = document["pages"]
    if not pages:
        return "empty"
    text_pages = sum(1 for page in pages if page["native_chars"] >= 80)
    scan_pages = sum(1 for page in pages if page["native_chars"] < 80 and page["image_coverage"] >= 0.5)
    if text_pages == len(pages):
        return "text"
    if scan_pages == len(pages):
        return "scan"
    if text_pages and scan_pages:
        return "mixed"
    return "other"


def summarize(documents: list[dict]) -> dict:
    buckets: dict[str, list[dict]] = {}
    for document in documents:
        buckets.setdefault(classify(document), []).append(document)

    summary: dict = {"documents": len(documents), "classes": {}, "char_histogram": {}}
    histogram: Counter = Counter()
    for label, docs in sorted(buckets.items()):
        pages = [page for doc in docs for page in doc["pages"]]
        summary["classes"][label] = {
            "documents": len(docs),
            "pages": len(pages),
            "median_native_chars": statistics.median([p["native_chars"] for p in pages]) if pages else 0,
            "p10_native_chars": sorted(p["native_chars"] for p in pages)[len(pages) // 10] if pages else 0,
            "median_image_coverage": round(
                statistics.median([p["image_coverage"] for p in pages]), 4
            )
            if pages
            else 0,
            "documents_list": [doc["parent"] + "/" + doc["name"] for doc in docs],
        }
    for document in documents:
        for page in document["pages"]:
            histogram[page["native_chars"] // 50 * 50] += 1
    summary["char_histogram"] = dict(sorted(histogram.items()))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--tables", action="store_true", help="also run get_drawings/find_tables")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    paths = sorted(args.root.rglob("*.pdf"))
    documents = [profile_document(path, with_tables=args.tables) for path in paths]
    summary = summarize(documents)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps({"summary": summary, "documents": documents}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if not args.quiet:
        print(f"documents={summary['documents']}")
        for label, data in summary["classes"].items():
            print(
                f"  {label:6s} docs={data['documents']:3d} pages={data['pages']:4d} "
                f"median_chars={data['median_native_chars']:8.1f} "
                f"p10_chars={data['p10_native_chars']:6d} "
                f"median_img_cov={data['median_image_coverage']:.3f}"
            )
        print("char histogram (bucket -> pages):")
        for bucket, count in summary["char_histogram"].items():
            print(f"  {bucket:6d} {count:4d} {'#' * min(60, count)}")
        print("\nper-document classification:")
        for document in documents:
            print(f"  {classify(document):6s} {document['parent']}/{document['name']} pages={document['page_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
