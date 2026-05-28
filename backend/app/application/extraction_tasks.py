from __future__ import annotations

import functools
from pathlib import Path

from app.infrastructure.task_repository import TaskRepository, default_task_repository
from app.infrastructure.task_runner import BackgroundTaskRunner, default_task_runner
from app.models_extraction import ExtractionFieldDef, ExtractionTask
from app.services.extraction_service import ExtractionService


class ExtractionTaskApplication:
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
        file_path: Path,
        filename: str,
        fields: list[ExtractionFieldDef],
        service: ExtractionService,
    ) -> ExtractionTask:
        task = ExtractionTask(
            task_id=task_id,
            filename=filename,
            file_path=str(file_path),
            fields=fields,
            extractor_used=service.client.name,
        )
        self.repository.save_extraction_task(task)
        return task

    def submit_extraction(
        self,
        *,
        service: ExtractionService,
        file_path: Path,
        field_defs: list[ExtractionFieldDef],
        task_id: str,
        filename: str,
    ) -> None:
        self.runner.submit(
            functools.partial(
                service.extract,
                file_path=str(file_path),
                field_defs=field_defs,
                task_id=task_id,
                filename=filename,
            )
        )


default_extraction_task_application = ExtractionTaskApplication()
