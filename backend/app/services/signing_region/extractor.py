from __future__ import annotations

import re
from statistics import median

from app.models import BBox, Document, Page, TextBlock
from app.services.signing_region.models import (
    SigningBlock,
    SigningElement,
    SigningElementType,
    SigningRegion,
    SigningRegionRole,
)
from app.services.table_compare.html_parser import parse_html_tables


SIGNING_ANCHOR_RE = re.compile(r"甲方|乙方|丙方|丁方|盖章|签章|签字|签署|签订日期|签署日期|法定代表人|授权代表|年月日")
BODY_RE = re.compile(r"应当|负责|承担|履行|支付|违约|权利|义务|为准|合同经|生效|协商|约定")
NUMBERED_RE = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百千万0-9]+[章节条款]|[一二三四五六七八九十百千万0-9]+[、.．]|\d+(?:\.\d+){0,4}[、.．]?)"
)
DATE_RE = re.compile(
    r"(?:\d{4}|[_＿]{2,4})\s*年\s*(?:\d{1,2}|[_＿]{1,4})?\s*月\s*(?:\d{1,2}|[_＿]{1,4})?\s*日"
    r"|年\s*(?:\d{1,2}|[_＿]{1,4})?\s*月\s*(?:\d{1,2}|[_＿]{1,4})?\s*日"
    r"|[_＿]{2,4}\s*年"
)
PARTY_FIELD_RE = re.compile(
    r"(?P<role>甲方|乙方|丙方|丁方)\s*[:：]\s*(?P<value>.*?)"
    r"(?=(?:甲方|乙方|丙方|丁方)\s*[:：]|$)"
)
PARTY_RESIDUAL_RE = re.compile(r"^[【】\[\]（）()]*盖章[】\]）)]*$")
PARTY_CONTINUATION_SUFFIX_RE = re.compile(
    r"(?:有限(?:责任)?公司|股份有限公司|集团(?:有限公司)?|分公司|公司|厂|中心|分部|事业部|委员会|"
    r"管理局|研究院|设计院|事务所|合作社)$"
)
PARTY_CONTINUATION_STOP_RE = re.compile(
    r"盖章|签章|签字|签名|代表|日期|时间|地址|电话|邮箱|邮编|联系人|开户|账号|统一社会信用代码"
)
FIELD_RE = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z\s_-]{0,24}|[\u4e00-\u9fff（）()]{1,24})\s*[:：]\s*"
    r"(?P<value>.*?)(?=(?:甲方|乙方|丙方|丁方|单位名称|公司名称|单位地址|法定代表人|法人代表或授权委托人|法人代表|法人|"
    r"授权代表|授权委托人|委托代理人|负责人|签字|签名|签订日期|签署日期|日期|地址|住所|联系人|"
    r"项目负责人|廉洁联系人|电话|手机|联系方式|传真|邮箱|电子邮箱|E-?mail|开户行|开户银行|账号|"
    r"帐户|账户|统一社会信用代码|税号|纳税人识别号|邮编|邮政编码)\s*[:：]|$)",
    re.IGNORECASE,
)
FIELD_ALIASES = (
    ("party", re.compile(r"^(?:甲方|乙方|丙方|丁方)$")),
    ("party_name", re.compile(r"单位名称|公司名称")),
    ("legal_representative", re.compile(r"法定代表人|法人(?:代表(?:或(?:授权委托人)?)?)?|负责人")),
    ("authorized_representative", re.compile(r"授权代表|授权委托人|委托代理人")),
    ("signature", re.compile(r"签字|签名")),
    ("date", re.compile(r"签订日期|签署日期|日期")),
    ("address", re.compile(r"地址|住所")),
    ("contact", re.compile(r"联系人|项目负责人|廉洁联系人")),
    ("phone", re.compile(r"电话|手机|联系方式")),
    ("fax", re.compile(r"传真")),
    ("email", re.compile(r"邮箱|电子邮箱|E-?mail", re.IGNORECASE)),
    ("bank", re.compile(r"开户行|开户银行")),
    ("account", re.compile(r"账号|帐户|账户")),
    ("credit_code", re.compile(r"统一社会信用代码|税号|纳税人识别(?:号)?")),
    ("postal_code", re.compile(r"邮编|邮政编码")),
)
TABLE_SIGNING_FIELD_KEYS = {
    "party_name",
    "legal_representative",
    "authorized_representative",
    "signature",
    "date",
    "address",
    "postal_code",
}
VISUAL_TYPES = {"seal", "stamp", "image", "figure", "table"}
SIGNATURE_FIELD_KEYS = {"authorized_representative", "legal_representative", "signature"}
SIGNATURE_VALUE_RE = re.compile(r"^[\u4e00-\u9fff·]{2,8}$")
SIGNATURE_LABEL_FRAGMENT_RE = re.compile(r"^(?:法定代表人|法人代表|授权代表|授权委托人|委托代理人|负责人|签字|签名)")
TRUNCATED_LEGAL_SIGNATURE_LABEL_RE = re.compile(r"^法定代表人或授(?:权(?:代(?:表)?)?)?$")


