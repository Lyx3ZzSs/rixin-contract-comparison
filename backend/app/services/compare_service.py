from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.infrastructure.task_repository import TaskRepository, default_task_repository
from app.models import CompareTask
from app.services.extractors.base import DocumentExtractor
from app.services.pipeline import ComparePipeline, PipelineContext
from app.services.pipeline_stages import ExtractionStage
from app.services.report_generator import ReportGenerator
from app.utils.id_utils import generate_task_id

logger = logging.getLogger(__name__)


class CompareService:
    def __init__(
        self,
        extractor: DocumentExtractor | None = None,
        structured_extractor: DocumentExtractor | None = None,
        repository: TaskRepository = default_task_repository,
        artifact_store: ArtifactStore = default_artifact_store,
    ) -> None:
        self._extractor = extractor
        self._structured_extractor = structured_extractor
        self.repository = repository
        self.artifact_store = artifact_store
        self._report_generator = ReportGenerator()

    def _build_pipeline(self) -> ComparePipeline:
        from app.services.pipeline_stages import (
            ClauseDiffStage,
            DocumentPreparationStage,
            DiffQualityStage,
            EvidenceStage,
            MatchStage,
            PreClauseDiffStage,
            SplitStage,
            SummaryStage,
            VisualizationStage,
        )

        extraction = ExtractionStage(
            extractor=self._extractor,
            structured_extractor=self._structured_extractor,
            artifact_store=self.artifact_store,
        )
        return ComparePipeline(
            stages=[
                extraction,
                DocumentPreparationStage(artifact_store=self.artifact_store),
                PreClauseDiffStage(artifact_store=self.artifact_store),
                SplitStage(artifact_store=self.artifact_store),
                MatchStage(artifact_store=self.artifact_store),
                ClauseDiffStage(artifact_store=self.artifact_store),
                EvidenceStage(),
                DiffQualityStage(artifact_store=self.artifact_store),
                VisualizationStage(artifact_store=self.artifact_store),
                SummaryStage(),
            ],
            repository=self.repository,
        )

    def compare(
        self,
        original_pdf: str | Path,
        compare_pdf: str | Path,
        task_id: str | None = None,
        original_filename: str | None = None,
        compare_filename: str | None = None,
    ) -> CompareTask:
        settings.ensure_storage()
        task_id = task_id or generate_task_id()
        task = self._load_or_create_task(
            task_id=task_id,
            original_pdf=Path(original_pdf),
            compare_pdf=Path(compare_pdf),
            original_filename=original_filename,
            compare_filename=compare_filename,
        )

        ctx = PipelineContext(
            task=task,
            original_pdf=Path(original_pdf),
            compare_pdf=Path(compare_pdf),
            progress_callback=self._make_progress_callback(task.task_id),
        )
        pipeline = self._build_pipeline()
        try:
            pipeline.run(ctx)
        except Exception as exc:
            logger.exception("Compare task failed")
            ctx.task = self._mark_failed(ctx.task, str(exc))
            raise
        return ctx.task

    def _make_progress_callback(self, task_id: str):
        def callback(percent: int, stage: str, detail: dict | None = None) -> None:
            from app.services.progress_bus import ProgressBus, ProgressEvent
            progress = min(max(percent, 0), 99)

            def mutate(task: CompareTask) -> None:
                task.stage = stage
                task.progress_percent = max(task.progress_percent, progress)

            try:
                task = self.repository.update_compare_task(task_id, mutate)
            except FileNotFoundError:
                task = CompareTask(task_id=task_id, stage=stage, progress_percent=progress)
                self.repository.save_compare_task(task)

            ProgressBus.get_instance().publish(ProgressEvent(
                task_id=task_id,
                stage=task.stage,
                progress_percent=task.progress_percent,
                status="PROCESSING",
                detail=detail,
            ))
        return callback

    def _load_or_create_task(
        self,
        *,
        task_id: str,
        original_pdf: Path,
        compare_pdf: Path,
        original_filename: str | None,
        compare_filename: str | None,
    ) -> CompareTask:
        try:
            task = self.repository.load_compare_task(task_id)
        except FileNotFoundError:
            task = CompareTask(task_id=task_id)

        task.status = "PROCESSING"
        task.stage = "文档解析中"
        task.progress_percent = 8
        task.original_filename = original_filename or task.original_filename or original_pdf.name
        task.compare_filename = compare_filename or task.compare_filename or compare_pdf.name
        task.original_pdf_path = str(original_pdf)
        task.compare_pdf_path = str(compare_pdf)
        task.updated_at = datetime.now(UTC).isoformat()
        self.repository.save_compare_task(task)
        return task

    def _mark_failed(self, task: CompareTask, error: str) -> CompareTask:
        def mutate(persisted: CompareTask) -> None:
            persisted.status = "FAILED"
            persisted.stage = "失败"
            persisted.progress_percent = 100
            if error not in persisted.errors:
                persisted.errors.append(error)

        from app.services.progress_bus import ProgressBus, ProgressEvent
        ProgressBus.get_instance().publish(ProgressEvent(
            task_id=task.task_id,
            stage="失败",
            progress_percent=100,
            status="FAILED",
            detail={"error": error},
        ))

        try:
            return self.repository.update_compare_task(task.task_id, mutate)
        except FileNotFoundError:
            mutate(task)
            self.repository.save_compare_task(task)
            return task

    def ensure_report(self, task: CompareTask) -> CompareTask:
        settings.ensure_storage()
        report_path = self.artifact_store.report_pdf_path(task.task_id)

        self._report_generator.generate(task, report_path)
        return self.repository.update_compare_task(
            task.task_id,
            lambda persisted: self._copy_report_artifacts(persisted, task, report_path),
        )

    def _copy_report_artifacts(self, target: CompareTask, source: CompareTask, report_path: Path) -> None:
        target.report_pdf_path = str(report_path)
        target.stage = "已完成"
        target.progress_percent = 100
