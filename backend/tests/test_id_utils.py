from __future__ import annotations

import uuid

from app.utils.id_utils import generate_diff_id, generate_task_id


def test_generate_task_id_returns_uuid4_string() -> None:
    task_id = generate_task_id()

    parsed = uuid.UUID(task_id, version=4)

    assert str(parsed) == task_id
    assert not task_id.startswith("T")


def test_generate_diff_id_keeps_existing_format() -> None:
    assert generate_diff_id(7) == "D007"
