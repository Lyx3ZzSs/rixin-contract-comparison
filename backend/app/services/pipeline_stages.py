from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.infrastructure.artifact_store import ArtifactStore, default_artifact_store
from app.models import (
    AuditItemReview,
    BBox,
    Clause,
    CompareTask,
    DiffItem,
    Document,
    DocumentProfile,
    EvidenceBox,
    OcrRemediationAction,
    OcrRawResultPaths,
    Page,
    ParseWarningDetail,
    TextBlock,
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
from app.services.native_heading_repair import NativeHeadingRepairService
from app.services.ocr_quality import OcrQualityProfiler
from app.services.ocr_remediation import OcrRemediationPlanner
from app.services.counterpart_text_recovery import CounterpartTextRecovery
from app.services.evidence_relocator import EvidenceRelocationResult, EvidenceRelocator
from app.services.footer_annotation_visual import FooterAnnotationVisualComparator
from app.services.page_diff import PageDiffConsolidator
from app.services.pipeline import PipelineContext
from app.services.repeated_overlay_filter import RepeatedOverlayFilter
from app.services.seal_comparator import build_seal_diffs
from app.services.signing_region.block_detector import (
    BODY_VERB_RE,
    DATE_LABEL_RE,
    NUMBERED_RE,
    PARTY_LABEL_RE,
    PARTY_RE,
    REPRESENTATIVE_RE,
    SEAL_RE,
    SIGN_RE,
    SIGNING_CONTEXT_RE,
    SigningBlockDetectionResult,
    SigningBlockDetector,
)
from app.services.signing_region.clause_document import SigningClauseDocumentBuilder
from app.services.signing_region.comparator import SigningRegionComparator
from app.services.signing_region.coverage import SigningRegionCoverageBuilder
from app.services.signing_region.diff_builder import SigningRegionDiffBuilder
from app.services.signing_region.extractor import SigningRegionExtractor
from app.services.signing_region.matcher import SigningRegionMatcher
from app.services.signing_region.models import (
    SigningBlock,
    SigningBlockConfidenceLevel,
    SigningBlockRole,
    SigningElement,
    SigningElementType,
    SigningRegion,
    VisualDetection,
    VisualDetectionResult,
)
from app.services.signing_region.visual import (
    LocalCpuVisualSignatureDetector,
    OpenCvSigningRegionFingerprinter,
    OpenCvVisualSignatureDetector,
    RemoteVisualSignatureDetector,
    VisualSignatureDetector,
)
from app.services.table_compare import TableComparator
from app.services.text_coordinate_locator import TextCoordinateLocator
from app.utils.id_utils import generate_diff_id

logger = logging.getLogger(__name__)

FINAL_DIFF_EVIDENCE_OVERLAP_THRESHOLD = 0.80


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


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


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
        native_heading_repair: NativeHeadingRepairService | None = None,
        repeated_overlay_filter: RepeatedOverlayFilter | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.require_structured_ocr = (
            settings.compare_require_structured_ocr if require_structured_ocr is None else require_structured_ocr
        )
        self.extractor = extractor or build_compare_document_extractor(artifact_store=artifact_store)
        self.structured_extractor = structured_extractor
        self.profiler = DocumentProfiler()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)
        self.native_heading_repair = native_heading_repair or NativeHeadingRepairService()
        self.repeated_overlay_filter = repeated_overlay_filter or RepeatedOverlayFilter()

    def execute(self, ctx: PipelineContext) -> None:
        task = ctx.task
        _emit_progress(ctx, 12, self.name, "original_extraction_started")
        original_extraction = self._extract_side(ctx.original_pdf, task.task_id, "原版文件")
        _emit_progress(ctx, 22, self.name, "original_extraction_done")
        _emit_progress(ctx, 24, self.name, "compare_extraction_started")
        compare_extraction = self._extract_side(ctx.compare_pdf, task.task_id, "新版文件")
        _emit_progress(ctx, 30, self.name, "compare_extraction_done")

        original_extraction, compare_extraction = self._align_structured_extractions(
            ctx.original_pdf,
            ctx.compare_pdf,
            task.task_id,
            original_extraction,
            compare_extraction,
        )
        _emit_progress(ctx, 32, self.name, "structured_alignment_done")
        original_heading_result = self.native_heading_repair.repair(original_extraction.document)
        compare_heading_result = self.native_heading_repair.repair(compare_extraction.document)
        original_extraction.warnings.extend(original_heading_result.warnings)
        compare_extraction.warnings.extend(compare_heading_result.warnings)
        original_overlay_result = self.repeated_overlay_filter.apply(original_extraction.document)
        compare_overlay_result = self.repeated_overlay_filter.apply(compare_extraction.document)
        _write_debug_artifact(
            task,
            "native_heading_repair",
            lambda: self.debug_writer.write_native_heading_repair(
                task.task_id,
                original_heading_result.to_debug_payload(),
                compare_heading_result.to_debug_payload(),
            ),
        )
        _write_debug_artifact(
            task,
            "repeated_overlay_filter",
            lambda: self.debug_writer.write_repeated_overlay_filter(
                task.task_id,
                original_overlay_result.to_debug_payload(),
                compare_overlay_result.to_debug_payload(),
            ),
        )
        task.metrics["native_heading_repair"] = {
            "original": original_heading_result.repaired_count,
            "compare": compare_heading_result.repaired_count,
        }
        task.metrics["repeated_overlay_filter"] = {
            "original": original_overlay_result.filtered_block_count,
            "compare": compare_overlay_result.filtered_block_count,
        }
        _emit_progress(ctx, 33, self.name, "extraction_evidence_normalization_done")
        original_extraction = self._refresh_profile_after_normalization(original_extraction)
        compare_extraction = self._refresh_profile_after_normalization(compare_extraction)
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
                task.task_id,
                original_extraction.profile,
                compare_extraction.profile,
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

    def _refresh_profile_after_normalization(self, extraction: ExtractionResult) -> ExtractionResult:
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
            current.warnings.append(f"为保持表格边界一致，{side} 尝试切换结构化 OCR 抽取失败，已保留 PyMuPDF 结果")
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
        self.footer_visual = FooterAnnotationVisualComparator()
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
            ocr_header_footer_diffs = self.header_footer.build_diffs(original_doc, compare_doc)
            visual_footer_diffs = self.footer_visual.build_diffs(
                original_doc,
                compare_doc,
                start_index=len(ocr_header_footer_diffs) + 1,
            )
            ocr_header_footer_diffs = self.footer_visual.remove_overlapping_ocr_diffs(
                ocr_header_footer_diffs,
                visual_footer_diffs,
            )
            header_footer_diffs = [*ocr_header_footer_diffs, *visual_footer_diffs]
            for index, diff in enumerate(header_footer_diffs, start=1):
                diff.diff_id = generate_diff_id(index)
            _emit_progress(ctx, 38, self.name, "header_footer_diff_done")
        metadata_diffs = self.cover_metadata.build_diffs(
            original_doc,
            compare_doc,
            start_index=len(header_footer_diffs) + 1,
        )
        _emit_progress(ctx, 39, self.name, "cover_metadata_diff_done")
        table_diffs, table_warnings = self.table_comparator.build_diffs(
            original_doc,
            compare_doc,
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
                original_doc,
                compare_doc,
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

            regions = LayoutResult(
                regions=[
                    LayoutRegion(
                        region_type="seal",
                        bbox=b.bbox,
                        page_number=b.page_no,
                        text=b.text,
                    )
                    for b in seal_blocks
                ]
            )
            seal_list = seal_detector.predict(regions)
            try:
                seal_list = service.recognize_seals(pdf_path, seal_list, ctx.task.task_id)
            except Exception:
                logger.debug("Seal OCR failed for %s document", label, exc_info=True)
                continue
            for seal, block in zip(seal_list, seal_blocks):
                if seal.text and not block.text.strip():
                    block.text = seal.text


class SigningRegionStage:
    name = "签章区域识别中"
    start_progress = 40
    progress = 42
    visual_confidence_threshold = 0.6

    def __init__(
        self,
        artifact_store: ArtifactStore = default_artifact_store,
        *,
        visual_detector: VisualSignatureDetector | None = None,
        visual_fingerprinter: OpenCvSigningRegionFingerprinter | None = None,
        visual_enabled: bool | None = None,
    ) -> None:
        self.block_detector = SigningBlockDetector()
        self.extractor = SigningRegionExtractor()
        self.clause_document_builder = SigningClauseDocumentBuilder()
        self.matcher = SigningRegionMatcher()
        self.comparator = SigningRegionComparator()
        self.diff_builder = SigningRegionDiffBuilder()
        self.coverage_builder = SigningRegionCoverageBuilder()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)
        self.visual_enabled = settings.signing_visual_enabled if visual_enabled is None else visual_enabled
        self.visual_detector = visual_detector if visual_detector is not None else self._default_visual_detector()
        self.visual_fingerprinter = (
            visual_fingerprinter if visual_fingerprinter is not None else OpenCvSigningRegionFingerprinter()
        )

    def execute(self, ctx: PipelineContext) -> None:
        if ctx.task.compare_options.ignore_stamps or ctx.task.compare_options.signing_region_mode == "off":
            ctx.signing_pages_original = []
            ctx.signing_pages_compare = []
            ctx.signing_blocks_original = []
            ctx.signing_blocks_compare = []
            ctx.clause_document_original = None
            ctx.clause_document_compare = None
            ctx.signing_regions_original = []
            ctx.signing_regions_compare = []
            ctx.signing_region_diffs = []
            ctx.signing_region_covered_diff_ids = set()
            ctx.signing_region_debug = {"skipped": True}
            _write_debug_artifact(
                ctx.task,
                "signing_region",
                lambda: self.debug_writer.write_signing_region(ctx.task.task_id, ctx.signing_region_debug),
            )
            _emit_progress(ctx, 42, self.name, "signing_region_skipped")
            return

        extractions = ctx.require_extractions()
        original_structure = self.block_detector.detect(extractions.original.document)
        compare_structure = self.block_detector.detect(extractions.compare.document)
        self._expand_detected_blocks_with_nearby_fragments(extractions.original.document, original_structure)
        self._expand_detected_blocks_with_nearby_fragments(extractions.compare.document, compare_structure)
        self._expand_detected_blocks_with_native_titles(
            ctx.original_pdf,
            extractions.original.document,
            original_structure,
        )
        self._expand_detected_blocks_with_native_titles(
            ctx.compare_pdf,
            extractions.compare.document,
            compare_structure,
        )
        original_regions, original_legacy_fallback = self._extract_regions_from_structure(
            extractions.original.document,
            original_structure,
        )
        compare_regions, compare_legacy_fallback = self._extract_regions_from_structure(
            extractions.compare.document,
            compare_structure,
        )
        original_clause_doc = self.clause_document_builder.build(
            extractions.original.document,
            original_structure.blocks,
            signing_pages=original_structure.pages,
        )
        compare_clause_doc = self.clause_document_builder.build(
            extractions.compare.document,
            compare_structure.blocks,
            signing_pages=compare_structure.pages,
        )
        original_candidate_regions = self._candidate_regions_from_low_confidence(
            original_structure.low_confidence_candidates
        )
        compare_candidate_regions = self._candidate_regions_from_low_confidence(
            compare_structure.low_confidence_candidates
        )
        original_visual_candidate_regions = self._candidate_regions_for_visual_scan(original_candidate_regions)
        compare_visual_candidate_regions = self._candidate_regions_for_visual_scan(compare_candidate_regions)
        suppressed_low_confidence_candidates: list[dict[str, Any]] = []
        visual_status = {
            "original": self._collect_visual(
                ctx.original_pdf,
                original_regions,
                ctx.task.task_id,
                side="original",
                suppressed=suppressed_low_confidence_candidates,
                candidate_regions=original_visual_candidate_regions,
            ),
            "compare": self._collect_visual(
                ctx.compare_pdf,
                compare_regions,
                ctx.task.task_id,
                side="compare",
                suppressed=suppressed_low_confidence_candidates,
                candidate_regions=compare_visual_candidate_regions,
            ),
        }
        self._promote_visual_supported_candidates(
            original_structure,
            visual_status["original"],
            candidate_regions=original_visual_candidate_regions,
        )
        self._promote_visual_supported_candidates(
            compare_structure,
            visual_status["compare"],
            candidate_regions=compare_visual_candidate_regions,
        )
        if original_visual_candidate_regions or compare_visual_candidate_regions:
            original_regions, original_legacy_fallback = self._extract_regions_from_structure(
                extractions.original.document,
                original_structure,
            )
            compare_regions, compare_legacy_fallback = self._extract_regions_from_structure(
                extractions.compare.document,
                compare_structure,
            )
            self._refresh_visual_elements_for_regions(
                visual_status["original"],
                original_regions,
                ctx.original_pdf,
                side="original",
            )
            self._refresh_visual_elements_for_regions(
                visual_status["compare"],
                compare_regions,
                ctx.compare_pdf,
                side="compare",
            )
        self._apply_visual_enrichment_when_comparable(visual_status)
        matches = self.matcher.match(original_regions, compare_regions)
        original_party_references = self.comparator.party_references(extractions.original.document)
        compare_party_references = self.comparator.party_references(extractions.compare.document)
        for original, compare, _ in matches:
            self.comparator.reconcile_occluded_party_ocr(
                original,
                compare,
                original_party_references=original_party_references,
                compare_party_references=compare_party_references,
            )
        comparisons = [
            self.comparator.compare(
                original,
                compare,
                match_confidence=match_confidence,
                original_party_references=original_party_references,
                compare_party_references=compare_party_references,
            )
            for original, compare, match_confidence in matches
        ]
        legacy_diffs = self._legacy_diffs(ctx)
        signing_region_diffs = self.diff_builder.build_diffs(
            comparisons,
            start_index=len(legacy_diffs) + 1,
        )
        coverage = self.coverage_builder.build(signing_region_diffs, legacy_diffs)

        ctx.signing_pages_original = original_structure.pages
        ctx.signing_pages_compare = compare_structure.pages
        ctx.signing_blocks_original = original_structure.blocks
        ctx.signing_blocks_compare = compare_structure.blocks
        ctx.clause_document_original = original_clause_doc.document
        ctx.clause_document_compare = compare_clause_doc.document
        ctx.signing_regions_original = original_regions
        ctx.signing_regions_compare = compare_regions
        ctx.signing_region_diffs = signing_region_diffs
        ctx.signing_region_covered_diff_ids = coverage.covered_diff_ids
        ctx.signing_region_debug = {
            "skipped": False,
            "signing_pages": {
                "original": _jsonable(original_structure.pages),
                "compare": _jsonable(compare_structure.pages),
            },
            "signing_blocks": {
                "original": _jsonable(original_structure.blocks),
                "compare": _jsonable(compare_structure.blocks),
            },
            "excluded_candidates": {
                "original": _jsonable(original_structure.excluded_candidates),
                "compare": _jsonable(compare_structure.excluded_candidates),
            },
            "low_confidence_candidates": {
                "original": _jsonable(original_structure.low_confidence_candidates),
                "compare": _jsonable(compare_structure.low_confidence_candidates),
            },
            "clause_exclusion": {
                "original": _jsonable(original_clause_doc.entries),
                "compare": _jsonable(compare_clause_doc.entries),
            },
            "legacy_region_fallback": {
                "original": original_legacy_fallback,
                "compare": compare_legacy_fallback,
            },
            "original_regions": _jsonable(original_regions),
            "compare_regions": _jsonable(compare_regions),
            "matches": [
                {
                    "original_region_id": original.region_id if original is not None else None,
                    "compare_region_id": compare.region_id if compare is not None else None,
                    "score": match_confidence,
                }
                for original, compare, match_confidence in matches
            ],
            "comparisons": _jsonable(comparisons),
            "diffs": _jsonable(signing_region_diffs),
            "visual_adapter_status": _jsonable(self._debug_visual_status(visual_status)),
            "visual_candidates": {
                "original": visual_status["original"].get("_visual_candidates", []),
                "compare": visual_status["compare"].get("_visual_candidates", []),
            },
            "configuration": self._debug_configuration(),
            "suppressed_low_confidence_candidates": suppressed_low_confidence_candidates,
            "coverage": {
                "entries": _jsonable(coverage.entries),
                "covered_diff_ids": sorted(coverage.covered_diff_ids),
            },
        }
        _write_debug_artifact(
            ctx.task,
            "signing_region",
            lambda: self.debug_writer.write_signing_region(ctx.task.task_id, ctx.signing_region_debug),
        )
        _emit_progress(ctx, 42, self.name, "signing_region_done")

    def _extract_regions_from_structure(
        self,
        document: Document,
        structure: SigningBlockDetectionResult,
    ) -> tuple[list[SigningRegion], bool]:
        regions = self.extractor.extract_from_blocks(structure.blocks, document=document)
        if regions or structure.blocks:
            return regions, False
        if structure.excluded_candidates or structure.low_confidence_candidates:
            return [], False
        return self.extractor.extract(document), True

    @staticmethod
    def _expand_detected_blocks_with_nearby_fragments(
        document: Document,
        structure: SigningBlockDetectionResult,
    ) -> None:
        pages_by_no = {page.page_no: page for page in document.pages}
        for signing_block in structure.blocks:
            page = pages_by_no.get(signing_block.page_no)
            if page is None:
                continue
            SigningRegionStage._expand_block_with_nearby_fragments(page, signing_block)

    @staticmethod
    def _expand_detected_blocks_with_native_titles(
        pdf_path: Path,
        document: Document,
        structure: SigningBlockDetectionResult,
    ) -> None:
        if not structure.blocks:
            return

        native_titles = SigningRegionStage._native_signing_title_blocks(pdf_path)
        if not native_titles:
            return

        pages_by_no = {page.page_no: page for page in document.pages}
        for signing_block in structure.blocks:
            page = pages_by_no.get(signing_block.page_no)
            if page is None:
                continue
            for title in sorted(
                native_titles.get(signing_block.page_no, []),
                key=lambda item: (item.bbox.y0, item.bbox.x0, item.block_id),
            ):
                if title.block_id in signing_block.source_block_ids:
                    continue
                if not SigningRegionStage._native_title_belongs_to_block(title, signing_block, page):
                    continue
                SigningRegionStage._merge_native_title_into_block(signing_block, title)

    @staticmethod
    def _native_signing_title_blocks(pdf_path: Path) -> dict[int, list[TextBlock]]:
        try:
            import fitz
        except Exception:
            logger.debug("PyMuPDF unavailable for native signing title extraction", exc_info=True)
            return {}

        if not pdf_path.exists():
            return {}

        titles_by_page: dict[int, list[TextBlock]] = {}
        try:
            with fitz.open(str(pdf_path)) as pdf:
                for page_index, pdf_page in enumerate(pdf, start=1):
                    line_words: dict[tuple[int, int], list[tuple[Any, ...]]] = {}
                    for word in pdf_page.get_text("words") or []:
                        if len(word) < 5:
                            continue
                        text = str(word[4] or "").strip()
                        if not text:
                            continue
                        block_no = int(word[5]) if len(word) > 5 else 0
                        line_no = int(word[6]) if len(word) > 6 else len(line_words)
                        line_words.setdefault((block_no, line_no), []).append(word)

                    page_titles: list[TextBlock] = []
                    sorted_lines = sorted(
                        line_words.values(),
                        key=lambda words: (
                            min(float(item[1]) for item in words),
                            min(float(item[0]) for item in words),
                        ),
                    )
                    for line_index, words in enumerate(sorted_lines, start=1):
                        ordered_words = sorted(words, key=lambda item: (float(item[0]), float(item[1])))
                        text = "".join(str(item[4] or "").strip() for item in ordered_words)
                        compact = SigningRegionStage._compact_candidate_text(text)
                        if not SigningRegionStage._is_native_signing_title(compact):
                            continue
                        page_titles.append(
                            TextBlock(
                                block_id=f"native_p{page_index}_signing_title_{line_index}",
                                page_no=page_index,
                                text=compact,
                                bbox=BBox(
                                    x0=min(float(item[0]) for item in ordered_words),
                                    y0=min(float(item[1]) for item in ordered_words),
                                    x1=max(float(item[2]) for item in ordered_words),
                                    y1=max(float(item[3]) for item in ordered_words),
                                ),
                                block_type="paragraph_title",
                                confidence=1.0,
                                source="native_pdf_text",
                                block_role="paragraph_title",
                                flow_role="heading",
                            )
                        )
                    if page_titles:
                        titles_by_page[page_index] = page_titles
        except Exception:
            logger.debug("Native signing title extraction failed for %s", pdf_path, exc_info=True)
            return {}
        return titles_by_page

    @staticmethod
    def _native_title_belongs_to_block(title: TextBlock, signing_block: SigningBlock, page: Page) -> bool:
        compact = SigningRegionStage._compact_candidate_text(title.text)
        if not SigningRegionStage._is_native_signing_title(compact):
            return False
        if title.bbox.y0 > page.height * 0.3:
            return False
        vertical_gap = signing_block.bbox.y0 - title.bbox.y1
        if vertical_gap < -16.0 or vertical_gap > max(120.0, page.height * 0.2):
            return False
        horizontal_margin = max(36.0, page.width * 0.08)
        return (
            title.bbox.x1 >= signing_block.bbox.x0 - horizontal_margin
            and title.bbox.x0 <= signing_block.bbox.x1 + horizontal_margin
        )

    @staticmethod
    def _merge_native_title_into_block(signing_block: SigningBlock, title: TextBlock) -> None:
        title_text = SigningRegionStage._compact_candidate_text(title.text)
        existing_compact = SigningRegionStage._compact_candidate_text(signing_block.text)
        if title_text and title_text not in existing_compact:
            signing_block.text = "\n".join(part for part in [title_text, signing_block.text] if part)

        signing_block.bbox = SigningRegionStage._union_bbox([title.bbox, signing_block.bbox])
        if title.block_id not in signing_block.source_block_ids:
            signing_block.source_block_ids.insert(0, title.block_id)
        if "native_signing_title" not in signing_block.confidence_reasons:
            signing_block.confidence_reasons.append("native_signing_title")

    @staticmethod
    def _is_native_signing_title(compact_text: str) -> bool:
        return compact_text in {"签署页", "签字页", "签章页"}

    @staticmethod
    def _expand_block_with_nearby_fragments(page: Page, signing_block: SigningBlock) -> None:
        source_ids = set(signing_block.source_block_ids)
        additions: list[TextBlock] = []
        for block in sorted(page.blocks, key=lambda item: (item.bbox.y0, item.bbox.x0, item.block_id)):
            if block.block_id in source_ids:
                continue
            if SigningRegionStage._is_page_footer_like(block, page):
                continue
            if SigningRegionStage._is_ignorable_fragment_block(block):
                continue
            if not SigningRegionStage._near_signing_block(signing_block.bbox, block.bbox, page):
                continue
            if not SigningRegionStage._absorbable_signing_fragment(signing_block.bbox, block):
                continue
            additions.append(block)
            source_ids.add(block.block_id)

        if not additions:
            return

        signing_block.bbox = SigningRegionStage._union_bbox(
            [
                signing_block.bbox,
                *(block.bbox for block in additions),
            ]
        )
        signing_block.source_block_ids.extend(block.block_id for block in additions)
        if "seal_signature_date_cluster" in signing_block.confidence_reasons:
            signing_block.exclude_from_clause_diff = True

        existing_text = signing_block.text
        seen_texts = {
            SigningRegionStage._compact_candidate_text(part)
            for part in existing_text.splitlines()
            if SigningRegionStage._compact_candidate_text(part)
        }
        appended_texts: list[str] = []
        for block in additions:
            if SigningRegionStage._is_visual_fragment_block(block):
                continue
            text = (block.text or "").strip()
            compact = SigningRegionStage._compact_candidate_text(text)
            if text and compact not in seen_texts:
                appended_texts.append(text)
                seen_texts.add(compact)
        if appended_texts:
            signing_block.text = "\n".join(part for part in [existing_text, *appended_texts] if part)

    @staticmethod
    def _near_signing_block(anchor: BBox, candidate: BBox, page: Page) -> bool:
        vertical_overlap = min(anchor.y1, candidate.y1) - max(anchor.y0, candidate.y0)
        vertical_gap = max(0.0, candidate.y0 - anchor.y1, anchor.y0 - candidate.y1)
        if vertical_overlap <= 0 and vertical_gap > 36.0:
            return False
        horizontal_margin = max(36.0, page.width * 0.08)
        return candidate.x1 >= anchor.x0 - horizontal_margin and candidate.x0 <= anchor.x1 + horizontal_margin

    @staticmethod
    def _absorbable_signing_fragment(anchor: BBox, block: TextBlock) -> bool:
        compact = SigningRegionStage._compact_candidate_text(block.text)
        if not compact:
            return False
        vertical_overlap = min(anchor.y1, block.bbox.y1) - max(anchor.y0, block.bbox.y0)
        if vertical_overlap <= 0:
            return False
        if SigningRegionStage._is_visual_fragment_block(block):
            return block.bbox.x1 >= anchor.x0 and block.bbox.x0 <= anchor.x1
        if SigningRegionStage._fragment_looks_like_numbered_clause(compact) or BODY_VERB_RE.search(compact):
            return False
        if any(pattern.search(compact) for pattern in [SEAL_RE, SIGN_RE, REPRESENTATIVE_RE, DATE_LABEL_RE]):
            return True
        return len(compact) <= 8

    @staticmethod
    def _fragment_looks_like_numbered_clause(compact: str) -> bool:
        return len(compact) > 8 and NUMBERED_RE.match(compact) is not None

    @staticmethod
    def _is_visual_fragment_block(block: TextBlock) -> bool:
        return (block.block_type or "").lower() in {"seal", "stamp", "image", "non_text"} or (
            block.flow_role or ""
        ).lower() == "non_text"

    @staticmethod
    def _is_page_footer_like(block: TextBlock, page: Page) -> bool:
        compact = SigningRegionStage._compact_candidate_text(block.text)
        return block.bbox.y0 >= page.height * 0.82 and re.fullmatch(r"[-—_]*\d+[-—_]*", compact) is not None

    @staticmethod
    def _is_ignorable_fragment_block(block: TextBlock) -> bool:
        block_type = (block.block_type or "").lower()
        flow_role = (block.flow_role or "").lower()
        block_role = (block.block_role or "").lower()
        ignored_roles = {"aside", "margin", "header", "footer", "noise"}
        ignored_types = {"aside_text", "header", "footer", "page_number"}
        return block_type in ignored_types or flow_role in ignored_roles or block_role in ignored_roles

    @staticmethod
    def _union_bbox(bboxes: list[BBox]) -> BBox:
        return BBox(
            x0=min(bbox.x0 for bbox in bboxes),
            y0=min(bbox.y0 for bbox in bboxes),
            x1=max(bbox.x1 for bbox in bboxes),
            y1=max(bbox.y1 for bbox in bboxes),
        )

    @staticmethod
    def _legacy_diffs(ctx: PipelineContext) -> list[DiffItem]:
        return [
            *ctx.header_footer_diffs,
            *ctx.metadata_diffs,
            *ctx.table_diffs,
            *ctx.seal_diffs,
        ]

    @staticmethod
    def _promote_visual_supported_candidates(
        structure: SigningBlockDetectionResult,
        visual_status: dict[str, Any],
        *,
        candidate_regions: list[SigningRegion] | None = None,
    ) -> None:
        promoted: list[SigningBlock] = []
        visual_candidates = visual_status.get("_visual_candidates", [])
        allowed_candidate_keys = (
            {SigningRegionStage._candidate_region_key(region.page_no, region.bbox) for region in candidate_regions}
            if candidate_regions is not None
            else None
        )
        for candidate_index, candidate in enumerate(structure.low_confidence_candidates, start=1):
            if not isinstance(candidate, dict):
                continue
            try:
                score = float(candidate.get("score") or 0)
            except (TypeError, ValueError):
                continue
            if score < 0.35:
                continue
            try:
                page_no = int(candidate.get("page_no", 0))
            except (TypeError, ValueError):
                continue
            bbox_payload = candidate.get("bbox")
            if page_no <= 0 or not isinstance(bbox_payload, dict):
                continue
            reasons = SigningRegionStage._candidate_list_value(candidate.get("reasons"))
            candidate_text = str(candidate.get("text") or "")
            if not SigningRegionStage._candidate_has_rule_support(reasons, candidate_text):
                continue
            try:
                bbox = BBox.model_validate(bbox_payload)
            except Exception:
                continue
            if (
                allowed_candidate_keys is not None
                and SigningRegionStage._candidate_region_key(page_no, bbox) not in allowed_candidate_keys
            ):
                continue
            matched_visual = SigningRegionStage._matching_visual_candidate(
                page_no,
                bbox,
                visual_candidates,
                source_region_id=f"LC-{page_no}-{candidate_index}",
            )
            if matched_visual is None:
                continue
            if not SigningRegionStage._candidate_visual_pair_is_promotable(
                candidate_text,
                reasons,
                matched_visual,
            ):
                continue
            matched_visual["used_for_promotion"] = True
            promoted.append(
                SigningBlock(
                    block_id=f"SB-VISUAL-{page_no}-{len(promoted) + 1}",
                    page_no=page_no,
                    bbox=bbox,
                    block_role=SigningBlockRole.UNKNOWN,
                    confidence=round(min(0.69, max(0.5, score + 0.15)), 2),
                    confidence_level=SigningBlockConfidenceLevel.MEDIUM,
                    confidence_reasons=[*reasons, "visual_candidate_promoted"],
                    source_block_ids=SigningRegionStage._candidate_list_value(candidate.get("block_ids")),
                    text=str(candidate.get("text") or ""),
                    exclude_from_clause_diff=False,
                )
            )
        structure.blocks.extend(promoted)

    @staticmethod
    def _candidate_list_value(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple, set)):
            return [item for item in value if isinstance(item, str)]
        return []

    @staticmethod
    def _candidate_has_rule_support(reasons: list[object], text: str = "") -> bool:
        compact = SigningRegionStage._compact_candidate_text(text)
        if SigningRegionStage._candidate_text_looks_like_body(compact):
            return False
        allowed_reasons = {
            "signing_page_context",
            "business_signing_form_fields",
            "page_signing_context_business_fields",
            "previous_page_signing_context_business_fields",
            "terminal_signing_clause_context",
            "signing_context_business_fields",
        }
        if any(reason in allowed_reasons for reason in reasons):
            return True
        if "paired_parties" not in reasons:
            return False
        return bool(PARTY_LABEL_RE.search(compact))

    @staticmethod
    def _candidate_visual_pair_is_promotable(
        text: str,
        reasons: list[object],
        visual_candidate: dict[str, Any],
    ) -> bool:
        compact = SigningRegionStage._compact_candidate_text(text)
        if SigningRegionStage._candidate_text_looks_like_body(compact):
            return False

        strong_rule_support = any(
            reason
            in {
                "signing_page_context",
                "business_signing_form_fields",
                "page_signing_context_business_fields",
                "previous_page_signing_context_business_fields",
                "terminal_signing_clause_context",
                "signing_context_business_fields",
            }
            for reason in reasons
        )
        if strong_rule_support:
            return True

        if "paired_parties" not in reasons:
            return False
        if not PARTY_LABEL_RE.search(compact):
            return False
        return SigningRegionStage._visual_candidate_is_red_seal(visual_candidate)

    @staticmethod
    def _compact_candidate_text(text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    @staticmethod
    def _candidate_text_looks_like_body(compact_text: str) -> bool:
        if not compact_text:
            return False
        has_body_reference_to_signing_page = SigningRegionStage._has_body_reference_to_signing_page(compact_text)
        if SIGNING_CONTEXT_RE.search(compact_text) and not has_body_reference_to_signing_page:
            return False
        has_party_without_label = PARTY_RE.search(compact_text) and not PARTY_LABEL_RE.search(compact_text)
        has_strong_signing_label = any(
            pattern.search(compact_text)
            for pattern in [
                SEAL_RE,
                SIGN_RE,
                REPRESENTATIVE_RE,
            ]
        )
        if has_body_reference_to_signing_page:
            return (
                len(compact_text) > 40
                or BODY_VERB_RE.search(compact_text) is not None
                or NUMBERED_RE.match(compact_text) is not None
            )
        if has_party_without_label and not has_strong_signing_label:
            return True
        if (BODY_VERB_RE.search(compact_text) or NUMBERED_RE.match(compact_text)) and not has_strong_signing_label:
            return True
        if DATE_LABEL_RE.search(compact_text) and len(compact_text) > 40 and not has_strong_signing_label:
            return True
        return False

    @staticmethod
    def _has_body_reference_to_signing_page(compact_text: str) -> bool:
        return bool(
            re.search(r"(?:本合同|合同)?签署页(?:中|中的|载明|有关|所列|记载)", compact_text)
            or re.search(r"送达.{0,20}签署页", compact_text)
        )

    @staticmethod
    def _visual_candidate_is_red_seal(visual_candidate: dict[str, Any]) -> bool:
        label = str(visual_candidate.get("label") or "").lower()
        if "seal" in label or "stamp" in label or "章" in label:
            return True
        reasons = SigningRegionStage._candidate_list_value(visual_candidate.get("reasons"))
        return "red_seal_pixels" in reasons

    @staticmethod
    def _matching_visual_candidate(
        page_no: int,
        bbox: BBox,
        visual_candidates: list[dict[str, Any]],
        *,
        source_region_id: str = "",
    ) -> dict[str, Any] | None:
        for visual_candidate in visual_candidates:
            if not isinstance(visual_candidate, dict):
                continue
            if not SigningRegionStage._visual_candidate_meets_promotion_threshold(visual_candidate):
                continue
            if visual_candidate.get("page_no") != page_no:
                continue
            if source_region_id and visual_candidate.get("source_region_id") == source_region_id:
                return visual_candidate
            visual_bbox_payload = visual_candidate.get("bbox")
            if not isinstance(visual_bbox_payload, dict):
                continue
            try:
                visual_bbox = BBox.model_validate(visual_bbox_payload)
            except Exception:
                continue
            if SigningRegionStage._overlap_ratio(bbox, visual_bbox) >= 0.2:
                return visual_candidate
        return None

    @staticmethod
    def _visual_candidate_meets_promotion_threshold(visual_candidate: dict[str, Any]) -> bool:
        try:
            confidence = float(visual_candidate.get("confidence"))
        except (TypeError, ValueError):
            return False
        return confidence >= SigningRegionStage.visual_confidence_threshold

    @staticmethod
    def _candidate_regions_from_low_confidence(candidates: list[object]) -> list[SigningRegion]:
        regions: list[SigningRegion] = []
        for index, candidate in enumerate(candidates, start=1):
            if not isinstance(candidate, dict):
                continue
            try:
                page_no = int(candidate.get("page_no", 0))
            except (TypeError, ValueError):
                continue
            bbox_payload = candidate.get("bbox")
            if page_no <= 0 or not isinstance(bbox_payload, dict):
                continue
            try:
                regions.append(
                    SigningRegion(
                        region_id=f"LC-{page_no}-{index}",
                        page_no=page_no,
                        bbox=BBox.model_validate(bbox_payload),
                        confidence=float(candidate.get("score") or 0),
                        confidence_reasons=SigningRegionStage._candidate_list_value(candidate.get("reasons")),
                    )
                )
            except Exception:
                continue
        return regions

    @staticmethod
    def _candidate_regions_for_visual_scan(candidate_regions: list[SigningRegion]) -> list[SigningRegion]:
        if not settings.signing_opencv_scan_candidate_pages:
            return []

        filtered_regions: list[SigningRegion] = []
        included_pages: set[int] = set()
        max_pages = settings.signing_opencv_max_candidate_pages
        for region in sorted(candidate_regions, key=SigningRegionStage._visual_scan_candidate_sort_key):
            if region.page_no not in included_pages:
                if len(included_pages) >= max_pages:
                    continue
                included_pages.add(region.page_no)
            filtered_regions.append(region)
        return filtered_regions

    @staticmethod
    def _visual_scan_candidate_sort_key(region: SigningRegion) -> tuple[int, int, int]:
        reasons = set(region.confidence_reasons)
        if reasons.intersection(
            {
                "seal_signature_date_cluster",
                "signing_context_business_fields",
                "terminal_business_signing_fields",
                "business_signing_form_fields",
                "page_signing_context_business_fields",
                "previous_page_signing_context_business_fields",
                "terminal_signing_clause_context",
            }
        ):
            priority = 0
        elif {"paired_parties", "two_column_layout"}.issubset(reasons):
            priority = 1
        elif "party_label" in reasons:
            priority = 2
        elif {"paired_parties", "bottom_signing_position"}.issubset(reasons):
            priority = 3
        elif "paired_parties" in reasons:
            priority = 4
        elif {"signing_page_context", "bottom_signing_position"}.issubset(reasons):
            priority = 5
        elif "signing_page_context" in reasons:
            priority = 6
        else:
            priority = 7
        return (priority, region.page_no, int(region.bbox.y0))

    @staticmethod
    def _candidate_region_key(page_no: int, bbox: BBox) -> tuple[int, float, float, float, float]:
        return (page_no, bbox.x0, bbox.y0, bbox.x1, bbox.y1)

    def _collect_visual(
        self,
        pdf_path: Path,
        regions: list[SigningRegion],
        task_id: str,
        *,
        side: str,
        suppressed: list[dict[str, Any]],
        candidate_regions: list[SigningRegion] | None = None,
    ) -> dict[str, Any]:
        status: dict[str, Any] = {
            "enabled": self.visual_enabled,
            "available": False,
            "model_name": "",
            "error": "",
            "detection_count": 0,
            "fingerprint_count": 0,
            "_detection_elements": [],
            "_fingerprint_elements": [],
            "_visual_candidates": [],
            "_detection_result": None,
        }
        if not self.visual_enabled:
            status["error"] = "disabled"
            return status

        detector_regions = [*regions, *(candidate_regions or [])]
        detection_result = self._detect_visual(pdf_path, detector_regions, task_id)
        status["_detection_result"] = detection_result
        status.update(
            {
                "available": detection_result.available,
                "model_name": detection_result.model_name,
                "error": detection_result.error,
                "detection_count": len(detection_result.detections),
            }
        )
        if detection_result.available:
            status["_visual_candidates"] = [
                {
                    "page_no": detection.page_no,
                    "bbox": detection.bbox.model_dump(mode="json"),
                    "label": detection.label,
                    "confidence": detection.confidence,
                    "model_name": detection.model_name or detection_result.model_name,
                    "reasons": detection.raw_data.get("reasons", []),
                    "source_region_id": detection.raw_data.get("source_region_id", ""),
                    "used_for_promotion": False,
                }
                for detection in detection_result.detections
            ]
            detection_elements = self._visual_detection_elements(
                regions,
                detection_result,
                side=side,
                suppressed=suppressed,
            )
            status["_detection_elements"] = detection_elements
            status["detection_count"] = len(detection_elements)

        fingerprint_elements = self._visual_fingerprint_elements(pdf_path, regions)
        status["_fingerprint_elements"] = fingerprint_elements
        status["fingerprint_count"] = len(fingerprint_elements)
        return status

    def _refresh_visual_elements_for_regions(
        self,
        visual_status: dict[str, Any],
        regions: list[SigningRegion],
        pdf_path: Path,
        *,
        side: str,
    ) -> None:
        if not visual_status.get("enabled"):
            return

        detection_result = visual_status.get("_detection_result")
        if isinstance(detection_result, VisualDetectionResult) and detection_result.available:
            detection_elements = self._visual_detection_elements(
                regions,
                detection_result,
                side=side,
                suppressed=None,
            )
            visual_status["_detection_elements"] = detection_elements
            visual_status["detection_count"] = len(detection_elements)

        fingerprint_elements = self._visual_fingerprint_elements(pdf_path, regions)
        visual_status["_fingerprint_elements"] = fingerprint_elements
        visual_status["fingerprint_count"] = len(fingerprint_elements)

    def _detect_visual(self, pdf_path: Path, regions: list[SigningRegion], task_id: str) -> VisualDetectionResult:
        if self.visual_detector is None:
            return VisualDetectionResult(available=False, error="visual_detector_not_configured")
        try:
            return self.visual_detector.detect(pdf_path, regions, task_id)
        except Exception as exc:
            logger.debug("Signing visual detector failed in pipeline: %s", exc, exc_info=True)
            return VisualDetectionResult(available=False, error="visual_detector_failed")

    def _visual_detection_elements(
        self,
        regions: list[SigningRegion],
        detection_result: VisualDetectionResult,
        *,
        side: str,
        suppressed: list[dict[str, Any]] | None,
    ) -> list[tuple[SigningRegion, SigningElement]]:
        elements: list[tuple[SigningRegion, SigningElement]] = []
        for index, detection in enumerate(detection_result.detections, start=1):
            if detection.confidence < self.visual_confidence_threshold:
                if suppressed is not None:
                    suppressed.append(
                        {
                            "side": side,
                            "page_no": detection.page_no,
                            "bbox": detection.bbox.model_dump(mode="json"),
                            "label": detection.label,
                            "confidence": detection.confidence,
                            "reason": "low_visual_confidence",
                        }
                    )
                continue
            region = self._matching_region(regions, detection)
            if region is None:
                continue
            elements.append(
                (
                    region,
                    SigningElement(
                        element_id=f"{region.region_id}-visual-model-{index}",
                        element_type=self._visual_detection_type(detection),
                        page_no=detection.page_no,
                        bbox=detection.bbox,
                        text=detection.label,
                        confidence=detection.confidence,
                        source="visual_model",
                        visual_hash=str(detection.raw_data.get("visual_hash") or detection.raw_data.get("hash") or ""),
                        model_name=detection.model_name or detection_result.model_name,
                        raw_ref=detection.model_dump(mode="json"),
                    ),
                )
            )
        return elements

    def _visual_fingerprint_elements(
        self, pdf_path: Path, regions: list[SigningRegion]
    ) -> list[tuple[SigningRegion, SigningElement]]:
        if self.visual_fingerprinter is None:
            return []
        elements: list[tuple[SigningRegion, SigningElement]] = []
        for region in regions:
            try:
                fingerprint = self.visual_fingerprinter.fingerprint_region(pdf_path, region)
            except Exception as exc:
                logger.debug("Signing region fingerprint failed: %s", exc, exc_info=True)
                continue
            visual_hash = str(fingerprint.get("hash") or fingerprint.get("visual_hash") or "")
            if not visual_hash:
                continue
            elements.append(
                (
                    region,
                    SigningElement(
                        element_id=f"{region.region_id}-visual-fingerprint",
                        element_type=SigningElementType.VISUAL_AREA,
                        page_no=region.page_no,
                        bbox=region.bbox,
                        confidence=1.0,
                        source="visual_fingerprint",
                        visual_hash=visual_hash,
                        raw_ref=dict(fingerprint),
                    ),
                )
            )
        return elements

    @staticmethod
    def _apply_visual_enrichment_when_comparable(visual_status: dict[str, dict[str, Any]]) -> None:
        original_comparable = SigningRegionStage._has_comparable_visual_result(visual_status["original"])
        compare_comparable = SigningRegionStage._has_comparable_visual_result(visual_status["compare"])
        if not (original_comparable and compare_comparable):
            return
        for status in visual_status.values():
            for region, element in [
                *status.get("_detection_elements", []),
                *status.get("_fingerprint_elements", []),
            ]:
                region.elements.append(element)

    @staticmethod
    def _has_comparable_visual_result(status: dict[str, Any]) -> bool:
        if not status.get("enabled"):
            return False
        if status.get("available") and status.get("detection_count", 0) > 0:
            return True
        return status.get("fingerprint_count", 0) > 0

    @staticmethod
    def _debug_visual_status(visual_status: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {
            side: {key: value for key, value in status.items() if not key.startswith("_")}
            for side, status in visual_status.items()
        }

    @staticmethod
    def _matching_region(regions: list[SigningRegion], detection: VisualDetection) -> SigningRegion | None:
        source_region_id = str(detection.raw_data.get("source_region_id") or "")
        if source_region_id:
            for region in regions:
                if region.page_no == detection.page_no and region.region_id == source_region_id:
                    return region
        for region in regions:
            if (
                region.page_no == detection.page_no
                and SigningRegionStage._overlap_ratio(region.bbox, detection.bbox) >= 0.2
            ):
                return region
        return None

    @staticmethod
    def _overlap_ratio(region_bbox, detection_bbox) -> float:
        x0 = max(region_bbox.x0, detection_bbox.x0)
        y0 = max(region_bbox.y0, detection_bbox.y0)
        x1 = min(region_bbox.x1, detection_bbox.x1)
        y1 = min(region_bbox.y1, detection_bbox.y1)
        inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        base = max(1.0, (detection_bbox.x1 - detection_bbox.x0) * (detection_bbox.y1 - detection_bbox.y0))
        return inter / base

    @staticmethod
    def _visual_detection_type(detection: VisualDetection) -> SigningElementType:
        label = detection.label.lower()
        if "seal" in label or "stamp" in label or "章" in detection.label:
            return SigningElementType.SEAL
        if "signature" in label or "sign" in label or "签" in detection.label:
            return SigningElementType.SIGNATURE
        return SigningElementType.VISUAL_AREA

    @staticmethod
    def _default_visual_detector() -> VisualSignatureDetector | None:
        backend = settings.signing_visual_backend
        if backend == "off":
            return None
        if backend == "remote":
            return RemoteVisualSignatureDetector()
        if backend == "local":
            return LocalCpuVisualSignatureDetector()
        if backend == "opencv":
            return OpenCvVisualSignatureDetector()
        if settings.signing_visual_detector_url.strip():
            return RemoteVisualSignatureDetector()
        if settings.signing_visual_local_model_path.strip():
            return LocalCpuVisualSignatureDetector()
        return OpenCvVisualSignatureDetector()

    def _debug_configuration(self) -> dict[str, Any]:
        return {
            "visual_enabled": self.visual_enabled,
            "visual_backend": settings.signing_visual_backend,
            "visual_confidence_threshold": self.visual_confidence_threshold,
            "visual_detector": type(self.visual_detector).__name__ if self.visual_detector is not None else "",
            "visual_detector_url_configured": bool(settings.signing_visual_detector_url.strip()),
            "visual_local_model_configured": bool(settings.signing_visual_local_model_path.strip()),
            "visual_detector_timeout": settings.signing_visual_detector_timeout,
            "visual_fingerprinter": type(self.visual_fingerprinter).__name__
            if self.visual_fingerprinter is not None
            else "",
            "opencv_available": OpenCvVisualSignatureDetector._dependencies() is not None,
        }


class SplitStage:
    name = "差异识别中"
    start_progress = 41
    progress = 45

    def __init__(self, artifact_store: ArtifactStore = default_artifact_store) -> None:
        self.splitter = ClauseSplitter()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        extractions = ctx.require_extractions()
        original_doc = ctx.clause_document_original or extractions.original.document
        compare_doc = ctx.clause_document_compare or extractions.compare.document

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
            semantic_recall_mode=settings.matching.semantic_recall_mode,
            semantic_min_rule_candidates=settings.matching.semantic_min_rule_candidates,
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
            + len(ctx.signing_region_diffs)
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
            *ctx.signing_region_diffs,
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
        self.footer_visual = FooterAnnotationVisualComparator()

    def execute(self, ctx: PipelineContext) -> None:
        clauses = ctx.require_clauses()
        matches = ctx.require_matches()
        original_locate_clauses = _clauses_for_evidence(
            clauses.original_clauses,
            [pair.original for pair in matches.pairs],
        )
        compare_locate_clauses = _clauses_for_evidence(
            clauses.compare_clauses,
            [pair.compare for pair in matches.pairs],
        )
        ctx.diffs = self.evidence_locator.locate(
            ctx.diffs,
            original_locate_clauses,
            compare_locate_clauses,
        )
        _emit_progress(ctx, 66, self.name, "text_evidence_located")
        ctx.diffs = self.text_coordinate_locator.refine(
            ctx.original_pdf,
            ctx.compare_pdf,
            ctx.diffs,
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
        ctx.diffs = self.footer_visual.remove_overlapping_diffs(ctx.diffs)
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
        summary = self.profiler.apply_to_diffs(
            diffs=ctx.diffs,
            profiles=profiles,
            original_clauses=ctx.original_clauses,
            compare_clauses=ctx.compare_clauses,
        )
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

    def __init__(
        self,
        artifact_store: ArtifactStore = default_artifact_store,
        text_recovery: CounterpartTextRecovery | None = None,
    ) -> None:
        self.planner = OcrRemediationPlanner()
        self.relocator = EvidenceRelocator()
        self.text_recovery = text_recovery or CounterpartTextRecovery()
        self.debug_writer = CompareDebugWriter(artifact_store=artifact_store)

    def execute(self, ctx: PipelineContext) -> None:
        summary = self.planner.plan(ctx.task.ocr_quality_summary, ctx.diffs)
        ctx.task.ocr_remediation_summary = summary
        self._execute_relocation_actions(ctx, summary.actions)
        self._execute_counterpart_text_retries(ctx, summary.actions)
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

    def _execute_counterpart_text_retries(self, ctx: PipelineContext, actions: list[OcrRemediationAction]) -> None:
        diffs_by_id = {diff.diff_id: diff for diff in ctx.diffs}
        resolved_ids: set[str] = set()
        for action in actions:
            if action.action_type != "RETRY_OCR_PAGE" or action.status != "PLANNED":
                continue
            diff = diffs_by_id.get(action.diff_id or "")
            if diff is None:
                action.status = "SKIPPED"
                action.notes.append("DIFF_NOT_FOUND")
                continue
            recovered = self.text_recovery.recover(
                diff=diff,
                side=action.side,
                page_no=action.page_no,
                original_pdf=ctx.original_pdf,
                compare_pdf=ctx.compare_pdf,
            )
            if not recovered:
                action.status = "FAILED"
                action.notes.append("COUNTERPART_TEXT_NOT_RECOVERED")
                continue
            action.status = "SUCCEEDED"
            action.changed_diff_text = True
            action.after_quality = {"recovered_text_length": len(recovered)}
            action.review_flags_added = ["OCR_COUNTERPART_TEXT_RECOVERED"]
            action.notes.append("FALSE_POSITIVE_SUPPRESSED")
            resolved_ids.add(diff.diff_id)
        if resolved_ids:
            ctx.diffs[:] = [diff for diff in ctx.diffs if diff.diff_id not in resolved_ids]

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
        successful_diff_ids = {action.diff_id for action in actions if action.status == "SUCCEEDED" and action.diff_id}
        manual_count = sum(1 for action in actions if action.status == "MANUAL_REVIEW_REQUIRED")
        unresolved_count = sum(
            1 for action in actions if action.status in {"PLANNED", "FAILED", "MANUAL_REVIEW_REQUIRED"}
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
        if summary is None:
            return

        final_ids = {diff.diff_id for diff in ctx.diffs}
        remaining_actions: list[OcrRemediationAction] = []
        for action in summary.actions:
            if action.diff_id is None:
                remaining_actions.append(action)
                continue
            original_diff_id = action.diff_id
            remapped_diff_id = merged_to_winner.get(original_diff_id, original_diff_id)
            if remapped_diff_id != original_diff_id:
                action.diff_id = remapped_diff_id
                action.action_id = DiffQualityStage._remapped_ocr_action_id(
                    action,
                    original_diff_id,
                    remapped_diff_id,
                )
            if remapped_diff_id in final_ids:
                remaining_actions.append(action)
        actions_by_id: dict[str, list[OcrRemediationAction]] = {}
        for action in remaining_actions:
            actions_by_id.setdefault(action.action_id, []).append(action)
        summary.actions = [
            _merge_ocr_remediation_actions(actions_by_id[action_id]) for action_id in sorted(actions_by_id)
        ]
        OcrRemediationStage._refresh_summary_counts(summary)

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
        diffs = [diff for diff in ctx.require_diffs() if diff.diff_id not in ctx.signing_region_covered_diff_ids]
        task.diffs, dedupe_remap = _dedupe_final_diffs(_filter_compare_option_diffs(task, diffs))
        ctx.diffs = task.diffs
        _remap_ocr_quality_summary_after_final_dedupe(task, dedupe_remap)
        DiffQualityStage._remap_ocr_remediation_summary(ctx, dedupe_remap)
        _remap_audit_item_reviews_after_final_dedupe(task, dedupe_remap)
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
        excluded_source_types.add("signing_region")
    if task.compare_options.signing_region_mode == "off":
        excluded_source_types.add("signing_region")
    if task.compare_options.ignore_headers_footers:
        excluded_source_types.add("header_footer")
    if not excluded_source_types:
        return diffs
    return [diff for diff in diffs if diff.source_type not in excluded_source_types]


def _dedupe_final_diffs(diffs: list[DiffItem]) -> tuple[list[DiffItem], dict[str, str]]:
    if not diffs:
        return [], {}

    original_ids = {diff.diff_id for diff in diffs if diff.diff_id}
    working_diffs, generated_ids = _with_stable_missing_diff_ids(diffs)

    members_by_id: dict[str, list[DiffItem]] = {}
    for diff in sorted(working_diffs, key=_diff_partition_key):
        members_by_id.setdefault(diff.diff_id, []).append(diff)

    groups: list[list[DiffItem]] = []
    same_id_groups = sorted(
        members_by_id.values(),
        key=lambda members: min(_diff_partition_key(member) for member in members),
    )
    for same_id_members in same_id_groups:
        target = next(
            (
                group
                for group in groups
                if all(
                    _different_id_diffs_are_spatial_duplicates(member, grouped_member)
                    for member in same_id_members
                    for grouped_member in group
                )
            ),
            None,
        )
        if target is None:
            groups.append(list(same_id_members))
        else:
            target.extend(same_id_members)

    remap: dict[str, str] = {}
    result: list[DiffItem] = []
    empty_id_targets: set[str] = set()
    for members in groups:
        member_ids = {member.diff_id for member in members}
        original_member_ids = sorted(member_ids & original_ids)
        canonical_id = original_member_ids[0] if original_member_ids else sorted(member_ids)[0]
        for old_id in original_member_ids:
            if old_id != canonical_id:
                remap[old_id] = canonical_id
        if member_ids & generated_ids:
            empty_id_targets.add(canonical_id)
        result.append(_merge_final_dedupe_group(members, canonical_id))

    if len(empty_id_targets) == 1:
        remap[""] = next(iter(empty_id_targets))
    result.sort(key=lambda diff: (diff.diff_id, diff.model_dump_json()))
    return result, remap


def _with_stable_missing_diff_ids(diffs: list[DiffItem]) -> tuple[list[DiffItem], set[str]]:
    working = [diff.model_copy(deep=True) for diff in diffs if diff.diff_id]
    missing: list[tuple[str, str, DiffItem]] = []
    for diff in diffs:
        if diff.diff_id:
            continue
        stable_payload = _stable_missing_id_payload(diff)
        stable_json = json.dumps(stable_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        full_json = json.dumps(
            diff.model_dump(mode="json", exclude={"diff_id"}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        missing.append((stable_json, full_json, diff))

    generated_ids: set[str] = set()
    digest_counts: dict[str, int] = {}
    for stable_json, _, diff in sorted(missing, key=lambda item: (item[0], item[1])):
        digest = hashlib.sha256(stable_json.encode("utf-8")).hexdigest()
        digest_counts[digest] = digest_counts.get(digest, 0) + 1
        generated_id = f"AUTO-{digest}-{digest_counts[digest]:04d}"
        generated_ids.add(generated_id)
        working.append(diff.model_copy(deep=True, update={"diff_id": generated_id}))
    return working, generated_ids


def _stable_missing_id_payload(diff: DiffItem) -> dict[str, Any]:
    return diff.model_dump(
        mode="json",
        exclude={
            "diff_id",
            "quality_status",
            "review_flags",
            "structural_flags",
            "text_confidence",
            "merged_sources",
            "review_status",
            "review_comment",
            "reviewed_by",
            "reviewed_at",
        },
    )


def _diff_partition_key(diff: DiffItem) -> tuple[str, str]:
    semantic_and_location = {
        "source_type": diff.source_type,
        "section_type": diff.section_type,
        "section_path": diff.section_path,
        "original_clause_id": diff.original_clause_id,
        "compare_clause_id": diff.compare_clause_id,
        "diff_type": diff.diff_type,
        "original_text": diff.original_text,
        "compare_text": diff.compare_text,
        "original_evidence": [item.model_dump(mode="json") for item in diff.original_evidence],
        "compare_evidence": [item.model_dump(mode="json") for item in diff.compare_evidence],
    }
    full_without_id = diff.model_dump(mode="json", exclude={"diff_id"})
    return (
        json.dumps(semantic_and_location, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        json.dumps(full_without_id, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _different_id_diffs_are_spatial_duplicates(left: DiffItem, right: DiffItem) -> bool:
    if left.diff_id and left.diff_id == right.diff_id:
        return True
    if (
        left.source_type != right.source_type
        or left.section_type != right.section_type
        or left.diff_type != right.diff_type
        or left.original_text != right.original_text
        or left.compare_text != right.compare_text
    ):
        return False
    if not _same_clause_or_stable_path(left, right):
        return False
    return _matching_located_evidence(left, right)


def _same_clause_or_stable_path(left: DiffItem, right: DiffItem) -> bool:
    left_clause_ids = (left.original_clause_id or "", left.compare_clause_id or "")
    right_clause_ids = (right.original_clause_id or "", right.compare_clause_id or "")
    same_clause_ids = any(left_clause_ids) and left_clause_ids == right_clause_ids
    same_section_path = bool(left.section_path) and left.section_path == right.section_path
    return same_clause_ids or same_section_path


def _matching_located_evidence(left: DiffItem, right: DiffItem) -> bool:
    side_results: list[bool] = []
    for left_evidence, right_evidence in (
        (left.original_evidence, right.original_evidence),
        (left.compare_evidence, right.compare_evidence),
    ):
        left_located = [item for item in left_evidence if _evidence_is_located(item)]
        right_located = [item for item in right_evidence if _evidence_is_located(item)]
        if not left_located and not right_located:
            continue
        if not left_located or not right_located:
            return False
        side_results.append(
            any(
                left_item.page_no == right_item.page_no
                and coverage(left_item.bbox, right_item.bbox) >= FINAL_DIFF_EVIDENCE_OVERLAP_THRESHOLD
                for left_item in left_located
                for right_item in right_located
            )
        )
    return bool(side_results) and all(side_results)


def coverage(left: BBox, right: BBox) -> float:
    left_coords, right_coords = _comparable_bbox_coordinates(left, right)
    left_area = _bbox_area(left_coords)
    right_area = _bbox_area(right_coords)
    minimum_area = min(left_area, right_area)
    if minimum_area <= 0:
        return 0.0
    intersection_width = max(0.0, min(left_coords[2], right_coords[2]) - max(left_coords[0], right_coords[0]))
    intersection_height = max(0.0, min(left_coords[3], right_coords[3]) - max(left_coords[1], right_coords[1]))
    return (intersection_width * intersection_height) / minimum_area


def _comparable_bbox_coordinates(
    left: BBox,
    right: BBox,
) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]]:
    if left.normalized is not None and right.normalized is not None:
        left_normalized = (left.normalized.x0, left.normalized.y0, left.normalized.x1, left.normalized.y1)
        right_normalized = (right.normalized.x0, right.normalized.y0, right.normalized.x1, right.normalized.y1)
        if _bbox_area(left_normalized) > 0 and _bbox_area(right_normalized) > 0:
            return left_normalized, right_normalized
    return (left.x0, left.y0, left.x1, left.y1), (right.x0, right.y0, right.x1, right.y1)


def _evidence_is_located(evidence: EvidenceBox) -> bool:
    if evidence.page_no < 0 or evidence.bbox is None:
        return False
    raw = evidence.bbox
    raw_area = _bbox_area((raw.x0, raw.y0, raw.x1, raw.y1))
    if evidence.bbox.normalized is None:
        return raw_area > 0
    normalized = evidence.bbox.normalized
    normalized_area = _bbox_area((normalized.x0, normalized.y0, normalized.x1, normalized.y1))
    return normalized_area > 0 or raw_area > 0


def _bbox_area(coords: tuple[float, float, float, float]) -> float:
    return max(0.0, coords[2] - coords[0]) * max(0.0, coords[3] - coords[1])


def _merge_final_dedupe_group(members: list[DiffItem], canonical_id: str) -> DiffItem:
    if len(members) == 1:
        return members[0].model_copy(deep=True, update={"diff_id": canonical_id})
    ordered = sorted(members, key=_diff_partition_key)
    survivor = ordered[0].model_copy(deep=True, update={"diff_id": canonical_id})
    field_conflict = False
    for field_name in (
        "diff_type",
        "original_clause_id",
        "compare_clause_id",
        "clause_no",
        "title",
        "original_text",
        "compare_text",
        "original_snippet",
        "compare_snippet",
        "readable_change",
        "source_type",
        "section_type",
        "match_method",
        "match_confidence",
    ):
        empty_value = None if field_name in {"original_clause_id", "compare_clause_id"} else ""
        value, conflict = _merge_informative_scalar(
            (getattr(member, field_name) for member in members),
            empty_value=empty_value,
        )
        setattr(survivor, field_name, value)
        if field_name != "source_type":
            field_conflict = field_conflict or conflict
    survivor.section_path, conflict = _merge_informative_list(member.section_path for member in members)
    field_conflict = field_conflict or conflict
    survivor.match_score = _maximum_optional_number(member.match_score for member in members)
    survivor.text_confidence = _maximum_optional_number(member.text_confidence for member in members)
    survivor.match_score_details, conflict = _deep_merge_dicts(member.match_score_details for member in members)
    field_conflict = field_conflict or conflict
    survivor.match_candidates = _merge_plain_model_list(
        candidate for member in members for candidate in member.match_candidates
    )
    survivor.original_evidence = _merge_model_list(item for member in members for item in member.original_evidence)
    survivor.compare_evidence = _merge_model_list(item for member in members for item in member.compare_evidence)
    survivor.original_change_ranges = _merge_model_list(
        item for member in members for item in member.original_change_ranges
    )
    survivor.compare_change_ranges = _merge_model_list(
        item for member in members for item in member.compare_change_ranges
    )
    survivor.structural_flags = sorted({flag for member in members for flag in member.structural_flags})
    survivor.review_flags = sorted({flag for member in members for flag in member.review_flags})
    survivor.merged_sources = sorted(
        {source for member in members for source in [member.source_type, *member.merged_sources] if source}
    )
    if any(member.quality_status == "NEEDS_REVIEW" for member in members):
        survivor.quality_status = "NEEDS_REVIEW"
    if field_conflict:
        survivor.quality_status = "NEEDS_REVIEW"
        survivor.review_flags = sorted({*survivor.review_flags, "FINAL_DEDUPE_FIELD_CONFLICT"})
    _merge_diff_review_projection(survivor, members)
    return survivor


def _merge_model_list(items: Any) -> list[Any]:
    by_payload = {item.model_dump_json(): item for item in items}
    return [by_payload[key].model_copy(deep=True) for key in sorted(by_payload)]


def _merge_plain_model_list(items: Any) -> list[Any]:
    by_payload = {json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")): item for item in items}
    return [by_payload[key] for key in sorted(by_payload)]


def _merge_informative_scalar(values: Any, *, empty_value: Any = "") -> tuple[Any, bool]:
    informative = {value for value in values if value is not None and (not isinstance(value, str) or value.strip())}
    if not informative:
        return empty_value, False
    chosen = max(informative, key=lambda value: (len(str(value).strip()), str(value)))
    return chosen, len(informative) > 1


def _merge_informative_list(values: Any) -> tuple[list[Any], bool]:
    informative = {
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")): value for value in values if value
    }
    if not informative:
        return [], False
    chosen_key = max(informative, key=lambda key: (len(informative[key]), key))
    return list(informative[chosen_key]), len(informative) > 1


def _maximum_optional_number(values: Any) -> Any:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _deep_merge_dicts(values: Any) -> tuple[dict[str, Any], bool]:
    dictionaries = [value for value in values if value]
    if not dictionaries:
        return {}, False
    merged: dict[str, Any] = {}
    conflict = False
    for key in sorted({key for value in dictionaries for key in value}):
        candidates = [value[key] for value in dictionaries if key in value]
        if all(isinstance(candidate, dict) for candidate in candidates):
            merged[key], nested_conflict = _deep_merge_dicts(candidates)
            conflict = conflict or nested_conflict
            continue
        payloads = {
            json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")): candidate
            for candidate in candidates
        }
        chosen_payload = max(payloads, key=lambda payload: (len(payload), payload))
        merged[key] = payloads[chosen_payload]
        conflict = conflict or len(payloads) > 1
    return merged, conflict


def _merge_ocr_remediation_actions(actions: list[OcrRemediationAction]) -> OcrRemediationAction:
    status_priority = {
        "SUCCEEDED": 0,
        "SKIPPED": 1,
        "PLANNED": 2,
        "FAILED": 3,
        "MANUAL_REVIEW_REQUIRED": 4,
    }
    chosen = max(
        actions,
        key=lambda action: (
            status_priority[action.status],
            action.model_dump_json(),
        ),
    ).model_copy(deep=True)
    chosen.before_quality, _ = _deep_merge_dicts(action.before_quality for action in actions)
    chosen.after_quality, _ = _deep_merge_dicts(action.after_quality for action in actions)
    chosen.review_flags_added = sorted({flag for action in actions for flag in action.review_flags_added})
    chosen.notes = sorted({note for action in actions for note in action.notes})
    chosen.reason, _ = _merge_informative_scalar(action.reason for action in actions)
    chosen.changed_evidence = chosen.status == "SUCCEEDED" and any(action.changed_evidence for action in actions)
    chosen.changed_diff_text = chosen.status == "SUCCEEDED" and any(action.changed_diff_text for action in actions)
    return chosen


def _merge_diff_review_projection(survivor: DiffItem, members: list[DiffItem]) -> None:
    reviewed = [member for member in members if member.review_status != "UNREVIEWED"]
    if not reviewed:
        survivor.review_status = "UNREVIEWED"
        survivor.review_comment = ""
        survivor.reviewed_by = ""
        survivor.reviewed_at = ""
        return
    chosen = max(
        reviewed,
        key=lambda member: (member.reviewed_at, member.reviewed_by, member.review_comment, member.review_status),
    )
    statuses = {member.review_status for member in reviewed}
    survivor.review_status = chosen.review_status if len(statuses) == 1 else "NEEDS_REVIEW"
    survivor.review_comment = chosen.review_comment
    survivor.reviewed_by = chosen.reviewed_by
    survivor.reviewed_at = chosen.reviewed_at


def _remap_audit_item_reviews_after_final_dedupe(
    task: CompareTask,
    dedupe_remap: dict[str, str],
) -> None:
    if not task.audit_item_reviews or not dedupe_remap:
        return

    grouped_reviews: dict[str, list[AuditItemReview]] = {}
    for item_id, review in sorted(task.audit_item_reviews.items()):
        diff_id, separator, suffix = item_id.partition(":")
        canonical_diff_id = dedupe_remap.get(diff_id, diff_id)
        canonical_item_id = f"{canonical_diff_id}:{suffix}" if separator else canonical_diff_id
        grouped_reviews.setdefault(canonical_item_id, []).append(review)

    task.audit_item_reviews = {
        item_id: _merge_audit_item_review_projection(reviews) for item_id, reviews in sorted(grouped_reviews.items())
    }


def _merge_audit_item_review_projection(reviews: list[AuditItemReview]) -> AuditItemReview:
    reviewed = [review for review in reviews if review.review_status != "UNREVIEWED"]
    if not reviewed:
        return AuditItemReview()
    chosen = max(
        reviewed,
        key=lambda review: (review.reviewed_at, review.reviewed_by, review.review_comment, review.review_status),
    )
    statuses = {review.review_status for review in reviewed}
    return chosen.model_copy(
        deep=True,
        update={"review_status": chosen.review_status if len(statuses) == 1 else "NEEDS_REVIEW"},
    )


def _remap_ocr_quality_summary_after_final_dedupe(
    task: CompareTask,
    dedupe_remap: dict[str, str],
) -> None:
    summary = task.ocr_quality_summary
    if summary is None:
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
