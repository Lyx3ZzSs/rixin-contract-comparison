from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.models import CompareTask


def to_jsonable(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model.dict()


def task_json_path(task_id: str) -> Path:
    return settings.tasks_dir / f"{task_id}.json"


def save_task(task: CompareTask) -> Path:
    settings.tasks_dir.mkdir(parents=True, exist_ok=True)
    path = task_json_path(task.task_id)
    path.write_text(json.dumps(to_jsonable(task), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_task(task_id: str) -> CompareTask:
    path = task_json_path(task_id)
    if not path.exists():
        raise FileNotFoundError(f"任务不存在: {task_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return CompareTask(**data)

