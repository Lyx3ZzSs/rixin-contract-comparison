from __future__ import annotations

import html
import json
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import fitz

from app.models import Document, TextBlock
from app.models_table import StructuredTable
from app.services.table_compare.matcher import TableMatcher
from app.services.table_compare.parser import LogicalTableParser

from analyze_full_extraction import load_mineru, load_paddle
from run_offline_benchmark import RESULTS, compact, discover_corpus, load_hybrid


HERE = Path(__file__).resolve().parent
PRIVATE = HERE / "review_artifacts" / "private" / "table_conflicts"


def table_text(table: StructuredTable) -> str:
    return "\n".join(" | ".join(cell.text for cell in row.cells) for row in table.rows)


def block_by_id(document: Document, block_id: str) -> TextBlock | None:
    return next(
        (
            block
            for page in document.pages
            for block in page.blocks
            if block.block_id == block_id
        ),
        None,
    )


def alternative_table(
    table: StructuredTable, document: Document | None, parser: LogicalTableParser
) -> dict[str, Any]:
    if document is None:
        return {"status": "BACKEND_DOCUMENT_MISSING"}
    alternatives = [
        value
        for value in parser.parse_tables(parser.table_blocks(document))
        if value.page_no == table.page_no
    ]
    if not alternatives:
        return {
            "status": "NO_TABLE_ON_SAME_PAGE",
            "same_page_table_count": 0,
            "cell_bbox_available": False,
        }
    reference = compact(table_text(table))
    scored = [
        (
            SequenceMatcher(None, reference, compact(table_text(value)), autojunk=False).ratio(),
            value,
        )
        for value in alternatives
    ]
    score, closest = max(scored, key=lambda value: value[0])
    return {
        "status": "CLOSEST_SAME_PAGE_TABLE",
        "same_page_table_count": len(alternatives),
        "text_sequence_ratio_to_E0": round(score, 4),
        "row_count": len(closest.rows),
        "col_count": closest.col_count,
        "html_cell_count": closest.html_cell_count,
        "bbox_cell_count": closest.bbox_cell_count,
        "cell_bbox_available": closest.bbox_cell_count > 0,
        "geometry_status": closest.geometry_status,
        "geometry_warnings": closest.geometry_warnings,
        "table_text": table_text(closest),
    }


def render_crop(case_id: str, side: str, document: Document, table: StructuredTable) -> str | None:
    block = block_by_id(document, table.source_block_id)
    if block is None or not document.path or not Path(document.path).exists():
        return None
    output = PRIVATE / f"{case_id}-{side}.png"
    with fitz.open(document.path) as pdf:
        page = pdf[table.page_no - 1]
        clip = fitz.Rect(block.bbox.x0, block.bbox.y0, block.bbox.x1, block.bbox.y1)
        clip = clip & page.rect
        if clip.is_empty:
            return None
        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), clip=clip, alpha=False).save(output)
    return output.name


def table_payload(table: StructuredTable, document: Document) -> dict[str, Any]:
    block = block_by_id(document, table.source_block_id)
    return {
        **table.model_dump(mode="json"),
        "text": table_text(table),
        "source_block": None if block is None else block.model_dump(mode="json"),
    }


def model_suggestion(original: StructuredTable | None, compare: StructuredTable | None) -> dict[str, Any]:
    tables = [value for value in (original, compare) if value is not None]
    warnings = {warning for table in tables for warning in table.geometry_warnings}
    if original is None or compare is None:
        label = "TABLE_PAIR_ERROR"
        reason = "TableMatcher left a table unmatched; semantic correspondence still requires human review."
    elif "severe_conflict" in {table.geometry_status for table in tables}:
        label = "AMBIGUOUS"
        reason = "HTML and bbox grids disagree severely; diagnostics do not establish which source is correct."
    elif any("bbox" in warning for warning in warnings):
        label = "CELL_BBOX_ERROR"
        reason = "The retained parser reports bbox/grid conflicts before row/column/cell diffing."
    else:
        label = "AMBIGUOUS"
        reason = "No deterministic extraction-layer cause can be established from diagnostics alone."
    return {"label": label, "ground_truth": False, "reason": reason}


