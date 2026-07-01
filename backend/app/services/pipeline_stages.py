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
    OcrRemediationAction,
    OcrRawResultPaths,
    ParseWarningDetail,
)
from app.services.compare_debug import CompareDebugWriter
from app.services.clause_splitter import ClauseSplitter
from app.services.cover_metadata import CoverMetadataComparator
from app.services.diff_engine import DiffEngine
from app.services.diff_quality import DiffQualityProcessor, DiffQualityResult
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
from app.services.model_routing import ModelRoutingAnalyzer
from app.services.ocr_quality import OcrQualityProfiler
from app.services.ocr_remediation import OcrRemediationPlanner
from app.services.evidence_relocator import EvidenceRelocationResult, EvidenceRelocator
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
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        task = ctx.task
        extractions = ctx.require_extractions()
        original_doc = extractions.original.document
        compare_doc = extractions.compare.document

        if task.compare_options.ignore_stamps:
            seal_diffs: list[DiffItem] = []
            _emit_progress(ctx, 37, self.name, "seal_recognition_skipped")
        else:
            self._recognize_seals(ctx, original_doc, compare_doc)
            _emit_progress(ctx, 37, self.name, "seal_recognition_done")

        if task.compare_options.ignore_headers_footers:
            header_footer_diffs = []
            _emit_progress(ctx, 38, self.name, "header_footer_diff_skipped")
        else:
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
        _write_debug_artifact(
            task,
            "table_repair",
            lambda: self.debug_writer.write_table_repair(
                task.task_id,
                self.table_comparator.last_debug_payload,
            ),
        )
        if not task.compare_options.ignore_stamps:
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


class OcrQualityStage:
    name = "OCR质量评估中"
    start_progress = 83
    progress = 84

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.profiler = OcrQualityProfiler()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        extractions = ctx.require_extractions()
        original_extraction = extractions.original
        compare_extraction = extractions.compare
        original_profiles = self.profiler.profile_side(
            side="original",
            document=original_extraction.document,
            document_profile=original_extraction.profile,
            layout_quality=original_extraction.layout_quality,
            warnings=self._quality_warnings(original_extraction),
        )
        compare_profiles = self.profiler.profile_side(
            side="compare",
            document=compare_extraction.document,
            document_profile=compare_extraction.profile,
            layout_quality=compare_extraction.layout_quality,
            warnings=self._quality_warnings(compare_extraction),
        )
        profiles = [*original_profiles, *compare_profiles]
        summary = self.profiler.apply_to_diffs(diffs=ctx.diffs, profiles=profiles)
        ctx.task.ocr_quality_summary = summary
        _append_warning_details(
            ctx.task,
            [
                ParseWarningDetail(
                    code=f"OCR_QUALITY_{profile.status}",
                    message=f"{profile.side} 第 {profile.page_no} 页 OCR 质量风险: {', '.join(profile.reasons)}",
                    severity="ERROR" if profile.status == "UNRELIABLE" else "WARNING",
                    page_no=profile.page_no,
                    source=f"ocr_quality:{profile.side}",
                )
                for profile in profiles
                if profile.status != "OK"
            ],
        )
        _write_debug_artifact(
            ctx.task,
            "ocr_quality",
            lambda: self.debug_writer.write_ocr_quality(ctx.task.task_id, summary),
        )
        _emit_progress(ctx, 84, self.name, "ocr_quality_done")

    @staticmethod
    def _quality_warnings(extraction: ExtractionResult) -> list[ParseWarningDetail | str]:
        warnings: list[ParseWarningDetail | str] = [*extraction.warnings]
        if extraction.profile is not None:
            warnings.extend(extraction.profile.warnings)
        if extraction.layout_quality is not None:
            warnings.extend(extraction.layout_quality.warnings)
        return warnings


