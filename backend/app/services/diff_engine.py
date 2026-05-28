from __future__ import annotations

import difflib
import re

from app.models import ClausePair, DiffItem, EvidenceBox, TextRange
from app.services.clause_splitter import ClauseSplitter
from app.utils.id_utils import generate_diff_id

try:
    from rapidfuzz import fuzz as rfuzz
except Exception:
    rfuzz = None


class DiffEngine:
    sentence_pattern = re.compile(r"(?<=[。！？!?；;])\s*")

    def build_diffs(self, pairs: list[ClausePair], start_index: int = 1) -> list[DiffItem]:
        diffs: list[DiffItem] = []
        next_index = start_index
        for pair in pairs:
            if pair.original is None and pair.compare is not None:
                diffs.append(self._build_add(pair, next_index))
                next_index += 1
            elif pair.compare is None and pair.original is not None:
                diffs.append(self._build_delete(pair, next_index))
                next_index += 1
            elif pair.original is not None and pair.compare is not None:
                if pair.original.normalized_text == pair.compare.normalized_text:
                    continue
                diffs.append(self._build_modify(pair, next_index))
                next_index += 1
        return diffs

    def _build_add(self, pair: ClausePair, index: int) -> DiffItem:
        clause = pair.compare
        assert clause is not None
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="ADD",
            compare_clause_id=clause.clause_id,
            clause_no=clause.clause_no,
            title=clause.title,
            compare_text=clause.text,
            compare_snippet=self._shorten(clause.text),
            readable_change=f"新增条款：{self._shorten(clause.text)}",
            source_type="clause",
            match_method=pair.match_method,
            match_score=pair.score or None,
            match_score_details=pair.score_details,
            match_candidates=pair.match_candidates,
            compare_evidence=clause.bboxes,
            compare_change_ranges=[TextRange(start=0, end=len(clause.text), highlight_type="ADD")],
        )

    def _build_delete(self, pair: ClausePair, index: int) -> DiffItem:
        clause = pair.original
        assert clause is not None
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="DELETE",
            original_clause_id=clause.clause_id,
            clause_no=clause.clause_no,
            title=clause.title,
            original_text=clause.text,
            original_snippet=self._shorten(clause.text),
            readable_change=f"删除条款：{self._shorten(clause.text)}",
            source_type="clause",
            match_method=pair.match_method,
            match_score=pair.score or None,
            match_score_details=pair.score_details,
            match_candidates=pair.match_candidates,
            original_evidence=clause.bboxes,
            original_change_ranges=[TextRange(start=0, end=len(clause.text), highlight_type="DELETE")],
        )

    def _build_modify(self, pair: ClausePair, index: int) -> DiffItem:
        left = pair.original
        right = pair.compare
        assert left is not None and right is not None
        original_snippet, compare_snippet, original_ranges, compare_ranges = self._changed_snippets(left.text, right.text)
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="MODIFY",
            original_clause_id=left.clause_id,
            compare_clause_id=right.clause_id,
            clause_no=left.clause_no or right.clause_no,
            title=right.title or left.title,
            original_text=left.text,
            compare_text=right.text,
            original_snippet=original_snippet,
            compare_snippet=compare_snippet,
            readable_change=f"原文：{original_snippet}\n修改后：{compare_snippet}",
            source_type="clause",
            match_score=pair.score,
            match_method=pair.match_method,
            match_score_details=pair.score_details,
            match_candidates=pair.match_candidates,
            review_flags=self._review_flags(pair),
            original_evidence=left.bboxes,
            compare_evidence=right.bboxes,
            original_change_ranges=original_ranges,
            compare_change_ranges=compare_ranges,
        )

    def _review_flags(self, pair: ClausePair) -> list[str]:
        flags: list[str] = []
        body_score = pair.score_details.get("body_score", pair.score)
        if pair.match_method == "same_clause_no_low_similarity":
            flags.append("SAME_CLAUSE_NO_LOW_SIMILARITY")
        if pair.match_method == "renumbered_similarity":
            flags.append("POSSIBLE_RENUMBERED_CLAUSE")
        if body_score < 60 and pair.score < self._low_confidence_match_threshold():
            flags.append("LOW_CONFIDENCE_MATCH")
        return flags

    def _low_confidence_match_threshold(self) -> float:
        return 75.0

    def _changed_snippets(self, left: str, right: str) -> tuple[str, str, list[TextRange], list[TextRange]]:
        left_compacted, left_segments = self._build_compacted_text(left)
        right_compacted, right_segments = self._build_compacted_text(right)

        matcher = difflib.SequenceMatcher(None, left_compacted, right_compacted)
        left_ranges: list[TextRange] = []
        right_ranges: list[TextRange] = []
        for hunk in self._change_hunks(matcher.get_opcodes()):
            c_left_start, c_left_end = hunk[0][1], hunk[-1][2]
            c_right_start, c_right_end = hunk[0][3], hunk[-1][4]

            has_left_change = any(i1 < i2 for tag, i1, i2, _, _ in hunk if tag != "insert")
            has_right_change = any(j1 < j2 for tag, _, _, j1, j2 in hunk if tag != "delete")

            left_start, left_end = self._compacted_range_to_original(
                c_left_start, c_left_end, left_segments
            )
            right_start, right_end = self._compacted_range_to_original(
                c_right_start, c_right_end, right_segments
            )

            left_start, left_end = self._trim_range_whitespace(left, left_start, left_end)
            right_start, right_end = self._trim_range_whitespace(right, right_start, right_end)

            if has_left_change and self._is_whitespace_only(left, left_start, left_end):
                has_left_change = False
            if has_right_change and self._is_whitespace_only(right, right_start, right_end):
                has_right_change = False

            if not has_left_change and not has_right_change:
                continue

            if has_left_change and has_right_change:
                refined_left_ranges, refined_right_ranges = self._refine_changed_ranges(
                    left,
                    left_start,
                    left_end,
                    right,
                    right_start,
                    right_end,
                )
                left_ranges.extend(refined_left_ranges)
                right_ranges.extend(refined_right_ranges)
            elif has_left_change:
                left_ranges.append(self._expand_token_range(left, left_start, left_end, "DELETE"))
            elif has_right_change:
                right_ranges.append(self._expand_token_range(right, right_start, right_end, "ADD"))
        left_ranges = self._merge_ranges(left_ranges)
        right_ranges = self._merge_ranges(right_ranges)
        return (
            self._shorten("".join(left[item.start : item.end] for item in left_ranges)),
            self._shorten("".join(right[item.start : item.end] for item in right_ranges)),
            left_ranges,
            right_ranges,
        )

    def _change_hunks(
        self,
        opcodes: list[tuple[str, int, int, int, int]],
    ) -> list[list[tuple[str, int, int, int, int]]]:
        hunks: list[list[tuple[str, int, int, int, int]]] = []
        current: list[tuple[str, int, int, int, int]] = []
        pending_equal: tuple[str, int, int, int, int] | None = None

        for opcode in opcodes:
            tag, i1, i2, j1, j2 = opcode
            if tag == "equal":
                if current and self._is_short_bridge(i2 - i1, j2 - j1):
                    pending_equal = opcode
                else:
                    if current:
                        hunks.append(current)
                        current = []
                    pending_equal = None
                continue
            if pending_equal is not None:
                current.append(pending_equal)
                pending_equal = None
            current.append(opcode)

        if current:
            hunks.append(current)
        return hunks

    def _is_short_bridge(self, left_len: int, right_len: int) -> bool:
        return max(left_len, right_len) <= 5

    def _refine_changed_ranges(
        self,
        left: str,
        left_start: int,
        left_end: int,
        right: str,
        right_start: int,
        right_end: int,
    ) -> tuple[list[TextRange], list[TextRange]]:
        multiline_ranges = self._refine_multiline_changed_ranges(
            left,
            left_start,
            left_end,
            right,
            right_start,
            right_end,
        )
        if multiline_ranges is not None:
            return multiline_ranges

        if self._should_refine_inline(left[left_start:left_end], right[right_start:right_end]):
            return self._refine_inline_changed_ranges(
                left,
                left_start,
                left_end,
                right,
                right_start,
                right_end,
            )

        return self._coarse_modify_ranges(left, left_start, left_end, right, right_start, right_end)

    def _coarse_modify_ranges(
        self,
        left: str,
        left_start: int,
        left_end: int,
        right: str,
        right_start: int,
        right_end: int,
    ) -> tuple[list[TextRange], list[TextRange]]:
        if self._is_percent_unit_change(left, left_start, left_end, right, right_start, right_end):
            return (
                [self._expand_numeric_unit_range(left, left_start, left_end, "MODIFY")],
                [self._expand_numeric_unit_range(right, right_start, right_end, "MODIFY")],
            )
        return (
            [self._expand_token_range(left, left_start, left_end, "MODIFY", right, right_start)],
            [self._expand_token_range(right, right_start, right_end, "MODIFY", left, left_start)],
        )

    def _should_refine_inline(self, left: str, right: str) -> bool:
        left_compact = re.sub(r"\s+", "", left or "")
        right_compact = re.sub(r"\s+", "", right or "")
        if not left_compact or not right_compact:
            return False
        return (
            left_compact in right_compact
            or right_compact in left_compact
            or self._has_shared_cjk_sequence(left_compact, right_compact)
        )

    def _has_shared_cjk_sequence(self, left: str, right: str) -> bool:
        match = difflib.SequenceMatcher(None, left, right).find_longest_match(0, len(left), 0, len(right))
        if match.size < 2:
            return False
        shared = left[match.a : match.a + match.size]
        return sum(1 for char in shared if "\u4e00" <= char <= "\u9fff") >= 2

    def _refine_inline_changed_ranges(
        self,
        left: str,
        left_start: int,
        left_end: int,
        right: str,
        right_start: int,
        right_end: int,
    ) -> tuple[list[TextRange], list[TextRange]]:
        local_left = left[left_start:left_end]
        local_right = right[right_start:right_end]
        local_left_compacted, local_left_segments = self._build_diff_text(local_left)
        local_right_compacted, local_right_segments = self._build_diff_text(local_right)
        if not local_left_compacted or not local_right_compacted:
            return (
                [self._expand_token_range(left, left_start, left_end, "DELETE")] if local_left_compacted else [],
                [self._expand_token_range(right, right_start, right_end, "ADD")] if local_right_compacted else [],
            )

        matcher = difflib.SequenceMatcher(None, local_left_compacted, local_right_compacted)
        left_ranges: list[TextRange] = []
        right_ranges: list[TextRange] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue

            current_left_start, current_left_end = self._local_range_to_original(
                i1,
                i2,
                local_left_segments,
                left_start,
            )
            current_right_start, current_right_end = self._local_range_to_original(
                j1,
                j2,
                local_right_segments,
                right_start,
            )
            current_left_start, current_left_end = self._trim_range_whitespace(left, current_left_start, current_left_end)
            current_right_start, current_right_end = self._trim_range_whitespace(right, current_right_start, current_right_end)

            has_left = tag != "insert" and current_left_start < current_left_end and not self._is_whitespace_only(left, current_left_start, current_left_end)
            has_right = tag != "delete" and current_right_start < current_right_end and not self._is_whitespace_only(right, current_right_start, current_right_end)
            if has_left and has_right:
                if self._is_percent_unit_change(
                    left,
                    current_left_start,
                    current_left_end,
                    right,
                    current_right_start,
                    current_right_end,
                ):
                    left_ranges.append(self._expand_numeric_unit_range(left, current_left_start, current_left_end, "MODIFY"))
                    right_ranges.append(self._expand_numeric_unit_range(right, current_right_start, current_right_end, "MODIFY"))
                else:
                    left_ranges.append(
                        self._expand_token_range(
                            left,
                            current_left_start,
                            current_left_end,
                            "MODIFY",
                            right,
                            current_right_start,
                        )
                    )
                    right_ranges.append(
                        self._expand_token_range(
                            right,
                            current_right_start,
                            current_right_end,
                            "MODIFY",
                            left,
                            current_left_start,
                        )
                    )
            elif has_left:
                left_ranges.append(self._expand_token_range(left, current_left_start, current_left_end, "DELETE"))
            elif has_right:
                right_ranges.append(self._expand_token_range(right, current_right_start, current_right_end, "ADD"))
        return left_ranges, right_ranges

    def _refine_multiline_changed_ranges(
        self,
        left: str,
        left_start: int,
        left_end: int,
        right: str,
        right_start: int,
        right_end: int,
    ) -> tuple[list[TextRange], list[TextRange]] | None:
        left_lines = self._line_ranges(left, left_start, left_end)
        right_lines = self._line_ranges(right, right_start, right_end)
        if len(left_lines) <= 1 or len(right_lines) <= 1:
            return None
        if any(len(line) > 40 for line, _, _ in [*left_lines, *right_lines]):
            return None

        pairs = self._match_similar_lines(left_lines, right_lines)
        if not pairs:
            return None

        left_ranges: list[TextRange] = []
        right_ranges: list[TextRange] = []
        previous_left = 0
        previous_right = 0
        for left_index, right_index in pairs:
            left_ranges.extend(self._ranges_for_unmatched_lines(left, left_lines, previous_left, left_index, "DELETE"))
            right_ranges.extend(self._ranges_for_unmatched_lines(right, right_lines, previous_right, right_index, "ADD"))

            line_left_ranges, line_right_ranges = self._refine_inline_changed_ranges(
                left,
                left_lines[left_index][1],
                left_lines[left_index][2],
                right,
                right_lines[right_index][1],
                right_lines[right_index][2],
            )
            left_ranges.extend(line_left_ranges)
            right_ranges.extend(line_right_ranges)
            previous_left = left_index + 1
            previous_right = right_index + 1

        left_ranges.extend(self._ranges_for_unmatched_lines(left, left_lines, previous_left, len(left_lines), "DELETE"))
        right_ranges.extend(self._ranges_for_unmatched_lines(right, right_lines, previous_right, len(right_lines), "ADD"))
        return left_ranges, right_ranges

    def _line_ranges(self, text: str, start: int, end: int) -> list[tuple[str, int, int]]:
        ranges: list[tuple[str, int, int]] = []
        cursor = start
        for raw_line in text[start:end].splitlines(keepends=True):
            line = raw_line.rstrip("\r\n")
            stripped = line.strip()
            if stripped:
                line_start = cursor + line.find(stripped)
                line_end = line_start + len(stripped)
                ranges.append((stripped, line_start, line_end))
            cursor += len(raw_line)
        return ranges

    def _match_similar_lines(
        self,
        left_lines: list[tuple[str, int, int]],
        right_lines: list[tuple[str, int, int]],
    ) -> list[tuple[int, int]]:
        pairs: list[tuple[int, int]] = []
        next_right = 0
        for left_index, (left_line, _, _) in enumerate(left_lines):
            best_index: int | None = None
            best_score = 0.0
            for right_index in range(next_right, len(right_lines)):
                right_line = right_lines[right_index][0]
                score = self._line_similarity(left_line, right_line)
                if score > best_score:
                    best_score = score
                    best_index = right_index
            if best_index is None or best_score < 0.5:
                continue
            pairs.append((left_index, best_index))
            next_right = best_index + 1
        return pairs

    def _line_similarity(self, left: str, right: str) -> float:
        left_compact = re.sub(r"\s+", "", left or "")
        right_compact = re.sub(r"\s+", "", right or "")
        if not left_compact or not right_compact:
            return 0.0
        if len(self._shared_meaningful_chars(left_compact, right_compact)) < 2:
            return 0.0
        return difflib.SequenceMatcher(None, left_compact, right_compact).ratio()

    def _shared_meaningful_chars(self, left: str, right: str) -> set[str]:
        return {
            char
            for char in set(left) & set(right)
            if char.isalnum() or "\u4e00" <= char <= "\u9fff"
        }

    def _ranges_for_unmatched_lines(
        self,
        text: str,
        lines: list[tuple[str, int, int]],
        start_index: int,
        end_index: int,
        highlight_type: str,
    ) -> list[TextRange]:
        ranges: list[TextRange] = []
        for _, start, end in lines[start_index:end_index]:
            ranges.append(self._expand_token_range(text, start, end, highlight_type))
        return ranges

    def _local_range_to_original(
        self,
        start: int,
        end: int,
        segments: list[tuple[int, int]],
        offset: int,
    ) -> tuple[int, int]:
        if start >= end or not segments:
            return offset, offset
        local_start, local_end = self._compacted_range_to_original(start, end, segments)
        return offset + local_start, offset + local_end

    def _expand_token_range(
        self,
        text: str,
        start: int,
        end: int,
        highlight_type: str,
        other_text: str | None = None,
        other_pos: int | None = None,
    ) -> TextRange:
        original_start, original_end = start, end
        while start > 0 and self._is_ascii_token_char(text[start - 1]):
            start -= 1
        while end < len(text) and self._is_ascii_token_char(text[end]):
            end += 1

        if other_text is not None and other_pos is not None:
            expanded_left = text[start:original_start]
            if expanded_left:
                search_start = max(0, other_pos - len(expanded_left) - 2)
                search_end = other_pos + len(expanded_left) + 2
                if other_text.find(expanded_left, search_start, search_end) >= 0:
                    start = original_start
            expanded_right = text[original_end:end]
            if expanded_right:
                search_start = max(0, other_pos - 2)
                search_end = other_pos + len(expanded_right) + 2
                if other_text.find(expanded_right, search_start, search_end) >= 0:
                    end = original_end

        return TextRange(start=start, end=end, highlight_type=highlight_type)

    def _is_ascii_token_char(self, char: str) -> bool:
        return char.isascii() and (char.isalnum() or char in "._-/%")

    def _is_percent_unit_change(
        self,
        left: str,
        left_start: int,
        left_end: int,
        right: str,
        right_start: int,
        right_end: int,
    ) -> bool:
        left_unit = left[left_start:left_end]
        right_unit = right[right_start:right_end]
        if {left_unit, right_unit} != {"%", "‰"}:
            return False
        return self._has_numeric_prefix(left, left_start) and self._has_numeric_prefix(right, right_start)

    def _has_numeric_prefix(self, text: str, index: int) -> bool:
        return index > 0 and text[index - 1].isdigit()

    def _expand_numeric_unit_range(self, text: str, start: int, end: int, highlight_type: str) -> TextRange:
        while start > 0 and self._is_number_context_char(text[start - 1]):
            start -= 1
        return TextRange(start=start, end=end, highlight_type=highlight_type)

    def _is_number_context_char(self, char: str) -> bool:
        return char.isdigit() or char in ".,"

    @staticmethod
    def _build_diff_text(text: str) -> tuple[str, list[tuple[int, int]]]:
        result_chars: list[str] = []
        segments: list[tuple[int, int]] = []
        for index, char in enumerate(text):
            if char.isspace():
                continue
            result_chars.append(char)
            segments.append((index, index + 1))
        return "".join(result_chars), segments

    @staticmethod
    def _build_compacted_text(text: str) -> tuple[str, list[tuple[int, int]]]:
        result_chars: list[str] = []
        segments: list[tuple[int, int]] = []
        i = 0
        while i < len(text):
            if text[i] in " \t\n\r":
                ws_start = i
                result_chars.append(" ")
                while i < len(text) and text[i] in " \t\n\r":
                    i += 1
                segments.append((ws_start, i))
            else:
                result_chars.append(text[i])
                segments.append((i, i + 1))
                i += 1
        return "".join(result_chars), segments

    @staticmethod
    def _compacted_range_to_original(
        start: int, end: int, segments: list[tuple[int, int]]
    ) -> tuple[int, int]:
        if start >= end or not segments:
            return 0, 0
        start = max(0, min(start, len(segments) - 1))
        end = max(start + 1, min(end, len(segments)))
        return segments[start][0], segments[end - 1][1]

    @staticmethod
    def _is_whitespace_only(text: str, start: int, end: int) -> bool:
        return all(c in " \t\n\r" for c in text[start:end])

    @staticmethod
    def _trim_range_whitespace(text: str, start: int, end: int) -> tuple[int, int]:
        while start < end and text[start] in " \t\n\r":
            start += 1
        while end > start and text[end - 1] in " \t\n\r":
            end -= 1
        return start, end

    def _merge_ranges(self, ranges: list[TextRange]) -> list[TextRange]:
        if not ranges:
            return []
        ordered = sorted(ranges, key=lambda item: (item.start, item.end))
        merged = [ordered[0]]
        for item in ordered[1:]:
            previous = merged[-1]
            if item.start <= previous.end:
                previous.end = max(previous.end, item.end)
                if previous.highlight_type != item.highlight_type:
                    previous.highlight_type = "MODIFY"
            else:
                merged.append(item)
        return merged

    def _sentences(self, text: str) -> list[str]:
        sentences = [part for part in self.sentence_pattern.split(text) if part.strip()]
        return sentences or [text]

    def _shorten(self, text: str, max_len: int = 220) -> str:
        text = re.sub(r"\s+", " ", text or "").strip()
        if len(text) <= max_len:
            return text
        return f"{text[:max_len]}..."

    # ------------------------------------------------------------------
    # Post-processing: remove overlaps between ADD and DELETE evidence
    # ------------------------------------------------------------------

    def deduplicate_overlaps(self, diffs: list[DiffItem]) -> list[DiffItem]:
        add_diffs = [d for d in diffs if d.diff_type == "ADD"]
        delete_diffs = [d for d in diffs if d.diff_type == "DELETE"]
        modify_diffs = [d for d in diffs if d.diff_type == "MODIFY"]

        # --- Phase 1: ADD vs MODIFY overlap ---
        result: list[DiffItem] = diffs
        if add_diffs and modify_diffs:
            add_bodies: dict[str, str] = {}
            for add in add_diffs:
                add_bodies[add.diff_id] = self._strip_clause_prefix(add.compare_text)

            add_overlap_map: dict[str, list[str]] = {}
            for mod in modify_diffs:
                delete_evidence_text = self._delete_evidence_text(mod)
                if not delete_evidence_text:
                    continue
                for add in add_diffs:
                    body = add_bodies[add.diff_id]
                    if body and len(body) >= 10:
                        matched = self._text_is_contained(body, delete_evidence_text)
                    else:
                        matched = self._title_in_delete_evidence(add, mod)
                    if matched:
                        add_overlap_map.setdefault(mod.diff_id, []).append(add.diff_id)

            if add_overlap_map:
                add_by_id = {d.diff_id: d for d in add_diffs}
                result = []
                for diff in diffs:
                    if diff.diff_type == "ADD" and diff.diff_id in {
                        aid for aids in add_overlap_map.values() for aid in aids
                    }:
                        body = add_bodies.get(diff.diff_id, "")
                        if body and len(body) >= 10:
                            result.append(self._shrink_add_to_prefix(diff))
                        else:
                            result.append(self._shrink_title_only_add(diff))
                        continue
                    if diff.diff_id in add_overlap_map:
                        diff = self._remove_delete_overlap(diff, add_overlap_map[diff.diff_id], add_by_id, add_bodies)
                        if not diff.original_change_ranges:
                            continue
                    result.append(diff)

        # --- Phase 2: DELETE vs MODIFY overlap ---
        if delete_diffs and modify_diffs:
            delete_bodies: dict[str, str] = {}
            for d in delete_diffs:
                delete_bodies[d.diff_id] = self._strip_clause_prefix(d.original_text)

            delete_overlap_map: dict[str, list[str]] = {}
            for mod in modify_diffs:
                add_ev_text = self._add_evidence_text(mod)
                if not add_ev_text:
                    continue
                for d in delete_diffs:
                    body = delete_bodies[d.diff_id]
                    if body and len(body) >= 10:
                        matched = self._text_is_contained(body, add_ev_text)
                    else:
                        matched = self._title_in_compare_evidence(d, mod)
                    if matched:
                        delete_overlap_map.setdefault(mod.diff_id, []).append(d.diff_id)

            if delete_overlap_map:
                delete_by_id = {d.diff_id: d for d in delete_diffs}
                final: list[DiffItem] = []
                for diff in result:
                    if diff.diff_type == "DELETE" and diff.diff_id in {
                        did for dids in delete_overlap_map.values() for did in dids
                    }:
                        body = delete_bodies.get(diff.diff_id, "")
                        if body and len(body) >= 10:
                            final.append(self._shrink_delete_to_prefix(diff))
                        else:
                            final.append(self._shrink_title_only_delete(diff))
                        continue
                    if diff.diff_id in delete_overlap_map:
                        diff = self._remove_compare_overlap(
                            diff, delete_overlap_map[diff.diff_id], delete_by_id, delete_bodies,
                        )
                        if not diff.compare_change_ranges and not diff.original_change_ranges:
                            continue
                    final.append(diff)
                result = final

        return result

    def _strip_clause_prefix(self, text: str) -> str:
        stripped = text.strip()
        match = ClauseSplitter.clause_start_pattern.match(stripped.splitlines()[0] if stripped else "")
        if match:
            end = match.end()
            first_line = stripped.splitlines()[0]
            prefix_len = len(first_line)
            return stripped[prefix_len:].strip()
        return stripped

    def _delete_evidence_text(self, diff: DiffItem) -> str:
        parts: list[str] = []
        for e in diff.original_evidence:
            if e.highlight_type in ("DELETE", "MODIFY"):
                parts.append(e.text)
        return " ".join(parts)

    def _add_evidence_text(self, diff: DiffItem) -> str:
        parts: list[str] = []
        for e in diff.compare_evidence:
            if e.highlight_type in ("ADD", "MODIFY"):
                parts.append(e.text)
        return " ".join(parts)

    def _text_overlap_score(self, left: str, right: str) -> float:
        if rfuzz is not None:
            return float(rfuzz.token_set_ratio(left, right))
        return SequenceMatcher(None, left, right).ratio() * 100

    def _text_is_contained(self, needle: str, haystack: str) -> bool:
        if rfuzz is not None:
            return float(rfuzz.partial_ratio(needle, haystack)) >= 85
        norm_needle = re.sub(r"\s+", "", needle)
        norm_hay = re.sub(r"\s+", "", haystack)
        return norm_needle in norm_hay

    def _title_in_delete_evidence(self, add: DiffItem, mod: DiffItem) -> bool:
        title = add.title or add.compare_snippet
        if not title or len(title) < 4:
            return False
        for e in mod.original_evidence:
            if e.highlight_type in ("DELETE", "MODIFY") and self._text_is_contained(title, e.text):
                return True
        return False

    def _title_in_compare_evidence(self, delete: DiffItem, mod: DiffItem) -> bool:
        title = delete.title or delete.original_snippet
        if not title or len(title) < 4:
            return False
        for e in mod.compare_evidence:
            if e.highlight_type in ("ADD", "MODIFY") and self._text_is_contained(title, e.text):
                return True
        return False

    def _shrink_add_to_prefix(self, diff: DiffItem) -> DiffItem:
        text = diff.compare_text
        first_line = text.strip().splitlines()[0] if text.strip() else ""
        match = ClauseSplitter.clause_start_pattern.match(first_line)
        if not match:
            return diff
        prefix_len = len(match.group(1))
        prefix = first_line[:prefix_len]

        prefix_evidence = [
            e for e in diff.compare_evidence
            if e.highlight_type == "ADD" and len(e.text) <= prefix_len + 5
        ]
        if not prefix_evidence:
            prefix_evidence = diff.compare_evidence[:1]

        return diff.model_copy(update={
            "compare_snippet": prefix,
            "readable_change": f"缺失编号：{prefix}",
            "compare_change_ranges": [TextRange(start=0, end=prefix_len, highlight_type="ADD")],
            "compare_evidence": prefix_evidence,
        })

    def _shrink_title_only_add(self, diff: DiffItem) -> DiffItem:
        title = diff.title or diff.compare_snippet
        if not title:
            return diff
        title_len = len(title)
        return diff.model_copy(update={
            "compare_snippet": title,
            "readable_change": f"缺失编号：{title}",
            "compare_change_ranges": [TextRange(start=0, end=title_len, highlight_type="ADD")],
        })

    def _shrink_delete_to_prefix(self, diff: DiffItem) -> DiffItem:
        text = diff.original_text
        first_line = text.strip().splitlines()[0] if text.strip() else ""
        match = ClauseSplitter.clause_start_pattern.match(first_line)
        if not match:
            return diff
        prefix_len = len(match.group(1))
        prefix = first_line[:prefix_len]

        prefix_evidence = [
            e for e in diff.original_evidence
            if e.highlight_type in ("DELETE", "MODIFY") and len(e.text) <= prefix_len + 5
        ]
        if not prefix_evidence:
            prefix_evidence = diff.original_evidence[:1]
        return diff.model_copy(update={
            "original_snippet": prefix,
            "readable_change": f"缺失编号：{prefix}",
            "original_change_ranges": [TextRange(start=0, end=prefix_len, highlight_type="DELETE")],
            "original_evidence": prefix_evidence,
        })

    def _shrink_title_only_delete(self, diff: DiffItem) -> DiffItem:
        title = diff.title or diff.original_snippet
        if not title:
            return diff
        title_len = len(title)
        return diff.model_copy(update={
            "original_snippet": title,
            "readable_change": f"缺失编号：{title}",
            "original_change_ranges": [TextRange(start=0, end=title_len, highlight_type="DELETE")],
        })

    def _remove_delete_overlap(
        self,
        diff: DiffItem,
        add_ids: list[str],
        add_by_id: dict[str, DiffItem],
        add_bodies: dict[str, str],
    ) -> DiffItem:
        delete_texts: set[str] = set()
        for aid in add_ids:
            body = add_bodies.get(aid, "")
            if body:
                delete_texts.add(re.sub(r"\s+", "", body))
            add_diff = add_by_id.get(aid)
            if add_diff:
                title = add_diff.title or add_diff.compare_snippet
                if title:
                    delete_texts.add(re.sub(r"\s+", "", title))

        filtered_evidence = [
            e for e in diff.original_evidence
            if e.highlight_type not in ("DELETE", "MODIFY")
            or not self._evidence_in_set(e, delete_texts)
        ]

        remaining_ranges = [
            r for r in diff.original_change_ranges
            if r.highlight_type != "DELETE" or not self._range_text_in_set(diff.original_text, r, delete_texts)
        ]

        return diff.model_copy(update={
            "original_evidence": filtered_evidence,
            "original_change_ranges": remaining_ranges or self._rebuild_ranges_from_evidence(filtered_evidence, diff.original_text),
            "original_snippet": self._rebuild_snippet(diff.original_text, remaining_ranges),
            "readable_change": self._rebuild_readable(diff.original_text, diff.compare_text, remaining_ranges, diff.compare_change_ranges),
        })

    def _remove_compare_overlap(
        self,
        diff: DiffItem,
        delete_ids: list[str],
        delete_by_id: dict[str, DiffItem],
        delete_bodies: dict[str, str],
    ) -> DiffItem:
        overlap_texts: set[str] = set()
        for did in delete_ids:
            body = delete_bodies.get(did, "")
            if body:
                overlap_texts.add(re.sub(r"\s+", "", body))
            delete_diff = delete_by_id.get(did)
            if delete_diff:
                title = delete_diff.title or delete_diff.original_snippet
                if title:
                    overlap_texts.add(re.sub(r"\s+", "", title))

        filtered_evidence = [
            e for e in diff.compare_evidence
            if e.highlight_type not in ("ADD", "MODIFY")
            or not self._evidence_in_set(e, overlap_texts)
        ]

        remaining_ranges = [
            r for r in diff.compare_change_ranges
            if r.highlight_type != "ADD" or not self._range_text_in_set(diff.compare_text, r, overlap_texts)
        ]

        return diff.model_copy(update={
            "compare_evidence": filtered_evidence,
            "compare_change_ranges": remaining_ranges or self._rebuild_ranges_from_evidence(filtered_evidence, diff.compare_text),
            "compare_snippet": self._rebuild_snippet(diff.compare_text, remaining_ranges),
            "readable_change": self._rebuild_readable(diff.original_text, diff.compare_text, diff.original_change_ranges, remaining_ranges),
        })

    def _evidence_in_set(self, evidence: "EvidenceBox", delete_texts: set[str]) -> bool:
        compact = re.sub(r"\s+", "", evidence.text)
        if not compact:
            return False
        for dt in delete_texts:
            if dt and (compact in dt or dt in compact):
                return True
            if self._text_overlap_score(compact, dt) >= 80:
                return True
        return False

    def _range_text_in_set(self, text: str, range_: TextRange, delete_texts: set[str]) -> bool:
        fragment = re.sub(r"\s+", "", text[range_.start:range_.end])
        if not fragment:
            return False
        for dt in delete_texts:
            if dt and len(fragment) >= 10 and (fragment in dt or dt in fragment):
                return True
        return False

    def _rebuild_ranges_from_evidence(self, evidence: list["EvidenceBox"], text: str) -> list[TextRange]:
        ranges: list[TextRange] = []
        for e in evidence:
            if e.highlight_type is not None:
                idx = text.find(e.text[:20]) if len(e.text) >= 20 else text.find(e.text)
                if idx >= 0:
                    ranges.append(TextRange(start=idx, end=idx + len(e.text), highlight_type=e.highlight_type))
        return ranges

    def _rebuild_snippet(self, text: str, ranges: list[TextRange]) -> str:
        if not ranges:
            return ""
        return self._shorten("".join(text[r.start:r.end] for r in ranges))

    def _rebuild_readable(self, original: str, compare: str, orig_ranges: list[TextRange], comp_ranges: list[TextRange]) -> str:
        orig_part = self._shorten("".join(original[r.start:r.end] for r in orig_ranges)) if orig_ranges else ""
        comp_part = self._shorten("".join(compare[r.start:r.end] for r in comp_ranges)) if comp_ranges else ""
        return f"原文：{orig_part}\n修改后：{comp_part}"
