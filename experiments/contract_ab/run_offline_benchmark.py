from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import fitz
from scipy.optimize import linear_sum_assignment

from app.models import Document
from app.services.clause_splitter import ClauseSplitter
from app.services.extractors.pymupdf import PyMuPDFExtractor
from app.services.matcher import ClauseMatcher
from app.services.table_compare.matcher import TableMatcher
from app.services.table_compare.parser import LogicalTableParser
from app.services.table_compare import utils as table_utils
from app.services.text_coordinate_locator import TextCoordinateLocator


ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "storage" / "tasks"
RESULTS = Path(__file__).resolve().parent / "results"
RESULTS.mkdir(exist_ok=True)


def file_id(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


def compact(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))


def document_text(document: Document) -> str:
    return "\n".join(block.text for page in document.pages for block in page.blocks if block.text)


def page_text(document: Document, page_no: int) -> str:
    if page_no < 1 or page_no > len(document.pages):
        return ""
    return "\n".join(block.text for block in document.pages[page_no - 1].blocks if block.text)


def counter_recall(reference: str, candidate: str) -> float:
    left = Counter(compact(reference))
    right = Counter(compact(candidate))
    total = sum(left.values())
    return round(sum(min(count, right.get(char, 0)) for char, count in left.items()) / total, 4) if total else 1.0


def sequence_ratio(reference: str, candidate: str) -> float:
    left, right = compact(reference), compact(candidate)
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return round(SequenceMatcher(None, left, right, autojunk=False).ratio(), 4)


def load_hybrid(path: Path) -> Document:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return Document.model_validate(payload["document"])


def pick_hybrid(task: Path, pdf: Path) -> Path | None:
    candidates = list((task / "diagnostics" / "ocr").glob("*ppstructure_ocr_hybrid_raw.json"))
    if not candidates:
        return None
    target = compact(pdf.stem)
    scored: list[tuple[float, Path]] = []
    for candidate in candidates:
        try:
            filename = load_hybrid(candidate).filename
        except Exception:
            filename = candidate.stem.replace("_ppstructure_ocr_hybrid_raw", "")
        scored.append((SequenceMatcher(None, target, compact(Path(filename).stem)).ratio(), candidate))
    return max(scored, key=lambda item: item[0])[1]


@dataclass
class CorpusDocument:
    case_id: str
    pdf: Path
    hybrid: Path | None
    pages: int
    categories: set[str]


def page_categories(page: fitz.Page) -> set[str]:
    text = compact(page.get_text("text"))
    native = len(text) >= 30
    images = bool(page.get_images(full=True))
    rotation = int(page.rotation or 0) % 360 != 0
    signing = bool(re.search(r"盖章|签字|签章|法定代表人|授权代表", text))
    drawings = page.get_drawings()
    horizontal = sum(1 for drawing in drawings for item in drawing.get("items", []) if item and item[0] == "l" and abs(item[1].y - item[2].y) <= 2)
    vertical = sum(1 for drawing in drawings for item in drawing.get("items", []) if item and item[0] == "l" and abs(item[1].x - item[2].x) <= 2)
    vector_table = native and horizontal >= 3 and vertical >= 2
    result: set[str] = set()
    if native and not images and not vector_table and not rotation and not signing:
        result.add("pure_native")
    if native and images:
        result.add("native_image")
    if vector_table:
        result.add("native_table")
    if native and signing:
        result.add("native_seal_signature")
    if rotation:
        result.add("rotated")
    if not native:
        result.add("scanned")
    return result


