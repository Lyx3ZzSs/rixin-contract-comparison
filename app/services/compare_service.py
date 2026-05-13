from __future__ import annotations

import logging
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.models import CompareTask
from app.services.ai_review import AIReviewService, AISummaryService
from app.services.clause_splitter import ClauseSplitter
from app.services.diff_engine import DiffEngine
from app.services.evidence_locator import EvidenceLocator
from app.services.matcher import ClauseMatcher
from app.services.pdf_highlighter import PdfHighlighter
from app.services.pdf_parser import PdfParser
from app.services.report_generator import ReportGenerator
from app.services.screenshot_service import ScreenshotService
from app.utils.id_utils import generate_task_id
from app.utils.json_utils import save_task

logger = logging.getLogger(__name__)


class CompareService:
    def __init__(self) -> None:
        self.parser = PdfParser()
        self.splitter = ClauseSplitter()
        self.matcher = ClauseMatcher(settings.match_threshold)
        self.diff_engine = DiffEngine()
        self.evidence_locator = EvidenceLocator()
        self.ai_review = AIReviewService()
        self.ai_summary = AISummaryService()
        self.highlighter = PdfHighlighter()
        self.screenshot_service = ScreenshotService()
        self.report_generator = ReportGenerator()

    def compare(
        self,
        original_pdf: str | Path,
        compare_pdf: str | Path,
        enable_ai_analysis: bool = True,
        task_id: str | None = None,
        original_filename: str | None = None,
        compare_filename: str | None = None,
    ) -> CompareTask:
        settings.ensure_storage()
        task_id = task_id or generate_task_id()
        task = CompareTask(
            task_id=task_id,
            original_filename=original_filename or Path(original_pdf).name,
            compare_filename=compare_filename or Path(compare_pdf).name,
            original_pdf_path=str(original_pdf),
            compare_pdf_path=str(compare_pdf),
        )
        save_task(task)

        try:
            original_doc = self.parser.parse(original_pdf)
            compare_doc = self.parser.parse(compare_pdf)
            original_clauses = self.splitter.split(original_doc, "O")
            compare_clauses = self.splitter.split(compare_doc, "N")
            pairs = self.matcher.match(original_clauses, compare_clauses)
            diffs = self.diff_engine.build_diffs(pairs)
            diffs = self.evidence_locator.locate(diffs)
            diffs = self.ai_review.analyze_diffs(diffs, enabled=enable_ai_analysis)
            task.diffs = diffs
            task.ai_summary = self.ai_summary.summarize(diffs, enabled=enable_ai_analysis)
            self._refresh_stats(task)

            original_highlight = settings.highlighted_dir / task_id / "original_highlighted.pdf"
            compare_highlight = settings.highlighted_dir / task_id / "compare_highlighted.pdf"
            try:
                self.highlighter.highlight_original(original_pdf, task.diffs, original_highlight)
                self.highlighter.highlight_compare(compare_pdf, task.diffs, compare_highlight)
                task.original_highlight_pdf_path = str(original_highlight)
                task.compare_highlight_pdf_path = str(compare_highlight)
            except Exception as exc:
                logger.exception("PDF highlight failed")
                task.errors.append(f"PDF 高亮生成失败: {exc}")
                original_highlight = self._copy_fallback_pdf(original_pdf, original_highlight)
                compare_highlight = self._copy_fallback_pdf(compare_pdf, compare_highlight)
                task.original_highlight_pdf_path = str(original_highlight)
                task.compare_highlight_pdf_path = str(compare_highlight)

            try:
                screenshot_dir = settings.screenshots_dir / task_id
                task.diffs = self.screenshot_service.create_screenshots(
                    task.original_highlight_pdf_path,
                    task.compare_highlight_pdf_path,
                    task.diffs,
                    screenshot_dir,
                )
            except Exception as exc:
                logger.exception("Screenshot generation failed")
                task.errors.append(f"差异截图生成失败: {exc}")

            try:
                report_path = settings.reports_dir / task_id / "contract_ai_compare_report.pdf"
                self.report_generator.generate(task, report_path)
                task.report_pdf_path = str(report_path)
            except Exception as exc:
                logger.exception("Report generation failed")
                task.errors.append(f"PDF 报告生成失败: {exc}")

            task.status = "COMPLETED"
            task.updated_at = datetime.now(UTC).isoformat()
            save_task(task)
            return task
        except Exception as exc:
            logger.exception("Compare task failed")
            task.status = "FAILED"
            task.errors.append(str(exc))
            task.updated_at = datetime.now(UTC).isoformat()
            save_task(task)
            raise

    def _refresh_stats(self, task: CompareTask) -> None:
        task.diff_count = len(task.diffs)
        risks = Counter(diff.ai_analysis.risk_level if diff.ai_analysis else "LOW" for diff in task.diffs)
        task.high_risk_count = risks["HIGH"]
        task.medium_risk_count = risks["MEDIUM"]
        task.low_risk_count = risks["LOW"]

    def _copy_fallback_pdf(self, source: str | Path, destination: str | Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return destination
