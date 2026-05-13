from __future__ import annotations

import difflib
import re

from app.models import ClausePair, DiffItem
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
        )

    def _build_modify(self, pair: ClausePair, index: int) -> DiffItem:
        left = pair.original
        right = pair.compare
        assert left is not None and right is not None
        original_snippet, compare_snippet = self._changed_snippets(left.text, right.text)
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
        )

    def _changed_snippets(self, left: str, right: str) -> tuple[str, str]:
        left_sentences = self._sentences(left)
        right_sentences = self._sentences(right)
        matcher = difflib.SequenceMatcher(None, left_sentences, right_sentences)
        left_changes: list[str] = []
        right_changes: list[str] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            left_changes.extend(left_sentences[i1:i2])
            right_changes.extend(right_sentences[j1:j2])
        if not left_changes and not right_changes:
            return self._char_level_change(left, right)
        return self._shorten("".join(left_changes) or left), self._shorten("".join(right_changes) or right)

    def _char_level_change(self, left: str, right: str) -> tuple[str, str]:
        matcher = difflib.SequenceMatcher(None, left, right)
        left_parts: list[str] = []
        right_parts: list[str] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "equal":
                left_parts.append(left[i1:i2])
                right_parts.append(right[j1:j2])
        return self._shorten("".join(left_parts) or left), self._shorten("".join(right_parts) or right)

    def _sentences(self, text: str) -> list[str]:
        sentences = [part for part in self.sentence_pattern.split(text) if part.strip()]
        return sentences or [text]

    def _shorten(self, text: str, max_len: int = 220) -> str:
        text = re.sub(r"\s+", " ", text or "").strip()
        if len(text) <= max_len:
            return text
        return f"{text[:max_len]}..."

