from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models import BBox, Document, Page, TextBlock
from app.services.signing_region.models import (
    SigningBlock,
    SigningBlockConfidenceLevel,
    SigningBlockRole,
    SigningPage,
    SigningPageType,
)


PARTY_RE = re.compile(r"甲方|乙方|丙方|丁方")
PARTY_LABEL_RE = re.compile(r"(?:甲方|乙方|丙方|丁方)[:：]")
SEAL_RE = re.compile(r"盖章|签章|公章")
SIGN_RE = re.compile(r"签字|签名")
REPRESENTATIVE_RE = re.compile(r"法定代表人|法人代表|授权代表|授权委托人")
DATE_LABEL_RE = re.compile(r"日期[:：]|签订日期|签署日期")
SIGNING_CONTEXT_RE = re.compile(r"以下无正文|签署页|签字页")
BODY_VERB_RE = re.compile(r"应当|负责|承担|履行|支付|违约|权利|义务|为准|合同经|生效|协商|约定")
NUMBERED_RE = re.compile(r"^\s*(?:第[一二三四五六七八九十百千万0-9]+[章节条款]|[一二三四五六七八九十百千万0-9]+[、.．]|\d+(?:\.\d+){0,4}[、.．]?)")


@dataclass
class SigningBlockDetectionResult:
    pages: list[SigningPage] = field(default_factory=list)
    blocks: list[SigningBlock] = field(default_factory=list)
    excluded_candidates: list[dict[str, object]] = field(default_factory=list)
    low_confidence_candidates: list[dict[str, object]] = field(default_factory=list)


