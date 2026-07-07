from __future__ import annotations

import re

from app.models import BBox, Document, Page, TextBlock
from app.services.signing_region.models import (
    SigningBlock,
    SigningElement,
    SigningElementType,
    SigningRegion,
    SigningRegionRole,
)


SIGNING_ANCHOR_RE = re.compile(r"甲方|乙方|丙方|丁方|盖章|签章|签字|签署|签订日期|签署日期|法定代表人|授权代表|年月日")
BODY_RE = re.compile(r"应当|负责|承担|履行|支付|违约|权利|义务|为准|合同经|生效|协商|约定")
NUMBERED_RE = re.compile(r"^\s*(?:第[一二三四五六七八九十百千万0-9]+[章节条款]|[一二三四五六七八九十百千万0-9]+[、.．]|\d+(?:\.\d+){0,4}[、.．]?)")
DATE_RE = re.compile(
    r"(?:\d{4}|[_＿]{2,4})\s*年\s*(?:\d{1,2}|[_＿]{1,4})?\s*月\s*(?:\d{1,2}|[_＿]{1,4})?\s*日"
    r"|年\s*(?:\d{1,2}|[_＿]{1,4})?\s*月\s*(?:\d{1,2}|[_＿]{1,4})?\s*日"
    r"|[_＿]{2,4}\s*年"
)
VISUAL_TYPES = {"seal", "stamp", "image", "figure", "table"}


class SigningRegionExtractor:
    bottom_ratio = 0.62
    cluster_gap = 90.0
    padding = 20.0

    def extract(self, document: Document) -> list[SigningRegion]:
        regions: list[SigningRegion] = []
        for page in document.pages:
            regions.extend(self._extract_page(page))
        return regions

    def extract_from_blocks(self, blocks: list[SigningBlock]) -> list[SigningRegion]:
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
