from __future__ import annotations

from pathlib import Path

from app.config import Settings, settings
from app.utils.file_utils import FileValidationError


class LocalArtifactStore:
    """Resolves and guards local task artifacts under the configured storage root."""

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings

    def assert_inside_storage(self, path: Path) -> None:
        resolved_path = path.resolve()
        storage = self.settings.storage_dir.resolve()
        if storage not in [resolved_path, *resolved_path.parents]:
            raise FileValidationError("非法文件路径。")

    def compare_artifact_urls(
        self,
        task_id: str,
        *,
        completed: bool,
        has_report: bool,
        has_highlights: bool,
    ) -> dict[str, str]:
        return {
            "report_url": f"/api/compare/{task_id}/report" if completed and has_report else "",
            "original_highlight_pdf_url": f"/api/compare/{task_id}/highlight/original" if has_highlights else "",
            "compare_highlight_pdf_url": f"/api/compare/{task_id}/highlight/compare" if has_highlights else "",
        }


default_artifact_store = LocalArtifactStore()
