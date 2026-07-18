from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - index updates require POSIX file locks
    fcntl = None

from pydantic import ValidationError as PydanticValidationError

from app.config import Settings, settings
from app.infrastructure.atomic_files import atomic_write_json
from app.models import CompareTask


_index_lock = threading.RLock()
_SCHEMA_VERSION = 1


class CompareTaskIndex:
    """A compact, derived index for comparison-record pagination."""

    def __init__(self, app_settings: Settings = settings) -> None:
        self.settings = app_settings

    @property
    def path(self) -> Path:
        return self.settings.storage_dir / "indexes" / "compare_records.json"

    @property
    def lock_path(self) -> Path:
        return self.path.with_suffix(".lock")

    def upsert(self, task: CompareTask) -> None:
        with _index_lock:
            with self._exclusive_lock():
                records = self._read_records()
                records[task.task_id] = comparison_record_summary(task)
                self._write_records(records)

    def list_records(self) -> list[dict[str, Any]]:
        with _index_lock:
            records = self._read_records()
        return sorted(
            (dict(record) for record in records.values()),
            key=lambda record: (str(record.get("created_at") or record.get("updated_at") or ""), str(record["task_id"])),
            reverse=True,
        )

    def rebuild(self) -> int:
        with _index_lock:
            with self._exclusive_lock():
                records: dict[str, dict[str, Any]] = {}
                tasks_dir = self.settings.tasks_dir
                if tasks_dir is not None and tasks_dir.exists():
                    for path in sorted(tasks_dir.glob("*/task.json"), key=lambda item: item.parent.name):
                        try:
                            payload = json.loads(path.read_text(encoding="utf-8"))
                            if not isinstance(payload, dict) or payload.get("task_type") == "extraction":
                                continue
                            task = CompareTask(**payload)
                        except (OSError, ValueError, TypeError, UnicodeError, PydanticValidationError):
                            continue
                        records[task.task_id] = comparison_record_summary(task)
                self._write_records(records)
        return len(records)

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        if fcntl is None:
            raise RuntimeError("Comparison record index operations require POSIX fcntl advisory locks")
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read_records(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError, UnicodeError):
            return {}
        if not isinstance(payload, Mapping) or payload.get("schema_version") != _SCHEMA_VERSION:
            return {}
        raw_records = payload.get("records")
        if not isinstance(raw_records, Mapping):
            return {}
        return {
            str(task_id): dict(record)
            for task_id, record in raw_records.items()
            if isinstance(record, Mapping) and str(record.get("task_id") or "") == str(task_id)
        }

    def _write_records(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        ordered_records = {task_id: dict(records[task_id]) for task_id in sorted(records)}
        atomic_write_json(
            self.path,
            {
                "schema_version": _SCHEMA_VERSION,
                "updated_at": datetime.now(UTC).isoformat(),
                "records": ordered_records,
            },
        )


def comparison_record_summary(task: CompareTask) -> dict[str, Any]:
    """Project only list and access-control metadata; task.json remains canonical."""
    return {
        "task_id": task.task_id,
        "owner_sub": task.owner_sub,
        "status": task.status,
        "terminal_reason": task.terminal_reason,
        "revision": task.revision,
        "report_revision": task.report_revision,
        "stage": task.stage,
        "progress_percent": task.progress_percent,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "original_filename": task.original_filename,
        "compare_filename": task.compare_filename,
        "diff_count": task.diff_count,
    }
