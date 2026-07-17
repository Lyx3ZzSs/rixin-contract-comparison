from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from app.config import Settings, settings
from app.infrastructure.atomic_files import atomic_write_json, update_task_manifest
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


class ArtifactPublishCommittedError(RuntimeError):
    """Report a publish whose destination could not be rolled back."""

    def __init__(
        self,
        *,
        destination: Path,
        owner_token: str,
        primary_error: Exception,
        rollback_error: Exception,
    ) -> None:
        super().__init__(
            f"Artifact publish committed at {destination}: primary={primary_error}; rollback={rollback_error}"
        )
        self.destination = destination
        self.owner_token = owner_token
        self.primary_error = primary_error
        self.rollback_error = rollback_error


class ArtifactStore(Protocol):
    def assert_inside_storage(self, path: Path) -> None:
        raise NotImplementedError

    def task_dir(self, area: ArtifactArea, task_id: str, *parts: str) -> Path:
        raise NotImplementedError

    def upload_path(self, task_id: str, label: str, filename: str) -> Path:
        raise NotImplementedError

    def staging_path(self, task_id: str, attempt_id: str, label: str, filename: str) -> Path:
        raise NotImplementedError

    def publish_staged(
        self,
        source: Path,
        destination: Path,
        *,
        owner_token: str = "",
        on_created: Callable[[Path], None] | None = None,
    ) -> Path:
        raise NotImplementedError

    def report_pdf_path(self, task_id: str, report_revision: int = 0) -> Path:
        raise NotImplementedError

    def report_manifest_path(self, task_id: str, report_revision: int = 0) -> Path:
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

    def publish_staged(
        self,
        source: Path,
        destination: Path,
        *,
        owner_token: str = "",
        on_created: Callable[[Path], None] | None = None,
    ) -> Path:
        self.assert_inside_storage(source)
        self.assert_inside_storage(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.hardlink_to(source)
        if on_created is not None:
            try:
                on_created(destination)
            except Exception as callback_error:
                try:
                    destination.unlink(missing_ok=True)
                except OSError as rollback_error:
                    logger.critical(
                        "Published upload callback failed and destination rollback also failed: destination=%s",
                        destination,
                        exc_info=True,
                    )
                    raise ArtifactPublishCommittedError(
                        destination=destination,
                        owner_token=owner_token,
                        primary_error=callback_error,
                        rollback_error=rollback_error,
                    ) from callback_error
                raise
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

    def report_pdf_path(self, task_id: str, report_revision: int = 0) -> Path:
        return self.task_dir("reports", task_id) / f"contract_compare_report-r{report_revision}.pdf"

    def report_manifest_path(self, task_id: str, report_revision: int = 0) -> Path:
        return self.task_dir("reports", task_id) / f"contract_compare_report-r{report_revision}.manifest.json"

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
        atomic_write_json(path, payload)
        self._record_manifest(path, "json")
        return path

    def _artifact_stem(self, value: str) -> str:
        return re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", value)[:80] or "document"

    def _safe_path_part(self, value: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", value or "")
        return "artifact" if sanitized in {"", ".", ".."} else sanitized

    def _record_manifest(self, path: Path, kind: str) -> None:
        try:
            task_root, relative_path = self._task_root_and_relative_path(path)
        except ValueError:
            return
        if relative_path.name == "manifest.json":
            return

        now = datetime.now(UTC).isoformat()
        relative = relative_path.as_posix()
        update_task_manifest(
            task_root / "manifest.json",
            task_id=task_root.name,
            artifact={
                "path": relative,
                "area": relative_path.parts[0] if relative_path.parts else "",
                "kind": kind,
                "updated_at": now,
            },
        )

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
