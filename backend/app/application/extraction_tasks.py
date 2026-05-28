from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from app.errors import ConflictError, NotFoundError
from app.infrastructure.task_repository import TaskRepository, default_task_repository
from app.infrastructure.task_runner import QueuedTaskRunner, TaskJob, default_task_runner
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

    def load_execution(self, task_id: str) -> TaskJob:
        self.load_extraction_task(task_id)
        return self.runner.latest_job(task_id, task_type="extraction")

    def cancel_extraction(self, task_id: str) -> TaskJob:
        self.load_extraction_task(task_id)
        jobs = self.runner.cancel(task_id, task_type="extraction")
        if not jobs:
            raise NotFoundError(f"任务执行记录不存在: {task_id}")
        job = jobs[0]
        if job.status == "CANCELLED":
            self._mark_cancelled(task_id)
        elif job.status == "CANCEL_REQUESTED":
            self._mark_cancel_requested(task_id)
        return job

    def retry_extraction(self, task_id: str) -> TaskJob:
        task = self.load_extraction_task(task_id)
        if task.status != "FAILED":
            raise ConflictError("只有失败的提取任务可以重试。")
        job = self.runner.retry(task_id, task_type="extraction")
        self._mark_retry_queued(task)
        return job

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

    def _mark_cancelled(self, task_id: str) -> None:
        def mutate(task: ExtractionTask) -> None:
            task.status = "FAILED"
            task.stage = "已取消"
            if "任务已取消。" not in task.errors:
                task.errors.append("任务已取消。")

        self.repository.update_extraction_task(task_id, mutate)

    def _mark_cancel_requested(self, task_id: str) -> None:
        def mutate(task: ExtractionTask) -> None:
            if task.status == "PROCESSING":
                task.stage = "取消请求已提交"

        self.repository.update_extraction_task(task_id, mutate)

    def _mark_retry_queued(self, task: ExtractionTask) -> None:
        def mutate(persisted: ExtractionTask) -> None:
            persisted.status = "PROCESSING"
            persisted.stage = "排队中"
            persisted.errors = []

        self.repository.update_extraction_task(task.task_id, mutate)


default_extraction_task_application = ExtractionTaskApplication()
