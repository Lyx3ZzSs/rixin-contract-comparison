from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from app.infrastructure.task_repository import TaskRepository, default_task_repository
from app.infrastructure.task_runner import QueuedTaskRunner, default_task_runner
from app.models_extraction import ExtractionFieldDef, ExtractionTask
from app.services.extraction_service import ExtractionService


class ExtractionTaskApplication:
    def __init__(
        self,
        repository: TaskRepository = default_task_repository,
        runner: QueuedTaskRunner = default_task_runner,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self.runner.register_handler("extraction", self._run_extraction_job)

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
            task_type="extraction",
            task_id=task_id,
            payload={
                "task_id": task_id,
                "file_path": str(file_path),
                "filename": filename,
                "field_defs": [field.model_dump(mode="json") for field in field_defs],
            },
        )

    def load_extraction_task(self, task_id: str) -> ExtractionTask:
        return self.repository.load_extraction_task(task_id)

    def list_extraction_tasks(self) -> list[ExtractionTask]:
        return self.repository.list_extraction_tasks()

    def _run_extraction_job(self, payload: Mapping[str, Any]) -> None:
        field_defs = [ExtractionFieldDef(**field) for field in payload.get("field_defs", [])]
        task = ExtractionService(repository=self.repository).extract(
            file_path=str(payload["file_path"]),
            field_defs=field_defs,
            task_id=str(payload["task_id"]),
            filename=str(payload.get("filename") or ""),
        )
        if task.status == "FAILED":
            raise RuntimeError("; ".join(task.errors) or f"提取任务失败: {task.task_id}")


default_extraction_task_application = ExtractionTaskApplication()
