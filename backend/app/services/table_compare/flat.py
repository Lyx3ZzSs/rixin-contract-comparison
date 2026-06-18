"""Flat text comparison fallback for table blocks without HTML."""

from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher

from app.models import BBox, DiffItem, EvidenceBox, TextBlock
from app.services.table_compare.matcher import TableMatcher
from app.services.table_compare.types import _FlatUnit, _LogicalRow
from app.services.table_compare import utils
from app.utils.id_utils import generate_diff_id


class FlatTextComparator:
    """Compares table blocks as flat text when structured parsing is unavailable.

    Provides three comparison tiers:
      - flat_compare:    lowest tier — line-by-line flat text
      - row_level_compare: middle tier — row-level granularity from logical tables
    """

    # -----------------------------------------------------------------
    # Tier 3: flat text comparison (existing)
    # -----------------------------------------------------------------

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

    # -----------------------------------------------------------------
    # Tier 2: row-level comparison (intermediate degradation)
    # -----------------------------------------------------------------

    _ROW_MATCH_THRESHOLD = 0.55

    def __init__(self) -> None:
        self._matcher = TableMatcher()

    def row_level_compare(
        self,
        original_tables: list,
        compare_tables: list,
        start_index: int,
        warnings: list[str],
    ) -> tuple[list[DiffItem], list[str]]:
        """Compare tables at row granularity — middle tier between full
        cell-level and flat-text.

        Uses the logical table structure to extract row texts, then matches
        rows by overall text similarity. Produces simpler DiffItems that
        indicate which rows differ without cell-level detail.
        """
        orig_rows = self._extract_row_units(original_tables)
        comp_rows = self._extract_row_units(compare_tables)
        if not orig_rows and not comp_rows:
            return [], warnings

        diffs: list[DiffItem] = []
        next_index = start_index
        paired_orig: set[int] = set()
        paired_comp: set[int] = set()

        for oi, ci, score in self._match_row_units(orig_rows, comp_rows):
            if score < self._ROW_MATCH_THRESHOLD:
                continue
            orig = orig_rows[oi]
            comp = comp_rows[ci]
            paired_orig.add(oi)
            paired_comp.add(ci)
            if orig.normalized == comp.normalized:
                continue
            if self._is_row_level_noise(orig, comp, score):
                continue
            diffs.append(DiffItem(
                diff_id=generate_diff_id(next_index),
                diff_type="MODIFY",
                title=self._row_diff_title(orig, comp),
                original_text=orig.text,
                compare_text=comp.text,
                original_snippet=orig.text[:300],
                compare_snippet=comp.text[:300],
                readable_change=f"表格行{orig.row_index + 1}变更",
                source_type="table",
                original_evidence=self._row_evidence(orig),
                compare_evidence=self._row_evidence(comp),
            ))
            next_index += 1

        for oi, orig in enumerate(orig_rows):
            if oi not in paired_orig:
                if self._is_fragment_row_unit(orig):
                    continue
                diffs.append(DiffItem(
                    diff_id=generate_diff_id(next_index),
                    diff_type="DELETE",
                    title=f"表格行{orig.row_index + 1}删除",
                    original_text=orig.text,
                    compare_text="",
                    original_snippet=orig.text[:300],
                    compare_snippet="",
                    readable_change=f"表格行{orig.row_index + 1}删除",
                    source_type="table",
                    original_evidence=self._row_evidence(orig),
                    compare_evidence=[],
                ))
                next_index += 1
        for ci, comp in enumerate(comp_rows):
            if ci not in paired_comp:
                if self._is_fragment_row_unit(comp):
                    continue
                diffs.append(DiffItem(
                    diff_id=generate_diff_id(next_index),
                    diff_type="ADD",
                    title=f"表格行{comp.row_index + 1}新增",
                    original_text="",
                    compare_text=comp.text,
                    original_snippet="",
                    compare_snippet=comp.text[:300],
                    readable_change=f"表格行{comp.row_index + 1}新增",
                    source_type="table",
                    original_evidence=[],
                    compare_evidence=self._row_evidence(comp),
                ))
                next_index += 1

        return diffs, warnings

    def _extract_row_units(self, tables: list) -> list[_RowUnit]:
        """Extract one _RowUnit per row from a list of _LogicalTable."""
        units: list[_RowUnit] = []
        for table in tables:
            caption = getattr(table, "caption", "") or ""
            table_type = utils.table_type(table)
            for table_row_index, row in enumerate(table.rows):
                texts = [cell.text for cell in row.cells if cell.text.strip()]
                if not texts:
                    continue
                row_text = " | ".join(texts)
                norm = utils.normalize(row_text)
                if not norm:
                    continue
                page_no = getattr(row, "page_no", 0) or 0
                bbox = self._row_bbox(row)
                units.append(_RowUnit(
                    row_index=row.row_index,
                    text=row_text,
                    normalized=norm,
                    page_no=page_no,
                    bbox=bbox,
                    caption=caption,
                    sequence=self._row_sequence(row),
                    product_name=self._row_product_name(table, table_row_index),
                    business_key=self._matcher._row_business_key(table, table_row_index),
                    table_type=table_type,
                    protected_values=self._protected_values(table, table_row_index),
                    nonempty_texts=tuple(texts),
                ))
        return units

    def _match_row_units(
        self, orig: list[_RowUnit], comp: list[_RowUnit],
    ) -> list[tuple[int, int, float]]:
        """Greedy best-first row matching by normalised text similarity."""
        candidates: list[tuple[float, int, int]] = []
        for oi, o in enumerate(orig):
            for ci, c in enumerate(comp):
                score = SequenceMatcher(None, o.normalized, c.normalized).ratio()
                if not self._row_units_compatible(o, c, score):
                    continue
                if score >= 0.4:
                    candidates.append((score, oi, ci))
        candidates.sort(reverse=True)
        used_o: set[int] = set()
        used_c: set[int] = set()
        result: list[tuple[int, int, float]] = []
        for score, oi, ci in candidates:
            if oi in used_o or ci in used_c:
                continue
            used_o.add(oi)
            used_c.add(ci)
            result.append((oi, ci, score))
        return result

    def _row_units_compatible(self, original: "_RowUnit", compare: "_RowUnit", score: float) -> bool:
        if original.table_type == "product" or compare.table_type == "product":
            if original.sequence and compare.sequence and original.sequence == compare.sequence:
                if original.product_name and compare.product_name:
                    product_score = SequenceMatcher(None, original.product_name, compare.product_name).ratio()
                    if product_score < 0.90:
                        return False
            if original.business_key and compare.business_key:
                key_score = SequenceMatcher(None, original.business_key, compare.business_key).ratio()
                if key_score >= 0.92:
                    return True
                if score < 0.92 and original.sequence and compare.sequence and original.sequence != compare.sequence:
                    return False
        return True

    def _is_row_level_noise(self, original: "_RowUnit", compare: "_RowUnit", score: float) -> bool:
        if score < 0.94:
            return False
        if self._protected_values_changed(original, compare):
            return False
        if utils.punctuation_fold(original.normalized) == utils.punctuation_fold(compare.normalized):
            return True
        if self._matcher.is_similar_ocr_noise(original.normalized, compare.normalized, 0.94):
            return True
        return score >= 0.96 and self._product_identity_compatible(original, compare)

    @staticmethod
    def _protected_values_changed(original: "_RowUnit", compare: "_RowUnit") -> bool:
        keys = set(original.protected_values) | set(compare.protected_values)
        for key in keys:
            left = original.protected_values.get(key, "")
            right = compare.protected_values.get(key, "")
            if left != right:
                return True
        return False

    @staticmethod
    def _product_identity_compatible(original: "_RowUnit", compare: "_RowUnit") -> bool:
        if not original.product_name or not compare.product_name:
            return True
        return SequenceMatcher(None, original.product_name, compare.product_name).ratio() >= 0.90

    @staticmethod
    def _row_sequence(row: _LogicalRow) -> str:
        for cell in row.cells:
            text = utils.normalize(cell.text)
            if re.fullmatch(r"\d{1,3}", text):
                return text
        return ""

    @staticmethod
    def _row_product_name(table, row: int) -> str:
        if utils.table_type(table) == "product":
            return utils.normalize(FlatTextComparator._cell_text(table, row, 1))
        return ""

    @staticmethod
    def _protected_values(table, row: int) -> dict[str, str]:
        values: dict[str, str] = {}
        for col, name in ((5, "quantity"), (6, "price"), (7, "amount")):
            text = FlatTextComparator._cell_text(table, row, col)
            norm = utils.normalize_cell_for_compare(text)
            if norm.startswith(("quantity:", "amount:", "date:", "percent:")):
                values[name] = norm
            elif utils.is_number_like(utils.normalize(text)):
                values[name] = utils.normalize(text)
        return values

    @staticmethod
    def _cell_text(table, row: int, col: int) -> str:
        cell = utils.anchor_cell(table, row, col)
        return cell.text if cell else ""

    @staticmethod
    def _is_fragment_row_unit(unit: "_RowUnit") -> bool:
        texts = [utils.normalize(text) for text in unit.nonempty_texts if utils.normalize(text)]
        if not texts:
            return True
        if unit.table_type == "product" and unit.product_name:
            return False
        if len(texts) <= 2 and all(text in {"序号", "金额", "数量", "单价", "备注"} or len(text) <= 3 for text in texts):
            return True
        if len(texts) <= 3 and all(len(text) <= 3 or text in {"套", "台", "个", "项", "年", "月"} for text in texts):
            return True
        return False

    @staticmethod
    def _row_diff_title(orig: _RowUnit, comp: _RowUnit) -> str:
        caption = orig.caption or comp.caption
        caption_prefix = f"「{caption}」" if caption else ""
        return f"表格行{caption_prefix}：行{orig.row_index + 1}变更"

    @staticmethod
    def _row_bbox(row: _LogicalRow) -> BBox | None:
        """Compute a bounding box spanning all cells in the row."""
        bboxes = [cell.bbox for cell in row.cells if cell.bbox is not None]
        if not bboxes:
            return None
        return BBox(
            x0=min(b.x0 for b in bboxes),
            y0=min(b.y0 for b in bboxes),
            x1=max(b.x1 for b in bboxes),
            y1=max(b.y1 for b in bboxes),
        )

    def _row_evidence(self, unit: _RowUnit) -> list[EvidenceBox]:
        if unit.bbox is None:
            return []
        return [EvidenceBox(
            page_no=unit.page_no,
            bbox=unit.bbox,
            method="table_row",
            text=unit.text[:300],
            highlight_type="MODIFY",
        )]

    # -----------------------------------------------------------------
    # Shared helpers (existing)
    # -----------------------------------------------------------------

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


class _RowUnit:
    """Lightweight container for a single logical row during row-level comparison."""

    __slots__ = (
        "row_index",
        "text",
        "normalized",
        "page_no",
        "bbox",
        "caption",
        "sequence",
        "product_name",
        "business_key",
        "table_type",
        "protected_values",
        "nonempty_texts",
    )

    def __init__(
        self,
        row_index: int,
        text: str,
        normalized: str,
        page_no: int,
        bbox: BBox | None,
        caption: str = "",
        sequence: str = "",
        product_name: str = "",
        business_key: str = "",
        table_type: str = "generic",
        protected_values: dict[str, str] | None = None,
        nonempty_texts: tuple[str, ...] = (),
    ):
        self.row_index = row_index
        self.text = text
        self.normalized = normalized
        self.page_no = page_no
        self.bbox = bbox
        self.caption = caption
        self.sequence = sequence
        self.product_name = product_name
        self.business_key = business_key
        self.table_type = table_type
        self.protected_values = protected_values or {}
        self.nonempty_texts = nonempty_texts
