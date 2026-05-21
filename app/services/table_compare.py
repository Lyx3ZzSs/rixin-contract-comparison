from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.models import BBox, DiffItem, Document, EvidenceBox, TextBlock
from app.utils.id_utils import generate_diff_id


@dataclass
class TableUnit:
    text: str
    normalized: str
    page_no: int
    bbox: BBox


class TableComparator:
    table_headers = {"序号", "产品名称", "详细配置", "品牌", "单位", "数量", "单价", "金额", "备注"}
    noise_pattern = re.compile(r"^(共\d+页第\d+页|第?\d+页|小计|\d+\s*套总计|\d+\s*套合计|\d+\s*套总合计)$")
    table_block_types = {"table", "table_title", "table_cell"}

    def build_diffs(self, original: Document, compare: Document, start_index: int = 1) -> tuple[list[DiffItem], list[str]]:
        warnings: list[str] = []
        if not self._has_structured_table(original) and not self._has_structured_table(compare):
            if any(page.blocks for page in [*original.pages, *compare.pages]):
                warnings.append("未获得结构化表格区域，已跳过表格比对。")
            return [], warnings

        original_units = self.extract_units(original)
        compare_units = self.extract_units(compare)
        if not original_units and not compare_units:
            return [], []

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
            if self._is_layout_only_change(left.normalized, right.normalized):
                paired_original.add(left_index)
                paired_compare.add(right_index)
                continue
            diffs.append(self._modify_diff(left, right, next_index))
            next_index += 1
            paired_original.add(left_index)
            paired_compare.add(right_index)

        deletions = [unit for index, unit in enumerate(original_unmatched) if index not in paired_original]
        additions = [unit for index, unit in enumerate(compare_unmatched) if index not in paired_compare]
        if deletions or additions:
            warnings.append("部分表格文本无法可靠按行/单元格配对，已跳过大范围表格字符级高亮。")

        return diffs, warnings

    def extract_units(self, document: Document) -> list[TableUnit]:
        units: list[TableUnit] = []
        for page in document.pages:
            for block in page.blocks:
                if not self.is_table_block(block):
                    continue
                for text in self._unit_texts(block):
                    normalized = self._normalize(text)
                    if not normalized or self._is_noise(normalized):
                        continue
                    units.append(TableUnit(text=text, normalized=normalized, page_no=block.page_no, bbox=block.bbox))
        return units

    def is_table_block(self, block: TextBlock) -> bool:
        block_type = (block.block_type or "").lower()
        return block_type in self.table_block_types

    def _unmatched_units(self, original: list[TableUnit], compare: list[TableUnit]) -> tuple[list[TableUnit], list[TableUnit]]:
        compare_counts = Counter(unit.normalized for unit in compare)
        original_unmatched: list[TableUnit] = []
        for unit in original:
            if compare_counts[unit.normalized] > 0:
                compare_counts[unit.normalized] -= 1
            else:
                original_unmatched.append(unit)

        original_counts = Counter(unit.normalized for unit in original)
        compare_unmatched: list[TableUnit] = []
        for unit in compare:
            if original_counts[unit.normalized] > 0:
                original_counts[unit.normalized] -= 1
            else:
                compare_unmatched.append(unit)
        return original_unmatched, compare_unmatched

    def _similar_pairs(self, original: list[TableUnit], compare: list[TableUnit]) -> list[tuple[int, int, float]]:
        candidates: list[tuple[int, int, float]] = []
        for left_index, left in enumerate(original):
            for right_index, right in enumerate(compare):
                score = SequenceMatcher(None, left.normalized, right.normalized).ratio()
                if score >= 0.7:
                    candidates.append((left_index, right_index, score))
        candidates.sort(key=lambda item: item[2], reverse=True)
        used_left: set[int] = set()
        used_right: set[int] = set()
        result: list[tuple[int, int, float]] = []
        for left_index, right_index, score in candidates:
            if left_index in used_left or right_index in used_right:
                continue
            used_left.add(left_index)
            used_right.add(right_index)
            result.append((left_index, right_index, score))
        return result

    def _modify_diff(self, left: TableUnit, right: TableUnit, index: int) -> DiffItem:
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="MODIFY",
            title="表格字段：标的物",
            original_text=left.text,
            compare_text=right.text,
            original_snippet=left.text,
            compare_snippet=right.text,
            readable_change=f"表格字段变更：{left.text} -> {right.text}",
            original_evidence=[self._evidence(left, "MODIFY")],
            compare_evidence=[self._evidence(right, "MODIFY")],
        )

    def _evidence(self, unit: TableUnit, highlight_type: str) -> EvidenceBox:
        return EvidenceBox(
            page_no=unit.page_no,
            bbox=unit.bbox,
            method="table_cell",
            text=unit.text[:300],
            highlight_type=highlight_type,
        )

    def _unit_texts(self, block: TextBlock) -> list[str]:
        lines = [line.strip() for line in self._clean_text(block.text).splitlines() if line.strip()]
        if not lines:
            return []
        if (block.block_type or "").lower() in self.table_block_types:
            return lines
        return ["\n".join(lines)]

    def _is_layout_only_change(self, left: str, right: str) -> bool:
        left_without_row_no = re.sub(r"^\d{1,2}", "", left)
        right_without_row_no = re.sub(r"^\d{1,2}", "", right)
        if left_without_row_no == right_without_row_no:
            return True
        if left in right or right in left:
            return True
        return self._canonical_config(left) == self._canonical_config(right)

    def _is_noise(self, normalized: str) -> bool:
        return bool(self.noise_pattern.fullmatch(normalized) or normalized in self.table_headers)

    def _normalize(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text or "")
        text = text.lower()
        text = re.sub(r"[\s\n\r\t]+", "", text)
        text = text.replace("：", ":").replace("；", ";").replace("，", ",").replace("。", ".")
        text = re.sub(r"[()（）]", "", text)
        text = text.replace("口:", "口:")
        return text.strip()

    def _canonical_config(self, text: str) -> str:
        text = re.sub(r"^\d{1,2}", "", text)
        text = text.replace(":", "").replace(";", "").replace(",", "").replace(".", "")
        text = text.replace("；", "").replace("，", "").replace("。", "")
        return text

    def _has_structured_table(self, document: Document) -> bool:
        return any(self.is_table_block(block) for page in document.pages for block in page.blocks)

    def _clean_text(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text or "").replace("\r", "\n")
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line).strip()
