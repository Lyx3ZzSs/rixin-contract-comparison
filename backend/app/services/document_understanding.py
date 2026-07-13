from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.config_models import DocumentUnderstandingSettings
from app.models import Document, Page, ParseWarningDetail, TextBlock
from app.services.cover.patterns import parse_labeled_line
from app.services.table_compare.constants import TABLE_HEADERS
from app.services.table_compare.utils import strip_html


@dataclass
class SemanticDecision:
    target_type: str
    target_id: str
    role: str
    confidence: float
    reason: str
    source: str = "rule"
    enter_clause_compare: bool | None = None


@dataclass
class DocumentUnderstandingResult:
    side: str
    page_ir: list[dict[str, Any]] = field(default_factory=list)
    semantic_decisions: list[SemanticDecision] = field(default_factory=list)
    validation_decisions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[ParseWarningDetail] = field(default_factory=list)

    def to_debug_payload(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "page_ir": self.page_ir,
            "semantic_decisions": [decision.__dict__ for decision in self.semantic_decisions],
            "validation_decisions": self.validation_decisions,
            "warnings": [warning.model_dump(mode="json") for warning in self.warnings],
        }


@dataclass
class PairDocumentUnderstandingResult:
    original: DocumentUnderstandingResult
    compare: DocumentUnderstandingResult

    def to_debug_payload(self) -> dict[str, Any]:
        return {
            "original": self.original.to_debug_payload(),
            "compare": self.compare.to_debug_payload(),
        }


