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
    paddleocr_version: str = os.getenv("PADDLEOCR_VERSION", os.getenv("PADDLEOCR_MODEL", "PP-OCRv5"))
    paddleocr_device: str = os.getenv("PADDLEOCR_DEVICE", "cpu")
    paddleocr_return_word_box: bool = _env_bool("PADDLEOCR_RETURN_WORD_BOX", "true")
    paddleocr_text_rec_score_thresh: float = float(os.getenv("PADDLEOCR_TEXT_REC_SCORE_THRESH", "0.0"))
    paddleocr_use_doc_orientation_classify: bool = _env_bool("PADDLEOCR_USE_DOC_ORIENTATION_CLASSIFY", "false")
    paddleocr_use_doc_unwarping: bool = _env_bool("PADDLEOCR_USE_DOC_UNWARPING", "false")
    paddleocr_use_textline_orientation: bool = _env_bool("PADDLEOCR_USE_TEXTLINE_ORIENTATION", "false")
    save_ocr_raw_result: bool = _env_bool("SAVE_OCR_RAW_RESULT", "true")

    paddleocr_vl_url: str = os.getenv("PADDLEOCR_VL_URL", "")
    paddleocr_vl_token: str = os.getenv("PADDLEOCR_VL_TOKEN", "")
    paddleocr_vl_model: str = os.getenv("PADDLEOCR_VL_MODEL", "PaddleOCR-VL-1.5")
    paddleocr_vl_api_mode: str = os.getenv("PADDLEOCR_VL_API_MODE", "auto").lower()
    paddleocr_vl_timeout_seconds: int = int(os.getenv("PADDLEOCR_VL_TIMEOUT_SECONDS", "60"))
    paddleocr_vl_poll_interval_seconds: float = float(os.getenv("PADDLEOCR_VL_POLL_INTERVAL_SECONDS", "2"))
    paddleocr_vl_max_wait_seconds: int = int(os.getenv("PADDLEOCR_VL_MAX_WAIT_SECONDS", "300"))
    paddleocr_vl_prompt: str = os.getenv(
        "PADDLEOCR_VL_PROMPT",
        "请识别图片中的合同文本，按阅读顺序输出 JSON 数组，每项包含 text、bbox 和 confidence。",
    )

    match_threshold: int = int(os.getenv("MATCH_THRESHOLD", "85"))
    report_font_path: str = os.getenv("REPORT_FONT_PATH", "")

    ai_llm_base_url: str = os.getenv("AI_LLM_BASE_URL", "")
    ai_llm_api_key: str = os.getenv("AI_LLM_API_KEY", "")
    ai_llm_model: str = os.getenv("AI_LLM_MODEL", "")
    ai_analysis_timeout_seconds: int = int(os.getenv("AI_ANALYSIS_TIMEOUT_SECONDS", "30"))
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
