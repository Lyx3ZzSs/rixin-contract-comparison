from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings, settings
from app.models import CompareTask


def to_jsonable(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model.dict()


_COLUMNS = (
    "task_id",
    "schema_version",
    "revision",
    "status",
    "terminal_reason",
    "report_revision",
    "stage",
    "progress_percent",
    "created_at",
    "updated_at",
    "owner_sub",
    "owner_username",
    "owner_display_name",
    "owner_department_code",
    "owner_department_name",
    "original_filename",
    "compare_filename",
    "execution_id",
    "execution_no",
    "execution_status",
    "execution_queued_at",
    "execution_started_at",
    "execution_finished_at",
    "execution_error_code",
    "execution_last_error",
    "diff_count",
)

_RUNTIME_PATH_FIELDS = {
    "original_pdf_path",
    "compare_pdf_path",
    "report_pdf_path",
    "ocr_raw_result_path",
    "ocr_raw_result_paths",
    "debug_artifact_paths",
}


class SQLiteTaskRepository:
    """SQLite is the sole authority for structured comparison-task state."""

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._connection_path: Path | None = None

    def resolve(self) -> SQLiteTaskRepository:
        self._connect()
        return self

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
            self._connection = None
            self._connection_path = None

    def healthcheck(self) -> None:
        with self._lock:
            self._connect().execute("SELECT 1").fetchone()

    def save_compare_task(self, task: CompareTask) -> Path | None:
        with self._lock:
            connection = self._connect()
            existing = connection.execute(
                "SELECT revision, created_at, owner_sub FROM tasks WHERE task_id = ?",
                (task.task_id,),
            ).fetchone()
            if existing is not None:
                task.revision = int(existing["revision"]) + 1
                task.created_at = str(existing["created_at"])
                if task.owner_sub != str(existing["owner_sub"]):
                    raise ValueError("任务所有者不能变更。")
            else:
                task.revision = max(1, task.revision)
            task.schema_version = max(3, task.schema_version)
            task.diff_count = len(task.diffs)
            task.updated_at = datetime.now(UTC).isoformat()
            values = self._serialize(task)
            placeholders = ", ".join("?" for _ in values)
            columns = ", ".join(values)
            updates = ", ".join(f"{name}=excluded.{name}" for name in values if name != "task_id")
            with connection:
                connection.execute(
                    f"INSERT INTO tasks ({columns}) VALUES ({placeholders}) "
                    f"ON CONFLICT(task_id) DO UPDATE SET {updates}",
                    tuple(values.values()),
                )
        return None

    def load_compare_task(self, task_id: str) -> CompareTask:
        with self._lock:
            row = self._connect().execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise FileNotFoundError(f"任务不存在: {task_id}")
        return self._deserialize(row)

    def list_compare_tasks(self) -> list[CompareTask]:
        with self._lock:
            rows = (
                self._connect()
                .execute("SELECT * FROM tasks ORDER BY COALESCE(created_at, updated_at) DESC, task_id DESC")
                .fetchall()
            )
        return [self._deserialize(row) for row in rows]

    def list_compare_record_summaries(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = (
                self._connect()
                .execute(
                    """
                SELECT task_id, owner_sub, status, terminal_reason, revision, report_revision,
                       stage, progress_percent, created_at, updated_at,
                       original_filename, compare_filename, diff_count
                FROM tasks
                ORDER BY COALESCE(created_at, updated_at) DESC, task_id DESC
                """
                )
                .fetchall()
            )
        return [dict(row) for row in rows]

    def update_compare_task(self, task_id: str, mutate: Callable[[CompareTask], None]) -> CompareTask:
        with self._lock:
            task = self.load_compare_task(task_id)
            owner_sub = task.owner_sub
            mutate(task)
            if task.task_id != task_id:
                raise ValueError("任务 ID 不能变更。")
            if task.owner_sub != owner_sub:
                raise ValueError("任务所有者不能变更。")
            self.save_compare_task(task)
            return task.model_copy(deep=True)

    def fail_interrupted_tasks(self) -> int:
        interrupted = [task for task in self.list_compare_tasks() if task.status == "PROCESSING"]
        for task in interrupted:

            def fail(current: CompareTask) -> None:
                now = datetime.now(UTC).isoformat()
                current.status = "FAILED"
                current.terminal_reason = "EXECUTION_FAILED"
                current.stage = "服务重启，任务已中止"
                current.progress_percent = 100
                current.execution_status = "FAILED"
                current.execution_finished_at = now
                current.execution_error_code = "SERVICE_RESTARTED"
                current.execution_last_error = "服务重启，请手动重试。"
                if current.execution_last_error not in current.errors:
                    current.errors.append(current.execution_last_error)

            self.update_compare_task(task.task_id, fail)
        return len(interrupted)

    def _connect(self) -> sqlite3.Connection:
        path = self.settings.task_database_path.resolve()
        if self._connection is not None and self._connection_path == path:
            return self._connection
        if self._connection is not None:
            self._connection.close()
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('PROCESSING', 'COMPLETED', 'FAILED')),
                terminal_reason TEXT NOT NULL,
                report_revision INTEGER NOT NULL,
                stage TEXT NOT NULL,
                progress_percent INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                owner_sub TEXT NOT NULL,
                owner_username TEXT NOT NULL,
                owner_display_name TEXT NOT NULL,
                owner_department_code TEXT NOT NULL,
                owner_department_name TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                compare_filename TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                execution_no INTEGER NOT NULL,
                execution_status TEXT NOT NULL,
                execution_queued_at TEXT NOT NULL,
                execution_started_at TEXT NOT NULL,
                execution_finished_at TEXT NOT NULL,
                execution_error_code TEXT NOT NULL,
                execution_last_error TEXT NOT NULL,
                diff_count INTEGER NOT NULL,
                details_json TEXT NOT NULL CHECK (json_valid(details_json))
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_owner_created
                ON tasks(owner_sub, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_tasks_status_created
                ON tasks(status, created_at DESC);
            """
        )
        self._connection = connection
        self._connection_path = path
        return connection

    def _serialize(self, task: CompareTask) -> dict[str, Any]:
        payload = to_jsonable(task)
        values = {name: payload.pop(name) for name in _COLUMNS}
        for field in _RUNTIME_PATH_FIELDS:
            payload.pop(field, None)
        values["details_json"] = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return values

    def _deserialize(self, row: sqlite3.Row) -> CompareTask:
        payload = json.loads(str(row["details_json"]))
        payload.update({name: row[name] for name in _COLUMNS})
        task_root = self.settings.tasks_dir / str(row["task_id"])
        payload["original_pdf_path"] = str(task_root / "input" / "original" / str(row["original_filename"]))
        payload["compare_pdf_path"] = str(task_root / "input" / "compare" / str(row["compare_filename"]))
        payload["report_pdf_path"] = str(task_root / "report" / f"report-r{row['report_revision']}.pdf")
        return CompareTask.model_validate(payload)


TaskRepository = SQLiteTaskRepository
default_task_repository = SQLiteTaskRepository()
