from __future__ import annotations

import re

from app.models import BBox, Document, EvidenceBox, TextBlock

from .constants import FIELD_LABELS, LABEL_TO_KEY
from .evidence import build_evidence
from .geometry import is_body_start
from .patterns import (
    clean_text,
    find_text_index,
    is_title_candidate,
    is_title_line,
    lines,
    parse_labeled_line,
    value_parts_from_line,
)
from .types import CoverExtraction, CoverField, CoverValuePart
from .value_resolver import nearby_value, next_value, set_field


def extract(normalizer, document: Document) -> dict[str, CoverField]:
    return _extract_cover(normalizer, document).fields


def _extract_cover(normalizer, document: Document) -> CoverExtraction:
    blocks = _cover_blocks(document)
    fields: dict[str, CoverField] = {}
    consumed: set[str] = set()
    for index, block in enumerate(blocks):
        table_fields = _extract_cover_table_fields(block)
        if table_fields:
            for key, field in table_fields.items():
                set_field(fields, key, field.value, field.evidences, field.value_parts)
            consumed.add(block.block_id)
            continue

        block_lines = lines(block.text)
        if not block_lines:
            continue
        for line_index, line in enumerate(block_lines):
            parsed = parse_labeled_line(line)
            if parsed is None:
                continue
            key, value = parsed
            evidences = [build_evidence(block, line)]
            vparts = value_parts_from_line(block, value) if value else []
            consumed_ids: set[str] = set()
            if not value and not _is_contract_number_key(key):
                value, extra_evidences, extra_ids, extra_parts = nearby_value(block, blocks)
                evidences.extend(extra_evidences)
                consumed_ids.update(extra_ids)
                vparts.extend(extra_parts)
            if not value and not _is_contract_number_key(key):
                value, extra_evidences, extra_ids, extra_parts = next_value(block_lines, line_index, blocks, index)
                evidences.extend(extra_evidences)
                consumed_ids.update(extra_ids)
                vparts.extend(extra_parts)
            if value and _valid_field_value(key, value):
                set_field(fields, key, value, evidences, vparts)
                consumed.update(consumed_ids)
                consumed.add(block.block_id)

    first_page_no = min((block.page_no for block in blocks), default=1)
    title_blocks = [
        block
        for block in blocks
        if block.page_no == first_page_no and block.block_id not in consumed and is_title_candidate(normalizer, block)
    ]
    if title_blocks and "project_title" not in fields:
        title_lines: list[str] = []
        evidences: list[EvidenceBox] = []
        for block in title_blocks:
            added = False
            for line in lines(block.text):
                if is_title_line(line):
                    title_lines.append(line)
                    added = True
            if added:
                evidences.append(build_evidence(block, block.text))
        value = "\n".join(title_lines).strip()
        if value:
            consumed.update(block.block_id for block in title_blocks)
            fields["project_title"] = CoverField(
                key="project_title",
                label=FIELD_LABELS["project_title"],
                value=value,
                evidences=evidences,
            )
    preamble_titles: dict[int, CoverField] = {}
    later_title_blocks: dict[int, list[TextBlock]] = {}
    for block in blocks:
        if block.page_no == first_page_no or block.block_id in consumed:
            continue
        block_type = (block.block_type or "").lower()
        if block_type not in {"doc_title", "title"}:
            continue
        later_title_blocks.setdefault(block.page_no, []).append(block)
    for page_no, page_blocks in sorted(later_title_blocks.items()):
        field = _preamble_title_field(page_no, page_blocks)
        if field is None:
            continue
        preamble_titles[page_no] = field
        consumed.update(block.block_id for block in page_blocks)
    return CoverExtraction(
        fields=fields,
        consumed_block_ids=consumed,
        preamble_titles=preamble_titles,
    )


def _preamble_title_field(page_no: int, blocks: list[TextBlock]) -> CoverField | None:
    ordered = sorted(blocks, key=lambda block: (block.bbox.y0, block.bbox.x0, block.block_id))
    title_lines: list[str] = []
    evidences: list[EvidenceBox] = []
    value_parts: list[CoverValuePart] = []
    cursor = 0
    for block in ordered:
        block_lines = [line for line in lines(block.text) if not parse_labeled_line(line)]
        block_text = "\n".join(block_lines).strip()
        if not block_text:
            continue
        if title_lines:
            cursor += 1
        title_lines.append(block_text)
        evidences.append(build_evidence(block, block_text))
        value_parts.append(
            CoverValuePart(
                block=block,
                text=block_text,
                start=cursor,
                end=cursor + len(block_text),
                block_start=find_text_index(block.text, block_text),
            )
        )
        cursor += len(block_text)
    value = "\n".join(title_lines).strip()
    if not value:
        return None
    return CoverField(
        key=f"preamble_title_{page_no}",
        label=f"前置标题（第{page_no}页）",
        value=value,
        evidences=evidences,
        value_parts=value_parts,
    )


