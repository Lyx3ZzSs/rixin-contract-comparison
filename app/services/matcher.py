from __future__ import annotations

from difflib import SequenceMatcher

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - fallback for minimal environments
    fuzz = None

from app.models import Clause, ClausePair


class ClauseMatcher:
    def __init__(self, threshold: int = 85) -> None:
        self.threshold = threshold

    def match(self, original: list[Clause], compare: list[Clause]) -> list[ClausePair]:
        pairs: list[ClausePair] = []
        matched_original: set[str] = set()
        matched_compare: set[str] = set()

        compare_by_no = {}
        for clause in compare:
            if clause.clause_no:
                compare_by_no.setdefault(clause.clause_no, []).append(clause)

        for left in original:
            if not left.clause_no or left.clause_id in matched_original:
                continue
            candidates = [c for c in compare_by_no.get(left.clause_no, []) if c.clause_id not in matched_compare]
            if candidates:
                right = max(candidates, key=lambda c: self._score(left.normalized_text, c.normalized_text))
                pairs.append(
                    ClausePair(
                        original=left,
                        compare=right,
                        score=self._score(left.normalized_text, right.normalized_text),
                        match_method="clause_no",
                    )
                )
                matched_original.add(left.clause_id)
                matched_compare.add(right.clause_id)

        for left in original:
            if left.clause_id in matched_original:
                continue
            best_clause = None
            best_score = 0.0
            best_method = "body_similarity"
            for right in compare:
                if right.clause_id in matched_compare:
                    continue
                title_score = self._score(left.title, right.title) if left.title and right.title else 0
                body_score = self._score(left.normalized_text, right.normalized_text)
                score = max(title_score, body_score)
                method = "title_similarity" if title_score >= body_score else "body_similarity"
                if score > best_score:
                    best_clause = right
                    best_score = score
                    best_method = method
            if best_clause is not None and best_score >= self.threshold:
                pairs.append(ClausePair(original=left, compare=best_clause, score=best_score, match_method=best_method))
                matched_original.add(left.clause_id)
                matched_compare.add(best_clause.clause_id)

        for left in original:
            if left.clause_id not in matched_original:
                pairs.append(ClausePair(original=left, compare=None, match_method="delete"))
        for right in compare:
            if right.clause_id not in matched_compare:
                pairs.append(ClausePair(original=None, compare=right, match_method="add"))

        return pairs

    def _score(self, left: str, right: str) -> float:
        if not left and not right:
            return 100.0
        if not left or not right:
            return 0.0
        if fuzz is not None:
            return float(fuzz.token_set_ratio(left, right))
        return SequenceMatcher(None, left, right).ratio() * 100

