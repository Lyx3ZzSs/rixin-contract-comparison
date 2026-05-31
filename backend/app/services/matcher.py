from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - fallback for minimal environments
    fuzz = None

from app.models import Clause, ClausePair
from app.services.clause_splitter import ClauseSplitter
from app.services.normalizer import TextNormalizer


@dataclass(frozen=True)
class MatchCandidate:
    original: Clause
    compare: Clause
    score: float
    method: str
    details: dict[str, float]


class ClauseMatcher:
    def __init__(self, threshold: int = 85, use_prefilter: bool = True) -> None:
        self.threshold = threshold
        self.normalizer = TextNormalizer()
        self._use_prefilter = use_prefilter

    def match(self, original: list[Clause], compare: list[Clause]) -> list[ClausePair]:
        pairs: list[ClausePair] = []
        matched_original: set[str] = set()
        matched_compare: set[str] = set()
        consumed_spans: dict[str, list[tuple[int, int]]] = {}

        all_candidates = self._build_candidates(original, compare)
        candidates_by_original = self._candidates_by_original(all_candidates)
        for candidate in all_candidates:
            if candidate.original.clause_id in matched_original or candidate.compare.clause_id in matched_compare:
                continue
            if not self._candidate_acceptable(candidate):
                continue
            pairs.append(
                ClausePair(
                    original=candidate.original,
                    compare=candidate.compare,
                    score=candidate.score,
                    match_method=candidate.method,
                    score_details=candidate.details,
                    match_candidates=self._candidate_summaries(candidates_by_original[candidate.original.clause_id]),
                )
            )
            matched_original.add(candidate.original.clause_id)
            matched_compare.add(candidate.compare.clause_id)

        pairs.sort(
            key=lambda pair: (
                original.index(pair.original) if pair.original in original else len(original),
                compare.index(pair.compare) if pair.compare in compare else len(compare),
            )
        )

        synthetic_pairs = self._match_contained_numbered_clauses(
            original,
            compare,
            matched_compare,
            consumed_spans,
        )
        if consumed_spans:
            pairs = [
                pair.model_copy(update={"original": self._redact_clause(pair.original, consumed_spans)})
                if pair.original is not None and pair.original.clause_id in consumed_spans
                else pair
                for pair in pairs
            ]
        pairs.extend(synthetic_pairs)

        for left in original:
            if left.clause_id not in matched_original:
                redacted = self._redact_clause(left, consumed_spans)
                if redacted is not None:
                    pairs.append(ClausePair(original=redacted, compare=None, match_method="delete"))
        for right in compare:
            if right.clause_id not in matched_compare:
                pairs.append(ClausePair(original=None, compare=right, match_method="add"))

        return pairs

    def _build_candidates(self, original: list[Clause], compare: list[Clause]) -> list[MatchCandidate]:
        n, m = len(original), len(compare)
        if not self._use_prefilter or n * m < 100:
            return self._build_candidates_exhaustive(original, compare)
        return self._build_candidates_prefiltered(original, compare)

    def _build_candidates_exhaustive(self, original: list[Clause], compare: list[Clause]) -> list[MatchCandidate]:
        candidates: list[MatchCandidate] = []
        original_count = max(1, len(original) - 1)
        compare_count = max(1, len(compare) - 1)
        for original_index, left in enumerate(original):
            for compare_index, right in enumerate(compare):
                details = self._score_details(left, right, original_index, compare_index, original, compare, original_count, compare_count)
                score = self._weighted_score(details)
                method = self._match_method(left, right, details, score)
                candidates.append(MatchCandidate(left, right, score, method, details))
        candidates.sort(
            key=lambda item: (
                item.score,
                item.details["body_score"],
                item.details["clause_no_score"],
                item.details["title_score"],
            ),
            reverse=True,
        )
        return candidates

    def _build_candidates_prefiltered(self, original: list[Clause], compare: list[Clause]) -> list[MatchCandidate]:
        compare_no_index = self._build_clause_no_index(compare)
        original_count = max(1, len(original) - 1)
        compare_count = max(1, len(compare) - 1)
        compare_indices_by_id = {id(c): i for i, c in enumerate(compare)}
        position_window = 0.15
        index_window = 3

        candidates: list[MatchCandidate] = []
        for original_index, left in enumerate(original):
            candidate_compare_indices: set[int] = set()

            left_norm = self._normalize_clause_no(left.clause_no)
            if left_norm and left_norm in compare_no_index:
                candidate_compare_indices.update(compare_no_index[left_norm])

            orig_ratio = original_index / original_count
            for ci, right in enumerate(compare):
                if abs(ci / compare_count - orig_ratio) <= position_window:
                    candidate_compare_indices.add(ci)

            approx_ci = round(orig_ratio * compare_count)
            for ci in range(max(0, approx_ci - index_window), min(len(compare), approx_ci + index_window + 1)):
                candidate_compare_indices.add(ci)

            if not candidate_compare_indices:
                candidate_compare_indices = set(range(len(compare)))

            for compare_index in candidate_compare_indices:
                right = compare[compare_index]
                details = self._score_details(left, right, original_index, compare_index, original, compare, original_count, compare_count)
                score = self._weighted_score(details)
                method = self._match_method(left, right, details, score)
                candidates.append(MatchCandidate(left, right, score, method, details))

        candidates.sort(
            key=lambda item: (
                item.score,
                item.details["body_score"],
                item.details["clause_no_score"],
                item.details["title_score"],
            ),
            reverse=True,
        )
        return candidates

    def _build_clause_no_index(self, clauses: list[Clause]) -> dict[str, list[int]]:
        index: dict[str, list[int]] = {}
        for i, clause in enumerate(clauses):
            norm = self._normalize_clause_no(clause.clause_no)
            if norm:
                index.setdefault(norm, []).append(i)
        return index

    def _score_details(
        self,
        left: Clause,
        right: Clause,
        original_index: int,
        compare_index: int,
        original: list[Clause],
        compare: list[Clause],
        original_count: int,
        compare_count: int,
    ) -> dict[str, float]:
        title_score = self._score(left.title, right.title) if left.title and right.title else 0.0
        body_score = self._score(left.normalized_text, right.normalized_text)
        clause_no_score = self._clause_no_score(left.clause_no, right.clause_no)
        position_score = self._position_score(original_index / original_count, compare_index / compare_count)
        neighbor_score = self._neighbor_score(original, compare, original_index, compare_index)
        return {
            "clause_no_score": round(clause_no_score, 2),
            "title_score": round(title_score, 2),
            "body_score": round(body_score, 2),
            "position_score": round(position_score, 2),
            "neighbor_score": round(neighbor_score, 2),
        }

    def _weighted_score(self, details: dict[str, float]) -> float:
        weighted = (
            details["clause_no_score"] * 0.25
            + details["title_score"] * 0.20
            + details["body_score"] * 0.40
            + details["position_score"] * 0.10
            + details["neighbor_score"] * 0.05
        )
        if details["body_score"] >= self.threshold:
            weighted = max(weighted, details["body_score"])
        return round(weighted, 2)

    def _candidate_acceptable(self, candidate: MatchCandidate) -> bool:
        details = candidate.details
        same_clause_no = self._same_clause_no(candidate.original.clause_no, candidate.compare.clause_no)
        if same_clause_no:
            return candidate.score >= 35 or details["body_score"] >= 45 or details["title_score"] >= 80
        if details["body_score"] >= self.threshold:
            return True
        if candidate.score >= min(self.threshold, 78) and details["body_score"] >= 55:
            return True
        return bool(details["title_score"] >= 92 and details["body_score"] >= 60)

    def _match_method(self, left: Clause, right: Clause, details: dict[str, float], score: float) -> str:
        same_clause_no = self._same_clause_no(left.clause_no, right.clause_no)
        if same_clause_no and details["body_score"] < 55:
            return "same_clause_no_low_similarity"
        if same_clause_no:
            return "same_clause_no_weighted"
        if left.clause_no and right.clause_no and details["body_score"] >= 70:
            return "renumbered_similarity"
        if details["title_score"] > details["body_score"] and score >= min(self.threshold, 78):
            return "title_weighted_similarity"
        return "body_weighted_similarity"

    def _candidates_by_original(self, candidates: list[MatchCandidate]) -> dict[str, list[MatchCandidate]]:
        grouped: dict[str, list[MatchCandidate]] = {}
        for candidate in candidates:
            grouped.setdefault(candidate.original.clause_id, []).append(candidate)
        return grouped

    def _candidate_summaries(self, candidates: list[MatchCandidate]) -> list[dict[str, object]]:
        result = []
        for candidate in candidates[:5]:
            result.append(
                {
                    "compare_clause_id": candidate.compare.clause_id,
                    "compare_clause_no": candidate.compare.clause_no,
                    "score": round(candidate.score, 2),
                    "match_method": candidate.method,
                    "score_details": candidate.details,
                }
            )
        return result

    def _clause_no_score(self, left: str, right: str) -> float:
        left_norm = self._normalize_clause_no(left)
        right_norm = self._normalize_clause_no(right)
        if left_norm and right_norm and left_norm == right_norm:
            return 100.0
        if not left_norm or not right_norm:
            return 0.0
        left_parts = left_norm.split(".")
        right_parts = right_norm.split(".")
        if left_parts[-1:] == right_parts[-1:] and len(left_parts) == len(right_parts):
            return 70.0
        if len(left_parts) == len(right_parts):
            return 40.0
        return self._score(left_norm, right_norm) * 0.5

    def _same_clause_no(self, left: str, right: str) -> bool:
        left_norm = self._normalize_clause_no(left)
        right_norm = self._normalize_clause_no(right)
        return bool(left_norm and right_norm and left_norm == right_norm)

    def _normalize_clause_no(self, value: str) -> str:
        value = unicodedata.normalize("NFKC", value or "").strip()
        value = value.strip("、. ")
        value = value.replace("第", "").replace("条", "").replace("章", "").replace("节", "")
        value = value.strip("（）()")
        chinese_digits = {
            "一": "1",
            "二": "2",
            "三": "3",
            "四": "4",
            "五": "5",
            "六": "6",
            "七": "7",
            "八": "8",
            "九": "9",
            "十": "10",
        }
        return chinese_digits.get(value, value.lower())

    def _position_score(self, left_ratio: float, right_ratio: float) -> float:
        distance = abs(left_ratio - right_ratio)
        return max(0.0, 100.0 - distance * 180.0)

    def _neighbor_score(
        self,
        original: list[Clause],
        compare: list[Clause],
        original_index: int,
        compare_index: int,
    ) -> float:
        scores: list[float] = []
        if original_index > 0 and compare_index > 0:
            scores.append(self._clause_no_score(original[original_index - 1].clause_no, compare[compare_index - 1].clause_no))
        if original_index + 1 < len(original) and compare_index + 1 < len(compare):
            scores.append(self._clause_no_score(original[original_index + 1].clause_no, compare[compare_index + 1].clause_no))
        return sum(scores) / len(scores) if scores else 0.0

    def _score(self, left: str, right: str) -> float:
        if not left and not right:
            return 100.0
        if not left or not right:
            return 0.0
        if fuzz is not None:
            return float(fuzz.token_set_ratio(left, right))
        return SequenceMatcher(None, left, right).ratio() * 100

    def _match_contained_numbered_clauses(
        self,
        original: list[Clause],
        compare: list[Clause],
        matched_compare: set[str],
        consumed_spans: dict[str, list[tuple[int, int]]],
    ) -> list[ClausePair]:
        pairs: list[ClausePair] = []
        synthetic_index = 1
        for right in compare:
            if right.clause_id in matched_compare or not right.clause_no:
                continue
            right_body = self._strip_clause_prefix_only(right.text)
            if len(self._search_text(right_body)[0]) < 4:
                continue
            match = self._best_contained_match(right_body, original, consumed_spans)
            if match is None:
                continue
            parent, start, end, score = match
            left = self._slice_clause(parent, start, end, f"{parent.clause_id}S{synthetic_index:02d}")
            if left is None:
                continue
            synthetic_index += 1
            pairs.append(
                ClausePair(
                    original=left,
                    compare=right,
                    score=score,
                    match_method="contained_original",
                    score_details={
                        "clause_no_score": 0.0,
                        "title_score": self._score(left.title, right.title) if left.title and right.title else 0.0,
                        "body_score": round(score, 2),
                        "position_score": 0.0,
                        "neighbor_score": 0.0,
                    },
                )
            )
            consumed_spans.setdefault(parent.clause_id, []).append((start, end))
            matched_compare.add(right.clause_id)
        return pairs

    def _best_contained_match(
        self,
        body: str,
        original: list[Clause],
        consumed_spans: dict[str, list[tuple[int, int]]],
    ) -> tuple[Clause, int, int, float] | None:
        best: tuple[Clause, int, int, float] | None = None
        for clause in original:
            span = self._find_contained_span(body, clause.text)
            if span is None or self._overlaps_existing(span, consumed_spans.get(clause.clause_id, [])):
                continue
            start, end, score = span
            if best is None or score > best[3]:
                best = (clause, start, end, score)
        return best

    def _find_contained_span(self, needle_text: str, haystack_text: str) -> tuple[int, int, float] | None:
        needle, _ = self._search_text(needle_text)
        haystack, haystack_map = self._search_text(haystack_text)
        if not needle or not haystack:
            return None
        if len(haystack) <= len(needle) + 8:
            return None

        exact = haystack.find(needle)
        if exact >= 0:
            return self._source_span(exact, exact + len(needle), haystack_map, 100.0)

        if len(needle) < 20:
            return None

        anchor_len = min(16, max(8, len(needle) // 8))
        start_anchor = needle[:anchor_len]
        end_anchor = needle[-anchor_len:]
        start_index = haystack.find(start_anchor)
        if start_index < 0:
            return None
        end_anchor_index = haystack.find(end_anchor, start_index + anchor_len)
        if end_anchor_index < 0:
            return None
        compact_end = end_anchor_index + anchor_len
        candidate = haystack[start_index:compact_end]
        score = self._ratio(needle, candidate)
        if score < self.threshold:
            return None
        return self._source_span(start_index, compact_end, haystack_map, score)

    def _source_span(
        self,
        compact_start: int,
        compact_end: int,
        index_map: list[int],
        score: float,
    ) -> tuple[int, int, float] | None:
        if compact_start >= compact_end or compact_end > len(index_map):
            return None
        start = index_map[compact_start]
        end = index_map[compact_end - 1] + 1
        return start, end, score

    def _strip_clause_prefix_only(self, text: str) -> str:
        stripped = (text or "").strip()
        if not stripped:
            return ""
        lines = stripped.splitlines()
        first_line = lines[0]
        match = ClauseSplitter.clause_start_pattern.match(first_line)
        if not match:
            return stripped
        first_line_body = first_line[match.end(1):].lstrip("、. 　")
        return "\n".join([first_line_body, *lines[1:]]).strip()

    def _slice_clause(self, clause: Clause, start: int, end: int, clause_id: str) -> Clause | None:
        while start < end and clause.text[start].isspace():
            start += 1
        while end > start and clause.text[end - 1].isspace():
            end -= 1
        if start >= end:
            return None
        text = clause.text[start:end]
        char_boxes = clause.char_boxes[start:end] if len(clause.char_boxes) == len(clause.text) else []
        return Clause(
            clause_id=clause_id,
            clause_no="",
            title=self._title_from_text(text),
            text=text,
            normalized_text=self.normalizer.normalize_for_match(text),
            page_numbers=clause.page_numbers,
            bboxes=clause.bboxes,
            source_block_ids=clause.source_block_ids,
            char_boxes=char_boxes,
        )

    def _redact_clause(self, clause: Clause | None, consumed_spans: dict[str, list[tuple[int, int]]]) -> Clause | None:
        if clause is None:
            return None
        spans = self._merge_spans(consumed_spans.get(clause.clause_id, []), len(clause.text))
        if not spans:
            return clause

        kept_chars: list[str] = []
        kept_boxes = [] if len(clause.char_boxes) == len(clause.text) else None
        span_index = 0
        for index, char in enumerate(clause.text):
            while span_index < len(spans) and index >= spans[span_index][1]:
                span_index += 1
            if span_index < len(spans) and spans[span_index][0] <= index < spans[span_index][1]:
                continue
            kept_chars.append(char)
            if kept_boxes is not None:
                kept_boxes.append(clause.char_boxes[index])

        text = "".join(kept_chars).strip()
        if not text:
            return None
        if kept_boxes is not None:
            leading = len("".join(kept_chars)) - len("".join(kept_chars).lstrip())
            trailing_text = "".join(kept_chars).strip()
            kept_boxes = kept_boxes[leading:leading + len(trailing_text)]
        return clause.model_copy(
            update={
                "text": text,
                "normalized_text": self.normalizer.normalize_for_match(text),
                "title": clause.title if text.startswith(clause.title) else self._title_from_text(text),
                "char_boxes": kept_boxes if kept_boxes is not None else [],
            }
        )

    def _merge_spans(self, spans: list[tuple[int, int]], max_len: int) -> list[tuple[int, int]]:
        cleaned = sorted((max(0, start), min(max_len, end)) for start, end in spans if start < end)
        if not cleaned:
            return []
        merged = [cleaned[0]]
        for start, end in cleaned[1:]:
            prev_start, prev_end = merged[-1]
            if start <= prev_end:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))
        return merged

    def _overlaps_existing(self, span: tuple[int, int, float], existing: list[tuple[int, int]]) -> bool:
        start, end, _ = span
        return any(start < right and left < end for left, right in existing)

    def _search_text(self, text: str) -> tuple[str, list[int]]:
        chars: list[str] = []
        index_map: list[int] = []
        for index, char in enumerate(text or ""):
            normalized = unicodedata.normalize("NFKC", char).lower()
            if not normalized or normalized.isspace():
                continue
            for normalized_char in normalized:
                if normalized_char.isspace():
                    continue
                chars.append(normalized_char)
                index_map.append(index)
        return "".join(chars), index_map

    def _ratio(self, left: str, right: str) -> float:
        if fuzz is not None:
            return float(fuzz.ratio(left, right))
        return SequenceMatcher(None, left, right).ratio() * 100

    def _title_from_text(self, text: str) -> str:
        first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        return re.sub(r"\s+", " ", first_line)[:40]