class SigningRegionExtractor:
    bottom_ratio = 0.62
    cluster_gap = 90.0
    padding = 20.0

    def extract(self, document: Document) -> list[SigningRegion]:
        regions: list[SigningRegion] = []
        for page in document.pages:
            regions.extend(self._extract_page(page))
        return regions

    def extract_from_blocks(
        self,
        blocks: list[SigningBlock],
        document: Document | None = None,
    ) -> list[SigningRegion]:
        page_blocks = {
            page.page_no: {block.block_id: block for block in page.blocks}
            for page in (document.pages if document is not None else [])
        }
        regions: list[SigningRegion] = []
        for index, block in enumerate(blocks, start=1):
            elements = block.elements or [
                SigningElement(
                    element_id=f"{block.block_id}-summary",
                    element_type=SigningElementType.SIGNING_TABLE,
                    page_no=block.page_no,
                    bbox=block.bbox,
                    text=block.text,
                    confidence=block.confidence,
                    source="inferred",
                    raw_ref={"source_block_ids": block.source_block_ids},
                )
            ]
            blocks_by_id = page_blocks.get(block.page_no, {})
            party_fields = self._party_field_elements(block, blocks_by_id)
            fields = self._field_elements(block, blocks_by_id)
            party_residuals = self._party_residual_elements(block, blocks_by_id)
            self._merge_party_residuals(party_fields, party_residuals)
            elements = [
                *elements,
                *party_fields,
                *fields,
                *party_residuals,
                *self._signature_elements(block, blocks_by_id, [*party_fields, *fields, *party_residuals]),
            ]
            regions.append(
                SigningRegion(
                    region_id=f"SR-{block.page_no}-{index}",
                    signing_block_id=block.block_id,
                    page_no=block.page_no,
                    bbox=block.bbox,
                    region_role=self._role_from_block(block),
                    confidence=block.confidence,
                    confidence_reasons=list(block.confidence_reasons),
                    elements=elements,
                )
            )
        return regions

    def _party_field_elements(
        self,
        signing_block: SigningBlock,
        blocks_by_id: dict[str, TextBlock],
    ) -> list[SigningElement]:
        if not blocks_by_id:
            return []

        source_blocks = [
            blocks_by_id[block_id]
            for block_id in signing_block.source_block_ids
            if block_id in blocks_by_id
            and (blocks_by_id[block_id].text or "").strip()
            and (blocks_by_id[block_id].block_type or "").lower() not in {"seal", "stamp", "image", "figure"}
        ]
        elements: list[SigningElement] = []
        for anchor in sorted(source_blocks, key=lambda item: (item.bbox.y0, item.bbox.x0)):
            matches = list(PARTY_FIELD_RE.finditer(anchor.text or ""))
            for match_index, match in enumerate(matches, start=1):
                role = match.group("role")
                value = (match.group("value") or "").strip()
                if not value:
                    continue
                party_bbox = self._field_bbox(anchor, match.start(), match.end())
                continuation_blocks = self._party_continuation_blocks(
                    anchor,
                    party_bbox,
                    value,
                    source_blocks,
                )
                full_value = "".join([value, *(item.text.strip() for item in continuation_blocks)])
                field_bboxes = [party_bbox, *(item.bbox for item in continuation_blocks)]
                field_segments = [f"{role}：{value}", *(item.text.strip() for item in continuation_blocks)]
                bbox = self._bbox_union(field_bboxes)
                confidence_values = [
                    item.confidence for item in [anchor, *continuation_blocks] if item.confidence is not None
                ]
                elements.append(
                    SigningElement(
                        element_id=f"{signing_block.block_id}-party-{role}-{match_index}",
                        element_type=SigningElementType.PARTY_FIELD,
                        page_no=signing_block.page_no,
                        bbox=bbox,
                        text=f"{role}：{full_value}",
                        confidence=min(confidence_values) if confidence_values else signing_block.confidence,
                        source="inferred",
                        raw_ref={
                            "party_role": role,
                            "source_block_ids": [anchor.block_id, *(item.block_id for item in continuation_blocks)],
                            "field_bboxes": [item.model_dump() for item in field_bboxes],
                            "field_segments": field_segments,
                        },
                    )
                )
        return elements

    @staticmethod
    def _signature_label_fragment_in_visual_block(block: TextBlock) -> bool:
        return (
            (block.block_type or "").lower() in {"seal", "stamp", "image", "figure"}
            and SIGNATURE_LABEL_FRAGMENT_RE.search(SigningRegionExtractor._compact(block.text)) is not None
        )

    @classmethod
    def _merge_party_residuals(
        cls,
        party_fields: list[SigningElement],
        party_residuals: list[SigningElement],
    ) -> None:
        fields_by_role = {
            str(element.raw_ref.get("party_role") or ""): element for element in party_fields
        }
        for residual in party_residuals:
            party = fields_by_role.get(str(residual.raw_ref.get("party_role") or ""))
            suffix = residual.text.strip()
            if party is None or not suffix:
                continue
            field_bboxes = [
                BBox.model_validate(item)
                for item in party.raw_ref.get("field_bboxes", [party.bbox.model_dump()])
            ]
            field_segments = list(party.raw_ref.get("field_segments") or [party.text])
            party.text += suffix
            party.bbox = cls._bbox_union([party.bbox, residual.bbox])
            party.raw_ref["source_block_ids"] = list(
                dict.fromkeys(
                    [
                        *party.raw_ref.get("source_block_ids", []),
                        *residual.raw_ref.get("source_block_ids", []),
                    ]
                )
            )
            party.raw_ref["field_bboxes"] = [
                item.model_dump() for item in [*field_bboxes, residual.bbox]
            ]
            party.raw_ref["field_segments"] = [*field_segments, suffix]

    def _party_residual_elements(
        self,
        signing_block: SigningBlock,
        blocks_by_id: dict[str, TextBlock],
    ) -> list[SigningElement]:
        source_blocks = [
            blocks_by_id[block_id]
            for block_id in signing_block.source_block_ids
            if block_id in blocks_by_id
            and (blocks_by_id[block_id].block_type or "").lower() not in {"seal", "stamp", "image", "figure"}
        ]
        midpoint = self._two_column_midpoint(signing_block, source_blocks)
        elements: list[SigningElement] = []
        for source in source_blocks:
            text = self._compact(source.text)
            if not PARTY_RESIDUAL_RE.fullmatch(text):
                continue
            role = self._field_party_role(
                "",
                source.bbox,
                midpoint,
                signing_block,
                occurrence=1,
                occurrence_count=1,
            )
            elements.append(
                SigningElement(
                    element_id=f"{signing_block.block_id}-party-residual-{source.block_id}",
                    element_type=SigningElementType.LABEL,
                    page_no=signing_block.page_no,
                    bbox=source.bbox,
                    text=text,
                    confidence=source.confidence if source.confidence is not None else signing_block.confidence,
                    source="inferred",
                    raw_ref={
                        "party_role": role,
                        "residual_kind": "party_suffix",
                        "source_block_ids": [source.block_id],
                    },
                )
            )
        return elements

    def _field_elements(
        self,
        signing_block: SigningBlock,
        blocks_by_id: dict[str, TextBlock],
    ) -> list[SigningElement]:
        source_blocks = [
            blocks_by_id[block_id]
            for block_id in signing_block.source_block_ids
            if block_id in blocks_by_id
            and (blocks_by_id[block_id].text or "").strip()
            and (
                (blocks_by_id[block_id].block_type or "").lower() not in {"seal", "stamp", "image", "figure"}
                or self._signature_label_fragment_in_visual_block(blocks_by_id[block_id])
            )
        ]
        source_blocks = self._expand_table_field_blocks(source_blocks)
        if not source_blocks:
            return []

        midpoint = self._two_column_midpoint(signing_block, source_blocks)
        elements: list[SigningElement] = []
        for source in sorted(source_blocks, key=lambda item: (item.bbox.y0, item.bbox.x0)):
            matches = list(FIELD_RE.finditer(source.text or ""))
            candidates = [
                (
                    self._compact(match.group("label")),
                    (match.group("value") or "").strip(),
                    match.start(),
                    match.end(),
                    match.start("value"),
                    match.end("value"),
                )
                for match in matches
            ]
            if not candidates:
                label = self._standalone_field_label(source.text or "")
                if label:
                    candidates.append((label, "", 0, len(source.text or ""), 0, 0))
            for match_index, (label, value, start, end, value_start, value_end) in enumerate(candidates, start=1):
                if not label:
                    continue
                field_prefix = (source.text or "")[start:value_start].strip() if value_start > start else label
                field_key = self._field_key(label)
                if source.source == "signing_table_cell" and field_key not in TABLE_SIGNING_FIELD_KEYS:
                    continue
                if field_key == "party" or (
                    field_key.startswith("custom:") and label.endswith(("甲方", "乙方", "丙方", "丁方"))
                ):
                    continue
                field_bbox = self._field_bbox(source, start, end)
                field_bboxes = [field_bbox]
                continuation_blocks = (
                    self._field_continuation_blocks(source, field_bbox, midpoint, source_blocks)
                    if field_key in {"address", "credit_code"}
                    else []
                )
                if field_key == "credit_code":
                    continuation_blocks = self._credit_code_continuations(value, continuation_blocks)
                value_bbox = (
                    self._field_bbox(source, value_start, value_end) if value and value_end > value_start else None
                )
                value_bboxes = [value_bbox] if value_bbox is not None else []
                field_segments = [f"{field_prefix or label}{value}"]
                if continuation_blocks:
                    value = "".join([value, *(item.text.strip() for item in continuation_blocks)])
                    field_bboxes.extend(item.bbox for item in continuation_blocks)
                    field_segments.extend(item.text.strip() for item in continuation_blocks)
                    field_bbox = self._bbox_union(field_bboxes)
                    value_bboxes.extend(item.bbox for item in continuation_blocks)
                    value_bbox = self._bbox_union(value_bboxes)
                party_role = self._field_party_role(
                    label,
                    field_bbox,
                    midpoint,
                    signing_block,
                    occurrence=match_index,
                    occurrence_count=sum(
                        self._field_key(candidate_label) == field_key for candidate_label, *_ in candidates
                    ),
                )
                elements.append(
                    SigningElement(
                        element_id=f"{signing_block.block_id}-field-{field_key}-{match_index}-{len(elements) + 1}",
                        element_type=SigningElementType.FIELD,
                        page_no=signing_block.page_no,
                        bbox=field_bbox,
                        text=value,
                        confidence=source.confidence if source.confidence is not None else signing_block.confidence,
                        source="inferred",
                        raw_ref={
                            "field_key": field_key,
                            "field_label": label,
                            "field_prefix": field_prefix or label,
                            "party_role": party_role,
                            "source_block_ids": [source.block_id, *(item.block_id for item in continuation_blocks)],
                            **(
                                {"field_bboxes": [bbox.model_dump() for bbox in field_bboxes]}
                                if len(field_bboxes) > 1
                                else {}
                            ),
                            **({"field_segments": field_segments} if len(field_segments) > 1 else {}),
                            **({"value_bbox": value_bbox.model_dump()} if value_bbox is not None else {}),
                            **(
                                {"value_bboxes": [bbox.model_dump() for bbox in value_bboxes]}
                                if len(value_bboxes) > 1
                                else {}
                            ),
                        },
                    )
                )
        return elements

    def _signature_elements(
        self,
        signing_block: SigningBlock,
        blocks_by_id: dict[str, TextBlock],
        parsed_elements: list[SigningElement],
    ) -> list[SigningElement]:
        source_blocks = [
            blocks_by_id[block_id]
            for block_id in signing_block.source_block_ids
            if block_id in blocks_by_id and (blocks_by_id[block_id].text or "").strip()
        ]
        signature_fields = [
            element
            for element in parsed_elements
            if element.element_type == SigningElementType.FIELD
            and str(element.raw_ref.get("field_key") or "") in SIGNATURE_FIELD_KEYS
            and not element.text.strip()
        ]
        if not signature_fields:
            return []

        used_ids = {
            str(block_id) for element in parsed_elements for block_id in element.raw_ref.get("source_block_ids", [])
        }
        midpoint = self._two_column_midpoint(signing_block, source_blocks)
        elements: list[SigningElement] = []
        for candidate in source_blocks:
            text = self._compact(candidate.text)
            if (
                candidate.block_id in used_ids
                or not SIGNATURE_VALUE_RE.fullmatch(text)
                or SIGNATURE_LABEL_FRAGMENT_RE.search(text)
            ):
                continue
            anchor = min(
                signature_fields,
                key=lambda element: max(element.bbox.y0 - candidate.bbox.y1, candidate.bbox.y0 - element.bbox.y1, 0.0),
            )
            vertical_gap = max(anchor.bbox.y0 - candidate.bbox.y1, candidate.bbox.y0 - anchor.bbox.y1, 0.0)
            if vertical_gap > 10.0:
                continue
            role = "甲方" if (candidate.bbox.x0 + candidate.bbox.x1) / 2 < midpoint else "乙方"
            elements.append(
                SigningElement(
                    element_id=f"{signing_block.block_id}-signature-{candidate.block_id}",
                    element_type=SigningElementType.SIGNATURE,
                    page_no=signing_block.page_no,
                    bbox=candidate.bbox,
                    text=text,
                    confidence=candidate.confidence if candidate.confidence is not None else signing_block.confidence,
                    source="ocr",
                    raw_ref={
                        "party_role": role,
                        "field_key": str(anchor.raw_ref.get("field_key") or "signature"),
                        "field_label": str(anchor.raw_ref.get("field_label") or "签字"),
                        "source_block_ids": [candidate.block_id],
                    },
                )
            )
        return elements

    @classmethod
    def _two_column_midpoint(cls, signing_block: SigningBlock, source_blocks: list[TextBlock]) -> float:
        fallback = (signing_block.bbox.x0 + signing_block.bbox.x1) / 2
        if "two_column_layout" not in signing_block.confidence_reasons:
            return fallback
        party_centers: dict[str, list[float]] = {"甲方": [], "乙方": []}
        for block in source_blocks:
            roles = {match.group("role") for match in PARTY_FIELD_RE.finditer(block.text or "")}
            if len(roles) != 1:
                continue
            role = next(iter(roles))
            if role in party_centers:
                party_centers[role].append((block.bbox.x0 + block.bbox.x1) / 2)
        if party_centers["甲方"] and party_centers["乙方"]:
            party_a_center = sum(party_centers["甲方"]) / len(party_centers["甲方"])
            party_b_center = sum(party_centers["乙方"]) / len(party_centers["乙方"])
            if abs(party_a_center - party_b_center) >= 40.0:
                return (party_a_center + party_b_center) / 2
        merged_boundaries: list[float] = []
        for block in source_blocks:
            matches_by_key: dict[str, list[BBox]] = {}
            for match in FIELD_RE.finditer(block.text or ""):
                key = cls._field_key(cls._compact(match.group("label")))
                if key != "party":
                    matches_by_key.setdefault(key, []).append(cls._field_bbox(block, match.start(), match.end()))
            for boxes in matches_by_key.values():
                if len(boxes) != 2:
                    continue
                left, right = sorted(boxes, key=lambda box: (box.x0 + box.x1) / 2)
                if (right.x0 + right.x1 - left.x0 - left.x1) / 2 >= 40.0:
                    merged_boundaries.append((left.x1 + right.x0) / 2)
        if merged_boundaries:
            return median(merged_boundaries)
        field_blocks = [
            block
            for block in source_blocks
            if FIELD_RE.search(block.text or "") or cls._standalone_field_label(block.text or "")
        ]
        starts = sorted({round(block.bbox.x0, 3) for block in field_blocks})
        if len(starts) < 2:
            return fallback
        left_start, right_start = max(zip(starts, starts[1:]), key=lambda pair: pair[1] - pair[0])
        if right_start - left_start < 40.0:
            return fallback
        left_x1 = max(block.bbox.x1 for block in field_blocks if block.bbox.x0 <= left_start)
        right_x0 = min(block.bbox.x0 for block in field_blocks if block.bbox.x0 >= right_start)
        return (left_x1 + right_x0) / 2 if left_x1 < right_x0 else (left_start + right_start) / 2

    @classmethod
    def _expand_table_field_blocks(cls, source_blocks: list[TextBlock]) -> list[TextBlock]:
        expanded: list[TextBlock] = []
        for source in source_blocks:
            if "<table" not in (source.raw_html or "").lower():
                expanded.append(source)
                continue
            cell_bboxes = [BBox(x0=item[0], y0=item[1], x1=item[2], y1=item[3]) for item in source.table_cell_bboxes]
            tables = parse_html_tables(
                source.raw_html,
                page_no=source.page_no,
                source_block_id=source.block_id,
                cell_bboxes=cell_bboxes,
            )
            for table_index, table in enumerate(tables, start=1):
                for row in table.rows:
                    for cell in row.cells:
                        text = cls._normalize_table_field_labels(cell.text)
                        if not text.strip():
                            continue
                        expanded.append(
                            source.model_copy(
                                update={
                                    "block_id": f"{source.block_id}-table-{table_index}-{row.row_index}-{cell.col_index}",
                                    "text": text,
                                    "bbox": cell.bbox or source.bbox,
                                    "source": "signing_table_cell",
                                    "raw_html": "",
                                    "table_cell_bboxes": [],
                                    "char_boxes": [],
                                }
                            )
                        )
        return expanded or source_blocks

    @staticmethod
    def _normalize_table_field_labels(text: str) -> str:
        normalized = text or ""
        for pattern, replacement in (
            (r"单\s*位\s*名\s*称", "单位名称"),
            (r"公\s*司\s*名\s*称", "公司名称"),
            (r"单\s*位\s*地\s*址", "单位地址"),
            (r"邮\s*政\s*编\s*码", "邮政编码"),
        ):
            normalized = re.sub(pattern, replacement, normalized)
        return normalized

    @classmethod
    def _standalone_field_label(cls, text: str) -> str:
        label = cls._compact(text).strip("：:＿_")
        if any(marker in label for marker in ("签字页", "签署页", "无正文")):
            return ""
        lookup_label = label.strip("（）()")
        if TRUNCATED_LEGAL_SIGNATURE_LABEL_RE.fullmatch(lookup_label):
            return "法定代表人或授权代表签字"
        return label if lookup_label and not cls._field_key(lookup_label).startswith("custom:") else ""

    @classmethod
    def _field_continuation_blocks(
        cls,
        source: TextBlock,
        field_bbox: BBox,
        midpoint: float,
        source_blocks: list[TextBlock],
    ) -> list[TextBlock]:
        field_is_left = (field_bbox.x0 + field_bbox.x1) / 2 < midpoint
        continuations: list[TextBlock] = []
        for candidate in sorted(source_blocks, key=lambda item: (item.bbox.y0, item.bbox.x0)):
            overlap_tolerance = 12.0 if source.source == "signing_table_cell" else 2.0
            if candidate.block_id == source.block_id or candidate.bbox.y0 < source.bbox.y1 - overlap_tolerance:
                continue
            if candidate.bbox.y0 - source.bbox.y1 > 48.0:
                break
            if ((candidate.bbox.x0 + candidate.bbox.x1) / 2 < midpoint) != field_is_left:
                continue
            text = (candidate.text or "").strip()
            if (
                not text
                or len(cls._compact(text)) > 80
                or FIELD_RE.search(text)
                or PARTY_FIELD_RE.search(text)
                or cls._standalone_field_label(text)
            ):
                continue
            continuations.append(candidate)
        return continuations

    @classmethod
    def _credit_code_continuations(
        cls,
        value: str,
        candidates: list[TextBlock],
    ) -> list[TextBlock]:
        compact_value = cls._compact(value).upper()
        continuations: list[TextBlock] = []
        for candidate in candidates:
            segment = cls._compact(candidate.text).upper()
            if not re.fullmatch(r"[0-9A-Z]+", segment) or len(compact_value) + len(segment) > 18:
                continue
            continuations.append(candidate)
            compact_value += segment
            if len(compact_value) == 18:
                break
        return continuations

    @staticmethod
    def _field_bbox(source: TextBlock, start: int, end: int) -> BBox:
        char_boxes = [
            char_box
            for char_box in source.char_boxes
            if char_box.text_index is not None and start <= char_box.text_index < end
        ]
        if char_boxes:
            return SigningRegionExtractor._bbox_union([char_box.bbox for char_box in char_boxes])
        text_length = max(1, len(source.text or ""))
        width = max(0.0, source.bbox.x1 - source.bbox.x0)
        return source.bbox.model_copy(
            update={
                "x0": source.bbox.x0 + width * start / text_length,
                "x1": source.bbox.x0 + width * end / text_length,
            }
        )

    @staticmethod
    def _field_key(label: str) -> str:
        compact = re.sub(r"\s+", "", label or "")
        for field_key, pattern in FIELD_ALIASES:
            if pattern.search(compact):
                return field_key
        return f"custom:{compact.casefold()}"

    @staticmethod
    def _field_party_role(
        label: str,
        bbox: BBox,
        midpoint: float,
        signing_block: SigningBlock,
        *,
        occurrence: int,
        occurrence_count: int,
    ) -> str:
        for role in ("甲方", "乙方", "丙方", "丁方"):
            if role in label:
                return role
        center_x = (bbox.x0 + bbox.x1) / 2
        if "two_column_layout" in signing_block.confidence_reasons:
            return "甲方" if center_x < midpoint else "乙方"
        if signing_block.block_role.value == "party_a":
            return "甲方"
        if signing_block.block_role.value == "party_b":
            return "乙方"
        if occurrence_count > 1:
            return "甲方" if occurrence % 2 else "乙方"
        if signing_block.block_role.value == "both_parties":
            return "甲方" if center_x < midpoint else "乙方"
        return "unknown"

    def _party_continuation_blocks(
        self,
        anchor: TextBlock,
        anchor_bbox: BBox,
        anchor_value: str,
        source_blocks: list[TextBlock],
    ) -> list[TextBlock]:
        # Party fields must use OCR text geometry. Layout regions may merge both
        # columns into the same bbox, which destroys left/right association.
        anchor_center_x = (anchor_bbox.x0 + anchor_bbox.x1) / 2
        signing_midpoint = (
            min(item.bbox.x0 for item in source_blocks) + max(item.bbox.x1 for item in source_blocks)
        ) / 2
        continuation: list[TextBlock] = []
        for candidate in sorted(source_blocks, key=lambda item: (item.bbox.y0, item.bbox.x0)):
            if candidate.block_id == anchor.block_id:
                continue
            bbox = candidate.bbox
            if bbox.y0 < anchor_bbox.y1 - 2.0:
                continue
            if bbox.y0 - anchor_bbox.y1 > 64.0:
                break
            candidate_center_x = (bbox.x0 + bbox.x1) / 2
            same_column = (anchor_center_x < signing_midpoint) == (candidate_center_x < signing_midpoint)
            if not same_column:
                continue
            text = (candidate.text or "").strip()
            compact = self._compact(text)
            if not compact or PARTY_FIELD_RE.search(text) or PARTY_CONTINUATION_STOP_RE.search(compact):
                continue
            if not self._looks_like_party_continuation(anchor_value, compact):
                continue
            continuation.append(candidate)
            anchor_value += compact
            anchor_bbox = self._bbox_union([anchor_bbox, bbox])
        return continuation

    @staticmethod
    def _looks_like_party_continuation(anchor_value: str, candidate: str) -> bool:
        compact_anchor = re.sub(r"\s+", "", anchor_value or "")
        if not candidate or len(candidate) > 32 or any(char.isdigit() for char in candidate):
            return False
        if compact_anchor.endswith(candidate):
            return False
        return (
            PARTY_CONTINUATION_SUFFIX_RE.search(candidate) is not None
            or PARTY_CONTINUATION_SUFFIX_RE.search(f"{compact_anchor}{candidate}") is not None
        )

    @staticmethod
    def _bbox_union(bboxes: list[BBox]) -> BBox:
        return BBox(
            x0=min(bbox.x0 for bbox in bboxes),
            y0=min(bbox.y0 for bbox in bboxes),
            x1=max(bbox.x1 for bbox in bboxes),
            y1=max(bbox.y1 for bbox in bboxes),
        )

    def _extract_page(self, page: Page) -> list[SigningRegion]:
        candidates = [block for block in page.blocks if self._is_candidate(block, page)]
        if not candidates:
            return []

        regions: list[SigningRegion] = []
        for index, blocks in enumerate(self._cluster(candidates), start=1):
            elements = [self._to_element(block, index) for block in blocks]
            confidence, reasons = self._confidence(blocks, page)
            if confidence < 0.5:
                continue
            bbox = self._padded_union([element.bbox for element in elements], page)
            regions.append(
                SigningRegion(
                    region_id=f"SR-{page.page_no}-{index}",
                    page_no=page.page_no,
                    bbox=bbox,
                    region_role=self._role(blocks, page),
                    confidence=confidence,
                    confidence_reasons=reasons,
                    elements=elements,
                )
            )
        return regions

    def _is_candidate(self, block: TextBlock, page: Page) -> bool:
        text = self._compact(block.text)
        block_type = (block.block_type or "").lower()
        bbox = self._effective_bbox(block)
        in_bottom = self._is_in_bottom_region(bbox, page)
        has_visual = block_type in VISUAL_TYPES
        has_anchor = bool(SIGNING_ANCHOR_RE.search(text))
        has_date = bool(DATE_RE.search(text))
        form_like = self._form_like(block.text or "")
        label_like = has_anchor and ("：" in text or ":" in text or "（" in text or "(" in text)
        signing_context = "以下无正文" in text or "签署页" in text or "签字页" in text

        if NUMBERED_RE.match(text) or BODY_RE.search(text):
            return has_visual and form_like
        if has_visual and in_bottom:
            return True
        if not in_bottom and not signing_context:
            return False
        if signing_context:
            return True
        if len(text) > 120 and not has_visual:
            return False
        if not has_anchor and not form_like and not has_date:
            return False
        return form_like or has_date or label_like

    def _cluster(self, blocks: list[TextBlock]) -> list[list[TextBlock]]:
        ordered = sorted(
            blocks,
            key=lambda block: (block.page_no, self._effective_bbox(block).y0, self._effective_bbox(block).x0),
        )
        clusters: list[list[TextBlock]] = []
        for block in ordered:
            if not clusters:
                clusters.append([block])
                continue
            previous = clusters[-1][-1]
            if self._effective_bbox(block).y0 - self._effective_bbox(previous).y1 <= self.cluster_gap:
                clusters[-1].append(block)
            else:
                clusters.append([block])
        return clusters

    def _to_element(self, block: TextBlock, region_index: int) -> SigningElement:
        block_type = (block.block_type or "").lower()
        return SigningElement(
            element_id=f"{block.block_id}-signing-{region_index}",
            element_type=self._element_type(block),
            page_no=block.page_no,
            bbox=self._effective_bbox(block),
            text=block.text,
            confidence=block.confidence if block.confidence is not None else 0.8,
            source="layout" if block_type in VISUAL_TYPES else "ocr",
            raw_ref={"block_id": block.block_id, "block_type": block.block_type},
        )

    def _element_type(self, block: TextBlock) -> SigningElementType:
        block_type = (block.block_type or "").lower()
        text = self._compact(block.text)
        if block_type in {"seal", "stamp"}:
            return SigningElementType.SEAL
        if block_type in {"image", "figure"}:
            return SigningElementType.SIGNATURE
        if block_type == "table":
            return SigningElementType.SIGNING_TABLE
        if DATE_RE.search(text):
            return SigningElementType.DATE_FIELD
        if SIGNING_ANCHOR_RE.search(text):
            return SigningElementType.LABEL
        return SigningElementType.VISUAL_AREA

    def _confidence(self, blocks: list[TextBlock], page: Page) -> tuple[float, list[str]]:
        del page
        reasons: list[str] = []
        texts = "".join(block.text or "" for block in blocks)
        block_types = {(block.block_type or "").lower() for block in blocks}
        score = 0.0
        if block_types & {"seal", "stamp"}:
            score += 0.4
            reasons.append("seal_block")
        if block_types & {"image", "figure", "table"}:
            score += 0.25
            reasons.append("visual_or_table_block")
        if SIGNING_ANCHOR_RE.search(texts):
            score += 0.2
            reasons.append("signing_label")
        if DATE_RE.search(texts):
            score += 0.15
            reasons.append("date_field")
        if self._form_like(texts):
            score += 0.2
            reasons.append("form_like")
        if "以下无正文" in texts or "签署页" in texts or "签字页" in texts:
            score += 0.2
            reasons.append("signing_page_context")
        return min(score, 1.0), reasons

    def _role(self, blocks: list[TextBlock], page: Page) -> SigningRegionRole:
        text = self._compact("".join(block.text or "" for block in blocks))
        if "甲方" in text and "乙方" in text:
            return SigningRegionRole.BOTH_PARTIES
        if "甲方" in text:
            return SigningRegionRole.PARTY_A
        if "乙方" in text:
            return SigningRegionRole.PARTY_B
        x0 = min(self._effective_bbox(block).x0 for block in blocks)
        x1 = max(self._effective_bbox(block).x1 for block in blocks)
        if page.width > 0 and x0 < page.width * 0.25 and x1 > page.width * 0.75:
            return SigningRegionRole.BOTH_PARTIES
        return SigningRegionRole.UNKNOWN

    @staticmethod
    def _role_from_block(block: SigningBlock) -> SigningRegionRole:
        if block.block_role.value == "party_a":
            return SigningRegionRole.PARTY_A
        if block.block_role.value == "party_b":
            return SigningRegionRole.PARTY_B
        if block.block_role.value == "both_parties":
            return SigningRegionRole.BOTH_PARTIES
        return SigningRegionRole.UNKNOWN

    def _is_in_bottom_region(self, bbox: BBox, page: Page) -> bool:
        if page.height <= 0:
            return False
        threshold = page.height * self.bottom_ratio
        center_y = (bbox.y0 + bbox.y1) / 2
        return bbox.y0 >= threshold or center_y >= threshold or bbox.y1 >= threshold

    @staticmethod
    def _effective_bbox(block: TextBlock) -> BBox:
        return block.layout_bbox or block.bbox

    def _padded_union(self, bboxes: list[BBox], page: Page) -> BBox:
        x0 = min(min(bbox.x0, bbox.x1) for bbox in bboxes) - self.padding
        y0 = min(min(bbox.y0, bbox.y1) for bbox in bboxes) - self.padding
        x1 = max(max(bbox.x0, bbox.x1) for bbox in bboxes) + self.padding
        y1 = max(max(bbox.y0, bbox.y1) for bbox in bboxes) + self.padding
        x0, x1 = self._clamp_ordered_axis(x0, x1, page.width)
        y0, y1 = self._clamp_ordered_axis(y0, y1, page.height)
        return BBox(
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
        )

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    @staticmethod
    def _clamp_ordered_axis(start: float, end: float, limit: float) -> tuple[float, float]:
        if limit <= 0:
            return 0.0, 0.0
        clamped_start = min(max(start, 0.0), limit)
        clamped_end = min(max(end, 0.0), limit)
        return min(clamped_start, clamped_end), max(clamped_start, clamped_end)

    @staticmethod
    def _form_like(text: str) -> bool:
        return (text or "").count("：") + (text or "").count(":") >= 2 or bool(re.search(r"[_＿—-]{2,}", text or ""))
