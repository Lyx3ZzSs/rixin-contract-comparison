from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.infrastructure.task_repository import TaskRepository, default_task_repository
from app.models import (
    Clause,
    ClausePair,
    CompareTask,
    DiffItem,
)
from app.services.extractors.base import ExtractionResult

logger = logging.getLogger(__name__)


@dataclass
class PipelineContext:
    """Shared mutable state flowing through the pipeline."""

    task: CompareTask
    original_pdf: Path
    compare_pdf: Path

    original_extraction: ExtractionResult | None = None
    compare_extraction: ExtractionResult | None = None
    metadata_diffs: list[DiffItem] = field(default_factory=list)
    table_diffs: list[DiffItem] = field(default_factory=list)
    table_warnings: list[str] = field(default_factory=list)
    original_clauses: list[Clause] = field(default_factory=list)
    compare_clauses: list[Clause] = field(default_factory=list)
    pairs: list[ClausePair] = field(default_factory=list)
    clause_diffs: list[DiffItem] = field(default_factory=list)
    diffs: list[DiffItem] = field(default_factory=list)


class PipelineStage(Protocol):
    """Single processing step in the comparison pipeline."""

    name: str
    progress: int

    def execute(self, ctx: PipelineContext) -> None: ...


def _update_progress(ctx: PipelineContext, stage: str, progress: int, repository: TaskRepository) -> None:
    def mutate(task: CompareTask) -> None:
        task.stage = stage
        task.progress_percent = max(task.progress_percent, min(progress, 99))

    try:
        persisted = repository.update_compare_task(ctx.task.task_id, mutate)
    except FileNotFoundError:
        mutate(ctx.task)
        ctx.task.updated_at = datetime.now(UTC).isoformat()
        repository.save_compare_task(ctx.task)
        return

    ctx.task.stage = persisted.stage
    ctx.task.progress_percent = persisted.progress_percent
    ctx.task.updated_at = persisted.updated_at
    ctx.task.revision = persisted.revision


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
        for stage in self.stages:
            _update_progress(ctx, stage.name, stage.progress, self.repository)
            stage.execute(ctx)
        ctx.task.status = "COMPLETED"
        ctx.task.stage = "已完成"
        ctx.task.progress_percent = 100
        ctx.task.updated_at = datetime.now(UTC).isoformat()
        try:
            ctx.task = self.repository.update_compare_task(
                ctx.task.task_id,
                lambda persisted: _copy_processing_result(persisted, ctx.task),
            )
        except FileNotFoundError:
            self.repository.save_compare_task(ctx.task)
        return ctx.task


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
    target.parse_warnings = source.parse_warnings
    target.parse_warning_details = source.parse_warning_details
    target.document_profiles = source.document_profiles
    target.debug_artifact_paths = source.debug_artifact_paths
    target.diff_count = source.diff_count
    target.high_risk_count = source.high_risk_count
    target.medium_risk_count = source.medium_risk_count
    target.low_risk_count = source.low_risk_count
    target.ai_summary = source.ai_summary
    target.report_ai_analysis = source.report_ai_analysis
    target.original_page_screenshots = source.original_page_screenshots
    target.compare_page_screenshots = source.compare_page_screenshots
    target.diffs = _merge_review_state(target.diffs, source.diffs)
    target.errors = source.errors


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
        AnalysisStage,
        ClauseDiffStage,
        EvidenceStage,
        ExtractionStage,
        MatchStage,
        PreClauseDiffStage,
        SplitStage,
        SummaryStage,
        VisualizationStage,
    )

    return [
        ExtractionStage(),
        PreClauseDiffStage(),
        SplitStage(),
        MatchStage(),
        ClauseDiffStage(),
        EvidenceStage(),
        AnalysisStage(),
        VisualizationStage(),
        SummaryStage(),
    ]
