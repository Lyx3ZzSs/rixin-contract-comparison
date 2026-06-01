from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings, settings
from app.models import CompareTask
from app.models_extraction import ExtractionTask


class TaskRepository(Protocol):
    def save_compare_task(self, task: CompareTask) -> Path | None:
        raise NotImplementedError

    def load_compare_task(self, task_id: str) -> CompareTask:
        raise NotImplementedError

    def list_compare_tasks(self) -> list[CompareTask]:
        raise NotImplementedError

    def update_compare_task(self, task_id: str, mutate: Callable[[CompareTask], None]) -> CompareTask:
        raise NotImplementedError

    def save_extraction_task(self, task: ExtractionTask) -> Path | None:
        raise NotImplementedError

    def load_extraction_task(self, task_id: str) -> ExtractionTask:
        raise NotImplementedError

    def list_extraction_tasks(self) -> list[ExtractionTask]:
        raise NotImplementedError

    def update_extraction_task(self, task_id: str, mutate: Callable[[ExtractionTask], None]) -> ExtractionTask:
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
        return self._write_task(task.task_id, self._stamped_payload(task.task_id, to_jsonable(task)))

    def load_compare_task(self, task_id: str) -> CompareTask:
        data = self._read_task_data(task_id)
        if data.get("task_type") == "extraction":
            raise FileNotFoundError(f"任务 {task_id} 不是对比任务。")
        return CompareTask(**data)

    def list_compare_tasks(self) -> list[CompareTask]:
        tasks: list[CompareTask] = []
        for data in self._iter_task_data():
            if data.get("task_type") == "extraction":
                continue
            try:
                tasks.append(CompareTask(**data))
            except (ValueError, TypeError):
                continue
        return sorted(tasks, key=lambda task: task.updated_at or task.created_at, reverse=True)

    def update_compare_task(self, task_id: str, mutate: Callable[[CompareTask], None]) -> CompareTask:
        with self._lock:
            task = self.load_compare_task(task_id)
            mutate(task)
            self._write_task(task.task_id, self._stamped_payload(task.task_id, to_jsonable(task)))
            return self.load_compare_task(task_id)

    def save_extraction_task(self, task: ExtractionTask) -> Path:
        return self._write_task(task.task_id, self._stamped_payload(task.task_id, to_jsonable(task)))

    def load_extraction_task(self, task_id: str) -> ExtractionTask:
        data = self._read_task_data(task_id)
        if data.get("task_type") != "extraction":
            raise FileNotFoundError(f"任务 {task_id} 不是提取任务。")
        return ExtractionTask(**data)

    def list_extraction_tasks(self) -> list[ExtractionTask]:
        tasks: list[ExtractionTask] = []
        for data in self._iter_task_data():
            if data.get("task_type") != "extraction":
                continue
            try:
                tasks.append(ExtractionTask(**data))
            except (ValueError, TypeError):
                continue
        return sorted(tasks, key=lambda task: task.updated_at or task.created_at, reverse=True)

    def update_extraction_task(self, task_id: str, mutate: Callable[[ExtractionTask], None]) -> ExtractionTask:
        with self._lock:
            task = self.load_extraction_task(task_id)
            mutate(task)
            self._write_task(task.task_id, self._stamped_payload(task.task_id, to_jsonable(task)))
            return self.load_extraction_task(task_id)

    def task_json_path(self, task_id: str) -> Path:
        return self.task_dir(task_id) / "task.json"

    def task_dir(self, task_id: str) -> Path:
        return self.settings.tasks_dir / self._safe_task_id(task_id)

    def _write_task(self, task_id: str, data: dict[str, Any]) -> Path:
        with self._lock:
            self.settings.tasks_dir.mkdir(parents=True, exist_ok=True)
            path = self.task_json_path(task_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_suffix(path.suffix + ".tmp")
            temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(path)
            self._write_manifest(task_id, data)
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
        manifest_path = task_dir / "manifest.json"
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
        artifacts["task.json"] = {
            "path": "task.json",
            "area": "metadata",
            "kind": "json",
            "updated_at": now,
        }
        manifest.update(
            {
                "task_id": self._safe_task_id(task_id),
                "task_type": data.get("task_type") or "compare",
                "status": data.get("status", ""),
                "stage": data.get("stage", ""),
                "updated_at": now,
                "artifacts": sorted(artifacts.values(), key=lambda item: str(item["path"])),
            }
        )
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

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

    def save_extraction_task(self, task: ExtractionTask) -> Path | None:
        return self.resolve().save_extraction_task(task)

    def load_extraction_task(self, task_id: str) -> ExtractionTask:
        return self.resolve().load_extraction_task(task_id)

    def list_extraction_tasks(self) -> list[ExtractionTask]:
        return self.resolve().list_extraction_tasks()

    def update_extraction_task(self, task_id: str, mutate: Callable[[ExtractionTask], None]) -> ExtractionTask:
        return self.resolve().update_extraction_task(task_id, mutate)


default_task_repository = LazyDefaultTaskRepository()
