from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

from app.config import settings
from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.models import (
    Clause,
    CompareTask,
    DiffItem,
    DocumentProfile,
    ParseWarningDetail,
)
from app.services.compare_debug import CompareDebugWriter
from app.services.clause_splitter import ClauseSplitter
from app.services.cover_metadata import CoverMetadataComparator
from app.services.diff_engine import DiffEngine
from app.services.document_profiler import DocumentProfiler
from app.services.evidence_locator import EvidenceLocator
from app.services.extractors import build_document_extractor
from app.services.extractors.base import (
    DocumentExtractionError,
    DocumentExtractor,
    ExtractionResult,
)
from app.services.matcher import ClauseMatcher
from app.services.pipeline import PipelineContext
from app.services.table_compare import TableComparator
from app.services.text_coordinate_locator import TextCoordinateLocator

logger = logging.getLogger(__name__)


def _append_warning_details(
    task: CompareTask,
    warnings: list[ParseWarningDetail],
) -> None:
    seen = {(item.code, item.message, item.page_no, item.source) for item in task.parse_warning_details}
    for warning in warnings:
        key = (warning.code, warning.message, warning.page_no, warning.source)
        if key in seen:
            continue
        task.parse_warning_details.append(warning)
        if warning.message not in task.parse_warnings:
            task.parse_warnings.append(warning.message)
        seen.add(key)


def _append_text_warnings(
    task: CompareTask,
    warnings: list[str],
    source: str,
) -> None:
    details = [
        ParseWarningDetail(
            code="PARSE_WARNING",
            message=w,
            severity="WARNING",
            source=source,
        )
        for w in warnings
        if w
    ]
    _append_warning_details(task, details)


def _write_debug_artifact(
    task: CompareTask,
    name: str,
    writer,
) -> None:
    try:
        task.debug_artifact_paths[name] = writer()
    except Exception:
        logger.debug("Compare debug artifact write failed: %s", name, exc_info=True)


