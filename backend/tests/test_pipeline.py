from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.errors import PipelineContractError
from app.infrastructure.task_repository import LocalJsonTaskRepository
from app.models import (
    BBox,
    Clause,
    ClausePair,
    CompareTask,
    DiffItem,
    Document,
    Page,
    TextBlock,
)
from app.services.extractors.base import ExtractionResult
from app.services.pipeline import ComparePipeline, PipelineContext
from app.services.pipeline_stages import (
    AnalysisStage,
    ClauseDiffStage,
    MatchStage,
    SplitStage,
    SummaryStage,
)


def make_document(text: str = "test clause text") -> Document:
    return Document(
        filename="test.pdf",
        path="test.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="p1_b1",
                        page_no=1,
                        text=text,
                        bbox=BBox(x0=10, y0=10, x1=100, y1=30),
                    )
                ],
            )
        ],
    )


def make_clause(
    clause_id: str = "O001",
    clause_no: str = "1",
    text: str = "第一条 甲方应在30日内付款。",
) -> Clause:
    return Clause(
        clause_id=clause_id,
        clause_no=clause_no,
        title=clause_no,
        text=text,
        normalized_text=text,
        page_numbers=[1],
        bboxes=[],
    )


def make_ctx(tmp_path: Path) -> PipelineContext:
    configure_storage(tmp_path)
    task = CompareTask(
        task_id="TTEST_PIPELINE",
        original_pdf_path=str(tmp_path / "original.pdf"),
        compare_pdf_path=str(tmp_path / "compare.pdf"),
    )
    return PipelineContext(
        task=task,
        original_pdf=tmp_path / "original.pdf",
        compare_pdf=tmp_path / "compare.pdf",
    )


def configure_storage(tmp_path: Path) -> None:
    settings.storage_dir = tmp_path / "storage"
    settings.uploads_dir = settings.storage_dir / "uploads"
    settings.tasks_dir = settings.storage_dir / "tasks"
    settings.highlighted_dir = settings.storage_dir / "highlighted"
    settings.screenshots_dir = settings.storage_dir / "screenshots"
    settings.reports_dir = settings.storage_dir / "reports"
    settings.ocr_dir = settings.storage_dir / "ocr"
    settings.debug_dir = settings.storage_dir / "debug"
    settings.task_jobs_dir = settings.storage_dir / "task_jobs"
    settings.ensure_storage()


