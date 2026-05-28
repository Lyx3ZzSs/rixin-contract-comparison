from __future__ import annotations

import functools
import logging
from pathlib import Path

from app.infrastructure.task_repository import TaskRepository, default_task_repository
from app.infrastructure.task_runner import BackgroundTaskRunner, default_task_runner
from app.models import CompareTask
from app.services.compare_service import CompareService

logger = logging.getLogger(__name__)


class CompareTaskApplication:
    def __init__(
        self,
        repository: TaskRepository = default_task_repository,
        runner: BackgroundTaskRunner = default_task_runner,
    ) -> None:
        self.repository = repository
        self.runner = runner

    def create_queued_task(
        self,
        *,
        task_id: str,
        original_path: Path,
        compare_path: Path,
        original_filename: str,
        compare_filename: str,
    ) -> CompareTask:
        task = CompareTask(
            task_id=task_id,
            stage="排队中",
            progress_percent=3,
            original_filename=original_filename or original_path.name,
            compare_filename=compare_filename or compare_path.name,
            original_pdf_path=str(original_path),
            compare_pdf_path=str(compare_path),
        )
        self.repository.save_compare_task(task)
        return task

    def submit_compare(
        self,
        *,
        original_path: Path,
        compare_path: Path,
        task_id: str,
        original_filename: str | None,
        compare_filename: str | None,
    ) -> None:
        self.runner.submit(
            functools.partial(
                self._run_compare_task,
                original_path=original_path,
                compare_path=compare_path,
                task_id=task_id,
                original_filename=original_filename,
                compare_filename=compare_filename,
            )
        )

    def _run_compare_task(
        self,
        *,
        original_path: Path,
        compare_path: Path,
        task_id: str,
        original_filename: str | None,
        compare_filename: str | None,
    ) -> None:
        try:
            CompareService().compare(
                original_path,
                compare_path,
                task_id=task_id,
                original_filename=original_filename,
                compare_filename=compare_filename,
            )
        except Exception:
            logger.exception("Background compare task failed: %s", task_id)


default_compare_task_application = CompareTaskApplication()

