from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.models import Document, EvidenceBox, TextBlock, TextRange, DiffItem
from app.services.normalizer import TextNormalizer
from app.utils.id_utils import generate_diff_id


@dataclass
class CoverField:
    key: str
    label: str
    value: str
    evidences: list[EvidenceBox] = field(default_factory=list)


class CoverMetadataComparator:
    field_labels = {
        "contract_no": "合同编号",
        "project_title": "项目名称",
        "buyer": "甲方",
        "seller": "乙方",
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
        "签订地点": "sign_place",
        "签订日期": "sign_date",
    }
    field_order = ["contract_no", "project_title", "buyer", "seller", "sign_place", "sign_date"]

    def __init__(self, normalizer: TextNormalizer | None = None) -> None:
        self.normalizer = normalizer or TextNormalizer()

    def build_diffs(self, original: Document, compare: Document, start_index: int = 1) -> list[DiffItem]:
        original_fields = self.extract(original)
        compare_fields = self.extract(compare)
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
        return diffs

    def extract(self, document: Document) -> dict[str, CoverField]:
        blocks = self._cover_blocks(document)
        fields: dict[str, CoverField] = {}
        consumed: set[str] = set()
        for index, block in enumerate(blocks):
            lines = self._lines(block.text)
            if not lines:
                continue
            for line_index, line in enumerate(lines):
                parsed = self._parse_labeled_line(line)
                if parsed is None:
                    continue
                key, value = parsed
                evidences = [self._evidence(block, line)]
                if not value and key != "contract_no":
                    value, extra_evidences, extra_ids = self._nearby_value(block, blocks)
                    evidences.extend(extra_evidences)
                    consumed.update(extra_ids)
                if not value and key != "contract_no":
                    value, extra_evidences, extra_ids = self._next_value(lines, line_index, blocks, index)
                    evidences.extend(extra_evidences)
                    consumed.update(extra_ids)
                if value:
                    self._set_field(fields, key, value, evidences)
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
                fields["project_title"] = CoverField(
                    key="project_title",
                    label=self.field_labels["project_title"],
                    value=value,
                    evidences=evidences,
                )
        return fields

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

    def _next_value(
        self,
        lines: list[str],
        line_index: int,
        blocks: list[TextBlock],
        block_index: int,
    ) -> tuple[str, list[EvidenceBox], set[str]]:
        if line_index + 1 < len(lines) and not self._parse_labeled_line(lines[line_index + 1]):
            values = [line for line in lines[line_index + 1 :] if not self._parse_labeled_line(line) and not self._is_noise_line(line)]
            if values:
                return "\n".join(values).strip(), [], set()
        for next_block in blocks[block_index + 1 : block_index + 4]:
            next_lines = self._lines(next_block.text)
            if not next_lines:
                continue
            if self._parse_labeled_line(next_lines[0]) or self._is_noise_line(next_lines[0]):
                continue
            return next_lines[0], [self._evidence(next_block, next_lines[0])], {next_block.block_id}
        return "", [], set()

    def _nearby_value(self, label_block: TextBlock, blocks: list[TextBlock]) -> tuple[str, list[EvidenceBox], set[str]]:
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
            return "", [], set()
        _, block, value = min(candidates, key=lambda item: item[0])
        return value, [self._evidence(block, value)], {block.block_id}

    def _set_field(self, fields: dict[str, CoverField], key: str, value: str, evidences: list[EvidenceBox]) -> None:
        if key in fields:
            return
        fields[key] = CoverField(key=key, label=self.field_labels.get(key, key), value=value, evidences=evidences)

    def _build_diff(self, key: str, left: CoverField | None, right: CoverField | None, index: int) -> DiffItem:
        label = self.field_labels.get(key, key)
        if left and right:
            return DiffItem(
                diff_id=generate_diff_id(index),
                diff_type="MODIFY",
                title=f"封面字段：{label}",
                original_text=left.value,
                compare_text=right.value,
                original_snippet=left.value,
                compare_snippet=right.value,
                readable_change=f"封面字段【{label}】变更：{left.value} -> {right.value}",
                original_evidence=self._mark(left.evidences, "MODIFY"),
                compare_evidence=self._mark(right.evidences, "MODIFY"),
                original_change_ranges=[TextRange(start=0, end=len(left.value), highlight_type="MODIFY")],
                compare_change_ranges=[TextRange(start=0, end=len(right.value), highlight_type="MODIFY")],
            )
        if right:
            return DiffItem(
                diff_id=generate_diff_id(index),
                diff_type="ADD",
                title=f"封面字段：{label}",
                compare_text=right.value,
                compare_snippet=right.value,
                readable_change=f"新增封面字段【{label}】：{right.value}",
                compare_evidence=self._mark(right.evidences, "ADD"),
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
            original_evidence=self._mark(left.evidences, "DELETE"),
            original_change_ranges=[TextRange(start=0, end=len(left.value), highlight_type="DELETE")],
        )

    def _mark(self, evidences: list[EvidenceBox], highlight_type: str) -> list[EvidenceBox]:
        return [evidence.model_copy(update={"highlight_type": highlight_type, "method": "cover_metadata"}) for evidence in evidences]

    def _evidence(self, block: TextBlock, text: str) -> EvidenceBox:
        return EvidenceBox(page_no=block.page_no, bbox=block.bbox, method="cover_metadata", text=text[:300])

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

    def _lines(self, text: str) -> list[str]:
        return [line.strip() for line in self._clean_text(text).splitlines() if line.strip()]

    def _clean_text(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text or "").replace("\r", "\n")
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line).strip()
