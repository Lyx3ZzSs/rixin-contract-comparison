from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any


class ParagraphBuilder:
    """Build stable paragraph units while preserving source evidence metadata."""

    terminal_punctuation = tuple("。；;！？!?")
    continuation_punctuation = tuple("，,、：:（(")
    paragraph_merged_flag = "PARAGRAPH_MERGED"
    cross_page_merged_flag = "CROSS_PAGE_CONTINUATION_MERGED"
    cross_page_title_boundary_flag = "CROSS_PAGE_TITLE_BOUNDARY"
    title_block_types = {"paragraph_title", "doc_title", "title"}
    boundary_block_types = {
        "footer",
        "header",
        "page_footer",
        "page_header",
        "footnote",
        "vision_footnote",
        "table",
        "table_title",
        "seal",
        "image",
        "figure",
    }

    def build(
        self,
        units: Sequence[Any],
        *,
        parse_marker: Callable[[str], object | None],
    ) -> list[Any]:
        result: list[Any] = []
        unit_list = list(units)
        for index, unit in enumerate(unit_list):
            if result:
                next_unit = unit_list[index + 1] if index + 1 < len(unit_list) else None
                unit = self._with_boundary_metadata(result[-1], unit, parse_marker, next_unit)
            if result and self._is_continuation(result[-1], unit, parse_marker):
                result[-1] = self._merge_units(result[-1], unit)
            else:
                result.append(unit)
        return result

    def _is_continuation(
        self,
        previous: Any,
        current: Any,
        parse_marker: Callable[[str], object | None],
    ) -> bool:
        if getattr(previous, "section_type", "main_contract") != getattr(current, "section_type", "main_contract"):
            return False

        previous_text = str(getattr(previous, "text", "") or "").strip()
        current_text = str(getattr(current, "text", "") or "").strip()
        if not previous_text or not current_text:
            return False
        if self._has_strong_boundary_block_type(current):
            return False
        if self._looks_like_signing_boundary(current_text):
            return False

        current_marker = parse_marker(current_text)
        if self._current_marker_blocks_continuation(current_marker, current_text):
            return False

        previous_marker = parse_marker(previous_text)
        if previous_marker is not None and not self._marker_title(previous_marker):
            return self._same_block(previous, current) or self._visually_close(previous, current) or (
                self._visually_continues_across_adjacent_pages(previous, current)
                and not self._looks_like_standalone_label(current_text)
            )

        current_is_standalone_label = (
            self._looks_like_standalone_label(current_text)
            and not self._looks_like_numeric_value_continuation(current_text)
        )
        if parse_marker(previous_text) is None and self._looks_like_standalone_label(previous_text):
            return False
        if previous_text.endswith(self.terminal_punctuation):
            return False
        if self._same_block(previous, current):
            return True
        if getattr(previous, "page_no", None) == getattr(current, "page_no", None):
            if previous_text.endswith(self.continuation_punctuation):
                return True
            return self._visually_close(previous, current) and not current_is_standalone_label
        return self._visually_continues_across_adjacent_pages(previous, current) and not current_is_standalone_label

    @staticmethod
    def _marker_title(marker: object) -> str:
        if isinstance(marker, tuple) and len(marker) >= 2:
            return str(marker[1] or "")
        return ""

    @classmethod
    def _has_strong_boundary_block_type(cls, unit: Any) -> bool:
        block_type = str(getattr(unit, "block_type", "") or "").lower()
        return block_type in cls.boundary_block_types or block_type in cls.title_block_types

    @staticmethod
    def _compact_text(text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    @classmethod
    def _looks_like_signing_boundary(cls, text: str) -> bool:
        compact = cls._compact_text(text)
        if not compact:
            return True
        if "以下无正文" in compact or "此页无正文" in compact or "本页无正文" in compact:
            return True
        if compact in {"签署页", "签字页"}:
            return True
        if len(compact) <= 24 and re.search(r"(签署页|签字页).{0,12}无正文", compact):
            return True
        if len(compact) <= 20 and re.search(r"(甲方|乙方|买方|卖方).{0,8}(盖章|签章|签字)", compact):
            return True
        if len(compact) <= 20 and re.fullmatch(r"(甲方|乙方|买方|卖方)[:：]?", compact):
            return True
        return False

    @staticmethod
    def _looks_like_numeric_value_continuation(text: str) -> bool:
        first_line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
        return bool(
            re.match(
                r"^\s*\d+(?:,\d{3})*(?:\.\d+)?\s*(?:元整|元|万元|亿元|%|‰|天|日|个月|月|年|台|套|个|项|批|份|件)",
                first_line,
                re.IGNORECASE,
            )
            or re.match(r"^\s*(?:人民币|¥|￥)\s*\d", first_line, re.IGNORECASE)
        )

    @classmethod
    def _current_marker_blocks_continuation(cls, marker: object | None, text: str) -> bool:
        return marker is not None and not cls._looks_like_numeric_value_continuation(text)

    def _with_boundary_metadata(
        self,
        previous: Any,
        current: Any,
        parse_marker: Callable[[str], object | None],
        next_unit: Any | None = None,
    ) -> Any:
        current_text = str(getattr(current, "text", "") or "").strip()
        if not current_text:
            return current
        previous_section = getattr(previous, "section_type", "main_contract")
        if previous_section == "signature" and self._looks_like_signing_boundary(current_text):
            return self._replace_unit(current, section_type="signature")
        if self.cross_page_merged_flag in getattr(previous, "split_flags", ()):
            return current
        if not self._visually_continues_across_adjacent_pages(previous, current):
            return current
        if self._looks_like_signing_boundary(current_text):
            return self._replace_unit(current, section_type="signature")
        if (
            parse_marker(current_text) is None
            and self._looks_like_standalone_label(current_text)
            and not self._looks_like_numeric_value_continuation(current_text)
            and self._looks_like_cross_page_title_boundary(current, next_unit)
        ):
            return self._with_flag(
                self._replace_unit(current, block_type="paragraph_title"),
                self.cross_page_title_boundary_flag,
            )
        return current

    @staticmethod
    def _looks_like_cross_page_title_boundary(current: Any, next_unit: Any | None) -> bool:
        if next_unit is None or getattr(current, "page_no", None) != getattr(next_unit, "page_no", None):
            return False
        current_bbox = getattr(current, "bbox", None)
        next_bbox = getattr(next_unit, "bbox", None)
        if current_bbox is None or next_bbox is None:
            return False
        current_height = max(1.0, current_bbox.y1 - current_bbox.y0)
        vertical_gap = next_bbox.y0 - current_bbox.y1
        if vertical_gap < 0 or vertical_gap > current_height * 2.2:
            return False
        return next_bbox.x0 > current_bbox.x0 + max(current_height * 0.5, 12.0)

    @staticmethod
    def _replace_unit(unit: Any, **changes: Any) -> Any:
        try:
            return replace(unit, **changes)
        except TypeError:
            return unit

    @staticmethod
    def _same_block(previous: Any, current: Any) -> bool:
        return bool(getattr(previous, "block_id", "") and getattr(previous, "block_id", "") == getattr(current, "block_id", ""))

    @staticmethod
    def _visually_close(previous: Any, current: Any) -> bool:
        if getattr(previous, "page_no", None) != getattr(current, "page_no", None):
            return False
        previous_bbox = getattr(previous, "bbox", None)
        current_bbox = getattr(current, "bbox", None)
        if previous_bbox is None or current_bbox is None:
            return False
        previous_height = max(1.0, previous_bbox.y1 - previous_bbox.y0)
        vertical_gap = current_bbox.y0 - previous_bbox.y1
        indent_delta = abs(current_bbox.x0 - previous_bbox.x0)
        return vertical_gap <= previous_height * 1.4 and indent_delta <= max(previous_height * 2.5, 24.0)

    @staticmethod
    def _visually_continues_across_adjacent_pages(previous: Any, current: Any) -> bool:
        previous_page = getattr(previous, "page_no", None)
        current_page = getattr(current, "page_no", None)
        if previous_page is None or current_page is None or current_page != previous_page + 1:
            return False
        previous_bbox = getattr(previous, "bbox", None)
        current_bbox = getattr(current, "bbox", None)
        if previous_bbox is None or current_bbox is None:
            return False

        previous_height = max(1.0, previous_bbox.y1 - previous_bbox.y0)
        current_height = max(1.0, current_bbox.y1 - current_bbox.y0)
        indent_delta = abs(current_bbox.x0 - previous_bbox.x0)
        if indent_delta > max(previous_height * 3.0, current_height * 3.0, 36.0):
            return False

        max_coordinate = max(
            abs(previous_bbox.y0),
            abs(previous_bbox.y1),
            abs(current_bbox.y0),
            abs(current_bbox.y1),
        )
        if max_coordinate <= 2.0:
            previous_near_bottom = previous_bbox.y0 >= 0.55 or previous_bbox.y1 >= 0.68
            current_near_top = current_bbox.y0 <= 0.35
        else:
            previous_near_bottom = previous_bbox.y0 >= 500.0 or previous_bbox.y1 >= 650.0
            current_near_top = current_bbox.y0 <= 180.0
        return previous_near_bottom and current_near_top

    @staticmethod
    def _looks_like_standalone_label(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        return bool(re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9]{1,8}[:：]?", compact))

    @staticmethod
    def _with_flag(unit: Any, flag: str) -> Any:
        flags = tuple(dict.fromkeys([*getattr(unit, "split_flags", ()), flag]))
        try:
            return replace(unit, split_flags=flags)
        except TypeError:
            return unit

    def _merge_units(self, previous: Any, current: Any) -> Any:
        separator = "\n"
        text = f"{getattr(previous, 'text', '')}{separator}{getattr(current, 'text', '')}"
        char_boxes = [
            *list(getattr(previous, "char_boxes", []) or []),
            None,
            *list(getattr(current, "char_boxes", []) or []),
        ]
        page_numbers = tuple(
            dict.fromkeys([
                *self._tuple_attr(previous, "page_numbers", getattr(previous, "page_no", None)),
                *self._tuple_attr(current, "page_numbers", getattr(current, "page_no", None)),
            ])
        )
        evidences = tuple([
            *self._tuple_attr(previous, "evidences", getattr(previous, "evidence", None)),
            *self._tuple_attr(current, "evidences", getattr(current, "evidence", None)),
        ])
        source_block_ids = tuple(
            dict.fromkeys([
                *self._tuple_attr(previous, "source_block_ids", getattr(previous, "block_id", None)),
                *self._tuple_attr(current, "source_block_ids", getattr(current, "block_id", None)),
            ])
        )
        merge_flags = [self.paragraph_merged_flag]
        if self._is_adjacent_page_pair(previous, current):
            merge_flags.append(self.cross_page_merged_flag)
        split_flags = tuple(
            dict.fromkeys([
                *getattr(previous, "split_flags", ()),
                *getattr(current, "split_flags", ()),
                *merge_flags,
            ])
        )
        try:
            return replace(
                previous,
                text=text,
                char_boxes=char_boxes,
                bbox=self._merged_bbox(previous, current),
                evidence=evidences[0] if evidences else getattr(previous, "evidence", None),
                block_id="+".join(source_block_ids) if source_block_ids else getattr(previous, "block_id", ""),
                split_flags=split_flags,
                page_numbers=page_numbers,
                evidences=evidences,
                source_block_ids=source_block_ids,
            )
        except TypeError:
            fallback = self._with_flag(previous, self.paragraph_merged_flag)
            if self._is_adjacent_page_pair(previous, current):
                fallback = self._with_flag(fallback, self.cross_page_merged_flag)
            return fallback

    @staticmethod
    def _is_adjacent_page_pair(previous: Any, current: Any) -> bool:
        previous_page = getattr(previous, "page_no", None)
        current_page = getattr(current, "page_no", None)
        return previous_page is not None and current_page == previous_page + 1

    @staticmethod
    def _tuple_attr(unit: Any, attr: str, fallback: Any = None) -> tuple[Any, ...]:
        value = getattr(unit, attr, ())
        if value:
            return tuple(value)
        if fallback is None:
            return ()
        return (fallback,)

    @staticmethod
    def _merged_bbox(previous: Any, current: Any) -> Any:
        previous_bbox = getattr(previous, "bbox", None)
        current_bbox = getattr(current, "bbox", None)
        if previous_bbox is None or current_bbox is None:
            return previous_bbox
        if getattr(previous, "page_no", None) != getattr(current, "page_no", None):
            return previous_bbox
        return previous_bbox.__class__(
            x0=min(previous_bbox.x0, current_bbox.x0),
            y0=min(previous_bbox.y0, current_bbox.y0),
            x1=max(previous_bbox.x1, current_bbox.x1),
            y1=max(previous_bbox.y1, current_bbox.y1),
        )
