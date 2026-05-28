from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.config import settings
from app.config import Settings
from app.infrastructure.database import normalize_database_url, session_scope
from app.infrastructure.task_repository import LocalJsonTaskRepository, build_task_repository, to_jsonable
from app.models import CompareTask
from app.models_extraction import ExtractionTask
from scripts.import_tasks_to_db import import_tasks


def configure_task_storage(tmp_path: Path) -> LocalJsonTaskRepository:
    settings.storage_dir = tmp_path / "storage"
    settings.tasks_dir = settings.storage_dir / "tasks"
    return LocalJsonTaskRepository(settings)


def test_local_json_task_repository_separates_task_types(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)

    repository.save_compare_task(CompareTask(task_id="TCOMPARE", updated_at="2026-05-21T09:00:00+00:00"))
    repository.save_extraction_task(ExtractionTask(task_id="TEXTRACT", updated_at="2026-05-21T10:00:00+00:00"))

    assert [task.task_id for task in repository.list_compare_tasks()] == ["TCOMPARE"]
    assert [task.task_id for task in repository.list_extraction_tasks()] == ["TEXTRACT"]


def test_local_json_task_repository_uses_last_write(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)

    repository.save_compare_task(CompareTask(task_id="TUPDATE", stage="first"))
    repository.save_compare_task(CompareTask(task_id="TUPDATE", stage="second"))

    assert repository.load_compare_task("TUPDATE").stage == "second"


def test_local_json_task_repository_skips_invalid_list_entries(tmp_path: Path) -> None:
    repository = configure_task_storage(tmp_path)
    repository.save_compare_task(CompareTask(task_id="TVALID"))
    (settings.tasks_dir / "broken.json").write_text("{", encoding="utf-8")

    assert [task.task_id for task in repository.list_compare_tasks()] == ["TVALID"]


def test_task_repository_factory_uses_local_json_backend(tmp_path: Path) -> None:
    app_settings = Settings(storage_dir=tmp_path / "storage", task_repository_backend=" LOCAL_JSON ")

    repository = build_task_repository(app_settings)

    assert app_settings.task_repository_backend == "local_json"
    assert isinstance(repository, LocalJsonTaskRepository)


def test_task_repository_factory_reports_missing_sqlalchemy_for_postgres(tmp_path: Path) -> None:
    if importlib.util.find_spec("sqlalchemy") is not None:
        pytest.skip("SQLAlchemy is installed in this environment.")

    app_settings = Settings(
        storage_dir=tmp_path / "storage",
        task_repository_backend="postgres",
        database_url="postgresql+psycopg://user:pass@localhost/db",
    )

    with pytest.raises(RuntimeError, match="requires SQLAlchemy"):
        build_task_repository(app_settings)


def test_database_url_uses_psycopg_driver_by_default() -> None:
    assert normalize_database_url("postgresql://user:pass@localhost/db") == (
        "postgresql+psycopg://user:pass@localhost/db"
    )
    assert normalize_database_url("postgres://user:pass@localhost/db") == (
        "postgresql+psycopg://user:pass@localhost/db"
    )
    assert normalize_database_url("postgresql+psycopg://user:pass@localhost/db") == (
        "postgresql+psycopg://user:pass@localhost/db"
    )


def test_database_url_rejects_psycopg2_scheme() -> None:
    with pytest.raises(RuntimeError, match="psycopg v3"):
        normalize_database_url("postgresql+psycopg2://user:pass@localhost/db")


def test_import_tasks_dry_run_validates_local_json_without_database(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "compare.json").write_text(
        json.dumps(to_jsonable(CompareTask(task_id="TCOMPARE")), ensure_ascii=False),
        encoding="utf-8",
    )
    (tasks_dir / "extraction.json").write_text(
        json.dumps(to_jsonable(ExtractionTask(task_id="TEXTRACT")), ensure_ascii=False),
        encoding="utf-8",
    )
    (tasks_dir / "broken.json").write_text("{", encoding="utf-8")

    stats = import_tasks(tasks_dir, "", dry_run=True)

    assert stats.imported == 2
    assert stats.skipped == 1
    assert stats.failed == 0


def test_session_scope_commits_and_closes_session() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.committed = False
            self.closed = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            raise AssertionError("rollback should not be called")

        def close(self) -> None:
            self.closed = True

    session = FakeSession()

    with session_scope(lambda: session) as scoped_session:
        assert scoped_session is session

    assert session.committed is True
    assert session.closed is True


def test_postgres_task_repository_contract_with_sqlite_session_factory() -> None:
    sqlalchemy = pytest.importorskip("sqlalchemy")
    orm = pytest.importorskip("sqlalchemy.orm")
    from app.infrastructure.db_models import Base
    from app.infrastructure.postgres_task_repository import PostgresTaskRepository

    engine = sqlalchemy.create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = orm.sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    repository = PostgresTaskRepository(session_factory=session_factory)

    repository.save_compare_task(CompareTask(task_id="TCOMPARE", stage="first"))
    repository.save_compare_task(CompareTask(task_id="TCOMPARE", stage="second"))
    repository.save_extraction_task(ExtractionTask(task_id="TEXTRACT"))

    assert repository.load_compare_task("TCOMPARE").stage == "second"
    assert [task.task_id for task in repository.list_compare_tasks()] == ["TCOMPARE"]
    assert [task.task_id for task in repository.list_extraction_tasks()] == ["TEXTRACT"]
