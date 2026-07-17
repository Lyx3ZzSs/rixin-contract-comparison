from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.errors import TaskTransitionConflict
from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.infrastructure.execution_state import TaskExecutionContext
from app.infrastructure.task_repository import TaskRepository, default_task_repository
from app.models import CompareOptions, CompareTask
from app.services.extractors.base import DocumentExtractor
from app.services.pipeline import ComparePipeline, PipelineContext
from app.services.pipeline_stages import ExtractionStage
from app.services.report_generator import ReportGenerator
from app.utils.id_utils import generate_task_id


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
            DocumentUnderstandingStage,
            DiffQualityStage,
            EvidenceStage,
            MatchStage,
            ModelRoutingStage,
            OcrQualityStage,
            OcrRemediationStage,
            PreClauseDiffStage,
            SigningRegionStage,
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
                DocumentUnderstandingStage(artifact_store=self.artifact_store),
                DocumentPreparationStage(artifact_store=self.artifact_store),
                PreClauseDiffStage(artifact_store=self.artifact_store),
                SigningRegionStage(artifact_store=self.artifact_store),
                SplitStage(artifact_store=self.artifact_store),
                MatchStage(artifact_store=self.artifact_store),
                ClauseDiffStage(artifact_store=self.artifact_store),
                EvidenceStage(),
                OcrQualityStage(artifact_store=self.artifact_store),
                OcrRemediationStage(artifact_store=self.artifact_store),
                ModelRoutingStage(artifact_store=self.artifact_store),
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
        compare_options: CompareOptions | None = None,
        execution_context: TaskExecutionContext | None = None,
    ) -> CompareTask:
        settings.ensure_storage()
        task_id = task_id or generate_task_id()
        task = self._load_or_create_task(
            task_id=task_id,
            original_pdf=Path(original_pdf),
            compare_pdf=Path(compare_pdf),
            original_filename=original_filename,
            compare_filename=compare_filename,
            compare_options=compare_options,
            execution_context=execution_context,
        )

        ctx = PipelineContext(
            task=task,
            original_pdf=Path(original_pdf),
            compare_pdf=Path(compare_pdf),
            execution_context=execution_context,
            progress_callback=self._make_progress_callback(task.task_id, execution_context),
        )
        pipeline = self._build_pipeline()
        pipeline.run(ctx)
        return ctx.task

    def _make_progress_callback(self, task_id: str, execution_context: TaskExecutionContext | None = None):
        def callback(percent: int, stage: str, detail: dict | None = None) -> None:
            from app.services.progress_bus import ProgressBus, ProgressEvent

            if execution_context is not None:
                execution_context.cancellation_token.raise_if_cancelled()
                coordinator = getattr(execution_context.cancellation_token, "coordinator", None)
                if coordinator is not None and coordinator.has_terminal_dependencies:
                    coordinator.commit_progress(
                        execution_context.job_id,
                        worker_id=execution_context.worker_id,
                        stage=stage,
                        progress_percent=percent,
                        detail=detail,
                    )
                    return
            progress = min(max(percent, 0), 99)

            def mutate(task: CompareTask) -> None:
                task.stage = stage
                task.progress_percent = max(task.progress_percent, progress)

            try:
                task = self.repository.update_compare_task(task_id, mutate)
            except FileNotFoundError:
                task = CompareTask(task_id=task_id, stage=stage, progress_percent=progress)
                self.repository.save_compare_task(task)

            if execution_context is not None:
                execution_context.cancellation_token.raise_if_cancelled()
            ProgressBus.get_instance().publish(
                ProgressEvent(
                    task_id=task_id,
                    stage=task.stage,
                    progress_percent=task.progress_percent,
                    status="PROCESSING",
                    detail=detail,
                )
            )

        return callback

    def _load_or_create_task(
        self,
        *,
        task_id: str,
        original_pdf: Path,
        compare_pdf: Path,
        original_filename: str | None,
        compare_filename: str | None,
        compare_options: CompareOptions | None,
        execution_context: TaskExecutionContext | None = None,
    ) -> CompareTask:
        try:
            task = self.repository.load_compare_task(task_id)
        except FileNotFoundError:
            task = CompareTask(task_id=task_id)
        else:
            if task.status != "PROCESSING":
                raise TaskTransitionConflict(
                    f"任务 {task.task_id} 不允许从 {task.status}/{task.terminal_reason} 重新进入执行。"
                )

        coordinator = (
            getattr(execution_context.cancellation_token, "coordinator", None)
            if execution_context is not None
            else None
        )
        if coordinator is not None and coordinator.has_terminal_dependencies:
            return coordinator.commit_progress(
                execution_context.job_id,
                worker_id=execution_context.worker_id,
                stage="文档解析中",
                progress_percent=8,
            )

        task.status = "PROCESSING"
        task.stage = "文档解析中"
        task.progress_percent = 8
        task.errors = []
        task.original_filename = original_filename or task.original_filename or original_pdf.name
        task.compare_filename = compare_filename or task.compare_filename or compare_pdf.name
        task.original_pdf_path = str(original_pdf)
        task.compare_pdf_path = str(compare_pdf)
        if compare_options is not None:
            task.compare_options = compare_options
        task.updated_at = datetime.now(UTC).isoformat()
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
