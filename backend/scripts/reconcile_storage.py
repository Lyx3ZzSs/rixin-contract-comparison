from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.infrastructure.reconciliation import reconcile_startup
from app.infrastructure.recovery_store import default_recovery_store
from app.infrastructure.runtime_lock import ApiRuntimeLock, RuntimeLockError
from app.infrastructure.task_repository import default_task_repository
from app.infrastructure.task_runner import default_task_runner
from app.services.progress_bus import ProgressBus


def main() -> int:
    maintenance_lock = ApiRuntimeLock(settings.storage_dir)
    try:
        maintenance_lock.acquire()
    except RuntimeLockError:
        return 1
    try:
        default_task_repository.resolve()
        default_task_runner.coordinator.configure_terminal_commits(
            default_task_repository,
            ProgressBus.get_instance(),
        )
        reconcile_startup(
            default_task_repository,
            default_task_runner.coordinator,
            recovery_store=default_recovery_store,
        )
    finally:
        maintenance_lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
