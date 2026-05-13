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

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    use_mock_llm: bool = os.getenv("USE_MOCK_LLM", "true").lower() in {"1", "true", "yes", "on"}

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
        ]

    def ensure_storage(self) -> None:
        for directory in self.storage_subdirs:
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()

