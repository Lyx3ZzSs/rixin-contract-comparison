from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.models import (
    Clause,
    ClausePair,
    CompareTask,
    DiffItem,
)
from app.services.extractors.base import ExtractionResult
from app.utils.json_utils import save_task

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


def _update_progress(ctx: PipelineContext, stage: str, progress: int) -> None:
    ctx.task.stage = stage
    ctx.task.progress_percent = max(ctx.task.progress_percent, min(progress, 99))
    ctx.task.updated_at = datetime.now(UTC).isoformat()
    save_task(ctx.task)


class ComparePipeline:
    """Orchestrates comparison stages sequentially."""

    def __init__(self, stages: list[PipelineStage] | None = None) -> None:
        self.stages = stages or _default_stages()

    def run(self, ctx: PipelineContext) -> CompareTask:
        for stage in self.stages:
            _update_progress(ctx, stage.name, stage.progress)
            stage.execute(ctx)
        ctx.task.status = "COMPLETED"
        ctx.task.stage = "已完成"
        ctx.task.progress_percent = 100
        ctx.task.updated_at = datetime.now(UTC).isoformat()
        save_task(ctx.task)
        return ctx.task


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
