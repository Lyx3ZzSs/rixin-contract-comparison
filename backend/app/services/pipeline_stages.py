from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings
from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.models import (
    Clause,
    CompareTask,
    DiffItem,
    Document,
    DocumentProfile,
    OcrRawResultPaths,
    ParseWarningDetail,
)
from app.services.compare_debug import CompareDebugWriter
from app.services.clause_splitter import ClauseSplitter
from app.services.cover_metadata import CoverMetadataComparator
from app.services.diff_engine import DiffEngine
from app.services.diff_quality import DiffQualityProcessor
from app.services.document_profiler import DocumentProfiler
from app.services.document_preparation import DocumentPreparer
from app.services.document_understanding import DocumentUnderstandingService
from app.services.evidence_locator import EvidenceLocator
from app.services.extractors import build_compare_document_extractor, build_document_extractor
from app.services.extractors.base import (
    DocumentExtractionError,
    DocumentExtractor,
    ExtractionResult,
)
from app.services.header_footer_compare import HeaderFooterComparator
from app.services.matcher import ClauseMatcher
from app.services.page_diff import PageDiffConsolidator
from app.services.pipeline import PipelineContext
from app.services.seal_comparator import build_seal_diffs
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


def _emit_progress(ctx: PipelineContext, progress: int, stage: str, sub_stage: str) -> None:
    if ctx.progress_callback:
        ctx.progress_callback(progress, stage, {"sub_stage": sub_stage})


