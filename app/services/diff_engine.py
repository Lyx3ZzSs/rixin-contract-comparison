from __future__ import annotations

import difflib
import re

from app.models import ClausePair, DiffItem, TextRange
from app.utils.id_utils import generate_diff_id


class DiffEngine:
    sentence_pattern = re.compile(r"(?<=[。！？!?；;])\s*")

    def build_diffs(self, pairs: list[ClausePair]) -> list[DiffItem]:
        diffs: list[DiffItem] = []
        for pair in pairs:
            if pair.original is None and pair.compare is not None:
                diffs.append(self._build_add(pair, len(diffs) + 1))
            elif pair.compare is None and pair.original is not None:
                diffs.append(self._build_delete(pair, len(diffs) + 1))
            elif pair.original is not None and pair.compare is not None:
                if pair.original.normalized_text == pair.compare.normalized_text:
                    continue
                diffs.append(self._build_modify(pair, len(diffs) + 1))
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
            original_evidence=left.bboxes,
            compare_evidence=right.bboxes,
            original_change_ranges=original_ranges,
            compare_change_ranges=compare_ranges,
        )

    def _changed_snippets(self, left: str, right: str) -> tuple[str, str, list[TextRange], list[TextRange]]:
        matcher = difflib.SequenceMatcher(None, left, right)
        left_ranges: list[TextRange] = []
        right_ranges: list[TextRange] = []
        for hunk in self._change_hunks(matcher.get_opcodes()):
            left_start, left_end = hunk[0][1], hunk[-1][2]
            right_start, right_end = hunk[0][3], hunk[-1][4]
            has_left_change = any(i1 < i2 for tag, i1, i2, _, _ in hunk if tag != "insert")
            has_right_change = any(j1 < j2 for tag, _, _, j1, j2 in hunk if tag != "delete")
            if has_left_change and has_right_change:
                left_ranges.append(self._expand_token_range(left, left_start, left_end, "MODIFY"))
                right_ranges.append(self._expand_token_range(right, right_start, right_end, "MODIFY"))
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
        return max(left_len, right_len) <= 2

    def _expand_token_range(self, text: str, start: int, end: int, highlight_type: str) -> TextRange:
        while start > 0 and self._is_ascii_token_char(text[start - 1]):
            start -= 1
        while end < len(text) and self._is_ascii_token_char(text[end]):
            end += 1
        return TextRange(start=start, end=end, highlight_type=highlight_type)

    def _is_ascii_token_char(self, char: str) -> bool:
        return char.isascii() and (char.isalnum() or char in "._-/%")

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
