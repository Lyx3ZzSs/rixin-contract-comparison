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
PARTY_LABEL_RE = re.compile(r"(?:甲方|乙方|丙方|丁方)\s*[:：]")
SEAL_RE = re.compile(r"盖章|签章|公章")
SIGN_RE = re.compile(r"签字|签名")
REPRESENTATIVE_RE = re.compile(r"法定代表人|法人代表|授权代表|授权委托人")
DATE_LABEL_RE = re.compile(r"日期[:：]|时间[:：]|签订日期|签署日期|签订时间|签署时间")
SIGNING_CONTEXT_RE = re.compile(r"以下无正文|签署页|签字页")
SIGNING_PAGE_TITLE_RE = re.compile(r"^\s*(?:签署页|签字页)\s*$")
SIGNING_TAIL_CONTEXT_RE = re.compile(r"合同经双方.{0,30}(?:签字|签署|盖章)|一式[一二两三四五六七八九十百千万\d]+份|传真件有效")
SIGNING_CONTINUATION_RE = re.compile(r"地址|邮编|电话|联系人|开户|账号|统一社会信用代码|邮箱|电子邮箱|E-?mail|传真")
DATE_TIME_CONTINUATION_LABEL_RE = re.compile(
    r"^(?:日期|时间)[:：]?(?:\d{2,4}(?:[年./-]\d{0,2})?(?:[月./-]\d{0,2})?日?\.?)?$"
)
BUSINESS_SIGNING_FIELD_RE = re.compile(
    r"单位名称|单位地址|地址|联系人|法人代表|法定代表人|授权委托人|委托代理人|电话|传真|开户银行|账号|帐|税号|"
    r"纳税人识别号|邮政编码|统一社会信用代码|邮箱|电子邮箱|E-?mail",
    re.IGNORECASE,
)
BODY_VERB_RE = re.compile(r"应当|负责(?!人)|承担|履行|支付|违约|权利|义务|为准|合同经|生效|协商|约定")
NUMBERED_RE = re.compile(r"^\s*(?:第[一二三四五六七八九十百千万0-9]+[章节条款]|[一二三四五六七八九十百千万0-9]+[、.．]|\d+(?:\.\d+){0,4}[、.．]?)")
PAGE_FOOTER_RE = re.compile(r"^(?:共?\d+页第\d+页|第\d+页|[-—_]*\d+[-—_]*)$")
BUSINESS_FIELD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("address", re.compile(r"地址")),
    ("contact", re.compile(r"联系人")),
    ("phone", re.compile(r"电话")),
    ("fax", re.compile(r"传真")),
    ("email", re.compile(r"邮箱|电子邮箱|E-?mail", re.IGNORECASE)),
    ("bank", re.compile(r"开户")),
    ("account", re.compile(r"账号|帐")),
    ("credit", re.compile(r"统一社会信用代码|税号|纳税人识别号")),
)


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
    continuation_gap = 64.0
    signing_title_continuation_gap = 220.0
    stacked_signing_continuation_gap = 170.0
    side_padding = 18.0
    top_padding = 8.0
    bottom_padding = 18.0

    def detect(self, document: Document) -> SigningBlockDetectionResult:
        result = SigningBlockDetectionResult()
        page_roles = self._page_roles(document)
        pages_by_no = {page.page_no: page for page in document.pages}
        terminal_page_no = max(pages_by_no) if pages_by_no else 0
        for page in document.pages:
            blocks = self._detect_page_blocks(
                page,
                page_roles.get(page.page_no, "body"),
                result,
                previous_page=pages_by_no.get(page.page_no - 1),
                is_terminal_page=page.page_no == terminal_page_no,
            )
            result.blocks.extend(blocks)
            if blocks:
                result.pages.append(self._to_page(page, page_roles.get(page.page_no, "body"), blocks))
        return result

    def _detect_page_blocks(
        self,
        page: Page,
        page_role: str,
        result: SigningBlockDetectionResult,
        *,
        previous_page: Page | None = None,
        is_terminal_page: bool = False,
    ) -> list[SigningBlock]:
        candidates = self._candidate_blocks(
            page,
            page_role,
            previous_page=previous_page,
            is_terminal_page=is_terminal_page,
        )
        if not candidates:
            return []
        signing_blocks: list[SigningBlock] = []
        for index, cluster in enumerate(self._cluster(candidates), start=1):
            cluster = self._extend_signing_continuation(cluster, page)
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
                    "bbox": bbox.model_dump(mode="json"),
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
                exclude_from_clause_diff=self._should_exclude_from_clause_diff(score, reasons),
            ))
        return self._suppress_nested_blocks(signing_blocks)

    @staticmethod
    def _suppress_nested_blocks(blocks: list[SigningBlock]) -> list[SigningBlock]:
        retained: list[SigningBlock] = []
        for block in blocks:
            source_ids = set(block.source_block_ids)
            if source_ids and any(
                block.block_id != other.block_id
                and block.confidence < other.confidence
                and other.confidence >= 0.7
                and source_ids <= set(other.source_block_ids)
                for other in blocks
            ):
                continue
            retained.append(block)
        return retained

    def _candidate_blocks(
        self,
        page: Page,
        page_role: str,
        *,
        previous_page: Page | None = None,
        is_terminal_page: bool = False,
    ) -> list[TextBlock]:
        selected = {block.block_id: block for block in page.blocks if self._is_candidate(block, page)}
        if page_role != "cover" and self._has_lower_middle_signing_tail_context(page, list(selected.values())):
            for block in page.blocks:
                if block.block_id not in selected and self._is_lower_middle_signing_tail_block(block, page):
                    selected[block.block_id] = block
        if (
            page_role != "cover"
            and self._has_terminal_business_signing_context(page, previous_page, is_terminal_page)
        ):
            for block in self._business_only_signing_blocks(page):
                selected.setdefault(block.block_id, block)
        return sorted(selected.values(), key=lambda block: (self._effective_bbox(block).y0, self._effective_bbox(block).x0))

    def _is_candidate(self, block: TextBlock, page: Page) -> bool:
        text = self._compact(block.text)
        if not text:
            return False
        bbox = self._effective_bbox(block)
        in_bottom = bbox.y1 >= page.height * self.bottom_ratio
        in_top = bbox.y0 <= page.height * self.top_continuation_ratio
        has_party = bool(PARTY_RE.search(text))
        has_party_label = bool(PARTY_LABEL_RE.search(text))
        has_structural_signing_signal = any(pattern.search(text) for pattern in [
            SEAL_RE,
            SIGN_RE,
            REPRESENTATIVE_RE,
            DATE_LABEL_RE,
            SIGNING_CONTEXT_RE,
        ])
        has_signing_signal = has_party or has_structural_signing_signal
        if not has_signing_signal:
            return False
        if has_party and not has_party_label and not has_structural_signing_signal:
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
        if SIGNING_CONTEXT_RE.search(text) and self._business_signing_field_count(text) >= 3:
            score += 0.25
            reasons.append("signing_context_business_fields")
        if not SIGNING_CONTEXT_RE.search(text) and self._is_terminal_business_signing_cluster(blocks, page):
            score += 0.6
            reasons.append("terminal_business_signing_fields")
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
        return has_cover_table and has_only_contract_meta and "cover_page_penalty" in reasons

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

    def _extend_signing_continuation(self, cluster: list[TextBlock], page: Page) -> list[TextBlock]:
        if not self._should_extend_signing_continuation(cluster):
            return cluster

        selected = {block.block_id: block for block in cluster}
        current_bbox = self._raw_union([self._effective_bbox(block) for block in cluster])
        ordered_blocks = sorted(page.blocks, key=lambda block: (self._effective_bbox(block).y0, self._effective_bbox(block).x0))
        for block in ordered_blocks:
            if block.block_id in selected:
                current_bbox = self._raw_union([current_bbox, self._effective_bbox(block)])
                continue

            bbox = self._effective_bbox(block)
            if bbox.y0 < current_bbox.y0:
                continue
            if self._is_page_footer(block, page):
                continue
            vertical_gap = max(0.0, bbox.y0 - current_bbox.y1)
            if vertical_gap > self._continuation_gap_for(cluster, block):
                continue
            if not self._horizontally_related(current_bbox, bbox, page):
                continue
            if not self._is_signing_continuation_block(block) and not self._is_stacked_signing_visual_continuation(
                cluster,
                block,
            ):
                continue

            selected[block.block_id] = block
            current_bbox = self._raw_union([current_bbox, bbox])

        return sorted(selected.values(), key=lambda block: (self._effective_bbox(block).y0, self._effective_bbox(block).x0))

    def _continuation_gap_for(self, cluster: list[TextBlock], candidate: TextBlock) -> float:
        text = self._compact("\n".join(block.text for block in cluster))
        if SIGNING_PAGE_TITLE_RE.match(text) and self._is_business_signing_continuation(candidate):
            return self.signing_title_continuation_gap
        if self._is_stacked_signing_continuation(cluster, candidate):
            return self.stacked_signing_continuation_gap
        return self.continuation_gap

    def _should_extend_signing_continuation(self, cluster: list[TextBlock]) -> bool:
        text = self._compact("\n".join(block.text for block in cluster))
        signal_count = sum(bool(pattern.search(text)) for pattern in [SEAL_RE, SIGN_RE, REPRESENTATIVE_RE, DATE_LABEL_RE])
        has_party_seal_header = PARTY_RE.search(text) is not None and SEAL_RE.search(text) is not None
        return bool(SIGNING_CONTEXT_RE.search(text)) or (
            PARTY_LABEL_RE.search(text) is not None and signal_count >= 2
        ) or has_party_seal_header

    def _is_signing_continuation_block(self, block: TextBlock) -> bool:
        text = self._compact(block.text)
        if not text:
            return False
        if self._is_signing_date_value(text):
            return True
        if self._looks_like_contract_body_text(block, text):
            return False
        has_explicit_signing_field = any(pattern.search(text) for pattern in [
            PARTY_LABEL_RE,
            SEAL_RE,
            SIGN_RE,
            REPRESENTATIVE_RE,
            DATE_LABEL_RE,
            SIGNING_CONTINUATION_RE,
        ]) or self._is_date_time_continuation_label(text)
        if has_explicit_signing_field:
            return True
        if self._is_visual_fragment(block):
            return False
        return len(text) <= 80 and NUMBERED_RE.match(text) is None and BODY_VERB_RE.search(text) is None

    def _has_lower_middle_signing_tail_context(self, page: Page, candidates: list[TextBlock]) -> bool:
        if not candidates:
            return False
        if not any(self._effective_bbox(block).y0 >= page.height * 0.35 for block in candidates):
            return False
        page_text = self._compact("\n".join(block.text for block in page.blocks))
        return SIGNING_TAIL_CONTEXT_RE.search(page_text) is not None

    def _is_lower_middle_signing_tail_block(self, block: TextBlock, page: Page) -> bool:
        text = self._compact(block.text)
        if not text:
            return False
        bbox = self._effective_bbox(block)
        if bbox.y0 < page.height * 0.35:
            return False
        if self._is_page_footer(block, page):
            return False
        if self._looks_like_contract_body_text(block, text):
            return False
        return any(pattern.search(text) for pattern in [
            PARTY_LABEL_RE,
            SEAL_RE,
            SIGN_RE,
            REPRESENTATIVE_RE,
            DATE_LABEL_RE,
            SIGNING_CONTINUATION_RE,
        ]) or self._is_date_time_continuation_label(text)

    def _is_business_signing_continuation(self, block: TextBlock) -> bool:
        text = self._compact(block.text)
        return (
            bool(text)
            and not self._looks_like_contract_body_text(block, text)
            and (SIGNING_CONTINUATION_RE.search(text) is not None or self._is_date_time_continuation_label(text))
        )

    def _is_stacked_signing_visual_continuation(self, cluster: list[TextBlock], block: TextBlock) -> bool:
        if not self._is_visual_fragment(block):
            return False
        cluster_bbox = self._raw_union([self._effective_bbox(item) for item in cluster])
        if not self._horizontally_overlaps(cluster_bbox, self._effective_bbox(block)):
            return False
        return self._is_stacked_signing_continuation(cluster, block)

    def _is_stacked_signing_continuation(self, cluster: list[TextBlock], candidate: TextBlock) -> bool:
        cluster_text = self._compact("\n".join(block.text for block in cluster))
        signal_count = sum(bool(pattern.search(cluster_text)) for pattern in [SEAL_RE, SIGN_RE, REPRESENTATIVE_RE, DATE_LABEL_RE])
        if not (PARTY_RE.search(cluster_text) and signal_count >= 2):
            return False

        text = self._compact(candidate.text)
        if not text:
            return False
        if self._looks_like_contract_body_text(candidate, text):
            return False
        if self._is_visual_fragment(candidate):
            return True
        if PARTY_RE.search(text) and (SEAL_RE.search(text) or REPRESENTATIVE_RE.search(text)):
            return True
        return any(pattern.search(text) for pattern in [SEAL_RE, SIGN_RE, REPRESENTATIVE_RE, DATE_LABEL_RE])

    def _has_terminal_business_signing_context(
        self,
        page: Page,
        previous_page: Page | None,
        is_terminal_page: bool,
    ) -> bool:
        if not is_terminal_page or not self._previous_page_has_signing_tail_marker(previous_page):
            return False
        return self._looks_like_business_only_signing_page(page)

    def _looks_like_business_only_signing_page(self, page: Page) -> bool:
        content_blocks = self._business_only_signing_blocks(page)
        if len(content_blocks) < 6:
            return False
        text = self._compact("\n".join(block.text for block in content_blocks))
        if self._business_field_category_count(text) < 5:
            return False
        field_blocks = [block for block in content_blocks if self._business_field_category_count(block.text) > 0]
        if len(field_blocks) < 4 or not self._looks_two_column(field_blocks, page):
            return False
        for block in page.blocks:
            if block in content_blocks or self._is_ignorable_for_business_only_page(block, page):
                continue
            if self._is_short_business_value_block(block, page):
                continue
            block_text = self._compact(block.text)
            if NUMBERED_RE.match(block_text) or BODY_VERB_RE.search(block_text) or len(block_text) >= 12:
                return False
        return True

    def _business_only_signing_blocks(self, page: Page) -> list[TextBlock]:
        blocks: list[TextBlock] = []
        for block in page.blocks:
            text = self._compact(block.text)
            if not text:
                continue
            if self._is_ignorable_for_business_only_page(block, page):
                continue
            if self._looks_like_contract_body_text(block, text) and not self._is_short_business_value_block(block, page):
                continue
            bbox = self._effective_bbox(block)
            if bbox.y0 < page.height * 0.22:
                continue
            blocks.append(block)
        return sorted(blocks, key=lambda block: (self._effective_bbox(block).y0, self._effective_bbox(block).x0))

    def _is_ignorable_for_business_only_page(self, block: TextBlock, page: Page) -> bool:
        if self._is_page_footer(block, page) or self._is_visual_fragment(block):
            return True
        block_type = (block.block_type or "").lower()
        flow_role = (block.flow_role or "").lower()
        block_role = (block.block_role or "").lower()
        return "header" in {block_type, flow_role, block_role} or flow_role == "margin"

    def _is_terminal_business_signing_cluster(self, blocks: list[TextBlock], page: Page) -> bool:
        if len(blocks) < 6:
            return False
        text = self._compact("\n".join(block.text for block in blocks))
        return (
            self._business_field_category_count(text) >= 5
            and self._looks_two_column(blocks, page)
            and all(
                not self._looks_like_contract_body_text(block, self._compact(block.text))
                or self._is_short_business_value_block(block, page)
                for block in blocks
            )
        )

    @staticmethod
    def _previous_page_has_signing_tail_marker(page: Page | None) -> bool:
        if page is None:
            return False
        text = re.sub(r"\s+", "", "\n".join(block.text for block in page.blocks))
        return SIGNING_CONTEXT_RE.search(text) is not None or SIGNING_TAIL_CONTEXT_RE.search(text) is not None

    def _is_short_business_value_block(self, block: TextBlock, page: Page) -> bool:
        text = self._compact(block.text)
        if not text or len(text) > 32:
            return False
        if self._is_ignorable_for_business_only_page(block, page):
            return False
        if BODY_VERB_RE.search(text) or text.endswith(("。", "；", ";")):
            return False
        return True

    @staticmethod
    def _is_visual_fragment(block: TextBlock) -> bool:
        return (block.block_type or "").lower() in {"seal", "image", "non_text"} or (block.flow_role or "").lower() == "non_text"

    @staticmethod
    def _is_signing_date_value(text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        return bool(
            re.fullmatch(r"\d{4}年\d{1,2}月(?:\d{1,2})?日", compact)
            or re.fullmatch(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", compact)
        )

    @staticmethod
    def _is_date_time_continuation_label(text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        return DATE_TIME_CONTINUATION_LABEL_RE.fullmatch(compact) is not None

    @staticmethod
    def _business_signing_field_count(text: str) -> int:
        return len({match.group(0) for match in BUSINESS_SIGNING_FIELD_RE.finditer(text or "")})

    @staticmethod
    def _business_field_category_count(text: str) -> int:
        compact = re.sub(r"\s+", "", text or "")
        return sum(1 for _, pattern in BUSINESS_FIELD_PATTERNS if pattern.search(compact))

    @staticmethod
    def _should_exclude_from_clause_diff(score: float, reasons: list[str]) -> bool:
        if score >= 0.7:
            return True
        if "seal_signature_date_cluster" in reasons and "bottom_signing_position" in reasons:
            return True
        if "terminal_business_signing_fields" in reasons:
            return True
        return "signing_page_context" in reasons and "signing_context_business_fields" in reasons

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
        if SIGNING_CONTEXT_RE.search(text):
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
        centers = [(block.bbox.x0 + block.bbox.x1) / 2 for block in blocks]
        return bool(centers) and min(centers) < page.width * 0.35 and max(centers) > page.width * 0.6

    def _padded_union(self, bboxes: list[BBox], page: Page) -> BBox:
        x0 = max(0.0, min(bbox.x0 for bbox in bboxes) - self.side_padding)
        y0 = max(0.0, min(bbox.y0 for bbox in bboxes) - self.top_padding)
        x1 = min(page.width, max(bbox.x1 for bbox in bboxes) + self.side_padding)
        y1 = min(page.height, max(bbox.y1 for bbox in bboxes) + self.bottom_padding)
        return BBox(x0=x0, y0=y0, x1=x1, y1=y1)

    @staticmethod
    def _raw_union(bboxes: list[BBox]) -> BBox:
        return BBox(
            x0=min(bbox.x0 for bbox in bboxes),
            y0=min(bbox.y0 for bbox in bboxes),
            x1=max(bbox.x1 for bbox in bboxes),
            y1=max(bbox.y1 for bbox in bboxes),
        )

    def _is_page_footer(self, block: TextBlock, page: Page) -> bool:
        text = self._compact(block.text)
        bbox = self._effective_bbox(block)
        return bbox.y0 >= page.height * 0.82 and PAGE_FOOTER_RE.match(text) is not None

    @staticmethod
    def _horizontally_related(anchor: BBox, candidate: BBox, page: Page) -> bool:
        margin = max(60.0, page.width * 0.08)
        return candidate.x1 >= anchor.x0 - margin and candidate.x0 <= anchor.x1 + margin

    @staticmethod
    def _horizontally_overlaps(anchor: BBox, candidate: BBox) -> bool:
        return candidate.x1 >= anchor.x0 and candidate.x0 <= anchor.x1

    @staticmethod
    def _effective_bbox(block: TextBlock) -> BBox:
        return block.layout_bbox or block.bbox

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"\s+", "", text or "")
