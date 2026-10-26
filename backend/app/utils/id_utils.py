from __future__ import annotations

import uuid


def generate_task_id() -> str:
    return str(uuid.uuid4())


def generate_diff_id(index: int) -> str:
    return f"D{index:03d}"


def next_diff_index(diffs) -> int:
    """Filtering leaves holes; list length is not an ID allocator."""
    return 1 + max((int(item.diff_id[1:]) for item in diffs
                    if item.diff_id.startswith("D") and item.diff_id[1:].isdigit()), default=0)