class OcrRemediationStage:
    name = "OCR风险处置规划中"
    start_progress = 84
    progress = 85

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.planner = OcrRemediationPlanner()
        self.relocator = EvidenceRelocator()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        summary = self.planner.plan(ctx.task.ocr_quality_summary, ctx.diffs)
        ctx.task.ocr_remediation_summary = summary
        self._execute_relocation_actions(ctx, summary.actions)
        self._refresh_summary_counts(summary)
        self._apply_planning_flags(ctx.diffs, summary.actions)
        _write_debug_artifact(
            ctx.task,
            "ocr_remediation",
            lambda: self.debug_writer.write_ocr_remediation(ctx.task.task_id, summary),
        )
        _emit_progress(ctx, 85, self.name, "ocr_remediation_planned")

    def _execute_relocation_actions(
        self,
        ctx: PipelineContext,
        actions: list[OcrRemediationAction],
    ) -> None:
        diffs_by_id = {diff.diff_id: diff for diff in ctx.diffs}
        for action in actions:
            if action.action_type != "RELOCATE_EVIDENCE" or action.status != "PLANNED":
                continue
            if action.side not in {"original", "compare"}:
                self._mark_relocation_skipped(action, "ACTION_NOT_ELIGIBLE")
                continue
            if not action.diff_id or action.diff_id not in diffs_by_id:
                self._mark_relocation_skipped(action, "DIFF_NOT_FOUND")
                continue

            diff = diffs_by_id[action.diff_id]
            result = self.relocator.relocate(
                diff,
                side=action.side,
                page_no=action.page_no,
                original_pdf=ctx.original_pdf,
                compare_pdf=ctx.compare_pdf,
            )
            self._apply_relocation_result(diff, action, result)

    @staticmethod
    def _mark_relocation_skipped(action: OcrRemediationAction, reason: str) -> None:
        action.status = "SKIPPED"
        action.changed_evidence = False
        action.changed_diff_text = False
        action.notes.append(reason)

    @staticmethod
    def _apply_relocation_result(
        diff: DiffItem,
        action: OcrRemediationAction,
        result: EvidenceRelocationResult,
    ) -> None:
        action.status = result.status
        action.changed_evidence = result.changed_evidence
        action.changed_diff_text = False
        action.before_quality = dict(result.before_quality)
        action.after_quality = dict(result.after_quality)
        action.notes.append(result.reason)

        if result.status == "SUCCEEDED":
            if action.side == "original":
                diff.original_evidence = result.evidence
            elif action.side == "compare":
                diff.compare_evidence = result.evidence
            OcrRemediationStage._append_action_flag(
                action,
                "OCR_REMEDIATION_EVIDENCE_RELOCATED",
            )
            return

        if result.status == "FAILED":
            action.changed_evidence = False
            OcrRemediationStage._append_action_flag(action, "OCR_REMEDIATION_UNRESOLVED")
            diff.quality_status = "NEEDS_REVIEW"

    @staticmethod
    def _append_action_flag(action: OcrRemediationAction, flag: str) -> None:
        if flag not in action.review_flags_added:
            action.review_flags_added.append(flag)

    @staticmethod
    def _refresh_summary_counts(summary) -> None:
        actions = summary.actions
        successful_diff_ids = {
            action.diff_id
            for action in actions
            if action.status == "SUCCEEDED" and action.diff_id
        }
        manual_count = sum(1 for action in actions if action.status == "MANUAL_REVIEW_REQUIRED")
        unresolved_count = sum(
            1
            for action in actions
            if action.status in {"PLANNED", "FAILED", "MANUAL_REVIEW_REQUIRED"}
        )

        summary.attempted_action_count = len(actions)
        summary.successful_action_count = sum(1 for action in actions if action.status == "SUCCEEDED")
        summary.unresolved_action_count = unresolved_count
        summary.risk_reduced_diff_count = len(successful_diff_ids)
        summary.manual_review_required_count = manual_count
        summary.requires_manual_review = bool(manual_count)

        if manual_count:
            summary.status = "MANUAL_REVIEW_REQUIRED"
        elif not actions or unresolved_count == 0:
            summary.status = "OK"
        else:
            summary.status = "ACTIONS_PLANNED"

    @staticmethod
    def _apply_planning_flags(diffs: list[DiffItem], actions: list[OcrRemediationAction]) -> None:
        flags_by_diff: dict[str, set[str]] = {}
        review_required_diff_ids: set[str] = set()
        for action in actions:
            if not action.diff_id:
                continue
            flags_by_diff.setdefault(action.diff_id, set()).update(action.review_flags_added)
            if action.status == "MANUAL_REVIEW_REQUIRED":
                flags_by_diff[action.diff_id].add("OCR_REMEDIATION_MANUAL_REVIEW")
            if action.status in {"PLANNED", "FAILED", "MANUAL_REVIEW_REQUIRED"}:
                review_required_diff_ids.add(action.diff_id)

        for diff in diffs:
            flags = flags_by_diff.get(diff.diff_id)
            if not flags:
                continue
            for flag in sorted(flags):
                if flag not in diff.review_flags:
                    diff.review_flags.append(flag)
            if diff.diff_id in review_required_diff_ids:
                diff.quality_status = "NEEDS_REVIEW"


