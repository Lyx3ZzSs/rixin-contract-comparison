from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.models import BBox, CharBox, Document, EvidenceBox, TextBlock, TextRange, DiffItem
from app.services.diff_engine import DiffEngine
from app.services.normalizer import TextNormalizer
from app.services.table_html_parser import parse_html_tables
from app.utils.id_utils import generate_diff_id


@dataclass
class CoverValuePart:
    block: TextBlock
    text: str
    start: int
    end: int
    block_start: int | None = None
    fallback_bbox: BBox | None = None


@dataclass
class CoverField:
    key: str
    label: str
    value: str
    evidences: list[EvidenceBox] = field(default_factory=list)
    value_parts: list[CoverValuePart] = field(default_factory=list)


@dataclass
class CoverExtraText:
    value: str
    evidence: EvidenceBox
    block_id: str


@dataclass
class CoverExtraFragment:
    value: str
    evidence: EvidenceBox
    block_id: str
    layout_block_id: str = ""


@dataclass
class CoverExtraction:
    fields: dict[str, CoverField] = field(default_factory=dict)
    consumed_block_ids: set[str] = field(default_factory=set)


class CoverMetadataComparator:
    field_labels = {
        "contract_no": "合同编号",
        "project_title": "项目名称",
        "buyer": "甲方",
        "seller": "乙方",
        "tax_no": "税号",
        "sign_place": "签订地点",
        "sign_date": "签订日期",
    }
    label_to_key = {
        "合同编号": "contract_no",
        "项目名称": "project_title",
        "项目": "project_title",
        "甲方": "buyer",
        "买方": "buyer",
        "乙方": "seller",
        "卖方": "seller",
        "税号": "tax_no",
        "纳税人识别号": "tax_no",
        "签订地点": "sign_place",
        "签订日期": "sign_date",
    }
    field_order = ["contract_no", "project_title", "buyer", "seller", "tax_no", "sign_place", "sign_date"]
    extra_field_labels = ("账号", "税号", "电话", "传真", "开户行", "法定代表人", "委托代理人", "通讯地址")

    def __init__(self, normalizer: TextNormalizer | None = None) -> None:
        self.normalizer = normalizer or TextNormalizer()

    def build_diffs(self, original: Document, compare: Document, start_index: int = 1) -> list[DiffItem]:
        original_extraction = self._extract_cover(original)
        compare_extraction = self._extract_cover(compare)
        original_fields = original_extraction.fields
        compare_fields = compare_extraction.fields
        diffs: list[DiffItem] = []
        next_index = start_index
        for key in self.field_order:
            left = original_fields.get(key)
            right = compare_fields.get(key)
            if left is None and right is None:
                continue
            left_value = left.value if left else ""
            right_value = right.value if right else ""
            if self._normalize_value(key, left_value) == self._normalize_value(key, right_value):
                continue
            diffs.append(self._build_diff(key, left, right, next_index))
            next_index += 1
        extra_diffs = self._build_extra_text_diffs(original, compare, original_extraction, compare_extraction, next_index)
        diffs.extend(extra_diffs)
        return diffs

    def extract(self, document: Document) -> dict[str, CoverField]:
        return self._extract_cover(document).fields

    def _extract_cover(self, document: Document) -> CoverExtraction:
        blocks = self._cover_blocks(document)
        fields: dict[str, CoverField] = {}
        consumed: set[str] = set()
        for index, block in enumerate(blocks):
            table_fields = self._extract_cover_table_fields(block)
            if table_fields:
                for key, field in table_fields.items():
                    self._set_field(fields, key, field.value, field.evidences, field.value_parts)
                consumed.add(block.block_id)
                continue

            lines = self._lines(block.text)
            if not lines:
                continue
            for line_index, line in enumerate(lines):
                parsed = self._parse_labeled_line(line)
                if parsed is None:
                    continue
                key, value = parsed
                evidences = [self._evidence(block, line)]
                value_parts = self._value_parts_from_line(block, value) if value else []
                if not value and key != "contract_no":
                    value, extra_evidences, extra_ids, extra_parts = self._nearby_value(block, blocks)
                    evidences.extend(extra_evidences)
                    consumed.update(extra_ids)
                    value_parts.extend(extra_parts)
                if not value and key != "contract_no":
                    value, extra_evidences, extra_ids, extra_parts = self._next_value(lines, line_index, blocks, index)
                    evidences.extend(extra_evidences)
                    consumed.update(extra_ids)
                    value_parts.extend(extra_parts)
                if value:
                    self._set_field(fields, key, value, evidences, value_parts)
                    consumed.add(block.block_id)

        first_page_no = min((block.page_no for block in blocks), default=1)
        title_blocks = [
            block
            for block in blocks
            if block.page_no == first_page_no and block.block_id not in consumed and self._is_title_candidate(block)
        ]
        if title_blocks:
            title_lines: list[str] = []
            evidences: list[EvidenceBox] = []
            for block in title_blocks:
                added = False
                for line in self._lines(block.text):
                    if self._is_title_line(line):
                        title_lines.append(line)
                        added = True
                if added:
                    evidences.append(self._evidence(block, block.text))
            value = "\n".join(title_lines).strip()
            if value:
                consumed.update(block.block_id for block in title_blocks)
                fields["project_title"] = CoverField(
                    key="project_title",
                    label=self.field_labels["project_title"],
                    value=value,
                    evidences=evidences,
                )
        return CoverExtraction(fields=fields, consumed_block_ids=consumed)

    def _cover_blocks(self, document: Document) -> list[TextBlock]:
        blocks: list[TextBlock] = []
        for page in document.pages:
            for block in page.blocks:
                text = self._clean_text(block.text)
                if not text:
                    continue
                if self._is_body_start(text):
                    return blocks
                blocks.append(block)
        return blocks

    def _parse_labeled_line(self, line: str) -> tuple[str, str] | None:
        compact = re.sub(r"\s+", "", line)
        for label, key in self.label_to_key.items():
            if compact in {label, f"{label}:", f"{label}："}:
                return key, ""
            match = re.match(rf"^{re.escape(label)}[:：]\s*(.*)$", line)
            if match:
                return key, match.group(1).strip()
        return None

    def _extract_cover_table_fields(self, block: TextBlock) -> dict[str, CoverField]:
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
                    parsed = self._parse_table_label_cell(cell.text)
                    if parsed is None:
                        continue
                    key, inline_value = parsed
                    value_cells = [item for item in cells[index + 1 :] if self._clean_text(item.text)]
                    if value_cells:
                        field = self._field_from_table_cells(block, key, value_cells)
                    elif inline_value:
                        field = self._field_from_inline_table_value(block, key, inline_value, cell.bbox)
                    else:
                        continue
                    if field.value:
                        fields.setdefault(key, field)
                    break
        return fields

    def _parse_table_label_cell(self, text: str) -> tuple[str, str] | None:
        cleaned = self._clean_text(text)
        parsed = self._parse_labeled_line(cleaned)
        if parsed is not None:
            return parsed
        compact = re.sub(r"\s+", "", cleaned).rstrip(":：")
        key = self.label_to_key.get(compact)
        return (key, "") if key else None

    def _field_from_table_cells(self, block: TextBlock, key: str, cells: list) -> CoverField:
        texts: list[str] = []
        evidences: list[EvidenceBox] = []
        value_parts: list[CoverValuePart] = []
        cursor = 0
        for cell in cells:
            text = self._clean_text(cell.text)
            if not text:
                continue
            if texts:
                cursor += 1
            bbox = cell.bbox or block.layout_bbox or block.bbox
            evidences.append(EvidenceBox(page_no=block.page_no, bbox=bbox, method="cover_metadata", text=text))
            block_start = self._find_text_index(block.text, text)
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
            label=self.field_labels.get(key, key),
            value=value,
            evidences=evidences,
            value_parts=value_parts,
        )

    def _field_from_inline_table_value(
        self,
        block: TextBlock,
        key: str,
        value: str,
        bbox: BBox | None,
    ) -> CoverField:
        text = self._clean_text(value)
        fallback_bbox = bbox or block.layout_bbox or block.bbox
        return CoverField(
            key=key,
            label=self.field_labels.get(key, key),
            value=text,
            evidences=[EvidenceBox(page_no=block.page_no, bbox=fallback_bbox, method="cover_metadata", text=text)],
            value_parts=[
                CoverValuePart(
                    block=block,
                    text=text,
                    start=0,
                    end=len(text),
                    block_start=self._find_text_index(block.text, text),
                    fallback_bbox=fallback_bbox,
                )
            ],
        )

    def _next_value(
        self,
        lines: list[str],
        line_index: int,
        blocks: list[TextBlock],
        block_index: int,
    ) -> tuple[str, list[EvidenceBox], set[str], list[CoverValuePart]]:
        current_block = blocks[block_index]
        values: list[str] = []
        for line in lines[line_index + 1 :]:
            if self._parse_labeled_line(line):
                break
            if not self._is_noise_line(line):
                values.append(line)
        if values:
            value = "\n".join(values).strip()
            return value, [self._evidence(current_block, value)], set(), self._value_parts_from_lines(current_block, values)

        for next_block in blocks[block_index + 1 : block_index + 4]:
            if next_block.page_no != current_block.page_no:
                break
            next_lines = self._lines(next_block.text)
            if not next_lines:
                continue
            if self._parse_labeled_line(next_lines[0]) or self._is_noise_line(next_lines[0]):
                continue
            return (
                next_lines[0],
                [self._evidence(next_block, next_lines[0])],
                {next_block.block_id},
                self._value_parts_from_line(next_block, next_lines[0]),
            )
        return "", [], set(), []

    def _nearby_value(self, label_block: TextBlock, blocks: list[TextBlock]) -> tuple[str, list[EvidenceBox], set[str], list[CoverValuePart]]:
        candidates: list[tuple[float, TextBlock, str]] = []
        label_mid_y = (label_block.bbox.y0 + label_block.bbox.y1) / 2
        label_height = max(1.0, label_block.bbox.y1 - label_block.bbox.y0)
        for block in blocks:
            if block.block_id == label_block.block_id or block.page_no != label_block.page_no:
                continue
            lines = self._lines(block.text)
            if not lines:
                continue
            value = lines[0]
            if self._parse_labeled_line(value) or self._is_noise_line(value):
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
        evidences = [self._evidence(block, text) for _, block, text in candidates]
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
                    block_start=self._find_text_index(block.text, text),
                )
            )
            cursor += len(text)
        return value, evidences, block_ids, value_parts

    def _set_field(
        self,
        fields: dict[str, CoverField],
        key: str,
        value: str,
        evidences: list[EvidenceBox],
        value_parts: list[CoverValuePart] | None = None,
    ) -> None:
        if key in fields:
            return
        fields[key] = CoverField(
            key=key,
            label=self.field_labels.get(key, key),
            value=value,
            evidences=evidences,
            value_parts=value_parts or [],
        )

    def _build_diff(self, key: str, left: CoverField | None, right: CoverField | None, index: int) -> DiffItem:
        label = self.field_labels.get(key, key)
        if left and right:
            original_snippet, compare_snippet, original_ranges, compare_ranges = DiffEngine()._changed_snippets(
                left.value,
                right.value,
            )
            return DiffItem(
                diff_id=generate_diff_id(index),
                diff_type="MODIFY",
                title=f"封面字段：{label}",
                original_text=left.value,
                compare_text=right.value,
                original_snippet=original_snippet,
                compare_snippet=compare_snippet,
                readable_change=self._field_readable_change(label, left.value, right.value, original_snippet, compare_snippet),
                source_type="metadata",
                original_evidence=self._field_evidence(left, original_ranges),
                compare_evidence=self._field_evidence(right, compare_ranges),
                original_change_ranges=original_ranges,
                compare_change_ranges=compare_ranges,
            )
        if right:
            return DiffItem(
                diff_id=generate_diff_id(index),
                diff_type="ADD",
                title=f"封面字段：{label}",
                compare_text=right.value,
                compare_snippet=right.value,
                readable_change=f"新增封面字段【{label}】：{right.value}",
                source_type="metadata",
                compare_evidence=self._field_evidence(right, [TextRange(start=0, end=len(right.value), highlight_type="ADD")], "ADD"),
                compare_change_ranges=[TextRange(start=0, end=len(right.value), highlight_type="ADD")],
            )
        assert left is not None
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="DELETE",
            title=f"封面字段：{label}",
            original_text=left.value,
            original_snippet=left.value,
            readable_change=f"删除封面字段【{label}】：{left.value}",
            source_type="metadata",
            original_evidence=self._field_evidence(left, [TextRange(start=0, end=len(left.value), highlight_type="DELETE")], "DELETE"),
            original_change_ranges=[TextRange(start=0, end=len(left.value), highlight_type="DELETE")],
        )

    def _build_extra_text_diffs(
        self,
        original: Document,
        compare: Document,
        original_extraction: CoverExtraction,
        compare_extraction: CoverExtraction,
        start_index: int,
    ) -> list[DiffItem]:
        if not self._has_cover_metadata(original_extraction) and not self._has_cover_metadata(compare_extraction):
            return []
        original_extras = self._extra_texts(original, original_extraction)
        compare_extras = self._extra_texts(compare, compare_extraction)
        compare_keys = {self._normalize_extra(extra.value) for extra in compare_extras}
        original_keys = {self._normalize_extra(extra.value) for extra in original_extras}
        diffs: list[DiffItem] = []
        next_index = start_index

        for extra in original_extras:
            if self._normalize_extra(extra.value) in compare_keys:
                continue
            diffs.append(self._build_extra_delete(extra, next_index))
            next_index += 1

        for extra in compare_extras:
            if self._normalize_extra(extra.value) in original_keys:
                continue
            diffs.append(self._build_extra_add(extra, next_index))
            next_index += 1
        return diffs

    def _has_cover_metadata(self, extraction: CoverExtraction) -> bool:
        return any(key in extraction.fields for key in self.field_order)

    def _extra_texts(self, document: Document, extraction: CoverExtraction) -> list[CoverExtraText]:
        width_by_page = {page.page_no: page.width for page in document.pages}
        fragments: list[CoverExtraFragment] = []
        for block in self._cover_blocks(document):
            if block.block_id in extraction.consumed_block_ids or self._skip_extra_block(block):
                continue
            for line in self._lines(block.text):
                if self._skip_extra_line(line, block, width_by_page.get(block.page_no, 0.0)):
                    continue
                fragments.append(
                    CoverExtraFragment(
                        value=line,
                        evidence=self._evidence(block, line),
                        block_id=block.block_id,
                        layout_block_id=block.layout_block_id,
                    )
                )

        extras: list[CoverExtraText] = []
        seen: set[str] = set()
        for extra in self._merge_extra_fragments(fragments):
            key = self._normalize_extra(extra.value)
            if not key or key in seen:
                continue
            extras.append(extra)
            seen.add(key)
        return extras

    def _merge_extra_fragments(self, fragments: list[CoverExtraFragment]) -> list[CoverExtraText]:
        rows = self._group_extra_fragments_by_row(fragments)
        extras: list[CoverExtraText] = []
        for row in rows:
            extras.extend(self._merge_extra_row(row))
        return extras

    def _group_extra_fragments_by_row(self, fragments: list[CoverExtraFragment]) -> list[list[CoverExtraFragment]]:
        ordered = sorted(
            fragments,
            key=lambda item: (item.evidence.page_no, self._mid_y(item.evidence.bbox), item.evidence.bbox.x0),
        )
        rows: list[list[CoverExtraFragment]] = []
        current: list[CoverExtraFragment] = []
        current_mid_y = 0.0
        current_height = 0.0
        current_page = 0

        for fragment in ordered:
            bbox = fragment.evidence.bbox
            mid_y = self._mid_y(bbox)
            height = max(1.0, bbox.y1 - bbox.y0)
            threshold = max(4.0, min(current_height or height, height) * 0.8)
            if current and (fragment.evidence.page_no != current_page or abs(mid_y - current_mid_y) > threshold):
                rows.append(current)
                current = []
            if not current:
                current_page = fragment.evidence.page_no
                current_mid_y = mid_y
                current_height = height
            else:
                count = len(current)
                current_mid_y = (current_mid_y * count + mid_y) / (count + 1)
                current_height = max(current_height, height)
            current.append(fragment)

        if current:
            rows.append(current)
        return rows

    def _merge_extra_row(self, row: list[CoverExtraFragment]) -> list[CoverExtraText]:
        ordered = sorted(row, key=lambda item: (item.evidence.bbox.x0, item.evidence.bbox.y0, item.block_id))
        merged: list[CoverExtraText] = []
        current_value = ""
        current_bbox: BBox | None = None
        current_page = 0
        current_block_ids: list[str] = []
        previous: CoverExtraFragment | None = None

        def flush() -> None:
            nonlocal current_value, current_bbox, current_page, current_block_ids, previous
            value = self._clean_text(current_value)
            if value and current_bbox is not None:
                merged.append(
                    CoverExtraText(
                        value=value,
                        evidence=EvidenceBox(page_no=current_page, bbox=current_bbox, method="cover_extra", text=value),
                        block_id="+".join(current_block_ids),
                    )
                )
            current_value = ""
            current_bbox = None
            current_page = 0
            current_block_ids = []
            previous = None

        for fragment in ordered:
            value = self._clean_text(fragment.value)
            if not value:
                continue
            if current_value and self._starts_extra_field(value):
                flush()
            elif current_value and previous is not None and not self._should_join_extra_fragments(previous, fragment, current_value):
                flush()

            if not current_value:
                current_value = value
                current_bbox = fragment.evidence.bbox
                current_page = fragment.evidence.page_no
                current_block_ids = [fragment.block_id]
            else:
                current_value += value
                assert current_bbox is not None
                current_bbox = self._union(current_bbox, fragment.evidence.bbox)
                current_block_ids.append(fragment.block_id)
            previous = fragment

        flush()
        return merged

    def _starts_extra_field(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
        return bool(re.match(rf"^({'|'.join(map(re.escape, self.extra_field_labels))})[:：]", compact))

    def _should_join_extra_fragments(
        self,
        previous: CoverExtraFragment,
        current: CoverExtraFragment,
        current_value: str,
    ) -> bool:
        if previous.evidence.page_no != current.evidence.page_no:
            return False
        if previous.layout_block_id and previous.layout_block_id == current.layout_block_id:
            return True
        previous_bbox = previous.evidence.bbox
        current_bbox = current.evidence.bbox
        height = max(previous_bbox.y1 - previous_bbox.y0, current_bbox.y1 - current_bbox.y0, 1.0)
        gap = current_bbox.x0 - previous_bbox.x1
        if current_value.rstrip().endswith((':', '：')):
            return gap <= max(80.0, height * 5)
        return gap <= max(24.0, height * 1.5)

    def _build_extra_delete(self, extra: CoverExtraText, index: int) -> DiffItem:
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="DELETE",
            title="封面额外文本",
            original_text=extra.value,
            original_snippet=extra.value,
            readable_change=f"删除封面额外文本：{extra.value}",
            source_type="metadata",
            original_evidence=self._mark_extra([extra.evidence], "DELETE"),
            original_change_ranges=[TextRange(start=0, end=len(extra.value), highlight_type="DELETE")],
        )

    def _build_extra_add(self, extra: CoverExtraText, index: int) -> DiffItem:
        return DiffItem(
            diff_id=generate_diff_id(index),
            diff_type="ADD",
            title="封面额外文本",
            compare_text=extra.value,
            compare_snippet=extra.value,
            readable_change=f"新增封面额外文本：{extra.value}",
            source_type="metadata",
            compare_evidence=self._mark_extra([extra.evidence], "ADD"),
            compare_change_ranges=[TextRange(start=0, end=len(extra.value), highlight_type="ADD")],
        )

    def _mark(self, evidences: list[EvidenceBox], highlight_type: str) -> list[EvidenceBox]:
        return [evidence.model_copy(update={"highlight_type": highlight_type, "method": "cover_metadata"}) for evidence in evidences]

    def _mark_extra(self, evidences: list[EvidenceBox], highlight_type: str) -> list[EvidenceBox]:
        return [evidence.model_copy(update={"highlight_type": highlight_type, "method": "cover_extra"}) for evidence in evidences]

    def _evidence(self, block: TextBlock, text: str, bbox_override: BBox | None = None) -> EvidenceBox:
        return EvidenceBox(page_no=block.page_no, bbox=bbox_override or block.bbox, method="cover_metadata", text=text[:300])

    def _field_evidence(
        self,
        field: CoverField,
        ranges: list[TextRange],
        fallback_highlight_type: str | None = None,
    ) -> list[EvidenceBox]:
        evidences: list[EvidenceBox] = []
        for text_range in ranges:
            evidences.extend(self._evidence_for_field_range(field, text_range, fallback_highlight_type))
        return evidences

    def _evidence_for_field_range(
        self,
        field: CoverField,
        text_range: TextRange,
        fallback_highlight_type: str | None = None,
    ) -> list[EvidenceBox]:
        start = max(0, text_range.start)
        end = min(len(field.value), text_range.end)
        if start >= end:
            return []
        precise: list[EvidenceBox] = []
        for part in field.value_parts:
            overlap_start = max(start, part.start)
            overlap_end = min(end, part.end)
            if overlap_start >= overlap_end:
                continue
            local_start = overlap_start - part.start
            local_end = overlap_end - part.start
            precise.extend(
                self._evidence_for_block_text_range(
                    part.block,
                    part.text,
                    local_start,
                    local_end,
                    text_range.highlight_type,
                    fallback_highlight_type,
                    block_start=part.block_start,
                    fallback_bbox=part.fallback_bbox,
                )
            )
        if precise:
            return precise
        fragment = field.value[start:end]
        highlight_type = fallback_highlight_type or text_range.highlight_type
        return [
            evidence.model_copy(update={"text": fragment, "highlight_type": highlight_type, "method": "cover_metadata"})
            for evidence in field.evidences
            if fragment and fragment in evidence.text
        ] or [
            evidence.model_copy(update={"highlight_type": highlight_type, "method": "cover_metadata"})
            for evidence in field.evidences
        ]

    def _evidence_for_block_text_range(
        self,
        block: TextBlock,
        text: str,
        start: int,
        end: int,
        highlight_type: str,
        fallback_highlight_type: str | None = None,
        block_start: int | None = None,
        fallback_bbox: BBox | None = None,
    ) -> list[EvidenceBox]:
        effective_highlight_type = fallback_highlight_type or highlight_type
        fragment = text[start:end]
        if not block.char_boxes:
            return [self._evidence(block, fragment, bbox_override=fallback_bbox).model_copy(update={"highlight_type": effective_highlight_type})]
        if block_start is None:
            block_start = self._find_text_index(block.text, text)
        if block_start is None:
            return [self._evidence(block, fragment, bbox_override=fallback_bbox).model_copy(update={"highlight_type": effective_highlight_type})]
        wanted_start = block_start + start
        wanted_end = block_start + end
        char_boxes = [
            char_box
            for char_box in block.char_boxes
            if char_box.text_index is not None and wanted_start <= char_box.text_index < wanted_end
        ]
        if not char_boxes:
            return [self._evidence(block, fragment, bbox_override=fallback_bbox).model_copy(update={"highlight_type": effective_highlight_type})]
        return [
            EvidenceBox(
                page_no=page_no,
                bbox=bbox,
                method="cover_metadata",
                text=fragment,
                highlight_type=highlight_type,
            )
            for page_no, bbox, fragment in self._merge_char_boxes(char_boxes)
        ]

    def _merge_char_boxes(self, char_boxes: list[CharBox]) -> list[tuple[int, BBox, str]]:
        if not char_boxes:
            return []
        ordered = sorted(char_boxes, key=lambda item: (item.page_no, item.bbox.y0, item.bbox.x0))
        merged: list[tuple[int, BBox, str]] = []
        current_page = ordered[0].page_no
        current_bbox = ordered[0].bbox
        current_text = ordered[0].char
        for char_box in ordered[1:]:
            if char_box.page_no == current_page and abs(self._mid_y(char_box.bbox) - self._mid_y(current_bbox)) <= 4.0:
                current_bbox = self._union(current_bbox, char_box.bbox)
                current_text += char_box.char
                continue
            merged.append((current_page, self._pad(current_bbox), current_text))
            current_page = char_box.page_no
            current_bbox = char_box.bbox
            current_text = char_box.char
        merged.append((current_page, self._pad(current_bbox), current_text))
        return merged

    def _is_title_candidate(self, block: TextBlock) -> bool:
        text = self.normalizer.normalize(block.text)
        if not text:
            return False
        block_type = (block.block_type or "").lower()
        if block_type in {"header", "footer", "page_header", "page_footer", "number", "table", "seal"}:
            return False
        return any(self._is_title_line(line) for line in self._lines(text))

    def _is_title_line(self, line: str) -> bool:
        if self._is_noise_line(line) or self._parse_labeled_line(line):
            return False
        compact = re.sub(r"\s+", "", line)
        if re.fullmatch(r"\d+\s*(套|台|个|项|批|份).+", compact):
            return True
        return bool(re.search(r"(合同|系统|项目|采购|中广核)", compact)) and len(compact) <= 80

    def _is_noise_line(self, line: str) -> bool:
        compact = re.sub(r"\s+", "", line)
        return bool(re.fullmatch(r"(第)?\d+页", compact) or re.fullmatch(r"共\d+页第\d+页", compact))

    def _skip_extra_block(self, block: TextBlock) -> bool:
        block_type = (block.block_type or "").lower()
        return block_type in {"header", "footer", "page_header", "page_footer", "number", "table", "table_title", "seal"}

    def _skip_extra_line(self, line: str, block: TextBlock, page_width: float) -> bool:
        if self._is_noise_line(line) or self._parse_labeled_line(line) or self._is_title_line(line):
            return True
        compact = re.sub(r"\s+", "", line)
        if not compact:
            return True
        return self._is_edge_noise(compact, block, page_width)

    def _is_edge_noise(self, compact: str, block: TextBlock, page_width: float) -> bool:
        if page_width <= 0 or len(compact) > 4:
            return False
        margin = page_width * 0.04
        return block.bbox.x0 <= margin or block.bbox.x1 >= page_width - margin

    def _value_parts_from_line(self, block: TextBlock, value: str) -> list[CoverValuePart]:
        if not value:
            return []
        start = self._find_text_index(block.text, value)
        return [CoverValuePart(block=block, text=value, start=0, end=len(value), block_start=start)]

    def _value_parts_from_lines(self, block: TextBlock, values: list[str]) -> list[CoverValuePart]:
        parts: list[CoverValuePart] = []
        field_cursor = 0
        source_cursor = 0
        for value in values:
            if not value:
                continue
            if parts:
                field_cursor += 1
            block_start = self._find_text_index(block.text, value, start=source_cursor)
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

    def _find_text_index(self, source: str, value: str, start: int = 0) -> int | None:
        index = source.find(value, max(0, start))
        if index >= 0:
            return index
        normalized_value = unicodedata.normalize("NFKC", value)
        normalized_source = unicodedata.normalize("NFKC", source)
        index = normalized_source.find(normalized_value, max(0, start))
        return index if index >= 0 else None

    def _field_readable_change(
        self,
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

    def _union(self, left: BBox, right: BBox) -> BBox:
        return BBox(
            x0=min(left.x0, right.x0),
            y0=min(left.y0, right.y0),
            x1=max(left.x1, right.x1),
            y1=max(left.y1, right.y1),
        )

    def _pad(self, bbox: BBox) -> BBox:
        return BBox(x0=max(0.0, bbox.x0 - 0.8), y0=max(0.0, bbox.y0 - 0.8), x1=bbox.x1 + 0.8, y1=bbox.y1 + 0.8)

    def _mid_y(self, bbox: BBox) -> float:
        return (bbox.y0 + bbox.y1) / 2

    def _is_body_start(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        if compact == "正文" or "达成合同如下" in compact:
            return True
        first_line = text.strip().splitlines()[0] if text.strip() else ""
        return bool(re.match(r"^(第[一二三四五六七八九十百千万0-9]+[章节条]|[一二三四五六七八九十]+、)", first_line))

    def _normalize_value(self, key: str, value: str) -> str:
        text = self.normalizer.normalize_for_match(value)
        if key in {"contract_no", "sign_date"}:
            text = re.sub(r"\s+", "", text)
        return text

    def _normalize_extra(self, value: str) -> str:
        text = unicodedata.normalize("NFKC", value or "").replace("\r", "\n")
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        text = "".join(line for line in lines if line)
        text = re.sub(r"\s+", "", text)
        text = re.sub(r"[，。；：、“”‘’（）()\[\]【】《》,.!?:;\"']", "", text)
        return text.lower()

    def _lines(self, text: str) -> list[str]:
        return [line.strip() for line in self._clean_text(text).splitlines() if line.strip()]

    def _clean_text(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text or "").replace("\r", "\n")
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line).strip()
