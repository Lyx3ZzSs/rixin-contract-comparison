from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.models import BBox, CharBox, Clause, Document, EvidenceBox, TextBlock
from app.services.table_compare import TableComparator
from app.services.normalizer import TextNormalizer


@dataclass(frozen=True)
class ClauseUnit:
    text: str
    char_boxes: list[CharBox | None]
    page_no: int
    block_id: str
    block_type: str
    bbox: BBox
    evidence: EvidenceBox
    layout_block_id: str = ""
    layout_order: int | None = None


class ClauseSplitter:
    clause_start_pattern = re.compile(
        r"^\s*((第[一二三四五六七八九十百千万0-9]+[章节条])|([一二三四五六七八九十]+、)|(（[一二三四五六七八九十0-9]+）)|(\d+(?:\.\d+){0,3}[\.、]?))\s*(.*)$"
    )
    skip_block_types = {
        "footer",
        "header",
        "page_footer",
        "page_header",
        "image",
        "figure",
        "seal",
        "chart",
        "formula",
        "vertical_text",
    }
    min_ocr_confidence = 0.5
    short_noise_confidence = 0.7
    vertical_height_width_ratio = 2.3
    mask_block_types = {"formula", "chart", "image", "figure"}
    mask_overlap_threshold = 0.5
    min_content_density = 0.15
    table_block_types = {"table", "table_title"}
    cover_block_types = {"doc_title", "title"}

    def __init__(self, text_normalizer: TextNormalizer | None = None) -> None:
        self.normalizer = text_normalizer or TextNormalizer()
        self.table_detector = TableComparator()

    def split(self, document: Document, prefix: str) -> list[Clause]:
        units = self._collect_units(document)
        if not units:
            return []

        units = self._trim_cover_units(self._order_units(units))
        if not units:
            return []

        clauses, saw_marker = self._detect_clause_items(units)
        if not saw_marker:
            clauses = self._single_unit_items(units)

        return self._build_clauses(clauses, prefix)

    def _collect_units(self, document: Document) -> list[ClauseUnit]:
        mask_index = self._build_mask_index(document)
        units: list[ClauseUnit] = []
        for page in document.pages:
            page_masks = mask_index.get(page.page_no, [])
            for block in page.blocks:
                block_type = (block.block_type or "").lower()
                if block_type in self.skip_block_types:
                    continue
                if self.table_detector.is_table_block(block):
                    continue
                if self._is_low_confidence(block):
                    continue
                if self._is_vertical_block(block):
                    continue
                if self._is_masked_by_non_text(block, page_masks):
                    continue
                if self._is_low_content_density(block):
                    continue
                normalized_block = self.normalizer.normalize(block.text)
                if not normalized_block:
                    continue
                if self._is_body_ocr_noise(block, normalized_block, page.width, page.height):
                    continue
                normalized_char_boxes = self._char_boxes_for_normalized_block(block.text, normalized_block, block.char_boxes)
                if not any(normalized_char_boxes):
                    normalized_char_boxes = self._estimate_char_boxes(normalized_block, block.bbox, block.page_no)
                pieces = self._split_block_lines(normalized_block)
                for piece, start, end in pieces:
                    units.append(
                        ClauseUnit(
                            text=piece,
                            char_boxes=normalized_char_boxes[start:end],
                            page_no=block.page_no,
                            block_id=block.block_id,
                            block_type=block_type,
                            bbox=block.bbox,
                            evidence=EvidenceBox(
                                page_no=block.page_no,
                                bbox=block.bbox,
                                method="block_fallback",
                                text=piece[:300],
                            ),
                            layout_block_id=block.layout_block_id,
                            layout_order=block.layout_order,
                        )
                    )
        return units

    def _is_low_confidence(self, block: TextBlock) -> bool:
        if block.confidence is None:
            return False
        return block.confidence < self.min_ocr_confidence

    def _is_vertical_block(self, block: TextBlock) -> bool:
        bbox = block.bbox
        width = bbox.x1 - bbox.x0
        height = bbox.y1 - bbox.y0
        if width <= 0 or height <= 0:
            return False
        return height / width > self.vertical_height_width_ratio

    def _build_mask_index(self, document: Document) -> dict[int, list[BBox]]:
        index: dict[int, list[BBox]] = {}
        for page in document.pages:
            masks = [
                block.bbox
                for block in page.blocks
                if (block.block_type or "").lower() in self.mask_block_types
            ]
            if masks:
                index[page.page_no] = masks
        return index

    def _is_masked_by_non_text(self, block: TextBlock, masks: list[BBox]) -> bool:
        if not masks:
            return False
        block_area = _bbox_area(block.bbox)
        if block_area <= 0:
            return False
        for mask_bbox in masks:
            overlap = _bbox_overlap_area(block.bbox, mask_bbox)
            if overlap / block_area > self.mask_overlap_threshold:
                return True
        return False

    def _is_low_content_density(self, block: TextBlock) -> bool:
        bbox = block.bbox
        width = bbox.x1 - bbox.x0
        height = bbox.y1 - bbox.y0
        if width <= 0 or height <= 0:
            return True
        text_len = len(block.text.strip())
        return text_len * height < width * self.min_content_density

    def _is_body_ocr_noise(
        self,
        block: TextBlock,
        normalized_text: str,
        page_width: float,
        page_height: float,
    ) -> bool:
        compact = re.sub(r"\s+", "", normalized_text or "")
        if not compact:
            return True
        marker = self._parse_marker(normalized_text)
        if marker is not None and not self._is_quantity_or_amount_marker(normalized_text, marker):
            return False
        if self._is_seal_fragment_noise(block, compact, page_width):
            return True
        if self._is_right_edge_ocr_fragment(block, compact, page_width):
            return True
        if self._is_short_isolated_noise(block, compact, page_width, page_height):
            return True
        return False

    def _is_seal_fragment_noise(self, block: TextBlock, compact: str, page_width: float) -> bool:
        if not re.fullmatch(r"(合同专|合同专用|合同专用章|合同章|专用章|公章|印章)", compact):
            return False
        return self._near_horizontal_edge(block.bbox, page_width)

    def _is_short_isolated_noise(
        self,
        block: TextBlock,
        compact: str,
        page_width: float,
        page_height: float,
    ) -> bool:
        if len(compact) > 5:
            return False
        if self._looks_like_meaningful_short_text(compact):
            return False
        bbox = block.bbox
        width = bbox.x1 - bbox.x0
        height = bbox.y1 - bbox.y0
        block_type = (block.block_type or "").lower()
        confidence = block.confidence
        low_confidence = confidence is not None and confidence < self.short_noise_confidence
        tiny_block = width <= max(page_width * 0.035, 16.0) and height <= max(page_height * 0.02, 16.0)
        edge_ocr_line = block_type == "ocr_line" and self._near_horizontal_edge(bbox, page_width)
        latin_noise = bool(re.fullmatch(r"[A-Za-z]{1,5}", compact)) and low_confidence
        small_latin_ocr_noise = block_type == "ocr_line" and latin_noise and height <= max(page_height * 0.02, 16.0)
        punctuation_number_noise = bool(re.fullmatch(r"[(（]?[-—_~]*\d{1,2}[)）.]?", compact)) and low_confidence
        symbol_noise = bool(re.fullmatch(r"[\W_]{1,5}", compact)) and low_confidence
        return bool(
            small_latin_ocr_noise
            or (
                (tiny_block or edge_ocr_line)
                and (latin_noise or punctuation_number_noise or symbol_noise)
            )
        )

    def _is_right_edge_ocr_fragment(self, block: TextBlock, compact: str, page_width: float) -> bool:
        if (block.block_type or "").lower() != "ocr_line":
            return False
        if len(compact) > 6:
            return False
        if self._looks_like_meaningful_short_text(compact):
            return False
        return self._near_horizontal_edge(block.bbox, page_width)

    def _looks_like_meaningful_short_text(self, compact: str) -> bool:
        if re.fullmatch(r"\d{4}年?", compact):
            return True
        if re.fullmatch(r"\d+(?:\.\d+)?(元|万元|台|套|个|项|批|份|天|月|年|%)", compact):
            return True
        if re.fullmatch(r"[一二三四五六七八九十百千万]+(元|万元|台|套|个|项|批|份|天|月|年)?", compact):
            return True
        if re.fullmatch(r"[甲乙丙丁]方", compact):
            return True
        return False

    def _near_horizontal_edge(self, bbox: BBox, page_width: float) -> bool:
        if page_width <= 0:
            return False
        return bbox.x0 <= page_width * 0.03 or bbox.x1 >= page_width * 0.97

    def _order_units(self, units: list[ClauseUnit]) -> list[ClauseUnit]:
        ordered: list[ClauseUnit] = []
        pages = sorted({unit.page_no for unit in units})
        for page_no in pages:
            page_units = [unit for unit in units if unit.page_no == page_no]
            if self._can_trust_layout_order(page_units):
                snapped = self._snap_y_by_layout_group(page_units)
                page_units.sort(key=lambda unit: (unit.layout_order or 0, snapped[unit.block_id], unit.bbox.x0, unit.block_id))
            else:
                snapped = self._snap_y_coordinates(page_units)
                page_units.sort(key=lambda unit: (snapped[unit.block_id], unit.bbox.x0, unit.layout_order or 0, unit.block_id))
            ordered.extend(page_units)
        return ordered

    def _snap_y_coordinates(self, units: list[ClauseUnit]) -> dict[str, float]:
        if not units:
            return {}
        heights = [unit.bbox.y1 - unit.bbox.y0 for unit in units]
        median_height = sorted(heights)[len(heights) // 2]
        threshold = max(median_height * 0.5, 2.0)

        sorted_by_y = sorted(units, key=lambda u: u.bbox.y0)
        groups: list[list[ClauseUnit]] = []
        current_group: list[ClauseUnit] = [sorted_by_y[0]]
        for unit in sorted_by_y[1:]:
            if unit.bbox.y0 - current_group[0].bbox.y0 <= threshold:
                current_group.append(unit)
            else:
                groups.append(current_group)
                current_group = [unit]
        groups.append(current_group)

        snapped: dict[str, float] = {}
        for group in groups:
            min_y = min(u.bbox.y0 for u in group)
            for unit in group:
                snapped[unit.block_id] = min_y
        return snapped

    def _snap_y_by_layout_group(self, units: list[ClauseUnit]) -> dict[str, float]:
        groups: dict[int, list[ClauseUnit]] = {}
        for unit in units:
            order = unit.layout_order or 0
            groups.setdefault(order, []).append(unit)

        snapped: dict[str, float] = {}
        for group_units in groups.values():
            if len(group_units) == 1:
                snapped[group_units[0].block_id] = group_units[0].bbox.y0
            else:
                snapped.update(self._snap_y_coordinates(group_units))
        return snapped

    def _can_trust_layout_order(self, units: list[ClauseUnit]) -> bool:
        with_order = [unit for unit in units if unit.layout_order is not None]
        if len(with_order) < 2 or len(with_order) != len(units):
            return False
        by_y = sorted(with_order, key=lambda unit: (unit.bbox.y0, unit.bbox.x0))
        return all(
            (left.layout_order or 0) <= (right.layout_order or 0)
            for left, right in zip(by_y, by_y[1:], strict=False)
        )

    def _detect_clause_items(self, units: list[ClauseUnit]) -> tuple[list[dict], bool]:
        clauses: list[dict] = []
        current: dict | None = None
        saw_marker = False
        entered_body = False
        for unit in units:
            marker = self._parse_marker(unit.text)
            block_type = unit.block_type
            if not entered_body and self._is_pre_body_noise(unit, marker):
                continue
            starts_unnumbered_title = (
                self._is_unnumbered_section_title(unit, marker)
                and not self._current_is_bare_marker(current)
            )
            starts_clause = (
                (
                    marker is not None
                    and block_type not in self.table_block_types
                    and not self._is_quantity_or_amount_marker(unit.text, marker)
                )
                or starts_unnumbered_title
            )
            if starts_clause:
                saw_marker = True
                entered_body = True
            elif block_type not in self.cover_block_types and current is not None:
                entered_body = True
            if starts_clause or current is None:
                if current is not None:
                    clauses.append(current)
                clause_no, title = marker if marker else ("", self._title_from_text(unit.text))
                current = {
                    "clause_no": clause_no,
                    "title": title,
                    "texts": [unit.text],
                    "char_boxes": [unit.char_boxes],
                    "page_numbers": [unit.page_no],
                    "bboxes": [unit.evidence],
                    "source_block_ids": [unit.block_id],
                    "segmentation_reason": f"marker:{clause_no}" if marker else "initial_unit_without_marker",
                    "segmentation_confidence": 0.95 if marker else 0.55,
                }
            else:
                current["texts"].append(unit.text)
                current["char_boxes"].append(unit.char_boxes)
                current["page_numbers"].append(unit.page_no)
                current["bboxes"].append(unit.evidence)
                current["source_block_ids"].append(unit.block_id)
        if current is not None:
            clauses.append(current)
        return clauses, saw_marker

    def _single_unit_items(self, units: list[ClauseUnit]) -> list[dict]:
        return [
            {
                "clause_no": "",
                "title": self._title_from_text(unit.text),
                "texts": [unit.text],
                "char_boxes": [unit.char_boxes],
                "page_numbers": [unit.page_no],
                "bboxes": [unit.evidence],
                "source_block_ids": [unit.block_id],
                "segmentation_reason": "fallback_single_unit",
                "segmentation_confidence": 0.45,
            }
            for unit in units
        ]

    def _build_clauses(self, clauses: list[dict], prefix: str) -> list[Clause]:
        result: list[Clause] = []
        for index, item in enumerate(clauses, start=1):
            text = "\n".join(item["texts"]).strip()
            char_boxes = self._join_char_boxes(item["char_boxes"])
            result.append(
                Clause(
                    clause_id=f"{prefix}C{index:03d}",
                    clause_no=item["clause_no"],
                    title=item["title"] or self._title_from_text(text),
                    text=text,
                    normalized_text=self.normalizer.normalize_for_match(text),
                    page_numbers=sorted(set(item["page_numbers"])),
                    bboxes=item["bboxes"],
                    source_block_ids=list(dict.fromkeys(item["source_block_ids"])),
                    char_boxes=char_boxes,
                    segmentation_reason=item.get("segmentation_reason", ""),
                    segmentation_confidence=item.get("segmentation_confidence", 0.8),
                )
            )
        return result

    def _is_pre_body_noise(self, unit: ClauseUnit, marker: tuple[str, str] | None) -> bool:
        block_type = unit.block_type
        text = unit.text
        if self._is_formal_marker_tuple(marker):
            return False
        if self._is_standalone_body_title(text):
            return True
        if block_type in self.cover_block_types:
            return True
        compact = re.sub(r"\s+", "", text or "")
        if self._is_cover_noise_text(compact):
            return True
        return False

    def _trim_cover_units(self, units: list[ClauseUnit]) -> list[ClauseUnit]:
        start_index = self._body_start_index(units)
        if start_index is None or start_index == 0:
            return units
        pre_body = units[:start_index]
        if any(self._looks_like_body_numbered_unit(unit) for unit in pre_body):
            return units
        if not any(self._looks_like_cover_unit(unit) for unit in pre_body):
            return units
        return units[start_index:]

    def _body_start_index(self, units: list[ClauseUnit]) -> int | None:
        for index, unit in enumerate(units):
            text = unit.text
            marker = self._parse_marker(text)
            if self._is_body_intro(text) or self._is_formal_body_marker(marker):
                return index
        return None

    def _looks_like_cover_unit(self, unit: ClauseUnit) -> bool:
        compact = re.sub(r"\s+", "", unit.text or "")
        block_type = unit.block_type
        return block_type in self.cover_block_types or self._is_cover_noise_text(compact) or bool(
            re.search(r"(采购合同|合同编号|签订日期|签订地点|甲方|乙方|项目|系统|中广核)", compact)
        )

    def _looks_like_body_numbered_unit(self, unit: ClauseUnit) -> bool:
        marker = self._parse_marker(unit.text)
        if marker is None:
            return False
        clause_no, _ = marker
        return bool(
            self._is_formal_clause_marker(clause_no)
            or re.fullmatch(r"\d+\.\d+(?:\.\d+)*", clause_no or "")
        )

    def _is_body_intro(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        return compact == "正文" or "达成合同如下" in compact

    def _is_formal_body_marker(self, marker: tuple[str, str] | None) -> bool:
        if marker is None:
            return False
        clause_no, title = marker
        if self._is_formal_clause_marker(clause_no):
            return True
        return bool(re.fullmatch(r"[一二三四五六七八九十]+", clause_no or "") and title)

    def _is_standalone_body_title(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        return compact == "正文"

    def _is_unnumbered_section_title(self, unit: ClauseUnit, marker: tuple[str, str] | None) -> bool:
        if marker is not None or unit.block_type != "paragraph_title":
            return False
        compact = re.sub(r"\s+", "", unit.text or "")
        if not compact or len(compact) < 4:
            return False
        if self._is_cover_noise_text(compact) or self._is_attachment_title(compact):
            return False
        return not bool(re.search(r"[:：。；;，,]$", compact))

    def _current_is_bare_marker(self, current: dict | None) -> bool:
        if current is None or len(current.get("texts", [])) != 1:
            return False
        text = str(current["texts"][0]).strip()
        marker = self._parse_marker(text)
        if marker is None:
            return False
        _, title = marker
        return not title

    def _is_attachment_title(self, compact: str) -> bool:
        return bool(re.fullmatch(r"附件[一二三四五六七八九十0-9]+.*", compact))

    def _is_cover_noise_text(self, compact: str) -> bool:
        return bool(
            re.search(r"合同编号|项目编号|采购合同$|买方[:：]|卖方[:：]|甲方[:：]|乙方[:：]|签订地点|签订日期", compact)
            or compact in {"甲方", "乙方", "买方", "卖方"}
            or re.fullmatch(r"共\d+页第\d+页", compact)
        )

    def _is_formal_marker_tuple(self, marker: tuple[str, str] | None) -> bool:
        if marker is None:
            return False
        clause_no, _ = marker
        return self._is_formal_clause_marker(clause_no)

    def _split_block_lines(self, text: str) -> list[tuple[str, int, int]]:
        lines = self._line_ranges(text)
        if len(lines) <= 1:
            stripped = text.strip()
            start = text.find(stripped) if stripped else 0
            return [(stripped, start, start + len(stripped))]
        pieces: list[tuple[str, int, int]] = []
        current: list[tuple[str, int, int]] = []
        for line, start, end in lines:
            marker = self._parse_marker(line)
            if marker and current:
                pieces.append(self._join_line_ranges(current))
                current = [(line, start, end)]
            else:
                current.append((line, start, end))
        if current:
            pieces.append(self._join_line_ranges(current))
        return pieces

    def _parse_marker(self, text: str) -> tuple[str, str] | None:
        first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        match = self.clause_start_pattern.match(first_line)
        if not match:
            return None
        clause_no = match.group(1).rstrip("、.")
        rest = match.group(6).strip() if match.lastindex and match.lastindex >= 6 else ""
        title = self._title_from_text(rest)
        return clause_no, title

    def _is_formal_clause_marker(self, clause_no: str) -> bool:
        return bool(re.fullmatch(r"第[一二三四五六七八九十百千万0-9]+[章节条]", clause_no or ""))

    def _is_quantity_or_amount_marker(self, text: str, marker: tuple[str, str]) -> bool:
        clause_no, _ = marker
        if self._is_formal_clause_marker(clause_no):
            return False
        first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        compact = re.sub(r"\s+", "", first_line)
        if re.fullmatch(r"\d{1,3}", compact):
            return True
        if re.match(r"^\s*\d+\s*(套|台|个|项|批|份|万元|元|天|月|个月|年|%)", first_line):
            return True
        if re.match(r"^\s*\d{4}\s*年", first_line):
            return True
        return False

    def _title_from_text(self, text: str) -> str:
        first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        first_line = re.sub(r"\s+", " ", first_line)
        return first_line[:40]

    def _line_ranges(self, text: str) -> list[tuple[str, int, int]]:
        ranges: list[tuple[str, int, int]] = []
        cursor = 0
        for raw_line in text.splitlines(keepends=True):
            line = raw_line.rstrip("\r\n")
            stripped = line.strip()
            if stripped:
                start = cursor + line.find(stripped)
                end = start + len(stripped)
                ranges.append((stripped, start, end))
            cursor += len(raw_line)
        if not ranges and text.strip():
            stripped = text.strip()
            start = text.find(stripped)
            ranges.append((stripped, start, start + len(stripped)))
        return ranges

    def _join_line_ranges(self, lines: list[tuple[str, int, int]]) -> tuple[str, int, int]:
        return "\n".join(line for line, _, _ in lines), lines[0][1], lines[-1][2]

    def _join_char_boxes(self, chunks: list[list[CharBox | None]]) -> list[CharBox | None]:
        joined: list[CharBox | None] = []
        for index, chunk in enumerate(chunks):
            if index > 0:
                joined.append(None)
            joined.extend(chunk)
        return joined

    def _char_boxes_for_normalized_block(
        self,
        source_text: str,
        normalized_text: str,
        source_char_boxes: list[CharBox],
    ) -> list[CharBox | None]:
        by_index = {char_box.text_index: char_box for char_box in source_char_boxes if char_box.text_index is not None}
        mapped: list[CharBox | None] = []
        source_cursor = 0
        for target_char in normalized_text:
            found_index = self._find_next_source_char(source_text, target_char, source_cursor)
            if found_index is None:
                mapped.append(None)
                continue
            source_cursor = found_index + 1
            char_box = by_index.get(found_index)
            if char_box is None:
                mapped.append(None)
            else:
                mapped.append(char_box.model_copy(update={"char": target_char}))
        return mapped

    def _find_next_source_char(self, source_text: str, target_char: str, start: int) -> int | None:
        normalized_target = self._normalize_char(target_char)
        for index in range(start, len(source_text)):
            if self._normalize_char(source_text[index]) == normalized_target:
                return index
        return None

    def _normalize_char(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value or "")
        return " " if normalized.isspace() else normalized

    def _estimate_char_boxes(self, text: str, bbox: BBox, page_no: int) -> list[CharBox | None]:
        lines = text.splitlines() or [text]
        line_count = max(1, len(lines))
        line_height = max(1.0, (bbox.y1 - bbox.y0) / line_count)
        char_boxes: list[CharBox | None] = []
        text_index = 0
        for line_index, line in enumerate(lines):
            if line_index > 0:
                char_boxes.append(None)
                text_index += 1
            width = max(1.0, bbox.x1 - bbox.x0)
            visible_count = max(1, len(line))
            char_width = width / visible_count
            y0 = bbox.y0 + line_index * line_height
            y1 = min(bbox.y1, y0 + line_height)
            for char_index, char in enumerate(line):
                x0 = bbox.x0 + char_index * char_width
                x1 = bbox.x0 + (char_index + 1) * char_width
                if char.isspace():
                    char_boxes.append(None)
                else:
                    char_boxes.append(
                        CharBox(
                            char=char,
                            page_no=page_no,
                            bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1),
                            text_index=None,
                        )
                    )
                text_index += 1
        return char_boxes


def _bbox_area(bbox: BBox) -> float:
    return max(0.0, bbox.x1 - bbox.x0) * max(0.0, bbox.y1 - bbox.y0)


def _bbox_overlap_area(left: BBox, right: BBox) -> float:
    x0 = max(left.x0, right.x0)
    y0 = max(left.y0, right.y0)
    x1 = min(left.x1, right.x1)
    y1 = min(left.y1, right.y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)
