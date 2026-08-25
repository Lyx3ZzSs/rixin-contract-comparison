from __future__ import annotations

from pathlib import Path

from app.clients import HttpClientProvider, default_http_client_provider
from app.config import STRUCTURED_DOCUMENT_EXTRACTORS, settings
from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.extractors.base import DocumentExtractor
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
    require_structured_ocr: bool = False,
    artifact_store: ArtifactStore = default_artifact_store,
    client_provider: HttpClientProvider = default_http_client_provider,
):
    """Build a document extractor by name.

    Delegates to ``ExtractorRegistry`` for named extractors.
    ``auto`` and ``default`` use the ``AutoDocumentExtractor`` wrapper.

    When ``extraction_cache_enabled`` is set, wraps the extractor with
    ``CachedExtractor`` for file-based caching of extraction results.
    """
    from app.services.extractors.registry import default_extractor_registry

    extractor_name = (name or settings.document_extractor or "auto").lower()

    if require_structured_ocr and extractor_name not in STRUCTURED_DOCUMENT_EXTRACTORS:
        raise DocumentExtractionError(f"严格结构化 OCR 模式不支持提取器: {extractor_name}")

    if extractor_name in {"auto", "default"}:
        extractor = AutoDocumentExtractor(artifact_store=artifact_store, client_provider=client_provider)
    elif require_structured_ocr:
        extractor = PPStructureOCRHybridExtractor(
            require_structure=True,
            client_provider=client_provider,
            artifact_store=artifact_store,
        )
    else:
        try:
            extractor = default_extractor_registry.build(
                extractor_name,
                artifact_store=artifact_store,
                client_provider=client_provider,
            )
        except ValueError:
            raise DocumentExtractionError(f"不支持的文档识别器: {extractor_name}")

    if False:  # extraction cache disabled (extraction feature removed)
        from app.infrastructure.extraction_cache import CachedExtractor, FileExtractionCache

        cache = FileExtractionCache(
            cache_dir=settings.storage_dir / "cache",
            default_ttl_hours=24,
        )
        fingerprint = _extractor_config_fingerprint(
            extractor_name,
            require_structured_ocr=require_structured_ocr,
        )
        extractor = CachedExtractor(extractor, cache, fingerprint)

    return extractor


def build_compare_document_extractor(
    *,
    artifact_store: ArtifactStore = default_artifact_store,
    client_provider: HttpClientProvider = default_http_client_provider,
):
    return build_document_extractor(
        settings.compare_document_extractor,
        require_structured_ocr=settings.compare_require_structured_ocr,
        artifact_store=artifact_store,
        client_provider=client_provider,
    )


def _extractor_config_fingerprint(extractor_name: str, *, require_structured_ocr: bool = False) -> str:
    """Deterministic hash of the config fields that affect extraction output."""
    import hashlib
    import json

    parts: dict[str, str] = {
        "name": extractor_name,
        "require_structured_ocr": str(require_structured_ocr),
        "layout_parser_version": "v3" if settings.layout_analysis_mode in {"v3", "v3_shadow"} else "v2",
        "layout_analysis_mode": settings.layout_analysis_mode,
    }

    if extractor_name in {"ppocrv5", "paddleocr", "paddle_ocr", "paddle", "pp_ocrv5"}:
        parts["ppocrv5"] = settings.ppocrv5.model_dump_json()
    elif extractor_name in {"ppstructure_ocr_hybrid", "ppstructure", "structure_ocr", "ppstructure_ppocrv5"}:
        parts["ppstructure"] = settings.ppstructure.model_dump_json()
        parts["ppocrv5"] = settings.ppocrv5.model_dump_json()
        parts["hybrid"] = settings.hybrid.model_dump_json()
    elif extractor_name in {"auto", "default"}:
        parts["pymupdf_min"] = str(settings.pymupdf_min_text_chars)
        parts["ppstructure"] = settings.ppstructure.model_dump_json()
        parts["ppocrv5"] = settings.ppocrv5.model_dump_json()

    payload = json.dumps(parts, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()[:12]
