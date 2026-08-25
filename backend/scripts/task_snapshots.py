from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def load_task_snapshot(task_dir: Path) -> dict[str, Any]:
    """Load one task for offline tooling, with legacy JSON as a fallback."""
    database = task_dir.parent.parent / "tasks.sqlite3"
    if database.exists():
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_dir.name,),
            ).fetchone()
        if row is None:
            raise FileNotFoundError(f"SQLite 中不存在任务: {task_dir.name}")
        return _row_payload(row)

    legacy_path = task_dir / "task.json"
    return json.loads(legacy_path.read_text(encoding="utf-8"))


def list_task_snapshots(tasks_dir: Path) -> list[dict[str, Any]]:
    """List SQLite snapshots, or legacy JSON snapshots when no database exists."""
    database = tasks_dir.parent / "tasks.sqlite3"
    if database.exists():
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY COALESCE(created_at, updated_at), task_id"
            ).fetchall()
        return [_row_payload(row) for row in rows]

    snapshots: list[dict[str, Any]] = []
    for path in sorted(tasks_dir.glob("*/task.json")):
        snapshots.append(json.loads(path.read_text(encoding="utf-8")))
    return snapshots


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(str(row["details_json"]))
    payload.update({key: row[key] for key in row.keys() if key != "details_json"})
    return payload
