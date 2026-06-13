from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_ensure_storage_only_precreates_tasks_directory(tmp_path: Path) -> None:
    app_settings = Settings(
        storage_dir=tmp_path / "storage",
        uploads_dir=None,
        tasks_dir=None,
        reports_dir=None,
        ocr_dir=None,
        debug_dir=None,
        cache_dir=None,
    )

    app_settings.ensure_storage()

    assert app_settings.tasks_dir.exists()
    for directory in [
        app_settings.uploads_dir,
        app_settings.reports_dir,
        app_settings.ocr_dir,
        app_settings.debug_dir,
        app_settings.cache_dir,
    ]:
        assert not directory.exists()


def test_compare_defaults_to_strict_structured_ocr() -> None:
    app_settings = Settings()

    assert app_settings.compare_document_extractor == "ppstructure_ocr_hybrid"
    assert app_settings.compare_require_structured_ocr is True


def test_compare_rejects_non_structured_extractor_in_strict_mode() -> None:
    with pytest.raises(ValidationError, match="COMPARE_DOCUMENT_EXTRACTOR"):
        Settings(
            compare_document_extractor="pymupdf",
            compare_require_structured_ocr=True,
        )


def test_compare_allows_non_structured_extractor_when_strict_mode_is_disabled() -> None:
    app_settings = Settings(
        compare_document_extractor="pymupdf",
        compare_require_structured_ocr=False,
    )

    assert app_settings.compare_document_extractor == "pymupdf"


def test_layout_analysis_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError, match="LAYOUT_ANALYSIS_MODE"):
        Settings(layout_analysis_mode="unknown")


def test_layout_analysis_mode_accepts_v3_rollout_modes() -> None:
    assert Settings(layout_analysis_mode="v3_shadow").layout_analysis_mode == "v3_shadow"
    assert Settings(layout_analysis_mode="v3").layout_analysis_mode == "v3"


def test_diff_engine_defaults_to_diff_match_patch() -> None:
    assert Settings().diff_engine == "diff_match_patch"


def test_diff_engine_accepts_difflib_fallback_mode() -> None:
    assert Settings(diff_engine="difflib").diff_engine == "difflib"
    assert Settings(diff_engine="diff-match-patch").diff_engine == "diff_match_patch"


def test_diff_engine_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError, match="DIFF_ENGINE"):
        Settings(diff_engine="unknown")


def test_semantic_matching_config_maps_to_nested_settings() -> None:
    app_settings = Settings(
        match_enable_semantic_match=True,
        match_semantic_provider="openai",
        match_semantic_base_url="http://127.0.0.1:8001/v1",
        match_semantic_api_key="test-key",
        match_semantic_model="bge-small-zh-v1.5",
        match_semantic_device="cpu",
        match_semantic_batch_size=16,
        match_semantic_timeout_seconds=30,
        match_semantic_max_retries=1,
        match_semantic_weight=0.1,
    )

    assert app_settings.matching.enable_semantic_match is True
    assert app_settings.matching.semantic_provider == "openai"
    assert app_settings.matching.semantic_base_url == "http://127.0.0.1:8001/v1"
    assert app_settings.matching.semantic_api_key == "test-key"
    assert app_settings.matching.semantic_model == "bge-small-zh-v1.5"
    assert app_settings.matching.semantic_device == "cpu"
    assert app_settings.matching.semantic_batch_size == 16
    assert app_settings.matching.semantic_timeout_seconds == 30
    assert app_settings.matching.semantic_max_retries == 1
    assert app_settings.matching.semantic_weight == 0.1


def test_semantic_matching_rejects_unknown_provider() -> None:
    with pytest.raises(ValidationError, match="MATCH_SEMANTIC_PROVIDER"):
        Settings(match_semantic_provider="custom")


def test_semantic_matching_rejects_invalid_http_url() -> None:
    with pytest.raises(ValidationError, match="MATCH_SEMANTIC_BASE_URL"):
        Settings(match_semantic_base_url="127.0.0.1:8001/v1")


def test_document_understanding_config_maps_to_nested_settings() -> None:
    app_settings = Settings(
        document_understanding_enabled=True,
        document_understanding_llm_enabled=True,
        document_understanding_llm_base_url="http://127.0.0.1:8002/v1",
        document_understanding_llm_api_key="test-key",
        document_understanding_llm_model="qwen2.5",
        document_understanding_llm_timeout_seconds=20,
        document_understanding_llm_max_retries=1,
        document_understanding_rule_confidence_accept=0.9,
        document_understanding_llm_confidence_accept=0.88,
        document_understanding_enable_ocr_correction=True,
        document_understanding_enable_cross_page_merge=False,
    )

    assert app_settings.document_understanding.enabled is True
    assert app_settings.document_understanding.llm_enabled is True
    assert app_settings.document_understanding.llm_base_url == "http://127.0.0.1:8002/v1"
    assert app_settings.document_understanding.llm_api_key == "test-key"
    assert app_settings.document_understanding.llm_model == "qwen2.5"
    assert app_settings.document_understanding.llm_timeout_seconds == 20
    assert app_settings.document_understanding.llm_max_retries == 1
    assert app_settings.document_understanding.rule_confidence_accept == 0.9
    assert app_settings.document_understanding.llm_confidence_accept == 0.88
    assert app_settings.document_understanding.enable_ocr_correction is True
    assert app_settings.document_understanding.enable_cross_page_merge is False


def test_document_understanding_can_fallback_to_global_ai_llm_config() -> None:
    app_settings = Settings(
        ai_llm_base_url="http://127.0.0.1:8003/v1",
        ai_llm_api_key="global-key",
        ai_llm_model="global-model",
    )

    assert app_settings.document_understanding.llm_base_url == "http://127.0.0.1:8003/v1"
    assert app_settings.document_understanding.llm_api_key == "global-key"
    assert app_settings.document_understanding.llm_model == "global-model"


def test_document_understanding_rejects_invalid_llm_url() -> None:
    with pytest.raises(ValidationError, match="LLM URL"):
        Settings(document_understanding_llm_base_url="127.0.0.1:8002/v1")
