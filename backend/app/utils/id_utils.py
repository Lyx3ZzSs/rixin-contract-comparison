from __future__ import annotations

import uuid


def generate_task_id() -> str:
    return str(uuid.uuid4())


def generate_diff_id(index: int) -> str:
    return f"D{index:03d}"
