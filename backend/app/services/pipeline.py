from __future__ import annotations

import dataclasses
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from app.errors import PipelineContractError
from app.infrastructure.execution_state import TaskExecutionContext
from app.infrastructure.task_repository import TaskRepository, default_task_repository
from app.models import (
    Clause,
    ClausePair,
    CompareTask,
    DiffItem,
)
from app.services.extractors.base import ExtractionResult
from app.services.pipeline_metrics import PipelineMetrics, StageMetrics, get_process_memory_mb
from app.services.progress_bus import ProgressBus, ProgressEvent

logger = logging.getLogger(__name__)


@dataclass
class ExtractionPair:
    original: ExtractionResult
    compare: ExtractionResult


@dataclass
class TableDiffResult:
    header_footer_diffs: list[DiffItem] = field(default_factory=list)
    metadata_diffs: list[DiffItem] = field(default_factory=list)
    table_diffs: list[DiffItem] = field(default_factory=list)
    table_warnings: list[str] = field(default_factory=list)


@dataclass
class ClauseSplitResult:
    original_clauses: list[Clause] = field(default_factory=list)
    compare_clauses: list[Clause] = field(default_factory=list)


@dataclass
class ClauseMatchResult:
    pairs: list[ClausePair] = field(default_factory=list)


@dataclass
class ClauseDiffResult:
    clause_diffs: list[DiffItem] = field(default_factory=list)
    diffs: list[DiffItem] = field(default_factory=list)


@dataclass
class PipelineContext:
    """Shared mutable state flowing through the pipeline."""

    task: CompareTask
    original_pdf: Path
    compare_pdf: Path
    execution_context: TaskExecutionContext | None = None

    original_extraction: ExtractionResult | None = None
    compare_extraction: ExtractionResult | None = None
    header_footer_diffs: list[DiffItem] = field(default_factory=list)
    metadata_diffs: list[DiffItem] = field(default_factory=list)
    table_diffs: list[DiffItem] = field(default_factory=list)
    table_warnings: list[str] = field(default_factory=list)
    original_clauses: list[Clause] = field(default_factory=list)
    compare_clauses: list[Clause] = field(default_factory=list)
    pairs: list[ClausePair] = field(default_factory=list)
    clause_diffs: list[DiffItem] = field(default_factory=list)
    seal_diffs: list[DiffItem] = field(default_factory=list)
    signing_pages_original: list[Any] = field(default_factory=list)
    signing_pages_compare: list[Any] = field(default_factory=list)
    signing_blocks_original: list[Any] = field(default_factory=list)
    signing_blocks_compare: list[Any] = field(default_factory=list)
    clause_document_original: Any | None = None
    clause_document_compare: Any | None = None
    signing_regions_original: list[Any] = field(default_factory=list)
    signing_regions_compare: list[Any] = field(default_factory=list)
    signing_region_diffs: list[DiffItem] = field(default_factory=list)
    signing_region_covered_diff_ids: set[str] = field(default_factory=set)
    signing_region_debug: dict[str, Any] = field(default_factory=dict)
    diffs: list[DiffItem] = field(default_factory=list)
    progress_callback: Callable[[int, str, dict[str, Any] | None], None] | None = None

    def set_extractions(self, original: ExtractionResult, compare: ExtractionResult) -> ExtractionPair:
        self.original_extraction = original
        self.compare_extraction = compare
        return ExtractionPair(original=original, compare=compare)

    def require_extractions(self) -> ExtractionPair:
        if self.original_extraction is None or self.compare_extraction is None:
            raise PipelineContractError("Pipeline stage requires document extraction results.")
        return ExtractionPair(original=self.original_extraction, compare=self.compare_extraction)

    def set_table_diffs(
        self,
        *,
        header_footer_diffs: list[DiffItem],
        metadata_diffs: list[DiffItem],
        table_diffs: list[DiffItem],
        table_warnings: list[str],
    ) -> TableDiffResult:
        self.header_footer_diffs = header_footer_diffs
        self.metadata_diffs = metadata_diffs
        self.table_diffs = table_diffs
        self.table_warnings = table_warnings
        return TableDiffResult(
            header_footer_diffs=header_footer_diffs,
            metadata_diffs=metadata_diffs,
            table_diffs=table_diffs,
            table_warnings=table_warnings,
        )

    def require_table_diffs(self) -> TableDiffResult:
        return TableDiffResult(
            header_footer_diffs=self.header_footer_diffs,
            metadata_diffs=self.metadata_diffs,
            table_diffs=self.table_diffs,
            table_warnings=self.table_warnings,
        )

    def set_clauses(self, original_clauses: list[Clause], compare_clauses: list[Clause]) -> ClauseSplitResult:
        self.original_clauses = original_clauses
        self.compare_clauses = compare_clauses
        return ClauseSplitResult(original_clauses, compare_clauses)

    def require_clauses(self) -> ClauseSplitResult:
        if not self.original_clauses and not self.compare_clauses:
            raise PipelineContractError("Pipeline stage requires split clauses.")
        return ClauseSplitResult(self.original_clauses, self.compare_clauses)

    def set_matches(self, pairs: list[ClausePair]) -> ClauseMatchResult:
        self.pairs = pairs
        return ClauseMatchResult(pairs)

    def require_matches(self) -> ClauseMatchResult:
        return ClauseMatchResult(self.pairs)

    def set_clause_diffs(self, clause_diffs: list[DiffItem], diffs: list[DiffItem]) -> ClauseDiffResult:
        self.clause_diffs = clause_diffs
        self.diffs = diffs
        return ClauseDiffResult(clause_diffs, diffs)

    def require_diffs(self) -> list[DiffItem]:
        return self.diffs


