from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.task_snapshots import load_task_snapshot


def _write_database(storage_dir: Path) -> None:
    with sqlite3.connect(storage_dir / "tasks.sqlite3") as connection:
        connection.execute(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                details_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO tasks VALUES (?, ?, ?, ?)",
            (
                "task-1",
                "2026-08-14T00:00:00+00:00",
                "2026-08-14T00:01:00+00:00",
                json.dumps({"status": "COMPLETED", "diffs": []}),
            ),
        )


def test_load_task_snapshot_reads_sqlite(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / "task-1"
    task_dir.mkdir(parents=True)
    _write_database(tmp_path)

    snapshot = load_task_snapshot(task_dir)

    assert snapshot["task_id"] == "task-1"
    assert snapshot["status"] == "COMPLETED"


def test_load_task_snapshot_does_not_fall_back_when_sqlite_exists(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / "legacy-only"
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text('{"task_id":"legacy-only"}', encoding="utf-8")
    _write_database(tmp_path)

    with pytest.raises(FileNotFoundError, match="SQLite 中不存在任务"):
        load_task_snapshot(task_dir)
