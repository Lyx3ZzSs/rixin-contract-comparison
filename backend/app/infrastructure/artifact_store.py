from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Literal

from app.config import Settings, settings
from app.infrastructure.atomic_files import atomic_write_json
from app.utils.file_utils import FileValidationError

ArtifactArea = Literal["uploads", "reports", "ocr", "debug", "cache", "compare"]


class ArtifactStore:
    """The fixed local file layout for task-owned binary and diagnostic data."""

    _areas = {
        "uploads": "input",
        "reports": "report",
        "ocr": "diagnostics/ocr",
        "debug": "diagnostics/debug",
        "cache": "staging/cache",
        "compare": "staging/compare",
    }

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings

    def assert_inside_storage(self, path: Path) -> None:
        resolved = path.resolve()
        root = self.settings.storage_dir.resolve()
        if root not in [resolved, *resolved.parents]:
            raise FileValidationError("非法文件路径。")

    def task_root(self, task_id: str) -> Path:
        return self.settings.tasks_dir / self._task_id(task_id)

    def task_dir(self, area: ArtifactArea, task_id: str, *parts: str) -> Path:
        path = self.task_root(task_id) / self._areas[area]
        for part in parts:
            path /= self._path_part(part)
        return path

    def upload_path(self, task_id: str, label: str, filename: str) -> Path:
        if label not in {"original", "compare"}:
            raise FileValidationError("非法文件角色。")
        return self.task_root(task_id) / "input" / label / filename

    def staging_path(self, task_id: str, attempt_id: str, label: str, filename: str) -> Path:
        if label not in {"original", "compare"}:
            raise FileValidationError("非法文件角色。")
        return self.task_root(task_id) / "staging" / self._path_part(attempt_id) / label / filename

    def publish_staged(
        self,
        source: Path,
        destination: Path,
        *,
        owner_token: str = "",
        on_created: Callable[[Path], None] | None = None,
    ) -> Path:
        del owner_token
        self.assert_inside_storage(source)
        self.assert_inside_storage(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        if on_created is not None:
            on_created(destination)
        return destination

    def report_pdf_path(self, task_id: str, report_revision: int = 0) -> Path:
        return self.task_root(task_id) / "report" / f"report-r{report_revision}.pdf"

    def raw_json_path(self, task_id: str, source_path: str | Path, suffix: str) -> Path:
        stem = self._artifact_stem(Path(source_path).stem)
        normalized_suffix = self._path_part(suffix).removesuffix(".json")
        return self.task_root(task_id) / "diagnostics" / "ocr" / f"{stem}_{normalized_suffix}.json"

    def debug_json_path(self, task_id: str, filename: str) -> Path:
        return self.task_root(task_id) / "diagnostics" / "debug" / self._path_part(Path(filename).name)

    def write_bytes(self, path: Path, content: bytes) -> Path:
        self.assert_inside_storage(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def write_json(self, path: Path, payload: Any) -> Path:
        self.assert_inside_storage(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, payload)
        return path

    def remove_diagnostics(self, task_id: str) -> None:
        shutil.rmtree(self.task_root(task_id) / "diagnostics", ignore_errors=True)

    def remove_expired_staging(self, task_id: str, *, older_than_seconds: int = 24 * 60 * 60) -> None:
        staging = self.task_root(task_id) / "staging"
        if not staging.exists():
            return
        cutoff = time.time() - older_than_seconds
        for child in staging.iterdir():
            try:
                modified_at = child.stat().st_mtime
            except OSError:
                continue
            if modified_at < cutoff:
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)

    def remove_staging_attempt(self, task_id: str, attempt_id: str) -> None:
        shutil.rmtree(self.task_root(task_id) / "staging" / self._path_part(attempt_id), ignore_errors=True)

    @staticmethod
    def _artifact_stem(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", value)[:80] or "document"

    @staticmethod
    def _path_part(value: str) -> str:
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise FileValidationError("非法文件路径。")
        return value

    @staticmethod
    def _task_id(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", value or "") or value in {".", ".."}:
            raise FileValidationError("非法任务 ID。")
        return value


LocalArtifactStore = ArtifactStore
default_artifact_store = ArtifactStore()
