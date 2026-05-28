"""create task records

Revision ID: 20260527_0001
Revises:
Create Date: 2026-05-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260527_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_records",
        sa.Column("task_id", sa.String(length=64), nullable=False),
        sa.Column("task_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=128), nullable=False),
        sa.Column("progress_percent", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("compare_filename", sa.String(length=512), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index("ix_task_records_task_type_updated_at", "task_records", ["task_type", "updated_at"])
    op.create_index("ix_task_records_status", "task_records", ["status"])


def downgrade() -> None:
    op.drop_index("ix_task_records_status", table_name="task_records")
    op.drop_index("ix_task_records_task_type_updated_at", table_name="task_records")
    op.drop_table("task_records")
