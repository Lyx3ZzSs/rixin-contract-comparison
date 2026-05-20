from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "on"}


class Settings:
    base_dir: Path = Path(__file__).resolve().parents[1]
    storage_dir: Path = Path(os.getenv("STORAGE_DIR", base_dir / "storage"))
    uploads_dir: Path = storage_dir / "uploads"
    tasks_dir: Path = storage_dir / "tasks"
    highlighted_dir: Path = storage_dir / "highlighted"
    screenshots_dir: Path = storage_dir / "screenshots"
    reports_dir: Path = storage_dir / "reports"
    ocr_dir: Path = storage_dir / "ocr"

    document_extractor: str = os.getenv("DOCUMENT_EXTRACTOR", "auto").lower()
    pymupdf_min_text_chars: int = int(os.getenv("PYMUPDF_MIN_TEXT_CHARS", "1"))
    paddleocr_vl_url: str = os.getenv(
        "PADDLEOCR_VL_URL",
        "https://u1013288-8b13-35b9cafe.westc.seetacloud.com:8443/",
    )
    paddleocr_vl_access_token: str = os.getenv("PADDLEOCR_VL_ACCESS_TOKEN", "")
    paddleocr_vl_timeout_seconds: int = int(os.getenv("PADDLEOCR_VL_TIMEOUT_SECONDS", "600"))
    paddleocr_vl_page_mode: bool = _env_bool("PADDLEOCR_VL_PAGE_MODE", "true")
    paddleocr_vl_retry_count: int = int(os.getenv("PADDLEOCR_VL_RETRY_COUNT", "1"))
    paddleocr_vl_use_doc_orientation_classify: bool = _env_bool("PADDLEOCR_VL_USE_DOC_ORIENTATION_CLASSIFY", "false")
    paddleocr_vl_use_doc_unwarping: bool = _env_bool("PADDLEOCR_VL_USE_DOC_UNWARPING", "false")
    paddleocr_vl_use_layout_detection: bool = _env_bool("PADDLEOCR_VL_USE_LAYOUT_DETECTION", "true")
    paddleocr_vl_use_chart_recognition: bool = _env_bool("PADDLEOCR_VL_USE_CHART_RECOGNITION", "false")
    paddleocr_vl_use_seal_recognition: bool = _env_bool("PADDLEOCR_VL_USE_SEAL_RECOGNITION", "false")
    paddleocr_vl_use_ocr_for_image_block: bool = _env_bool("PADDLEOCR_VL_USE_OCR_FOR_IMAGE_BLOCK", "true")
    paddleocr_vl_format_block_content: bool = _env_bool("PADDLEOCR_VL_FORMAT_BLOCK_CONTENT", "true")
    paddleocr_vl_merge_layout_blocks: bool = _env_bool("PADDLEOCR_VL_MERGE_LAYOUT_BLOCKS", "true")
    paddleocr_vl_prettify_markdown: bool = _env_bool("PADDLEOCR_VL_PRETTIFY_MARKDOWN", "false")
    paddleocr_vl_max_pixels: int = int(os.getenv("PADDLEOCR_VL_MAX_PIXELS", "0"))
    paddleocr_vl_max_new_tokens: int = int(os.getenv("PADDLEOCR_VL_MAX_NEW_TOKENS", "0"))
    ppocrv5_url: str = os.getenv("PPOCRV5_URL", "")
    ppocrv5_access_token: str = os.getenv("PPOCRV5_ACCESS_TOKEN", "")
    ppocrv5_timeout_seconds: int = int(os.getenv("PPOCRV5_TIMEOUT_SECONDS", "600"))
    ppocrv5_return_word_box: bool = _env_bool("PPOCRV5_RETURN_WORD_BOX", "true")
    ppocrv5_text_rec_score_thresh: float = float(os.getenv("PPOCRV5_TEXT_REC_SCORE_THRESH", "0.0"))
    ppocrv5_use_doc_orientation_classify: bool = _env_bool("PPOCRV5_USE_DOC_ORIENTATION_CLASSIFY", "false")
    ppocrv5_use_doc_unwarping: bool = _env_bool("PPOCRV5_USE_DOC_UNWARPING", "false")
    ppocrv5_use_textline_orientation: bool = _env_bool("PPOCRV5_USE_TEXTLINE_ORIENTATION", "false")
    hybrid_layout_overlap_threshold: float = float(os.getenv("HYBRID_LAYOUT_OVERLAP_THRESHOLD", "0.5"))
    hybrid_layout_center_fallback: bool = _env_bool("HYBRID_LAYOUT_CENTER_FALLBACK", "true")
    hybrid_save_merged_raw: bool = _env_bool("HYBRID_SAVE_MERGED_RAW", "true")
    save_ocr_raw_result: bool = _env_bool("SAVE_OCR_RAW_RESULT", "true")

    match_threshold: int = int(os.getenv("MATCH_THRESHOLD", "85"))
    report_font_path: str = os.getenv("REPORT_FONT_PATH", "")

    ai_llm_base_url: str = os.getenv("AI_LLM_BASE_URL", "")
    ai_llm_api_key: str = os.getenv("AI_LLM_API_KEY", "")
    ai_llm_model: str = os.getenv("AI_LLM_MODEL", "")
    ai_analysis_timeout_seconds: int = int(os.getenv("AI_ANALYSIS_TIMEOUT_SECONDS", "30"))
    ai_extraction_timeout_seconds: int = int(os.getenv("AI_EXTRACTION_TIMEOUT_SECONDS", "120"))
    ai_system_prompt: str = os.getenv(
        "AI_SYSTEM_PROMPT",
        (
            "你是一名资深合同审查专家，擅长识别合同版本差异中的法律、财务、履约和商业风险。"
            "请基于原文、新文和差异摘要进行审计分析，输出严格 JSON，不要输出 Markdown。"
            "JSON 字段必须包含 risk_level、risk_score、contract_element、change_summary、"
            "risk_explanation、review_suggestion。risk_level 只能是 LOW、MEDIUM、HIGH，"
            "risk_score 为 0 到 100 的整数。重点关注付款、金额、数量、期限、交付、验收、"
            "质量、违约责任、解除、争议解决、保密、主体信息等合同风险。"
        ),
    )
    report_max_screenshot_pages: int = int(os.getenv("REPORT_MAX_SCREENSHOT_PAGES", "10"))

    max_upload_size_mb: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "30"))

    @property
    def storage_subdirs(self) -> list[Path]:
        return [
            self.uploads_dir,
            self.tasks_dir,
            self.highlighted_dir,
            self.screenshots_dir,
            self.reports_dir,
            self.ocr_dir,
        ]

    def ensure_storage(self) -> None:
        for directory in self.storage_subdirs:
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
