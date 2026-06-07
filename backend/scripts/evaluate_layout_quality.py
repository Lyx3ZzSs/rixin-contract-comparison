from __future__ import annotations

import argparse
import base64
import gzip
import html
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.models import Document
from app.services.clause_splitter import ClauseSplitter
from app.services.extractors.ppocrv5 import PPOCRV5Extractor
from app.services.extractors.ppstructure import PPStructureExtractor
from app.services.extractors.ppstructure_ocr_hybrid import PPStructureOCRHybridExtractor


DEFAULT_THRESHOLDS = {
    "region_precision": 0.98,
    "region_recall": 0.98,
    "bbox_hit_rate": 0.995,
    "reading_order_pair_accuracy": 0.99,
    "valid_ocr_match_recall": 0.995,
    "noise_precision": 0.98,
    "table_cell_accuracy": 1.0,
    "non_text_recall": 1.0,
    "clause_order_accuracy": 1.0,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate layout analysis golden cases.")
    parser.add_argument("case_root", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--html-output", type=Path, default=None)
    parser.add_argument("--fail-on-threshold", action="store_true")
    args = parser.parse_args()
    report = evaluate_case_root(args.case_root)
    content = json.dumps(_public_report(report), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content)
    if args.html_output:
        write_html_report(args.html_output, report)
    if args.fail_on_threshold and report["threshold_failures"]:
        return 1
    return 0


def evaluate_case_root(case_root: Path) -> dict[str, Any]:
    cases = [
        path
        for path in sorted(case_root.iterdir())
        if path.is_dir() and (path / "expected.json").exists() and _has_actual_source(path)
    ]
    results = [evaluate_case(case) for case in cases]
    totals = {
        "expected_regions": sum(item["expected_regions"] for item in results),
        "actual_regions": sum(item["actual_regions"] for item in results),
        "matched_regions": sum(item["matched_regions"] for item in results),
        "bbox_hits": sum(item["bbox_hits"] for item in results),
        "reading_order_hits": sum(item["reading_order_hits"] for item in results),
        "reading_order_compared": sum(item["reading_order_compared"] for item in results),
        "reading_order_pair_hits": sum(item["reading_order_pair_hits"] for item in results),
        "reading_order_pair_compared": sum(item["reading_order_pair_compared"] for item in results),
        "invalid_bbox_count": sum(item["invalid_bbox_count"] for item in results),
        "valid_ocr_match_expected": sum(item["valid_ocr_match_expected"] for item in results),
        "valid_ocr_match_hits": sum(item["valid_ocr_match_hits"] for item in results),
        "noise_actual": sum(item["noise_actual"] for item in results),
        "noise_hits": sum(item["noise_hits"] for item in results),
        "table_cell_expected": sum(item["table_cell_expected"] for item in results),
        "table_cell_hits": sum(item["table_cell_hits"] for item in results),
        "non_text_expected": sum(item["non_text_expected"] for item in results),
        "non_text_hits": sum(item["non_text_hits"] for item in results),
        "clause_order_expected": sum(item["clause_order_expected"] for item in results),
        "clause_order_hits": sum(item["clause_order_hits"] for item in results),
    }
    totals.update(_rates(totals))
    totals["label_macro_f1"] = _macro_label_f1(results)
    failures = [
        f"{metric}={totals.get(metric, 0):.4f} < {threshold:.4f}"
        for metric, threshold in DEFAULT_THRESHOLDS.items()
        if totals.get(metric, 0) < threshold
    ]
    failures.extend(
        f"{item['case_id']}: minimum_page_reading_order_pair_accuracy="
        f"{item['minimum_page_reading_order_pair_accuracy']:.4f} < 0.9700"
        for item in results
        if item["minimum_page_reading_order_pair_accuracy"] < 0.97
    )
    return {
        "case_root": str(case_root),
        "case_count": len(results),
        "thresholds": DEFAULT_THRESHOLDS,
        "threshold_failures": failures,
        "aggregate": totals,
        "cases": results,
    }


def evaluate_case(case_dir: Path) -> dict[str, Any]:
    expected_payload = _read_json(case_dir / "expected.json")
    actual_payload = _actual_payload(case_dir)
    expected = _blocks(expected_payload)
    actual = _blocks(actual_payload)
    matches = _match_blocks(expected, actual)
    reading_order_hits = sum(
        expected[left].get("reading_order") == actual[right].get("reading_order")
        for left, right in matches
        if expected[left].get("reading_order") is not None
    )
    reading_order_compared = sum(expected[left].get("reading_order") is not None for left, _ in matches)
    pair_hits, pair_compared = _reading_order_pairs(expected, actual, matches)
    page_pair_accuracies = _page_reading_order_pair_accuracies(expected, actual, matches)
    label_counts = _label_counts(expected, actual, matches)
    match_metrics = _specialized_match_metrics(expected, actual, matches)
    clause_hits, clause_expected = _clause_order_metrics(case_dir, actual_payload)
    result = {
        "case_id": case_dir.name,
        "expected_regions": len(expected),
        "actual_regions": len(actual),
        "matched_regions": len(matches),
        "bbox_hits": len(matches),
        "reading_order_hits": reading_order_hits,
        "reading_order_compared": reading_order_compared,
        "reading_order_pair_hits": pair_hits,
        "reading_order_pair_compared": pair_compared,
        "invalid_bbox_count": sum(not _valid_bbox(block.get("bbox")) for block in actual),
        "minimum_page_reading_order_pair_accuracy": min(page_pair_accuracies, default=1.0),
        "label_counts": label_counts,
        "issues": _case_issues(expected, actual, matches),
        "_expected": expected_payload,
        "_actual": actual_payload,
        "_pdf_path": str(case_dir / "source.pdf") if (case_dir / "source.pdf").exists() else "",
        **match_metrics,
        "clause_order_hits": clause_hits,
        "clause_order_expected": clause_expected,
    }
    result.update(_rates(result))
    return result


def write_html_report(path: Path, report: dict[str, Any]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in report["cases"]:
        case_path = path / f"{case['case_id']}.html"
        case_path.write_text(_case_html(case), encoding="utf-8")
        rows.append(
            f"<tr><td><a href='{html.escape(case_path.name)}'>{html.escape(case['case_id'])}</a></td>"
            f"<td>{case['region_precision']:.2%}</td><td>{case['region_recall']:.2%}</td>"
            f"<td>{case['reading_order_pair_accuracy']:.2%}</td><td>{len(case['issues'])}</td></tr>"
        )
    failures = "<br>".join(html.escape(item) for item in report["threshold_failures"]) or "None"
    index = (
        "<!doctype html><meta charset='utf-8'><title>Layout quality</title>"
        "<style>body{font-family:Arial;margin:24px}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:6px}</style>"
        f"<h1>Layout quality report</h1><p>Threshold failures: {failures}</p>"
        "<table><tr><th>Case</th><th>Precision</th><th>Recall</th><th>Reading order</th><th>Issues</th></tr>"
        + "".join(rows)
        + "</table>"
    )
    (path / "index.html").write_text(index, encoding="utf-8")


def _actual_payload(case_dir: Path) -> dict[str, Any]:
    if (case_dir / "actual.json").exists():
        return _read_json(case_dir / "actual.json")
    manifest = _read_json(case_dir / "case.json")
    pdf_path = case_dir / manifest.get("pdf", "source.pdf")
    structure_payload = _read_json_any(case_dir / manifest["ppstructure"])
    ocr_payload = _read_json_any(case_dir / manifest["ppocrv5"])
    app_settings = Settings(layout_analysis_mode=manifest.get("mode", "v3"))
    structure = PPStructureExtractor(app_settings=app_settings)
    ocr = PPOCRV5Extractor(app_settings=app_settings)
    structure_document = structure.payload_to_document(structure_payload, pdf_path)
    ocr_document = ocr.payload_to_document(ocr_payload, pdf_path)
    hybrid = PPStructureOCRHybridExtractor(
        structure_extractor=structure,
        ocr_extractor=ocr,
        app_settings=app_settings,
    )
    document = hybrid._merge_documents(ocr_document, structure_document)
    return document.model_dump(mode="json")


def _has_actual_source(case_dir: Path) -> bool:
    return (case_dir / "actual.json").exists() or (case_dir / "case.json").exists()


def _blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"page_no": page.get("page_no"), **block}
        for page in payload.get("pages", [])
        for block in page.get("blocks", [])
    ]


def _match_blocks(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> list[tuple[int, int]]:
    candidates: list[tuple[float, int, int]] = []
    for left_index, left in enumerate(expected):
        for right_index, right in enumerate(actual):
            if left.get("page_no") != right.get("page_no") or _label(left) != _label(right):
                continue
            overlap = _iou(left.get("bbox"), right.get("bbox"))
            if overlap >= 0.5:
                candidates.append((overlap, left_index, right_index))
    matches: list[tuple[int, int]] = []
    used_left: set[int] = set()
    used_right: set[int] = set()
    for _, left_index, right_index in sorted(candidates, key=lambda item: (-item[0], item[1], item[2])):
        if left_index in used_left or right_index in used_right:
            continue
        matches.append((left_index, right_index))
        used_left.add(left_index)
        used_right.add(right_index)
    return matches


def _reading_order_pairs(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    matches: list[tuple[int, int]],
) -> tuple[int, int]:
    by_page: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for left, right in matches:
        if expected[left].get("reading_order") is None or actual[right].get("reading_order") is None:
            continue
        by_page.setdefault(int(expected[left].get("page_no") or 0), []).append((expected[left], actual[right]))
    hits = compared = 0
    for pairs in by_page.values():
        for index, (left_expected, left_actual) in enumerate(pairs):
            for right_expected, right_actual in pairs[index + 1 :]:
                expected_before = left_expected["reading_order"] < right_expected["reading_order"]
                actual_before = left_actual["reading_order"] < right_actual["reading_order"]
                hits += expected_before == actual_before
                compared += 1
    return hits, compared


def _page_reading_order_pair_accuracies(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    matches: list[tuple[int, int]],
) -> list[float]:
    pages = sorted({int(expected[left].get("page_no") or 0) for left, _ in matches})
    results = []
    for page_no in pages:
        page_matches = [pair for pair in matches if int(expected[pair[0]].get("page_no") or 0) == page_no]
        hits, compared = _reading_order_pairs(expected, actual, page_matches)
        results.append(_divide(hits, compared))
    return results


def _specialized_match_metrics(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    matches: list[tuple[int, int]],
) -> dict[str, int]:
    matched_by_left = {left: right for left, right in matches}
    valid_statuses = {"matched", "ambiguous"}
    valid_expected = [index for index, block in enumerate(expected) if block.get("layout_match_status") in valid_statuses]
    noise_actual = [index for index, block in enumerate(actual) if block.get("layout_match_status") == "noise_unmatched"]
    matched_by_right = {right: left for left, right in matches}
    table_expected = [index for index, block in enumerate(expected) if block.get("table_cell_bboxes")]
    non_text_expected = [index for index, block in enumerate(expected) if block.get("flow_role") == "non_text"]
    return {
        "valid_ocr_match_expected": len(valid_expected),
        "valid_ocr_match_hits": sum(
            expected_index in matched_by_left
            and actual[matched_by_left[expected_index]].get("layout_match_status") in valid_statuses
            for expected_index in valid_expected
        ),
        "noise_actual": len(noise_actual),
        "noise_hits": sum(
            actual_index in matched_by_right
            and expected[matched_by_right[actual_index]].get("layout_match_status") == "noise_unmatched"
            for actual_index in noise_actual
        ),
        "table_cell_expected": len(table_expected),
        "table_cell_hits": sum(
            expected_index in matched_by_left
            and expected[expected_index].get("table_cell_bboxes")
            == actual[matched_by_left[expected_index]].get("table_cell_bboxes")
            for expected_index in table_expected
        ),
        "non_text_expected": len(non_text_expected),
        "non_text_hits": sum(expected_index in matched_by_left for expected_index in non_text_expected),
    }


def _clause_order_metrics(case_dir: Path, actual_payload: dict[str, Any]) -> tuple[int, int]:
    expected_path = case_dir / "expected_clauses.json"
    if not expected_path.exists():
        return 0, 0
    expected = _read_json_any(expected_path)
    clauses = ClauseSplitter().split(Document.model_validate(actual_payload), "L")
    actual = [_clause_order_summary(clause) for clause in clauses]
    hits = sum(left == right for left, right in zip(expected, actual, strict=False))
    return hits, len(expected)


def _clause_order_summary(clause: Any) -> dict[str, str]:
    return {
        "clause_no": clause.clause_no,
        "title": clause.title,
        "normalized_text": clause.match_text or clause.normalized_text,
    }


def _label_counts(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    matches: list[tuple[int, int]],
) -> dict[str, dict[str, int]]:
    expected_counts = Counter(_label(block) for block in expected)
    actual_counts = Counter(_label(block) for block in actual)
    matched_counts = Counter(_label(expected[left]) for left, _ in matches)
    labels = sorted(expected_counts.keys() | actual_counts.keys())
    return {
        label: {
            "expected": expected_counts[label],
            "actual": actual_counts[label],
            "matched": matched_counts[label],
        }
        for label in labels
    }


def _macro_label_f1(results: list[dict[str, Any]]) -> float:
    totals: dict[str, Counter[str]] = {}
    for result in results:
        for label, counts in result["label_counts"].items():
            totals.setdefault(label, Counter()).update(counts)
    scores = []
    for counts in totals.values():
        precision = _divide(counts["matched"], counts["actual"])
        recall = _divide(counts["matched"], counts["expected"])
        scores.append(_divide(2 * precision * recall, precision + recall))
    return round(sum(scores) / len(scores), 4) if scores else 0.0


def _case_issues(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    matches: list[tuple[int, int]],
) -> list[str]:
    matched_left = {left for left, _ in matches}
    matched_right = {right for _, right in matches}
    issues = [f"missing expected block {index}" for index in range(len(expected)) if index not in matched_left]
    issues.extend(f"unexpected actual block {index}" for index in range(len(actual)) if index not in matched_right)
    return issues


def _case_html(case: dict[str, Any]) -> str:
    expected_pages = {page["page_no"]: page for page in case["_expected"].get("pages", [])}
    actual_pages = {page["page_no"]: page for page in case["_actual"].get("pages", [])}
    pdf_images = _pdf_images(Path(case["_pdf_path"])) if case["_pdf_path"] else {}
    page_sections = []
    for page_no in sorted(expected_pages.keys() | actual_pages.keys()):
        expected = expected_pages.get(page_no, {}).get("blocks", [])
        actual = actual_pages.get(page_no, {}).get("blocks", [])
        width, height = _page_dimensions(expected, actual)
        background = (
            f"<image href='data:image/png;base64,{pdf_images[page_no]}' width='{width}' height='{height}'/>"
            if page_no in pdf_images
            else ""
        )
        page_sections.append(
            f"<h2>Page {page_no}</h2><svg viewBox='0 0 {width} {height}'>{background}"
            + "".join(_svg_box(block, "expected") for block in expected)
            + "".join(_svg_box(block, "actual") for block in actual)
            + "</svg>"
        )
    issues = "".join(f"<li>{html.escape(issue)}</li>" for issue in case["issues"]) or "<li>None</li>"
    return (
        "<!doctype html><meta charset='utf-8'><title>Layout case</title>"
        "<style>body{font-family:Arial;margin:20px}svg{width:min(900px,100%);border:1px solid #aaa}"
        ".expected{fill:none;stroke:#1b8f3a;stroke-width:2}.actual{fill:none;stroke:#d33;stroke-width:1}"
        "text{font-size:11px;paint-order:stroke;stroke:white;stroke-width:3px}</style>"
        f"<h1>{html.escape(case['case_id'])}</h1><ul>{issues}</ul>{''.join(page_sections)}"
    )


def _svg_box(block: dict[str, Any], css_class: str) -> str:
    bbox = block.get("bbox") or {}
    if not _valid_bbox(bbox):
        return ""
    label = html.escape(f"{_label(block)} #{block.get('reading_order', '-')}")
    return (
        f"<rect class='{css_class}' x='{bbox['x0']}' y='{bbox['y0']}' "
        f"width='{bbox['x1'] - bbox['x0']}' height='{bbox['y1'] - bbox['y0']}'/>"
        f"<text x='{bbox['x0']}' y='{bbox['y0'] + 11}'>{label}</text>"
    )


def _pdf_images(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    pdf = fitz.open(path)
    try:
        return {
            index + 1: base64.b64encode(page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False).tobytes("png")).decode()
            for index, page in enumerate(pdf)
        }
    finally:
        pdf.close()


def _page_dimensions(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> tuple[float, float]:
    blocks = [*expected, *actual]
    return (
        max(((block.get("bbox") or {}).get("x1", 595) for block in blocks), default=595),
        max(((block.get("bbox") or {}).get("y1", 842) for block in blocks), default=842),
    )


def _label(block: dict[str, Any]) -> str:
    return str(block.get("flow_role") or block.get("block_role") or block.get("block_type") or "")


def _iou(left: Any, right: Any) -> float:
    if not _valid_bbox(left) or not _valid_bbox(right):
        return 0.0
    intersection = max(0.0, min(left["x1"], right["x1"]) - max(left["x0"], right["x0"])) * max(
        0.0, min(left["y1"], right["y1"]) - max(left["y0"], right["y0"])
    )
    union = _area(left) + _area(right) - intersection
    return intersection / union if union > 0 else 0.0


def _valid_bbox(bbox: Any) -> bool:
    return isinstance(bbox, dict) and bbox.get("x1", 0) > bbox.get("x0", 0) and bbox.get("y1", 0) > bbox.get("y0", 0)


def _area(bbox: dict[str, Any]) -> float:
    return (bbox["x1"] - bbox["x0"]) * (bbox["y1"] - bbox["y0"])


def _rates(result: dict[str, Any]) -> dict[str, float]:
    return {
        "region_precision": _divide(result["matched_regions"], result["actual_regions"]),
        "region_recall": _divide(result["matched_regions"], result["expected_regions"]),
        "bbox_hit_rate": _divide(result["bbox_hits"], result["expected_regions"]),
        "reading_order_accuracy": _divide(result["reading_order_hits"], result["reading_order_compared"]),
        "reading_order_pair_accuracy": _divide(
            result["reading_order_pair_hits"],
            result["reading_order_pair_compared"],
        ),
        "valid_ocr_match_recall": _divide(result["valid_ocr_match_hits"], result["valid_ocr_match_expected"]),
        "noise_precision": _divide(result["noise_hits"], result["noise_actual"]),
        "table_cell_accuracy": _divide(result["table_cell_hits"], result["table_cell_expected"]),
        "non_text_recall": _divide(result["non_text_hits"], result["non_text_expected"]),
        "clause_order_accuracy": _divide(result["clause_order_hits"], result["clause_order_expected"]),
    }


def _divide(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_json_any(path: Path) -> Any:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    return _read_json(path)


def _public_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (
            [
                {item_key: item_value for item_key, item_value in item.items() if not item_key.startswith("_")}
                for item in value
            ]
            if key == "cases"
            else value
        )
        for key, value in report.items()
    }


if __name__ == "__main__":
    raise SystemExit(main())
