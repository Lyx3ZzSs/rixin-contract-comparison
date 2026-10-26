from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.services.page_number_evidence import PAGE_NUMBER_PATTERN
from app.services.document_understanding import DocumentUnderstandingService
from app.models import BBox, Document, Page, TextBlock
from app.services.cover.patterns import parse_labeled_line
from app.services.table_compare.constants import TABLE_HEADERS
from app.services.table_compare.utils import strip_html
from app.services.pipeline_metrics import PerformanceRecorder


@dataclass
class DocumentPreparationDecision:
    side: str
    page_no: int
    block_id: str
    reason: str
    text_preview: str
    source_evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DocumentPreparationResult:
    decisions: list[DocumentPreparationDecision] = field(default_factory=list)
    performance: PerformanceRecorder = field(default_factory=PerformanceRecorder, repr=False, compare=False)

    def to_debug_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "side": decision.side,
                "page_no": decision.page_no,
                "block_id": decision.block_id,
                "reason": decision.reason,
                "text_preview": decision.text_preview,
                **({"source_evidence": decision.source_evidence} if decision.source_evidence else {}),
            }
            for decision in self.decisions
        ]


class DocumentPreparer:
    def __init__(self, *, image_analysis_max_inflight: int = 2):
        self.image_analysis_max_inflight = image_analysis_max_inflight

    page_number_pattern = PAGE_NUMBER_PATTERN
    clause_heading_pattern = re.compile(
        r"^\s*(?:第[一二三四五六七八九十百千万0-9]+[章节条]|[一二三四五六七八九十百千万0-9]+[、.．])"
    )
    table_block_types = {"table", "table_cell"}
    table_note_label_pattern = re.compile(r"^(?:单位|币种|金额单位|计量单位|含税|不含税)[:：]")
    quote_metadata_label_pattern = re.compile(
        r"^(?:项目名称|项目名|项图名称|报价单位|报价日期|联系方式|项目经理|邮箱|网址|地址|"
        r"24小时服务热线|服务热线|联系人|电话)[:：]"
    )
    quote_title_pattern = re.compile(r"(?:报价明细|报价单|报价表|报价清单)$")
    appendix_title_pattern = re.compile(r"^(?:附件|附录|附表)\s*[一二三四五六七八九十0-9]*[:：、.．]?\s*")
    safety_title_pattern = re.compile(r"(?:安全协议|安全管理协议|安全生产协议|安全责任协议)")
    quote_page_pattern = re.compile(r"(?:报价明细|报价单|报价表|报价清单|分项报价表|报价汇总表)")
    generic_table_terms = TABLE_HEADERS | {
        "名称",
        "项目",
        "内容",
        "规格",
        "型号",
        "品牌",
        "单位",
        "数量",
        "单价",
        "总价",
        "报价",
        "金额",
        "合计",
        "税率",
        "期限",
        "时间",
        "日期",
        "编号",
        "联系人",
        "电话",
        "地址",
        "服务内容",
        "服务期限",
        "配置",
        "参数",
        "指标",
        "标准",
        "备注",
    }
    clause_prefix_pattern = re.compile(
        r"^\s*(?:第[一二三四五六七八九十百千万0-9]+[章节条]|[一二三四五六七八九十百千万0-9]+[、.．]|[（(][一二三四五六七八九十百千万0-9]+[)）])\s*"
    )
    formal_clause_heading_pattern = re.compile(
        r"^\s*(?:第[一二三四五六七八九十百千万0-9]+[章节条]|[一二三四五六七八九十百千万]+、)"
    )
    main_contract_heading_pattern = re.compile(r"^\s*第[一二三四五六七八九十百千万0-9]+[章节条]")
    decimal_clause_heading_pattern = re.compile(r"^\s*(\d+(?:\.\d+){1,3})[、.．]?\s*(.+)$")
    integer_clause_heading_pattern = re.compile(r"^\s*\d+[、.．]\s*(.+)$")
    date_like_pattern = re.compile(r"^\s*(?:19|20)\d{2}[./年-]\d{1,2}(?:[./月-]\d{1,2}日?)?\s*$")
    parenthesized_clause_heading_pattern = re.compile(
        r"^\s*[（(][一二三四五六七八九十百千万0-9]+[）)]\s*(?P<body>.+)$"
    )
    strong_clause_business_pattern = re.compile(
        r"事故|处罚|违约|赔偿|责任|义务|付款|支付|金额|期限|验收|交付|保密|知识产权|"
        r"终止|解除|不可抗力|争议|仲裁|诉讼|应当|应|不得|负责|承担|提供"
    )
    revision_artifact_pattern = re.compile(
        r"^(?P<action>删除|插入|设置格式)\[(?P<author>[^\[\]\r\n]{1,64})\]\s*[:：]\s*(?P<payload>[^\r\n]*)$"
    )
    revision_format_payload_pattern = re.compile(
        r"^(?:突出显示|字体|字号|字形|颜色|下划线|加粗|倾斜|删除线|行距|居中|缩进|对齐|段落)(?:\s*[:：,，].*|\s*\d+.*)?$"
    )

    def prepare_pair(self, original: Document, compare: Document) -> DocumentPreparationResult:
        from app.services.document_role_repair import reconcile_contact_placeholders, reconcile_body_roles, reconcile_local_source_disagreement
        result = DocumentPreparationResult()
        timed = result.performance.call
        from app.services.address_evidence import reconcile_addresses
        for side, block in timed("address_evidence", reconcile_addresses, original, compare):
            result.decisions.append(DocumentPreparationDecision(side=side, page_no=block.page_no,
                block_id=block.block_id, reason=("address_line_readings_disagree"
                    if "address_line_readings_disagree" in block.semantic_reasons
                    else "own_address_line_two_scale_verified"), text_preview=block.text,
                source_evidence=[e.model_dump(mode="json") for e in block.source_evidence
                                 if e.source.startswith("address_line_")]))
        from app.services.cover.party_ocr import reconcile_cover_title_roles
        for side, block in timed("cover_title_roles", reconcile_cover_title_roles, original, compare):
            result.decisions.append(DocumentPreparationDecision(side=side,page_no=block.page_no,block_id=block.block_id,
                reason="matched_cover_title_role",text_preview=block.text))
        for side, block in timed("local_source_disagreement", reconcile_local_source_disagreement, original, compare):
            result.decisions.append(DocumentPreparationDecision(side=side,page_no=block.page_no,block_id=block.block_id,
                reason="own_layout_and_peer_sources_agree",text_preview=block.text))
        from app.services.formula_variable_ocr import reconcile_formula_variables
        for side, block in timed("formula_variables", reconcile_formula_variables, original, compare):
            result.decisions.append(DocumentPreparationDecision(side=side, page_no=block.page_no,
                block_id=block.block_id, reason="own_formula_variable_two_scale_verified", text_preview=block.text))
        from app.services.payment_symbol_ocr import reconcile_payment_symbols
        for side, block in timed("payment_symbols", reconcile_payment_symbols, original, compare):
            result.decisions.append(DocumentPreparationDecision(side=side, page_no=block.page_no,
                block_id=block.block_id, reason="own_payment_symbol_two_scale_verified", text_preview=block.text))
        from app.services.numeric_range_ocr import reconcile_numeric_ranges
        for side, block in timed("numeric_ranges", reconcile_numeric_ranges, original, compare):
            result.decisions.append(DocumentPreparationDecision(side=side, page_no=block.page_no,
                block_id=block.block_id, reason="own_numeric_range_two_scale_verified", text_preview=block.text))
        from app.services.single_character_ocr import reconcile_single_characters
        for side, block in timed("single_characters", reconcile_single_characters, original, compare):
            result.decisions.append(DocumentPreparationDecision(side=side, page_no=block.page_no,
                block_id=block.block_id, reason="own_single_character_two_context_verified", text_preview=block.text,
                source_evidence=[e.model_dump(mode="json") for e in block.source_evidence
                                 if e.source in {"single_character_before", "single_character_roi_ppocr"}]))
        from app.services.financial_amount_ocr import reconcile_financial_amounts
        for side, block in timed("financial_amounts", reconcile_financial_amounts, original, compare):
            result.decisions.append(DocumentPreparationDecision(side=side, page_no=block.page_no,
                block_id=block.block_id, reason="own_financial_amount_two_context_verified", text_preview=block.text,
                source_evidence=[e.model_dump(mode="json") for e in block.source_evidence
                                 if e.source in {"financial_amount_before", "financial_amount_roi_ppocr"}]))
        from app.services.document_role_repair import reconcile_interior_document_titles
        for side, block in timed("interior_titles", reconcile_interior_document_titles, original, compare):
            result.decisions.append(DocumentPreparationDecision(side=side, page_no=block.page_no,
                block_id=block.block_id, reason="paired_interior_document_title", text_preview=block.text))
        for side, block in timed("body_roles", reconcile_body_roles, original, compare):
            result.decisions.append(DocumentPreparationDecision(
                side=side, page_no=block.page_no, block_id=block.block_id,
                reason="paired_body_geometry_role_recovery", text_preview=block.text,
            ))
        for side, block in timed("contact_placeholders", reconcile_contact_placeholders, original, compare):
            result.decisions.append(DocumentPreparationDecision(
                side=side, page_no=block.page_no, block_id=block.block_id,
                reason="own_contact_placeholder_pixels", text_preview=block.text,
                source_evidence=[e.model_dump(mode="json") for e in block.source_evidence],
            ))
        from app.services.printed_text_recall import recover_printed_signing_lines, recover_occluded_field_lines
        for side, reference, target in (("compare", original, compare), ("original", compare, original)):
            for block in timed("occluded_field_recall", recover_occluded_field_lines, reference, target):
                result.decisions.append(DocumentPreparationDecision(side=side, page_no=block.page_no,
                    block_id=block.block_id, reason="own_occluded_field_two_scale_verified", text_preview=block.text))
            for block in timed("printed_signing_recall", recover_printed_signing_lines, reference, target):
                result.decisions.append(DocumentPreparationDecision(side=side, page_no=block.page_no,
                    block_id=block.block_id, reason="own_red_suppressed_printed_line", text_preview=block.text))
        timed("original", self.prepare, original, "original", result)
        timed("compare", self.prepare, compare, "compare", result)
        from app.services.heading_recovery import reconcile_numbered_heading_roles
        for side, block in timed("numbered_heading_roles", reconcile_numbered_heading_roles, original, compare):
            result.decisions.append(DocumentPreparationDecision(
                side=side, page_no=block.page_no, block_id=block.block_id,
                reason="paired_located_numbered_heading", text_preview=block.text,
            ))
        from app.services.page_number_evidence import paired_counter_role_recoveries
        for side, block, reference in timed("paired_page_counters", paired_counter_role_recoveries, original, compare):
            block.enter_clause_compare = False
            self._mark_role(block, side, "page_footer", "paired_page_counter_geometry_verified", result)
            result.decisions[-1].source_evidence = [{
                "source": "opposite_confirmed_page_counter",
                "page_no": reference.page_no,
                "block_id": reference.block_id,
                "raw_text": reference.text,
                "bbox": reference.bbox.model_dump(mode="json"),
                "confidence": reference.confidence,
            }]
        return result

    def prepare(self, document: Document, side: str, result: DocumentPreparationResult | None = None) -> DocumentPreparationResult:
        result = result or DocumentPreparationResult()
        if not document.pages:
            return result
        timed = result.performance.call
        from app.services.seal_image_validation import reclassify_unverified_seal_images
        for item in timed("seal_image_validation", reclassify_unverified_seal_images, document):
            result.decisions.append(DocumentPreparationDecision(side=side,page_no=item["page_no"],
                block_id=item["block_id"],reason=item["reason"],text_preview="图像保留；缺少印章类别证据"))
        from app.services.diagonal_watermark import classify_diagonal_watermarks
        for item in timed("diagonal_watermarks", classify_diagonal_watermarks, document):
            result.decisions.append(DocumentPreparationDecision(side=side,page_no=item["page_no"],
                block_id=item["block_id"],reason="pale_diagonal_watermark_ownership",text_preview=item["text"]))
        from app.services.document_role_repair import normalize_signing_lines
        for block in timed("signing_lines", normalize_signing_lines, document):
            result.decisions.append(DocumentPreparationDecision(side=side,page_no=block.page_no,block_id=block.block_id,
                reason="signing_physical_line_ownership",text_preview=block.text[:160]))

        from app.services.document_role_repair import repeated_title_ids
        title_ids = timed("repeated_titles", repeated_title_ids, document)

        from app.services.document_role_repair import recover_visible_native_punctuation, recover_native_technical_tokens
        for block in [*timed("native_punctuation", recover_visible_native_punctuation, document),
                      *timed("native_technical_tokens", recover_native_technical_tokens, document)]:
            result.decisions.append(DocumentPreparationDecision(
                side=side, page_no=block.page_no, block_id=block.block_id,
                reason="visible_native_punctuation_corroborated", text_preview=block.text,
            ))

        timed("sparse_native_tables", self._recover_sparse_native_tables, document, side, result)
        from app.services.table_slash_recovery import repair_table_slashes, recover_native_continuation_rows
        for block in timed("native_continuation_rows", recover_native_continuation_rows, document):
            result.decisions.append(DocumentPreparationDecision(side=side,page_no=block.page_no,
                block_id=block.block_id,reason="visible_native_continuation_grid",text_preview=block.text))
        for block, previous, _ in timed("table_slashes", repair_table_slashes, document):
            result.decisions.append(DocumentPreparationDecision(side=side, page_no=block.page_no, block_id=block.block_id,
                reason="visible_single_stroke_slash_recovered", text_preview=f"{previous} → /"))
        from app.services.ruled_table_recovery import recover_ruled_tables
        for block in timed("ruled_tables", recover_ruled_tables, document,
                           max_inflight=self.image_analysis_max_inflight, performance=result.performance):
            result.decisions.append(DocumentPreparationDecision(
                side=side, page_no=block.page_no, block_id=block.block_id,
                reason="visible_ruled_grid_character_ownership", text_preview=block.text[:160],
            ))
        active_section_role = None
        for page in document.pages:
            timed("layout_prefixes", self._recover_layout_prefixes, page, side, result)
            from app.services.document_role_repair import repair_corroborated_ocr
            for block in timed("corroborated_ocr", repair_corroborated_ocr, page):
                result.decisions.append(DocumentPreparationDecision(
                    side=side, page_no=page.page_no, block_id=block.block_id,
                    reason="same_region_corroborated_ocr_repair", text_preview=block.text[:160],
                ))
            active_section_role = timed("page_structure", self._classify_page_structure,
                                        page, side, result, active_section_role)
            for block in page.blocks:
                if block.block_id in title_ids:
                    block.block_type = "doc_title"
                    block.enter_clause_compare = False
                    self._mark_role(block, side, "repeated_document_title", "cover_title_repeated", result)

        from app.services.header_footer_compare import HeaderFooterComparator
        footer_blocks = timed("footer_ownership", HeaderFooterComparator().owned_footer_blocks, document)
        header_blocks = timed("header_ownership", HeaderFooterComparator().owned_header_blocks, document)
        attachment_instance = ""
        for page in document.pages:
            entries = self._attachment_entry_blocks(page)
            from app.services.page_number_evidence import page_counters
            counters = page_counters(page)
            for block in page.blocks:
                if any(c.bbox.x0 <= block.bbox.x0 and c.bbox.x1 >= block.bbox.x1
                       and c.bbox.y0 <= block.bbox.y0 and c.bbox.y1 >= block.bbox.y1
                       for c in counters):
                    block.enter_clause_compare = False
                    self._mark_role(block, side, "page_footer", "page_counter_glyph_ownership", result)
                if block.block_id in entries:
                    attachment_instance = entries[block.block_id]
                    if "document_entry_boundary" not in block.semantic_reasons:
                        block.semantic_reasons.append("document_entry_boundary")
                    block.enter_clause_compare = True
                    self._mark_role(block, side, "appendix_section", "attachment_page_entry", result)
                if attachment_instance:
                    reason = "attachment_instance:" + attachment_instance
                    if reason not in block.semantic_reasons:
                        block.semantic_reasons.append(reason)
                if (page.page_no, block.block_id) in header_blocks:
                    block.enter_clause_compare = False
                    self._mark_role(block, side, "page_header", "shared_header_evidence_ownership", result)
                if (page.page_no, block.block_id) in footer_blocks:
                    block.enter_clause_compare = False
                    self._mark_role(block, side, "page_footer", "shared_footer_evidence_ownership", result)
        return result

    @staticmethod
    def _attachment_entry_blocks(page: Page) -> dict[str, str]:
        """An attachment number followed by its title establishes a local entry.

        A mid-page reference or a catalog of attachment numbers is not an entry.
        Coordinate tolerance is relative to glyph height for scanned headings.
        """
        headings = {"doc_title", "paragraph_title", "title"}
        blocks = [b for b in page.blocks if b.text.strip() and b.flow_role not in {"margin", "non_text"}
                  and b.block_type not in {"header", "footer", "number", "image", "seal"}]
        blocks.sort(key=lambda b: (b.bbox.y0, b.bbox.x0))
        result = {}
        for index, marker in enumerate(blocks[:-1]):
            text = re.sub(r"\s+", "", marker.text)
            if not re.fullmatch(r"(?:附件|附录)[一二三四五六七八九十百0-9]+[:：、.．]?", text):
                continue
            title = blocks[index+1]
            height = max(1, marker.bbox.y1-marker.bbox.y0, title.bbox.y1-title.bbox.y0)
            gap = title.bbox.y0-marker.bbox.y1
            title_text = re.sub(r"\s+", "", title.text)
            if (page.height <= 0 or marker.bbox.y0 > page.height*.25 or title.bbox.y1 > page.height*.35
                    or title.block_type not in headings or not 2 <= len(title_text) <= 50
                    or re.match(r"附件|附录|目录", title_text) or re.search(r"[。；;…]", title_text)
                    or not -height*.25 <= gap <= height*4):
                continue
            # Do not treat an item in a contents list as an attachment opening.
            if any("目录" == re.sub(r"\s+", "", b.text) for b in blocks[:index]):
                continue
            # The visible title may genuinely change; it is not an identity key.
            result[marker.block_id] = text.rstrip(":：、.．")
        return result

    @staticmethod
    def _recover_sparse_native_tables(document: Document, side: str, result: DocumentPreparationResult) -> None:
        """Recover lost OCR cells from the same PDF's visible text and ruled grid."""
        from html import escape
        from pathlib import Path
        from xml.etree import ElementTree

        import fitz
        from app.models import CharBox, TextSourceEvidence

        candidates = []
        for page in document.pages:
            for block in page.blocks:
                if not block.raw_html:
                    continue
                try:
                    root = ElementTree.fromstring(block.raw_html)
                except ElementTree.ParseError:
                    continue
                rows = [["".join(cell.itertext()).strip() for cell in row.findall("td")] for row in root.iter("tr")]
                if len(rows) >= 2 and all(len(row) == 2 for row in rows):
                    values = [value for row in rows for value in row]
                    if sum(not v for v in values) / len(values) >= 0.6:
                        candidates.append((page, block, rows))
        if not candidates or not Path(document.path).is_file():
            return
        with fitz.open(document.path) as pdf:
            for page, block, rows in candidates:
                if not 1 <= page.page_no <= len(pdf):
                    continue
                native = pdf[page.page_no - 1]
                # Hidden OCR layers are not independent evidence of visible content.
                if any(span["type"] == 3 or span["opacity"] < 0.9 for span in native.get_texttrace()):
                    continue
                region = fitz.Rect(block.bbox.x0 - 3, block.bbox.y0 - 3, block.bbox.x1 + 3, block.bbox.y1 + 3)
                tables = native.find_tables(clip=region).tables
                tables = [table for table in tables if table.row_count == len(rows) and table.col_count == 2]
                if len(tables) != 1:
                    continue
                words = native.get_text("words")
                recovered = []
                for row in tables[0].rows:
                    recovered.append([" ".join(w[4] for w in words if cell and fitz.Point((w[0]+w[2])/2, (w[1]+w[3])/2) in fitz.Rect(cell)) for cell in row.cells])
                def norm(value: str) -> str:
                    return re.sub(r"\s+", "", value)
                if any(norm(old) not in norm(new) for old_row, new_row in zip(rows, recovered) for old, new in zip(old_row, new_row) if old):
                    continue
                if any(not row[0] for row in recovered) or sum(map(len, (v for row in recovered for v in row))) < 30:
                    continue
                chars = []
                text = ""
                for raw_block in native.get_text("rawdict")["blocks"]:
                    for line in raw_block.get("lines", []):
                        for span in line["spans"]:
                            for char in span["chars"]:
                                bounds = fitz.Rect(char["bbox"])
                                if (bounds.tl + bounds.br) / 2 not in region:
                                    continue
                                chars.append(CharBox(char=char["c"], page_no=page.page_no, text_index=len(text),
                                                     bbox=BBox(x0=bounds.x0, y0=bounds.y0, x1=bounds.x1, y1=bounds.y1)))
                                text += char["c"]
                        text += "\n"
                block.source_evidence.append(TextSourceEvidence(source="pdf_native_table_recovery", raw_text=text,
                                                                  page_no=page.page_no, bbox=block.bbox, confidence=1.0))
                block.text = text.strip()
                block.char_boxes = chars
                block.raw_html = "<table>" + "".join("<tr>" + "".join(f"<td>{escape(v)}</td>" for v in row) + "</tr>" for row in recovered) + "</table>"
                result.decisions.append(DocumentPreparationDecision(side=side, page_no=page.page_no, block_id=block.block_id,
                                                                    reason="sparse_table_native_text_recovered", text_preview=block.text[:160]))

    def _recover_layout_prefixes(self, page: Page, side: str, result: DocumentPreparationResult) -> None:
        """Recover an omitted leading line only from the same OCR layout region."""
        def compact(text: str) -> str:
            return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))

        recovered: list[TextBlock] = []
        seen: set[str] = set()
        for block in page.blocks:
            if not block.layout_block_id or block.layout_block_id in seen or block.block_type != "text":
                continue
            group = [item for item in page.blocks if item.layout_block_id == block.layout_block_id]
            first = min(group, key=lambda item: (item.bbox.y0, item.bbox.x0))
            if block is not first:
                continue
            seen.add(block.layout_block_id)
            anchor = compact(first.text)
            if self._recover_inline_layout_prefix(page, first, group, side, result):
                continue
            if len(anchor) < 16:
                continue
            for source in first.source_evidence:
                if source.source != "ppstructure" or source.page_no != page.page_no or source.bbox is None:
                    continue
                raw = unicodedata.normalize("NFKC", source.raw_text)
                normalized = compact(raw)
                offset = normalized.find(anchor)
                if not 12 <= offset <= 100 or normalized.count(anchor) != 1:
                    continue
                prefix = normalized[:offset]
                if any(prefix in compact(item.text) for item in page.blocks):
                    continue
                gap = first.bbox.y0 - source.bbox.y0
                if not 8 <= gap <= 60:
                    continue
                # Never invent character coordinates from a paragraph OCR result.
                bbox = BBox(x0=source.bbox.x0, y0=source.bbox.y0,
                            x1=source.bbox.x1, y1=first.bbox.y0)
                recovered.append(TextBlock(
                    block_id=first.block_id + "_layout_prefix", page_no=page.page_no,
                    text=prefix, bbox=bbox, source="ppstructure_layout_prefix_recovery",
                    confidence=source.confidence, reading_order=(first.reading_order or 0) - 1,
                    source_evidence=[source], block_type="text", flow_role="body",
                ))
                result.decisions.append(DocumentPreparationDecision(
                    side=side, page_no=page.page_no, block_id=recovered[-1].block_id,
                    reason="same_layout_missing_prefix_recovered", text_preview=prefix,
                ))
                break
        page.blocks.extend(recovered)
        if recovered:
            page.blocks.sort(key=lambda item: (item.reading_order or 0, item.bbox.y0, item.bbox.x0))

    def _recover_inline_layout_prefix(
        self, page: Page, block: TextBlock, group: list[TextBlock], side: str,
        result: DocumentPreparationResult,
    ) -> bool:
        """Use a same-side layout reading for an omitted labelled row prefix.

        The line OCR must be its exact suffix in the same physical row. Other
        OCR blocks in that row must not already own the missing prefix.
        """
        old = block.text.strip()
        if len(old) < 6 or len(group) != 1:
            return False
        for source in block.source_evidence:
            full = source.raw_text.strip()
            if (source.source != "ppstructure" or source.page_no != page.page_no
                    or source.bbox is None or source.confidence is None or source.confidence < 0.95
                    or not re.match(r"^(?:项目名称|合同名称|工程名称)\s*[:：]", full)
                    or not full.endswith(old) or not 6 <= len(full)-len(old) <= 80):
                continue
            roi = source.bbox
            height = max(1, block.bbox.y1-block.bbox.y0)
            if (abs(roi.y0-block.bbox.y0) > height * 0.4
                    or abs(roi.y1-block.bbox.y1) > height * 0.4
                    or roi.x0 >= block.bbox.x0 - height or abs(roi.x1-block.bbox.x1) > height):
                continue
            if any(other is not block and other.text.strip()
                   and min(other.bbox.y1,roi.y1)>max(other.bbox.y0,roi.y0)
                   and min(other.bbox.x1,block.bbox.x0)>max(other.bbox.x0,roi.x0)
                   for other in page.blocks):
                continue
            shift = len(full)-len(old)
            block.text = full
            block.char_boxes = [char.model_copy(update={"text_index": char.text_index+shift})
                                for char in block.char_boxes if char.text_index is not None]
            block.bbox = roi.model_copy()
            block.block_type = "paragraph_title"
            block.source = "+".join(filter(None,[block.source,"same_side_inline_layout_prefix"]))
            result.decisions.append(DocumentPreparationDecision(
                side=side,page_no=page.page_no,block_id=block.block_id,
                reason="same_side_inline_layout_prefix",text_preview=full,
            ))
            return True
        return False

    def _classify_page_structure(
        self,
        page: Page,
        side: str,
        result: DocumentPreparationResult,
        active_section_role: str | None = None,
    ) -> str | None:
        from app.services.document_role_repair import revision_continuations
        continuations = revision_continuations(page, self._is_rendered_revision_artifact)
        body_start_y = min(
            (
                block.bbox.y0
                for block in page.blocks
                if self.clause_heading_pattern.match(block.text or "")
            ),
            default=page.height,
        )
        table_blocks = [
            block
            for block in page.blocks
            if (block.block_type or "").lower() in self.table_block_types
        ]

        entry_blocks = self._quotation_entry_blocks(page, table_blocks)
        for block in self._section_flow_blocks(page):
            if block.raw_html and "设置格式" in block.raw_html:
                def strip_revision_cell(match: re.Match[str]) -> str:
                    cell_text = strip_html(match.group("body")).strip()
                    if self.revision_artifact_pattern.fullmatch(unicodedata.normalize("NFKC", cell_text)):
                        result.decisions.append(DocumentPreparationDecision(
                            side=side, page_no=page.page_no, block_id=block.block_id,
                            reason="rendered_word_revision_table_cell", text_preview=cell_text,
                        ))
                        return match.group("open") + match.group("close")
                    return match.group(0)
                block.raw_html = re.sub(
                    r"(?P<open><t[dh]\b[^>]*>)(?P<body>.*?)(?P<close></t[dh]>)",
                    strip_revision_cell, block.raw_html, flags=re.IGNORECASE | re.DOTALL,
                )
            compact = re.sub(r"\s+", "", block.text or "")
            if not compact:
                continue
            if block.block_id in continuations or self._is_rendered_revision_artifact(block, page):
                block.enter_clause_compare = False
                self._mark_role(block, side, "revision_artifact", "rendered_word_revision_artifact", result)
                continue
            if self._is_page_number_footer(block, page, compact):
                self._mark_role(block, side, "page_footer", "page_number_footer", result)
                continue
            if block.block_id in entry_blocks:
                active_section_role = "quote_section"
                self._mark_role(block, side, active_section_role, "quotation_page_entry", result)
                if block.block_id == entry_blocks[0] and "document_entry_boundary" not in block.semantic_reasons:
                    block.semantic_reasons.append("document_entry_boundary")
                continue
            section_role = self._section_role(block, compact)
            if section_role:
                active_section_role = section_role
                self._mark_role(block, side, section_role, "section_classifier", result)
                continue
            # A local table caption keeps its identity inside an appendix too.
            table_role = self._table_context_role(block, table_blocks, compact)
            if table_role:
                self._mark_role(block, side, table_role, "nearby_table_header_context", result)
                continue
            if active_section_role and self._looks_like_main_contract_heading(compact):
                active_section_role = None
            if active_section_role and not self._looks_like_main_contract_restart(compact):
                self._mark_role(block, side, active_section_role, "section_classifier_continuation", result)
                continue
            if self._is_quote_page_metadata(block, table_blocks, compact):
                self._mark_role(block, side, "quote_metadata", "nearby_quote_page_metadata", result)
                continue
            if page.page_no == 1 and block.bbox.y0 <= body_start_y and self._is_cover_metadata(block):
                self._mark_role(block, side, "cover_metadata", "cover_metadata_parser", result)
                continue
            table_role = self._table_context_role(block, table_blocks, compact)
            if table_role:
                self._mark_role(block, side, table_role, "nearby_table_header_context", result)
                continue
            block_type = (block.block_type or "").lower()
            if block_type in {"footnote", "vision_footnote"}:
                if self._is_strong_clause_candidate(block, page):
                    block.block_type = "text"
                    block.enter_clause_compare = True
                    block.flow_role = "body"
                    self._mark_role(block, side, "main_clause", "strong_clause_overrides_footnote", result)
                    continue
                near_bottom = page.height > 0 and block.bbox.y0 >= page.height * 0.90
                role = "page_footer" if near_bottom or self.page_number_pattern.fullmatch(compact) else "body_footnote"
                self._mark_role(block, side, role, "footnote_secondary_classification", result)

        return active_section_role

    def _quotation_entry_blocks(self, page: Page, tables: list[TextBlock]) -> list[str]:
        """Bind a quotation's leading masthead to its title, before forward roles.

        Require a heading near the page top, followed by a table, with no prose
        preceding it. Mid-page quotations and ordinary continuation text keep
        their existing boundaries. No company-name vocabulary is involved.
        """
        headings = {"doc_title", "paragraph_title", "title"}
        margins = {"header", "footer", "page_header", "page_footer", "image", "seal", "stamp"}
        ordered = sorted(page.blocks, key=lambda b: (b.bbox.y0, b.bbox.x0))
        for title in ordered:
            compact = re.sub(r"\s+", "", title.text)
            if (title.block_type not in headings or title.bbox.y1 > page.height * .25
                    or self._section_role(title, compact) != "quote_section"
                    or not any(t.bbox.y0 >= title.bbox.y1 for t in tables)):
                continue
            leading = [b for b in ordered if b.bbox.y0 < title.bbox.y0 and b.text.strip()
                       and b.block_type not in margins and b.flow_role not in {"margin", "non_text"}]
            if any(b.block_type not in headings for b in leading):
                continue
            return [b.block_id for b in [*leading, title]]
        return []

    def _is_strong_clause_candidate(self, block: TextBlock, page: Page) -> bool:
        text = unicodedata.normalize("NFKC", block.text or "").strip()
        compact = re.sub(r"\s+", "", text)
        if not self._looks_like_real_clause_heading(compact):
            return False
        if len(compact) < 12:
            return False
        sentence_like = bool(re.search(r"[。；;]", text)) or len(compact) >= 24
        business_signal = bool(self.strong_clause_business_pattern.search(compact))
        body_position = page.height <= 0 or block.bbox.y0 < page.height * 0.90
        return sentence_like and business_signal and body_position

    def _is_rendered_revision_artifact(self, block: TextBlock, page: Page) -> bool:
        compact = unicodedata.normalize("NFKC", block.text or "").strip()
        match = self.revision_artifact_pattern.fullmatch(compact)
        if match is None:
            return False
        near_comment_margin = page.width > 0 and block.bbox.x0 >= page.width * 0.65
        narrow_block = page.width > 0 and (block.bbox.x1 - block.bbox.x0) <= page.width * 0.30
        if match.group("action") == "设置格式":
            payload = match.group("payload").strip()
            return (near_comment_margin and narrow_block) or not payload or bool(
                self.revision_format_payload_pattern.fullmatch(payload)
            )
        return near_comment_margin and narrow_block

    def _is_page_number_footer(self, block: TextBlock, page: Page, compact: str) -> bool:
        return bool(
            page.height > 0
            and block.bbox.y0 >= page.height * 0.90
            and self.page_number_pattern.fullmatch(compact)
        )

    def _looks_like_real_clause_heading(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text or "")
        if not compact or self.date_like_pattern.fullmatch(compact):
            return False
        if self.formal_clause_heading_pattern.match(compact):
            return True
        decimal_match = self.decimal_clause_heading_pattern.match(compact)
        if decimal_match:
            return len(decimal_match.group(2).strip("、.．:：")) >= 4
        parenthesized_match = self.parenthesized_clause_heading_pattern.match(compact)
        if parenthesized_match:
            return len(parenthesized_match.group("body").strip("、.．:：")) >= 4
        integer_match = self.integer_clause_heading_pattern.match(compact)
        return bool(integer_match and len(integer_match.group(1).strip("、.．:：")) >= 4)

    def _looks_like_main_contract_heading(self, compact: str) -> bool:
        return bool(self.main_contract_heading_pattern.match(compact))

    def _section_role(self, block: TextBlock, compact: str) -> str | None:
        block_type = (block.block_type or "").lower()
        if self._looks_like_real_clause_heading(compact):
            return None
        if DocumentUnderstandingService.is_quote_title_text(compact):
            return "quote_section"
        if self.safety_title_pattern.search(compact):
            return "safety_section"
        if self.appendix_title_pattern.match(compact):
            if self.safety_title_pattern.search(compact):
                return "safety_section"
            if DocumentUnderstandingService.is_quote_title_text(compact):
                return "quote_section"
            return "appendix_section"
        if block_type == "doc_title" and DocumentUnderstandingService.is_quote_title_text(compact):
            return "quote_section"
        return None

    def _section_flow_blocks(self, page: Page) -> list[TextBlock]:
        blocks = list(page.blocks)
        if not blocks:
            return []
        if all(block.layout_order is not None for block in blocks):
            return sorted(
                blocks,
                key=lambda block: (
                    block.layout_order or 0,
                    block.bbox.y0,
                    block.bbox.x0,
                    block.block_id,
                ),
            )
        return sorted(
            blocks,
            key=lambda block: (
                block.bbox.y0,
                block.bbox.x0,
                block.reading_order or 0,
                block.block_id,
            ),
        )

    def _looks_like_main_contract_restart(self, compact: str) -> bool:
        if self.appendix_title_pattern.match(compact):
            return False
        return bool(re.search(r"(?:^正文$|达成合同如下|合同正文)", compact))

    def _is_cover_metadata(self, block: TextBlock) -> bool:
        for line in (block.text or "").splitlines():
            if parse_labeled_line(line.strip()):
                return True
        return False

    def _table_context_role(
        self,
        block: TextBlock,
        tables: list[TextBlock],
        compact: str,
    ) -> str | None:
        if (block.block_type or "").lower() in self.table_block_types:
            return None

        nearby_tables = [table for table in tables if self._near_table(block.bbox, table.bbox)]
        if not nearby_tables:
            return None

        if self.table_note_label_pattern.match(compact):
            return "table_note"

        subject = re.sub(r"^[（(]?\d+[）).、]", "", compact)
        if (4 <= len(subject) <= 40 and subject != compact
                and any(subject in re.sub(r"\s+", "", table.text)[:150] for table in nearby_tables)):
            return "table_caption"

        candidate_terms = self._candidate_table_terms(block.text)
        if not candidate_terms or not self._looks_like_table_caption(block.text, candidate_terms):
            return None

        table_terms: set[str] = set()
        for table in nearby_tables:
            table_terms.update(self._table_header_terms(table))
        overlap = candidate_terms & table_terms
        if len(overlap) >= 2:
            return "table_caption"
        if len(overlap) == 1 and len(candidate_terms) <= 2:
            return "table_caption"
        return None

    def _is_quote_page_metadata(
        self,
        block: TextBlock,
        tables: list[TextBlock],
        compact: str,
    ) -> bool:
        if not tables or not any(self._near_table(block.bbox, table.bbox) for table in tables):
            return False
        if self.quote_metadata_label_pattern.match(compact):
            return True
        return bool(len(compact) <= 80 and self.quote_title_pattern.search(compact))

    def _candidate_table_terms(self, text: str) -> set[str]:
        without_prefix = self.clause_prefix_pattern.sub("", text or "")
        return self._tokenize_table_text(without_prefix)

    def _table_header_terms(self, table: TextBlock) -> set[str]:
        source = "\n".join(part for part in (table.raw_html and strip_html(table.raw_html), table.text) if part)
        return self._tokenize_table_text(source)

    def _tokenize_table_text(self, text: str) -> set[str]:
        normalized = strip_html(text or "")
        tokens = {
            token
            for token in re.split(r"[\s,，、;；:：|/\\()\[\]（）【】<>《》]+", normalized)
            if 1 < len(token) <= 16
        }
        compact = re.sub(r"\s+", "", normalized)
        tokens.update(term for term in self.generic_table_terms if term in compact)
        return tokens

    def _looks_like_table_caption(self, text: str, candidate_terms: set[str]) -> bool:
        without_prefix = self.clause_prefix_pattern.sub("", text or "").strip()
        compact = re.sub(r"\s+", "", without_prefix)
        if not compact or len(compact) > 100:
            return False
        if re.search(r"[。；;]", compact):
            return False
        if re.search(r"(应当|应|须|负责|确认|约定|履行|符合|承担|支付|交付|提供)", compact):
            return False
        separators = len(re.findall(r"[、,，/\s]+", without_prefix))
        return compact.endswith((":", "：")) or separators >= 2 or len(candidate_terms) >= 3

    def _near_table(self, block: BBox, table: BBox) -> bool:
        vertical_gap = table.y0 - block.y1
        horizontal_overlap = max(0.0, min(block.x1, table.x1) - max(block.x0, table.x0))
        block_width = max(1.0, block.x1 - block.x0)
        return -20.0 <= vertical_gap <= 140.0 and horizontal_overlap / block_width >= 0.2

    def _mark_role(
        self,
        block: TextBlock,
        side: str,
        role: str,
        reason: str,
        result: DocumentPreparationResult,
    ) -> None:
        if (block.block_role or "").lower() == role:
            return
        block.block_role = role
        result.decisions.append(
            DocumentPreparationDecision(
                side=side,
                page_no=block.page_no,
                block_id=block.block_id,
                reason=reason,
                text_preview=(block.text or "")[:160],
            )
        )
