from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.extractors.paddleocr import PaddleOCRExtractor
from app.services.extractors.paddleocr_vl import PaddleOCRVLExtractor
from app.services.extractors.pymupdf import PyMuPDFExtractor


class AutoDocumentExtractor:
    name = "auto"

    def __init__(
        self,
        primary: PyMuPDFExtractor | None = None,
        fallback: PaddleOCRExtractor | None = None,
        min_text_chars: int | None = None,
    ) -> None:
        self.primary = primary or PyMuPDFExtractor()
        self.fallback = fallback or PaddleOCRExtractor()
        self.min_text_chars = settings.pymupdf_min_text_chars if min_text_chars is None else min_text_chars

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        try:
            result = self.primary.extract(path, task_id=task_id)
            text_length = sum(len(block.text.strip()) for page in result.document.pages for block in page.blocks)
            if text_length >= self.min_text_chars:
                return result
            fallback = self.fallback.extract(path, task_id=task_id)
            fallback.extractor_used = "pymupdf_fallback_paddleocr"
            fallback.warnings.insert(0, f"PyMuPDF 文本量过少({text_length} 字)，已降级 PaddleOCR。")
            return fallback
        except DocumentExtractionError as pymupdf_error:
            try:
                fallback = self.fallback.extract(path, task_id=task_id)
            except DocumentExtractionError as paddle_error:
                raise DocumentExtractionError(
                    f"PyMuPDF 抽取失败: {pymupdf_error}; PaddleOCR 抽取失败: {paddle_error}"
                ) from paddle_error
            fallback.extractor_used = "pymupdf_fallback_paddleocr"
            fallback.warnings.insert(0, f"PyMuPDF 抽取失败，已降级 PaddleOCR: {pymupdf_error}")
            return fallback


def build_document_extractor(name: str | None = None):
    extractor_name = (name or settings.document_extractor or "auto").lower()
    if extractor_name in {"auto", "default"}:
        return AutoDocumentExtractor()
    if extractor_name in {"pymupdf", "fitz", "pdf_text"}:
        return PyMuPDFExtractor()
    if extractor_name in {"paddleocr", "paddle_ocr", "paddle"}:
        return PaddleOCRExtractor()
    if extractor_name in {"paddleocr_vl", "paddleocr-vl", "vl"}:
        return PaddleOCRVLExtractor()
    raise DocumentExtractionError(f"不支持的文档识别器: {extractor_name}")