def render_html(payload: dict[str, Any]) -> str:
    sections = []
    for case in payload["cases"]:
        columns = []
        for side in ("original", "compare"):
            table = case.get(side)
            if table is None:
                columns.append(f"<div><h3>{side}</h3><p>UNMATCHED</p></div>")
                continue
            image = case.get(f"{side}_crop")
            image_tag = f"<img src='{html.escape(image)}'>" if image else ""
            columns.append(
                f"<div><h3>{side}</h3>{image_tag}<pre>{html.escape(table['text'])}</pre>"
                f"<details><summary>raw HTML / cell bbox</summary><pre>{html.escape(json.dumps(table, ensure_ascii=False, indent=2))}</pre></details></div>"
            )
        sections.append(
            f"<section><h2>{case['case_id']}</h2>"
            f"<p>human_label=<b>{case['human_label']}</b>; before pairing/cell diff={case['before_pairing_or_cell_diff']}</p>"
            f"<div class='grid'>{''.join(columns)}</div>"
            f"<h3>MinerU same-page comparison</h3><pre>{html.escape(json.dumps(case['MinerU'], ensure_ascii=False, indent=2))}</pre>"
            f"<h3>PaddleOCR-VL same-page comparison</h3><pre>{html.escape(json.dumps(case['PaddleOCR-VL'], ensure_ascii=False, indent=2))}</pre>"
            f"<h3>model suggestion (not ground truth)</h3><pre>{html.escape(json.dumps(case['model_suggestion'], ensure_ascii=False, indent=2))}</pre></section>"
        )
    return """<!doctype html><meta charset="utf-8"><title>Table conflict review</title>
<style>body{font:14px/1.5 system-ui;margin:24px;background:#f5f5f5}section{background:white;padding:20px;margin:0 0 24px;border-radius:10px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}img{max-width:100%;border:1px solid #ccc}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#fafafa;padding:10px}</style>""" + "".join(sections)


