from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError as PydanticValidationError

from app.config import Settings, settings
from app.errors import TaskRepositoryReadError
from app.infrastructure.atomic_files import atomic_write_json, update_task_manifest
from app.models import CompareTask, OcrRawResultPaths

logger = logging.getLogger(__name__)


class TaskRepository(Protocol):
    def save_compare_task(self, task: CompareTask) -> Path | None:
        raise NotImplementedError

    def load_compare_task(self, task_id: str) -> CompareTask:
        raise NotImplementedError

    def list_compare_tasks(self) -> list[CompareTask]:
        raise NotImplementedError

    def update_compare_task(self, task_id: str, mutate: Callable[[CompareTask], None]) -> CompareTask:
        raise NotImplementedError


def to_jsonable(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model.dict()


class LocalJsonTaskRepository:
    """MinerU-style local file task store.

    Each task owns a directory under ``storage/tasks/{task_id}``. The complete
    task payload lives in ``task.json`` and task artifacts live beside it.
    """

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings
        self._lock = threading.RLock()

    def save_compare_task(self, task: CompareTask) -> Path:
        data = self._normalize_compare_payload_for_storage(task.task_id, to_jsonable(task))
        return self._write_task(task.task_id, self._stamped_payload(task.task_id, data))

    def load_compare_task(self, task_id: str) -> CompareTask:
        try:
            data = self._read_task_data(task_id)
            if not isinstance(data, dict):
                cause = TypeError(f"expected JSON object, got {type(data).__name__}")
                raise TaskRepositoryReadError(f"任务 {task_id} 的持久化数据无法读取或校验。") from cause
            if data.get("task_type") == "extraction":
                raise FileNotFoundError(f"任务 {task_id} 不是对比任务。")
            data = self._hydrate_compare_payload(task_id, data)
            return CompareTask(**data)
        except FileNotFoundError:
            raise
        except (OSError, json.JSONDecodeError, PydanticValidationError, UnicodeError) as exc:
            raise TaskRepositoryReadError(f"任务 {task_id} 的持久化数据无法读取或校验。") from exc

    def list_compare_tasks(self) -> list[CompareTask]:
        tasks: list[CompareTask] = []
        for data in self._iter_task_data():
            if data.get("task_type") == "extraction":
                continue
            try:
                task_id = str(data.get("task_id") or "")
                if task_id:
                    data = self._hydrate_compare_payload(task_id, data)
                tasks.append(CompareTask(**data))
            except (ValueError, TypeError):
                continue
        return sorted(tasks, key=lambda task: task.created_at or task.updated_at, reverse=True)

    def update_compare_task(self, task_id: str, mutate: Callable[[CompareTask], None]) -> CompareTask:
        with self._lock:
            task = self.load_compare_task(task_id)
            mutate(task)
            data = self._normalize_compare_payload_for_storage(task.task_id, to_jsonable(task))
            data = self._stamped_payload(task.task_id, data)
            committed = CompareTask(**self._hydrate_compare_payload(task.task_id, data))
            serialized = self._normalize_compare_payload_for_storage(task.task_id, to_jsonable(committed))
            self._write_task(task.task_id, serialized)
            return committed.model_copy(deep=True)

    def task_json_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "task.json"

    def task_dir(self, task_id: str) -> Path:
        return self.settings.tasks_dir / self._safe_task_id(task_id)

    def _write_task(self, task_id: str, data: dict[str, Any]) -> Path:
        with self._lock:
            self.settings.tasks_dir.mkdir(parents=True, exist_ok=True)
            path = self.task_json_path(task_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            # task.json is authoritative; manifest.json is a derived index.
            atomic_write_json(path, data)
            try:
                self._write_manifest(task_id, data)
            except OSError:
                logger.warning(
                    "Task manifest refresh failed after authoritative task commit: task_id=%s",
                    task_id,
                    exc_info=True,
                )
            return path

    def _stamped_payload(self, task_id: str, data: dict[str, Any]) -> dict[str, Any]:
        current_revision = 0
        try:
            current_revision = int(self._read_task_data(task_id).get("revision") or 0)
        except (FileNotFoundError, TypeError, ValueError):
            current_revision = int(data.get("revision") or 0)
        data["schema_version"] = int(data.get("schema_version") or 1)
        data["revision"] = current_revision + 1
        data["updated_at"] = datetime.now(UTC).isoformat()
        return data

    def _normalize_compare_payload_for_storage(self, task_id: str, data: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(data)
        normalized["schema_version"] = max(2, int(normalized.get("schema_version") or 1))
        normalized["diff_count"] = len(normalized.get("diffs") or [])
        self._coerce_empty_optional_paths(normalized)

        for field in self._compare_path_fields():
            normalized[field] = self._to_task_relative_path(task_id, normalized.get(field))
        for field in self._required_compare_path_fields():
            if normalized.get(field) is None:
                normalized[field] = ""

        debug_paths = normalized.get("debug_artifact_paths")
        if isinstance(debug_paths, dict):
            normalized["debug_artifact_paths"] = {
                str(name): self._to_task_relative_path(task_id, value) or "" for name, value in debug_paths.items()
            }

        raw_paths = normalized.get("ocr_raw_result_paths")
        if not raw_paths and normalized.get("ocr_raw_result_path"):
            raw_paths = OcrRawResultPaths.from_legacy_value(normalized.get("ocr_raw_result_path")).model_dump(
                mode="json"
            )
        normalized["ocr_raw_result_paths"] = self._normalize_ocr_raw_result_paths(task_id, raw_paths)
        normalized["ocr_raw_result_path"] = ""
        return normalized

    def _hydrate_compare_payload(self, task_id: str, data: dict[str, Any]) -> dict[str, Any]:
        hydrated = dict(data)
        self._coerce_empty_optional_paths(hydrated)

        for field in self._compare_path_fields():
            hydrated[field] = self._to_task_absolute_path(task_id, hydrated.get(field))
        for field in self._required_compare_path_fields():
            if hydrated.get(field) is None:
                hydrated[field] = ""

        debug_paths = hydrated.get("debug_artifact_paths")
        if isinstance(debug_paths, dict):
            hydrated["debug_artifact_paths"] = {
                str(name): self._to_task_absolute_path(task_id, value) or "" for name, value in debug_paths.items()
            }

        raw_paths = hydrated.get("ocr_raw_result_paths")
        if not raw_paths and hydrated.get("ocr_raw_result_path"):
            raw_paths = OcrRawResultPaths.from_legacy_value(hydrated.get("ocr_raw_result_path")).model_dump(mode="json")
        hydrated["ocr_raw_result_paths"] = self._hydrate_ocr_raw_result_paths(task_id, raw_paths)
        return hydrated

    def _normalize_ocr_raw_result_paths(self, task_id: str, raw_paths: Any) -> dict[str, Any]:
        paths = OcrRawResultPaths.from_legacy_value(raw_paths).model_dump(mode="json")
        for side in ["original", "compare"]:
            side_paths = paths.get(side) or {}
            for kind, value in list(side_paths.items()):
                side_paths[kind] = self._to_task_relative_path(task_id, value)
        return paths

    def _hydrate_ocr_raw_result_paths(self, task_id: str, raw_paths: Any) -> dict[str, Any]:
        paths = OcrRawResultPaths.from_legacy_value(raw_paths).model_dump(mode="json")
        for side in ["original", "compare"]:
            side_paths = paths.get(side) or {}
            for kind, value in list(side_paths.items()):
                side_paths[kind] = self._to_task_absolute_path(task_id, value)
        return paths

    def _to_task_relative_path(self, task_id: str, value: Any) -> str | None:
        if value in (None, ""):
            return None
        path = Path(str(value))
        if not path.is_absolute():
            return path.as_posix()
        try:
            return path.resolve().relative_to(self.task_dir(task_id).resolve()).as_posix()
        except ValueError:
            return str(path)

    def _to_task_absolute_path(self, task_id: str, value: Any) -> str | None:
        if value in (None, ""):
            return None
        path = Path(str(value))
        if path.is_absolute():
            return str(path)
        return str(self.task_dir(task_id) / path)

    def _coerce_empty_optional_paths(self, data: dict[str, Any]) -> None:
        for field in [
            "original_highlight_pdf_path",
            "compare_highlight_pdf_path",
            "report_pdf_path",
        ]:
            if data.get(field) == "":
                data[field] = None

    def _compare_path_fields(self) -> list[str]:
        return [
            "original_pdf_path",
            "compare_pdf_path",
            "original_highlight_pdf_path",
            "compare_highlight_pdf_path",
            "report_pdf_path",
        ]

    def _required_compare_path_fields(self) -> list[str]:
        return [
            "original_pdf_path",
            "compare_pdf_path",
        ]

    def _read_task_data(self, task_id: str) -> dict[str, Any]:
        path = self.task_json_path(task_id)
        if not path.exists():
            raise FileNotFoundError(f"任务不存在: {task_id}")
        with self._lock:
            return json.loads(path.read_text(encoding="utf-8"))

    def _iter_task_data(self) -> list[dict[str, Any]]:
        if not self.settings.tasks_dir.exists():
            return []

        items: list[dict[str, Any]] = []
        for path in self.settings.tasks_dir.glob("*/task.json"):
            try:
                with self._lock:
                    items.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                continue
        return items

    def _write_manifest(self, task_id: str, data: dict[str, Any]) -> None:
        task_dir = self.task_dir(task_id)
        now = datetime.now(UTC).isoformat()
        update_task_manifest(
            task_dir / "manifest.json",
            task_id=self._safe_task_id(task_id),
            fields={
                "task_type": data.get("task_type") or "compare",
                "status": data.get("status", ""),
                "stage": data.get("stage", ""),
            },
            artifact={
                "path": "task.json",
                "area": "metadata",
                "kind": "json",
                "updated_at": now,
            },
        )

    def _safe_task_id(self, task_id: str) -> str:
        sanitized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in task_id)
        return sanitized or "task"


def build_task_repository(app_settings: Settings = settings) -> TaskRepository:
    return LocalJsonTaskRepository(app_settings)


class LazyDefaultTaskRepository:
    """Defers repository initialization until runtime configuration is available."""

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings
        self._lock = threading.RLock()
        self._repository: TaskRepository | None = None

    def resolve(self) -> TaskRepository:
        with self._lock:
            if self._repository is None:
                self._repository = build_task_repository(self.settings)
            return self._repository

    def save_compare_task(self, task: CompareTask) -> Path | None:
        return self.resolve().save_compare_task(task)

    def load_compare_task(self, task_id: str) -> CompareTask:
        return self.resolve().load_compare_task(task_id)

    def list_compare_tasks(self) -> list[CompareTask]:
        return self.resolve().list_compare_tasks()

    def update_compare_task(self, task_id: str, mutate: Callable[[CompareTask], None]) -> CompareTask:
        return self.resolve().update_compare_task(task_id, mutate)


default_task_repository = LazyDefaultTaskRepository()
