from __future__ import annotations

from pathlib import Path

import fitz

from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.extractors.base import DocumentExtractionError, ExtractionResult


class PyMuPDFExtractor:
    name = "pymupdf"

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        path = Path(path)
        if not path.exists():
            raise DocumentExtractionError(f"文件不存在: {path}")
        if path.suffix.lower() != ".pdf":
            raise DocumentExtractionError("仅支持 PDF 文件。")

        try:
            pdf = fitz.open(path)
        except Exception as exc:
            raise DocumentExtractionError(f"PDF 打开失败: {exc}") from exc

        pages: list[Page] = []
        text_found = False
        try:
            for page_index, pdf_page in enumerate(pdf, start=1):
                rect = pdf_page.rect
                blocks: list[TextBlock] = []
                raw = pdf_page.get_text("rawdict")
                for block_index, block in enumerate(raw.get("blocks", [])):
                    if block.get("type") != 0:
                        continue
                    text, char_boxes = self._extract_block_text(block, page_index)
                    text, char_boxes = self._strip_text_with_char_boxes(text, char_boxes)
                    if not text:
                        continue
                    text_found = True
                    x0, y0, x1, y1 = self._block_bbox(block, char_boxes)
                    blocks.append(
                        TextBlock(
                            block_id=f"p{page_index}_b{block_index}",
                            page_no=page_index,
                            text=text,
                            bbox=BBox(x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1)),
                            block_type="text",
                            char_boxes=char_boxes,
                        )
                    )
                pages.append(
                    Page(
                        page_no=page_index,
                        width=float(rect.width),
                        height=float(rect.height),
                        blocks=blocks,
                    )
                )
        finally:
            page_count = len(pdf)
            pdf.close()

        if not text_found:
            raise DocumentExtractionError("当前 PDF 未提取到可复制文本。")

        document = Document(filename=path.name, path=str(path), page_count=page_count, pages=pages)
        return ExtractionResult(document=document, extractor_used=self.name)

    def _extract_block_text(self, block: dict, page_no: int) -> tuple[str, list[CharBox]]:
        parts: list[str] = []
        char_boxes: list[CharBox] = []
        text_index = 0
        for line_index, line in enumerate(block.get("lines", [])):
            if line_index > 0:
                parts.append("\n")
                text_index += 1
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    value = str(char.get("c") or "")
                    if not value:
                        continue
                    bbox = char.get("bbox")
                    parts.append(value)
                    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                        char_boxes.append(
                            CharBox(
                                char=value,
                                page_no=page_no,
                                bbox=BBox(
                                    x0=float(bbox[0]),
                                    y0=float(bbox[1]),
                                    x1=float(bbox[2]),
                                    y1=float(bbox[3]),
                                ),
                                text_index=text_index,
                            )
                        )
                    text_index += len(value)
        return "".join(parts), char_boxes

    def _strip_text_with_char_boxes(self, text: str, char_boxes: list[CharBox]) -> tuple[str, list[CharBox]]:
        stripped = text.strip()
        if not stripped:
            return "", []
        leading = len(text) - len(text.lstrip())
        trailing_start = leading + len(stripped)
        adjusted = [
            char_box.model_copy(update={"text_index": char_box.text_index - leading})
            for char_box in char_boxes
            if char_box.text_index is not None and leading <= char_box.text_index < trailing_start
        ]
        return stripped, adjusted

    def _block_bbox(self, block: dict, char_boxes: list[CharBox]) -> tuple[float, float, float, float]:
        bbox = block.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            return float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
        if char_boxes:
            x0 = min(char_box.bbox.x0 for char_box in char_boxes)
            y0 = min(char_box.bbox.y0 for char_box in char_boxes)
            x1 = max(char_box.bbox.x1 for char_box in char_boxes)
            y1 = max(char_box.bbox.y1 for char_box in char_boxes)
            return x0, y0, x1, y1
        return 0.0, 0.0, 0.0, 0.0
