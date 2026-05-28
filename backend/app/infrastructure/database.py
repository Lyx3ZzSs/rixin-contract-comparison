from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from app.config import settings


PSYCOPG2_SCHEMES = ("postgresql+psycopg2://", "postgres+psycopg2://")


def require_sqlalchemy() -> Any:
    try:
        import sqlalchemy
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "PostgreSQL task repository requires SQLAlchemy. "
            "Install backend dependencies with `python -m pip install -r requirements.txt`."
        ) from exc
    return sqlalchemy


def require_sqlalchemy_orm() -> Any:
    require_sqlalchemy()
    try:
        import sqlalchemy.orm
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "PostgreSQL task repository requires SQLAlchemy ORM. "
            "Install backend dependencies with `python -m pip install -r requirements.txt`."
        ) from exc
    return sqlalchemy.orm


def create_engine(database_url: str | None = None) -> Any:
    sqlalchemy = require_sqlalchemy()
    url = normalize_database_url(database_url or settings.database_url)
    if not url:
        raise RuntimeError("DATABASE_URL must be configured when TASK_REPOSITORY_BACKEND=postgres.")
    return sqlalchemy.create_engine(url, pool_pre_ping=True, future=True)


def normalize_database_url(database_url: str) -> str:
    url = database_url.strip()
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith(PSYCOPG2_SCHEMES):
        raise RuntimeError(
            "DATABASE_URL is configured for psycopg2, but this project uses psycopg v3. "
            "Use `postgresql+psycopg://user:password@host:5432/database`."
        )
    return url


def create_session_factory(database_url: str | None = None) -> Any:
    orm = require_sqlalchemy_orm()
    engine = create_engine(database_url)
    return orm.sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope(session_factory: Any) -> Generator[Any, None, None]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