def discover_corpus() -> tuple[list[CorpusDocument], list[tuple[str, str, Path]]]:
    by_hash: dict[str, CorpusDocument] = {}
    pair_seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str, Path]] = []
    for task in sorted(TASKS.glob("*")):
        side_paths: dict[str, Path] = {}
        for side in ("original", "compare"):
            pdfs = sorted((task / "input" / side).glob("*.pdf"))
            if not pdfs:
                continue
            pdf = pdfs[0]
            case_id = file_id(pdf)
            side_paths[side] = pdf
            if case_id not in by_hash:
                categories: set[str] = set()
                with fitz.open(pdf) as opened:
                    for page in opened:
                        categories.update(page_categories(page))
                    page_count = len(opened)
                by_hash[case_id] = CorpusDocument(case_id, pdf, pick_hybrid(task, pdf), page_count, categories)
            elif by_hash[case_id].hybrid is None:
                by_hash[case_id].hybrid = pick_hybrid(task, pdf)
        if set(side_paths) == {"original", "compare"}:
            key = (file_id(side_paths["original"]), file_id(side_paths["compare"]))
            if key not in pair_seen:
                pair_seen.add(key)
                pairs.append((key[0], key[1], task))
    return list(by_hash.values()), pairs


def representative_pages(corpus: list[CorpusDocument]) -> list[dict[str, Any]]:
    wanted = ["pure_native", "native_image", "native_table", "native_seal_signature", "rotated", "scanned"]
    result: list[dict[str, Any]] = []
    for category in wanted:
        found = None
        for item in corpus:
            with fitz.open(item.pdf) as pdf:
                for index, page in enumerate(pdf, start=1):
                    if category in page_categories(page):
                        found = {"category": category, "case_id": item.case_id, "page_no": index}
                        break
            if found:
                break
        if found:
            result.append(found)
    return result


def corpus_manifest(corpus: list[CorpusDocument], pairs: list[tuple[str, str, Path]]) -> dict[str, Any]:
    page_counts = Counter()
    total_pages = 0
    for item in corpus:
        total_pages += item.pages
        with fitz.open(item.pdf) as pdf:
            for page in pdf:
                for category in page_categories(page):
                    page_counts[category] += 1
    return {
        "documents": len(corpus),
        "comparison_pairs": len(pairs),
        "pages": total_pages,
        "documents_with_e0_retained_output": sum(item.hybrid is not None for item in corpus),
        "page_category_counts_nonexclusive": dict(sorted(page_counts.items())),
        "cases": [
            {"case_id": item.case_id, "pages": item.pages, "categories": sorted(item.categories), "has_e0": item.hybrid is not None}
            for item in sorted(corpus, key=lambda value: value.case_id)
        ],
        "representative_pages": representative_pages(corpus),
        "human_labels": False,
    }


