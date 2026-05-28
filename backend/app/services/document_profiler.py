from __future__ import annotations

import re

from app.models import BBox, Document, DocumentProfile, Page, PageProfile, ParseWarningDetail, TextBlock


class DocumentProfiler:
    """Build lightweight page-level extraction diagnostics for comparison debugging."""

    low_text_chars_per_page = 20
    table_area_ratio_threshold = 0.28
    image_area_ratio_threshold = 0.55

    def profile(self, document: Document, extractor_used: str = "") -> DocumentProfile:
        page_profiles = [self._page_profile(page) for page in document.pages]
        total_text_chars = sum(page.char_count for page in page_profiles)
        table_block_count = sum(page.table_block_count for page in page_profiles)
        image_block_count = sum(page.image_block_count for page in page_profiles)
        scanned_page_count = sum(1 for page in page_profiles if page.extraction_strategy == "ocr")
        table_heavy_page_count = sum(1 for page in page_profiles if page.table_heavy)
        warnings = self._warnings(page_profiles, extractor_used)

        if scanned_page_count and scanned_page_count == len(page_profiles):
            recommended_strategy = "ocr"
        elif scanned_page_count or table_heavy_page_count:
            recommended_strategy = "mixed"
        else:
            recommended_strategy = "text"

        return DocumentProfile(
            filename=document.filename,
            page_count=document.page_count,
            extractor_used=extractor_used,
            total_text_chars=total_text_chars,
            table_block_count=table_block_count,
            image_block_count=image_block_count,
            scanned_page_count=scanned_page_count,
            table_heavy_page_count=table_heavy_page_count,
            page_profiles=page_profiles,
            recommended_strategy=recommended_strategy,
            warnings=warnings,
        )

    def _page_profile(self, page: Page) -> PageProfile:
        text_blocks = [block for block in page.blocks if self._block_family(block) == "text"]
        table_blocks = [block for block in page.blocks if self._block_family(block) == "table"]
        image_blocks = [block for block in page.blocks if self._block_family(block) == "image"]
        confidences = [block.confidence for block in page.blocks if block.confidence is not None]
        char_count = sum(len((block.text or "").strip()) for block in page.blocks if self._block_family(block) != "image")
        table_area_ratio = self._area_ratio(table_blocks, page)
        image_area_ratio = self._area_ratio(image_blocks, page)
        low_text = char_count < self.low_text_chars_per_page
        table_heavy = bool(table_blocks) and (table_area_ratio >= self.table_area_ratio_threshold or len(table_blocks) >= len(text_blocks) + 2)

        if low_text and image_area_ratio >= self.image_area_ratio_threshold:
            strategy = "ocr"
        elif table_heavy:
            strategy = "structured_ocr"
        elif low_text:
            strategy = "ocr"
        else:
            strategy = "text"

        return PageProfile(
            page_no=page.page_no,
            width=page.width,
            height=page.height,
            text_block_count=len(text_blocks),
            table_block_count=len(table_blocks),
            image_block_count=len(image_blocks),
            char_count=char_count,
            avg_confidence=sum(confidences) / len(confidences) if confidences else None,
            table_area_ratio=round(table_area_ratio, 4),
            image_area_ratio=round(image_area_ratio, 4),
            page_role=self._page_role(page),
            extraction_strategy=strategy,
            low_text=low_text,
            table_heavy=table_heavy,
        )

    def _warnings(self, page_profiles: list[PageProfile], extractor_used: str) -> list[ParseWarningDetail]:
        warnings: list[ParseWarningDetail] = []
        lower_extractor = (extractor_used or "").lower()
        for page in page_profiles:
            if page.low_text and "pymupdf" in lower_extractor:
                warnings.append(
                    ParseWarningDetail(
                        code="LOW_TEXT_PAGE",
                        message=f"第 {page.page_no} 页文本量较低，可能需要 OCR 或人工复核。",
                        page_no=page.page_no,
                        source="document_profiler",
                    )
                )
            if page.table_heavy and "ppstructure" not in lower_extractor:
                warnings.append(
                    ParseWarningDetail(
                        code="TABLE_HEAVY_PAGE",
                        message=f"第 {page.page_no} 页疑似表格密集页，建议使用结构化 OCR 抽取。",
                        page_no=page.page_no,
                        source="document_profiler",
                    )
                )
        return warnings

    def _page_role(self, page: Page) -> str:
        text = re.sub(r"\s+", "", "\n".join(block.text or "" for block in page.blocks))
        if re.search(r"^附件[一二三四五六七八九十\d]?", text) or "附件" in text[:40]:
            return "appendix"
        if page.page_no == 1 and re.search(r"(合同编号|签订日期|甲方|乙方|采购合同|买方|卖方)", text):
            return "cover"
        if text in {"正文"} or "达成合同如下" in text[:120]:
            return "body_start"
        return "body"

    def _block_family(self, block: TextBlock) -> str:
        block_type = (block.block_type or "").lower()
        if block_type in {"table", "table_title", "table_cell"} or block.raw_html:
            return "table"
        if block_type in {"image", "figure", "chart", "seal"}:
            return "image"
        return "text"

    def _area_ratio(self, blocks: list[TextBlock], page: Page) -> float:
        page_area = max(1.0, page.width * page.height)
        return min(1.0, sum(self._area(block.layout_bbox or block.bbox) for block in blocks) / page_area)

    def _area(self, bbox: BBox) -> float:
        return max(0.0, bbox.x1 - bbox.x0) * max(0.0, bbox.y1 - bbox.y0)