def _valid_field_value(key: str, value: str) -> bool:
    if key != "sign_date":
        return True
    compact = re.sub(r"\s+", "", value or "")
    if len(re.findall(r"\d{4}", compact)) != 1:
        return False
    return bool(
        re.search(r"\d{4}年\d{1,2}月\d{1,2}日?", compact)
        or re.search(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}", compact)
    )


def _is_contract_number_key(key: str) -> bool:
    return key in {"contract_no", "contract_no_buyer", "contract_no_seller"}


def _cover_blocks(document: Document) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    skip_types = {"attachment_header", "seal_region", "edge_noise"}
    for page in document.pages:
        for block in page.blocks:
            if (block.block_type or "").lower() in skip_types:
                continue
            text = clean_text(block.text)
            if not text:
                continue
            if is_body_start(text, block.block_type):
                return blocks
            blocks.append(block)
    return blocks


def _extract_cover_table_fields(block: TextBlock) -> dict[str, CoverField]:
    from app.services.table_compare.html_parser import parse_html_tables

    html = block.raw_html or ""
    if "<table" not in html.lower():
        return {}
    try:
        cell_bboxes = [
            BBox(x0=b[0], y0=b[1], x1=b[2], y1=b[3])
            for b in block.table_cell_bboxes
            if len(b) >= 4
        ]
        tables = parse_html_tables(
            html,
            page_no=block.page_no,
            source="cover_table",
            source_block_id=block.block_id,
            cell_bboxes=cell_bboxes,
        )
    except Exception:
        return {}

    fields: dict[str, CoverField] = {}
    for table in tables:
        for row in table.rows:
            cells = sorted(row.cells, key=lambda cell: cell.col_index)
            for index, cell in enumerate(cells):
                parsed = _parse_table_label_cell(cell.text)
                if parsed is None:
                    continue
                key, inline_value = parsed
                value_cells = [item for item in cells[index + 1 :] if clean_text(item.text)]
                if value_cells:
                    field = _field_from_table_cells(block, key, value_cells)
                elif inline_value:
                    field = _field_from_inline_table_value(block, key, inline_value, cell.bbox)
                else:
                    continue
                if field.value:
                    fields.setdefault(key, field)
                break
    return fields


def _parse_table_label_cell(text: str) -> tuple[str, str] | None:
    cleaned = clean_text(text)
    parsed = parse_labeled_line(cleaned)
    if parsed is not None:
        return parsed
    compact = re.sub(r"\s+", "", cleaned).rstrip(":：")
    key = LABEL_TO_KEY.get(compact)
    return (key, "") if key else None


def _field_from_table_cells(block: TextBlock, key: str, cells: list) -> CoverField:
    texts: list[str] = []
    evidences: list[EvidenceBox] = []
    value_parts: list[CoverValuePart] = []
    cursor = 0
    for cell in cells:
        text = clean_text(cell.text)
        if not text:
            continue
        if texts:
            cursor += 1
        bbox = cell.bbox or block.layout_bbox or block.bbox
        evidences.append(EvidenceBox(page_no=block.page_no, bbox=bbox, method="cover_metadata", text=text))
        block_start = find_text_index(block.text, text)
        value_parts.append(
            CoverValuePart(
                block=block,
                text=text,
                start=cursor,
                end=cursor + len(text),
                block_start=block_start,
                fallback_bbox=bbox,
            )
        )
        texts.append(text)
        cursor += len(text)
    value = "\n".join(texts).strip()
    return CoverField(
        key=key,
        label=FIELD_LABELS.get(key, key),
        value=value,
        evidences=evidences,
        value_parts=value_parts,
    )


def _field_from_inline_table_value(
    block: TextBlock,
    key: str,
    value: str,
    bbox: BBox | None,
) -> CoverField:
    text = clean_text(value)
    fallback_bbox = bbox or block.layout_bbox or block.bbox
    return CoverField(
        key=key,
        label=FIELD_LABELS.get(key, key),
        value=text,
        evidences=[EvidenceBox(page_no=block.page_no, bbox=fallback_bbox, method="cover_metadata", text=text)],
        value_parts=[
            CoverValuePart(
                block=block,
                text=text,
                start=0,
                end=len(text),
                block_start=find_text_index(block.text, text),
                fallback_bbox=fallback_bbox,
            )
        ],
    )
