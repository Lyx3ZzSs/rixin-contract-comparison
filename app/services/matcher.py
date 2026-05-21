from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - fallback for minimal environments
    fuzz = None

from app.models import Clause, ClausePair
from app.services.clause_splitter import ClauseSplitter
from app.services.normalizer import TextNormalizer


class ClauseMatcher:
    def __init__(self, threshold: int = 85) -> None:
        self.threshold = threshold
        self.normalizer = TextNormalizer()

    def match(self, original: list[Clause], compare: list[Clause]) -> list[ClausePair]:
        pairs: list[ClausePair] = []
        matched_original: set[str] = set()
        matched_compare: set[str] = set()
        consumed_spans: dict[str, list[tuple[int, int]]] = {}

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
