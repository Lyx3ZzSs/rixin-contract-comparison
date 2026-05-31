"""Flat text comparison fallback for table blocks without HTML."""

from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher

from app.models import DiffItem, EvidenceBox, TextBlock
from app.services.table_compare.types import _FlatUnit
from app.services.table_compare import utils
from app.utils.id_utils import generate_diff_id


class FlatTextComparator:
    """Compares table blocks as flat text when structured parsing is unavailable."""

    def flat_compare(
        self,
        original_blocks: list[tuple[TextBlock, str]],
        compare_blocks: list[tuple[TextBlock, str]],
        start_index: int,
        warnings: list[str],
    ) -> tuple[list[DiffItem], list[str]]:
        original_units = self._extract_flat_units(original_blocks)
        compare_units = self._extract_flat_units(compare_blocks)
        if not original_units and not compare_units:
            return [], warnings

        original_unmatched, compare_unmatched = self._unmatched_units(original_units, compare_units)
        if not original_unmatched and not compare_unmatched:
            return [], warnings
        if len(original_unmatched) + len(compare_unmatched) > 8:
            warnings.append("表格抽取顺序差异较大，已跳过大范围表格字符级高亮。")
            return [], warnings

        diffs: list[DiffItem] = []
        next_index = start_index
        paired_original: set[int] = set()
        paired_compare: set[int] = set()

        for left_index, right_index, score in self._similar_pairs(original_unmatched, compare_unmatched):
            if score < 0.82:
                continue
            left = original_unmatched[left_index]
            right = compare_unmatched[right_index]
            if self._is_layout_only_change(utils.normalize(left.text), utils.normalize(right.text)):
                paired_original.add(left_index)
                paired_compare.add(right_index)
                continue
            diffs.append(DiffItem(
                diff_id=generate_diff_id(next_index),
                diff_type="MODIFY",
                title="表格字段：标的物",
                original_text=left.text,
                compare_text=right.text,
                original_snippet=left.text,
                compare_snippet=right.text,
                readable_change=f"表格字段变更：{left.text} -> {right.text}",
                source_type="table",
                original_evidence=[self._evidence(left, "MODIFY")],
                compare_evidence=[self._evidence(right, "MODIFY")],
            ))
            next_index += 1
            paired_original.add(left_index)
            paired_compare.add(right_index)

        deletions = [u for i, u in enumerate(original_unmatched) if i not in paired_original]
        additions = [u for i, u in enumerate(compare_unmatched) if i not in paired_compare]
        if deletions or additions:
            warnings.append("部分表格文本无法可靠按行/单元格配对，已跳过大范围表格字符级高亮。")

        return diffs, warnings

    def _extract_flat_units(self, blocks: list[tuple[TextBlock, str]]) -> list[_FlatUnit]:
        units: list[_FlatUnit] = []
        for block, text in blocks:
            for line in utils.clean_text(text).splitlines():
                line = line.strip()
                if not line:
                    continue
                normalized = utils.normalize(line)
                if not normalized or utils.is_noise(normalized):
                    continue
                units.append(_FlatUnit(text=line, normalized=normalized, page_no=block.page_no, bbox=block.bbox))
        return units

    def _unmatched_units(self, original: list[_FlatUnit], compare: list[_FlatUnit]) -> tuple[list[_FlatUnit], list[_FlatUnit]]:
        compare_counts = Counter(u.normalized for u in compare)
        original_unmatched: list[_FlatUnit] = []
        for unit in original:
            if compare_counts[unit.normalized] > 0:
                compare_counts[unit.normalized] -= 1
            else:
                original_unmatched.append(unit)
        original_counts = Counter(u.normalized for u in original)
        compare_unmatched: list[_FlatUnit] = []
        for unit in compare:
            if original_counts[unit.normalized] > 0:
                original_counts[unit.normalized] -= 1
            else:
                compare_unmatched.append(unit)
        return original_unmatched, compare_unmatched

    def _similar_pairs(self, original: list[_FlatUnit], compare: list[_FlatUnit]) -> list[tuple[int, int, float]]:
        candidates: list[tuple[int, int, float]] = []
        for li, left in enumerate(original):
            for ri, right in enumerate(compare):
                score = SequenceMatcher(None, left.normalized, right.normalized).ratio()
                if score >= 0.7:
                    candidates.append((li, ri, score))
        candidates.sort(key=lambda x: x[2], reverse=True)
        used_l: set[int] = set()
        used_r: set[int] = set()
        result: list[tuple[int, int, float]] = []
        for li, ri, score in candidates:
            if li in used_l or ri in used_r:
                continue
            used_l.add(li)
            used_r.add(ri)
            result.append((li, ri, score))
        return result

    def _is_layout_only_change(self, left: str, right: str) -> bool:
        left_no_row = re.sub(r"^\d{1,2}", "", left)
        right_no_row = re.sub(r"^\d{1,2}", "", right)
        if left_no_row == right_no_row:
            return True
        if left in right or right in left:
            return True
        return self._canonical_config(left) == self._canonical_config(right)

    def _canonical_config(self, text: str) -> str:
        text = re.sub(r"^\d{1,2}", "", text)
        text = text.replace(":", "").replace(";", "").replace(",", "").replace(".", "")
        text = text.replace("；", "").replace("，", "").replace("。", "")
        return text

    def _evidence(self, unit: _FlatUnit, highlight_type: str) -> EvidenceBox:
        return EvidenceBox(
            page_no=unit.page_no,
            bbox=unit.bbox,
            method="table_cell",
            text=unit.text[:300],
            highlight_type=highlight_type,
        )
