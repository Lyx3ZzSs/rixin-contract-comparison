"""Bounded reuse of immutable provider vectors during one clause match."""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass


@dataclass(slots=True)
class _VectorEntry:
    vector: list[float]
    token: int
    norm: float


class VectorSimilarityCache:
    """Own vector references and never recycle score keys after LRU eviction.

    Provider vectors are read-only for the duration of a match. Object identity
    locates an owned vector; scores use monotonically assigned local tokens,
    not object addresses. No state survives the owning match/context.
    """

    def __init__(self, *, max_vectors: int = 4096, max_pairs: int = 100_000) -> None:
        self.max_vectors = max(0, max_vectors)
        self.max_pairs = max(0, max_pairs)
        self._vectors: OrderedDict[int, _VectorEntry] = OrderedDict()
        self._pairs: OrderedDict[tuple[int, int], float] = OrderedDict()
        self._next_token = 0
        self._counts = dict(vector_hits=0, vector_misses=0, vector_evictions=0,
                            pair_hits=0, pair_misses=0, pair_evictions=0,
                            peak_vectors=0, peak_pairs=0)

    def _vector(self, vector: list[float]) -> _VectorEntry:
        identity = id(vector)
        entry = self._vectors.get(identity)
        if entry is not None and entry.vector is vector:
            self._vectors.move_to_end(identity)
            self._counts["vector_hits"] += 1
            return entry
        self._counts["vector_misses"] += 1
        entry = _VectorEntry(vector, self._next_token, math.sqrt(sum(a * a for a in vector)))
        self._next_token += 1
        if self.max_vectors:
            if len(self._vectors) >= self.max_vectors:
                self._vectors.popitem(last=False)
                self._counts["vector_evictions"] += 1
            self._vectors[identity] = entry
            self._counts["peak_vectors"] = max(self._counts["peak_vectors"], len(self._vectors))
        return entry

    def __call__(self, left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return 0.0
        a, b = self._vector(left), self._vector(right)
        key = (a.token, b.token)
        if key in self._pairs:
            self._pairs.move_to_end(key)
            self._counts["pair_hits"] += 1
            return self._pairs[key]
        self._counts["pair_misses"] += 1
        # Preserve Python summation, clamping and argument order exactly.
        dot = sum(x * y for x, y in zip(left, right, strict=False))
        score = (0.0 if a.norm <= 0 or b.norm <= 0
                 else max(0.0, min(100.0, dot / (a.norm * b.norm) * 100.0)))
        if self.max_pairs and self.max_vectors:
            if len(self._pairs) >= self.max_pairs:
                self._pairs.popitem(last=False)
                self._counts["pair_evictions"] += 1
            self._pairs[key] = score
            self._counts["peak_pairs"] = max(self._counts["peak_pairs"], len(self._pairs))
        return score

    def snapshot(self) -> dict[str, int]:
        return {**self._counts, "vectors": len(self._vectors), "pairs": len(self._pairs),
                "max_vectors": self.max_vectors, "max_pairs": self.max_pairs}

    def clear(self) -> None:
        self._vectors.clear()
        self._pairs.clear()
