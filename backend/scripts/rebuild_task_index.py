from __future__ import annotations

from app.config import Settings, settings
from app.infrastructure.task_index import CompareTaskIndex


def rebuild_task_index(app_settings: Settings = settings) -> int:
    return CompareTaskIndex(app_settings).rebuild()


if __name__ == "__main__":
    print(f"Rebuilt {rebuild_task_index()} comparison record index entries.")