def main() -> None:
    corpus, pairs = discover_corpus()
    e0 = {item.case_id: load_hybrid(item.hybrid) for item in corpus if item.hybrid}
    mineru: dict[str, Document] = {}
    paddle: dict[str, Document] = {}
    for item in corpus:
        try:
            mineru[item.case_id] = load_mineru(item.case_id, item.pdf)
        except Exception:
            pass
        try:
            paddle[item.case_id] = load_paddle(item.case_id, item.pdf)
        except Exception:
            pass
    parser = LogicalTableParser()
    matcher = TableMatcher()
    cases: list[dict[str, Any]] = []
    PRIVATE.mkdir(parents=True, exist_ok=True)

    def add_case(
        pair_id: str,
        left_id: str,
        right_id: str,
        original: StructuredTable | None,
        compare: StructuredTable | None,
        pair_score: float | None,
    ) -> None:
        case_number = len(cases) + 1
        case_id = f"TC-{case_number:03d}"
        suggestion = model_suggestion(original, compare)
        warnings = [
            warning
            for table in (original, compare)
            if table is not None
            for warning in table.geometry_warnings
        ]
        before = bool(warnings)
        case: dict[str, Any] = {
            "case_id": case_id,
            "pair_id": pair_id,
            "pair_score": pair_score,
            "original": None if original is None else table_payload(original, e0[left_id]),
            "compare": None if compare is None else table_payload(compare, e0[right_id]),
            "geometry_warnings": warnings,
            "before_pairing_or_cell_diff": before,
            "human_label": "UNLABELED",
            "model_suggestion": suggestion,
            "MinerU": {
                "original": None
                if original is None
                else alternative_table(original, mineru.get(left_id), parser),
                "compare": None
                if compare is None
                else alternative_table(compare, mineru.get(right_id), parser),
                "improves_E0_conflict": "UNKNOWN",
            },
            "PaddleOCR-VL": {
                "original": None
                if original is None
                else alternative_table(original, paddle.get(left_id), parser),
                "compare": None
                if compare is None
                else alternative_table(compare, paddle.get(right_id), parser),
                "improves_E0_conflict": "UNKNOWN",
            },
        }
        if original is not None:
            case["original_crop"] = render_crop(case_id, "original", e0[left_id], original)
        if compare is not None:
            case["compare_crop"] = render_crop(case_id, "compare", e0[right_id], compare)
        cases.append(case)

    for left_id, right_id, _task in pairs:
        original_tables = parser.parse_tables(parser.table_blocks(e0[left_id]))
        compare_tables = parser.parse_tables(parser.table_blocks(e0[right_id]))
        matched = matcher.match_tables(original_tables, compare_tables)
        matched_original = {value[0] for value in matched}
        matched_compare = {value[1] for value in matched}
        pair_id = f"{left_id}-{right_id}"
        for original_index, compare_index, score in matched:
            original, compare = original_tables[original_index], compare_tables[compare_index]
            if not original.geometry_warnings and not compare.geometry_warnings:
                continue
            add_case(pair_id, left_id, right_id, original, compare, score)
        for index, table in enumerate(original_tables):
            if index not in matched_original:
                add_case(pair_id, left_id, right_id, table, None, None)
        for index, table in enumerate(compare_tables):
            if index not in matched_compare:
                add_case(pair_id, left_id, right_id, None, table, None)
    payload = {
        "case_count": len(cases),
        "human_labeled_count": 0,
        "cases": cases,
    }
    (PRIVATE / "table_conflicts.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (PRIVATE / "table_conflicts.html").write_text(render_html(payload), encoding="utf-8")
    suggestion_counts = Counter(value["model_suggestion"]["label"] for value in cases)
    backend_presence = {}
    for backend in ("MinerU", "PaddleOCR-VL"):
        expected = 0
        found = 0
        cell_bbox = 0
        for case in cases:
            for side in ("original", "compare"):
                if case[side] is None:
                    continue
                expected += 1
                alt = case[backend][side]
                found += alt.get("status") == "CLOSEST_SAME_PAGE_TABLE"
                cell_bbox += bool(alt.get("cell_bbox_available"))
        backend_presence[backend] = {
            "expected_conflict_table_sides": expected,
            "same_page_table_found": found,
            "cell_bbox_available": cell_bbox,
            "improved_cases": None,
        }
    public = {
        "case_count": len(cases),
        "matched_pairs_with_extraction_geometry_conflict": sum(
            value["original"] is not None
            and value["compare"] is not None
            and value["before_pairing_or_cell_diff"]
            for value in cases
        ),
        "unmatched_table_cases": sum(
            value["original"] is None or value["compare"] is None for value in cases
        ),
        "errors_before_pairing_or_cell_diff": sum(
            value["before_pairing_or_cell_diff"] for value in cases
        ),
        "raw_extraction_input_conflict_cases": sum(
            value["before_pairing_or_cell_diff"] for value in cases
        ),
        "raw_extraction_note": "The HTML/cell-bbox inconsistency is carried by TextBlock input; TableComparator diagnoses it while parsing, before table pairing and cell diff.",
        "human_labeled_count": 0,
        "unlabeled_count": len(cases),
        "human_root_cause_taxonomy": {},
        "model_suggestion_counts_not_ground_truth": dict(suggestion_counts),
        "html_vs_cell_bbox_more_often_wrong": "UNKNOWN",
        "not_fixable_by_column_matcher_lower_bound": len(cases),
        "not_fixable_note": "All included cases are extraction-geometry conflicts or unmatched table pairs; column assignment cannot repair either layer.",
        "backend_same_problem_tables": backend_presence,
        "review_artifact": str(PRIVATE / "table_conflicts.html"),
    }
    (RESULTS / "table_conflict_review.json").write_text(
        json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(public, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
