from __future__ import annotations

from pathlib import Path

from app.clients import HttpClientProvider, default_http_client_provider
from app.config import settings
from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.extractors.base import DocumentExtractor
from app.services.extractors.ppocrv5 import PPOCRV5Extractor
from app.services.extractors.ppstructure_ocr_hybrid import PPStructureOCRHybridExtractor
from app.services.extractors.pymupdf import PyMuPDFExtractor
from app.services.document_profiler import DocumentProfiler


class AutoDocumentExtractor:
    name = "auto"

    def __init__(
        self,
        primary: PyMuPDFExtractor | None = None,
        fallback: DocumentExtractor | None = None,
        min_text_chars: int | None = None,
        artifact_store: ArtifactStore = default_artifact_store,
        client_provider: HttpClientProvider = default_http_client_provider,
    ) -> None:
        self.primary = primary or PyMuPDFExtractor()
        self.fallback = fallback or PPStructureOCRHybridExtractor(
            client_provider=client_provider,
            artifact_store=artifact_store,
        )
        self.min_text_chars = settings.pymupdf_min_text_chars if min_text_chars is None else min_text_chars
        self.profiler = DocumentProfiler()

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        try:
            result = self.primary.extract(path, task_id=task_id)
            result = self._attach_profile(result)
            text_length = sum(len(block.text.strip()) for page in result.document.pages for block in page.blocks)
            if text_length >= self.min_text_chars:
                return result
            fallback = self.fallback.extract(path, task_id=task_id)
            fallback.extractor_used = self._auto_extractor_name(fallback.extractor_used)
            fallback = self._attach_profile(fallback)
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
            fallback = self._attach_profile(fallback)
            fallback.warnings.insert(0, f"PyMuPDF 抽取失败，已切换结构化 OCR 抽取: {pymupdf_error}")
            return fallback

    def _auto_extractor_name(self, extractor_used: str) -> str:
        if extractor_used == "ppstructure_ocr_hybrid_ocr_only":
            return "auto_ppocrv5"
        return f"auto_{extractor_used or 'ocr'}"

    def _attach_profile(self, result: ExtractionResult) -> ExtractionResult:
        profile = self.profiler.profile(result.document, result.extractor_used)
        result.profile = profile
        result.document.profile = profile
        return result


def build_document_extractor(
    name: str | None = None,
    *,
    artifact_store: ArtifactStore = default_artifact_store,
    client_provider: HttpClientProvider = default_http_client_provider,
):
    extractor_name = (name or settings.document_extractor or "auto").lower()
    if extractor_name in {"auto", "default"}:
        return AutoDocumentExtractor(artifact_store=artifact_store, client_provider=client_provider)
    if extractor_name in {"pymupdf", "fitz", "pdf_text"}:
        return PyMuPDFExtractor()
    if extractor_name in {"ppocrv5", "pp_ocrv5", "paddleocr", "paddle_ocr", "paddle"}:
        return PPOCRV5Extractor(client_provider=client_provider, artifact_store=artifact_store)
    if extractor_name in {"ppstructure_ocr_hybrid", "ppstructure_ppocrv5", "structure_ocr", "ppstructure"}:
        return PPStructureOCRHybridExtractor(client_provider=client_provider, artifact_store=artifact_store)
    raise DocumentExtractionError(f"不支持的文档识别器: {extractor_name}")
