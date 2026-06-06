from __future__ import annotations

from app.models import EvidenceBox, TextBlock

from .evidence import build_evidence
from .patterns import (
    find_text_index,
    is_noise_line,
    lines,
    parse_labeled_line,
    value_parts_from_line,
    value_parts_from_lines,
)
from .types import CoverField, CoverValuePart


def next_value(
    lines_list: list[str],
    line_index: int,
    blocks: list[TextBlock],
    block_index: int,
) -> tuple[str, list[EvidenceBox], set[str], list[CoverValuePart]]:
    current_block = blocks[block_index]
    values: list[str] = []
    for line in lines_list[line_index + 1 :]:
        if parse_labeled_line(line):
            break
        if not is_noise_line(line):
            values.append(line)
    if values:
        value = "\n".join(values).strip()
        return value, [build_evidence(current_block, value)], set(), value_parts_from_lines(current_block, values)

    for next_block in blocks[block_index + 1 : block_index + 4]:
        if next_block.page_no != current_block.page_no:
            break
        next_lines = lines(next_block.text)
        if not next_lines:
            continue
        if parse_labeled_line(next_lines[0]) or is_noise_line(next_lines[0]):
            continue
        return (
            next_lines[0],
            [build_evidence(next_block, next_lines[0])],
            {next_block.block_id},
            value_parts_from_line(next_block, next_lines[0]),
        )
    return "", [], set(), []


def nearby_value(
    label_block: TextBlock,
    blocks: list[TextBlock],
) -> tuple[str, list[EvidenceBox], set[str], list[CoverValuePart]]:
    candidates: list[tuple[float, TextBlock, str]] = []
    label_mid_y = (label_block.bbox.y0 + label_block.bbox.y1) / 2
    label_height = max(1.0, label_block.bbox.y1 - label_block.bbox.y0)
    for block in blocks:
        if block.block_id == label_block.block_id or block.page_no != label_block.page_no:
            continue
        block_lines = lines(block.text)
        if not block_lines:
            continue
        value = block_lines[0]
        if parse_labeled_line(value) or is_noise_line(value):
            continue
        mid_y = (block.bbox.y0 + block.bbox.y1) / 2
        y_distance = abs(mid_y - label_mid_y)
        if y_distance > max(8.0, label_height * 1.2):
            continue
        x_gap = block.bbox.x0 - label_block.bbox.x1
        if x_gap < -label_height:
            continue
        candidates.append((y_distance * 10 + max(0.0, x_gap), block, value))
    if not candidates:
        return "", [], set(), []
    candidates.sort(key=lambda item: (item[1].bbox.x0, item[0], item[1].block_id))
    value = "".join(item[2] for item in candidates)
    evidences = [build_evidence(block, text) for _, block, text in candidates]
    block_ids = {block.block_id for _, block, _ in candidates}
    value_parts: list[CoverValuePart] = []
    cursor = 0
    for _, block, text in candidates:
        value_parts.append(
            CoverValuePart(
                block=block,
                text=text,
                start=cursor,
                end=cursor + len(text),
                block_start=find_text_index(block.text, text),
            )
        )
        cursor += len(text)
    return value, evidences, block_ids, value_parts


def set_field(
    fields: dict[str, CoverField],
    key: str,
    value: str,
    evidences: list[EvidenceBox],
    value_parts: list[CoverValuePart] | None = None,
) -> None:
    from .constants import FIELD_LABELS

    if key in fields:
        return
    fields[key] = CoverField(
        key=key,
        label=FIELD_LABELS.get(key, key),
        value=value,
        evidences=evidences,
        value_parts=value_parts or [],
    )
