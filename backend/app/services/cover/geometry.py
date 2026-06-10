from __future__ import annotations

import re

from app.models import BBox


def union_bboxes(left: BBox, right: BBox) -> BBox:
    return BBox(
        x0=min(left.x0, right.x0),
        y0=min(left.y0, right.y0),
        x1=max(left.x1, right.x1),
        y1=max(left.y1, right.y1),
    )


def pad_bbox(bbox: BBox) -> BBox:
    return BBox(x0=max(0.0, bbox.x0 - 0.8), y0=max(0.0, bbox.y0 - 0.8), x1=bbox.x1 + 0.8, y1=bbox.y1 + 0.8)


def mid_y(bbox: BBox) -> float:
    return (bbox.y0 + bbox.y1) / 2


def is_body_start(text: str, block_type: str = "") -> bool:
    compact = re.sub(r"\s+", "", text)
    if compact == "正文" or re.search(r"达成(?:合同如下|以下协议|如下协议)", compact):
        return True
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    if re.match(r"^(第[一二三四五六七八九十百千万0-9]+[章节条]|[一二三四五六七八九十]+、)", first_line):
        return True

    normalized_first_line = re.sub(r"\s+", "", first_line)
    if re.match(r"^\d{1,2}(?:\.\d{1,2}){0,3}[.、][\u4e00-\u9fffA-Za-z]", normalized_first_line):
        return True
    if re.match(r"^\d{1,2}(?:\.\d{1,2}){1,3}[\u4e00-\u9fffA-Za-z]", normalized_first_line):
        return True

    if (block_type or "").lower() == "paragraph_title":
        return bool(re.match(r"^\d{1,2}(?:\.\d{1,2}){0,3}[\u4e00-\u9fffA-Za-z]", normalized_first_line))
    return False
