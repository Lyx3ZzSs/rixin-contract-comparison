from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


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
    paddleocr_access_token: str = os.getenv("PADDLEOCR_ACCESS_TOKEN", "")
    paddleocr_model: str = os.getenv("PADDLEOCR_MODEL", "PP-OCRv5")
    paddleocr_job_url: str = os.getenv("PADDLEOCR_JOB_URL", "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs")
    paddleocr_timeout_seconds: int = int(os.getenv("PADDLEOCR_TIMEOUT_SECONDS", "120"))
    paddleocr_poll_interval_seconds: float = float(os.getenv("PADDLEOCR_POLL_INTERVAL_SECONDS", "2"))
    paddleocr_max_wait_seconds: int = int(os.getenv("PADDLEOCR_MAX_WAIT_SECONDS", "300"))
    save_ocr_raw_result: bool = os.getenv("SAVE_OCR_RAW_RESULT", "true").lower() in {"1", "true", "yes", "on"}

    match_threshold: int = int(os.getenv("MATCH_THRESHOLD", "85"))
    report_font_path: str = os.getenv("REPORT_FONT_PATH", "")

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
