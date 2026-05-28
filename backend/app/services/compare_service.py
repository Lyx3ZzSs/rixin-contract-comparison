from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.models import CompareTask
from app.services.extractors.base import DocumentExtractor
from app.services.pipeline import ComparePipeline, PipelineContext
from app.services.pipeline_stages import ExtractionStage
from app.services.report_generator import ReportGenerator
from app.services.screenshot_service import ScreenshotService
from app.utils.id_utils import generate_task_id
from app.utils.json_utils import save_task

logger = logging.getLogger(__name__)


class CompareService:
    def __init__(
        self,
        extractor: DocumentExtractor | None = None,
        structured_extractor: DocumentExtractor | None = None,
    ) -> None:
        self._extractor = extractor
        self._structured_extractor = structured_extractor
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
        return ComparePipeline(stages=[
            extraction,
            PreClauseDiffStage(),
            SplitStage(),
            MatchStage(),
            ClauseDiffStage(),
            EvidenceStage(),
            AnalysisStage(),
            VisualizationStage(),
            SummaryStage(),
        ])

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
        task = CompareTask(
            task_id=task_id,
            stage="文档解析中",
            progress_percent=8,
            original_filename=original_filename or Path(original_pdf).name,
            compare_filename=compare_filename or Path(compare_pdf).name,
            original_pdf_path=str(original_pdf),
            compare_pdf_path=str(compare_pdf),
        )
        save_task(task)

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
            ctx.task.status = "FAILED"
            ctx.task.stage = "失败"
            ctx.task.progress_percent = 100
            ctx.task.errors.append(str(exc))
            ctx.task.updated_at = datetime.now(UTC).isoformat()
            save_task(ctx.task)
            raise
        return ctx.task

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
        task.report_pdf_path = str(report_path)
        task.stage = "已完成"
        task.progress_percent = 100
        task.updated_at = datetime.now(UTC).isoformat()
        save_task(task)
        return task
