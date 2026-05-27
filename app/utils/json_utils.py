from __future__ import annotations

from pathlib import Path

from app.infrastructure.task_repository import default_task_repository, to_jsonable
from app.models import CompareTask
from app.models_extraction import ExtractionTask


def task_json_path(task_id: str) -> Path:
    return default_task_repository.task_json_path(task_id)


def save_task(task: CompareTask) -> Path:
    return default_task_repository.save_compare_task(task)


def load_task(task_id: str) -> CompareTask:
    return default_task_repository.load_compare_task(task_id)


def list_compare_tasks() -> list[CompareTask]:
    return default_task_repository.list_compare_tasks()


def save_extraction_task(task: ExtractionTask) -> Path:
    return default_task_repository.save_extraction_task(task)


def load_extraction_task(task_id: str) -> ExtractionTask:
    return default_task_repository.load_extraction_task(task_id)


def list_extraction_tasks() -> list[ExtractionTask]:
    return default_task_repository.list_extraction_tasks()