class ModelRoutingStage:
    name = "OCR模型路由评估中"
    start_progress = 85
    progress = 85

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.analyzer = ModelRoutingAnalyzer()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        try:
            summary = self.analyzer.analyze(
                ctx.task.ocr_quality_summary,
                ctx.diffs,
                ctx.task.parse_warning_details,
            )
            _write_debug_artifact(
                ctx.task,
                "ocr_model_routing",
                lambda: self.debug_writer.write_model_routing(ctx.task.task_id, summary),
            )
        except Exception:
            logger.debug("OCR model routing debug artifact generation failed", exc_info=True)
        _emit_progress(ctx, 85, self.name, "model_routing_evaluated")


class DiffQualityStage:
    name = "差异质量评估中"
    start_progress = 85
    progress = 86

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.processor = DiffQualityProcessor()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        diffs = ctx.require_diffs()
        original_document = ctx.original_extraction.document if ctx.original_extraction is not None else None
        compare_document = ctx.compare_extraction.document if ctx.compare_extraction is not None else None
        result = self.processor.process(
            diffs,
            original_clauses=ctx.original_clauses,
            compare_clauses=ctx.compare_clauses,
            original_document=original_document,
            compare_document=compare_document,
        )
        ctx.diffs = result.diffs
        merged_to_winner = self._merged_to_winner(result)
        self._remap_ocr_quality_summary(ctx, result, merged_to_winner)
        self._remap_ocr_remediation_summary(ctx, merged_to_winner)
        _write_debug_artifact(
            ctx.task,
            "diff_quality",
            lambda: self.debug_writer.write_diff_quality(
                ctx.task.task_id,
                result.to_debug_payload(),
            ),
        )
        _emit_progress(ctx, 86, self.name, "diff_quality_done")

    @staticmethod
    def _merged_to_winner(result: DiffQualityResult) -> dict[str, str]:
        return {
            str(decision.detail["merged_diff_id"]): decision.diff_id
            for decision in result.decisions
            if decision.action == "cross_source_merged" and decision.detail.get("merged_diff_id")
        }

    @staticmethod
    def _remap_ocr_quality_summary(
        ctx: PipelineContext,
        result: DiffQualityResult,
        merged_to_winner: dict[str, str],
    ) -> None:
        summary = ctx.task.ocr_quality_summary
        if summary is None:
            return

        final_ids = {diff.diff_id for diff in result.diffs}
        affected_ids: set[str] = set()
        for profile in summary.profiles:
            remapped_ids = []
            for diff_id in profile.affected_diff_ids:
                mapped_id = merged_to_winner.get(diff_id, diff_id)
                if mapped_id in final_ids:
                    remapped_ids.append(mapped_id)
            profile.affected_diff_ids = sorted(set(remapped_ids))
            affected_ids.update(profile.affected_diff_ids)

        summary.affected_diff_count = len(affected_ids)
        summary.requires_review = summary.requires_review or bool(affected_ids)

    @staticmethod
    def _remap_ocr_remediation_summary(ctx: PipelineContext, merged_to_winner: dict[str, str]) -> None:
        summary = ctx.task.ocr_remediation_summary
        if summary is None or not merged_to_winner:
            return

        for action in summary.actions:
            if not action.diff_id:
                continue
            original_diff_id = action.diff_id
            remapped_diff_id = merged_to_winner.get(original_diff_id)
            if not remapped_diff_id:
                continue
            action.diff_id = remapped_diff_id
            action.action_id = DiffQualityStage._remapped_ocr_action_id(
                action,
                original_diff_id,
                remapped_diff_id,
            )

    @staticmethod
    def _remapped_ocr_action_id(
        action: OcrRemediationAction,
        original_diff_id: str,
        remapped_diff_id: str,
    ) -> str:
        parts = action.action_id.split(":")
        if len(parts) >= 4 and parts[2] == original_diff_id:
            parts[2] = remapped_diff_id
            return ":".join(parts)
        if original_diff_id in action.action_id:
            return action.action_id.replace(original_diff_id, remapped_diff_id, 1)
        return ":".join(
            [
                action.side or "unknown",
                str(action.page_no) if action.page_no is not None else "unknown",
                remapped_diff_id,
                action.action_type,
            ]
        )


