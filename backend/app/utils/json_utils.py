from __future__ import annotations

from pathlib import Path

from app.infrastructure.task_repository import LocalJsonTaskRepository, default_task_repository, to_jsonable
from app.models import CompareTask
from app.models_extraction import ExtractionTask


def task_json_path(task_id: str) -> Path:
    if not isinstance(default_task_repository, LocalJsonTaskRepository):
        raise RuntimeError("task_json_path is only available when TASK_REPOSITORY_BACKEND=local_json.")
    return default_task_repository.task_json_path(task_id)


def save_task(task: CompareTask) -> Path | None:
    return default_task_repository.save_compare_task(task)


def load_task(task_id: str) -> CompareTask:
    return default_task_repository.load_compare_task(task_id)


def list_compare_tasks() -> list[CompareTask]:
    return default_task_repository.list_compare_tasks()


def save_extraction_task(task: ExtractionTask) -> Path | None:
    return default_task_repository.save_extraction_task(task)


def load_extraction_task(task_id: str) -> ExtractionTask:
    return default_task_repository.load_extraction_task(task_id)


def list_extraction_tasks() -> list[ExtractionTask]:
    return default_task_repository.list_extraction_tasks()
