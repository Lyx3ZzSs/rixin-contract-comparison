"""Hierarchical configuration models for per-model and per-domain settings.

These nested models compose into the main ``Settings`` class in ``config.py``.
All fields have defaults so existing ``.env`` files work without changes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Per-model configuration (inspired by MinerU's per-model config layer)
# ---------------------------------------------------------------------------

class ModelConfig(BaseModel):
    """Configuration for a single model (local or remote)."""

    enabled: bool = True
    device: str = "auto"  # "auto" | "cuda" | "cpu" | "mps"
    batch_size: int = Field(default=8, ge=1)
    timeout_seconds: int = Field(default=600, ge=1)
    max_retries: int = Field(default=3, ge=0)
    model_path: str | None = None  # Local model weights path
    api_url: str = ""  # Remote API endpoint
    api_key: str = ""  # Auth token / API key

    @field_validator("api_url")
    @classmethod
    def validate_api_url(cls, value: str) -> str:
        url = value.strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("api_url must start with http:// or https://")
        return url


# ---------------------------------------------------------------------------
# Extraction domain
# ---------------------------------------------------------------------------

class HybridSettings(BaseModel):
    """Hybrid extractor (PPStructure + PPOCRv5) settings."""

    layout_overlap_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    center_fallback: bool = True
    save_merged_raw: bool = True


class PPOCRV5Settings(BaseModel):
    """PPOCRv5 OCR model settings."""

    url: str = ""
    access_token: str = ""
    timeout_seconds: int = Field(default=600, ge=1)
    return_word_box: bool = True
    text_rec_score_thresh: float = Field(default=0.0, ge=0.0, le=1.0)
    edge_noise_score_thresh: float = Field(default=0.30, ge=0.0, le=1.0)
    edge_noise_margin_ratio: float = Field(default=0.02, ge=0.0, le=1.0)
    edge_noise_max_chars: int = Field(default=2, ge=0)
    use_doc_orientation_classify: bool = False
    use_doc_unwarping: bool = False
    use_textline_orientation: bool = False


class PPStructureSettings(BaseModel):
    """PPStructure layout parsing model settings."""

    url: str = ""
    access_token: str = ""
    timeout_seconds: int = Field(default=600, ge=1)
    use_doc_orientation_classify: bool = False
    use_doc_unwarping: bool = False
    use_textline_orientation: bool = False
    use_table_recognition: bool = True
    use_seal_recognition: bool = True
    use_region_detection: bool = True
    format_block_content: bool = True


# ---------------------------------------------------------------------------
# Document understanding domain
# ---------------------------------------------------------------------------

class DocumentUnderstandingSettings(BaseModel):
    """Rule-based document semantic cleanup settings."""

    enabled: bool = True
    rule_confidence_accept: float = Field(default=0.85, ge=0.0, le=1.0)

# ---------------------------------------------------------------------------
# Matching domain
# ---------------------------------------------------------------------------

class MatchingSettings(BaseModel):
    """Clause matching settings."""

    threshold: int = Field(default=85, ge=0, le=100)
    use_prefilter: bool = True
    assignment_strategy: str = "greedy"
    enable_semantic_match: bool = False
    semantic_provider: str = "local"
    semantic_model_path: str = ""
    semantic_base_url: str = ""
    semantic_api_key: str = ""
    semantic_model: str = ""
    semantic_device: str = "auto"
    semantic_batch_size: int = Field(default=32, ge=1)
    semantic_timeout_seconds: int = Field(default=60, ge=1)
    semantic_max_retries: int = Field(default=2, ge=0)
    semantic_weight: float = Field(default=0.08, ge=0.0, le=0.3)
    enable_rerank: bool = False
    rerank_base_url: str = ""
    rerank_api_key: str = ""
    rerank_model: str = ""
    rerank_top_k: int = Field(default=30, ge=1, le=50)
    rerank_timeout_seconds: int = Field(default=30, ge=1)
    rerank_max_retries: int = Field(default=1, ge=0)
    rerank_weight: float = Field(default=0.12, ge=0.0, le=0.5)
    low_confidence_review_threshold: float = Field(default=78.0, ge=0.0, le=100.0)

    @field_validator("assignment_strategy")
    @classmethod
    def validate_assignment_strategy(cls, value: str) -> str:
        strategy = str(value or "greedy").strip().lower()
        if strategy not in {"greedy", "optimal"}:
            raise ValueError("assignment_strategy must be one of: greedy, optimal")
        return strategy

    @field_validator("semantic_provider")
    @classmethod
    def validate_semantic_provider(cls, value: str) -> str:
        provider = str(value or "local").strip().lower()
        if provider not in {"local", "openai"}:
            raise ValueError("semantic_provider must be one of: local, openai")
        return provider

    @field_validator("semantic_base_url")
    @classmethod
    def validate_semantic_base_url(cls, value: str) -> str:
        url = value.strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("semantic_base_url must start with http:// or https://")
        return url

    @field_validator("rerank_base_url")
    @classmethod
    def validate_rerank_base_url(cls, value: str) -> str:
        url = value.strip()
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("rerank_base_url must start with http:// or https://")
        return url


# ---------------------------------------------------------------------------
# Report / output domain
# ---------------------------------------------------------------------------

class ReportSettings(BaseModel):
    """PDF report and LibreOffice conversion settings."""

    font_path: str = ""
    libreoffice_path: str = ""
    libreoffice_host: str = "127.0.0.1"
    libreoffice_port: int = Field(default=2002, ge=1, le=65535)
    libreoffice_timeout_seconds: int = Field(default=30, ge=5, le=120)


# ---------------------------------------------------------------------------
# Pipeline / infrastructure domain
# ---------------------------------------------------------------------------

class PipelineSettings(BaseModel):
    """Task runner and pipeline infrastructure settings."""

    task_runner_max_workers: int = Field(default=2, ge=1, le=16)
    task_runner_max_attempts: int = Field(default=1, ge=1, le=5)
    task_runner_lease_seconds: int = Field(default=3600, ge=30)
    task_runner_retry_delay_seconds: float = Field(default=2.0, ge=0)
    task_runner_poll_interval_seconds: float = Field(default=0.25, ge=0.01)


class RegistrySettings(BaseModel):
    """Model registry memory management settings."""

    max_loaded_models: int = Field(default=0, ge=0)
    preload_models: list[str] = Field(default_factory=list)
    eviction_policy: str = "lru"
