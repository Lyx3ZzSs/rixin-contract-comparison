from __future__ import annotations

import re
import unicodedata

from app.models import TextBlock
from app.services.normalizer import TextNormalizer

from .constants import LABEL_TO_KEY


def parse_labeled_line(line: str) -> tuple[str, str] | None:
    compact = re.sub(r"\s+", "", line)
    for label, key in LABEL_TO_KEY.items():
        if compact in {label, f"{label}:", f"{label}："}:
            return key, ""
        match = re.match(rf"^{re.escape(label)}[:：]\s*(.*)$", line)
        if match:
            return key, match.group(1).strip()
    return None


def is_title_candidate(normalizer: TextNormalizer, block: TextBlock) -> bool:
    text = normalizer.normalize(block.text)
    if not text:
        return False
    block_type = (block.block_type or "").lower()
    if block_type in {
        "header",
        "footer",
        "page_header",
        "page_footer",
        "number",
        "table",
        "seal",
        "seal_region",
        "attachment_header",
        "edge_noise",
    }:
        return False
    return any(is_title_line(line) for line in lines(text))


def is_title_line(line: str) -> bool:
    if is_noise_line(line) or parse_labeled_line(line):
        return False
    compact = re.sub(r"\s+", "", line)
    if re.search(r"(买卖双方|甲乙双方|达成(?:以下协议|合同如下|如下协议))", compact):
        return False
    if re.fullmatch(r"\d+\s*(套|台|个|项|批|份).+", compact):
        return True
    return bool(re.search(r"(合同|系统|项目|采购|中广核)", compact)) and len(compact) <= 80


def is_noise_line(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    return bool(re.fullmatch(r"(第)?\d+页", compact) or re.fullmatch(r"共\d+页第\d+页", compact))


def skip_extra_block(block: TextBlock) -> bool:
    block_type = (block.block_type or "").lower()
    if block_type in {"header", "page_header"} and _is_unmatched_cover_extra(block):
        return False
    return block_type in {
        "header",
        "footer",
        "page_header",
        "page_footer",
        "number",
        "table",
        "table_title",
        "seal",
        "seal_region",
        "attachment_header",
        "edge_noise",
    }


def _is_unmatched_cover_extra(block: TextBlock) -> bool:
    return (
        not block.layout_block_id
        and (block.source or "").lower().endswith("_unmatched")
        and (block.layout_match_status or "") == "meaningful_unmatched"
    )


def skip_extra_line(line: str, block: TextBlock, page_width: float) -> bool:
    if is_noise_line(line) or parse_labeled_line(line) or is_title_line(line):
        return True
    compact = re.sub(r"\s+", "", line)
    if not compact:
        return True
    return is_edge_noise(compact, block, page_width)


def is_edge_noise(compact: str, block: TextBlock, page_width: float) -> bool:
    if page_width <= 0 or len(compact) > 4:
        return False
    margin = page_width * 0.04
    return block.bbox.x0 <= margin or block.bbox.x1 >= page_width - margin


def normalize_value(normalizer: TextNormalizer, key: str, value: str) -> str:
    text = normalizer.normalize_for_match(value)
    if key in {"contract_no", "sign_date"}:
        text = re.sub(r"\s+", "", text)
    return text


def normalize_extra(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "").replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    text = "".join(line for line in lines if line)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[_＿﹍﹎]+", "", text)
    text = re.sub(r"[，。；：、“”‘’（）()\[\]【】《》,.!?:;\"']", "", text)
    return text.lower()


def lines(text: str) -> list[str]:
    return [line.strip() for line in clean_text(text).splitlines() if line.strip()]


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").replace("\r", "\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def field_readable_change(
    label: str,
    original: str,
    compare: str,
    original_snippet: str,
    compare_snippet: str,
) -> str:
    if original_snippet and not compare_snippet:
        return f"封面字段【{label}】删除：{original_snippet}"
    if compare_snippet and not original_snippet:
        return f"封面字段【{label}】新增：{compare_snippet}"
    if original_snippet or compare_snippet:
        return f"封面字段【{label}】变更：{original_snippet} -> {compare_snippet}"
    return f"封面字段【{label}】变更：{original} -> {compare}"


def value_parts_from_line(block: TextBlock, value: str) -> list:
    from .types import CoverValuePart

    if not value:
        return []
    start = find_text_index(block.text, value)
    return [CoverValuePart(block=block, text=value, start=0, end=len(value), block_start=start)]


def value_parts_from_lines(block: TextBlock, values: list[str]) -> list:
    from .types import CoverValuePart

    parts: list[CoverValuePart] = []
    field_cursor = 0
    source_cursor = 0
    for value in values:
        if not value:
            continue
        if parts:
            field_cursor += 1
        block_start = find_text_index(block.text, value, start=source_cursor)
        if block_start is not None:
            source_cursor = block_start + len(value)
        parts.append(
            CoverValuePart(
                block=block,
                text=value,
                start=field_cursor,
                end=field_cursor + len(value),
                block_start=block_start,
            )
        )
        field_cursor += len(value)
    return parts


def find_text_index(source: str, value: str, start: int = 0) -> int | None:
    index = source.find(value, max(0, start))
    if index >= 0:
        return index
    normalized_value = unicodedata.normalize("NFKC", value)
    normalized_source = unicodedata.normalize("NFKC", source)
    index = normalized_source.find(normalized_value, max(0, start))
    return index if index >= 0 else None
