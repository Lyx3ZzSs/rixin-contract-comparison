from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from app.models_extraction import ExtractionFieldDef, ExtractionTask
from app.services.ppocrv5_llm_extraction import PPOCRV5LLMExtractionClient
from app.utils.json_utils import save_extraction_task

logger = logging.getLogger(__name__)


class ExtractionService:
    def __init__(self, client: PPOCRV5LLMExtractionClient | None = None) -> None:
        self.client = client or PPOCRV5LLMExtractionClient()

    def _update_stage(self, task: ExtractionTask, stage: str) -> None:
        task.stage = stage
        task.updated_at = datetime.now(UTC).isoformat()
        save_extraction_task(task)

    def extract(
        self,
        file_path: str,
        field_defs: list[ExtractionFieldDef],
        task_id: str,
        filename: str = "",
    ) -> ExtractionTask:
        t_start = time.perf_counter()

        task = ExtractionTask(
            task_id=task_id,
            filename=filename,
            file_path=file_path,
            fields=field_defs,
            extractor_used=self.client.name,
        )
        save_extraction_task(task)

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
            logger.info("[%s] 提取失败 总耗时 %.2fs", task_id, time.perf_counter() - t_start)
        finally:
            task.updated_at = datetime.now(UTC).isoformat()
            save_extraction_task(task)

        if task.status == "COMPLETED":
            logger.info("[%s] 任务完成 总耗时 %.2fs", task_id, time.perf_counter() - t_start)
        return task
