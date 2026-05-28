from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.infrastructure.task_repository import TaskRepository, default_task_repository
from app.models import CompareTask
from app.services.extractors.base import DocumentExtractor
from app.services.pipeline import ComparePipeline, PipelineContext
from app.services.pipeline_stages import ExtractionStage
from app.services.report_generator import ReportGenerator
from app.services.screenshot_service import ScreenshotService
from app.utils.id_utils import generate_task_id

logger = logging.getLogger(__name__)


class CompareService:
    def __init__(
        self,
        extractor: DocumentExtractor | None = None,
        structured_extractor: DocumentExtractor | None = None,
        repository: TaskRepository = default_task_repository,
    ) -> None:
        self._extractor = extractor
        self._structured_extractor = structured_extractor
        self.repository = repository
        self._report_generator = ReportGenerator()
        self._screenshot_service = ScreenshotService()

    def _build_pipeline(self) -> ComparePipeline:
        from app.services.pipeline_stages import (
            AnalysisStage,
            ClauseDiffStage,
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
        )
        return ComparePipeline(
            stages=[
                extraction,
                PreClauseDiffStage(),
                SplitStage(),
                MatchStage(),
                ClauseDiffStage(),
                EvidenceStage(),
                AnalysisStage(),
                VisualizationStage(),
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
        )
        pipeline = self._build_pipeline()
        try:
            pipeline.run(ctx)
        except Exception as exc:
            logger.exception("Compare task failed")
            ctx.task = self._mark_failed(ctx.task, str(exc))
            raise
        return ctx.task

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

        try:
            return self.repository.update_compare_task(task.task_id, mutate)
        except FileNotFoundError:
            mutate(task)
            self.repository.save_compare_task(task)
            return task

    def ensure_report(self, task: CompareTask) -> CompareTask:
        settings.ensure_storage()
        report_path = settings.reports_dir / task.task_id / "contract_compare_report.pdf"

        page_screenshot_dir = settings.screenshots_dir / task.task_id / "pages"
        if not task.original_page_screenshots and task.original_highlight_pdf_path:
            task.original_page_screenshots = self._screenshot_service.create_page_screenshots(
                task.original_highlight_pdf_path,
                page_screenshot_dir,
                "original",
                settings.report_max_screenshot_pages,
            )
        if not task.compare_page_screenshots and task.compare_highlight_pdf_path:
            task.compare_page_screenshots = self._screenshot_service.create_page_screenshots(
                task.compare_highlight_pdf_path,
                page_screenshot_dir,
                "compare",
                settings.report_max_screenshot_pages,
            )

        self._report_generator.generate(task, report_path)
        return self.repository.update_compare_task(
            task.task_id,
            lambda persisted: self._copy_report_artifacts(persisted, task, report_path),
        )

    def _copy_report_artifacts(self, target: CompareTask, source: CompareTask, report_path: Path) -> None:
        target.report_pdf_path = str(report_path)
        target.original_page_screenshots = source.original_page_screenshots
        target.compare_page_screenshots = source.compare_page_screenshots
        target.stage = "已完成"
        target.progress_percent = 100
