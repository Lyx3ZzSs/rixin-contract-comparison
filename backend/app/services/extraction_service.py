from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from app.infrastructure.task_repository import TaskRepository, default_task_repository
from app.models_extraction import ExtractionFieldDef, ExtractionTask
from app.services.ppocrv5_llm_extraction import PPOCRV5LLMExtractionClient

logger = logging.getLogger(__name__)


class ExtractionService:
    def __init__(
        self,
        client: PPOCRV5LLMExtractionClient | None = None,
        repository: TaskRepository = default_task_repository,
    ) -> None:
        self.client = client or PPOCRV5LLMExtractionClient()
        self.repository = repository

    def _update_stage(self, task: ExtractionTask, stage: str) -> None:
        def mutate(persisted: ExtractionTask) -> None:
            persisted.stage = stage

        try:
            persisted = self.repository.update_extraction_task(task.task_id, mutate)
        except FileNotFoundError:
            mutate(task)
            task.updated_at = datetime.now(UTC).isoformat()
            self.repository.save_extraction_task(task)
            return

        task.stage = persisted.stage
        task.updated_at = persisted.updated_at
        task.revision = persisted.revision

    def extract(
        self,
        file_path: str,
        field_defs: list[ExtractionFieldDef],
        task_id: str,
        filename: str = "",
    ) -> ExtractionTask:
        t_start = time.perf_counter()

        task = self._load_or_create_task(
            task_id=task_id,
            file_path=file_path,
            field_defs=field_defs,
            filename=filename,
        )

        try:
            self._update_stage(task, "preprocessing")
            result = self.client.extract_fields(
                file_path,
                field_defs,
                task_id=task_id,
                stage_callback=lambda stage: self._update_stage(task, stage),
            )
            task.results = result.results
            task.raw_result_path = result.raw_result_path
            task.converted_file_path = result.converted_file_path
            task.status = "COMPLETED"
        except Exception as exc:
            task.status = "FAILED"
            task.errors.append(str(exc))
            task = self._mark_failed(task, str(exc))
            logger.info("[%s] 提取失败 总耗时 %.2fs", task_id, time.perf_counter() - t_start)
        else:
            task = self._save_result(task)

        if task.status == "COMPLETED":
            logger.info("[%s] 任务完成 总耗时 %.2fs", task_id, time.perf_counter() - t_start)
        return task

    def _load_or_create_task(
        self,
        *,
        task_id: str,
        file_path: str,
        field_defs: list[ExtractionFieldDef],
        filename: str,
    ) -> ExtractionTask:
        try:
            task = self.repository.load_extraction_task(task_id)
        except FileNotFoundError:
            task = ExtractionTask(task_id=task_id)

        task.status = "PROCESSING"
        task.filename = filename or task.filename
        task.file_path = file_path
        task.fields = field_defs
        task.extractor_used = self.client.name
        task.updated_at = datetime.now(UTC).isoformat()
        self.repository.save_extraction_task(task)
        return task

    def _save_result(self, task: ExtractionTask) -> ExtractionTask:
        def mutate(persisted: ExtractionTask) -> None:
            persisted.status = task.status
            persisted.stage = task.stage
            persisted.filename = task.filename
            persisted.file_path = task.file_path
            persisted.converted_file_path = task.converted_file_path
            persisted.extractor_used = task.extractor_used
            persisted.raw_result_path = task.raw_result_path
            persisted.fields = task.fields
            persisted.results = task.results
            persisted.errors = task.errors

        try:
            return self.repository.update_extraction_task(task.task_id, mutate)
        except FileNotFoundError:
            self.repository.save_extraction_task(task)
            return task

    def _mark_failed(self, task: ExtractionTask, error: str) -> ExtractionTask:
        def mutate(persisted: ExtractionTask) -> None:
            persisted.status = "FAILED"
            if error not in persisted.errors:
                persisted.errors.append(error)

        try:
            return self.repository.update_extraction_task(task.task_id, mutate)
        except FileNotFoundError:
            self.repository.save_extraction_task(task)
            return task
