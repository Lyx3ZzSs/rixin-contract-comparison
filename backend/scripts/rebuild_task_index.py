from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings, settings  # noqa: E402
from app.infrastructure.task_index import CompareTaskIndex  # noqa: E402


def rebuild_task_index(app_settings: Settings = settings) -> int:
    return CompareTaskIndex(app_settings).rebuild()


if __name__ == "__main__":
    print(f"Rebuilt {rebuild_task_index()} comparison record index entries.")