class TestSplitStage:
    def test_requires_extraction_results(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)

        with pytest.raises(PipelineContractError, match="requires document extraction"):
            SplitStage().execute(ctx)

    def test_splits_documents_into_clauses(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.original_extraction = ExtractionResult(
            document=make_document("第一条 甲方付款。\n第二条 乙方交货。"),
            extractor_used="test",
        )
        ctx.compare_extraction = ExtractionResult(
            document=make_document("第一条 甲方付款。\n第二条 乙方交货。"),
            extractor_used="test",
        )

        stage = SplitStage()
        stage.execute(ctx)

        assert len(ctx.original_clauses) > 0
        assert len(ctx.compare_clauses) > 0


class TestMatchStage:
    def test_matches_clauses_by_number(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.original_clauses = [
            make_clause("O001", "1", "第一条 付款条款。"),
            make_clause("O002", "2", "第二条 交付条款。"),
        ]
        ctx.compare_clauses = [
            make_clause("N001", "1", "第一条 付款条款。"),
            make_clause("N002", "2", "第二条 交付条款。"),
        ]

        stage = MatchStage()
        stage.execute(ctx)

        assert len(ctx.pairs) >= 2
        matched_methods = [p.match_method for p in ctx.pairs]
        assert any("clause_no" in m for m in matched_methods)


class TestClauseDiffStage:
    def test_builds_diffs_from_pairs(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.metadata_diffs = []
        ctx.table_diffs = []
        clause = make_clause("O001", "1", "付款30天")
        ctx.pairs = [
            ClausePair(
                original=clause,
                compare=make_clause("N001", "1", "付款45天"),
                score=0.9,
                match_method="exact_number",
            )
        ]

        stage = ClauseDiffStage()
        stage.execute(ctx)

        assert len(ctx.clause_diffs) == 1
        assert ctx.diffs == ctx.clause_diffs
        assert ctx.diffs[0].diff_type == "MODIFY"


class TestClauseDiffStageMerge:
    def test_merges_metadata_table_and_clause_diffs(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        meta_diff = DiffItem(diff_id="D001", diff_type="MODIFY", title="meta")
        table_diff = DiffItem(diff_id="D002", diff_type="ADD", title="table")
        ctx.metadata_diffs = [meta_diff]
        ctx.table_diffs = [table_diff]
        ctx.pairs = []

        stage = ClauseDiffStage()
        stage.execute(ctx)

        assert ctx.diffs == [meta_diff, table_diff]


class TestAnalysisStage:
    def test_assigns_risk_levels(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.diffs = [
            DiffItem(
                diff_id="D001",
                diff_type="MODIFY",
                title="付款条款变更",
                original_text="付款30天",
                compare_text="付款45天",
            ),
        ]

        stage = AnalysisStage()
        stage.execute(ctx)

        assert ctx.diffs[0].ai_analysis is not None
        assert ctx.task.diffs == ctx.diffs


class TestSummaryStage:
    def test_generates_summary_text(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.diffs = [
            DiffItem(
                diff_id="D001",
                diff_type="MODIFY",
                title="test",
                original_text="a",
                compare_text="b",
            ),
        ]
        ctx.task.diffs = ctx.diffs

        stage = SummaryStage()
        stage.execute(ctx)

        assert "1 处差异" in ctx.task.ai_summary
        assert ctx.task.diff_count == 1


class TestComparePipeline:
    def test_pipeline_runs_all_stages_in_order(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        execution_log: list[str] = []

        class FakeStage:
            def __init__(self, name: str, progress: int) -> None:
                self.name = name
                self.progress = progress

            def execute(self, ctx: PipelineContext) -> None:
                execution_log.append(self.name)

        pipeline = ComparePipeline(stages=[
            FakeStage("stage_a", 30),
            FakeStage("stage_b", 60),
            FakeStage("stage_c", 90),
        ])
        result = pipeline.run(ctx)

        assert result.status == "COMPLETED"
        assert result.progress_percent == 100
        assert execution_log == ["stage_a", "stage_b", "stage_c"]

    def test_pipeline_sets_progress_for_each_stage(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        progress_values: list[int] = []

        class TrackingStage:
            def __init__(self, name: str, progress: int) -> None:
                self.name = name
                self.progress = progress

            def execute(self, ctx: PipelineContext) -> None:
                progress_values.append(ctx.task.progress_percent)

        pipeline = ComparePipeline(stages=[
            TrackingStage("a", 30),
            TrackingStage("b", 60),
            TrackingStage("c", 90),
        ])
        pipeline.run(ctx)

        assert progress_values == [30, 60, 90]

    def test_pipeline_completion_preserves_existing_review_state(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        repository = LocalJsonTaskRepository(settings)
        repository.save_compare_task(
            CompareTask(
                task_id=ctx.task.task_id,
                status="COMPLETED",
                diffs=[
                    DiffItem(
                        diff_id="D001",
                        diff_type="MODIFY",
                        review_status="CONFIRMED",
                        review_comment="已确认",
                        reviewed_by="legal",
                        reviewed_at="2026-05-21T10:00:00+00:00",
                    )
                ],
            )
        )
        ctx.task.diffs = [DiffItem(diff_id="D001", diff_type="MODIFY", review_status="UNREVIEWED")]

        class NoOpStage:
            name = "noop"
            progress = 50

            def execute(self, ctx: PipelineContext) -> None:
                return None

        result = ComparePipeline(stages=[NoOpStage()], repository=repository).run(ctx)

        assert result.diffs[0].review_status == "CONFIRMED"
        assert result.diffs[0].review_comment == "已确认"


class TestPipelineStageFailure:
    def test_pipeline_error_propagates(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)

        class FailingStage:
            name = "failing"
            progress = 50

            def execute(self, ctx: PipelineContext) -> None:
                raise RuntimeError("extraction failed")

        pipeline = ComparePipeline(stages=[FailingStage()])
        with pytest.raises(RuntimeError, match="extraction failed"):
            pipeline.run(ctx)
