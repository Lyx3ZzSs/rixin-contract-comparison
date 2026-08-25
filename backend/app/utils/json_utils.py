from __future__ import annotations

from typing import Any

from app.infrastructure.task_repository import (
    default_task_repository,
    to_jsonable as _to_jsonable,
)
from app.models import CompareTask


def to_jsonable(model: Any) -> dict[str, Any]:
    return _to_jsonable(model)


def save_task(task: CompareTask) -> None:
    return default_task_repository.save_compare_task(task)


def load_task(task_id: str) -> CompareTask:
    return default_task_repository.load_compare_task(task_id)


def list_compare_tasks() -> list[CompareTask]:
    return default_task_repository.list_compare_tasks()
