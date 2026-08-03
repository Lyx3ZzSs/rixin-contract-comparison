from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz

from app.models import Document, Page
from app.services.document_profiler import DocumentProfiler
from app.services.extractors.base import DocumentExtractionError, ExtractionResult
from app.services.extractors.pymupdf import PyMuPDFExtractor
from app.services.pipeline_metrics import PerformanceRecorder
from app.services.table_compare.constants import TABLE_HEADERS


SIGNING_PATTERN = re.compile(
    r"签署页|签字页|签章页|(?:甲方|乙方|买方|卖方).{0,8}(?:盖章|签章)|"
    r"法定代表人.{0,8}(?:签字|签章)|授权代表.{0,8}(?:签字|签章)|"
    r"signaturepage|authorizedrepresentative.{0,12}signature",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NativeFastPathDecision:
    accepted: bool
    extraction: ExtractionResult | None
    reasons: tuple[str, ...]
    metrics: dict[str, Any]


class NativeFastPathEvaluator:
    """Fail-closed eligibility gate for the native PDF comparison path."""

    def __init__(
        self,
        *,
        min_chars_per_page: int = 80,
        min_char_box_coverage: float = 0.98,
        max_suspicious_char_ratio: float = 0.01,
        extractor: PyMuPDFExtractor | None = None,
        profiler: DocumentProfiler | None = None,
    ) -> None:
        self.min_chars_per_page = max(1, min_chars_per_page)
        self.min_char_box_coverage = max(0.0, min(1.0, min_char_box_coverage))
        self.max_suspicious_char_ratio = max(0.0, min(1.0, max_suspicious_char_ratio))
        self.extractor = extractor or PyMuPDFExtractor()
        self.profiler = profiler or DocumentProfiler()

    def evaluate(self, path: Path) -> NativeFastPathDecision:
        recorder = PerformanceRecorder()
        recorder.set_counter("file_size_bytes", path.stat().st_size if path.exists() else 0)
        reasons: set[str] = set()
        try:
            with recorder.measure("native_text_extraction"):
                extraction = self.extractor.extract(path, task_id=None)
        except DocumentExtractionError:
            reasons.add("native_text_extraction_failed")
            return self._decision(None, reasons, recorder)

        with recorder.measure("native_text_quality_gate"):
            self._inspect_document(extraction.document, reasons, recorder)
        try:
            with recorder.measure("native_pdf_feature_gate"):
                self._inspect_pdf_features(path, reasons, recorder)
        except Exception:
            reasons.add("native_pdf_feature_inspection_failed")

        extraction.extractor_used = "pymupdf_fast_path"
        extraction.profile = self.profiler.profile(
            extraction.document,
            extraction.extractor_used,
        )
        extraction.document.profile = extraction.profile
        decision = self._decision(extraction, reasons, recorder)
        extraction.performance = decision.metrics
        return decision

    def _decision(
        self,
        extraction: ExtractionResult | None,
        reasons: set[str],
        recorder: PerformanceRecorder,
    ) -> NativeFastPathDecision:
        ordered_reasons = tuple(sorted(reasons))
        metrics = recorder.snapshot()
        metrics["accepted"] = not ordered_reasons
        metrics["reasons"] = list(ordered_reasons)
        metrics["thresholds"] = {
            "min_chars_per_page": self.min_chars_per_page,
            "min_char_box_coverage": self.min_char_box_coverage,
            "max_suspicious_char_ratio": self.max_suspicious_char_ratio,
        }
        return NativeFastPathDecision(
            accepted=not ordered_reasons,
            extraction=extraction,
            reasons=ordered_reasons,
            metrics=metrics,
        )

    def _inspect_document(
        self,
        document: Document,
        reasons: set[str],
        recorder: PerformanceRecorder,
    ) -> None:
        if document.page_count <= 0 or len(document.pages) != document.page_count:
            reasons.add("page_count_mismatch")
        total_chars = 0
        total_char_boxes = 0
        suspicious_chars = 0
        low_text_pages = 0
        multi_column_pages = 0
        table_text_pages = 0
        signing_text_pages = 0
        for page in document.pages:
            page_chars = [char for block in page.blocks for char in block.text if not char.isspace()]
            page_char_boxes = sum(
                1
                for block in page.blocks
                for char_box in block.char_boxes
                if char_box.char and not char_box.char.isspace()
            )
            total_chars += len(page_chars)
            total_char_boxes += page_char_boxes
            suspicious_chars += sum(self._is_suspicious_char(char) for char in page_chars)
            if len(page_chars) < self.min_chars_per_page:
                low_text_pages += 1
            if self._looks_multi_column(page):
                multi_column_pages += 1
            page_text = "".join(block.text for block in page.blocks)
            if self._looks_like_table_text(page_text):
                table_text_pages += 1
            if SIGNING_PATTERN.search(re.sub(r"\s+", "", page_text)):
                signing_text_pages += 1

        coverage = total_char_boxes / max(1, total_chars)
        suspicious_ratio = suspicious_chars / max(1, total_chars)
        recorder.set_counter("page_count", document.page_count)
        recorder.set_counter("text_char_count", total_chars)
        recorder.set_counter("char_box_count", total_char_boxes)
        recorder.set_counter("char_box_coverage", round(coverage, 6))
        recorder.set_counter("suspicious_char_ratio", round(suspicious_ratio, 6))
        recorder.set_counter("low_text_page_count", low_text_pages)
        recorder.set_counter("multi_column_page_count", multi_column_pages)
        recorder.set_counter("table_text_page_count", table_text_pages)
        recorder.set_counter("signing_text_page_count", signing_text_pages)

        if low_text_pages:
            reasons.add("low_text_page")
        if coverage < self.min_char_box_coverage:
            reasons.add("insufficient_char_box_coverage")
        if suspicious_ratio > self.max_suspicious_char_ratio:
            reasons.add("suspicious_text_encoding")
        if multi_column_pages:
            reasons.add("multi_column_layout")
        if table_text_pages:
            reasons.add("table_like_text")
        if signing_text_pages:
            reasons.add("signing_region_text")

    def _inspect_pdf_features(
        self,
        path: Path,
        reasons: set[str],
        recorder: PerformanceRecorder,
    ) -> None:
        image_pages = 0
        table_pages = 0
        rotated_pages = 0
        form_pages = 0
        annotation_pages = 0
        red_vector_pages = 0
        complex_vector_pages = 0
        with fitz.open(path) as pdf:
            if pdf.needs_pass:
                reasons.add("password_protected_pdf")
                return
            for page in pdf:
                if page.rotation % 360:
                    rotated_pages += 1
                if page.get_image_info(xrefs=True):
                    image_pages += 1
                if page.first_widget is not None:
                    form_pages += 1
                if page.first_annot is not None:
                    annotation_pages += 1
                drawings = page.get_drawings()
                if any(self._is_red_drawing(drawing) for drawing in drawings):
                    red_vector_pages += 1
                if len(drawings) >= 40:
                    complex_vector_pages += 1
                tables = page.find_tables(paths=drawings)
                if tables.tables:
                    table_pages += 1

        recorder.set_counter("image_page_count", image_pages)
        recorder.set_counter("detected_table_page_count", table_pages)
        recorder.set_counter("rotated_page_count", rotated_pages)
        recorder.set_counter("form_page_count", form_pages)
        recorder.set_counter("annotation_page_count", annotation_pages)
        recorder.set_counter("red_vector_page_count", red_vector_pages)
        recorder.set_counter("complex_vector_page_count", complex_vector_pages)
        if image_pages:
            reasons.add("embedded_images")
        if table_pages:
            reasons.add("detected_table")
        if rotated_pages:
            reasons.add("rotated_page")
        if form_pages:
            reasons.add("interactive_form")
        if annotation_pages:
            reasons.add("pdf_annotations")
        if red_vector_pages:
            reasons.add("red_vector_graphics")
        if complex_vector_pages:
            reasons.add("complex_vector_graphics")

    @staticmethod
    def _is_suspicious_char(char: str) -> bool:
        if char == "\ufffd":
            return True
        category = unicodedata.category(char)
        return category in {"Cc", "Cf", "Co", "Cs", "Cn"}

    @staticmethod
    def _looks_multi_column(page: Page) -> bool:
        blocks = [
            block
            for block in page.blocks
            if len(re.sub(r"\s+", "", block.text)) >= 5
            and block.bbox.x1 > block.bbox.x0
            and block.bbox.y1 > block.bbox.y0
        ]
        for index, left in enumerate(blocks):
            for right in blocks[index + 1 :]:
                overlap = min(left.bbox.y1, right.bbox.y1) - max(left.bbox.y0, right.bbox.y0)
                min_height = min(
                    left.bbox.y1 - left.bbox.y0,
                    right.bbox.y1 - right.bbox.y0,
                )
                if overlap <= 0 or overlap / max(1.0, min_height) < 0.6:
                    continue
                horizontal_gap = max(
                    right.bbox.x0 - left.bbox.x1,
                    left.bbox.x0 - right.bbox.x1,
                )
                if horizontal_gap >= max(12.0, page.width * 0.02):
                    return True
        return False

    @staticmethod
    def _looks_like_table_text(text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        header_count = sum(header in compact for header in TABLE_HEADERS)
        numeric_cell_count = len(re.findall(r"\d+(?:\.\d+)?", compact))
        return header_count >= 3 and numeric_cell_count >= 2

    @staticmethod
    def _is_red_drawing(drawing: dict[str, Any]) -> bool:
        for key in ("color", "fill"):
            value = drawing.get(key)
            if not isinstance(value, (list, tuple)) or len(value) < 3:
                continue
            red, green, blue = (float(component) for component in value[:3])
            if red >= 0.5 and red >= green * 1.5 and red >= blue * 1.5:
                return True
        return False
