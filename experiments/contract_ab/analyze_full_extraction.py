from __future__ import annotations

import argparse
import html
import json
import re
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

import fitz

from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter
from app.services.diff.facade import DiffEngine
from app.services.evidence_locator import EvidenceLocator
from app.services.matcher import ClauseMatcher
from app.services.table_compare.facade import TableComparator
from app.services.table_compare.parser import LogicalTableParser

from run_offline_benchmark import (
    RESULTS,
    compact,
    counter_recall,
    discover_corpus,
    document_text,
    load_hybrid,
    page_categories,
    page_text,
    sequence_ratio,
)


HERE = Path(__file__).resolve().parent
MINERU_CACHE = HERE / "cache" / "mineru-full"
PADDLE_CACHE = HERE / "cache" / "paddle-full"


def plain_html(value: str) -> str:
    value = re.sub(r"</(?:td|th)>", "\t", value, flags=re.I)
    value = re.sub(r"</tr>", "\n", value, flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def estimated_char_boxes(
    text: str, bbox: BBox, page_no: int, confidence: float | None = None
) -> list[CharBox]:
    if not text or bbox.x1 <= bbox.x0 or bbox.y1 <= bbox.y0:
        return []
    lines = text.splitlines(keepends=True) or [text]
    line_height = (bbox.y1 - bbox.y0) / max(1, len(lines))
    result: list[CharBox] = []
    for line_index, line in enumerate(lines):
        visible = line.rstrip("\r\n")
        step = (bbox.x1 - bbox.x0) / max(1, len(visible))
        for index, char in enumerate(visible):
            result.append(
                CharBox(
                    char=char,
                    page_no=page_no,
                    bbox=BBox(
                        x0=bbox.x0 + index * step,
                        y0=bbox.y0 + line_index * line_height,
                        x1=bbox.x0 + (index + 1) * step,
                        y1=bbox.y0 + (line_index + 1) * line_height,
                    ),
                    text_index=None,
                    confidence=confidence,
                )
            )
        if line.endswith(("\n", "\r")):
            result.append(
                CharBox(
                    char="\n",
                    page_no=page_no,
                    bbox=BBox(
                        x0=bbox.x1,
                        y0=bbox.y0 + line_index * line_height,
                        x1=bbox.x1,
                        y1=bbox.y0 + (line_index + 1) * line_height,
                    ),
                    text_index=None,
                    confidence=confidence,
                )
            )
    return result


def mineru_block_content(block: dict[str, Any]) -> tuple[str, str]:
    texts: list[str] = []
    raw_html = ""

    def visit(value: Any) -> None:
        nonlocal raw_html
        if isinstance(value, dict):
            if isinstance(value.get("html"), str):
                raw_html = value["html"]
                texts.append(plain_html(value["html"]))
            elif isinstance(value.get("content"), str):
                texts.append(value["content"])
            for key in ("blocks", "lines", "spans"):
                visit(value.get(key))
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(block)
    return "\n".join(value for value in texts if value), raw_html


def load_mineru(case_id: str, source_pdf: Path) -> Document:
    path = next((MINERU_CACHE / case_id).glob("*/*_middle.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    pages: list[Page] = []
    for page_payload in payload.get("pdf_info") or []:
        page_no = int(page_payload.get("page_idx", len(pages))) + 1
        width, height = (float(value) for value in page_payload.get("page_size", [0, 0]))
        blocks: list[TextBlock] = []
        for index, item in enumerate(page_payload.get("para_blocks") or []):
            raw_box = item.get("bbox") or [0, 0, 0, 0]
            bbox = BBox(x0=raw_box[0], y0=raw_box[1], x1=raw_box[2], y1=raw_box[3])
            text, raw_html = mineru_block_content(item)
            block_type = str(item.get("type") or "text")
            blocks.append(
                TextBlock(
                    block_id=f"{case_id}-mineru-p{page_no}-b{index}",
                    page_no=page_no,
                    text=text,
                    bbox=bbox,
                    block_type=block_type,
                    confidence=None,
                    source="mineru_experiment",
                    reading_order=int(item.get("index", index)),
                    raw_html=raw_html,
                    table_cell_bboxes=[],
                    char_boxes=estimated_char_boxes(text, bbox, page_no),
                )
            )
        pages.append(Page(page_no=page_no, width=width, height=height, blocks=blocks))
    return Document(
        filename=source_pdf.name,
        path=str(source_pdf),
        page_count=len(pages),
        pages=pages,
    )


def paddle_confidence(payload: dict[str, Any], bbox: list[float]) -> float | None:
    best: tuple[float, float] | None = None
    for item in (payload.get("layout_det_res") or {}).get("boxes") or []:
        candidate = item.get("coordinate") or item.get("bbox") or []
        if len(candidate) != 4:
            continue
        distance = sum(abs(float(candidate[i]) - float(bbox[i])) for i in range(4))
        score = item.get("score")
        if isinstance(score, (int, float)) and (best is None or distance < best[0]):
            best = (distance, float(score))
    return best[1] if best is not None else None


def load_paddle(case_id: str, source_pdf: Path) -> Document:
    pages: list[Page] = []
    with fitz.open(source_pdf) as pdf:
        for page_index, pdf_page in enumerate(pdf):
            path = PADDLE_CACHE / case_id / f"page-{page_index + 1:04d}.json"
            raw = json.loads(path.read_text(encoding="utf-8"))
            payload = raw.get("res", raw)
            pixel_width = float(payload.get("width") or pdf_page.rect.width)
            pixel_height = float(payload.get("height") or pdf_page.rect.height)
            scale_x = float(pdf_page.rect.width) / max(1.0, pixel_width)
            scale_y = float(pdf_page.rect.height) / max(1.0, pixel_height)
            blocks: list[TextBlock] = []
            for index, item in enumerate(payload.get("parsing_res_list") or []):
                raw_box = item.get("block_bbox") or [0, 0, 0, 0]
                bbox = BBox(
                    x0=float(raw_box[0]) * scale_x,
                    y0=float(raw_box[1]) * scale_y,
                    x1=float(raw_box[2]) * scale_x,
                    y1=float(raw_box[3]) * scale_y,
                )
                content = str(item.get("block_content") or "")
                raw_html = content if "<table" in content.lower() else ""
                text = plain_html(content) if raw_html else content
                confidence = paddle_confidence(payload, raw_box)
                blocks.append(
                    TextBlock(
                        block_id=f"{case_id}-paddlevl-p{page_index + 1}-b{index}",
                        page_no=page_index + 1,
                        text=text,
                        bbox=bbox,
                        block_type=str(item.get("block_label") or "text"),
                        confidence=confidence,
                        source="paddleocr_vl_experiment",
                        reading_order=item.get("block_order", index),
                        raw_html=raw_html,
                        table_cell_bboxes=[],
                        char_boxes=estimated_char_boxes(text, bbox, page_index + 1, confidence),
                    )
                )
            pages.append(
                Page(
                    page_no=page_index + 1,
                    width=float(pdf_page.rect.width),
                    height=float(pdf_page.rect.height),
                    blocks=blocks,
                )
            )
    return Document(
        filename=source_pdf.name,
        path=str(source_pdf),
        page_count=len(pages),
        pages=pages,
    )


def block_metrics(document: Document) -> dict[str, Any]:
    blocks = [block for page in document.pages for block in page.blocks]
    parser = LogicalTableParser()
    parsed = parser.parse_tables(parser.table_blocks(document))
    labels = Counter(block.block_type for block in blocks)
    return {
        "pages": len(document.pages),
        "chars": len(compact(document_text(document))),
        "blocks": len(blocks),
        "block_type_counts": dict(labels),
        "blocks_with_reading_order": sum(block.reading_order is not None for block in blocks),
        "blocks_with_confidence": sum(block.confidence is not None for block in blocks),
        "table_blocks": sum(block.block_type in {"table", "table_body"} for block in blocks),
        "raw_html_tables": sum(bool(block.raw_html) for block in blocks),
        "parsed_tables": len(parsed),
        "cell_bboxes": sum(len(block.table_cell_bboxes) for block in blocks),
        "estimated_char_boxes": sum(
            box.text_index is None for block in blocks for box in block.char_boxes
        ),
        "indexed_char_boxes": sum(
            box.text_index is not None for block in blocks for box in block.char_boxes
        ),
        "seal_blocks": sum("seal" in block.block_type.lower() for block in blocks),
        "image_blocks": sum("image" in block.block_type.lower() for block in blocks),
        "formula_blocks": sum("formula" in block.block_type.lower() for block in blocks),
    }


def clause_pair_signature(pair: Any) -> tuple[str, str] | None:
    if pair.original is None or pair.compare is None:
        return None
    left = compact(pair.original.clause_no)
    right = compact(pair.compare.clause_no)
    if not left or not right:
        return None
    return left, right


def pair_pipeline(
    original: Document, compare: Document, prefix: str
) -> tuple[dict[str, Any], set[tuple[str, str]]]:
    splitter = ClauseSplitter()
    original_clauses = splitter.split(original, f"{prefix}-o-")
    compare_clauses = splitter.split(compare, f"{prefix}-c-")
    started = time.perf_counter()
    pairs = ClauseMatcher().match(original_clauses, compare_clauses)
    matching_seconds = time.perf_counter() - started
    diffs = DiffEngine().build_diffs(pairs)
    EvidenceLocator().locate(diffs, original_clauses, compare_clauses)
    table = TableComparator()
    table_diffs, table_warnings = table.build_diffs(original, compare)
    signatures = {value for pair in pairs if (value := clause_pair_signature(pair)) is not None}
    evidence = [box for diff in diffs for box in [*diff.original_evidence, *diff.compare_evidence]]
    return (
        {
            "original_clauses": len(original_clauses),
            "compare_clauses": len(compare_clauses),
            "matched": sum(pair.original is not None and pair.compare is not None for pair in pairs),
            "added": sum(pair.original is None for pair in pairs),
            "deleted": sum(pair.compare is None for pair in pairs),
            "clause_diffs": len(diffs),
            "clause_diff_types": dict(Counter(diff.diff_type for diff in diffs)),
            "clause_evidence_boxes": len(evidence),
            "clause_evidence_methods": dict(Counter(box.method for box in evidence)),
            "table_diffs": len(table_diffs),
            "table_warnings": len(table_warnings),
            "table_tier": table.last_debug_payload.get("tier"),
            "table_block_counts": table.last_debug_payload.get("table_block_counts"),
            "parsed_table_counts": table.last_debug_payload.get("parsed_table_counts"),
            "logical_table_counts": table.last_debug_payload.get("logical_table_counts"),
            "matching_seconds": round(matching_seconds, 4),
        },
        signatures,
    )


def backend_summary(
    name: str,
    docs: dict[str, Document],
    e0_docs: dict[str, Document],
    pairs: list[tuple[str, str, Path]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    splitter = ClauseSplitter()
    rows: list[dict[str, Any]] = []
    for case_id, document in sorted(docs.items()):
        metrics = block_metrics(document)
        baseline = e0_docs.get(case_id)
        metrics.update(
            {
                "case_id": case_id,
                "status": "OK",
                "clauses": len(splitter.split(document, f"{name}-{case_id}-")),
                "e0_character_multiset_recall": counter_recall(
                    document_text(baseline), document_text(document)
                )
                if baseline
                else None,
                "e0_sequence_ratio": sequence_ratio(
                    document_text(baseline), document_text(document)
                )
                if baseline
                else None,
            }
        )
        rows.append(metrics)
    pair_rows: list[dict[str, Any]] = []
    decisions: dict[str, set[tuple[str, str]]] = {}
    for left_id, right_id, _task in pairs:
        if left_id not in docs or right_id not in docs:
            pair_rows.append(
                {"pair_id": f"{left_id}-{right_id}", "status": "MISSING_DOCUMENT"}
            )
            continue
        try:
            metrics, signatures = pair_pipeline(
                docs[left_id], docs[right_id], f"{name}-{left_id}-{right_id}"
            )
            pair_id = f"{left_id}-{right_id}"
            decisions[pair_id] = signatures
            pair_rows.append({"pair_id": pair_id, "status": "OK", **metrics})
        except Exception as exc:
            pair_rows.append(
                {
                    "pair_id": f"{left_id}-{right_id}",
                    "status": "FAILED",
                    "error_type": type(exc).__name__,
                }
            )
    numeric_keys = [
        "chars",
        "blocks",
        "blocks_with_reading_order",
        "blocks_with_confidence",
        "clauses",
        "table_blocks",
        "raw_html_tables",
        "parsed_tables",
        "cell_bboxes",
        "estimated_char_boxes",
        "indexed_char_boxes",
        "seal_blocks",
        "image_blocks",
        "formula_blocks",
    ]
    result: dict[str, Any] = {
        "backend": name,
        "documents_ok": len(docs),
        "documents_failed": len(errors),
        "document_success_rate": round(len(docs) / max(1, len(docs) + len(errors)), 4),
        "pages_ok": sum(len(document.pages) for document in docs.values()),
        "failure_modes": errors,
        "rows": rows,
        "pair_rows": pair_rows,
        "pairs_ok": sum(row["status"] == "OK" for row in pair_rows),
        "pairs_failed": sum(row["status"] != "OK" for row in pair_rows),
        "mean_e0_character_multiset_recall": round(
            statistics.mean(
                row["e0_character_multiset_recall"]
                for row in rows
                if row["e0_character_multiset_recall"] is not None
            ),
            4,
        )
        if rows
        else None,
        "mean_e0_sequence_ratio": round(
            statistics.mean(
                row["e0_sequence_ratio"]
                for row in rows
                if row["e0_sequence_ratio"] is not None
            ),
            4,
        )
        if rows
        else None,
    }
    result.update({key: sum(row[key] for row in rows) for key in numeric_keys})
    result["block_type_counts"] = dict(
        sum((Counter(row["block_type_counts"]) for row in rows), Counter())
    )
    result["decision_signatures"] = {
        pair_id: [list(value) for value in sorted(values)] for pair_id, values in decisions.items()
    }
    category_values: dict[str, list[dict[str, Any]]] = {}
    corpus_by_id = {item.case_id: item for item in discover_corpus()[0]}
    for case_id, document in docs.items():
        baseline = e0_docs.get(case_id)
        item = corpus_by_id[case_id]
        with fitz.open(item.pdf) as source:
            for page_index, source_page in enumerate(source, start=1):
                candidate_page = document.pages[page_index - 1]
                blocks = candidate_page.blocks
                candidate_text = "\n".join(block.text for block in blocks if block.text)
                reference_text = page_text(baseline, page_index) if baseline else ""
                page_metrics = {
                    "character_multiset_recall": counter_recall(reference_text, candidate_text),
                    "sequence_ratio": sequence_ratio(reference_text, candidate_text),
                    "blocks": len(blocks),
                    "table_blocks": sum(block.block_type in {"table", "table_body"} for block in blocks),
                    "image_blocks": sum("image" in block.block_type.lower() for block in blocks),
                    "seal_blocks": sum("seal" in block.block_type.lower() for block in blocks),
                }
                for category in page_categories(source_page):
                    category_values.setdefault(category, []).append(page_metrics)
    result["page_category_metrics_nonexclusive"] = {
        category: {
            "pages": len(values),
            "mean_e0_character_multiset_recall": round(
                statistics.mean(value["character_multiset_recall"] for value in values), 4
            ),
            "mean_e0_sequence_ratio": round(
                statistics.mean(value["sequence_ratio"] for value in values), 4
            ),
            "blocks": sum(value["blocks"] for value in values),
            "table_blocks": sum(value["table_blocks"] for value in values),
            "image_blocks": sum(value["image_blocks"] for value in values),
            "seal_blocks": sum(value["seal_blocks"] for value in values),
        }
        for category, values in sorted(category_values.items())
    }
    return result


def load_all(kind: str) -> tuple[dict[str, Document], list[dict[str, str]]]:
    docs: dict[str, Document] = {}
    errors: list[dict[str, str]] = []
    for item in discover_corpus()[0]:
        try:
            if kind == "E0":
                if item.hybrid is None:
                    raise FileNotFoundError("retained hybrid output")
                document = load_hybrid(item.hybrid)
            elif kind == "MinerU":
                document = load_mineru(item.case_id, item.pdf)
            else:
                document = load_paddle(item.case_id, item.pdf)
            if len(document.pages) != item.pages:
                raise ValueError(f"page_count:{len(document.pages)}!={item.pages}")
            docs[item.case_id] = document
        except Exception as exc:
            errors.append(
                {
                    "case_id": item.case_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:200],
                }
            )
    return docs, errors


def decision_stability(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    left = {
        key: {tuple(value) for value in values}
        for key, values in baseline.get("decision_signatures", {}).items()
    }
    right = {
        key: {tuple(value) for value in values}
        for key, values in candidate.get("decision_signatures", {}).items()
    }
    intersection = sum(len(left[key] & right.get(key, set())) for key in left)
    union = sum(len(left[key] | right.get(key, set())) for key in left)
    return {
        "clause_number_pair_intersection": intersection,
        "clause_number_pair_union": union,
        "clause_number_pair_jaccard": round(intersection / union, 4) if union else None,
        "note": "Agreement with E0 by non-empty clause-number pair; not an accuracy label.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backends", nargs="+", choices=["E0", "MinerU", "PaddleOCR-VL"], required=True
    )
    args = parser.parse_args()
    _corpus, pairs = discover_corpus()
    e0_docs, e0_errors = load_all("E0")
    existing_path = RESULTS / "stage4_full_extraction.json"
    report = json.loads(existing_path.read_text(encoding="utf-8")) if existing_path.exists() else {}
    if "E0" not in report or "E0" in args.backends:
        report["E0"] = backend_summary("E0", e0_docs, e0_docs, pairs, e0_errors)
    for backend in args.backends:
        if backend == "E0":
            continue
        docs, errors = load_all(backend)
        report[backend] = backend_summary(backend, docs, e0_docs, pairs, errors)
        report[backend]["matcher_stability_vs_E0"] = decision_stability(
            report["E0"], report[backend]
        )
    report["contract_matrix"] = {
        "E0": {
            "text": "EXACT",
            "page": "EXACT",
            "bbox": "EXACT",
            "reading_order": "EXACT",
            "block_type": "EXACT",
            "confidence": "EXACT",
            "char_word_geometry": "DEGRADED",
            "table_structure": "EXACT",
            "raw_html": "EXACT",
            "cell_bbox": "EXACT",
        },
        "MinerU": {
            "text": "EXACT",
            "page": "EXACT",
            "bbox": "EXACT",
            "reading_order": "EXACT",
            "block_type": "EXACT",
            "confidence": "MISSING",
            "char_word_geometry": "MISSING",
            "table_structure": "EXACT",
            "raw_html": "EXACT",
            "cell_bbox": "MISSING",
        },
        "PaddleOCR-VL": {
            "text": "EXACT",
            "page": "ADAPTABLE",
            "bbox": "ADAPTABLE",
            "reading_order": "DEGRADED",
            "block_type": "EXACT",
            "confidence": "ADAPTABLE",
            "char_word_geometry": "MISSING",
            "table_structure": "EXACT",
            "raw_html": "EXACT",
            "cell_bbox": "MISSING",
        },
    }
    existing_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: {
                    metric: value[metric]
                    for metric in (
                        "documents_ok",
                        "documents_failed",
                        "pages_ok",
                        "chars",
                        "clauses",
                        "parsed_tables",
                        "mean_e0_character_multiset_recall",
                        "mean_e0_sequence_ratio",
                        "pairs_ok",
                        "pairs_failed",
                    )
                }
                for key, value in report.items()
                if key in {"E0", "MinerU", "PaddleOCR-VL"}
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
