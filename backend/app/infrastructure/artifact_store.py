from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, Protocol

from app.config import Settings, settings
from app.utils.file_utils import FileValidationError

ArtifactArea = Literal[
    "uploads",
    "tasks",
    "reports",
    "ocr",
    "debug",
    "task_jobs",
]


class ArtifactStore(Protocol):
    def assert_inside_storage(self, path: Path) -> None:
        raise NotImplementedError

    def task_dir(self, area: ArtifactArea, task_id: str, *parts: str) -> Path:
        raise NotImplementedError

    def upload_path(self, task_id: str, label: str, filename: str) -> Path:
        raise NotImplementedError

    def report_pdf_path(self, task_id: str) -> Path:
        raise NotImplementedError

    def raw_json_path(self, task_id: str, source_path: str | Path, suffix: str) -> Path:
        raise NotImplementedError

    def debug_json_path(self, task_id: str, filename: str) -> Path:
        raise NotImplementedError

    def write_bytes(self, path: Path, content: bytes) -> Path:
        raise NotImplementedError

    def write_json(self, path: Path, payload: Any) -> Path:
        raise NotImplementedError


class LocalArtifactStore:
    """Resolves and guards local task artifacts under the configured storage root."""

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings

    def assert_inside_storage(self, path: Path) -> None:
        resolved_path = path.resolve()
        storage = self.settings.storage_dir.resolve()
        if storage not in [resolved_path, *resolved_path.parents]:
            raise FileValidationError("非法文件路径。")

    def task_dir(self, area: ArtifactArea, task_id: str, *parts: str) -> Path:
        base = self._area_root(area)
        path = base / self._safe_path_part(task_id)
        for part in parts:
            path = path / self._safe_path_part(part)
        return path

    def upload_path(self, task_id: str, label: str, filename: str) -> Path:
        return self.task_dir("uploads", task_id) / f"{self._safe_path_part(label)}_{Path(filename).name}"

    def report_pdf_path(self, task_id: str) -> Path:
        return self.task_dir("reports", task_id) / "contract_compare_report.pdf"

    def raw_json_path(self, task_id: str, source_path: str | Path, suffix: str) -> Path:
        stem = self._artifact_stem(Path(source_path).stem)
        normalized_suffix = self._safe_path_part(suffix).removesuffix(".json")
        return self.task_dir("ocr", task_id) / f"{stem}_{normalized_suffix}.json"

    def debug_json_path(self, task_id: str, filename: str) -> Path:
        return self.task_dir("debug", task_id) / Path(filename).name

    def write_bytes(self, path: Path, content: bytes) -> Path:
        self.assert_inside_storage(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def write_json(self, path: Path, payload: Any) -> Path:
        self.assert_inside_storage(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _area_root(self, area: ArtifactArea) -> Path:
        roots: dict[ArtifactArea, Path] = {
            "uploads": self.settings.uploads_dir,
            "tasks": self.settings.tasks_dir,
            "reports": self.settings.reports_dir,
            "ocr": self.settings.ocr_dir,
            "debug": self.settings.debug_dir,
            "task_jobs": self.settings.task_jobs_dir,
        }
        return roots[area]

    def _artifact_stem(self, value: str) -> str:
        return re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", value)[:80] or "document"

    def _safe_path_part(self, value: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", value or "")
        return sanitized or "artifact"


default_artifact_store = LocalArtifactStore()
