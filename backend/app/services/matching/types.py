from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models import Clause


@dataclass(frozen=True)
class MatchCandidate:
    original: Clause
    compare: Clause
    score: float
    method: str
    details: dict[str, Any]
    sources: tuple[str, ...] = ()


@dataclass
class FlowEdge:
    to: int
    reverse: int
    capacity: int
    cost: int