class DocumentUnderstandingService:
    """Semantic cleanup after PP-Structure/PPOCR hybrid extraction.

    The service applies deterministic rules to classify pages and blocks before
    clause splitting.
    """

    quote_pattern = re.compile(r"(?:报价明细|报价单|报价表|报价清单|分项报价表|报价汇总表)")
    safety_pattern = re.compile(r"(?:安全协议|安全管理协议|安全生产协议|安全责任协议)")
    appendix_pattern = re.compile(r"^(?:附件|附录|附表)\s*[一二三四五六七八九十0-9]*[:：、.．]?\s*")
    page_number_pattern = re.compile(r"^(?:第?\s*\d+\s*页?|共\s*\d+\s*页\s*第\s*\d+\s*页)$")
    toc_line_pattern = re.compile(
        r"^\s*(?:第?[一二三四五六七八九十百千万0-9]+[章节条]|[一二三四五六七八九十0-9]+[、.．]|\d+(?:\.\d+){0,3})"
        r".{0,80}(?:\.{2,}|…{2,}|·{2,}|[-—_]{2,})\s*\d+\s*$"
    )
    simple_toc_line_pattern = re.compile(
        r"^\s*(?:第?[一二三四五六七八九十百千万0-9]+[章节条]|[一二三四五六七八九十0-9]+[、.．]|\d+(?:\.\d+){0,3})"
        r".{2,60}\s+\d+\s*$"
    )
    table_note_pattern = re.compile(r"^(?:单位|币种|金额单位|计量单位|含税|不含税)[:：]")
    table_block_types = {"table", "table_cell"}
    margin_block_types = {"header", "footer", "page_header", "page_footer", "footnote", "vision_footnote"}
    non_text_block_types = {"image", "figure", "chart", "formula", "seal", "abandon"}

    def __init__(
        self,
        settings: DocumentUnderstandingSettings,
    ) -> None:
        self.settings = settings

    def understand_pair(self, original: Document, compare: Document) -> PairDocumentUnderstandingResult:
        return PairDocumentUnderstandingResult(
            original=self.understand(original, "original"),
            compare=self.understand(compare, "compare"),
        )

    def understand(self, document: Document, side: str) -> DocumentUnderstandingResult:
        result = DocumentUnderstandingResult(side=side)
        if not self.settings.enabled:
            return result
        for page in document.pages:
            page_ir = self._page_ir(page)
            result.page_ir.append(page_ir)
            self._apply_rule_page(page, page_ir, result)
        return result

    def _page_ir(self, page: Page) -> dict[str, Any]:
        return {
            "page_no": page.page_no,
            "page_size": [page.width, page.height],
            "blocks": [
                {
                    "block_id": block.block_id,
                    "text": block.text,
                    "normalized_text": self._compact(block.text),
                    "block_type": block.block_type,
                    "flow_role": block.flow_role,
                    "block_role": block.block_role,
                    "bbox": [block.bbox.x0, block.bbox.y0, block.bbox.x1, block.bbox.y1],
                    "confidence": block.confidence,
                    "layout_order": block.layout_order,
                    "reading_order": block.reading_order,
                    "source": block.source,
                    "has_raw_html": bool(block.raw_html),
                    "layout_match_status": block.layout_match_status,
                    "layout_match_score": block.layout_match_score,
                }
                for block in sorted(page.blocks, key=lambda item: (item.reading_order is None, item.reading_order or 0, item.bbox.y0))
            ],
        }

    def _apply_rule_page(self, page: Page, page_ir: dict[str, Any], result: DocumentUnderstandingResult) -> None:
        blocks = page.blocks
        compact_texts = [self._compact(block.text) for block in blocks if self._compact(block.text)]
        full_text = "\n".join(compact_texts)
        table_area_ratio = self._block_area_ratio(page, [b for b in blocks if self._is_table_block(b)])
        toc_lines = sum(1 for text in compact_texts if self._is_toc_line(text))
        role = "main_body"
        confidence = 0.62
        reasons: list[str] = ["default_main_body"]

        if self._looks_like_toc_page(compact_texts, toc_lines):
            role = "toc"
            confidence = 0.95
            reasons = ["toc_title_or_dense_toc_lines"]
        elif page.page_no == 1 and any(self._is_cover_metadata_text(text) for text in compact_texts[:8]):
            role = "cover"
            confidence = 0.86
            reasons = ["first_page_cover_metadata"]
        elif self.quote_pattern.search(full_text[:500]):
            role = "quote"
            confidence = 0.88
            reasons = ["quote_title_terms"]
        elif self.safety_pattern.search(full_text[:500]):
            role = "safety_agreement"
            confidence = 0.86
            reasons = ["safety_agreement_terms"]
        elif any(self.appendix_pattern.match(text) for text in compact_texts[:5]):
            role = "appendix"
            confidence = 0.84
            reasons = ["appendix_title_terms"]
        elif table_area_ratio >= 0.28:
            role = "table_heavy"
            confidence = 0.87
            reasons = [f"table_area_ratio={table_area_ratio:.3f}"]

        self._apply_page_decision(page, role, confidence, ";".join(reasons), "rule", result)
        for block in blocks:
            self._apply_rule_block(page, block, role, result)

    def _apply_rule_block(
        self,
        page: Page,
        block: TextBlock,
        page_role: str,
        result: DocumentUnderstandingResult,
    ) -> None:
        text = self._compact(block.text)
        block_type = (block.block_type or "").lower()
        flow_role = (block.flow_role or "").lower()
        role = ""
        enter: bool | None = None
        confidence = 0.75
        reason = "rule_default"

        if flow_role == "margin" or block_type in self.margin_block_types:
            role = "footer" if page.height and block.bbox.y0 >= page.height * 0.5 else "header"
            enter = False
            confidence = 0.92
            reason = "layout_margin_or_header_footer_type"
        elif flow_role in {"noise", "non_text"} or block_type in self.non_text_block_types:
            role = "seal" if block_type == "seal" else "noise"
            enter = False
            confidence = 0.9
            reason = "layout_noise_or_non_text"
        elif self._is_table_block(block):
            role = "table_body"
            enter = False
            confidence = 0.93
            reason = "layout_table_block"
        elif flow_role == "caption" or self.table_note_pattern.match(text) or self._looks_like_table_context(block.text):
            role = "table_note" if self.table_note_pattern.match(text) else "table_caption"
            enter = False
            confidence = 0.83
            reason = "table_caption_or_note_terms"
        elif page_role == "toc" or self._is_toc_line(text):
            role = "toc_line" if text != "目录" else "toc_title"
            enter = False
            confidence = 0.93 if page_role == "toc" else 0.86
            reason = "toc_page_or_toc_line_pattern"
        elif page_role == "cover" and self._is_cover_metadata_text(text):
            role = "cover_metadata"
            enter = False
            confidence = 0.86
            reason = "cover_metadata_label"
        elif page_role in {"appendix", "quote", "safety_agreement"}:
            role = page_role
            enter = True
            confidence = 0.8
            reason = f"page_role_{page_role}"
        elif text:
            role = "main_clause"
            enter = True
            confidence = 0.72
            reason = "text_body_candidate"

        if role:
            self._apply_block_decision(block, role, confidence, reason, "rule", enter, result)

    def _apply_page_decision(
        self,
        page: Page,
        role: str,
        confidence: float,
        reason: str,
        source: str,
        result: DocumentUnderstandingResult,
    ) -> None:
        if confidence < page.semantic_confidence:
            return
        page.semantic_role = role
        page.semantic_confidence = confidence
        page.semantic_reasons = list(dict.fromkeys([*page.semantic_reasons, reason]))
        result.semantic_decisions.append(SemanticDecision(
            target_type="page",
            target_id=str(page.page_no),
            role=role,
            confidence=confidence,
            reason=reason,
            source=source,
        ))

    def _apply_block_decision(
        self,
        block: TextBlock,
        role: str,
        confidence: float,
        reason: str,
        source: str,
        enter_clause_compare: bool | None,
        result: DocumentUnderstandingResult,
    ) -> None:
        if not role or confidence < block.semantic_confidence:
            return
        block.semantic_role = role
        block.semantic_confidence = confidence
        block.semantic_reasons = list(dict.fromkeys([*block.semantic_reasons, reason]))
        if enter_clause_compare is not None:
            block.enter_clause_compare = enter_clause_compare
        if role and not block.block_role:
            block.block_role = role
        result.semantic_decisions.append(SemanticDecision(
            target_type="block",
            target_id=block.block_id,
            role=role,
            confidence=confidence,
            reason=reason,
            source=source,
            enter_clause_compare=enter_clause_compare,
        ))

    def _looks_like_toc_page(self, compact_texts: list[str], toc_lines: int) -> bool:
        if any(text == "目录" for text in compact_texts[:5]):
            return True
        meaningful = [text for text in compact_texts if len(text) >= 4]
        return bool(toc_lines >= 5 and toc_lines >= max(2, len(meaningful) // 3))

    def _is_toc_line(self, text: str) -> bool:
        if not text:
            return False
        if text == "目录":
            return True
        if self.page_number_pattern.fullmatch(text):
            return False
        return bool(self.toc_line_pattern.match(text) or self.simple_toc_line_pattern.match(text))

    def _is_table_block(self, block: TextBlock) -> bool:
        block_type = (block.block_type or "").lower()
        flow_role = (block.flow_role or "").lower()
        return bool(block_type in self.table_block_types or flow_role == "table" or block.raw_html)

    def _looks_like_table_context(self, text: str) -> bool:
        tokens = {
            token
            for token in re.split(r"[\s,，、;；:：|/\\()\[\]（）【】<>《》]+", strip_html(text or ""))
            if 1 < len(token) <= 16
        }
        return len(tokens & (TABLE_HEADERS | {"数量", "单价", "总价", "金额", "税率", "合计", "备注"})) >= 2

    def _block_area_ratio(self, page: Page, blocks: list[TextBlock]) -> float:
        page_area = max(1.0, page.width * page.height)
        area = sum(max(0.0, block.bbox.x1 - block.bbox.x0) * max(0.0, block.bbox.y1 - block.bbox.y0) for block in blocks)
        return min(1.0, area / page_area)

    def _main_clause_density(self, compact_texts: list[str]) -> float:
        if not compact_texts:
            return 0.0
        clause_like = sum(1 for text in compact_texts if re.match(r"^(?:第[一二三四五六七八九十百千万0-9]+[章节条]|[一二三四五六七八九十0-9]+[、.．]|\d+(?:\.\d+)+)", text))
        return clause_like / len(compact_texts)

    def _is_cover_metadata_text(self, text: str) -> bool:
        return any(parse_labeled_line(line.strip()) for line in text.splitlines()) or bool(
            re.search(r"(?:合同编号|项目名称|甲方|乙方|采购方|供应商|签订日期)[:：]", text)
        )

    def _compact(self, text: str) -> str:
        return re.sub(r"\s+", "", text or "")
