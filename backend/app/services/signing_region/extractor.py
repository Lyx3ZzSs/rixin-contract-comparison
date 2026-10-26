from __future__ import annotations

import html
import re
import unicodedata
from datetime import date
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
    r"|\d{4}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2}"
)
DATE_COMPONENT_RE = re.compile(
    r"(?P<year>\d{4}|[_＿]{2,4})\s*年\s*"
    r"(?P<month>\d{1,2}|[_＿]{1,4})?\s*月\s*"
    r"(?P<day>\d{1,2}|[_＿]{1,4})?\s*日"
)
OCR_TOLERANT_DATE_COMPONENT_RE = re.compile(
    r"(?P<year>\d{4}|[_＿]{2,4})\s*年\s*"
    r"(?P<month>\d{1,2}|[_＿]{1,4})?\s*月\s*"
    r"(?P<day>\d{1,2}|[_＿]{1,4})?\s*[08oO](?!\d)"
)
SEPARATED_DATE_COMPONENT_RE = re.compile(
    r"(?P<year>\d{4})\s*[./-]\s*"
    r"(?P<month>\d{1,2})\s*[./-]\s*"
    r"(?P<day>\d{1,2})"
)
PARTY_FIELD_RE = re.compile(
    r"(?P<role>甲方|乙方|丙方|丁方|委托人|承包人|买方|卖方|设计方)\s*[:：]\s*(?P<value>.*?)"
    r"(?=(?:甲方|乙方|丙方|丁方|委托人|承包人|买方|卖方|设计方)\s*[:：]|$)"
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
    r"(?P<value>.*?)(?=(?:计划开工日期|计划竣工日期|实际开工日期|实际竣工日期|本项目现场负责人姓名|承包人|建设单位意见|甲方|乙方|丙方|丁方|单位名称|公司名称|单位地址|法定代表人|法人代表或授权委托人|法人代表|法人|"
    r"授权代表|授权委托人|委托代理人|负责人|签字|签名|盖章|签订日期|签署日期|日期|地址|住所|联系人|"
    r"项目负责人|廉洁联系人|电话|手机|联系方式|传真|邮箱|电子邮箱|E-?mail|开户行|开户银行|账号|"
    r"帐户|账户|银行行号|联行号|统一社会信用代码|税号|纳税人识别号|邮编|邮政编码)\s*[:：]|$)",
    re.IGNORECASE,
)
FIELD_ALIASES = (
    ("party", re.compile(r"^(?:甲方|乙方|丙方|丁方|委托人|承包人|买方|卖方|设计方)$")),
    ("party_name", re.compile(r"单位名称|公司名称")),
    ("legal_representative", re.compile(r"法定代表人|法人(?:代表(?:或(?:授权委托人)?)?)?|负责人")),
    ("authorized_representative", re.compile(r"授权代表|授权委托人|委托代理人")),
    ("date", re.compile(r"签订日期|签署日期|签字日期|签订时间|签署时间|签字时间|^(?:日期|时间)$")),
    ("signature", re.compile(r"签字|签名")),
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
SIGNATURE_COMPANY_FRAGMENT_RE = re.compile(r"(?:公司|有限|责任|股份|集团|科技)$")
TRUNCATED_LEGAL_SIGNATURE_LABEL_RE = re.compile(r"^法定代表人或授(?:权(?:代(?:表)?)?)?$")
TABLE_MARKUP_RE = re.compile(r"</?(?:html|body|table|thead|tbody|tr|th|td)\b", re.IGNORECASE)
EMAIL_VALUE_RE = re.compile(
    r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)*"
    r"\.[A-Z]{2,63}",
    re.IGNORECASE,
)


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
        for index, source_block in enumerate(blocks, start=1):
            block = source_block.model_copy(deep=True)
            self._infer_party_layout(block, page_blocks.get(block.page_no, {}))
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
                    group_id=getattr(block, "group_id", ""),
                    page_no=block.page_no,
                    bbox=block.bbox,
                    region_role=self._role_from_block(block),
                    confidence=block.confidence,
                    confidence_reasons=list(block.confidence_reasons),
                    elements=elements,
                )
            )
        return regions

    def _infer_party_layout(
        self,
        signing_block: SigningBlock,
        blocks_by_id: dict[str, TextBlock],
    ) -> None:
        source_blocks = [
            blocks_by_id[block_id]
            for block_id in signing_block.source_block_ids
            if block_id in blocks_by_id and (blocks_by_id[block_id].text or "").strip()
        ]
        field_blocks = self._expand_table_field_blocks(source_blocks)
        if self._has_stacked_party_layout(field_blocks) and not any(
            a.block_id != b.block_id and abs(a.bbox.y0-b.bbox.y0) < 12 and abs(a.bbox.x0-b.bbox.x0) > 100
            and FIELD_RE.search(a.text) and FIELD_RE.search(b.text)
            for a in field_blocks for b in field_blocks
        ):
            signing_block.confidence_reasons = [r for r in signing_block.confidence_reasons if r != "two_column_layout"]
            if "stacked_party_layout" not in signing_block.confidence_reasons:
                signing_block.confidence_reasons.append("stacked_party_layout")
        elif self._has_two_column_field_layout(field_blocks):
            if "two_column_layout" not in signing_block.confidence_reasons:
                signing_block.confidence_reasons.append("two_column_layout")
        elif self._has_stacked_party_layout(field_blocks):
            if "stacked_party_layout" not in signing_block.confidence_reasons:
                signing_block.confidence_reasons.append("stacked_party_layout")

    @classmethod
    def _has_two_column_field_layout(cls, source_blocks: list[TextBlock]) -> bool:
        field_blocks = [
            block
            for block in source_blocks
            if FIELD_RE.search(block.text or "") or cls._standalone_field_label(block.text or "")
        ]
        starts = sorted({round(block.bbox.x0, 1) for block in field_blocks})
        if len(starts) < 2:
            return False
        left, right = max(zip(starts, starts[1:]), key=lambda pair: pair[1] - pair[0])
        return bool(
            right - left >= 80.0
            and sum(block.bbox.x0 <= left + 8.0 for block in field_blocks) >= 2
            and sum(block.bbox.x0 >= right - 8.0 for block in field_blocks) >= 2
        )

    @classmethod
    def _has_stacked_party_layout(cls, source_blocks: list[TextBlock]) -> bool:
        by_key: dict[str, list[float]] = {}
        for block in source_blocks:
            labels = [cls._compact(match.group("label")) for match in FIELD_RE.finditer(block.text or "")]
            standalone = cls._standalone_field_label(block.text or "")
            if standalone:
                labels.append(standalone)
            for label in labels:
                key = cls._field_key(label)
                if key in {"legal_representative", "authorized_representative", "signature", "date"}:
                    by_key.setdefault(key, []).append((block.bbox.y0 + block.bbox.y1) / 2)
        return any(len(values) >= 2 and max(values) - min(values) >= 80.0 for values in by_key.values())

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
            and (
                (blocks_by_id[block_id].block_type or "").lower() not in {"seal", "stamp", "image", "figure"}
                or PARTY_FIELD_RE.match(blocks_by_id[block_id].text or "") is not None
            )
        ]
        elements: list[SigningElement] = []
        for anchor in sorted(source_blocks, key=lambda item: (item.bbox.y0, item.bbox.x0)):
            matches = list(PARTY_FIELD_RE.finditer(anchor.text or ""))
            for match_index, match in enumerate(matches, start=1):
                role = match.group("role")
                role = {"委托人":"甲方", "承包人":"乙方"}.get(role,role)
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
                            **({"field_key": "party_name", "source_block_type": anchor.block_type,
                                "value_roi": BBox(
                                    x0=self._field_bbox(anchor, match.start("value"), match.end("value")).x0,
                                    y0=party_bbox.y0, x1=min(signing_block.bbox.x1, party_bbox.x1 + 12),
                                    y1=min(signing_block.bbox.y1, party_bbox.y1 + 44),
                                ).model_dump()}
                               if anchor.block_type in {"seal", "stamp"} else {}),
                            "source_block_ids": [anchor.block_id, *(item.block_id for item in continuation_blocks)],
                            "field_bboxes": [item.model_dump() for item in field_bboxes],
                            "field_segments": field_segments,
                        },
                    )
                )
        return elements

    @classmethod
    def _field_label_fragment_in_visual_block(cls, block: TextBlock) -> bool:
        if (block.block_type or "").lower() not in {
            "seal",
            "stamp",
            "image",
            "figure",
        }:
            return False
        text = block.text or ""
        for match in FIELD_RE.finditer(text):
            if not cls._field_key(cls._compact(match.group("label"))).startswith("custom:"):
                return True
        label = cls._standalone_field_label(text)
        return bool(label and not cls._field_key(label).startswith("custom:"))

    @classmethod
    def _merge_party_residuals(
        cls,
        party_fields: list[SigningElement],
        party_residuals: list[SigningElement],
    ) -> None:
        fields_by_role = {str(element.raw_ref.get("party_role") or ""): element for element in party_fields}
        for residual in party_residuals:
            party = fields_by_role.get(str(residual.raw_ref.get("party_role") or ""))
            suffix = residual.text.strip()
            if party is None or not suffix:
                continue
            field_bboxes = [
                BBox.model_validate(item) for item in party.raw_ref.get("field_bboxes", [party.bbox.model_dump()])
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
            party.raw_ref["field_bboxes"] = [item.model_dump() for item in [*field_bboxes, residual.bbox]]
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
                or self._field_label_fragment_in_visual_block(blocks_by_id[block_id])
            )
        ]
        source_blocks = self._expand_table_field_blocks(source_blocks)
        if not source_blocks:
            return []

        midpoint = self._two_column_midpoint(signing_block, source_blocks)
        stacked_midpoint = (signing_block.bbox.y0 + signing_block.bbox.y1) / 2
        elements: list[SigningElement] = []
        used_source_ids: set[str] = set()
        for source in sorted(source_blocks, key=lambda item: (item.bbox.y0, item.bbox.x0)):
            matches = list(FIELD_RE.finditer(source.text or ""))
            candidates = [
                (
                    self._compact(match.group("label")),
                    self._clean_field_value(match.group("value") or ""),
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
                if field_key.startswith("custom:") and re.search(r"甲方|乙方|丙方|丁方|盖章|签章", label):
                    continue
                if field_key == "party" or (
                    field_key.startswith("custom:") and label.endswith(("甲方", "乙方", "丙方", "丁方"))
                ):
                    continue
                field_bbox = self._field_bbox(source, start, end)
                label_bbox = self._field_bbox(source, start, value_start) if value_start > start else field_bbox
                field_bboxes = [field_bbox]
                continuation_blocks = (
                    self._field_continuation_blocks(source, field_bbox, midpoint, source_blocks,
                                                    use_columns=self._uses_party_columns(signing_block))
                    if field_key in {"address", "credit_code", "bank"}
                    else []
                )
                if field_key == "credit_code":
                    continuation_blocks = self._credit_code_continuations(value, continuation_blocks)
                if field_key == "bank":
                    continuation_blocks = [item for item in continuation_blocks
                                           if value.endswith("支") and item.text.strip() == "行"][:1]
                unresolved_address_lines = []
                if field_key == "address":
                    from app.services.address_evidence import address_tail_candidates
                    accepted_ids = {b.block_id for b in continuation_blocks}
                    unresolved_address_lines = [b for b in address_tail_candidates(
                        source, field_bbox, list(blocks_by_id.values())) if b.block_id not in accepted_ids]
                value_bbox = (
                    self._field_bbox(source, value_start, value_end) if value and value_end > value_start else None
                )
                value_bboxes = [value_bbox] if value_bbox is not None else []
                field_segments = [f"{field_prefix or label}{value}"]
                if continuation_blocks:
                    continuation_bboxes = [
                        self._field_bbox(item, 0, len(item.text or "")) for item in continuation_blocks
                    ]
                    value = "".join([value, *(item.text.strip() for item in continuation_blocks)])
                    field_bboxes.extend(continuation_bboxes)
                    field_segments.extend(item.text.strip() for item in continuation_blocks)
                    field_bbox = self._bbox_union(field_bboxes)
                    value_bboxes.extend(continuation_bboxes)
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
                    stacked_midpoint=stacked_midpoint,
                )
                explicit_role = self._business_role_for_field(label_bbox, source_blocks)
                if explicit_role:
                    party_role = explicit_role
                adjacent_value_source = None
                if field_key == "date" and not value:
                    adjacent_value_source = self._adjacent_date_value_block(
                        source,
                        field_bbox=field_bbox,
                        party_role=party_role,
                        midpoint=midpoint,
                        stacked_midpoint=stacked_midpoint,
                        signing_block=signing_block,
                        source_blocks=source_blocks,
                    )
                    if adjacent_value_source is not None:
                        value = (adjacent_value_source.text or "").strip()
                        value_bbox = adjacent_value_source.bbox
                        value_bboxes = [value_bbox]
                value_roi = self._field_value_roi(
                    signing_block,
                    party_role=party_role,
                    midpoint=midpoint,
                    source_bbox=source.bbox,
                    field_bbox=field_bbox,
                    value_roi_x0=label_bbox.x1,
                )
                (
                    preferred_value,
                    value_evidence,
                    content_recognition_status,
                    content_recognition_reason,
                    display_value_bbox,
                    rejected_value_evidence,
                ) = self._fuse_field_value_evidence(
                    source,
                    label=label,
                    field_key=field_key,
                    primary_value=value,
                    primary_value_bbox=value_bbox,
                    field_bbox=field_bbox,
                    value_roi=value_roi,
                    party_role=party_role,
                    midpoint=midpoint,
                    signing_block=signing_block,
                    primary_value_source=adjacent_value_source,
                )
                supporting_unlocalized_evidence = [
                    item
                    for item in rejected_value_evidence
                    if item.get("rejection_reason") == "UNLOCALIZED_BLOCK_VALUE"
                ]
                rejected_contaminated_evidence = [
                    item
                    for item in rejected_value_evidence
                    if item.get("rejection_reason") != "UNLOCALIZED_BLOCK_VALUE"
                ]
                if preferred_value:
                    value = preferred_value
                address_disputed = field_key == "address" and any(
                    "address_line_readings_disagree" in b.semantic_reasons
                    for b in [source, *continuation_blocks])
                if address_disputed:
                    content_recognition_status = "NEEDS_REVIEW"
                    content_recognition_reason = "ADDRESS_LINE_READINGS_DISAGREE"
                elif unresolved_address_lines:
                    content_recognition_status = "NEEDS_REVIEW"
                    content_recognition_reason = "INCOMPLETE_ADDRESS_LINES"
                used_source_ids.update(
                    [
                        source.block_id,
                        *(item.block_id for item in continuation_blocks),
                        *([adjacent_value_source.block_id] if adjacent_value_source is not None else []),
                    ]
                )
                value_source = adjacent_value_source or source
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
                            **({"address_recognition_disputed": True} if address_disputed else {}),
                            **({"address_readings": [dict(e.model_dump(mode="json"), text=e.raw_text)
                                for b in [source, *continuation_blocks] for e in b.source_evidence
                                if e.source == "address_line_roi_ppocr_disputed"]} if address_disputed else {}),
                            **({"unresolved_address_lines": [
                                {"block_id": b.block_id, "text": b.text, "bbox": b.bbox.model_dump()}
                                for b in unresolved_address_lines]} if unresolved_address_lines else {}),
                            "column_midpoint": midpoint if self._uses_party_columns(signing_block) else None,
                            "field_label": label,
                            "field_prefix": field_prefix or label,
                            "party_role": party_role,
                            "source_bbox": source.bbox.model_dump(),
                            "source_block_ids": [
                                source.block_id,
                                *(item.block_id for item in continuation_blocks),
                                *([adjacent_value_source.block_id] if adjacent_value_source is not None else []),
                            ],
                            **(
                                {"field_bboxes": [bbox.model_dump() for bbox in field_bboxes]}
                                if len(field_bboxes) > 1
                                else {}
                            ),
                            **({"field_segments": field_segments} if len(field_segments) > 1 else {}),
                            **({"value_bbox": value_bbox.model_dump()} if value_bbox is not None else {}),
                            "value_roi": value_roi.model_dump(),
                            **(
                                {"display_value_bbox": display_value_bbox.model_dump()}
                                if display_value_bbox is not None
                                else {}
                            ),
                            "value_evidence": value_evidence,
                            "rejected_value_evidence": rejected_value_evidence,
                            "supporting_unlocalized_evidence": supporting_unlocalized_evidence,
                            "rejected_contaminated_evidence": rejected_contaminated_evidence,
                            "content_recognition_status": content_recognition_status,
                            "content_recognition_reason": content_recognition_reason,
                            **(
                                {"value_bboxes": [bbox.model_dump() for bbox in value_bboxes]}
                                if len(value_bboxes) > 1
                                else {}
                            ),
                            **(self._date_component_ref(value_source) if field_key == "date" else {}),
                        },
                    )
                )
        elements.extend(
            self._standalone_date_elements(
                signing_block,
                source_blocks,
                used_source_ids=used_source_ids,
                midpoint=midpoint,
                stacked_midpoint=stacked_midpoint,
                start_index=len(elements) + 1,
            )
        )
        return elements

    def _standalone_date_elements(
        self,
        signing_block: SigningBlock,
        source_blocks: list[TextBlock],
        *,
        used_source_ids: set[str],
        midpoint: float,
        stacked_midpoint: float,
        start_index: int,
    ) -> list[SigningElement]:
        elements: list[SigningElement] = []
        for source in source_blocks:
            text = (source.text or "").strip()
            leading_date = DATE_RE.match(text)
            mixed_date_and_next_label = bool(leading_date and re.fullmatch(
                r"(?:日期|签署日期|签订日期)\s*[:：]", text[leading_date.end():].strip()))
            if mixed_date_and_next_label:
                # One OCR line may join left-party handwriting to the right
                # party's still-blank date label. Each keeps its own geometry.
                date_bbox = self._field_bbox(source, leading_date.start(), leading_date.end())
                text = leading_date.group()
                source = source.model_copy(update={"text": text, "bbox": date_bbox})
            elif source.block_id in used_source_ids or DATE_RE.fullmatch(text) is None:
                continue
            role = self._field_party_role(
                "日期",
                source.bbox,
                midpoint,
                signing_block,
                occurrence=1,
                occurrence_count=1,
                stacked_midpoint=stacked_midpoint,
            )
            role = self._business_role_for_field(source.bbox, source_blocks) or role
            value = text if self._date_parts(text) is not None else ""
            elements.append(
                SigningElement(
                    element_id=f"{signing_block.block_id}-field-date-standalone-{start_index + len(elements)}",
                    element_type=SigningElementType.FIELD,
                    page_no=signing_block.page_no,
                    bbox=source.bbox,
                    text=value,
                    confidence=source.confidence if source.confidence is not None else signing_block.confidence,
                    source="inferred",
                    raw_ref={
                        "field_key": "date",
                        "field_label": "签署日期",
                        "field_prefix": "签署日期：",
                        "party_role": role,
                        "source_bbox": source.bbox.model_dump(),
                        "source_block_ids": [source.block_id],
                        **({"value_bbox": source.bbox.model_dump()} if value else {}),
                        **self._date_component_ref(source),
                    },
                )
            )
        return elements

    @classmethod
    def _adjacent_date_value_block(
        cls,
        source: TextBlock,
        *,
        field_bbox: BBox,
        party_role: str,
        midpoint: float,
        stacked_midpoint: float,
        signing_block: SigningBlock,
        source_blocks: list[TextBlock],
    ) -> TextBlock | None:
        label_height = max(1.0, field_bbox.y1 - field_bbox.y0)
        candidates: list[tuple[float, TextBlock]] = []
        for candidate in source_blocks:
            text = (candidate.text or "").strip()
            if candidate.block_id == source.block_id or DATE_RE.fullmatch(text) is None:
                continue
            candidate_role = cls._field_party_role(
                "日期",
                candidate.bbox,
                midpoint,
                signing_block,
                occurrence=1,
                occurrence_count=1,
                stacked_midpoint=stacked_midpoint,
            )
            if candidate_role != party_role:
                continue
            overlap = min(field_bbox.y1, candidate.bbox.y1) - max(field_bbox.y0, candidate.bbox.y0)
            candidate_height = max(1.0, candidate.bbox.y1 - candidate.bbox.y0)
            if overlap / min(label_height, candidate_height) < 0.35:
                continue
            horizontal_gap = candidate.bbox.x0 - field_bbox.x1
            if horizontal_gap < -2.0 or horizontal_gap > max(45.0, label_height * 4.0):
                continue
            center_gap = abs(
                (candidate.bbox.y0 + candidate.bbox.y1) / 2 - (field_bbox.y0 + field_bbox.y1) / 2
            )
            candidates.append((horizontal_gap + center_gap, candidate))
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

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
                or candidate.block_type in {"seal", "stamp"}
                or text in {"合同专用章", "财务专用章", "发票专用章", "公章"}
                or (candidate.confidence is not None and candidate.confidence < 0.8)
                or not SIGNATURE_VALUE_RE.fullmatch(text)
                or SIGNATURE_LABEL_FRAGMENT_RE.search(text)
                or SIGNATURE_COMPANY_FRAGMENT_RE.search(text)
            ):
                continue
            anchor = min(
                signature_fields,
                key=lambda element: max(element.bbox.y0 - candidate.bbox.y1, candidate.bbox.y0 - element.bbox.y1, 0.0),
            )
            vertical_gap = max(anchor.bbox.y0 - candidate.bbox.y1, candidate.bbox.y0 - anchor.bbox.y1, 0.0)
            if vertical_gap > 10.0:
                continue
            role = str(anchor.raw_ref.get("party_role") or "unknown")
            if self._uses_party_columns(signing_block):
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
        width = signing_block.bbox.x1 - signing_block.bbox.x0
        midpoint_min = signing_block.bbox.x0 + width * 0.30
        midpoint_max = signing_block.bbox.x0 + width * 0.70
        table_cells = [block for block in source_blocks if block.source == "signing_table_cell"]
        table_starts = sorted({round(block.bbox.x0, 3) for block in table_cells})
        if len(table_starts) >= 2:
            left_start, right_start = max(
                zip(table_starts, table_starts[1:]),
                key=lambda pair: pair[1] - pair[0],
            )
            if right_start - left_start >= 40.0:
                left_x1 = max(block.bbox.x1 for block in table_cells if block.bbox.x0 <= left_start)
                right_x0 = min(block.bbox.x0 for block in table_cells if block.bbox.x0 >= right_start)
                candidate = (left_x1 + right_x0) / 2 if left_x1 <= right_x0 + 4.0 else (left_start + right_start) / 2
                if midpoint_min <= candidate <= midpoint_max:
                    return candidate
        bare_party_starts: dict[str, list[float]] = {"甲方": [], "乙方": []}
        for block in source_blocks:
            match = re.fullmatch(r"(甲方|乙方)\s*[:：]?\s*(?:[（(]盖章[）)])?", (block.text or "").strip())
            if match:
                bare_party_starts[match.group(1)].append(block.bbox.x0)
        if bare_party_starts["甲方"] and bare_party_starts["乙方"]:
            left, right = min(bare_party_starts["甲方"]), min(bare_party_starts["乙方"])
            if right - left >= 80:
                # Empty headings locate column starts, not column centers.
                # Ink following a short left label can extend close to the
                # next column's start and must remain owned by the left party.
                return right - max(8.0, min(18.0, (right-left) * 0.05))
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
        candidate = (left_x1 + right_x0) / 2 if left_x1 <= right_x0 + 4.0 else (left_start + right_start) / 2
        return candidate if midpoint_min <= candidate <= midpoint_max else fallback

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
                        cell_bbox = cell.bbox or source.bbox
                        char_boxes = [
                            char_box
                            for char_box in source.char_boxes
                            if cell_bbox.x0 <= (char_box.bbox.x0 + char_box.bbox.x1) / 2 <= cell_bbox.x1
                            and cell_bbox.y0 <= (char_box.bbox.y0 + char_box.bbox.y1) / 2 <= cell_bbox.y1
                        ]
                        if "visible_ruled_grid" in source.source and char_boxes:
                            lines = []
                            for char in sorted(char_boxes, key=lambda c: ((c.bbox.y0+c.bbox.y1)/2, c.bbox.x0)):
                                cy = (char.bbox.y0+char.bbox.y1)/2
                                if not lines or abs(cy-lines[-1][0]) > 4:
                                    lines.append((cy, []))
                                lines[-1][1].append(char)
                            for line_index, (_, chars) in enumerate(lines):
                                chars.sort(key=lambda c: c.bbox.x0)
                                local_chars = [c.model_copy(update={"text_index": i}) for i, c in enumerate(chars)]
                                expanded.append(source.model_copy(update={
                                    "block_id": f"{source.block_id}-cell-{row.row_index}-{cell.col_index}-line-{line_index}",
                                    "text": "".join(c.char for c in local_chars),
                                    "bbox": cls._bbox_union([c.bbox for c in local_chars]),
                                    "source": "signing_table_cell", "raw_html": "", "table_cell_bboxes": [],
                                    "char_boxes": local_chars, "source_evidence": [],
                                }))
                            continue
                        expanded.append(
                            source.model_copy(
                                update={
                                    "block_id": f"{source.block_id}-table-{table_index}-{row.row_index}-{cell.col_index}",
                                    "text": text,
                                    "bbox": cell_bbox,
                                    "source": "signing_table_cell",
                                    "raw_html": "",
                                    "table_cell_bboxes": [],
                                    "char_boxes": char_boxes,
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
        label = cls._compact(text).strip("：:；;＿_")
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
        *,
        use_columns: bool = True,
    ) -> list[TextBlock]:
        field_is_left = (field_bbox.x0 + field_bbox.x1) / 2 < midpoint
        continuations: list[TextBlock] = []
        for candidate in sorted(source_blocks, key=lambda item: (item.bbox.y0, item.bbox.x0)):
            if candidate.block_type in {"seal", "stamp", "image", "figure"} or candidate.flow_role in {"noise", "non_text", "margin"}:
                continue
            overlap_tolerance = 12.0 if source.source == "signing_table_cell" else 2.0
            if candidate.block_id == source.block_id or candidate.bbox.y0 < source.bbox.y1 - overlap_tolerance:
                continue
            if candidate.bbox.y0 - source.bbox.y1 > 48.0:
                break
            if use_columns and ((candidate.bbox.x0 + candidate.bbox.x1) / 2 < midpoint) != field_is_left:
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
            # Wrapped fields continue at their left edge. Floating fragments
            # inside the column (seal serials / curved stamp text) are not a
            # continuation merely because they occur on the next OCR row.
            line_height = max(1.0, field_bbox.y1 - field_bbox.y0)
            continuation_bbox = cls._field_bbox(candidate, 0, len(candidate.text or ""))
            if continuation_bbox.x0 - field_bbox.x0 > max(18.0, line_height * 1.5):
                continue
            if continuations and candidate.bbox.y0 < continuations[-1].bbox.y1 - 2.0:
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

    @classmethod
    def _field_bbox(cls, source: TextBlock, start: int, end: int) -> BBox:
        if source.source == "signing_table_cell" and source.char_boxes:
            target = cls._normalized_chars((source.text or "")[start:end])
            ordered = sorted(source.char_boxes, key=lambda item: (item.bbox.y0, item.bbox.x0))
            chars: list[str] = []
            boxes = []
            for char_box in ordered:
                normalized = cls._normalized_chars(char_box.char)
                chars.extend(normalized)
                boxes.extend([char_box.bbox] * len(normalized))
            offset = "".join(chars).find(target)
            if target and offset >= 0:
                return cls._bbox_union(boxes[offset : offset + len(target)])
        char_boxes = [
            char_box
            for char_box in source.char_boxes
            if char_box.text_index is not None and start <= char_box.text_index < end
        ]
        if char_boxes:
            return cls._bbox_union([char_box.bbox for char_box in char_boxes])
        if source.source == "signing_table_cell":
            return source.bbox
        text_length = max(1, len(source.text or ""))
        width = max(0.0, source.bbox.x1 - source.bbox.x0)
        return source.bbox.model_copy(
            update={
                "x0": source.bbox.x0 + width * start / text_length,
                "x1": source.bbox.x0 + width * end / text_length,
            }
        )

    @staticmethod
    def _uses_party_columns(signing_block: SigningBlock) -> bool:
        return (
            "stacked_party_layout" not in signing_block.confidence_reasons
            and (
                "two_column_layout" in signing_block.confidence_reasons
                or "paired_parties" in signing_block.confidence_reasons
            )
        )

    @classmethod
    def _field_value_roi(
        cls,
        signing_block: SigningBlock,
        *,
        party_role: str,
        midpoint: float,
        source_bbox: BBox,
        field_bbox: BBox,
        value_roi_x0: float,
    ) -> BBox:
        two_column_layout = cls._uses_party_columns(signing_block)
        column_x0 = midpoint if two_column_layout and party_role == "乙方" else signing_block.bbox.x0
        column_x1 = midpoint if two_column_layout and party_role == "甲方" else signing_block.bbox.x1
        return BBox(
            x0=max(column_x0, min(column_x1, value_roi_x0)),
            y0=min(source_bbox.y0, field_bbox.y0),
            x1=min(column_x1, signing_block.bbox.x1),
            y1=max(source_bbox.y1, field_bbox.y1),
        )

    @classmethod
    def _fuse_field_value_evidence(
        cls,
        source: TextBlock,
        *,
        label: str,
        field_key: str,
        primary_value: str,
        primary_value_bbox: BBox | None,
        field_bbox: BBox,
        value_roi: BBox,
        party_role: str,
        midpoint: float,
        signing_block: SigningBlock,
        primary_value_source: TextBlock | None = None,
    ) -> tuple[
        str,
        list[dict[str, object]],
        str,
        str,
        BBox | None,
        list[dict[str, object]],
    ]:
        # `field_bbox` belongs to the current FIELD_RE occurrence.  Re-finding
        # the first label of the same kind here loses that identity when a
        # merged line contains the same field in both party columns.
        two_column_layout = cls._uses_party_columns(signing_block)
        column_x0 = midpoint if two_column_layout and party_role == "乙方" else signing_block.bbox.x0
        column_x1 = midpoint if two_column_layout and party_role == "甲方" else signing_block.bbox.x1
        raw_sources = list(source.source_evidence)
        if not raw_sources:
            raw_sources = [
                {
                    "source": "ppocrv5" if "ppocr" in (source.source or "") else source.source or "document_text",
                    "raw_text": source.text,
                    "page_no": source.page_no,
                    "bbox": source.bbox,
                    "confidence": source.confidence,
                }
            ]
        evidence: list[dict[str, object]] = []
        rejected: list[dict[str, object]] = []

        def reject(
            *,
            source_name: str,
            raw_text: str,
            page_no: int,
            candidate_bbox: BBox,
            confidence: float | None,
            reason: str,
            candidate_value: str = "",
        ) -> None:
            rejected.append(
                {
                    "source": source_name,
                    "raw_text": raw_text,
                    "page_no": page_no,
                    "bbox": candidate_bbox.model_dump(),
                    "confidence": confidence,
                    "field_key": field_key,
                    "party_role": party_role,
                    "localized": False,
                    "provenance_valid": False,
                    "rejection_reason": reason,
                    **(
                        {"candidate_value": candidate_value, "text": candidate_value}
                        if candidate_value
                        else {}
                    ),
                }
            )

        for raw_source in raw_sources:
            source_name = str(
                raw_source.source if hasattr(raw_source, "source") else raw_source.get("source", "unknown")
            )
            is_structure_source = "ppstructure" in source_name.casefold()
            raw_text = str(
                raw_source.raw_text if hasattr(raw_source, "raw_text") else raw_source.get("raw_text", "")
            )
            if source.source == "signing_table_cell" and TABLE_MARKUP_RE.search(raw_text):
                # A table-cell block already has cell-local text and geometry.
                # Reusing the whole PP-Structure HTML here lets FIELD_RE cross
                # </td><td> boundaries and assign a neighbour's value/party.
                continue
            candidate_bbox = BBox.model_validate(
                raw_source.bbox if hasattr(raw_source, "bbox") else raw_source.get("bbox")
            )
            page_no = int(
                raw_source.page_no if hasattr(raw_source, "page_no") else raw_source.get("page_no", source.page_no)
            )
            confidence = (
                raw_source.confidence if hasattr(raw_source, "confidence") else raw_source.get("confidence")
            )
            if page_no != source.page_no:
                continue
            candidate_matches = list(FIELD_RE.finditer(raw_text))
            matching_field_labels = [
                match
                for match in candidate_matches
                if cls._field_key(cls._compact(match.group("label"))) == field_key
            ]
            row_overlap = min(field_bbox.y1, candidate_bbox.y1) - max(field_bbox.y0, candidate_bbox.y0)
            localized_value_fragment = (
                not candidate_matches
                and bool(cls._clean_field_value(raw_text))
                and row_overlap > 0
                and cls._bbox_within_roi(candidate_bbox, value_roi)
            )
            if (
                cls._bbox_overlap_ratio(source.bbox, candidate_bbox) < 0.45
                and not localized_value_fragment
            ):
                continue
            if two_column_layout and not cls._bbox_within_column(
                candidate_bbox,
                column_x0=column_x0,
                column_x1=column_x1,
            ):
                if matching_field_labels:
                    reject(
                        source_name=source_name,
                        raw_text=raw_text,
                        page_no=page_no,
                        candidate_bbox=candidate_bbox,
                        confidence=confidence,
                        reason=(
                            "AMBIGUOUS_MULTI_LABEL_BLOCK"
                            if len(matching_field_labels) > 1
                            else "CROSS_COLUMN_SOURCE_BLOCK"
                        ),
                    )
                continue

            # A value-only Structure token/cell with its own bbox is already
            # spatially localized.  It may support a blank OCR field, provided
            # it is in the same party column, row and value ROI.
            candidate_match = matching_field_labels[0] if len(matching_field_labels) == 1 else None
            if candidate_match is None:
                fragment = cls._clean_field_value(raw_text)
                if (
                    localized_value_fragment
                    and fragment
                ):
                    evidence.append(
                        {
                            "source": source_name,
                            "text": fragment,
                            "raw_text": raw_text,
                            "normalized_text": cls._normalized_chars(fragment),
                            "page_no": page_no,
                            "bbox": candidate_bbox.model_dump(),
                            "confidence": confidence,
                            "field_key": field_key,
                            "party_role": party_role,
                            "localized": True,
                            "provenance_valid": True,
                            "localization_method": "SOURCE_FRAGMENT_BBOX",
                        }
                    )
                elif len(matching_field_labels) > 1:
                    reject(
                        source_name=source_name,
                        raw_text=raw_text,
                        page_no=page_no,
                        candidate_bbox=candidate_bbox,
                        confidence=confidence,
                        reason="AMBIGUOUS_MULTI_LABEL_BLOCK",
                    )
                continue
            source_height = max(1.0, source.bbox.y1 - source.bbox.y0)
            candidate_height = max(1.0, candidate_bbox.y1 - candidate_bbox.y0)
            candidate_field_keys = {
                cls._field_key(cls._compact(match.group("label"))) for match in candidate_matches
            }
            if (
                len(candidate_field_keys) > 1
                and candidate_height > source_height * 1.75
                and any(key != field_key for key in candidate_field_keys)
            ):
                # A layout block spanning several signing rows cannot provide
                # field-local geometry.  In scans it commonly concatenates the
                # next signature stroke to the preceding email/phone value.
                reject(
                    source_name=source_name,
                    raw_text=raw_text,
                    page_no=page_no,
                    candidate_bbox=candidate_bbox,
                    confidence=confidence,
                    reason="UNLOCALIZED_MULTI_ROW_BLOCK",
                )
                continue
            candidate_value = cls._clean_field_value(candidate_match.group("value") or "")
            if not candidate_value:
                continue
            if (
                not is_structure_source
                and raw_text == (source.text or "")
                and primary_value_bbox is not None
            ):
                candidate_value_bbox = primary_value_bbox
            else:
                # A labelled PP-Structure line/block has no value-substring
                # geometry in TextSourceEvidence.  Intersecting its block bbox
                # with the target ROI would fabricate localization and can move
                # a value across party columns.  Keep it only as diagnostics.
                reject(
                    source_name=source_name,
                    raw_text=raw_text,
                    page_no=page_no,
                    candidate_bbox=candidate_bbox,
                    confidence=confidence,
                    reason="UNLOCALIZED_BLOCK_VALUE",
                    candidate_value=candidate_value,
                )
                continue
            if candidate_value_bbox is None:
                continue
            evidence.append(
                {
                    "source": source_name,
                    "text": candidate_value,
                    "raw_text": raw_text,
                    "normalized_text": cls._normalized_chars(candidate_value),
                    "page_no": page_no,
                    "bbox": candidate_value_bbox.model_dump(),
                    "confidence": confidence,
                    "field_key": field_key,
                    "party_role": party_role,
                    "localized": True,
                    "provenance_valid": True,
                    "localization_method": "PRIMARY_VALUE_BBOX",
                }
            )

        if primary_value and not any(item["text"] == primary_value for item in evidence):
            fallback_bbox = primary_value_bbox
            if fallback_bbox is not None:
                fallback_source = primary_value_source or source
                evidence.insert(
                    0,
                    {
                        "source": (
                            "ppocrv5"
                            if "ppocr" in (fallback_source.source or "")
                            else fallback_source.source or "document_text"
                        ),
                        "text": primary_value,
                        "raw_text": fallback_source.text,
                        "normalized_text": cls._normalized_chars(primary_value),
                        "page_no": fallback_source.page_no,
                        "bbox": fallback_bbox.model_dump(),
                        "confidence": fallback_source.confidence,
                        "field_key": field_key,
                        "party_role": party_role,
                        "localized": True,
                        "provenance_valid": True,
                        "localization_method": "PRIMARY_VALUE_BBOX",
                    },
                )
        unique: dict[tuple[str, str], dict[str, object]] = {}
        for item in evidence:
            unique.setdefault((str(item["source"]), str(item["normalized_text"])), item)
        evidence = list(unique.values())
        if not evidence:
            return primary_value, [], "CONFIRMED", "", primary_value_bbox, rejected

        values = list(dict.fromkeys(str(item["normalized_text"]) for item in evidence if item["normalized_text"]))
        display_bbox = cls._bbox_union([BBox.model_validate(item["bbox"]) for item in evidence])
        if len(values) <= 1:
            independent_sources = {str(item["source"]) for item in evidence}
            reason = "SOURCE_VALUES_AGREE" if len(independent_sources) >= 2 else "SINGLE_SOURCE"
            single_source_review = False
            if reason == "SINGLE_SOURCE" and field_key in {*SIGNATURE_FIELD_KEYS, "date"}:
                accepted_value = values[0] if values else ""
                single_source_review = any(
                    item.get("rejection_reason") == "UNLOCALIZED_BLOCK_VALUE"
                    and cls._normalized_chars(str(item.get("candidate_value") or ""))
                    not in {"", accepted_value}
                    for item in rejected
                )
            return (
                primary_value or str(evidence[0]["text"]),
                evidence,
                "NEEDS_REVIEW" if single_source_review else "CONFIRMED",
                reason,
                display_bbox,
                rejected,
            )

        ordered = sorted(values, key=len)
        shortest, longest = ordered[0], ordered[-1]
        containment = shortest and (longest.startswith(shortest) or longest.endswith(shortest))
        longer_evidence = next(item for item in evidence if item["normalized_text"] == longest)
        shorter_evidence = next(item for item in evidence if item["normalized_text"] == shortest)
        longer_bbox = BBox.model_validate(longer_evidence["bbox"])
        shorter_bbox = BBox.model_validate(shorter_evidence["bbox"])
        visual_extension = longer_bbox.x0 < shorter_bbox.x0 - 1.0 or longer_bbox.x1 > shorter_bbox.x1 + 1.0
        if len(values) == 2 and containment and visual_extension:
            if field_key == "email" and EMAIL_VALUE_RE.fullmatch(longest) is None:
                preferred_evidence = next(
                    (
                        item
                        for item in evidence
                        if item["normalized_text"] == cls._normalized_chars(primary_value)
                    ),
                    shorter_evidence,
                )
                return (
                    primary_value or str(preferred_evidence["text"]),
                    evidence,
                    "NEEDS_REVIEW",
                    "INVALID_EMAIL_CANDIDATE_REJECTED",
                    BBox.model_validate(preferred_evidence["bbox"]),
                    rejected,
                )
            return (
                str(longer_evidence["text"]),
                evidence,
                "NEEDS_REVIEW",
                "ALIGNED_SUFFIX_CANDIDATE",
                display_bbox,
                rejected,
            )
        return (
            primary_value,
            evidence,
            "NEEDS_REVIEW",
            "CONFLICTING_VALUE_CANDIDATES",
            display_bbox,
            rejected,
        )

    @staticmethod
    def _bbox_within_roi(bbox: BBox, roi: BBox, tolerance: float = 2.0) -> bool:
        return (
            bbox.x0 >= roi.x0 - tolerance
            and bbox.x1 <= roi.x1 + tolerance
            and bbox.y0 >= roi.y0 - tolerance
            and bbox.y1 <= roi.y1 + tolerance
        )

    @staticmethod
    def _bbox_within_column(
        bbox: BBox,
        *,
        column_x0: float,
        column_x1: float,
        tolerance: float = 2.0,
    ) -> bool:
        return bbox.x0 >= column_x0 - tolerance and bbox.x1 <= column_x1 + tolerance

    @staticmethod
    def _bbox_intersection(left: BBox, right: BBox) -> BBox | None:
        x0 = max(left.x0, right.x0)
        y0 = max(left.y0, right.y0)
        x1 = min(left.x1, right.x1)
        y1 = min(left.y1, right.y1)
        if x1 <= x0 or y1 <= y0:
            return None
        return BBox(x0=x0, y0=y0, x1=x1, y1=y1)

    @classmethod
    def _bbox_overlap_ratio(cls, left: BBox, right: BBox) -> float:
        intersection = cls._bbox_intersection(left, right)
        if intersection is None:
            return 0.0
        intersection_area = (intersection.x1 - intersection.x0) * (intersection.y1 - intersection.y0)
        smaller_area = min(
            max(1.0, (left.x1 - left.x0) * (left.y1 - left.y0)),
            max(1.0, (right.x1 - right.x0) * (right.y1 - right.y0)),
        )
        return intersection_area / smaller_area

    @classmethod
    def _date_component_ref(cls, source: TextBlock) -> dict[str, object]:
        match = cls._date_component_match(source, source.text or "")
        if match is None:
            return {}
        components: dict[str, str] = {}
        component_bboxes: dict[str, dict[str, object]] = {}
        for name in ("year", "month", "day"):
            value = (match.group(name) or "").strip()
            components[name] = value
            if not value:
                continue
            start, end = match.span(name)
            component_bboxes[name] = cls._field_bbox(source, start, end).model_dump()
        value_bbox = cls._date_value_bbox_from_char_boxes(source)
        if value_bbox is None:
            value_bbox = cls._field_bbox(source, match.start(), match.end())
        return {
            "date_components": components,
            "date_component_bboxes": component_bboxes,
            **({"date_value_bbox": value_bbox.model_dump()} if value_bbox is not None else {}),
        }

    @classmethod
    def _date_value_bbox_from_char_boxes(cls, source: TextBlock) -> BBox | None:
        if not source.char_boxes:
            return None
        ordered = sorted(source.char_boxes, key=lambda item: (item.bbox.y0, item.bbox.x0))
        chars: list[str] = []
        boxes: list[BBox] = []
        for char_box in ordered:
            normalized = cls._normalized_chars(char_box.char)
            chars.extend(normalized)
            boxes.extend([char_box.bbox] * len(normalized))
        match = cls._date_component_match(source, "".join(chars))
        if match is None:
            return None
        return cls._bbox_union(boxes[match.start() : match.end()])

    @staticmethod
    def _date_component_match(source: TextBlock, text: str) -> re.Match[str] | None:
        match = DATE_COMPONENT_RE.search(text)
        if match is None and source.source == "signing_table_cell":
            match = OCR_TOLERANT_DATE_COMPONENT_RE.search(text)
        if match is None:
            match = SEPARATED_DATE_COMPONENT_RE.search(text)
        return match

    @staticmethod
    def _date_parts(value: str) -> tuple[int, int, int] | None:
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))
        match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", compact)
        if match is None:
            match = re.search(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})", compact)
        if match is None:
            return None
        parts = tuple(int(item) for item in match.groups())
        try:
            date(*parts)
        except ValueError:
            return None
        return parts

    @staticmethod
    def _normalized_chars(text: str) -> str:
        return "".join(unicodedata.normalize("NFKC", text or "").split())

    @staticmethod
    def _clean_field_value(text: str) -> str:
        value = html.unescape(text or "")
        boundary = TABLE_MARKUP_RE.search(value)
        if boundary is not None:
            value = value[: boundary.start()]
        value = re.sub(r"<[^>]+>", "", value)
        return value.strip()

    @staticmethod
    def _field_key(label: str) -> str:
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", label or ""))
        for field_key, pattern in FIELD_ALIASES:
            if pattern.search(compact):
                return field_key
        return f"custom:{compact.casefold()}"

    @classmethod
    def _business_role_for_field(cls, bbox: BBox, source_blocks: list[TextBlock]) -> str:
        """Bind business roles by their own local labels, even if rows reorder."""
        anchors = [
            (match.group("role"), cls._field_bbox(block, match.start(), match.end()))
            for block in source_blocks for match in PARTY_FIELD_RE.finditer(block.text or "")
            if match.group("role") in {"买方", "卖方", "设计方"}
        ]
        if len({role for role, _ in anchors}) < 2:
            return ""
        # A field starts under its heading.  Restrict to the same column before
        # taking the nearest preceding heading; party names may be very wide.
        eligible = [(role, anchor) for role, anchor in anchors
                    if anchor.y0 <= bbox.y0 + 3 and abs(anchor.x0 - bbox.x0) <= 70]
        if not eligible:
            return "unknown"
        return max(eligible, key=lambda item: item[1].y0)[0]

    @staticmethod
    def _field_party_role(
        label: str,
        bbox: BBox,
        midpoint: float,
        signing_block: SigningBlock,
        *,
        occurrence: int,
        occurrence_count: int,
        stacked_midpoint: float | None = None,
    ) -> str:
        for role in ("甲方", "乙方", "丙方", "丁方"):
            if role in label:
                return role
        center_x = (bbox.x0 + bbox.x1) / 2
        if "two_column_layout" in signing_block.confidence_reasons:
            # OCR occasionally joins the tail of the right column to a field
            # that starts in the left column.  The label/start edge remains a
            # more stable identity signal than the resulting very wide bbox.
            width = max(1.0, signing_block.bbox.x1 - signing_block.bbox.x0)
            if bbox.x0 < midpoint - width * 0.12 < bbox.x1:
                return "甲方"
            return "甲方" if center_x < midpoint else "乙方"
        if "stacked_party_layout" in signing_block.confidence_reasons and stacked_midpoint is not None:
            return "甲方" if (bbox.y0 + bbox.y1) / 2 < stacked_midpoint else "乙方"
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
