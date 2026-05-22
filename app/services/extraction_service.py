from __future__ import annotations

from datetime import UTC, datetime

from app.models_extraction import ExtractionFieldDef, ExtractionTask
from app.services.ppocrv5_llm_extraction import PPOCRV5LLMExtractionClient
from app.utils.json_utils import save_extraction_task


class ExtractionService:
    def __init__(self, client: PPOCRV5LLMExtractionClient | None = None) -> None:
        self.client = client or PPOCRV5LLMExtractionClient()

    def extract(
        self,
        file_path: str,
        field_defs: list[ExtractionFieldDef],
        task_id: str,
        filename: str = "",
    ) -> ExtractionTask:
        task = ExtractionTask(
            task_id=task_id,
            filename=filename,
            file_path=file_path,
            fields=field_defs,
            extractor_used=self.client.name,
        )
        save_extraction_task(task)

        try:
            result = self.client.extract_fields(file_path, field_defs, task_id=task_id)
            task.results = result.results
            task.raw_result_path = result.raw_result_path
            task.converted_file_path = result.converted_file_path
            task.status = "COMPLETED"
        except Exception as exc:
            task.status = "FAILED"
            task.errors.append(str(exc))
        finally:
            task.updated_at = datetime.now(UTC).isoformat()
            save_extraction_task(task)

        return task
