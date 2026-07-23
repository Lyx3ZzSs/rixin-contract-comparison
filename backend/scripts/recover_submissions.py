from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.application.submission_recovery import SubmissionRecoveryService
from app.infrastructure.recovery_store import default_recovery_store
from app.infrastructure.task_repository import default_task_repository


def main() -> int:
    recovery_service = SubmissionRecoveryService(
        recovery_store=default_recovery_store,
        repository=default_task_repository,
    )
    return 0 if recovery_service.recover_all() else 1


if __name__ == "__main__":
    raise SystemExit(main())
