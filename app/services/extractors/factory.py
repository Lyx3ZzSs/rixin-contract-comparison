from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.extractors.base import DocumentExtractor
from app.services.extractors.ppocrv5 import PPOCRV5Extractor
from app.services.extractors.ppstructure_ocr_hybrid import PPStructureOCRHybridExtractor
from app.services.extractors.pymupdf import PyMuPDFExtractor


class AutoDocumentExtractor:
    name = "auto"

    def __init__(
        self,
        primary: PyMuPDFExtractor | None = None,
        fallback: DocumentExtractor | None = None,
        min_text_chars: int | None = None,
    ) -> None:
        self.primary = primary or PyMuPDFExtractor()
        self.fallback = fallback or PPStructureOCRHybridExtractor()
        self.min_text_chars = settings.pymupdf_min_text_chars if min_text_chars is None else min_text_chars

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        try:
            result = self.primary.extract(path, task_id=task_id)
            text_length = sum(len(block.text.strip()) for page in result.document.pages for block in page.blocks)
            if text_length >= self.min_text_chars:
                return result
            fallback = self.fallback.extract(path, task_id=task_id)
            fallback.extractor_used = self._auto_extractor_name(fallback.extractor_used)
            fallback.warnings.insert(0, f"PyMuPDF 文本量过少({text_length} 字)，已切换结构化 OCR 抽取。")
            return fallback
        except DocumentExtractionError as pymupdf_error:
            try:
                fallback = self.fallback.extract(path, task_id=task_id)
            except DocumentExtractionError as ocr_error:
                raise DocumentExtractionError(
                    f"PyMuPDF 抽取失败: {pymupdf_error}; 结构化 OCR 抽取失败: {ocr_error}"
                ) from ocr_error
            fallback.extractor_used = self._auto_extractor_name(fallback.extractor_used)
            fallback.warnings.insert(0, f"PyMuPDF 抽取失败，已切换结构化 OCR 抽取: {pymupdf_error}")
            return fallback

    def _auto_extractor_name(self, extractor_used: str) -> str:
        if extractor_used == "ppstructure_ocr_hybrid_ocr_only":
            return "auto_ppocrv5"
        return f"auto_{extractor_used or 'ocr'}"


def build_document_extractor(name: str | None = None):
    extractor_name = (name or settings.document_extractor or "auto").lower()
    if extractor_name in {"auto", "default"}:
        return AutoDocumentExtractor()
    if extractor_name in {"pymupdf", "fitz", "pdf_text"}:
        return PyMuPDFExtractor()
    if extractor_name in {"ppocrv5", "pp_ocrv5", "paddleocr", "paddle_ocr", "paddle"}:
        return PPOCRV5Extractor()
    if extractor_name in {"ppstructure_ocr_hybrid", "ppstructure_ppocrv5", "structure_ocr", "ppstructure"}:
        return PPStructureOCRHybridExtractor()
    raise DocumentExtractionError(f"不支持的文档识别器: {extractor_name}")