class PipelineStage(Protocol):
    """Single processing step in the comparison pipeline."""

    name: str
    start_progress: int
    progress: int

    def execute(self, ctx: PipelineContext) -> None: ...


def _update_progress(ctx: PipelineContext, stage: str, progress: int, repository: TaskRepository) -> None:
    progress = min(max(progress, 0), 99)

    if ctx.execution_context is not None:
        _raise_if_cancelled(ctx)
        coordinator = getattr(ctx.execution_context.cancellation_token, "coordinator", None)
        if coordinator is not None and coordinator.has_terminal_dependencies:
            persisted = coordinator.commit_progress(
                ctx.execution_context.job_id,
                worker_id=ctx.execution_context.worker_id,
                stage=stage,
                progress_percent=progress,
            )
            ctx.task.stage = persisted.stage
            ctx.task.progress_percent = persisted.progress_percent
            ctx.task.updated_at = persisted.updated_at
            ctx.task.revision = persisted.revision
            _raise_if_cancelled(ctx)
            return

    def mutate(task: CompareTask) -> None:
        task.stage = stage
        task.progress_percent = max(task.progress_percent, progress)

    try:
        persisted = repository.update_compare_task(ctx.task.task_id, mutate)
    except FileNotFoundError:
        mutate(ctx.task)
        ctx.task.updated_at = datetime.now(UTC).isoformat()
        repository.save_compare_task(ctx.task)
    else:
        ctx.task.stage = persisted.stage
        ctx.task.progress_percent = persisted.progress_percent
        ctx.task.updated_at = persisted.updated_at
        ctx.task.revision = persisted.revision

    _raise_if_cancelled(ctx)
    ProgressBus.get_instance().publish(
        ProgressEvent(
            task_id=ctx.task.task_id,
            stage=ctx.task.stage,
            progress_percent=ctx.task.progress_percent,
            status="PROCESSING",
        )
    )


class ComparePipeline:
    """Orchestrates comparison stages sequentially."""

    def __init__(
        self,
        stages: list[PipelineStage] | None = None,
        repository: TaskRepository = default_task_repository,
    ) -> None:
        self.stages = stages or _default_stages()
        self.repository = repository

    def run(self, ctx: PipelineContext) -> CompareTask:
        pipeline_t0 = time.perf_counter()
        metrics = PipelineMetrics(
            task_id=ctx.task.task_id,
            started_at=datetime.now(UTC).isoformat(),
        )
        peak_memory = 0.0
        for stage in self.stages:
            _raise_if_cancelled(ctx)
            _update_progress(ctx, stage.name, stage.start_progress, self.repository)
            _raise_if_cancelled(ctx)
            stage_t0 = time.perf_counter()
            mem_start = get_process_memory_mb()
            peak_memory = max(peak_memory, mem_start)
            sm = StageMetrics(name=stage.name, memory_mb_start=mem_start)
            try:
                stage.execute(ctx)
                _raise_if_cancelled(ctx)
            except Exception as exc:
                sm.error = str(exc)
                sm.duration_seconds = time.perf_counter() - stage_t0
                sm.memory_mb_end = get_process_memory_mb()
                peak_memory = max(peak_memory, sm.memory_mb_end)
                metrics.stages.append(sm)
                metrics.finished_at = datetime.now(UTC).isoformat()
                metrics.total_duration_seconds = time.perf_counter() - pipeline_t0
                metrics.peak_memory_mb = peak_memory
                ctx.task.metrics = {**ctx.task.metrics, **dataclasses.asdict(metrics)}
                _raise_if_cancelled(ctx)
                raise
            sm.duration_seconds = time.perf_counter() - stage_t0
            sm.memory_mb_end = get_process_memory_mb()
            metrics.stages.append(sm)
            peak_memory = max(peak_memory, sm.memory_mb_end)
            _update_progress(ctx, stage.name, stage.progress, self.repository)
        metrics.finished_at = datetime.now(UTC).isoformat()
        metrics.total_duration_seconds = time.perf_counter() - pipeline_t0
        metrics.peak_memory_mb = peak_memory
        ctx.task.metrics = {**ctx.task.metrics, **dataclasses.asdict(metrics)}

        _raise_if_cancelled(ctx)
        try:
            persisted = self.repository.load_compare_task(ctx.task.task_id)
        except FileNotFoundError:
            pass
        else:
            ctx.task.diffs = _merge_review_state(persisted.diffs, ctx.task.diffs)
        return ctx.task


