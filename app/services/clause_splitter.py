from __future__ import annotations

import re
import unicodedata

from app.models import BBox, CharBox, Clause, Document, EvidenceBox
from app.services.normalizer import TextNormalizer


class ClauseSplitter:
    clause_start_pattern = re.compile(
        r"^\s*((第[一二三四五六七八九十百千万0-9]+[章节条])|([一二三四五六七八九十]+、)|(（[一二三四五六七八九十0-9]+）)|(\d+(?:\.\d+){0,3}[\.、]?))\s*(.*)$"
    )

    def __init__(self, text_normalizer: TextNormalizer | None = None) -> None:
        self.normalizer = text_normalizer or TextNormalizer()

    def split(self, document: Document, prefix: str) -> list[Clause]:
        units = []
        for page in document.pages:
            for block in page.blocks:
                normalized_block = self.normalizer.normalize(block.text)
                if not normalized_block:
                    continue
                normalized_char_boxes = self._char_boxes_for_normalized_block(block.text, normalized_block, block.char_boxes)
                if not any(normalized_char_boxes):
                    normalized_char_boxes = self._estimate_char_boxes(normalized_block, block.bbox, block.page_no)
                pieces = self._split_block_lines(normalized_block)
                for piece, start, end in pieces:
                    units.append(
                        {
                            "text": piece,
                            "char_boxes": normalized_char_boxes[start:end],
                            "page_no": block.page_no,
                            "block_id": block.block_id,
                            "evidence": EvidenceBox(
                                page_no=block.page_no,
                                bbox=block.bbox,
                                method="block_fallback",
                                text=piece[:300],
                            ),
                        }
                    )

        if not units:
            return []

        clauses: list[dict] = []
        current: dict | None = None
        saw_marker = False
        for unit in units:
            marker = self._parse_marker(unit["text"])
            starts_clause = marker is not None and not self._is_table_continuation(current, marker)
            if starts_clause:
                saw_marker = True
            if starts_clause or current is None:
                if current is not None:
                    clauses.append(current)
                clause_no, title = marker if marker else ("", "")
                current = {
                    "clause_no": clause_no,
                    "title": title,
                    "texts": [unit["text"]],
                    "char_boxes": [unit["char_boxes"]],
                    "page_numbers": [unit["page_no"]],
                    "bboxes": [unit["evidence"]],
                    "source_block_ids": [unit["block_id"]],
                }
            else:
                current["texts"].append(unit["text"])
                current["char_boxes"].append(unit["char_boxes"])
                current["page_numbers"].append(unit["page_no"])
                current["bboxes"].append(unit["evidence"])
                current["source_block_ids"].append(unit["block_id"])
        if current is not None:
            clauses.append(current)

        if not saw_marker:
            clauses = [
                {
                    "clause_no": "",
                    "title": self._title_from_text(unit["text"]),
                    "texts": [unit["text"]],
                    "char_boxes": [unit["char_boxes"]],
                    "page_numbers": [unit["page_no"]],
                    "bboxes": [unit["evidence"]],
                    "source_block_ids": [unit["block_id"]],
                }
                for unit in units
            ]

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
                )
            )
        return result

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
            if marker and current and not self._is_table_lines_continuation(current, marker):
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

    def _is_table_continuation(self, current: dict | None, marker: tuple[str, str]) -> bool:
        if current is None or not self._is_product_table_context("\n".join(current["texts"])):
            return False
        clause_no, _ = marker
        return not self._is_formal_clause_marker(clause_no)

    def _is_table_lines_continuation(
        self,
        current_lines: list[tuple[str, int, int]],
        marker: tuple[str, str],
    ) -> bool:
        current_text = "\n".join(line for line, _, _ in current_lines)
        if not self._is_product_table_context(current_text):
            return False
        clause_no, _ = marker
        return not self._is_formal_clause_marker(clause_no)

    def _is_product_table_context(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if "产品名称" not in compact:
            return False
        return "规格型号" in compact or "单价" in compact or "合计" in compact

    def _is_formal_clause_marker(self, clause_no: str) -> bool:
        return bool(re.fullmatch(r"第[一二三四五六七八九十百千万0-9]+[章节条]", clause_no or ""))

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
