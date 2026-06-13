from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config_models import DocumentUnderstandingSettings
from app.models import Document, Page, ParseWarningDetail, TextBlock
from app.services.cover.patterns import parse_labeled_line
from app.services.table_compare.constants import TABLE_HEADERS
from app.services.table_compare.utils import strip_html

logger = logging.getLogger(__name__)


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
class OcrCorrectionSuggestion:
    block_id: str
    raw: str
    corrected: str
    confidence: float
    reason: str
    risk: str = "medium"
    source: str = "llm"


@dataclass
class MergeSuggestion:
    block_ids: list[str]
    action: str
    confidence: float
    reason: str
    source: str = "llm"


@dataclass
class DocumentUnderstandingResult:
    side: str
    page_ir: list[dict[str, Any]] = field(default_factory=list)
    semantic_decisions: list[SemanticDecision] = field(default_factory=list)
    llm_requests: list[dict[str, Any]] = field(default_factory=list)
    llm_results: list[dict[str, Any]] = field(default_factory=list)
    validation_decisions: list[dict[str, Any]] = field(default_factory=list)
    ocr_corrections: list[OcrCorrectionSuggestion] = field(default_factory=list)
    merge_suggestions: list[MergeSuggestion] = field(default_factory=list)
    warnings: list[ParseWarningDetail] = field(default_factory=list)

    def to_debug_payload(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "page_ir": self.page_ir,
            "semantic_decisions": [decision.__dict__ for decision in self.semantic_decisions],
            "llm_requests": self.llm_requests,
            "llm_results": self.llm_results,
            "validation_decisions": self.validation_decisions,
            "ocr_corrections": [suggestion.__dict__ for suggestion in self.ocr_corrections],
            "merge_suggestions": [suggestion.__dict__ for suggestion in self.merge_suggestions],
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

    The service applies deterministic rules first. LLM calls are optional and
    only used for low-confidence pages; LLM output is treated as suggestions
    and must pass schema/id/confidence validation before it is written back.
    """

    signature_pattern = re.compile(r"(?:签字页|签署页|此页无正文|盖章|法定代表人|授权代表|委托代理人)")
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
    critical_value_pattern = re.compile(
        r"(?:\d+(?:\.\d+)?%?|[一二三四五六七八九十百千万]+)(?:元|万元|年|月|日|天|个|项|套|次|%)|"
        r"(?:合同编号|统一社会信用代码|开户行|账号|税号|甲方|乙方)"
    )

    table_block_types = {"table", "table_cell"}
    margin_block_types = {"header", "footer", "page_header", "page_footer", "footnote", "vision_footnote"}
    non_text_block_types = {"image", "figure", "chart", "formula", "seal", "abandon"}

    def __init__(
        self,
        settings: DocumentUnderstandingSettings,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or httpx.Client(timeout=settings.llm_timeout_seconds)

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
            if self._should_call_llm(page) and self.settings.llm_enabled:
                self._apply_llm_page(page, page_ir, result)
        if self.settings.llm_enabled and self.settings.enable_cross_page_merge:
            self._apply_cross_page_windows(document, result)
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
        signature_hits = sum(1 for text in compact_texts if self.signature_pattern.search(text) and len(text) <= 160)
        role = "main_body"
        confidence = 0.62
        reasons: list[str] = ["default_main_body"]

        if self._looks_like_toc_page(compact_texts, toc_lines):
            role = "toc"
            confidence = 0.95
            reasons = ["toc_title_or_dense_toc_lines"]
        elif signature_hits >= 1 and self._main_clause_density(compact_texts) < 0.25:
            role = "signature"
            confidence = 0.9 if signature_hits >= 2 else 0.82
            reasons = ["signature_terms_low_clause_density"]
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
        elif page_role == "signature" or (self.signature_pattern.search(text) and len(text) <= 160):
            role = "signature_field"
            enter = False
            confidence = 0.88
            reason = "signature_terms"
        elif page_role == "cover" and self._is_cover_metadata_text(text):
            role = "cover_metadata"
            enter = False
            confidence = 0.86
            reason = "cover_metadata_label"
        elif page_role in {"appendix", "quote", "safety_agreement"}:
            role = page_role
            enter = page_role == "safety_agreement"
            confidence = 0.8
            reason = f"page_role_{page_role}"
        elif text:
            role = "main_clause"
            enter = True
            confidence = 0.72
            reason = "text_body_candidate"

        if role:
            self._apply_block_decision(block, role, confidence, reason, "rule", enter, result)

    def _apply_llm_page(self, page: Page, page_ir: dict[str, Any], result: DocumentUnderstandingResult) -> None:
        if not self.settings.llm_base_url or not self.settings.llm_model:
            result.validation_decisions.append({
                "page_no": page.page_no,
                "action": "skip_llm",
                "reason": "missing_llm_base_url_or_model",
            })
            return
        payload = self._llm_payload(page_ir)
        result.llm_requests.append({"page_no": page.page_no, "payload": payload})
        try:
            raw = self._post_llm(payload)
            parsed = self._parse_llm_json(raw)
        except Exception as exc:
            logger.debug("Document understanding LLM failed", exc_info=True)
            warning = ParseWarningDetail(
                code="DOCUMENT_UNDERSTANDING_LLM_FAILED",
                message=f"第 {page.page_no} 页 LLM 语义清洗失败，已使用规则结果: {exc}",
                page_no=page.page_no,
                source="document_understanding",
            )
            result.warnings.append(warning)
            return
        result.llm_results.append({"page_no": page.page_no, "result": parsed})
        self._validate_and_apply_llm(page, parsed, result)

    def _llm_payload(self, page_ir: dict[str, Any]) -> dict[str, Any]:
        blocks = []
        for block in page_ir["blocks"]:
            text = str(block.get("text") or "")
            blocks.append({
                **block,
                "text": text[:1200],
            })
        return {
            "task": (
                "Classify contract document page/block semantic roles. Return strict JSON only. "
                "Do not rewrite contract text. OCR corrections must be suggestions."
            ),
            "allowed_page_roles": [
                "cover", "toc", "main_body", "signature", "appendix", "quote",
                "table_heavy", "safety_agreement", "unknown",
            ],
            "allowed_block_roles": [
                "main_clause", "toc_title", "toc_line", "header", "footer",
                "table_body", "table_caption", "table_note", "signature_field",
                "cover_metadata", "appendix", "quote", "safety_agreement", "noise",
            ],
            "schema": {
                "page_role": "string",
                "confidence": "number 0..1",
                "reason": "string",
                "block_roles": [
                    {
                        "block_id": "string",
                        "role": "string",
                        "confidence": "number 0..1",
                        "enter_clause_compare": "boolean",
                        "reason": "string",
                    }
                ],
                "ocr_correction_suggestions": [
                    {
                        "block_id": "string",
                        "raw": "string",
                        "corrected": "string",
                        "confidence": "number 0..1",
                        "risk": "low|medium|high",
                        "reason": "string",
                    }
                ],
                "merge_suggestions": [
                    {
                        "block_ids": ["string"],
                        "action": "merge|split|keep",
                        "confidence": "number 0..1",
                        "reason": "string",
                    }
                ],
            },
            "page": {
                "page_no": page_ir["page_no"],
                "page_size": page_ir["page_size"],
                "blocks": blocks,
            },
        }

    def _post_llm(self, payload: dict[str, Any]) -> str:
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        body = {
            "model": self.settings.llm_model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a contract document understanding engine. Return valid JSON only.",
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            "temperature": 0,
        }
        last_error: Exception | None = None
        for _ in range(self.settings.llm_max_retries + 1):
            try:
                response = self.client.post(
                    self.settings.llm_base_url.rstrip("/") + "/chat/completions",
                    headers=headers,
                    json=body,
                    timeout=self.settings.llm_timeout_seconds,
                )
                response.raise_for_status()
                data = response.json()
                return str(data["choices"][0]["message"]["content"])
            except Exception as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("LLM request failed without an exception")

    def _parse_llm_json(self, raw: str) -> dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return json.loads(text)

    def _validate_and_apply_llm(self, page: Page, parsed: dict[str, Any], result: DocumentUnderstandingResult) -> None:
        block_by_id = {block.block_id: block for block in page.blocks}
        page_confidence = self._confidence(parsed.get("confidence"))
        page_role = str(parsed.get("page_role") or "unknown")
        if page_confidence >= self.settings.llm_confidence_accept and page_role in {
            "cover", "toc", "main_body", "signature", "appendix", "quote", "table_heavy", "safety_agreement", "unknown",
        }:
            self._apply_page_decision(
                page,
                page_role,
                page_confidence,
                str(parsed.get("reason") or "llm_page_role"),
                "llm",
                result,
            )
        else:
            result.validation_decisions.append({
                "page_no": page.page_no,
                "action": "reject_page_role",
                "role": page_role,
                "confidence": page_confidence,
                "reason": "low_confidence_or_unknown_role",
            })

        for item in parsed.get("block_roles") or []:
            if not isinstance(item, dict):
                continue
            block_id = str(item.get("block_id") or "")
            block = block_by_id.get(block_id)
            confidence = self._confidence(item.get("confidence"))
            if block is None or confidence < self.settings.llm_confidence_accept:
                result.validation_decisions.append({
                    "page_no": page.page_no,
                    "block_id": block_id,
                    "action": "reject_block_role",
                    "confidence": confidence,
                    "reason": "missing_block_or_low_confidence",
                })
                continue
            self._apply_block_decision(
                block,
                str(item.get("role") or ""),
                confidence,
                str(item.get("reason") or "llm_block_role"),
                "llm",
                item.get("enter_clause_compare") if isinstance(item.get("enter_clause_compare"), bool) else None,
                result,
            )

        if self.settings.enable_ocr_correction:
            self._validate_ocr_corrections(page, parsed, result)
        if self.settings.enable_cross_page_merge:
            self._validate_merge_suggestions(page, parsed, result)

    def _validate_ocr_corrections(self, page: Page, parsed: dict[str, Any], result: DocumentUnderstandingResult) -> None:
        block_by_id = {block.block_id: block for block in page.blocks}
        for item in parsed.get("ocr_correction_suggestions") or []:
            if not isinstance(item, dict):
                continue
            block = block_by_id.get(str(item.get("block_id") or ""))
            confidence = self._confidence(item.get("confidence"))
            raw = str(item.get("raw") or "")
            corrected = str(item.get("corrected") or "")
            risk = str(item.get("risk") or "medium")
            if block is None or confidence < self.settings.llm_confidence_accept or not raw or not corrected:
                continue
            if risk == "high" or self.critical_value_pattern.search(raw) or self.critical_value_pattern.search(corrected):
                result.validation_decisions.append({
                    "page_no": page.page_no,
                    "block_id": block.block_id,
                    "action": "keep_high_risk_ocr_suggestion_only",
                    "raw": raw,
                    "corrected": corrected,
                })
            suggestion = OcrCorrectionSuggestion(
                block_id=block.block_id,
                raw=raw,
                corrected=corrected,
                confidence=confidence,
                reason=str(item.get("reason") or "llm_ocr_correction"),
                risk=risk,
            )
            block.ocr_correction_suggestions.append(suggestion.__dict__)
            result.ocr_corrections.append(suggestion)

    def _validate_merge_suggestions(self, page: Page, parsed: dict[str, Any], result: DocumentUnderstandingResult) -> None:
        block_ids = {block.block_id for block in page.blocks}
        for item in parsed.get("merge_suggestions") or []:
            if not isinstance(item, dict):
                continue
            ids = [str(value) for value in item.get("block_ids") or [] if str(value)]
            confidence = self._confidence(item.get("confidence"))
            if confidence < self.settings.llm_confidence_accept or not ids or any(block_id not in block_ids for block_id in ids):
                result.validation_decisions.append({
                    "page_no": page.page_no,
                    "block_ids": ids,
                    "action": "reject_merge_suggestion",
                    "confidence": confidence,
                    "reason": "missing_block_or_low_confidence",
                })
                continue
            result.merge_suggestions.append(MergeSuggestion(
                block_ids=ids,
                action=str(item.get("action") or "merge"),
                confidence=confidence,
                reason=str(item.get("reason") or "llm_merge_suggestion"),
            ))

    def _apply_cross_page_windows(self, document: Document, result: DocumentUnderstandingResult) -> None:
        if not self.settings.llm_base_url or not self.settings.llm_model:
            return
        for left, right in zip(document.pages, document.pages[1:], strict=False):
            left_blocks = self._boundary_blocks(left, tail=True)
            right_blocks = self._boundary_blocks(right, tail=False)
            if not left_blocks or not right_blocks:
                continue
            payload = self._llm_cross_page_payload(left, right, left_blocks, right_blocks)
            result.llm_requests.append({
                "page_no": [left.page_no, right.page_no],
                "kind": "cross_page_merge",
                "payload": payload,
            })
            try:
                parsed = self._parse_llm_json(self._post_llm(payload))
            except Exception as exc:
                logger.debug("Cross-page document understanding LLM failed", exc_info=True)
                result.warnings.append(ParseWarningDetail(
                    code="DOCUMENT_UNDERSTANDING_CROSS_PAGE_LLM_FAILED",
                    message=f"第 {left.page_no}-{right.page_no} 页跨页条款判断失败，已跳过: {exc}",
                    page_no=left.page_no,
                    source="document_understanding",
                ))
                continue
            result.llm_results.append({
                "page_no": [left.page_no, right.page_no],
                "kind": "cross_page_merge",
                "result": parsed,
            })
            self._validate_cross_page_merge_suggestions(left, right, parsed, result)

    def _boundary_blocks(self, page: Page, *, tail: bool) -> list[TextBlock]:
        candidates = [
            block
            for block in sorted(page.blocks, key=lambda item: (item.reading_order is None, item.reading_order or 0, item.bbox.y0))
            if block.enter_clause_compare is not False
            and (block.semantic_role in {"", "main_clause"} or page.semantic_role in {"main_body", "unknown"})
            and self._compact(block.text)
        ]
        return candidates[-3:] if tail else candidates[:3]

    def _llm_cross_page_payload(
        self,
        left: Page,
        right: Page,
        left_blocks: list[TextBlock],
        right_blocks: list[TextBlock],
    ) -> dict[str, Any]:
        return {
            "task": (
                "Judge whether the end of the first page and the beginning of the second page "
                "belong to the same contract clause. Return strict JSON only."
            ),
            "schema": {
                "merge_suggestions": [
                    {
                        "block_ids": ["string"],
                        "action": "merge|keep",
                        "confidence": "number 0..1",
                        "reason": "string",
                    }
                ]
            },
            "pages": [
                {
                    "page_no": left.page_no,
                    "role": left.semantic_role,
                    "blocks": [self._llm_block_payload(block) for block in left_blocks],
                },
                {
                    "page_no": right.page_no,
                    "role": right.semantic_role,
                    "blocks": [self._llm_block_payload(block) for block in right_blocks],
                },
            ],
        }

    def _llm_block_payload(self, block: TextBlock) -> dict[str, Any]:
        return {
            "block_id": block.block_id,
            "text": (block.text or "")[:1200],
            "semantic_role": block.semantic_role,
            "block_type": block.block_type,
            "flow_role": block.flow_role,
            "bbox": [block.bbox.x0, block.bbox.y0, block.bbox.x1, block.bbox.y1],
            "confidence": block.confidence,
        }

    def _validate_cross_page_merge_suggestions(
        self,
        left: Page,
        right: Page,
        parsed: dict[str, Any],
        result: DocumentUnderstandingResult,
    ) -> None:
        block_ids = {block.block_id for block in [*left.blocks, *right.blocks]}
        for item in parsed.get("merge_suggestions") or []:
            if not isinstance(item, dict):
                continue
            ids = [str(value) for value in item.get("block_ids") or [] if str(value)]
            confidence = self._confidence(item.get("confidence"))
            if confidence < self.settings.llm_confidence_accept or len(ids) < 2 or any(block_id not in block_ids for block_id in ids):
                result.validation_decisions.append({
                    "page_no": [left.page_no, right.page_no],
                    "block_ids": ids,
                    "action": "reject_cross_page_merge_suggestion",
                    "confidence": confidence,
                    "reason": "missing_block_or_low_confidence",
                })
                continue
            result.merge_suggestions.append(MergeSuggestion(
                block_ids=ids,
                action=str(item.get("action") or "merge"),
                confidence=confidence,
                reason=str(item.get("reason") or "llm_cross_page_merge_suggestion"),
            ))

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
        page.semantic_reasons = [reason]
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
        block.semantic_reasons = [reason]
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

    def _should_call_llm(self, page: Page) -> bool:
        if not self.settings.llm_enabled:
            return False
        if page.semantic_confidence < self.settings.rule_confidence_accept:
            return True
        return any(
            block.layout_match_status in {"ambiguous", "meaningful_unmatched"}
            or (block.confidence is not None and block.confidence < 0.55)
            for block in page.blocks
        )

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

    def _confidence(self, value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 0.0