def _raise_if_cancelled(ctx: PipelineContext) -> None:
    if ctx.execution_context is not None:
        ctx.execution_context.cancellation_token.raise_if_cancelled()


def _copy_processing_result(target: CompareTask, source: CompareTask) -> None:
    target.status = source.status
    target.stage = source.stage
    target.progress_percent = source.progress_percent
    target.original_filename = source.original_filename
    target.compare_filename = source.compare_filename
    target.original_pdf_path = source.original_pdf_path
    target.compare_pdf_path = source.compare_pdf_path
    target.original_highlight_pdf_path = source.original_highlight_pdf_path
    target.compare_highlight_pdf_path = source.compare_highlight_pdf_path
    target.extractor_used = source.extractor_used
    target.ocr_raw_result_path = source.ocr_raw_result_path
    target.ocr_raw_result_paths = source.ocr_raw_result_paths
    target.parse_warnings = source.parse_warnings
    target.parse_warning_details = source.parse_warning_details
    target.document_profiles = source.document_profiles
    target.ocr_quality_summary = source.ocr_quality_summary
    target.ocr_remediation_summary = source.ocr_remediation_summary
    target.debug_artifact_paths = source.debug_artifact_paths
    target.diff_count = source.diff_count
    target.audit_item_reviews = source.audit_item_reviews
    target.diffs = _merge_review_state(target.diffs, source.diffs)
    target.errors = source.errors
    target.metrics = source.metrics


def _merge_review_state(existing: list[DiffItem], incoming: list[DiffItem]) -> list[DiffItem]:
    existing_by_id = {diff.diff_id: diff for diff in existing}
    merged: list[DiffItem] = []
    for diff in incoming:
        previous = existing_by_id.get(diff.diff_id)
        if previous is None or not _has_review_state(previous):
            merged.append(diff)
            continue
        merged.append(
            diff.model_copy(
                update={
                    "review_status": previous.review_status,
                    "review_comment": previous.review_comment,
                    "reviewed_by": previous.reviewed_by,
                    "reviewed_at": previous.reviewed_at,
                }
            )
        )
    return merged


def _has_review_state(diff: DiffItem) -> bool:
    return (
        diff.review_status != "UNREVIEWED"
        or bool(diff.review_comment)
        or bool(diff.reviewed_by)
        or bool(diff.reviewed_at)
    )


def _default_stages() -> list[PipelineStage]:
    from app.services.pipeline_stages import (
        ClauseDiffStage,
        DocumentPreparationStage,
        DocumentUnderstandingStage,
        DiffQualityStage,
        EvidenceStage,
        ExtractionStage,
        MatchStage,
        ModelRoutingStage,
        OcrQualityStage,
        OcrRemediationStage,
        PreClauseDiffStage,
        SigningRegionStage,
        SplitStage,
        SummaryStage,
        VisualizationStage,
    )

    return [
        ExtractionStage(),
        DocumentUnderstandingStage(),
        DocumentPreparationStage(),
        PreClauseDiffStage(),
        SigningRegionStage(),
        SplitStage(),
        MatchStage(),
        ClauseDiffStage(),
        EvidenceStage(),
        OcrQualityStage(),
        OcrRemediationStage(),
        ModelRoutingStage(),
        DiffQualityStage(),
        VisualizationStage(),
        SummaryStage(),
    ]