class SigningBlockDetector:
    bottom_ratio = 0.58
    top_continuation_ratio = 0.24
    cluster_gap = 92.0
    padding = 18.0

    def detect(self, document: Document) -> SigningBlockDetectionResult:
        result = SigningBlockDetectionResult()
        page_roles = self._page_roles(document)
        for page in document.pages:
            blocks = self._detect_page_blocks(page, page_roles.get(page.page_no, "body"), result)
            result.blocks.extend(blocks)
            if blocks:
                result.pages.append(self._to_page(page, page_roles.get(page.page_no, "body"), blocks))
        return result

    def _detect_page_blocks(
        self,
        page: Page,
        page_role: str,
        result: SigningBlockDetectionResult,
    ) -> list[SigningBlock]:
        candidates = [block for block in page.blocks if self._is_candidate(block, page)]
        if not candidates:
            return []
        signing_blocks: list[SigningBlock] = []
        for index, cluster in enumerate(self._cluster(candidates), start=1):
            text = "\n".join(block.text.strip() for block in cluster if block.text.strip())
            bbox = self._padded_union([self._effective_bbox(block) for block in cluster], page)
            score, reasons = self._score_cluster(cluster, page, page_role)
            if self._is_cover_signing_info_table(cluster, page_role, reasons):
                result.excluded_candidates.append({
                    "page_no": page.page_no,
                    "block_ids": [block.block_id for block in cluster],
                    "reason": "cover_signing_info_table",
                    "text": text[:200],
                })
                continue
            if score < 0.5:
                result.low_confidence_candidates.append({
                    "page_no": page.page_no,
                    "block_ids": [block.block_id for block in cluster],
                    "score": score,
                    "reasons": reasons,
                    "text": text[:200],
                })
                continue
            signing_blocks.append(SigningBlock(
                block_id=f"SB-{page.page_no}-{index}",
                page_no=page.page_no,
                bbox=bbox,
                block_role=self._role(text),
                confidence=score,
                confidence_level=self._level(score),
                confidence_reasons=reasons,
                source_block_ids=[block.block_id for block in cluster],
                text=text,
                exclude_from_clause_diff=score >= 0.7,
            ))
        return signing_blocks

    def _is_candidate(self, block: TextBlock, page: Page) -> bool:
        text = self._compact(block.text)
        if not text:
            return False
        bbox = self._effective_bbox(block)
        in_bottom = bbox.y1 >= page.height * self.bottom_ratio
        in_top = bbox.y0 <= page.height * self.top_continuation_ratio
        has_signing_signal = any(pattern.search(text) for pattern in [
            PARTY_RE,
            SEAL_RE,
            SIGN_RE,
            REPRESENTATIVE_RE,
            DATE_LABEL_RE,
            SIGNING_CONTEXT_RE,
        ])
        if not has_signing_signal:
            return False
        if self._looks_like_contract_body_text(block, text):
            return False
        return in_bottom or in_top or bool(SIGNING_CONTEXT_RE.search(text))

    def _score_cluster(self, blocks: list[TextBlock], page: Page, page_role: str) -> tuple[float, list[str]]:
        text = self._compact("\n".join(block.text for block in blocks))
        reasons: list[str] = []
        score = 0.0
        if PARTY_RE.search(text) and "甲方" in text and "乙方" in text:
            score += 0.25
            reasons.append("paired_parties")
        elif PARTY_RE.search(text):
            score += 0.1
            reasons.append("party_label")
        signal_count = sum(bool(pattern.search(text)) for pattern in [SEAL_RE, SIGN_RE, REPRESENTATIVE_RE, DATE_LABEL_RE])
        if signal_count >= 3:
            score += 0.35
            reasons.append("seal_signature_date_cluster")
        elif signal_count >= 2:
            score += 0.25
            reasons.append("multiple_signing_labels")
        if SIGNING_CONTEXT_RE.search(text):
            score += 0.2
            reasons.append("signing_page_context")
        if self._cluster_in_bottom(blocks, page):
            score += 0.15
            reasons.append("bottom_signing_position")
        if self._cluster_in_top(blocks, page) and signal_count >= 2:
            score += 0.15
            reasons.append("top_signing_continuation")
        if self._looks_two_column(blocks, page):
            score += 0.1
            reasons.append("two_column_layout")
        if page_role == "cover":
            score -= 0.35
            reasons.append("cover_page_penalty")
        return max(0.0, min(round(score, 2), 1.0)), reasons

    def _is_cover_signing_info_table(self, blocks: list[TextBlock], page_role: str, reasons: list[str]) -> bool:
        if page_role != "cover":
            return False
        text = self._compact("\n".join(block.text for block in blocks))
        has_cover_table = any((block.block_type or "").lower() == "table" for block in blocks)
        has_only_contract_meta = "签订地点" in text or "签订日期" in text
        has_strong_signing = SEAL_RE.search(text) or SIGN_RE.search(text) or REPRESENTATIVE_RE.search(text) or SIGNING_CONTEXT_RE.search(text)
        return has_cover_table and has_only_contract_meta and not has_strong_signing and "cover_page_penalty" in reasons

    def _cluster(self, blocks: list[TextBlock]) -> list[list[TextBlock]]:
        ordered = sorted(blocks, key=lambda block: (block.page_no, self._effective_bbox(block).y0, self._effective_bbox(block).x0))
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

    def _to_page(self, page: Page, page_role: str, blocks: list[SigningBlock]) -> SigningPage:
        full_page = self._is_full_signing_page(page, blocks)
        return SigningPage(
            page_no=page.page_no,
            bbox=BBox(x0=0, y0=0, x1=page.width, y1=page.height),
            page_role=page_role,
            signing_page_type=SigningPageType.FULL_PAGE if full_page else SigningPageType.MIXED_PAGE,
            confidence=max(block.confidence for block in blocks),
            confidence_reasons=["contains_high_confidence_signing_block"],
            block_ids=[block.block_id for block in blocks],
            exclude_full_page_from_clause_diff=full_page and all(block.exclude_from_clause_diff for block in blocks),
        )

    def _is_full_signing_page(self, page: Page, blocks: list[SigningBlock]) -> bool:
        if not page.blocks or not blocks or not all(block.exclude_from_clause_diff for block in blocks):
            return False

        signing_source_ids = {block_id for block in blocks for block_id in block.source_block_ids}
        non_signing_blocks = [block for block in page.blocks if block.block_id not in signing_source_ids]
        if any(self._is_substantive_non_signing_block(block, page) for block in non_signing_blocks):
            return False

        signing_source_count = len(signing_source_ids)
        total_text_chars = sum(len(self._compact(block.text)) for block in page.blocks)
        signing_text_chars = sum(
            len(self._compact(block.text))
            for block in page.blocks
            if block.block_id in signing_source_ids
        )
        signing_text_ratio = signing_text_chars / total_text_chars if total_text_chars else 0.0
        has_signing_context = any(SIGNING_CONTEXT_RE.search(self._compact(block.text)) for block in page.blocks)
        has_signing_only_blocks = not non_signing_blocks
        has_full_page_shape = self._blocks_cover_full_page_shape(page, blocks)
        has_split_signing_blocks = has_signing_only_blocks and signing_source_count >= 3

        return (
            signing_text_ratio >= 0.55
            and (has_signing_context or has_full_page_shape or has_split_signing_blocks)
        )

    def _is_substantive_non_signing_block(self, block: TextBlock, page: Page) -> bool:
        text = self._compact(block.text)
        if not text:
            return False
        if SIGNING_CONTEXT_RE.search(text):
            return False
        if self._is_candidate(block, page):
            return False
        return len(text) >= 12 or BODY_VERB_RE.search(text) is not None or NUMBERED_RE.match(text) is not None

    @staticmethod
    def _looks_like_contract_body_text(block: TextBlock, text: str) -> bool:
        if not (BODY_VERB_RE.search(text) or NUMBERED_RE.match(text)):
            return False
        has_form_signal = (
            PARTY_LABEL_RE.search(text)
            or REPRESENTATIVE_RE.search(text)
            or DATE_LABEL_RE.search(text)
            or SIGNING_CONTEXT_RE.search(text)
        )
        if has_form_signal:
            return False
        if (block.block_type or "").lower() == "table" and SEAL_RE.search(text) and SIGN_RE.search(text):
            return False
        return True

    @staticmethod
    def _blocks_cover_full_page_shape(page: Page, blocks: list[SigningBlock]) -> bool:
        y0 = min(block.bbox.y0 for block in blocks)
        y1 = max(block.bbox.y1 for block in blocks)
        return y0 <= page.height * 0.35 and y1 >= page.height * 0.72

    @staticmethod
    def _page_roles(document: Document) -> dict[int, str]:
        if document.profile is None:
            return {}
        return {profile.page_no: profile.page_role for profile in document.profile.page_profiles}

    @staticmethod
    def _role(text: str) -> SigningBlockRole:
        compact = re.sub(r"\s+", "", text)
        if "甲方" in compact and "乙方" in compact:
            return SigningBlockRole.BOTH_PARTIES
        if "甲方" in compact:
            return SigningBlockRole.PARTY_A
        if "乙方" in compact:
            return SigningBlockRole.PARTY_B
        return SigningBlockRole.UNKNOWN

    @staticmethod
    def _level(score: float) -> SigningBlockConfidenceLevel:
        if score >= 0.7:
            return SigningBlockConfidenceLevel.HIGH
        if score >= 0.5:
            return SigningBlockConfidenceLevel.MEDIUM
        return SigningBlockConfidenceLevel.LOW

    def _cluster_in_bottom(self, blocks: list[TextBlock], page: Page) -> bool:
        return any(self._effective_bbox(block).y1 >= page.height * self.bottom_ratio for block in blocks)

    def _cluster_in_top(self, blocks: list[TextBlock], page: Page) -> bool:
        return any(self._effective_bbox(block).y0 <= page.height * self.top_continuation_ratio for block in blocks)

    def _looks_two_column(self, blocks: list[TextBlock], page: Page) -> bool:
        centers = [(self._effective_bbox(block).x0 + self._effective_bbox(block).x1) / 2 for block in blocks]
        return bool(centers) and min(centers) < page.width * 0.35 and max(centers) > page.width * 0.6

    def _padded_union(self, bboxes: list[BBox], page: Page) -> BBox:
        x0 = max(0.0, min(bbox.x0 for bbox in bboxes) - self.padding)
        y0 = max(0.0, min(bbox.y0 for bbox in bboxes) - self.padding)
        x1 = min(page.width, max(bbox.x1 for bbox in bboxes) + self.padding)
        y1 = min(page.height, max(bbox.y1 for bbox in bboxes) + self.padding)
        return BBox(x0=x0, y0=y0, x1=x1, y1=y1)

    @staticmethod
    def _effective_bbox(block: TextBlock) -> BBox:
        return block.layout_bbox or block.bbox

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"\s+", "", text or "")
