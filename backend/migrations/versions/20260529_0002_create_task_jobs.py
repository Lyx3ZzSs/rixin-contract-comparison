"""create task jobs

Revision ID: 20260529_0002
Revises: 20260527_0001
Create Date: 2026-05-29
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "20260529_0002"
down_revision = "20260527_0001"
branch_labels = None
depends_on = None


def _execute_sql_file(filename: str) -> None:
    sql_dir = Path(__file__).resolve().parents[1] / "sql" / revision
    for statement in (sql_dir / filename).read_text(encoding="utf-8").split(";"):
        statement = statement.strip()
        if statement:
            op.execute(statement)


def upgrade() -> None:
    _execute_sql_file("up.sql")


def downgrade() -> None:
    _execute_sql_file("down.sql")
