from __future__ import annotations

from pathlib import Path

import fitz

from app.models import BBox, Document, Page, TextBlock


class PdfParseError(ValueError):
    pass


class PdfParser:
    def parse(self, path: str | Path) -> Document:
        path = Path(path)
        if not path.exists():
            raise PdfParseError(f"文件不存在: {path}")
        if path.suffix.lower() != ".pdf":
            raise PdfParseError("仅支持 PDF 文件。")

        try:
            pdf = fitz.open(path)
        except Exception as exc:
            raise PdfParseError(f"PDF 打开失败: {exc}") from exc

        pages: list[Page] = []
        text_found = False
        try:
            for page_index, pdf_page in enumerate(pdf, start=1):
                rect = pdf_page.rect
                blocks: list[TextBlock] = []
                for block_index, block in enumerate(pdf_page.get_text("blocks")):
                    x0, y0, x1, y1, text, *_ = block
                    text = (text or "").strip()
                    if not text:
                        continue
                    text_found = True
                    blocks.append(
                        TextBlock(
                            block_id=f"p{page_index}_b{block_index}",
                            page_no=page_index,
                            text=text,
                            bbox=BBox(x0=float(x0), y0=float(y0), x1=float(x1), y1=float(y1)),
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
            raise PdfParseError("当前版本不支持扫描件或图片型 PDF，请上传可复制文本的 PDF。")

        return Document(filename=path.name, path=str(path), page_count=page_count, pages=pages)

