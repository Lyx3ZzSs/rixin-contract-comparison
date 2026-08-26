from __future__ import annotations

import html
import json
from collections import Counter, defaultdict, deque
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.models import Clause
from app.services.clause_splitter import ClauseSplitter
from app.services.matcher import ClauseMatcher

from run_offline_benchmark import (
    RESULTS,
    compact,
    discover_corpus,
    load_hybrid,
    sequence_dp,
    sequence_dp_with_groups,
)


HERE = Path(__file__).resolve().parent
PRIVATE = HERE / "review_artifacts" / "private"


def clause_payload(clause: Clause, clauses: list[Clause]) -> dict[str, Any]:
    index = clauses.index(clause)
    before = clauses[index - 1] if index > 0 else None
    after = clauses[index + 1] if index + 1 < len(clauses) else None
    return {
        "clause_id": clause.clause_id,
        "order_index": clause.order_index,
        "clause_no": clause.clause_no,
        "title": clause.title,
        "section_path": clause.section_path,
        "text": clause.text,
        "page_numbers": clause.page_numbers,
        "evidence": [value.model_dump(mode="json") for value in clause.bboxes],
        "before": None
        if before is None
        else {
            "clause_id": before.clause_id,
            "clause_no": before.clause_no,
            "title": before.title,
            "text": before.text,
            "page_numbers": before.page_numbers,
        },
        "after": None
        if after is None
        else {
            "clause_id": after.clause_id,
            "clause_no": after.clause_no,
            "title": after.title,
            "text": after.text,
            "page_numbers": after.page_numbers,
        },
    }


def edge(candidate: Any) -> tuple[str, str]:
    return candidate.original.clause_id, candidate.compare.clause_id


def components(edges: set[tuple[str, str]]) -> list[tuple[set[str], set[str]]]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for original, compare in edges:
        left, right = f"o:{original}", f"c:{compare}"
        adjacency[left].add(right)
        adjacency[right].add(left)
    seen: set[str] = set()
    result: list[tuple[set[str], set[str]]] = []
    for start in sorted(adjacency):
        if start in seen:
            continue
        queue = deque([start])
        nodes: set[str] = set()
        seen.add(start)
        while queue:
            node = queue.popleft()
            nodes.add(node)
            for target in adjacency[node]:
                if target not in seen:
                    seen.add(target)
                    queue.append(target)
        result.append(
            (
                {node[2:] for node in nodes if node.startswith("o:")},
                {node[2:] for node in nodes if node.startswith("c:")},
            )
        )
    return result


def decision_edges(selected: list[Any], originals: set[str], compares: set[str]) -> list[list[str]]:
    return [
        [candidate.original.clause_id, candidate.compare.clause_id]
        for candidate in selected
        if candidate.original.clause_id in originals and candidate.compare.clause_id in compares
    ]


