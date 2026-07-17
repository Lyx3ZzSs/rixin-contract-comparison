from __future__ import annotations

import logging
from pathlib import Path

from app.infrastructure.recovery_store import RecoveryMarker, RecoveryMarkerDeferred, RecoveryStore
from app.infrastructure.task_repository import TaskRepository

logger = logging.getLogger(__name__)


class SubmissionRecoveryService:
    """Idempotently resolve interrupted upload submissions during startup."""

    def __init__(self, *, recovery_store: RecoveryStore, repository: TaskRepository) -> None:
        self.recovery_store = recovery_store
        self.repository = repository

    def recover_all(self) -> bool:
        return self.recovery_store.recover_all(self._prepare_marker)

    def _prepare_marker(self, marker: RecoveryMarker) -> RecoveryMarker | None:
        final_actions = [action for action in marker.actions if action.scope == "final_input"]
        if not final_actions:
            return marker
        try:
            task = self.repository.load_compare_task(marker.task_id)
        except FileNotFoundError:
            return marker
        except Exception as exc:
            logger.warning(
                "event=recovery_marker_deferred task_id=%s reason=task_commit_check_failed detail=%s",
                marker.task_id,
                exc,
            )
            raise RecoveryMarkerDeferred(str(exc)) from exc

        persisted_paths = {
            Path(task.original_pdf_path).resolve(),
            Path(task.compare_pdf_path).resolve(),
        }
        action_paths = {Path(action.path).resolve() for action in final_actions}
        if action_paths <= persisted_paths:
            return self.recovery_store.finalize_final_inputs(marker)
        return marker