class ExtractionStage:
    name = "文档解析中"
    start_progress = 10
    progress = 35

    def __init__(
        self,
        extractor: DocumentExtractor | None = None,
        structured_extractor: DocumentExtractor | None = None,
        artifact_store: ArtifactStore = default_artifact_store,
        require_structured_ocr: bool | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.require_structured_ocr = (
            settings.compare_require_structured_ocr
            if require_structured_ocr is None
            else require_structured_ocr
        )
        self.extractor = extractor or build_compare_document_extractor(artifact_store=artifact_store)
        self.structured_extractor = structured_extractor
        self.profiler = DocumentProfiler()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        task = ctx.task
        _emit_progress(ctx, 12, self.name, "original_extraction_started")
        original_extraction = self._extract_side(ctx.original_pdf, task.task_id, "原版文件")
        _emit_progress(ctx, 22, self.name, "original_extraction_done")
        _emit_progress(ctx, 24, self.name, "compare_extraction_started")
        compare_extraction = self._extract_side(ctx.compare_pdf, task.task_id, "新版文件")
        _emit_progress(ctx, 30, self.name, "compare_extraction_done")

        original_extraction, compare_extraction = self._align_structured_extractions(
            ctx.original_pdf, ctx.compare_pdf, task.task_id,
            original_extraction, compare_extraction,
        )
        _emit_progress(ctx, 32, self.name, "structured_alignment_done")
        original_extraction = self._ensure_profile(original_extraction)
        compare_extraction = self._ensure_profile(compare_extraction)
        _emit_progress(ctx, 34, self.name, "document_profile_done")

        task.extractor_used = self._merge_extractor_names(
            original_extraction.extractor_used,
            compare_extraction.extractor_used,
        )
        task.ocr_raw_result_path = self._merge_raw_paths(
            original_extraction.raw_result_path,
            compare_extraction.raw_result_path,
        )
        task.ocr_raw_result_paths = OcrRawResultPaths.from_legacy_value(task.ocr_raw_result_path)
        task.parse_warnings.extend(original_extraction.warnings)
        task.parse_warnings.extend(compare_extraction.warnings)
        _append_text_warnings(task, original_extraction.warnings, "original_extractor")
        _append_text_warnings(task, compare_extraction.warnings, "compare_extractor")

        self._record_profile(task, "original", original_extraction.profile)
        self._record_profile(task, "compare", compare_extraction.profile)
        self._record_layout_quality(task, original_extraction)
        self._record_layout_quality(task, compare_extraction)
        _write_debug_artifact(
            task,
            "document_profiles",
            lambda: self.debug_writer.write_profiles(
                task.task_id, original_extraction.profile, compare_extraction.profile,
            ),
        )
        if original_extraction.layout_quality is not None or compare_extraction.layout_quality is not None:
            _write_debug_artifact(
                task,
                "layout_quality",
                lambda: self.debug_writer.write_layout_quality(
                    task.task_id,
                    original_extraction.layout_quality,
                    compare_extraction.layout_quality,
                ),
            )

        ctx.set_extractions(original_extraction, compare_extraction)

    def _extract_side(self, pdf_path: Path, task_id: str, side_label: str) -> ExtractionResult:
        try:
            result = self.extractor.extract(pdf_path, task_id=task_id)
        except DocumentExtractionError as exc:
            raise DocumentExtractionError(f"{side_label}结构化 OCR 失败：{exc}") from exc
        if self.require_structured_ocr and result.extractor_used != "ppstructure_ocr_hybrid":
            raise DocumentExtractionError(
                f"{side_label}结构化 OCR 失败：提取器返回了非结构化结果 {result.extractor_used or 'unknown'}"
            )
        return result

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

    def _record_layout_quality(self, task: CompareTask, extraction: ExtractionResult) -> None:
        if extraction.layout_quality is not None:
            _append_warning_details(task, extraction.layout_quality.warnings)

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


class DocumentPreparationStage:
    name = "文档准备中"
    start_progress = 35
    progress = 36

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.preparer = DocumentPreparer()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        extractions = ctx.require_extractions()
        result = self.preparer.prepare_pair(
            extractions.original.document,
            extractions.compare.document,
        )
        _write_debug_artifact(
            ctx.task,
            "document_preparation",
            lambda: self.debug_writer.write_document_preparation(
                ctx.task.task_id,
                result.to_debug_payload(),
            ),
        )
        _emit_progress(ctx, 36, self.name, "document_preparation_done")


class DocumentUnderstandingStage:
    name = "文档语义清洗中"
    start_progress = 34
    progress = 35

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.service = DocumentUnderstandingService(settings.document_understanding)
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        extractions = ctx.require_extractions()
        result = self.service.understand_pair(
            extractions.original.document,
            extractions.compare.document,
        )
        _append_warning_details(ctx.task, result.original.warnings)
        _append_warning_details(ctx.task, result.compare.warnings)
        _write_debug_artifact(
            ctx.task,
            "document_understanding",
            lambda: self.debug_writer.write_document_understanding(
                ctx.task.task_id,
                result.to_debug_payload(),
            ),
        )
        _emit_progress(ctx, 35, self.name, "document_understanding_done")


class PreClauseDiffStage:
    name = "差异识别中"
    start_progress = 36
    progress = 40

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.header_footer = HeaderFooterComparator()
        self.cover_metadata = CoverMetadataComparator()
        self.table_comparator = TableComparator()

    def execute(self, ctx: PipelineContext) -> None:
        task = ctx.task
        extractions = ctx.require_extractions()
        original_doc = extractions.original.document
        compare_doc = extractions.compare.document

        self._recognize_seals(ctx, original_doc, compare_doc)
        _emit_progress(ctx, 37, self.name, "seal_recognition_done")

        header_footer_diffs = self.header_footer.build_diffs(original_doc, compare_doc)
        _emit_progress(ctx, 38, self.name, "header_footer_diff_done")
        metadata_diffs = self.cover_metadata.build_diffs(
            original_doc,
            compare_doc,
            start_index=len(header_footer_diffs) + 1,
        )
        _emit_progress(ctx, 39, self.name, "cover_metadata_diff_done")
        table_diffs, table_warnings = self.table_comparator.build_diffs(
            original_doc, compare_doc,
            start_index=len(header_footer_diffs) + len(metadata_diffs) + 1,
        )
        seal_diffs = build_seal_diffs(
            original_doc, compare_doc,
            start_index=len(header_footer_diffs) + len(metadata_diffs) + len(table_diffs) + 1,
        )
        result = ctx.set_table_diffs(
            header_footer_diffs=header_footer_diffs,
            metadata_diffs=metadata_diffs,
            table_diffs=table_diffs,
            table_warnings=table_warnings,
        )
        task.parse_warnings.extend(result.table_warnings)
        _append_text_warnings(task, result.table_warnings, "table_compare")
        ctx.seal_diffs = seal_diffs

    @staticmethod
    def _recognize_seals(ctx: PipelineContext, original_doc: Document, compare_doc: Document) -> None:
        """Run dedicated OCR on seal regions (inspired by MinerU's seal OCR pipeline)."""
        from app.services.seal_ocr import SealOCRService
        from app.services.models.seal_detector import SealDetector

        seal_detector = SealDetector()
        service = SealOCRService()

        for label, doc, pdf_path in [
            ("original", original_doc, ctx.original_pdf),
            ("compare", compare_doc, ctx.compare_pdf),
        ]:
            seal_blocks = [b for p in doc.pages for b in p.blocks if b.block_type == "seal"]
            if not seal_blocks:
                continue
            from app.services.models.layout_detector import LayoutRegion, LayoutResult
            regions = LayoutResult(regions=[
                LayoutRegion(
                    region_type="seal",
                    bbox=b.bbox,
                    page_number=b.page_no,
                    text=b.text,
                )
                for b in seal_blocks
            ])
            seal_list = seal_detector.predict(regions)
            try:
                seal_list = service.recognize_seals(pdf_path, seal_list, ctx.task.task_id)
            except Exception:
                logger.debug("Seal OCR failed for %s document", label, exc_info=True)
                continue
            for seal, block in zip(seal_list, seal_blocks):
                if seal.text and not block.text.strip():
                    block.text = seal.text


class SplitStage:
    name = "差异识别中"
    start_progress = 41
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
        _emit_progress(ctx, 43, self.name, "clause_split_done")
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
        _write_debug_artifact(
            ctx.task,
            "section_outline",
            lambda: self.debug_writer.write_section_outline(
                ctx.task.task_id,
                clauses.original_clauses,
                clauses.compare_clauses,
            ),
        )
        _write_debug_artifact(
            ctx.task,
            "clause_split_quality",
            lambda: self.debug_writer.write_clause_split_quality(
                ctx.task.task_id,
                clauses.original_clauses,
                clauses.compare_clauses,
            ),
        )
        _emit_progress(ctx, 44, self.name, "clause_debug_artifacts_done")


class MatchStage:
    name = "条款匹配中"
    start_progress = 46
    progress = 55

    def __init__(
        self,
        threshold: int | None = None,
        artifact_store: ArtifactStore = default_artifact_store,
    ) -> None:
        self.matcher = ClauseMatcher(
            threshold if threshold is not None else settings.match_threshold,
            use_prefilter=settings.matching.use_prefilter,
            assignment_strategy=settings.matching.assignment_strategy,
            enable_semantic_match=settings.matching.enable_semantic_match,
            semantic_provider=settings.matching.semantic_provider,
            semantic_model_path=settings.matching.semantic_model_path,
            semantic_base_url=settings.matching.semantic_base_url,
            semantic_api_key=settings.matching.semantic_api_key,
            semantic_model=settings.matching.semantic_model,
            semantic_device=settings.matching.semantic_device,
            semantic_batch_size=settings.matching.semantic_batch_size,
            semantic_timeout_seconds=settings.matching.semantic_timeout_seconds,
            semantic_max_retries=settings.matching.semantic_max_retries,
            semantic_weight=settings.matching.semantic_weight,
            enable_rerank=settings.matching.enable_rerank,
            rerank_base_url=settings.matching.rerank_base_url,
            rerank_api_key=settings.matching.rerank_api_key,
            rerank_model=settings.matching.rerank_model,
            rerank_top_k=settings.matching.rerank_top_k,
            rerank_timeout_seconds=settings.matching.rerank_timeout_seconds,
            rerank_max_retries=settings.matching.rerank_max_retries,
            rerank_weight=settings.matching.rerank_weight,
            low_confidence_review_threshold=settings.matching.low_confidence_review_threshold,
        )
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        clauses = ctx.require_clauses()
        matches = ctx.set_matches(self.matcher.match(clauses.original_clauses, clauses.compare_clauses))
        _emit_progress(ctx, 53, self.name, "clause_match_done")
        _write_debug_artifact(
            ctx.task,
            "clause_matches",
            lambda: self.debug_writer.write_matches(ctx.task.task_id, matches.pairs),
        )
        _write_debug_artifact(
            ctx.task,
            "match_matrix_summary",
            lambda: self.debug_writer.write_match_matrix_summary(ctx.task.task_id, matches.pairs),
        )
        _emit_progress(ctx, 54, self.name, "match_debug_artifact_done")


class ClauseDiffStage:
    name = "差异计算中"
    start_progress = 56
    progress = 60

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.diff_engine = DiffEngine()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        table_diffs = ctx.require_table_diffs()
        matches = ctx.require_matches()
        pre_clause_count = (
            len(table_diffs.header_footer_diffs)
            + len(table_diffs.metadata_diffs)
            + len(table_diffs.table_diffs)
            + len(ctx.seal_diffs)
        )
        clause_diffs = self.diff_engine.build_diffs(
            matches.pairs,
            start_index=pre_clause_count + 1,
        )
        _emit_progress(ctx, 59, self.name, "clause_diff_done")
        diffs = [
            *table_diffs.header_footer_diffs,
            *table_diffs.metadata_diffs,
            *table_diffs.table_diffs,
            *ctx.seal_diffs,
            *clause_diffs,
        ]
        ctx.set_clause_diffs(clause_diffs, diffs)


class EvidenceStage:
    name = "证据定位中"
    start_progress = 61
    progress = 84

    def __init__(self) -> None:
        self.evidence_locator = EvidenceLocator()
        self.text_coordinate_locator = TextCoordinateLocator()
        self.page_consolidator = PageDiffConsolidator()

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
        _emit_progress(ctx, 66, self.name, "text_evidence_located")
        ctx.diffs = self.text_coordinate_locator.refine(
            ctx.original_pdf, ctx.compare_pdf, ctx.diffs,
        )
        _emit_progress(ctx, 76, self.name, "coordinate_refined")
        self.evidence_locator.assign_evidence_confidence(ctx.diffs)
        ctx.diffs = DiffEngine().deduplicate_overlaps(ctx.diffs)
        if ctx.original_extraction is not None and ctx.compare_extraction is not None:
            ctx.diffs = self.page_consolidator.consolidate(
                ctx.original_extraction.document,
                ctx.compare_extraction.document,
                ctx.diffs,
            )
            self.evidence_locator.assign_evidence_confidence(ctx.diffs)
        _emit_progress(ctx, 82, self.name, "evidence_confidence_done")


class DiffQualityStage:
    name = "差异质量评估中"
    start_progress = 84
    progress = 85

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.processor = DiffQualityProcessor()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        result = self.processor.process(ctx.require_diffs())
        ctx.diffs = result.diffs
        _write_debug_artifact(
            ctx.task,
            "diff_quality",
            lambda: self.debug_writer.write_diff_quality(
                ctx.task.task_id,
                result.to_debug_payload(),
            ),
        )
        _emit_progress(ctx, 85, self.name, "diff_quality_done")


class VisualizationStage:
    name = "高亮信息准备中"
    start_progress = 85
    progress = 86

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.artifact_store = artifact_store

    def execute(self, ctx: PipelineContext) -> None:
        ctx.task.original_highlight_pdf_path = None
        ctx.task.compare_highlight_pdf_path = None


class SummaryStage:
    name = "汇总统计中"
    start_progress = 87
    progress = 95

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        task = ctx.task
        task.diffs = _dedupe_final_diffs(ctx.require_diffs())
        _write_debug_artifact(
            task,
            "diff_decisions",
            lambda: self.debug_writer.write_diffs(task.task_id, task.diffs),
        )
        _emit_progress(ctx, 93, self.name, "diff_debug_artifact_done")
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


def _dedupe_final_diffs(diffs: list[DiffItem]) -> list[DiffItem]:
    seen_ids: set[str] = set()
    seen_content: set[tuple[str, str, str, str, str, str]] = set()
    result: list[DiffItem] = []
    for diff in diffs:
        if diff.diff_id in seen_ids:
            continue
        content_key = (
            diff.source_type,
            diff.section_type,
            diff.title,
            diff.diff_type,
            diff.original_text or diff.original_snippet,
            diff.compare_text or diff.compare_snippet,
        )
        if content_key in seen_content:
            continue
        seen_ids.add(diff.diff_id)
        seen_content.add(content_key)
        result.append(diff)
    return result