def suggest_component(
    original_clauses: list[Clause],
    compare_clauses: list[Clause],
    transitions: list[dict[str, Any]],
    decisions: dict[str, list[list[str]]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    if transitions:
        kind = transitions[0]["kind"]
        left = compact("".join(value.text for value in original_clauses))
        right = compact("".join(value.text for value in compare_clauses))
        combined = SequenceMatcher(None, left, right, autojunk=False).ratio() if left or right else 1.0
        individual = max(
            (
                SequenceMatcher(None, compact(original.text), compact(compare.text), autojunk=False).ratio()
                for original in original_clauses
                for compare in compare_clauses
            ),
            default=0.0,
        )
        if combined >= 0.78 and combined - individual >= 0.08:
            label = "TRUE_SPLIT" if kind == "1:2" else "TRUE_MERGE"
        elif combined < 0.55:
            label = "FALSE_SPLIT" if kind == "1:2" else "FALSE_MERGE"
        else:
            label = "AMBIGUOUS"
        return {
            "label": label,
            "root_cause": "TRUE_SPLIT_MERGE" if label.startswith("TRUE_") else "UNKNOWN",
            "ground_truth": False,
            "combined_text_ratio": round(combined, 4),
            "best_individual_text_ratio": round(individual, 4),
            "reason": "Text-combination heuristic only; human semantic review is still required.",
        }
    score_by_edge = {
        (value["original_clause_id"], value["compare_clause_id"]): float(value["score"])
        for value in candidates
    }
    totals = {
        name: sum(score_by_edge.get(tuple(value), 0.0) for value in values)
        for name, values in decisions.items()
    }
    ordered = sorted(totals.items(), key=lambda value: value[1], reverse=True)
    label = "AMBIGUOUS"
    if len(ordered) > 1 and ordered[0][1] - ordered[1][1] >= 5.0:
        label = {
            "greedy": "GREEDY_CORRECT",
            "optimal": "OPTIMAL_CORRECT",
            "sequence": "SEQUENCE_CORRECT",
        }[ordered[0][0]]
    return {
        "label": label,
        "root_cause": "UNKNOWN",
        "ground_truth": False,
        "local_candidate_score_sums": {key: round(value, 4) for key, value in totals.items()},
        "reason": "Frozen scorer preference only; candidate score is not semantic ground truth.",
    }


def render_html(payload: dict[str, Any]) -> str:
    sections = []
    for component in payload["components"]:
        clause_columns = []
        for side, label in (("original_clauses", "Original"), ("compare_clauses", "Compare")):
            cards = []
            for clause in component[side]:
                cards.append(
                    "<article><h4>"
                    + html.escape(f"{clause['clause_no']} {clause['title']}")
                    + "</h4><p>"
                    + html.escape(" / ".join(clause["section_path"]))
                    + "</p><p>pages="
                    + html.escape(str(clause["page_numbers"]))
                    + "</p><pre>"
                    + html.escape(clause["text"])
                    + "</pre><details><summary>前后文</summary><pre>"
                    + html.escape(json.dumps({"before": clause["before"], "after": clause["after"]}, ensure_ascii=False, indent=2))
                    + "</pre></details></article>"
                )
            clause_columns.append(f"<div><h3>{label}</h3>{''.join(cards)}</div>")
        sections.append(
            f"<section><h2>{html.escape(component['component_id'])}</h2>"
            f"<p>human_label=<b>{component['human_label']}</b>; root_cause=<b>{component['root_cause']}</b></p>"
            f"<div class='grid'>{''.join(clause_columns)}</div>"
            f"<h3>决策与候选</h3><pre>{html.escape(json.dumps({'decisions': component['decisions'], 'split_merge_transitions': component['split_merge_transitions'], 'candidates': component['candidates'], 'model_suggestion': component['model_suggestion']}, ensure_ascii=False, indent=2))}</pre>"
            "</section>"
        )
    return """<!doctype html><meta charset="utf-8"><title>Matcher conflict review</title>
<style>body{font:14px/1.5 system-ui;margin:24px;background:#f5f5f5}section{background:white;padding:20px;margin:0 0 24px;border-radius:10px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}article{border:1px solid #ddd;padding:12px;margin:8px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#fafafa;padding:10px}h2{position:sticky;top:0;background:white}</style>""" + "".join(sections)


def main() -> None:
    corpus, pairs = discover_corpus()
    by_id = {item.case_id: load_hybrid(item.hybrid) for item in corpus if item.hybrid}
    splitter = ClauseSplitter()
    matcher = ClauseMatcher()
    review_components: list[dict[str, Any]] = []
    transition_counts = Counter()
    edge_counts = Counter()
    for left_id, right_id, _task in pairs:
        original = splitter.split(by_id[left_id], f"o-{left_id}-")
        compare = splitter.split(by_id[right_id], f"c-{right_id}-")
        candidates = matcher._build_candidates(original, compare)
        acceptable = [candidate for candidate in candidates if matcher._candidate_acceptable(candidate)]
        selections = {
            "greedy": matcher._select_greedy_candidates(acceptable),
            "optimal": matcher._select_optimal_candidates(acceptable),
            "sequence": sequence_dp(original, compare, acceptable),
        }
        selected_sets = {key: {edge(value) for value in values} for key, values in selections.items()}
        edge_counts["greedy_vs_optimal_symmetric_difference"] += len(
            selected_sets["greedy"] ^ selected_sets["optimal"]
        )
        edge_counts["greedy_vs_sequence_symmetric_difference"] += len(
            selected_sets["greedy"] ^ selected_sets["sequence"]
        )
        edge_counts["optimal_vs_sequence_symmetric_difference"] += len(
            selected_sets["optimal"] ^ selected_sets["sequence"]
        )
        all_selected = set().union(*selected_sets.values())
        disagreement = {
            value
            for value in all_selected
            if len({value in selected for selected in selected_sets.values()}) > 1
        }
        grouped = sequence_dp_with_groups(original, compare, acceptable)
        transitions = []
        transition_edges: set[tuple[str, str]] = set()
        for kind, group in grouped:
            if kind not in {"1:2", "2:1"}:
                continue
            group_edges = [edge(candidate) for candidate in group]
            transition_edges.update(group_edges)
            transitions.append({"kind": kind, "edges": [list(value) for value in group_edges]})
            transition_counts[kind] += 1
        all_component_edges = disagreement | transition_edges
        edge_counts["assignment_disagreement_edges"] += len(disagreement)
        edge_counts["split_merge_transition_edges"] += len(transition_edges)
        edge_counts["overlapping_edges"] += len(disagreement & transition_edges)
        by_edge = {edge(candidate): candidate for candidate in candidates}
        original_by_id = {clause.clause_id: clause for clause in original}
        compare_by_id = {clause.clause_id: clause for clause in compare}
        for originals, compares in components(all_component_edges):
            component_transitions = [
                value
                for value in transitions
                if any(edge_value[0] in originals or edge_value[1] in compares for edge_value in value["edges"])
            ]
            candidate_payload = []
            for original_id in originals:
                for compare_id in compares:
                    candidate = by_edge.get((original_id, compare_id))
                    if candidate is None:
                        continue
                    candidate_payload.append(
                        {
                            "original_clause_id": original_id,
                            "compare_clause_id": compare_id,
                            "score": candidate.score,
                            "method": candidate.method,
                            "score_details": candidate.details,
                            "acceptable": matcher._candidate_acceptable(candidate),
                        }
                    )
            ordered_originals = [
                original_by_id[value]
                for value in sorted(originals, key=lambda item: original.index(original_by_id[item]))
            ]
            ordered_compares = [
                compare_by_id[value]
                for value in sorted(compares, key=lambda item: compare.index(compare_by_id[item]))
            ]
            decision_payload = {
                key: decision_edges(values, originals, compares)
                for key, values in selections.items()
            }
            sorted_candidates = sorted(candidate_payload, key=lambda value: -float(value["score"]))
            review_components.append(
                {
                    "pair_id": f"{left_id}-{right_id}",
                    "involved_original_clause_ids": sorted(originals),
                    "involved_compare_clause_ids": sorted(compares),
                    "original_clauses": [clause_payload(value, original) for value in ordered_originals],
                    "compare_clauses": [clause_payload(value, compare) for value in ordered_compares],
                    "candidates": sorted_candidates,
                    "decisions": decision_payload,
                    "split_merge_transitions": component_transitions,
                    "human_label": "UNLABELED",
                    "root_cause": "UNKNOWN",
                    "model_suggestion": suggest_component(
                        ordered_originals,
                        ordered_compares,
                        component_transitions,
                        decision_payload,
                        sorted_candidates,
                    ),
                }
            )
    for index, component in enumerate(review_components, start=1):
        component["component_id"] = f"MC-{index:03d}"
    payload = {
        "component_count": len(review_components),
        "human_labeled_count": 0,
        "transition_counts_before_component_merge": dict(transition_counts),
        "edge_counts_before_component_merge": dict(edge_counts),
        "components": review_components,
    }
    PRIVATE.mkdir(parents=True, exist_ok=True)
    (PRIVATE / "matcher_conflicts.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (PRIVATE / "matcher_conflicts.html").write_text(render_html(payload), encoding="utf-8")
    public = {
        "component_count": len(review_components),
        "human_labeled_count": 0,
        "transition_counts_before_component_merge": dict(transition_counts),
        "edge_counts_before_component_merge": dict(edge_counts),
        "component_shapes": dict(
            Counter(
                f"{len(value['involved_original_clause_ids'])}:{len(value['involved_compare_clause_ids'])}"
                for value in review_components
            )
        ),
        "component_ids": [value["component_id"] for value in review_components],
        "human_adjudication_status": "PENDING",
        "root_cause_taxonomy": {"UNKNOWN": len(review_components)},
        "model_suggestion_counts_not_ground_truth": dict(
            Counter(value["model_suggestion"]["label"] for value in review_components)
        ),
        "review_artifact": str(PRIVATE / "matcher_conflicts.html"),
    }
    (RESULTS / "matcher_conflict_components.json").write_text(
        json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(public, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
