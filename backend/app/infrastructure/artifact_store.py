from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from app.config import Settings, settings
from app.utils.file_utils import FileValidationError

logger = logging.getLogger(__name__)

ArtifactArea = Literal[
    "uploads",
    "reports",
    "ocr",
    "debug",
    "cache",
    "compare",
]


class ArtifactStore(Protocol):
    def assert_inside_storage(self, path: Path) -> None:
        raise NotImplementedError

    def task_dir(self, area: ArtifactArea, task_id: str, *parts: str) -> Path:
        raise NotImplementedError

    def upload_path(self, task_id: str, label: str, filename: str) -> Path:
        raise NotImplementedError

    def staging_path(self, task_id: str, attempt_id: str, label: str, filename: str) -> Path:
        raise NotImplementedError

    def publish_staged(self, source: Path, destination: Path) -> Path:
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
    """Resolves and guards MinerU-style local task artifacts."""

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings

    def assert_inside_storage(self, path: Path) -> None:
        resolved_path = path.resolve()
        storage = self.settings.storage_dir.resolve()
        if storage not in [resolved_path, *resolved_path.parents]:
            raise FileValidationError("非法文件路径。")

    def task_dir(self, area: ArtifactArea, task_id: str, *parts: str) -> Path:
        path = self.task_root(task_id) / area
        for part in parts:
            path = path / self._safe_path_part(part)
        return path

    def task_root(self, task_id: str) -> Path:
        return self.settings.tasks_dir / self._safe_path_part(task_id)

    def upload_path(self, task_id: str, label: str, filename: str) -> Path:
        return self.task_dir("uploads", task_id) / f"{self._safe_path_part(label)}_{Path(filename).name}"

    def staging_path(self, task_id: str, attempt_id: str, label: str, filename: str) -> Path:
        return (
            self.task_root(task_id)
            / "staging"
            / self._safe_path_part(attempt_id)
            / (f"{self._safe_path_part(label)}_{Path(filename).name}")
        )

    def publish_staged(self, source: Path, destination: Path) -> Path:
        self.assert_inside_storage(source)
        self.assert_inside_storage(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.hardlink_to(source)
        try:
            source.unlink()
        except OSError:
            logger.error(
                "Published upload but staging cleanup failed: source=%s destination=%s",
                source,
                destination,
                exc_info=True,
            )
        try:
            self._record_manifest(destination, "bytes")
        except OSError:
            logger.warning(
                "Published upload but derived manifest refresh failed: destination=%s",
                destination,
                exc_info=True,
            )
        return destination

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
        self._record_manifest(path, "bytes")
        return path

    def write_json(self, path: Path, payload: Any) -> Path:
        self.assert_inside_storage(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._record_manifest(path, "json")
        return path

    def _artifact_stem(self, value: str) -> str:
        return re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", value)[:80] or "document"

    def _safe_path_part(self, value: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", value or "")
        return sanitized or "artifact"

    def _record_manifest(self, path: Path, kind: str) -> None:
        try:
            task_root, relative_path = self._task_root_and_relative_path(path)
        except ValueError:
            return
        if relative_path.name == "manifest.json":
            return

        manifest_path = task_root / "manifest.json"
        now = datetime.now(UTC).isoformat()
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                manifest = {}
        else:
            manifest = {}

        artifacts = {
            str(item.get("path")): item
            for item in manifest.get("artifacts", [])
            if isinstance(item, dict) and item.get("path")
        }
        relative = relative_path.as_posix()
        artifacts[relative] = {
            "path": relative,
            "area": relative_path.parts[0] if relative_path.parts else "",
            "kind": kind,
            "updated_at": now,
        }
        manifest.update(
            {
                "task_id": task_root.name,
                "updated_at": now,
                "artifacts": sorted(artifacts.values(), key=lambda item: str(item["path"])),
            }
        )
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def _task_root_and_relative_path(self, path: Path) -> tuple[Path, Path]:
        resolved = path.resolve()
        tasks_root = self.settings.tasks_dir.resolve()
        relative_to_tasks = resolved.relative_to(tasks_root)
        if len(relative_to_tasks.parts) < 2:
            raise ValueError("path is not inside a task directory")
        task_root = tasks_root / relative_to_tasks.parts[0]
        relative_path = Path(*relative_to_tasks.parts[1:])
        return task_root, relative_path


default_artifact_store = LocalArtifactStore()
