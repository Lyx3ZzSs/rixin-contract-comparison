from __future__ import annotations

import uuid


def generate_task_id() -> str:
    return f"T{uuid.uuid4().hex[:12].upper()}"


def generate_diff_id(index: int) -> str:
    return f"D{index:03d}"

