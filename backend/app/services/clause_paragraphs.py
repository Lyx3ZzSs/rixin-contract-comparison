from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any


class ParagraphBuilder:
    """Build stable paragraph units while preserving source evidence metadata."""

    terminal_punctuation = tuple("。；;！？!?")
    continuation_punctuation = tuple("，,、：:（(")

    def build(
        self,
        units: Sequence[Any],
        *,
        parse_marker: Callable[[str], object | None],
    ) -> list[Any]:
        result: list[Any] = []
        for unit in units:
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
        if parse_marker(getattr(current, "text", "")) is not None:
            return False
        previous_text = str(getattr(previous, "text", "") or "").strip()
        current_text = str(getattr(current, "text", "") or "").strip()
        if not previous_text or not current_text:
            return False
        previous_marker = parse_marker(previous_text)
        if previous_marker is not None and not self._marker_title(previous_marker):
            return True
        if previous_text.endswith(self.continuation_punctuation):
            return True
        if previous_text.endswith(self.terminal_punctuation):
            return False
        if self._same_block(previous, current):
            return True
        return self._visually_close(previous, current) and not self._looks_like_standalone_label(current_text)

    @staticmethod
    def _marker_title(marker: object) -> str:
        if isinstance(marker, tuple) and len(marker) >= 2:
            return str(marker[1] or "")
        return ""

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
        split_flags = tuple(
            dict.fromkeys([
                *getattr(previous, "split_flags", ()),
                *getattr(current, "split_flags", ()),
                "PARAGRAPH_MERGED",
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
            return self._with_flag(previous, "PARAGRAPH_MERGED")

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
