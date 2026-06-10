from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import fitz

from app.models import Document, LayoutQualityReport, Page, ParseWarningDetail
from app.services.extractors.base import DocumentExtractor, ExtractionResult

logger = logging.getLogger(__name__)


class WindowedExtractionWrapper:
    """Wraps a DocumentExtractor to process large PDFs in page windows.

    Splits the PDF into windows of *window_size* pages with *overlap* pages
    of overlap between consecutive windows.  Each window is extracted
    separately by the inner extractor, then results are merged.
    """

    def __init__(
        self,
        inner: DocumentExtractor,
        window_size: int = 10,
        overlap: int = 1,
    ) -> None:
        self._inner = inner
        self._window_size = window_size
        self._overlap = overlap

    @property
    def name(self) -> str:
        return self._inner.name

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        path = Path(path)
        page_count = self._get_page_count(path)
        if page_count <= self._window_size or self._window_size <= 0:
            return self._inner.extract(path, task_id=task_id)
        return self._extract_windowed(path, page_count, task_id)

    def _get_page_count(self, path: Path) -> int:
        try:
            with fitz.open(str(path)) as doc:
                return len(doc)
        except Exception:
            return 0

    def _extract_windowed(self, path: Path, page_count: int, task_id: str | None) -> ExtractionResult:
        windows = self._build_windows(page_count)
        logger.info("Windowed extraction: %d pages → %d windows (size=%d, overlap=%d)",
                     page_count, len(windows), self._window_size, self._overlap)

        results: list[ExtractionResult] = []
        for win_start, win_end in windows:
            win_result = self._extract_window(path, win_start, win_end, task_id)
            results.append(win_result)

        return self._merge_results(results, path)

    def _build_windows(self, page_count: int) -> list[tuple[int, int]]:
        windows: list[tuple[int, int]] = []
        step = max(1, self._window_size - self._overlap)
        start = 0
        while start < page_count:
            end = min(start + self._window_size, page_count)
            windows.append((start, end))
            if end >= page_count:
                break
            start += step
        return windows

    def _extract_window(self, path: Path, start: int, end: int, task_id: str | None) -> ExtractionResult:
        with fitz.open(str(path)) as src:
            window_pdf = fitz.open()
            window_pdf.insert_pdf(src, from_page=start, to_page=end - 1)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = tmp.name
            window_pdf.save(tmp_path)
            window_pdf.close()

        try:
            result = self._inner.extract(tmp_path, task_id=task_id)
            for page in result.document.pages:
                page.page_no += start
                for block in page.blocks:
                    block.page_no += start
                    for char_box in block.char_boxes:
                        char_box.page_no += start
            return result
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _merge_results(self, results: list[ExtractionResult], original_path: Path) -> ExtractionResult:
        all_pages: list[Page] = []
        all_warnings: list[str] = []
        seen_page_nos: set[int] = set()
        extractor_used = ""
        raw_result_path = ""
        layout_quality_reports: list[LayoutQualityReport] = []

        for result in results:
            extractor_used = extractor_used or result.extractor_used
            raw_result_path = result.raw_result_path or raw_result_path
            all_warnings.extend(result.warnings)
            if result.layout_quality is not None:
                layout_quality_reports.append(result.layout_quality)
            for page in result.document.pages:
                if page.page_no not in seen_page_nos:
                    seen_page_nos.add(page.page_no)
                    all_pages.append(page)

        all_pages.sort(key=lambda p: p.page_no)

        if not all_pages:
            return ExtractionResult(
                document=Document(filename=original_path.name, path=str(original_path), page_count=0),
                extractor_used=extractor_used or "windowed",
                warnings=all_warnings,
            )

        return ExtractionResult(
            document=Document(
                filename=original_path.name,
                path=str(original_path),
                page_count=len(all_pages),
                pages=all_pages,
            ),
            extractor_used=extractor_used or "windowed",
            raw_result_path=raw_result_path,
            warnings=all_warnings,
            layout_quality=self._merge_layout_quality(layout_quality_reports, all_pages),
        )

    def _merge_layout_quality(
        self,
        reports: list[LayoutQualityReport],
        pages: list[Page],
    ) -> LayoutQualityReport | None:
        if not reports:
            return None
        label_counts: dict[str, int] = {}
        warnings: list[ParseWarningDetail] = []
        warning_keys: set[tuple[str, str, int | None, str]] = set()
        for report in reports:
            for label, count in report.label_counts.items():
                label_counts[label] = label_counts.get(label, 0) + count
            for warning in report.warnings:
                key = (warning.code, warning.message, warning.page_no, warning.source)
                if key not in warning_keys:
                    warnings.append(warning)
                    warning_keys.add(key)
        blocks = [block for page in pages for block in page.blocks]
        return LayoutQualityReport(
            parser_version=reports[0].parser_version,
            mode=reports[0].mode,
            page_count=len(pages),
            region_count=sum(report.region_count for report in reports),
            label_counts=label_counts,
            invalid_bbox_count=sum(report.invalid_bbox_count for report in reports),
            empty_region_count=sum(report.empty_region_count for report in reports),
            table_region_count=sum(report.table_region_count for report in reports),
            table_cell_matched_count=sum(report.table_cell_matched_count for report in reports),
            table_cell_unmatched_count=sum(report.table_cell_unmatched_count for report in reports),
            ocr_block_count=sum(not block.source.startswith("ppstructure_layout_only") for block in blocks),
            matched_ocr_block_count=sum(
                bool(block.layout_block_id) and not block.source.startswith("ppstructure_layout_only")
                for block in blocks
            ),
            unmatched_ocr_block_count=sum(
                not block.layout_block_id and not block.source.startswith("ppstructure_layout_only")
                for block in blocks
            ),
            ambiguous_match_count=sum(block.layout_match_status == "ambiguous" for block in blocks),
            meaningful_unmatched_count=sum(block.layout_match_status == "meaningful_unmatched" for block in blocks),
            noise_unmatched_count=sum(block.layout_match_status == "noise_unmatched" for block in blocks),
            structure_only_count=sum(block.layout_match_status == "structure_only" for block in blocks),
            reading_order_count=sum(block.reading_order is not None for block in blocks),
            reading_order_conflict_count=sum(report.reading_order_conflict_count for report in reports),
            page_quality=[page_quality for report in reports for page_quality in report.page_quality],
            warnings=warnings,
        )