class VisualizationStage:
    name = "高亮信息准备中"
    start_progress = 86
    progress = 87

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
        task.diffs, dedupe_remap = _dedupe_final_diffs(_filter_compare_option_diffs(task, ctx.require_diffs()))
        _remap_ocr_quality_summary_after_final_dedupe(task, dedupe_remap)
        DiffQualityStage._remap_ocr_remediation_summary(ctx, dedupe_remap)
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


def _filter_compare_option_diffs(task: CompareTask, diffs: list[DiffItem]) -> list[DiffItem]:
    excluded_source_types: set[str] = set()
    if task.compare_options.ignore_stamps:
        excluded_source_types.add("seal")
    if task.compare_options.ignore_headers_footers:
        excluded_source_types.add("header_footer")
    if not excluded_source_types:
        return diffs
    return [diff for diff in diffs if diff.source_type not in excluded_source_types]


def _dedupe_final_diffs(diffs: list[DiffItem]) -> tuple[list[DiffItem], dict[str, str]]:
    seen_ids: dict[str, DiffItem] = {}
    seen_content: dict[tuple[str, str, str, str, str, str], DiffItem] = {}
    remap: dict[str, str] = {}
    result: list[DiffItem] = []
    for diff in diffs:
        if diff.diff_id in seen_ids:
            survivor = seen_ids[diff.diff_id]
            remap[diff.diff_id] = survivor.diff_id
            _merge_final_dedupe_review_state(survivor, diff)
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
            survivor = seen_content[content_key]
            remap[diff.diff_id] = survivor.diff_id
            _merge_final_dedupe_review_state(survivor, diff)
            continue
        seen_ids[diff.diff_id] = diff
        seen_content[content_key] = diff
        result.append(diff)
    return result, remap


def _merge_final_dedupe_review_state(survivor: DiffItem, dropped: DiffItem) -> None:
    for flag in dropped.review_flags:
        if flag not in survivor.review_flags:
            survivor.review_flags.append(flag)
    if dropped.quality_status == "NEEDS_REVIEW":
        survivor.quality_status = "NEEDS_REVIEW"


def _remap_ocr_quality_summary_after_final_dedupe(
    task: CompareTask,
    dedupe_remap: dict[str, str],
) -> None:
    summary = task.ocr_quality_summary
    if summary is None or not dedupe_remap:
        return

    final_ids = {diff.diff_id for diff in task.diffs}
    affected_ids: set[str] = set()
    for profile in summary.profiles:
        remapped_ids = []
        for diff_id in profile.affected_diff_ids:
            mapped_id = dedupe_remap.get(diff_id, diff_id)
            if mapped_id in final_ids:
                remapped_ids.append(mapped_id)
        profile.affected_diff_ids = sorted(set(remapped_ids))
        affected_ids.update(profile.affected_diff_ids)

    summary.affected_diff_count = len(affected_ids)
    summary.requires_review = summary.requires_review or bool(affected_ids)
