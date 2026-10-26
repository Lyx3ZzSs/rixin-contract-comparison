from __future__ import annotations

from collections.abc import Callable, Mapping
from difflib import SequenceMatcher

try:
    from rapidfuzz import fuzz, process
except Exception:  # pragma: no cover - fallback for minimal environments
    fuzz = None
    process = None


ScoreFn = Callable[[str, str], float]


def token_score(left: str, right: str) -> float:
    if not left and not right:
        return 100.0
    if not left or not right:
        return 0.0
    if fuzz is not None:
        return float(fuzz.token_set_ratio(left, right))
    return SequenceMatcher(None, left, right).ratio() * 100


def ratio_score(left: str, right: str) -> float:
    if not left and not right:
        return 100.0
    if not left or not right:
        return 0.0
    if fuzz is not None:
        return float(fuzz.ratio(left, right))
    return SequenceMatcher(None, left, right).ratio() * 100


def partial_score(left: str, right: str) -> float:
    if not left and not right:
        return 100.0
    if not left or not right:
        return 0.0
    if fuzz is not None:
        return float(fuzz.partial_ratio(left, right))
    return SequenceMatcher(None, left, right).ratio() * 100


def top_k_indices(
    query: str,
    choices: Mapping[int, str],
    *,
    limit: int,
    score_cutoff: float,
    scorer: ScoreFn,
    rapidfuzz_scorer: Callable[..., float] | None,
) -> list[int]:
    if not query or limit <= 0:
        return []
    filtered_choices = {index: choice for index, choice in choices.items() if choice}
    if not filtered_choices:
        return []
    if process is not None and rapidfuzz_scorer is not None:
        matches = process.extract(
            query,
            filtered_choices,
            scorer=rapidfuzz_scorer,
            processor=None,
            limit=limit,
            score_cutoff=score_cutoff,
        )
        return [int(index) for _, _, index in matches]

    scored: list[tuple[float, int]] = []
    for index, choice in filtered_choices.items():
        score = scorer(query, choice)
        if score >= score_cutoff:
            scored.append((score, index))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [index for _, index in scored[:limit]]


RAPIDFUZZ_RATIO = fuzz.ratio if fuzz is not None else None
RAPIDFUZZ_TOKEN_SET = fuzz.token_set_ratio if fuzz is not None else None
