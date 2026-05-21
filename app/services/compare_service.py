from __future__ import annotations

import logging
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.models import Clause, CompareTask
from app.services.clause_splitter import ClauseSplitter
from app.services.cover_metadata import CoverMetadataComparator
from app.services.diff_engine import DiffEngine
from app.services.evidence_locator import EvidenceLocator
from app.services.extractors import build_document_extractor
from app.services.extractors.base import DocumentExtractionError, DocumentExtractor, ExtractionResult
from app.services.matcher import ClauseMatcher
from app.services.pdf_highlighter import PdfHighlighter
from app.services.report_generator import ReportGenerator
from app.services.screenshot_service import ScreenshotService
from app.services.table_compare import TableComparator
from app.utils.id_utils import generate_task_id
from app.utils.json_utils import save_task

logger = logging.getLogger(__name__)


class CompareService:
    def __init__(
        self,
        extractor: DocumentExtractor | None = None,
        structured_extractor: DocumentExtractor | None = None,
    ) -> None:
        self.extractor = extractor or build_document_extractor()
        self.structured_extractor = structured_extractor
        self.cover_metadata = CoverMetadataComparator()
        self.table_comparator = TableComparator()
        self.splitter = ClauseSplitter()
        self.matcher = ClauseMatcher(settings.match_threshold)
        self.diff_engine = DiffEngine()
        self.evidence_locator = EvidenceLocator()
        self.highlighter = PdfHighlighter()
        self.screenshot_service = ScreenshotService()
        self.report_generator = ReportGenerator()

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
            original_filename=original_filename or Path(original_pdf).name,
            compare_filename=compare_filename or Path(compare_pdf).name,
            original_pdf_path=str(original_pdf),
            compare_pdf_path=str(compare_pdf),
        )
        save_task(task)

        try:
            original_extraction = self.extractor.extract(original_pdf, task_id=task_id)
            compare_extraction = self.extractor.extract(compare_pdf, task_id=task_id)
            original_extraction, compare_extraction = self._align_structured_extractions(
                original_pdf,
                compare_pdf,
                task_id,
                original_extraction,
                compare_extraction,
            )
            task.extractor_used = self._merge_extractor_names(
                original_extraction.extractor_used,
                compare_extraction.extractor_used,
            )
            task.ocr_raw_result_path = self._merge_raw_paths(
                original_extraction.raw_result_path,
                compare_extraction.raw_result_path,
            )
            task.parse_warnings.extend(original_extraction.warnings)
            task.parse_warnings.extend(compare_extraction.warnings)

            original_doc = original_extraction.document
            compare_doc = compare_extraction.document
            metadata_diffs = self.cover_metadata.build_diffs(original_doc, compare_doc)
            table_diffs, table_warnings = self.table_comparator.build_diffs(
                original_doc,
                compare_doc,
                start_index=len(metadata_diffs) + 1,
            )
            task.parse_warnings.extend(table_warnings)
            original_clauses = self.splitter.split(original_doc, "O")
            compare_clauses = self.splitter.split(compare_doc, "N")
            pairs = self.matcher.match(original_clauses, compare_clauses)
            clause_diffs = self.diff_engine.build_diffs(pairs, start_index=len(metadata_diffs) + len(table_diffs) + 1)
            diffs = [*metadata_diffs, *table_diffs, *clause_diffs]
            original_locate_clauses = self._clauses_for_evidence(original_clauses, [pair.original for pair in pairs])
            compare_locate_clauses = self._clauses_for_evidence(compare_clauses, [pair.compare for pair in pairs])
            diffs = self.evidence_locator.locate(diffs, original_locate_clauses, compare_locate_clauses)
            diffs = self.diff_engine.deduplicate_overlaps(diffs)
            task.diffs = diffs
            task.ai_summary = self._program_summary(diffs)
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

    def ensure_report(self, task: CompareTask) -> CompareTask:
        settings.ensure_storage()
        report_path = settings.reports_dir / task.task_id / "contract_compare_report.pdf"

        page_screenshot_dir = settings.screenshots_dir / task.task_id / "pages"
        if not task.original_page_screenshots and task.original_highlight_pdf_path:
            task.original_page_screenshots = self.screenshot_service.create_page_screenshots(
                task.original_highlight_pdf_path,
                page_screenshot_dir,
                "original",
                settings.report_max_screenshot_pages,
            )
        if not task.compare_page_screenshots and task.compare_highlight_pdf_path:
            task.compare_page_screenshots = self.screenshot_service.create_page_screenshots(
                task.compare_highlight_pdf_path,
                page_screenshot_dir,
                "compare",
                settings.report_max_screenshot_pages,
            )

        self.report_generator.generate(task, report_path)
        task.report_pdf_path = str(report_path)
        task.updated_at = datetime.now(UTC).isoformat()
        save_task(task)
        return task

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

    def _align_structured_extractions(
        self,
        original_pdf: str | Path,
        compare_pdf: str | Path,
        task_id: str,
        original: ExtractionResult,
        compare: ExtractionResult,
    ) -> tuple[ExtractionResult, ExtractionResult]:
        if not settings.align_structured_extraction:
            return original, compare

        original_structured = self._is_structured_extractor_used(original.extractor_used)
        compare_structured = self._is_structured_extractor_used(compare.extractor_used)
        original_pymupdf = self._is_pymupdf_extractor_used(original.extractor_used)
        compare_pymupdf = self._is_pymupdf_extractor_used(compare.extractor_used)

        if original_structured and compare_pymupdf:
            compare = self._upgrade_to_structured_extraction(compare_pdf, task_id, compare, "compare", original.extractor_used)
        elif compare_structured and original_pymupdf:
            original = self._upgrade_to_structured_extraction(original_pdf, task_id, original, "original", compare.extractor_used)
        return original, compare

    def _upgrade_to_structured_extraction(
        self,
        pdf_path: str | Path,
        task_id: str,
        current: ExtractionResult,
        side: str,
        reference_extractor_used: str,
    ) -> ExtractionResult:
        try:
            extractor = self.structured_extractor or self._matching_structured_extractor(reference_extractor_used)
            upgraded = extractor.extract(pdf_path, task_id=task_id)
        except DocumentExtractionError as exc:
            current.warnings.append(
                f"为保持表格边界一致，{side} 尝试切换结构化 OCR 抽取失败，已保留 PyMuPDF 结果: {exc}"
            )
            return current
        except Exception as exc:  # pragma: no cover - defensive around remote OCR clients
            logger.exception("Structured extraction alignment failed")
            current.warnings.append(
                f"为保持表格边界一致，{side} 尝试切换结构化 OCR 抽取失败，已保留 PyMuPDF 结果: {exc}"
            )
            return current

        upgraded.warnings = [
            *current.warnings,
            f"为保持表格边界一致，{side} 已从 PyMuPDF 切换为结构化 OCR 抽取。",
            *upgraded.warnings,
        ]
        return upgraded

    def _is_pymupdf_extractor_used(self, extractor_used: str) -> bool:
        return (extractor_used or "").lower() == "pymupdf"

    def _is_structured_extractor_used(self, extractor_used: str) -> bool:
        name = (extractor_used or "").lower()
        if not name or "ocr_only" in name:
            return False
        return any(marker in name for marker in ("ppstructure", "paddleocr_vl", "vl_ocr_hybrid"))

    def _matching_structured_extractor(self, extractor_used: str) -> DocumentExtractor:
        name = (extractor_used or "").lower()
        if "vl_ocr_hybrid" in name:
            return build_document_extractor("vl_ocr_hybrid")
        if "paddleocr_vl" in name:
            return build_document_extractor("paddleocr_vl")
        return build_document_extractor("ppstructure_ocr_hybrid")

    def _program_summary(self, diffs) -> str:
        if not diffs:
            return "未发现合同条款差异。"
        types = Counter(diff.diff_type for diff in diffs)
        risks = Counter(diff.ai_analysis.risk_level if diff.ai_analysis else "LOW" for diff in diffs)
        key_elements = Counter(diff.ai_analysis.contract_element for diff in diffs if diff.ai_analysis).most_common(3)
        element_text = "、".join(element for element, _ in key_elements) or "一般条款"
        return (
            f"本次共识别 {len(diffs)} 处差异，其中新增 {types['ADD']} 处、删除 {types['DELETE']} 处、"
            f"修改 {types['MODIFY']} 处。风险分布为高风险 {risks['HIGH']} 处、中风险 {risks['MEDIUM']} 处、"
            f"低风险 {risks['LOW']} 处，重点关注 {element_text}。请结合业务背景逐条复核。"
        )

    def _clauses_for_evidence(self, base_clauses: list[Clause], pair_clauses: list[Clause | None]) -> list[Clause]:
        by_id = {clause.clause_id: clause for clause in base_clauses}
        for clause in pair_clauses:
            if clause is not None:
                by_id[clause.clause_id] = clause
        return list(by_id.values())

    def _merge_extractor_names(self, original: str, compare: str) -> str:
        if original == compare:
            return original
        return f"original:{original},compare:{compare}"

    def _merge_raw_paths(self, original: str, compare: str) -> str:
        return "\n".join(path for path in [original, compare] if path)
