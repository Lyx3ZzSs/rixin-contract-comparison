from __future__ import annotations

import logging

from app.services.matching.types import FlowEdge, MatchCandidate

logger = logging.getLogger(__name__)


class AssignmentMixin:
    def _select_global_candidates(self, candidates: list[MatchCandidate]) -> list[MatchCandidate]:
        acceptable = [candidate for candidate in candidates if self._candidate_acceptable(candidate)]
        if self.assignment_strategy == "optimal":
            try:
                selected = self._select_optimal_candidates(acceptable)
            except Exception:
                logger.warning(
                    "Optimal clause assignment failed; falling back to greedy selection.",
                    exc_info=True,
                )
                return self._select_greedy_candidates(acceptable)
            if acceptable and not selected:
                logger.warning("Optimal clause assignment returned no matches; falling back to greedy selection.")
                return self._select_greedy_candidates(acceptable)
            return selected
        return self._select_greedy_candidates(acceptable)

    def _select_greedy_candidates(self, acceptable: list[MatchCandidate]) -> list[MatchCandidate]:
        acceptable = sorted(acceptable, key=self._candidate_priority_key, reverse=True)
        by_original: dict[str, list[MatchCandidate]] = {}
        by_compare: dict[str, list[MatchCandidate]] = {}
        for candidate in acceptable:
            by_original.setdefault(candidate.original.clause_id, []).append(candidate)
            by_compare.setdefault(candidate.compare.clause_id, []).append(candidate)

        selected: list[MatchCandidate] = []
        used_original: set[str] = set()
        used_compare: set[str] = set()

        for candidate in acceptable:
            if candidate.original.clause_id in used_original or candidate.compare.clause_id in used_compare:
                continue
            if self._is_mutual_best(candidate, by_original, by_compare):
                selected.append(candidate)
                used_original.add(candidate.original.clause_id)
                used_compare.add(candidate.compare.clause_id)

        for candidate in acceptable:
            if candidate.original.clause_id in used_original or candidate.compare.clause_id in used_compare:
                continue
            selected.append(candidate)
            used_original.add(candidate.original.clause_id)
            used_compare.add(candidate.compare.clause_id)

        return selected

    def _select_optimal_candidates(self, candidates: list[MatchCandidate]) -> list[MatchCandidate]:
        if not candidates:
            return []
        best_by_pair: dict[tuple[str, str], MatchCandidate] = {}
        for candidate in candidates:
            key = (candidate.original.clause_id, candidate.compare.clause_id)
            existing = best_by_pair.get(key)
            if existing is None or candidate.score > existing.score:
                best_by_pair[key] = candidate
        sparse_candidates = list(best_by_pair.values())
        original_ids = sorted({candidate.original.clause_id for candidate in sparse_candidates})
        compare_ids = sorted({candidate.compare.clause_id for candidate in sparse_candidates})
        original_index = {clause_id: index for index, clause_id in enumerate(original_ids)}
        compare_index = {clause_id: index for index, clause_id in enumerate(compare_ids)}
        selected_keys = self._max_weight_matching(
            [
                (
                    original_index[candidate.original.clause_id],
                    compare_index[candidate.compare.clause_id],
                    candidate.score,
                )
                for candidate in sparse_candidates
            ],
            left_count=len(original_ids),
            right_count=len(compare_ids),
        )
        by_index_pair = {
            (original_index[candidate.original.clause_id], compare_index[candidate.compare.clause_id]): candidate
            for candidate in sparse_candidates
        }
        selected = [by_index_pair[key] for key in selected_keys if key in by_index_pair]
        selected.sort(
            key=self._candidate_priority_key,
            reverse=True,
        )
        return selected

    @staticmethod
    def _candidate_priority_key(candidate: MatchCandidate) -> tuple[float, float, float, float]:
        return (
            candidate.score,
            candidate.details["body_score"],
            candidate.details["clause_no_score"],
            candidate.details["title_score"],
        )

    def _max_weight_matching(
        self,
        edges: list[tuple[int, int, float]],
        *,
        left_count: int,
        right_count: int,
    ) -> set[tuple[int, int]]:
        if not edges or left_count <= 0 or right_count <= 0:
            return set()
        source = 0
        left_start = 1
        right_start = left_start + left_count
        sink = right_start + right_count
        graph: list[list[FlowEdge]] = [[] for _ in range(sink + 1)]

        def add_edge(left: int, right: int, capacity: int, cost: int) -> None:
            graph[left].append(FlowEdge(right, len(graph[right]), capacity, cost))
            graph[right].append(FlowEdge(left, len(graph[left]) - 1, 0, -cost))

        for left in range(left_count):
            add_edge(source, left_start + left, 1, 0)
        for right in range(right_count):
            add_edge(right_start + right, sink, 1, 0)
        cardinality_bonus = 100_000
        for left, right, score in edges:
            add_edge(left_start + left, right_start + right, 1, -(cardinality_bonus + int(round(score * 100))))

        selected: set[tuple[int, int]] = set()
        while True:
            distance = [10**18] * len(graph)
            previous_node = [-1] * len(graph)
            previous_edge = [-1] * len(graph)
            in_queue = [False] * len(graph)
            queue = [source]
            distance[source] = 0
            in_queue[source] = True
            while queue:
                node = queue.pop(0)
                in_queue[node] = False
                for edge_index, edge in enumerate(graph[node]):
                    if edge.capacity <= 0:
                        continue
                    next_distance = distance[node] + edge.cost
                    if next_distance < distance[edge.to]:
                        distance[edge.to] = next_distance
                        previous_node[edge.to] = node
                        previous_edge[edge.to] = edge_index
                        if not in_queue[edge.to]:
                            queue.append(edge.to)
                            in_queue[edge.to] = True
            if distance[sink] >= 0 or previous_node[sink] < 0:
                break
            node = sink
            while node != source:
                prev = previous_node[node]
                edge_index = previous_edge[node]
                edge = graph[prev][edge_index]
                edge.capacity -= 1
                graph[node][edge.reverse].capacity += 1
                node = prev

        for left in range(left_count):
            graph_node = left_start + left
            for edge in graph[graph_node]:
                if right_start <= edge.to < right_start + right_count and edge.capacity == 0:
                    selected.add((left, edge.to - right_start))
        return selected

    def _is_mutual_best(
        self,
        candidate: MatchCandidate,
        by_original: dict[str, list[MatchCandidate]],
        by_compare: dict[str, list[MatchCandidate]],
    ) -> bool:
        original_best = by_original.get(candidate.original.clause_id, [])[:1]
        compare_best = by_compare.get(candidate.compare.clause_id, [])[:1]
        return bool(original_best and compare_best and original_best[0] is candidate and compare_best[0] is candidate)
