from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.models import Clause, ClausePair
from app.services.matching._fuzz import ratio_score


class StructuralDriftMixin:
    def _match_structural_drift_candidates(
        self,
        original: list[Clause],
        compare: list[Clause],
        matched_original: set[str],
        matched_compare: set[str],
        consumed_original_spans: dict[str, list[tuple[int, int]]],
        consumed_compare_spans: dict[str, list[tuple[int, int]]],
    ) -> list[ClausePair]:
        pairs: list[ClausePair] = []
        pairs.extend(
            self._match_many_originals_to_one_compare(
                original,
                compare,
                matched_original,
                matched_compare,
                consumed_compare_spans,
            )
        )
        pairs.extend(
            self._match_one_original_to_many_compares(
                original,
                compare,
                matched_original,
                matched_compare,
                consumed_original_spans,
            )
        )
        return pairs

    def _match_many_originals_to_one_compare(
        self,
        original: list[Clause],
        compare: list[Clause],
        matched_original: set[str],
        matched_compare: set[str],
        consumed_compare_spans: dict[str, list[tuple[int, int]]],
    ) -> list[ClausePair]:
        pairs: list[ClausePair] = []
        synthetic_index = 1
        for right in compare:
            if right.clause_id in matched_compare:
                continue
            matches: list[tuple[Clause, int, int, float]] = []
            local_spans: list[tuple[int, int]] = []
            for left in original:
                if left.clause_id in matched_original:
                    continue
                if (left.section_type or "main_contract") != (right.section_type or "main_contract"):
                    continue
                if not self._eligible_for_containment(left):
                    continue
                span = self._best_span_for_clause_in_text(left, right.text, local_spans)
                if span is None:
                    continue
                start, end, score = span
                matches.append((left, start, end, score))
                local_spans.append((start, end))
            if len(matches) < 2:
                continue
            matches.sort(key=lambda item: (item[1], item[0].order_index, item[0].clause_id))
            group_pairs: list[tuple[ClausePair, Clause, tuple[int, int]]] = []
            for left, start, end, score in matches:
                right_slice = self._slice_clause(right, start, end, f"{right.clause_id}M{synthetic_index:02d}")
                if right_slice is None:
                    continue
                synthetic_index += 1
                group_pairs.append(
                    (
                        ClausePair(
                            original=left,
                            compare=right_slice,
                            score=score,
                            match_method="merged_compare",
                            score_details=self._structural_drift_score_details(
                                score,
                                body_length_coverage=self._body_length_coverage(left.text, right_slice.text),
                                merged=True,
                            ),
                            match_confidence="MEDIUM",
                        ),
                        left,
                        (start, end),
                    )
                )
            if len(group_pairs) < 2:
                continue
            for pair, left, span in group_pairs:
                pairs.append(pair)
                matched_original.add(left.clause_id)
                consumed_compare_spans.setdefault(right.clause_id, []).append(span)
            matched_compare.add(right.clause_id)
        return pairs

    def _match_one_original_to_many_compares(
        self,
        original: list[Clause],
        compare: list[Clause],
        matched_original: set[str],
        matched_compare: set[str],
        consumed_original_spans: dict[str, list[tuple[int, int]]],
    ) -> list[ClausePair]:
        pairs: list[ClausePair] = []
        synthetic_index = 1
        for left in original:
            if left.clause_id in matched_original:
                continue
            matches: list[tuple[Clause, int, int, float]] = []
            local_spans: list[tuple[int, int]] = []
            for right in compare:
                if right.clause_id in matched_compare:
                    continue
                if (left.section_type or "main_contract") != (right.section_type or "main_contract"):
                    continue
                if not self._eligible_for_containment(right):
                    continue
                span = self._best_span_for_clause_in_text(right, left.text, local_spans)
                if span is None:
                    continue
                start, end, score = span
                matches.append((right, start, end, score))
                local_spans.append((start, end))
            if len(matches) < 2:
                continue
            matches.sort(key=lambda item: (item[1], item[0].order_index, item[0].clause_id))
            group_pairs: list[tuple[ClausePair, Clause, tuple[int, int]]] = []
            for right, start, end, score in matches:
                left_slice = self._slice_clause(left, start, end, f"{left.clause_id}P{synthetic_index:02d}")
                if left_slice is None:
                    continue
                synthetic_index += 1
                group_pairs.append(
                    (
                        ClausePair(
                            original=left_slice,
                            compare=right,
                            score=score,
                            match_method="split_original",
                            score_details=self._structural_drift_score_details(
                                score,
                                body_length_coverage=self._body_length_coverage(left_slice.text, right.text),
                                split=True,
                            ),
                            match_confidence="MEDIUM",
                        ),
                        right,
                        (start, end),
                    )
                )
            if len(group_pairs) < 2:
                continue
            for pair, right, span in group_pairs:
                pairs.append(pair)
                matched_compare.add(right.clause_id)
                consumed_original_spans.setdefault(left.clause_id, []).append(span)
            matched_original.add(left.clause_id)
        return pairs

    def _best_span_for_clause_in_text(
        self,
        needle: Clause,
        haystack_text: str,
        existing_spans: list[tuple[int, int]],
    ) -> tuple[int, int, float] | None:
        best: tuple[int, int, float] | None = None
        for needle_text in self._coverage_texts(needle):
            span = self._find_contained_span(needle_text, haystack_text)
            if span is None or self._overlaps_existing(span, existing_spans):
                continue
            if best is None or span[2] > best[2]:
                best = span
        return best

    def _structural_drift_score_details(
        self,
        score: float,
        *,
        body_length_coverage: float,
        merged: bool = False,
        split: bool = False,
    ) -> dict[str, Any]:
        rounded = round(score, 2)
        return {
            "clause_no_score": 0.0,
            "title_score": 0.0,
            "body_score": rounded,
            "body_ratio_score": rounded,
            "body_token_score": rounded,
            "body_partial_score": rounded,
            "body_length_coverage": round(body_length_coverage, 4),
            "position_score": 0.0,
            "neighbor_score": 0.0,
            "contained_span_score": rounded,
            "structural_drift_candidate": 1.0,
            "partial_clause_match": 1.0,
            "merged_compare_clause": 1.0 if merged else 0.0,
            "split_original_clause": 1.0 if split else 0.0,
        }

    def _body_length_coverage(self, left_text: str, right_text: str) -> float:
        left, _ = self._search_text(left_text)
        right, _ = self._search_text(right_text)
        max_len = max(len(left), len(right))
        return min(len(left), len(right)) / max_len if max_len else 1.0

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
            match = self._best_contained_match(right_body, right.section_type, original, consumed_spans)
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

    def _match_unmatched_originals_inside_matched_compare(
        self,
        original: list[Clause],
        compare: list[Clause],
        pairs: list[ClausePair],
        matched_original: set[str],
        consumed_compare_spans: dict[str, list[tuple[int, int]]],
    ) -> list[ClausePair]:
        matched_compare_clauses = [
            pair.compare
            for pair in pairs
            if pair.original is not None and pair.compare is not None
        ]
        if not matched_compare_clauses:
            return []

        synthetic_pairs: list[ClausePair] = []
        synthetic_index = 1
        compare_order = {clause.clause_id: index for index, clause in enumerate(compare)}
        for left in original:
            if left.clause_id in matched_original:
                continue
            if not self._eligible_for_containment(left):
                continue
            match = self._best_compare_containment(left, matched_compare_clauses, consumed_compare_spans)
            if match is None:
                continue
            right, start, end, score = match
            right_slice = self._slice_clause(right, start, end, f"{right.clause_id}S{synthetic_index:02d}")
            if right_slice is None:
                continue
            synthetic_index += 1
            synthetic_pairs.append(
                ClausePair(
                    original=left,
                    compare=right_slice,
                    score=score,
                    match_method="contained_compare",
                    score_details={
                        "clause_no_score": 0.0,
                        "title_score": self._score(left.title, right_slice.title) if left.title and right_slice.title else 0.0,
                        "body_score": round(score, 2),
                        "body_ratio_score": round(score, 2),
                        "body_token_score": round(score, 2),
                        "body_partial_score": round(score, 2),
                        "body_length_coverage": 1.0,
                        "position_score": self._position_score(
                            original.index(left) / max(1, len(original) - 1),
                            compare_order.get(right.clause_id, len(compare)) / max(1, len(compare) - 1),
                        ),
                        "neighbor_score": 0.0,
                        "contained_in_matched_compare": 1.0,
                    },
                    match_confidence="MEDIUM",
                )
            )
            consumed_compare_spans.setdefault(right.clause_id, []).append((start, end))
            matched_original.add(left.clause_id)
        return synthetic_pairs

    def _best_compare_containment(
        self,
        needle: Clause,
        haystacks: list[Clause],
        consumed_compare_spans: dict[str, list[tuple[int, int]]],
    ) -> tuple[Clause, int, int, float] | None:
        best: tuple[Clause, int, int, float] | None = None
        for right in haystacks:
            if (needle.section_type or "main_contract") != (right.section_type or "main_contract"):
                continue
            span = None
            for needle_text in self._coverage_texts(needle):
                span = self._find_contained_span(needle_text, right.text)
                if span is not None:
                    break
            if span is None or self._overlaps_existing(span, consumed_compare_spans.get(right.clause_id, [])):
                continue
            start, end, score = span
            if best is None or score > best[3]:
                best = (right, start, end, score)
        return best

    def _containment_texts(self, clause: Clause) -> list[str]:
        texts = [clause.text, self._strip_clause_prefix_only(clause.text)]
        stripped = (clause.text or "").strip()
        title = (clause.title or "").strip()
        if title and stripped.startswith(title):
            without_title = stripped[len(title):].lstrip("\n\r:： \t")
            if without_title and without_title != stripped:
                texts.append(without_title)
        if title:
            compact_title = self.normalizer.normalize_for_match(title)
            for line in stripped.splitlines():
                compact_line = self.normalizer.normalize_for_match(line)
                if compact_line and compact_line != compact_title and compact_title not in compact_line:
                    texts.append(line)
        return list(dict.fromkeys(text for text in texts if text))

    def _coverage_texts(self, clause: Clause) -> list[str]:
        texts = [*self._containment_texts(clause), *self._paragraph_sentence_fragments(clause.text)]
        return list(dict.fromkeys(text for text in texts if len(self._search_text(text)[0]) >= 8))

    def _paragraph_sentence_fragments(self, text: str) -> list[str]:
        fragments: list[str] = []
        for paragraph in re.split(r"[\n\r]+", text or ""):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            fragments.append(paragraph)
            sentence_parts = re.split(r"(?<=[。；;.!?！？])\s*|[；;]\s*", paragraph)
            for sentence in sentence_parts:
                sentence = sentence.strip()
                if sentence and sentence != paragraph:
                    fragments.append(sentence)
        return fragments

    def _best_contained_match(
        self,
        body: str,
        section_type: str,
        original: list[Clause],
        consumed_spans: dict[str, list[tuple[int, int]]],
    ) -> tuple[Clause, int, int, float] | None:
        best: tuple[Clause, int, int, float] | None = None
        for clause in original:
            if (clause.section_type or "main_contract") != (section_type or "main_contract"):
                continue
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
        min_exact_len = 8 if len(needle) < 20 else 1
        if exact >= 0 and len(needle) >= min_exact_len:
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

    def _eligible_for_containment(self, clause: Clause) -> bool:
        compact_len = len(self._search_text(clause.text)[0])
        if compact_len >= 20:
            return True
        if compact_len < 8:
            return False
        return bool(
            clause.clause_no
            or len(self.normalizer.normalize_for_match(clause.title)) >= 2
            or self._has_segmentation_flag(clause)
        )

    @staticmethod
    def _has_segmentation_flag(clause: Clause) -> bool:
        return any(
            flag in {"PARAGRAPH_MERGED", "WEAK_NUMERIC_MARKER", "WEAK_HEADING", "READING_ORDER_REPAIRED"}
            for flag in clause.split_flags
        )

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
        parsed = self.number_parser.parse_line(first_line)
        if parsed is None:
            return stripped
        first_line_body = first_line[len(parsed.marker_text) :].lstrip("、.． 　")
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
            normalized_text=self.normalizer.normalize_for_diff(text),
            match_text=self.normalizer.normalize_for_match(text),
            page_numbers=clause.page_numbers,
            bboxes=clause.bboxes,
            source_block_ids=clause.source_block_ids,
            char_boxes=char_boxes,
            section_type=clause.section_type,
            section_path=clause.section_path,
            clause_key=clause.clause_key,
            order_index=clause.order_index,
            split_flags=clause.split_flags,
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

        kept_text = "".join(kept_chars)
        text = kept_text.strip()
        if not text:
            return None
        if kept_boxes is not None:
            leading = len(kept_text) - len(kept_text.lstrip())
            kept_boxes = kept_boxes[leading:leading + len(text)]
        return clause.model_copy(
            update={
                "text": text,
                "normalized_text": self.normalizer.normalize_for_diff(text),
                "match_text": self.normalizer.normalize_for_match(text),
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
        return ratio_score(left, right)

    def _title_from_text(self, text: str) -> str:
        first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        return re.sub(r"\s+", " ", first_line)[:40]
