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

BASE_DIR = Path(__file__).resolve().parents[1]
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
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    base_dir: Path = BASE_DIR
    storage_dir: Path = Field(default_factory=lambda: BASE_DIR / "storage")
    uploads_dir: Path | None = None
    tasks_dir: Path | None = None
    highlighted_dir: Path | None = None
    screenshots_dir: Path | None = None
    reports_dir: Path | None = None
    ocr_dir: Path | None = None
    debug_dir: Path | None = None

    document_extractor: str = "auto"
    pymupdf_min_text_chars: int = Field(default=1, ge=0)
    align_structured_extraction: bool = True
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
    ppstructure_use_seal_recognition: bool = False
    ppstructure_use_region_detection: bool = True
    ppstructure_format_block_content: bool = True
    hybrid_layout_overlap_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    hybrid_layout_center_fallback: bool = True
    hybrid_save_merged_raw: bool = True
    save_ocr_raw_result: bool = True

    match_threshold: int = Field(default=85, ge=0, le=100)
    report_font_path: str = ""
    libreoffice_path: str = ""
    libreoffice_host: str = "127.0.0.1"
    libreoffice_port: int = Field(default=2002, ge=1, le=65535)
    libreoffice_timeout_seconds: int = Field(default=30, ge=5, le=120)

    ai_llm_base_url: str = ""
    ai_llm_api_key: str = ""
    ai_llm_model: str = ""
    ai_extraction_timeout_seconds: int = Field(default=120, ge=1)
    report_max_screenshot_pages: int = Field(default=10, ge=0)

    max_upload_size_mb: int = Field(default=30, ge=1)
    extraction_max_document_size_mb: int = Field(default=60, ge=1)
    extraction_max_image_size_mb: int = Field(default=5, ge=1)
    save_extraction_raw_result: bool = True
    extraction_task_description: str = DEFAULT_EXTRACTION_TASK_DESCRIPTION
    extraction_output_format: str = DEFAULT_EXTRACTION_OUTPUT_FORMAT
    extraction_rules_str: str = DEFAULT_EXTRACTION_RULES_STR
    extraction_few_shot_demo: str = DEFAULT_EXTRACTION_FEW_SHOT_DEMO

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

    @model_validator(mode="after")
    def populate_storage_subdirs(self) -> Settings:
        storage_dir = self.storage_dir
        if self.uploads_dir is None:
            self.uploads_dir = storage_dir / "uploads"
        if self.tasks_dir is None:
            self.tasks_dir = storage_dir / "tasks"
        if self.highlighted_dir is None:
            self.highlighted_dir = storage_dir / "highlighted"
        if self.screenshots_dir is None:
            self.screenshots_dir = storage_dir / "screenshots"
        if self.reports_dir is None:
            self.reports_dir = storage_dir / "reports"
        if self.ocr_dir is None:
            self.ocr_dir = storage_dir / "ocr"
        if self.debug_dir is None:
            self.debug_dir = storage_dir / "debug"
        return self

    @property
    def storage_subdirs(self) -> list[Path]:
        return [
            self.uploads_dir,
            self.tasks_dir,
            self.highlighted_dir,
            self.screenshots_dir,
            self.reports_dir,
            self.ocr_dir,
            self.debug_dir,
        ]

    def ensure_storage(self) -> None:
        for directory in self.storage_subdirs:
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
