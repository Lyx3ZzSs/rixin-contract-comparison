from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import Settings, settings
from app.infrastructure.database import create_session_factory, session_scope
from app.infrastructure.db_models import TaskRecord
from app.infrastructure.task_repository import to_jsonable
from app.models import CompareTask
from app.models_extraction import ExtractionTask


class PostgresTaskRepository:
    """PostgreSQL-backed task store.

    The first database-backed phase keeps complete task payloads in JSONB while
    duplicating frequently queried fields into indexed columns for listing.
    """

    def __init__(
        self,
        app_settings: Settings = settings,
        *,
        database_url: str | None = None,
        session_factory: Any | None = None,
    ) -> None:
        self.settings = app_settings
        self.session_factory = session_factory or create_session_factory(database_url or app_settings.database_url)

    def save_compare_task(self, task: CompareTask) -> Path | None:
        self._stamp_task(task, self._current_revision(task.task_id))
        self._upsert_record(self._record_values("compare", task, to_jsonable(task)))
        return None

    def load_compare_task(self, task_id: str) -> CompareTask:
        payload = self._load_payload(task_id, "compare")
        return CompareTask(**payload)

    def list_compare_tasks(self) -> list[CompareTask]:
        return [CompareTask(**payload) for payload in self._list_payloads("compare")]

    def update_compare_task(self, task_id: str, mutate: Callable[[CompareTask], None]) -> CompareTask:
        task = self._update_task_payload(task_id, "compare", CompareTask, mutate)
        assert isinstance(task, CompareTask)
        return task

    def save_extraction_task(self, task: ExtractionTask) -> Path | None:
        self._stamp_task(task, self._current_revision(task.task_id))
        self._upsert_record(self._record_values("extraction", task, to_jsonable(task)))
        return None

    def load_extraction_task(self, task_id: str) -> ExtractionTask:
        payload = self._load_payload(task_id, "extraction")
        return ExtractionTask(**payload)

    def list_extraction_tasks(self) -> list[ExtractionTask]:
        return [ExtractionTask(**payload) for payload in self._list_payloads("extraction")]

    def update_extraction_task(self, task_id: str, mutate: Callable[[ExtractionTask], None]) -> ExtractionTask:
        task = self._update_task_payload(task_id, "extraction", ExtractionTask, mutate)
        assert isinstance(task, ExtractionTask)
        return task

    def _upsert_record(self, values: dict[str, Any]) -> None:
        with session_scope(self.session_factory) as session:
            if session.bind.dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert

                statement = insert(TaskRecord).values(**values)
                excluded = statement.excluded
                update_values = {
                    "task_type": excluded.task_type,
                    "status": excluded.status,
                    "stage": excluded.stage,
                    "progress_percent": excluded.progress_percent,
                    "created_at": excluded.created_at,
                    "updated_at": excluded.updated_at,
                    "filename": excluded.filename,
                    "original_filename": excluded.original_filename,
                    "compare_filename": excluded.compare_filename,
                    "schema_version": excluded.schema_version,
                    "payload": excluded.payload,
                }
                session.execute(
                    statement.on_conflict_do_update(index_elements=[TaskRecord.task_id], set_=update_values)
                )
                return

            record = session.get(TaskRecord, values["task_id"])
            if record is None:
                session.add(TaskRecord(**values))
                return
            for key, value in values.items():
                setattr(record, key, value)

    def _load_payload(self, task_id: str, task_type: str) -> dict[str, Any]:
        from sqlalchemy import select

        with session_scope(self.session_factory) as session:
            statement = select(TaskRecord.payload).where(
                TaskRecord.task_id == task_id,
                TaskRecord.task_type == task_type,
            )
            payload = session.execute(statement).scalar_one_or_none()
            if payload is None:
                task_name = "提取任务" if task_type == "extraction" else "对比任务"
                raise FileNotFoundError(f"任务不存在或不是{task_name}: {task_id}")
            return payload

    def _list_payloads(self, task_type: str) -> list[dict[str, Any]]:
        from sqlalchemy import select

        with session_scope(self.session_factory) as session:
            statement = (
                select(TaskRecord.payload)
                .where(TaskRecord.task_type == task_type)
                .order_by(TaskRecord.updated_at.desc(), TaskRecord.created_at.desc())
            )
            return list(session.execute(statement).scalars().all())

    def _update_task_payload(
        self,
        task_id: str,
        task_type: str,
        model_type: type[CompareTask] | type[ExtractionTask],
        mutate: Callable[[Any], None],
    ) -> CompareTask | ExtractionTask:
        with session_scope(self.session_factory) as session:
            record = session.get(TaskRecord, task_id)
            if record is None or record.task_type != task_type:
                task_name = "提取任务" if task_type == "extraction" else "对比任务"
                raise FileNotFoundError(f"任务不存在或不是{task_name}: {task_id}")
            task = model_type(**record.payload)
            mutate(task)
            self._stamp_task(task, int(record.payload.get("revision") or 0))
            values = self._record_values(task_type, task, to_jsonable(task))
            for key, value in values.items():
                setattr(record, key, value)
            return task

    def _current_revision(self, task_id: str) -> int:
        from sqlalchemy import select

        with session_scope(self.session_factory) as session:
            payload = session.execute(
                select(TaskRecord.payload).where(TaskRecord.task_id == task_id)
            ).scalar_one_or_none()
            if not payload:
                return 0
            try:
                return int(payload.get("revision") or 0)
            except (TypeError, ValueError):
                return 0

    def _stamp_task(self, task: CompareTask | ExtractionTask, current_revision: int) -> None:
        task.schema_version = int(task.schema_version or 1)
        task.revision = current_revision + 1
        task.updated_at = datetime.now(UTC).isoformat()

    def _record_values(
        self,
        task_type: str,
        task: CompareTask | ExtractionTask,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "task_type": task_type,
            "status": task.status,
            "stage": task.stage,
            "progress_percent": getattr(task, "progress_percent", 0),
            "created_at": self._parse_datetime(task.created_at),
            "updated_at": self._parse_datetime(task.updated_at),
            "filename": getattr(task, "filename", ""),
            "original_filename": getattr(task, "original_filename", ""),
            "compare_filename": getattr(task, "compare_filename", ""),
            "schema_version": task.schema_version,
            "payload": payload,
        }

    def _parse_datetime(self, value: str) -> datetime:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