def extraction_benchmark(corpus: list[CorpusDocument]) -> tuple[dict[str, Any], dict[str, Document], dict[str, Document]]:
    e0_docs: dict[str, Document] = {}
    e1_docs: dict[str, Document] = {}
    e0_rows: list[dict[str, Any]] = []
    e1_rows: list[dict[str, Any]] = []
    splitter = ClauseSplitter()
    extractor = PyMuPDFExtractor()
    for item in corpus:
        if item.hybrid:
            started = time.perf_counter()
            e0 = load_hybrid(item.hybrid)
            load_seconds = time.perf_counter() - started
            e0_docs[item.case_id] = e0
            clauses = splitter.split(e0, f"e0-{item.case_id}-")
            blocks = [block for page in e0.pages for block in page.blocks]
            e0_rows.append(
                {
                    "case_id": item.case_id,
                    "status": "RETAINED_OUTPUT",
                    "load_seconds": round(load_seconds, 4),
                    "chars": len(compact(document_text(e0))),
                    "blocks": len(blocks),
                    "clauses": len(clauses),
                    "tables": sum(block.block_type == "table" or bool(block.raw_html) for block in blocks),
                    "char_boxes": sum(len(block.char_boxes) for block in blocks),
                    "cell_bboxes": sum(len(block.table_cell_bboxes) for block in blocks),
                }
            )
        started = time.perf_counter()
        try:
            e1 = extractor.extract(item.pdf).document
        except Exception as exc:
            e1_rows.append({"case_id": item.case_id, "status": "FAILED", "seconds": round(time.perf_counter() - started, 4), "error_type": type(exc).__name__})
            continue
        elapsed = time.perf_counter() - started
        e1_docs[item.case_id] = e1
        clauses = splitter.split(e1, f"e1-{item.case_id}-")
        blocks = [block for page in e1.pages for block in page.blocks]
        e0 = e0_docs.get(item.case_id)
        e1_rows.append(
            {
                "case_id": item.case_id,
                "status": "OK",
                "seconds": round(elapsed, 4),
                "chars": len(compact(document_text(e1))),
                "blocks": len(blocks),
                "clauses": len(clauses),
                "char_boxes": sum(len(block.char_boxes) for block in blocks),
                "e0_character_multiset_recall": counter_recall(document_text(e0), document_text(e1)) if e0 else None,
                "e0_sequence_ratio": sequence_ratio(document_text(e0), document_text(e1)) if e0 else None,
            }
        )
    ok = [row for row in e1_rows if row["status"] == "OK"]
    return (
        {
            "E0": {
                "status": "RETAINED_RUNTIME_OUTPUT",
                "documents": len(e0_rows),
                "rows": e0_rows,
                "latency_note": "JSON load time is not OCR latency; retained request timing is reported separately.",
            },
            "E1": {
                "status": "RUNTIME",
                "documents_ok": len(ok),
                "documents_failed": len(e1_rows) - len(ok),
                "failure_rate": round((len(e1_rows) - len(ok)) / len(e1_rows), 4) if e1_rows else 0,
                "latency_seconds_total": round(sum(row["seconds"] for row in e1_rows), 4),
                "latency_seconds_median": round(sorted(row["seconds"] for row in e1_rows)[len(e1_rows) // 2], 4) if e1_rows else None,
                "mean_e0_character_multiset_recall": round(sum(row["e0_character_multiset_recall"] for row in ok if row["e0_character_multiset_recall"] is not None) / max(1, sum(row["e0_character_multiset_recall"] is not None for row in ok)), 4),
                "mean_e0_sequence_ratio": round(sum(row["e0_sequence_ratio"] for row in ok if row["e0_sequence_ratio"] is not None) / max(1, sum(row["e0_sequence_ratio"] is not None for row in ok)), 4),
                "rows": e1_rows,
            },
        },
        e0_docs,
        e1_docs,
    )


def crossing_count(selected: list[Any], original_order: dict[str, int], compare_order: dict[str, int]) -> int:
    points = [(original_order[item.original.clause_id], compare_order[item.compare.clause_id]) for item in selected]
    return sum(1 for i, left in enumerate(points) for right in points[i + 1 :] if (left[0] - right[0]) * (left[1] - right[1]) < 0)


def sequence_dp(original: list[Any], compare: list[Any], candidates: list[Any]) -> list[Any]:
    by_pair = {(item.original.clause_id, item.compare.clause_id): item for item in candidates}
    n, m = len(original), len(compare)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    move = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best, direction = dp[i - 1][j], "up"
            if dp[i][j - 1] > best:
                best, direction = dp[i][j - 1], "left"
            candidate = by_pair.get((original[i - 1].clause_id, compare[j - 1].clause_id))
            if candidate is not None:
                value = dp[i - 1][j - 1] + 100_000 + int(round(candidate.score * 100))
                if value > best:
                    best, direction = value, "diag"
            dp[i][j], move[i][j] = best, direction
    selected = []
    i, j = n, m
    while i and j:
        direction = move[i][j]
        if direction == "diag":
            selected.append(by_pair[(original[i - 1].clause_id, compare[j - 1].clause_id)])
            i -= 1
            j -= 1
        elif direction == "left":
            j -= 1
        else:
            i -= 1
    selected.reverse()
    return selected


def sequence_dp_with_groups(
    original: list[Any], compare: list[Any], candidates: list[Any]
) -> list[tuple[str, tuple[Any, ...]]]:
    """Order-preserving prototype with 1:1, 1:2, 2:1, and gap transitions.

    Group scores are the arithmetic mean of the already-frozen acceptable pair
    scores. No new retrieval, scorer, policy, threshold, or weight is used.
    """
    by_pair = {(item.original.clause_id, item.compare.clause_id): item for item in candidates}
    n, m = len(original), len(compare)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    move: list[list[tuple[str, tuple[Any, ...]] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best = dp[i - 1][j]
            choice: tuple[str, tuple[Any, ...]] = ("up", ())
            if dp[i][j - 1] > best:
                best, choice = dp[i][j - 1], ("left", ())
            one = by_pair.get((original[i - 1].clause_id, compare[j - 1].clause_id))
            if one is not None:
                value = dp[i - 1][j - 1] + 100_000 + int(round(one.score * 100))
                if value > best:
                    best, choice = value, ("1:1", (one,))
            if j >= 2:
                first = by_pair.get((original[i - 1].clause_id, compare[j - 2].clause_id))
                second = by_pair.get((original[i - 1].clause_id, compare[j - 1].clause_id))
                if first is not None and second is not None:
                    score = (first.score + second.score) / 2
                    value = dp[i - 1][j - 2] + 200_000 + int(round(score * 100))
                    if value > best:
                        best, choice = value, ("1:2", (first, second))
            if i >= 2:
                first = by_pair.get((original[i - 2].clause_id, compare[j - 1].clause_id))
                second = by_pair.get((original[i - 1].clause_id, compare[j - 1].clause_id))
                if first is not None and second is not None:
                    score = (first.score + second.score) / 2
                    value = dp[i - 2][j - 1] + 200_000 + int(round(score * 100))
                    if value > best:
                        best, choice = value, ("2:1", (first, second))
            dp[i][j], move[i][j] = best, choice
    selected: list[tuple[str, tuple[Any, ...]]] = []
    i, j = n, m
    while i and j:
        choice = move[i][j] or ("up", ())
        kind, group = choice
        if kind == "1:1":
            selected.append((kind, group))
            i -= 1
            j -= 1
        elif kind == "1:2":
            selected.append((kind, group))
            i -= 1
            j -= 2
        elif kind == "2:1":
            selected.append((kind, group))
            i -= 2
            j -= 1
        elif kind == "left":
            j -= 1
        else:
            i -= 1
    selected.reverse()
    return selected


def matcher_benchmark(pairs: list[tuple[str, str, Path]], e0_docs: dict[str, Document]) -> dict[str, Any]:
    splitter = ClauseSplitter()
    matcher = ClauseMatcher()
    rows = []
    totals = Counter()
    for left_id, right_id, _task in pairs:
        if left_id not in e0_docs or right_id not in e0_docs:
            continue
        original = splitter.split(e0_docs[left_id], f"o-{left_id}-")
        compare = splitter.split(e0_docs[right_id], f"c-{right_id}-")
        started = time.perf_counter()
        all_candidates = matcher._build_candidates(original, compare)
        candidate_seconds = time.perf_counter() - started
        acceptable = [item for item in all_candidates if matcher._candidate_acceptable(item)]
        variants = {}
        selections: dict[str, list[Any]] = {}
        for name, selector in (("M0_greedy", matcher._select_greedy_candidates), ("M1_optimal", matcher._select_optimal_candidates)):
            started = time.perf_counter()
            selected = selector(acceptable)
            selections[name] = selected
            variants[name] = {"matches": len(selected), "score_sum": round(sum(item.score for item in selected), 2), "assignment_seconds": round(time.perf_counter() - started, 6)}
        started = time.perf_counter()
        selections["M2_sequence_1to1"] = sequence_dp(original, compare, acceptable)
        variants["M2_sequence_1to1"] = {
            "matches": len(selections["M2_sequence_1to1"]),
            "score_sum": round(sum(item.score for item in selections["M2_sequence_1to1"]), 2),
            "assignment_seconds": round(time.perf_counter() - started, 6),
        }
        original_order = {item.clause_id: index for index, item in enumerate(original)}
        compare_order = {item.clause_id: index for index, item in enumerate(compare)}
        edge_sets = {}
        for name, selected in selections.items():
            variants[name]["crossings"] = crossing_count(selected, original_order, compare_order)
            edge_sets[name] = {(item.original.clause_id, item.compare.clause_id) for item in selected}
        greedy = edge_sets["M0_greedy"]
        for name in ("M1_optimal", "M2_sequence_1to1"):
            variants[name]["edge_disagreement_vs_greedy"] = len(greedy.symmetric_difference(edge_sets[name]))
        acceptable_edges = {(c.original.clause_id, c.compare.clause_id) for c in acceptable}
        adjacent_split_opportunities = sum(
            1
            for i in range(len(original))
            for j in range(len(compare) - 1)
            if (original[i].clause_id, compare[j].clause_id) in acceptable_edges
            and (original[i].clause_id, compare[j + 1].clause_id) in acceptable_edges
        )
        adjacent_merge_opportunities = sum(
            1
            for i in range(len(original) - 1)
            for j in range(len(compare))
            if (original[i].clause_id, compare[j].clause_id) in acceptable_edges
            and (original[i + 1].clause_id, compare[j].clause_id) in acceptable_edges
        )
        started = time.perf_counter()
        grouped = sequence_dp_with_groups(original, compare, acceptable)
        group_edges = {
            (candidate.original.clause_id, candidate.compare.clause_id)
            for _kind, group in grouped
            for candidate in group
        }
        variants["M2_sequence_with_1to2_2to1"] = {
            "status": "OFFLINE_PROTOTYPE_NO_LABELS",
            "groups": len(grouped),
            "matched_original_clauses": len({candidate.original.clause_id for _kind, group in grouped for candidate in group}),
            "matched_compare_clauses": len({candidate.compare.clause_id for _kind, group in grouped for candidate in group}),
            "score_sum_of_group_means": round(sum(sum(candidate.score for candidate in group) / len(group) for _kind, group in grouped), 2),
            "selected_1to2": sum(kind == "1:2" for kind, _group in grouped),
            "selected_2to1": sum(kind == "2:1" for kind, _group in grouped),
            "assignment_seconds": round(time.perf_counter() - started, 6),
            "edge_disagreement_vs_greedy": len(greedy.symmetric_difference(group_edges)),
            "adjacent_1to2_candidate_groups": adjacent_split_opportunities,
            "adjacent_2to1_candidate_groups": adjacent_merge_opportunities,
            "aggregation_rule": "mean of the two frozen acceptable pair scores; cardinality bonus counts two covered clauses",
        }
        rows.append({"pair_id": f"{left_id}-{right_id}", "original_clauses": len(original), "compare_clauses": len(compare), "candidates": len(all_candidates), "acceptable": len(acceptable), "candidate_seconds": round(candidate_seconds, 4), "variants": variants})
        totals["pairs"] += 1
        for name in ("M0_greedy", "M1_optimal", "M2_sequence_1to1"):
            for metric in ("matches", "crossings", "edge_disagreement_vs_greedy"):
                totals[f"{name}.{metric}"] += variants[name].get(metric, 0)
            totals[f"{name}.score_sum"] += variants[name]["score_sum"]
        totals["candidate_seconds"] += candidate_seconds
        totals["split_opportunities"] += adjacent_split_opportunities
        totals["merge_opportunities"] += adjacent_merge_opportunities
        totals["M2_group.selected_1to2"] += variants["M2_sequence_with_1to2_2to1"]["selected_1to2"]
        totals["M2_group.selected_2to1"] += variants["M2_sequence_with_1to2_2to1"]["selected_2to1"]
        totals["M2_group.groups"] += variants["M2_sequence_with_1to2_2to1"]["groups"]
        totals["M2_group.matched_original_clauses"] += variants["M2_sequence_with_1to2_2to1"]["matched_original_clauses"]
        totals["M2_group.matched_compare_clauses"] += variants["M2_sequence_with_1to2_2to1"]["matched_compare_clauses"]
    return {"human_labels": False, "error_attribution": "UNKNOWN", "rows": rows, "totals": {key: round(value, 4) if isinstance(value, float) else value for key, value in totals.items()}}


def bbox_key(box: Any) -> tuple[int, int, int, int, int]:
    return (box.page_no, round(box.bbox.x0), round(box.bbox.y0), round(box.bbox.x1), round(box.bbox.y1))


def evidence_benchmark(e0_docs: dict[str, Document]) -> dict[str, Any]:
    splitter = ClauseSplitter()
    locator = TextCoordinateLocator()
    counts = Counter()
    searches = Counter()
    cases = []
    for case_id, document in e0_docs.items():
        clauses = splitter.split(document, f"e-{case_id}-")
        page_by_no = {page.page_no: page for page in document.pages}
        block_source = {block.block_id: block.source for page in document.pages for block in page.blocks}
        for clause in clauses:
            counts["clauses"] += 1
            counts["cross_block_clauses"] += len(clause.source_block_ids) > 1
            counts["cross_page_clauses"] += len(set(clause.page_numbers)) > 1
            counts["structural_slice_clauses"] += bool(clause.split_flags)
            counts["offset_length_mismatch"] += len(clause.char_boxes) != len(clause.text)
            counts["nfkc_changed_clauses"] += unicodedata.normalize("NFKC", clause.text) != clause.text
            boxes = [box for box in clause.char_boxes if box is not None]
            counts["char_slots"] += len(clause.char_boxes)
            counts["missing_char_slots"] += sum(box is None for box in clause.char_boxes)
            counts["indexed_geometry_slots"] += sum(box.text_index is not None for box in boxes)
            counts["estimated_geometry_slots"] += sum(box.text_index is None for box in boxes)
            repeated = Counter(bbox_key(box) for box in boxes)
            counts["word_level_slots_labeled_exact"] += sum(count for count in repeated.values() if count > 1)
            for box in boxes:
                page = page_by_no.get(box.page_no)
                if page and (box.bbox.x0 < -1 or box.bbox.y0 < -1 or box.bbox.x1 > page.width + 1 or box.bbox.y1 > page.height + 1):
                    counts["out_of_bounds_slots"] += 1
            if clause.source_block_ids:
                sources = {block_source.get(block_id, "") for block_id in clause.source_block_ids}
                counts["ppocr_source_clauses"] += any("ppocr" in source for source in sources)
        pdf_path = Path(document.path)
        if pdf_path.exists():
            try:
                with fitz.open(pdf_path) as pdf:
                    attempted = 0
                    for clause in clauses:
                        if attempted >= 25:
                            break
                        if not clause.text.strip() or not any(box is None or box.text_index is None for box in clause.char_boxes):
                            continue
                        query = " ".join(clause.text[:80].split())
                        if not query:
                            continue
                        attempted += 1
                        searches["attempts"] += 1
                        preferred_pages = [page for page in clause.page_numbers if 1 <= page <= len(pdf)]
                        pages = preferred_pages + [page for page in range(1, len(pdf) + 1) if page not in preferred_pages]
                        direct = False
                        word_fallback = False
                        for candidate_page in pages:
                            page = pdf[candidate_page - 1]
                            searches["search_for_page_attempts"] += 1
                            if page.search_for(query):
                                direct = True
                                break
                            if locator._word_sequence_candidates(page, query, candidate_page, "MODIFY"):
                                word_fallback = True
                                break
                        searches["search_for_success"] += direct
                        searches["word_sequence_fallback_success"] += word_fallback
                        searches["failure"] += not (direct or word_fallback)
            except Exception:
                searches["pdf_open_failure"] += 1
        cases.append({"case_id": case_id, "clauses": len(clauses)})
    relocation = Counter()
    relocation_reasons = Counter()
    for path in TASKS.glob("*/diagnostics/debug/ocr_remediation.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for action in payload.get("actions", []):
            if action.get("action_type") != "RELOCATE_EVIDENCE":
                continue
            relocation[str(action.get("status") or "UNKNOWN")] += 1
            for note in action.get("notes", []):
                relocation_reasons[str(note)] += 1
    indexed = counts["indexed_geometry_slots"]
    return {
        "counts": dict(counts),
        "search_probe": dict(searches),
        "search_probe_note": "Stress probe uses the first 80 characters of clauses containing missing geometry; it is not the production diff-snippet success rate.",
        "retained_evidence_relocator": {
            "status_counts": dict(relocation),
            "reason_counts": dict(relocation_reasons),
            "note": "Counts include repeated retained task runs and are not deduplicated by PDF hash.",
        },
        "word_level_share_of_indexed_geometry": round(counts["word_level_slots_labeled_exact"] / indexed, 4) if indexed else None,
        "taxonomy": {
            "OFFSET": counts["offset_length_mismatch"] + counts["missing_char_slots"],
            "OCR_GEOMETRY": counts["word_level_slots_labeled_exact"] + counts["estimated_geometry_slots"],
            "PDF_TRANSFORM": counts["out_of_bounds_slots"],
            "SEARCH": searches["failure"],
            "OTHER": counts["structural_slice_clauses"],
        },
        "cases": cases,
    }


def column_signature(table: Any, col: int) -> str:
    parts = []
    for row in table.rows:
        for cell in row.cells:
            if cell.col_index <= col < cell.col_index + cell.colspan:
                parts.append(table_utils.normalize(cell.text))
                break
    return "|".join(parts)


def table_benchmark(pairs: list[tuple[str, str, Path]], e0_docs: dict[str, Document]) -> dict[str, Any]:
    diagnostic = Counter()
    for path in TASKS.glob("*/diagnostics/debug/table_repair.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for side in ("original", "compare"):
            metrics = payload.get("quality_metrics", {}).get(side, {})
            for warning, count in metrics.get("geometry_warning_counts", {}).items():
                diagnostic[warning] += count
            for status, count in metrics.get("geometry_status_counts", {}).items():
                diagnostic[f"geometry_status:{status}"] += count
        shape = payload.get("shape_changes", {}).get("compare_vs_original_logical", {})
        diagnostic["table_pair_count_delta_cases"] += bool(shape.get("table_count_delta"))
        diagnostic["row_count_delta_cases"] += bool(shape.get("row_count_delta"))
    parser = LogicalTableParser()
    matcher = TableMatcher()
    prototype = Counter()
    gains: list[float] = []
    for left_id, right_id, _task in pairs:
        if left_id not in e0_docs or right_id not in e0_docs:
            continue
        left = parser.parse_tables(parser.table_blocks(e0_docs[left_id]))
        right = parser.parse_tables(parser.table_blocks(e0_docs[right_id]))
        for oi, ci, _score in matcher.match_tables(left, right):
            original, compare = left[oi], right[ci]
            if not original.col_count or not compare.col_count:
                continue
            similarities = [[sequence_ratio(column_signature(original, i), column_signature(compare, j)) for j in range(compare.col_count)] for i in range(original.col_count)]
            size = max(original.col_count, compare.col_count)
            costs = [[1.0] * size for _ in range(size)]
            for i in range(original.col_count):
                for j in range(compare.col_count):
                    costs[i][j] = 1.0 - similarities[i][j]
            rows, cols = linear_sum_assignment(costs)
            mapping = [(int(i), int(j)) for i, j in zip(rows, cols, strict=True) if i < original.col_count and j < compare.col_count]
            hungarian = sum(similarities[i][j] for i, j in mapping)
            identity = sum(similarities[i][i] for i in range(min(original.col_count, compare.col_count)))
            gain = hungarian - identity
            gains.append(gain)
            prototype["table_pairs"] += 1
            prototype["mapping_changed"] += mapping != [(i, i) for i in range(min(original.col_count, compare.col_count))]
            prototype["objective_improved"] += gain > 1e-9
            prototype["objective_gain_milli"] += round(gain * 1000)
    taxonomy = {
        "EXTRACTION_GRID_ERROR": diagnostic["bbox_html_col_count_delta"] + diagnostic["bbox_html_row_count_delta"] + diagnostic["geometry_status:severe_conflict"],
        "CELL_BBOX_ERROR": diagnostic["bbox_geometry_unusable"] + diagnostic["bbox_count_mismatch"] + diagnostic["right_fragment_overflow"],
        "TABLE_PAIR_ERROR": diagnostic["table_pair_count_delta_cases"],
        "COLUMN_ALIGNMENT_ERROR": diagnostic["bbox_html_col_alignment_conflict"],
        "ROW_ALIGNMENT_ERROR": diagnostic["bbox_html_row_alignment_conflict"] + diagnostic["row_count_delta_cases"],
        "CELL_DIFF_ERROR": 0,
    }
    return {
        "diagnostic_counts": dict(diagnostic),
        "root_cause_taxonomy_proxy": taxonomy,
        "hungarian_column_probe": {
            **dict(prototype),
            "objective_gain": round(sum(gains), 4),
            "mean_objective_gain": round(sum(gains) / len(gains), 4) if gains else None,
            "labeled_error_reduction": None,
            "note": "No cell/column gold labels; objective improvement is not accuracy.",
        },
    }


def mineru_probe() -> dict[str, Any]:
    try:
        import mineru  # type: ignore
    except Exception as exc:
        return {"status": "NOT_RUN", "package_import": False, "reason": type(exc).__name__, "model_available": False}
    model_cache = list(Path.home().glob(".cache/huggingface/hub/models--opendatalab--MinerU*"))
    return {"status": "NOT_RUN", "package_import": True, "package_path_hash": hashlib.sha256(str(Path(mineru.__file__).parent).encode()).hexdigest()[:12], "model_available": bool(model_cache), "reason": "Required MinerU inference model/runtime was not available; no download was authorized."}


def main() -> None:
    corpus, pairs = discover_corpus()
    manifest = corpus_manifest(corpus, pairs)
    (RESULTS / "corpus_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    extraction, e0_docs, e1_docs = extraction_benchmark(corpus)
    extraction["E0"]["comparison_pairs_with_both_documents"] = sum(
        left_id in e0_docs and right_id in e0_docs for left_id, right_id, _task in pairs
    )
    extraction["E1"]["comparison_pairs_with_both_documents"] = sum(
        left_id in e1_docs and right_id in e1_docs for left_id, right_id, _task in pairs
    )
    report = {
        "schema_version": 1,
        "corpus": manifest,
        "extraction": extraction,
        "mineru": mineru_probe(),
        "matcher": matcher_benchmark(pairs, e0_docs),
        "evidence": evidence_benchmark(e0_docs),
        "tables": table_benchmark(pairs, e0_docs),
        "contract_matrix": {
            "E0_current_hybrid": {"text": "EXACT", "page": "EXACT", "bbox": "EXACT", "reading_order": "EXACT", "block_type": "EXACT", "confidence": "EXACT", "char_word_geometry": "DEGRADED", "table_structure": "EXACT", "raw_html": "EXACT", "cell_bbox": "EXACT"},
            "E1_pymupdf": {"text": "EXACT", "page": "EXACT", "bbox": "EXACT", "reading_order": "ADAPTABLE", "block_type": "DEGRADED", "confidence": "MISSING", "char_word_geometry": "EXACT", "table_structure": "MISSING", "raw_html": "MISSING", "cell_bbox": "MISSING"},
            "E2_mineru": {field: "MISSING" for field in ("text", "page", "bbox", "reading_order", "block_type", "confidence", "char_word_geometry", "table_structure", "raw_html", "cell_bbox")},
        },
    }
    (RESULTS / "offline_benchmark.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"corpus": {key: manifest[key] for key in ("documents", "comparison_pairs", "pages", "page_category_counts_nonexclusive")}, "E1": {key: extraction["E1"][key] for key in ("documents_ok", "documents_failed", "failure_rate", "latency_seconds_total", "mean_e0_character_multiset_recall", "mean_e0_sequence_ratio")}, "matcher": report["matcher"]["totals"], "evidence": report["evidence"]["taxonomy"], "tables": report["tables"]["hungarian_column_probe"], "mineru": report["mineru"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
