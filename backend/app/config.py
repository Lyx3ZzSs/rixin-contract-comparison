from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config_models import (
    DocumentUnderstandingSettings,
    HybridSettings,
    MatchingSettings,
    PipelineSettings,
    PPOCRV5Settings,
    PPStructureSettings,
    RegistrySettings,
    ReportSettings,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
BASE_DIR = BACKEND_DIR.parent
SUPPORTED_DOCUMENT_EXTRACTORS = {
    "auto",
    "default",
    "pymupdf",
    "fitz",
    "pdf_text",
    "ppocrv5",
    "pp_ocrv5",
    "paddleocr",
    "paddle_ocr",
    "paddle",
    "ppstructure_ocr_hybrid",
    "ppstructure_ppocrv5",
    "structure_ocr",
    "ppstructure",
}
STRUCTURED_DOCUMENT_EXTRACTORS = {
    "ppstructure_ocr_hybrid",
    "ppstructure_ppocrv5",
    "structure_ocr",
    "ppstructure",
}


class Settings(BaseSettings):
    """Application settings with hierarchical nested models.

    All flat fields are kept for backward compatibility — existing ``.env``
    files and ``settings.xxx`` access patterns continue to work.
    Nested models (``extraction``, ``matching``, etc.) provide per-domain
    grouped access.
    """

    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- Storage paths (flat, for backward compat) ----------------------

    base_dir: Path = BASE_DIR
    storage_dir: Path = Field(default_factory=lambda: BASE_DIR / "storage")
    uploads_dir: Path | None = None
    tasks_dir: Path | None = None
    reports_dir: Path | None = None
    ocr_dir: Path | None = None
    debug_dir: Path | None = None
    cache_dir: Path | None = None
    max_upload_size_mb: int = Field(default=30, ge=1)

    # -- Extraction (flat env vars → nested model) ----------------------

    document_extractor: str = "auto"
    compare_document_extractor: str = "ppstructure_ocr_hybrid"
    compare_require_structured_ocr: bool = True
    pymupdf_min_text_chars: int = Field(default=1, ge=0)
    align_structured_extraction: bool = True
    save_ocr_raw_result: bool = True

    ppocrv5_url: str = ""
    ppocrv5_access_token: str = ""
    ppocrv5_timeout_seconds: int = Field(default=600, ge=1)
    ppocrv5_return_word_box: bool = True
    ppocrv5_text_rec_score_thresh: float = Field(default=0.0, ge=0.0, le=1.0)
    ppocrv5_edge_noise_score_thresh: float = Field(default=0.30, ge=0.0, le=1.0)
    ppocrv5_edge_noise_margin_ratio: float = Field(default=0.02, ge=0.0, le=1.0)
    ppocrv5_edge_noise_max_chars: int = Field(default=2, ge=0)
    ppocrv5_use_doc_orientation_classify: bool = False
    ppocrv5_use_doc_unwarping: bool = False
    ppocrv5_use_textline_orientation: bool = False

    ppstructure_url: str = ""
    ppstructure_access_token: str = ""
    ppstructure_timeout_seconds: int = Field(default=600, ge=1)
    ppstructure_use_doc_orientation_classify: bool = False
    ppstructure_use_doc_unwarping: bool = False
    ppstructure_use_textline_orientation: bool = False
    ppstructure_use_table_recognition: bool = True
    ppstructure_use_seal_recognition: bool = True
    ppstructure_use_region_detection: bool = True
    ppstructure_format_block_content: bool = True

    hybrid_layout_overlap_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    hybrid_layout_center_fallback: bool = True
    hybrid_save_merged_raw: bool = True

    layout_analysis_mode: str = "v2"


    # -- AI / LLM (flat env vars) ----------------------------------------

    ai_llm_base_url: str = ""
    ai_llm_api_key: str = ""
    ai_llm_model: str = ""
    ai_extraction_timeout_seconds: int = Field(default=60, ge=1)

    document_understanding_enabled: bool = True
    document_understanding_llm_enabled: bool = False
    document_understanding_llm_base_url: str = ""
    document_understanding_llm_api_key: str = ""
    document_understanding_llm_model: str = ""
    document_understanding_llm_timeout_seconds: int = Field(default=60, ge=1)
    document_understanding_llm_max_retries: int = Field(default=2, ge=0)
    document_understanding_rule_confidence_accept: float = Field(default=0.85, ge=0.0, le=1.0)
    document_understanding_llm_confidence_accept: float = Field(default=0.82, ge=0.0, le=1.0)
    document_understanding_enable_ocr_correction: bool = False
    document_understanding_enable_cross_page_merge: bool = True


    # -- Matching (flat env vars) -----------------------------------------

    match_threshold: int = Field(default=85, ge=0, le=100)
    match_use_prefilter: bool = True
    match_enable_semantic_match: bool = False
    match_semantic_provider: str = "local"
    match_semantic_model_path: str = ""
    match_semantic_base_url: str = ""
    match_semantic_api_key: str = ""
    match_semantic_model: str = ""
    match_semantic_device: str = "auto"
    match_semantic_batch_size: int = Field(default=32, ge=1)
    match_semantic_timeout_seconds: int = Field(default=60, ge=1)
    match_semantic_max_retries: int = Field(default=2, ge=0)
    match_semantic_weight: float = Field(default=0.08, ge=0.0, le=0.3)
    match_low_confidence_review_threshold: float = Field(default=78.0, ge=0.0, le=100.0)

    # -- Diff (flat env vars) ---------------------------------------------

    diff_engine: str = "diff_match_patch"

    # -- Report (flat env vars) -------------------------------------------

    report_font_path: str = ""
    libreoffice_path: str = ""
    libreoffice_host: str = "127.0.0.1"
    libreoffice_port: int = Field(default=2002, ge=1, le=65535)
    libreoffice_timeout_seconds: int = Field(default=30, ge=5, le=120)

    # -- Pipeline (flat env vars) -----------------------------------------

    task_runner_max_workers: int = Field(default=2, ge=1, le=16)
    task_runner_max_attempts: int = Field(default=1, ge=1, le=5)
    task_runner_lease_seconds: int = Field(default=3600, ge=30)
    task_runner_retry_delay_seconds: float = Field(default=2.0, ge=0)
    task_runner_poll_interval_seconds: float = Field(default=0.25, ge=0.01)

    # -- Model registry (flat env vars) ----------------------------------

    model_registry_max_loaded: int = Field(default=0, ge=0)
    model_registry_preload: str = ""

    # -- Nested models (populated by model_validator) --------------------

    matching: MatchingSettings = Field(default_factory=MatchingSettings, exclude=True)
    report: ReportSettings = Field(default_factory=ReportSettings, exclude=True)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings, exclude=True)
    registry: RegistrySettings = Field(default_factory=RegistrySettings, exclude=True)
    document_understanding: DocumentUnderstandingSettings = Field(
        default_factory=DocumentUnderstandingSettings,
        exclude=True,
    )

    ppstructure: PPStructureSettings = Field(default_factory=PPStructureSettings, exclude=True)
    ppocrv5: PPOCRV5Settings = Field(default_factory=PPOCRV5Settings, exclude=True)
    hybrid: HybridSettings = Field(default_factory=HybridSettings, exclude=True)

    # -- Validators (flat env var validation, unchanged) -----------------

    @field_validator("document_extractor", "compare_document_extractor", mode="before")
    @classmethod
    def validate_document_extractor(cls, value: Any) -> str:
        extractor = str(value or "auto").strip().lower()
        if extractor not in SUPPORTED_DOCUMENT_EXTRACTORS:
            allowed = ", ".join(sorted(SUPPORTED_DOCUMENT_EXTRACTORS))
            raise ValueError(f"DOCUMENT_EXTRACTOR must be one of: {allowed}")
        return extractor

    @field_validator("diff_engine", mode="before")
    @classmethod
    def validate_diff_engine(cls, value: Any) -> str:
        engine = str(value or "diff_match_patch").strip().lower().replace("-", "_")
        if engine not in {"diff_match_patch", "difflib"}:
            raise ValueError("DIFF_ENGINE must be one of: diff_match_patch, difflib")
        return engine

    @field_validator("ppocrv5_url", "ppstructure_url")
    @classmethod
    def validate_optional_http_url(cls, value: str) -> str:
        url = value.strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("URL values must start with http:// or https://")
        return url

    @field_validator("match_semantic_provider", mode="before")
    @classmethod
    def validate_match_semantic_provider(cls, value: Any) -> str:
        provider = str(value or "local").strip().lower()
        if provider not in {"local", "openai"}:
            raise ValueError("MATCH_SEMANTIC_PROVIDER must be one of: local, openai")
        return provider

    @field_validator("match_semantic_base_url")
    @classmethod
    def validate_match_semantic_base_url(cls, value: str) -> str:
        url = value.strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("MATCH_SEMANTIC_BASE_URL must start with http:// or https://")
        return url

    @field_validator("ai_llm_base_url", "document_understanding_llm_base_url")
    @classmethod
    def validate_optional_llm_http_url(cls, value: str) -> str:
        url = value.strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("LLM URL values must start with http:// or https://")
        return url

    @field_validator("layout_analysis_mode")
    @classmethod
    def validate_layout_analysis_mode(cls, value: str) -> str:
        if value not in {"v2", "v3", "v3_shadow", "shadow"}:
            raise ValueError("LAYOUT_ANALYSIS_MODE must be one of: v2, v3, v3_shadow, shadow")
        return value

    # -- Model validator: populate nested + storage subdirs --------------

    @model_validator(mode="after")
    def _build_nested_and_paths(self) -> Settings:
        if (
            self.compare_require_structured_ocr
            and self.compare_document_extractor not in STRUCTURED_DOCUMENT_EXTRACTORS
        ):
            allowed = ", ".join(sorted(STRUCTURED_DOCUMENT_EXTRACTORS))
            raise ValueError(
                "COMPARE_DOCUMENT_EXTRACTOR must use a structured OCR extractor "
                f"when COMPARE_REQUIRE_STRUCTURED_OCR=true: {allowed}"
            )

        # Populate nested models from flat fields
        self.matching = MatchingSettings(
            threshold=self.match_threshold,
            use_prefilter=self.match_use_prefilter,
            enable_semantic_match=self.match_enable_semantic_match,
            semantic_provider=self.match_semantic_provider,
            semantic_model_path=self.match_semantic_model_path,
            semantic_base_url=self.match_semantic_base_url,
            semantic_api_key=self.match_semantic_api_key,
            semantic_model=self.match_semantic_model,
            semantic_device=self.match_semantic_device,
            semantic_batch_size=self.match_semantic_batch_size,
            semantic_timeout_seconds=self.match_semantic_timeout_seconds,
            semantic_max_retries=self.match_semantic_max_retries,
            semantic_weight=self.match_semantic_weight,
            low_confidence_review_threshold=self.match_low_confidence_review_threshold,
        )

        self.report = ReportSettings(
            font_path=self.report_font_path,
            libreoffice_path=self.libreoffice_path,
            libreoffice_host=self.libreoffice_host,
            libreoffice_port=self.libreoffice_port,
            libreoffice_timeout_seconds=self.libreoffice_timeout_seconds,
        )

        self.pipeline = PipelineSettings(
            task_runner_max_workers=self.task_runner_max_workers,
            task_runner_max_attempts=self.task_runner_max_attempts,
            task_runner_lease_seconds=self.task_runner_lease_seconds,
            task_runner_retry_delay_seconds=self.task_runner_retry_delay_seconds,
            task_runner_poll_interval_seconds=self.task_runner_poll_interval_seconds,
        )

        preload_list = [s.strip() for s in self.model_registry_preload.split(",") if s.strip()] if self.model_registry_preload else []
        self.registry = RegistrySettings(
            max_loaded_models=self.model_registry_max_loaded,
            preload_models=preload_list,
        )

        self.document_understanding = DocumentUnderstandingSettings(
            enabled=self.document_understanding_enabled,
            llm_enabled=self.document_understanding_llm_enabled,
            llm_base_url=self.document_understanding_llm_base_url or self.ai_llm_base_url,
            llm_api_key=self.document_understanding_llm_api_key or self.ai_llm_api_key,
            llm_model=self.document_understanding_llm_model or self.ai_llm_model,
            llm_timeout_seconds=self.document_understanding_llm_timeout_seconds,
            llm_max_retries=self.document_understanding_llm_max_retries,
            rule_confidence_accept=self.document_understanding_rule_confidence_accept,
            llm_confidence_accept=self.document_understanding_llm_confidence_accept,
            enable_ocr_correction=self.document_understanding_enable_ocr_correction,
            enable_cross_page_merge=self.document_understanding_enable_cross_page_merge,
        )

        self.ppstructure = PPStructureSettings(
            url=self.ppstructure_url,
            access_token=self.ppstructure_access_token,
            timeout_seconds=self.ppstructure_timeout_seconds,
            use_doc_orientation_classify=self.ppstructure_use_doc_orientation_classify,
            use_doc_unwarping=self.ppstructure_use_doc_unwarping,
            use_textline_orientation=self.ppstructure_use_textline_orientation,
            use_table_recognition=self.ppstructure_use_table_recognition,
            use_seal_recognition=self.ppstructure_use_seal_recognition,
            use_region_detection=self.ppstructure_use_region_detection,
            format_block_content=self.ppstructure_format_block_content,
        )

        self.ppocrv5 = PPOCRV5Settings(
            url=self.ppocrv5_url,
            access_token=self.ppocrv5_access_token,
            timeout_seconds=self.ppocrv5_timeout_seconds,
            return_word_box=self.ppocrv5_return_word_box,
            text_rec_score_thresh=self.ppocrv5_text_rec_score_thresh,
            edge_noise_score_thresh=self.ppocrv5_edge_noise_score_thresh,
            edge_noise_margin_ratio=self.ppocrv5_edge_noise_margin_ratio,
            edge_noise_max_chars=self.ppocrv5_edge_noise_max_chars,
            use_doc_orientation_classify=self.ppocrv5_use_doc_orientation_classify,
            use_doc_unwarping=self.ppocrv5_use_doc_unwarping,
            use_textline_orientation=self.ppocrv5_use_textline_orientation,
        )

        self.hybrid = HybridSettings(
            layout_overlap_threshold=self.hybrid_layout_overlap_threshold,
            center_fallback=self.hybrid_layout_center_fallback,
            save_merged_raw=self.hybrid_save_merged_raw,
        )

        # Resolve storage paths
        storage_dir = self._resolve_runtime_path(self.storage_dir)
        self.storage_dir = storage_dir
        if self.uploads_dir is None:
            self.uploads_dir = storage_dir / "uploads"
        else:
            self.uploads_dir = self._resolve_runtime_path(self.uploads_dir)
        if self.tasks_dir is None:
            self.tasks_dir = storage_dir / "tasks"
        else:
            self.tasks_dir = self._resolve_runtime_path(self.tasks_dir)
        if self.reports_dir is None:
            self.reports_dir = storage_dir / "reports"
        else:
            self.reports_dir = self._resolve_runtime_path(self.reports_dir)
        if self.ocr_dir is None:
            self.ocr_dir = storage_dir / "ocr"
        else:
            self.ocr_dir = self._resolve_runtime_path(self.ocr_dir)
        if self.debug_dir is None:
            self.debug_dir = storage_dir / "debug"
        else:
            self.debug_dir = self._resolve_runtime_path(self.debug_dir)
        if self.cache_dir is None:
            self.cache_dir = storage_dir / "cache"
        else:
            self.cache_dir = self._resolve_runtime_path(self.cache_dir)

        return self

    def _resolve_runtime_path(self, path: Path) -> Path:
        return path if path.is_absolute() else BASE_DIR / path

    @property
    def storage_subdirs(self) -> list[Path]:
        return [
            self.tasks_dir,
        ]

    def ensure_storage(self) -> None:
        for directory in self.storage_subdirs:
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