class ExtractionStage:
    name = "文档解析中"
    progress = 35

    def __init__(
        self,
        extractor: DocumentExtractor | None = None,
        structured_extractor: DocumentExtractor | None = None,
        artifact_store: ArtifactStore = default_artifact_store,
    ) -> None:
        self.artifact_store = artifact_store
        self.extractor = extractor or build_document_extractor(artifact_store=artifact_store)
        self.structured_extractor = structured_extractor
        self.profiler = DocumentProfiler()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        task = ctx.task
        original_extraction = self.extractor.extract(ctx.original_pdf, task_id=task.task_id)
        compare_extraction = self.extractor.extract(ctx.compare_pdf, task_id=task.task_id)

        original_extraction, compare_extraction = self._align_structured_extractions(
            ctx.original_pdf, ctx.compare_pdf, task.task_id,
            original_extraction, compare_extraction,
        )
        original_extraction = self._ensure_profile(original_extraction)
        compare_extraction = self._ensure_profile(compare_extraction)

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
        _append_text_warnings(task, original_extraction.warnings, "original_extractor")
        _append_text_warnings(task, compare_extraction.warnings, "compare_extractor")

        self._record_profile(task, "original", original_extraction.profile)
        self._record_profile(task, "compare", compare_extraction.profile)
        _write_debug_artifact(
            task,
            "document_profiles",
            lambda: self.debug_writer.write_profiles(
                task.task_id, original_extraction.profile, compare_extraction.profile,
            ),
        )

        ctx.set_extractions(original_extraction, compare_extraction)

    def _ensure_profile(self, extraction: ExtractionResult) -> ExtractionResult:
        if extraction.profile is None:
            extraction.profile = self.profiler.profile(extraction.document, extraction.extractor_used)
            extraction.document.profile = extraction.profile
        return extraction

    def _record_profile(self, task: CompareTask, side: str, profile: DocumentProfile | None) -> None:
        if profile is None:
            return
        task.document_profiles[side] = profile
        _append_warning_details(task, profile.warnings)

    def _align_structured_extractions(
        self,
        original_pdf: Path,
        compare_pdf: Path,
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
            compare = self._upgrade_to_structured(compare_pdf, task_id, compare, "compare", original.extractor_used)
        elif compare_structured and original_pymupdf:
            original = self._upgrade_to_structured(original_pdf, task_id, original, "original", compare.extractor_used)
        return original, compare

    def _upgrade_to_structured(
        self,
        pdf_path: Path,
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
        except Exception:
            logger.exception("Structured extraction alignment failed")
            current.warnings.append(
                f"为保持表格边界一致，{side} 尝试切换结构化 OCR 抽取失败，已保留 PyMuPDF 结果"
            )
            return current

        upgraded.warnings = [
            *current.warnings,
            f"为保持表格边界一致，{side} 已从 PyMuPDF 切换为结构化 OCR 抽取。",
            *upgraded.warnings,
        ]
        return upgraded

    @staticmethod
    def _is_pymupdf_extractor_used(extractor_used: str) -> bool:
        return (extractor_used or "").lower() == "pymupdf"

    @staticmethod
    def _is_structured_extractor_used(extractor_used: str) -> bool:
        name = (extractor_used or "").lower()
        if not name or "ocr_only" in name:
            return False
        return "ppstructure" in name

    def _matching_structured_extractor(self, extractor_used: str) -> DocumentExtractor:
        return build_document_extractor("ppstructure_ocr_hybrid", artifact_store=self.artifact_store)

    @staticmethod
    def _merge_extractor_names(original: str, compare: str) -> str:
        if original == compare:
            return original
        return f"original:{original},compare:{compare}"

    @staticmethod
    def _merge_raw_paths(original: str, compare: str) -> str:
        return "\n".join(path for path in [original, compare] if path)


class PreClauseDiffStage:
    name = "差异识别中"
    progress = 40

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.cover_metadata = CoverMetadataComparator()
        self.table_comparator = TableComparator()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        task = ctx.task
        extractions = ctx.require_extractions()
        original_doc = extractions.original.document
        compare_doc = extractions.compare.document

        metadata_diffs = self.cover_metadata.build_diffs(original_doc, compare_doc)
        table_diffs, table_warnings = self.table_comparator.build_diffs(
            original_doc, compare_doc,
            start_index=len(metadata_diffs) + 1,
        )
        table_diffs = _deduplicate_cover_table_diffs(metadata_diffs, table_diffs)
        result = ctx.set_table_diffs(
            metadata_diffs=metadata_diffs,
            table_diffs=table_diffs,
            table_warnings=table_warnings,
        )
        task.parse_warnings.extend(result.table_warnings)
        _append_text_warnings(task, result.table_warnings, "table_compare")


class SplitStage:
    name = "差异识别中"
    progress = 45

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.splitter = ClauseSplitter()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        extractions = ctx.require_extractions()
        original_doc = extractions.original.document
        compare_doc = extractions.compare.document

        clauses = ctx.set_clauses(
            self.splitter.split(original_doc, "O"),
            self.splitter.split(compare_doc, "N"),
        )
        _write_debug_artifact(
            ctx.task,
            "original_clauses",
            lambda: self.debug_writer.write_clauses(ctx.task.task_id, "original", clauses.original_clauses),
        )
        _write_debug_artifact(
            ctx.task,
            "compare_clauses",
            lambda: self.debug_writer.write_clauses(ctx.task.task_id, "compare", clauses.compare_clauses),
        )


class MatchStage:
    name = "条款匹配中"
    progress = 55

    def __init__(
        self,
        threshold: int | None = None,
        artifact_store: ArtifactStore = default_artifact_store,
    ) -> None:
        self.matcher = ClauseMatcher(threshold if threshold is not None else settings.match_threshold)
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        clauses = ctx.require_clauses()
        matches = ctx.set_matches(self.matcher.match(clauses.original_clauses, clauses.compare_clauses))
        _write_debug_artifact(
            ctx.task,
            "clause_matches",
            lambda: self.debug_writer.write_matches(ctx.task.task_id, matches.pairs),
        )


class ClauseDiffStage:
    name = "差异计算中"
    progress = 60

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.diff_engine = DiffEngine()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        table_diffs = ctx.require_table_diffs()
        matches = ctx.require_matches()
        clause_diffs = self.diff_engine.build_diffs(
            matches.pairs,
            start_index=len(table_diffs.metadata_diffs) + len(table_diffs.table_diffs) + 1,
        )
        ctx.set_clause_diffs(
            clause_diffs,
            [*table_diffs.metadata_diffs, *table_diffs.table_diffs, *clause_diffs],
        )


class EvidenceStage:
    name = "证据定位中"
    progress = 70

    def __init__(self) -> None:
        self.evidence_locator = EvidenceLocator()
        self.text_coordinate_locator = TextCoordinateLocator()

    def execute(self, ctx: PipelineContext) -> None:
        clauses = ctx.require_clauses()
        matches = ctx.require_matches()
        original_locate_clauses = _clauses_for_evidence(
            clauses.original_clauses, [pair.original for pair in matches.pairs],
        )
        compare_locate_clauses = _clauses_for_evidence(
            clauses.compare_clauses, [pair.compare for pair in matches.pairs],
        )
        ctx.diffs = self.evidence_locator.locate(
            ctx.diffs, original_locate_clauses, compare_locate_clauses,
        )
        ctx.diffs = self.text_coordinate_locator.refine(
            ctx.original_pdf, ctx.compare_pdf, ctx.diffs,
        )
        self.evidence_locator.assign_evidence_confidence(ctx.diffs)
        ctx.diffs = DiffEngine().deduplicate_overlaps(ctx.diffs)


class VisualizationStage:
    name = "高亮信息准备中"
    progress = 90

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.artifact_store = artifact_store

    def execute(self, ctx: PipelineContext) -> None:
        ctx.task.original_highlight_pdf_path = ""
        ctx.task.compare_highlight_pdf_path = ""


class SummaryStage:
    name = "汇总统计中"
    progress = 95

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        task = ctx.task
        task.diffs = ctx.require_diffs()
        _write_debug_artifact(
            task,
            "diff_decisions",
            lambda: self.debug_writer.write_diffs(task.task_id, task.diffs),
        )
        _refresh_stats(task)


def _clauses_for_evidence(
    base_clauses: list[Clause],
    pair_clauses: list[Clause | None],
) -> list[Clause]:
    by_id = {clause.clause_id: clause for clause in base_clauses}
    for clause in pair_clauses:
        if clause is not None:
            by_id[clause.clause_id] = clause
    return list(by_id.values())


def _refresh_stats(task: CompareTask) -> None:
    task.diff_count = len(task.diffs)
