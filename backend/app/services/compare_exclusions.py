from __future__ import annotations

import re
import unicodedata
from collections import Counter

from app.models import CompareOptions, Document, Page, TextBlock


class CompareExclusionFilter:
    header_footer_types = {"header", "footer", "page_header", "page_footer"}
    comment_types = {"comment", "annotation", "note", "pdf_comment", "markup"}
    stamp_types = {"seal", "stamp"}
    watermark_types = {"watermark"}
    watermark_tokens = {"水印", "样本", "草稿", "draft", "confidential", "仅供"}
    page_number_pattern = re.compile(r"^(?:第?\d+页?|共\d+页第\d+页)$")

    def apply(self, document: Document, options: CompareOptions) -> Document:
        if not self._has_exclusions(options):
            return document

        repeated_margin_texts = self._repeated_margin_texts(document) if options.ignore_headers_footers else set()
        repeated_center_texts = self._repeated_center_texts(document) if options.ignore_watermarks else set()
        pages: list[Page] = []
        for page in document.pages:
            blocks = [
                block
                for block in page.blocks
                if not self._should_exclude(block, page, options, repeated_margin_texts, repeated_center_texts)
            ]
            pages.append(page.model_copy(update={"blocks": blocks}))
        return document.model_copy(update={"pages": pages})

    def _has_exclusions(self, options: CompareOptions) -> bool:
        return any(
            [
                options.ignore_headers_footers,
                options.ignore_comments,
                options.ignore_stamps,
                options.ignore_watermarks,
            ]
        )

    def _should_exclude(
        self,
        block: TextBlock,
        page: Page,
        options: CompareOptions,
        repeated_margin_texts: set[str],
        repeated_center_texts: set[str],
    ) -> bool:
        block_type = (block.block_type or "").lower()
        if options.ignore_headers_footers and (
            block_type in self.header_footer_types or self._looks_like_header_footer(block, page, repeated_margin_texts)
        ):
            return True
        if options.ignore_comments and block_type in self.comment_types:
            return True
        if options.ignore_stamps and block_type in self.stamp_types:
            return True
        if options.ignore_watermarks and (
            block_type in self.watermark_types or self._looks_like_watermark(block, page, repeated_center_texts)
        ):
            return True
        return False

    def _looks_like_header_footer(self, block: TextBlock, page: Page, repeated_texts: set[str]) -> bool:
        text = self._compact_text(block.text)
        if not text:
            return False
        near_top = block.bbox.y1 <= page.height * 0.08
        near_bottom = block.bbox.y0 >= page.height * 0.92
        if not near_top and not near_bottom:
            return False
        if self._looks_like_clause_start(text):
            return False
        key = self._key(text)
        if key in repeated_texts:
            return True
        if near_bottom and self.page_number_pattern.fullmatch(text):
            return True
        return len(text) <= 40 and (near_bottom or self._looks_like_running_header(text, page, block))

    def _looks_like_running_header(self, text: str, page: Page, block: TextBlock) -> bool:
        if re.search(r"(合同|协议|条款|甲方|乙方)", text) and block.bbox.x0 < page.width * 0.22:
            return False
        return bool(re.search(r"[A-Za-z0-9][A-Za-z0-9._/-]{3,}", text) or len(text) <= 10)

    def _looks_like_watermark(self, block: TextBlock, page: Page, repeated_texts: set[str]) -> bool:
        text = self._compact_text(block.text)
        if not text or len(text) > 24:
            return False
        key = self._key(text)
        has_watermark_token = any(token in text.lower() for token in self.watermark_tokens)
        if not has_watermark_token:
            return False
        if key in repeated_texts:
            return True
        center_x = (block.bbox.x0 + block.bbox.x1) / 2
        center_y = (block.bbox.y0 + block.bbox.y1) / 2
        return (
            page.width * 0.25 <= center_x <= page.width * 0.75
            and page.height * 0.20 <= center_y <= page.height * 0.80
        )

    def _repeated_margin_texts(self, document: Document) -> set[str]:
        counter: Counter[str] = Counter()
        for page in document.pages:
            seen_on_page: set[str] = set()
            for block in page.blocks:
                if block.bbox.y1 <= page.height * 0.08 or block.bbox.y0 >= page.height * 0.92:
                    key = self._key(block.text)
                    if key:
                        seen_on_page.add(key)
            counter.update(seen_on_page)
        return {key for key, count in counter.items() if count >= 2}

    def _repeated_center_texts(self, document: Document) -> set[str]:
        counter: Counter[str] = Counter()
        for page in document.pages:
            seen_on_page: set[str] = set()
            for block in page.blocks:
                center_x = (block.bbox.x0 + block.bbox.x1) / 2
                center_y = (block.bbox.y0 + block.bbox.y1) / 2
                if (
                    page.width * 0.25 <= center_x <= page.width * 0.75
                    and page.height * 0.20 <= center_y <= page.height * 0.80
                ):
                    key = self._key(block.text)
                    if key:
                        seen_on_page.add(key)
            counter.update(seen_on_page)
        return {key for key, count in counter.items() if count >= 2}

    def _looks_like_clause_start(self, text: str) -> bool:
        return bool(
            re.match(r"^第[一二三四五六七八九十百千万0-9]+[章节条]", text)
            or re.match(r"^[一二三四五六七八九十]+、", text)
            or re.match(r"^\d+(?:\.\d+){0,3}[.、]?", text)
        )

    def _key(self, text: str) -> str:
        return re.sub(r"[\W_]+", "", self._compact_text(text), flags=re.UNICODE).lower()

    def _compact_text(self, text: str) -> str:
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
