from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config_defaults import (
    DEFAULT_EXTRACTION_FEW_SHOT_DEMO,
    DEFAULT_EXTRACTION_OUTPUT_FORMAT,
    DEFAULT_EXTRACTION_RULES_STR,
    DEFAULT_EXTRACTION_TASK_DESCRIPTION,
)
from app.config_models import (
    AILLMSettings,
    ExtractionSettings,
    HybridSettings,
    MatchingSettings,
    ModelConfig,
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

    extraction_max_document_size_mb: int = Field(default=60, ge=1)
    extraction_max_image_size_mb: int = Field(default=5, ge=1)
    save_extraction_raw_result: bool = True
    extraction_cache_enabled: bool = False
    extraction_cache_ttl_hours: int = Field(default=72, ge=1)
    extraction_window_size: int = Field(default=0, ge=0)
    extraction_window_overlap: int = Field(default=1, ge=0)

    # -- AI / LLM (flat env vars) ----------------------------------------

    ai_llm_base_url: str = ""
    ai_llm_api_key: str = ""
    ai_llm_model: str = ""
    ai_extraction_timeout_seconds: int = Field(default=120, ge=1)

    extraction_task_description: str = DEFAULT_EXTRACTION_TASK_DESCRIPTION
    extraction_output_format: str = DEFAULT_EXTRACTION_OUTPUT_FORMAT
    extraction_rules_str: str = DEFAULT_EXTRACTION_RULES_STR
    extraction_few_shot_demo: str = DEFAULT_EXTRACTION_FEW_SHOT_DEMO

    # -- Matching (flat env vars) -----------------------------------------

    match_threshold: int = Field(default=85, ge=0, le=100)
    match_use_prefilter: bool = True

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

    extraction: ExtractionSettings = Field(default_factory=ExtractionSettings, exclude=True)
    ai_llm: AILLMSettings = Field(default_factory=AILLMSettings, exclude=True)
    matching: MatchingSettings = Field(default_factory=MatchingSettings, exclude=True)
    report: ReportSettings = Field(default_factory=ReportSettings, exclude=True)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings, exclude=True)
    registry: RegistrySettings = Field(default_factory=RegistrySettings, exclude=True)

    # -- Validators (flat env var validation, unchanged) -----------------

    @field_validator("document_extractor", mode="before")
    @classmethod
    def validate_document_extractor(cls, value: Any) -> str:
        extractor = str(value or "auto").strip().lower()
        if extractor not in SUPPORTED_DOCUMENT_EXTRACTORS:
            allowed = ", ".join(sorted(SUPPORTED_DOCUMENT_EXTRACTORS))
            raise ValueError(f"DOCUMENT_EXTRACTOR must be one of: {allowed}")
        return extractor

    @field_validator("ppocrv5_url", "ppstructure_url", "ai_llm_base_url")
    @classmethod
    def validate_optional_http_url(cls, value: str) -> str:
        url = value.strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("URL values must start with http:// or https://")
        return url

    @field_validator(
        "extraction_task_description",
        "extraction_output_format",
        "extraction_rules_str",
        "extraction_few_shot_demo",
    )
    @classmethod
    def use_default_for_empty_prompt_values(cls, value: str, info: Any) -> str:
        defaults = {
            "extraction_task_description": DEFAULT_EXTRACTION_TASK_DESCRIPTION,
            "extraction_output_format": DEFAULT_EXTRACTION_OUTPUT_FORMAT,
            "extraction_rules_str": DEFAULT_EXTRACTION_RULES_STR,
            "extraction_few_shot_demo": DEFAULT_EXTRACTION_FEW_SHOT_DEMO,
        }
        return value if value.strip() else defaults[info.field_name]

    # -- Model validator: populate nested + storage subdirs --------------

    @model_validator(mode="after")
    def _build_nested_and_paths(self) -> Settings:
        # Populate nested models from flat fields
        self.extraction = ExtractionSettings(
            backend=self.document_extractor,
            pymupdf_min_text_chars=self.pymupdf_min_text_chars,
            align_structured=self.align_structured_extraction,
            save_raw_result=self.save_ocr_raw_result,
            max_document_size_mb=self.extraction_max_document_size_mb,
            max_image_size_mb=self.extraction_max_image_size_mb,
            cache_enabled=self.extraction_cache_enabled,
            cache_ttl_hours=self.extraction_cache_ttl_hours,
            window_size=self.extraction_window_size,
            window_overlap=self.extraction_window_overlap,
            ppocrv5=PPOCRV5Settings(
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
            ),
            ppstructure=PPStructureSettings(
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
            ),
            hybrid=HybridSettings(
                layout_overlap_threshold=self.hybrid_layout_overlap_threshold,
                center_fallback=self.hybrid_layout_center_fallback,
                save_merged_raw=self.hybrid_save_merged_raw,
            ),
        )

        self.ai_llm = AILLMSettings(
            base_url=self.ai_llm_base_url,
            api_key=self.ai_llm_api_key,
            model=self.ai_llm_model,
            timeout_seconds=self.ai_extraction_timeout_seconds,
        )

        self.matching = MatchingSettings(threshold=self.match_threshold, use_prefilter=self.match_use_prefilter)

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
