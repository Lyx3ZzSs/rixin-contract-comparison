from __future__ import annotations

import json
import threading
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

    def save_extraction_task(self, task: ExtractionTask) -> Path | None:
        raise NotImplementedError

    def load_extraction_task(self, task_id: str) -> ExtractionTask:
        raise NotImplementedError

    def list_extraction_tasks(self) -> list[ExtractionTask]:
        raise NotImplementedError


def to_jsonable(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model.dict()


class LocalJsonTaskRepository:
    """Local JSON task store used by the MVP runtime.

    The repository keeps the current file format but centralizes task persistence
    behind an interface so API and service code do not depend on JSON files.
    """

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings
        self._lock = threading.RLock()

    def save_compare_task(self, task: CompareTask) -> Path:
        return self._write_task(task.task_id, to_jsonable(task))

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

    def save_extraction_task(self, task: ExtractionTask) -> Path:
        return self._write_task(task.task_id, to_jsonable(task))

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

    def task_json_path(self, task_id: str) -> Path:
        return self.settings.tasks_dir / f"{task_id}.json"

    def _write_task(self, task_id: str, data: dict[str, Any]) -> Path:
        with self._lock:
            self.settings.tasks_dir.mkdir(parents=True, exist_ok=True)
            path = self.task_json_path(task_id)
            temp_path = path.with_suffix(path.suffix + ".tmp")
            temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(path)
            return path

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
        for path in self.settings.tasks_dir.glob("*.json"):
            try:
                with self._lock:
                    items.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError):
                continue
        return items


def build_task_repository(app_settings: Settings = settings) -> TaskRepository:
    if app_settings.task_repository_backend == "local_json":
        return LocalJsonTaskRepository(app_settings)
    if app_settings.task_repository_backend == "postgres":
        try:
            from app.infrastructure.postgres_task_repository import PostgresTaskRepository
        except ModuleNotFoundError as exc:
            if exc.name == "sqlalchemy":
                raise RuntimeError(
                    "TASK_REPOSITORY_BACKEND=postgres requires SQLAlchemy. "
                    "Install backend dependencies with `python -m pip install -r requirements.txt`."
                ) from exc
            raise

        return PostgresTaskRepository(app_settings)
    raise ValueError(f"Unsupported task repository backend: {app_settings.task_repository_backend}")


default_task_repository = build_task_repository()
