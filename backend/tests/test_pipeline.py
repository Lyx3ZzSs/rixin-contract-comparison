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
    ClauseDiffStage,
    MatchStage,
    PreClauseDiffStage,
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


def make_table_document(html: str, text: str = "") -> Document:
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
                        block_id="p1_t1",
                        page_no=1,
                        text=text or html,
                        raw_html=html,
                        bbox=BBox(x0=50, y0=100, x1=540, y1=500),
                        block_type="table",
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
    settings.reports_dir = settings.storage_dir / "reports"
    settings.ocr_dir = settings.storage_dir / "ocr"
    settings.debug_dir = settings.storage_dir / "debug"
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

    def test_keeps_punctuation_only_diffs(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.pairs = [
            ClausePair(
                original=make_clause("O001", "1", "甲方应付款。"),
                compare=make_clause("N001", "1", "甲方应付款，"),
                score=0.9,
                match_method="exact_number",
            )
        ]

        ClauseDiffStage().execute(ctx)

        assert len(ctx.clause_diffs) == 1
        assert ctx.diffs == ctx.clause_diffs

    def test_keeps_non_punctuation_changes_with_punctuation_changes(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.pairs = [
            ClausePair(
                original=make_clause("O001", "1", "甲方应在30日内付款。"),
                compare=make_clause("N001", "1", "甲方应在45日内付款，"),
                score=0.9,
                match_method="exact_number",
            )
        ]

        ClauseDiffStage().execute(ctx)

        assert len(ctx.diffs) == 1
        diff = ctx.diffs[0]
        assert "30" in diff.original_snippet
        assert "45" in diff.compare_snippet
        assert "。" in diff.original_snippet
        assert "，" in diff.compare_snippet


class TestClauseDiffStageMerge:
    def test_merges_metadata_table_and_clause_diffs(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        header_diff = DiffItem(diff_id="D001", diff_type="MODIFY", title="header", source_type="header_footer")
        meta_diff = DiffItem(diff_id="D002", diff_type="MODIFY", title="meta")
        table_diff = DiffItem(diff_id="D003", diff_type="ADD", title="table")
        ctx.header_footer_diffs = [header_diff]
        ctx.metadata_diffs = [meta_diff]
        ctx.table_diffs = [table_diff]
        ctx.pairs = []

        stage = ClauseDiffStage()
        stage.execute(ctx)

        assert ctx.diffs == [header_diff, meta_diff, table_diff]


class TestPreClauseDiffStage:
    def test_extracts_metadata_and_table_diffs(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        original_html = (
            "<table><tr><td>甲方</td><td>江苏东大</td></tr>"
            "<tr><td>签订日期</td><td>2026年4月 日</td></tr></table>"
        )
        compare_html = (
            "<table><tr><td>甲方</td><td>江苏东大</td></tr>"
            "<tr><td>签订日期</td><td>2026年4月21日</td></tr></table>"
        )
        ctx.original_extraction = ExtractionResult(document=make_table_document(original_html), extractor_used="test")
        ctx.compare_extraction = ExtractionResult(document=make_table_document(compare_html), extractor_used="test")

        PreClauseDiffStage().execute(ctx)

        assert [diff.title for diff in ctx.metadata_diffs] == ["封面字段：签订日期"]

    def test_extracts_header_footer_diffs_before_metadata_and_table(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        original_html = "<table><tr><td>甲方</td><td>江苏东大</td></tr></table>"
        compare_html = "<table><tr><td>甲方</td><td>江苏新公司</td></tr></table>"
        original = make_table_document(original_html)
        compare = make_table_document(compare_html)
        original.pages[0].blocks.insert(
            0,
            TextBlock(
                block_id="o_header",
                page_no=1,
                text="合同编号：A-001",
                bbox=BBox(x0=40, y0=20, x1=180, y1=36),
                block_type="header",
            ),
        )
        compare.pages[0].blocks.insert(
            0,
            TextBlock(
                block_id="c_header",
                page_no=1,
                text="合同编号：B-002",
                bbox=BBox(x0=40, y0=20, x1=180, y1=36),
                block_type="header",
            ),
        )
        ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
        ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")

        PreClauseDiffStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.header_footer_diffs] == ["D001"]
        assert ctx.header_footer_diffs[0].source_type == "header_footer"
        assert all(diff.diff_id != "D001" for diff in [*ctx.metadata_diffs, *ctx.table_diffs])

    def test_keeps_header_footer_diffs(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        original = make_document("正文条款一致。")
        compare = make_document("正文条款一致。")
        original.pages[0].blocks.insert(
            0,
            TextBlock(
                block_id="o_header",
                page_no=1,
                text="合同编号：A-001",
                bbox=BBox(x0=40, y0=20, x1=180, y1=36),
                block_type="header",
            ),
        )
        compare.pages[0].blocks.insert(
            0,
            TextBlock(
                block_id="c_header",
                page_no=1,
                text="合同编号：B-002",
                bbox=BBox(x0=40, y0=20, x1=180, y1=36),
                block_type="header",
            ),
        )
        ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
        ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")

        PreClauseDiffStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.header_footer_diffs] == ["D001"]
        assert ctx.header_footer_diffs[0].source_type == "header_footer"
        assert any(block.block_type == "header" for block in ctx.original_extraction.document.pages[0].blocks)
        assert any(block.block_type == "header" for block in ctx.compare_extraction.document.pages[0].blocks)

    def test_keeps_stamp_diffs(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        original = make_document("正文条款一致。")
        compare = make_document("正文条款一致。")
        original.pages[0].blocks.append(
            TextBlock(
                block_id="o_seal",
                page_no=1,
                text="原印章",
                bbox=BBox(x0=350, y0=600, x1=430, y1=680),
                block_type="seal",
            ),
        )
        compare.pages[0].blocks.append(
            TextBlock(
                block_id="c_seal",
                page_no=1,
                text="新印章",
                bbox=BBox(x0=350, y0=600, x1=430, y1=680),
                block_type="seal",
            ),
        )
        ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
        ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")

        PreClauseDiffStage().execute(ctx)

        assert len(ctx.seal_diffs) == 1
        assert ctx.seal_diffs[0].source_type == "seal"
        assert any(block.block_type == "seal" for block in ctx.original_extraction.document.pages[0].blocks)
        assert any(block.block_type == "seal" for block in ctx.compare_extraction.document.pages[0].blocks)


class TestSummaryStage:
    def test_refreshes_diff_count_and_writes_debug_artifact(self, tmp_path: Path) -> None:
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

        assert ctx.task.diffs == ctx.diffs
        assert ctx.task.diff_count == 1
        assert "diff_decisions" in ctx.task.debug_artifact_paths


class TestComparePipeline:
    def test_pipeline_runs_all_stages_in_order(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        execution_log: list[str] = []

        class FakeStage:
            def __init__(self, name: str, progress: int) -> None:
                self.name = name
                self.start_progress = progress
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
                self.start_progress = progress
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
            start_progress = 50
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
            start_progress = 50
            progress = 50

            def execute(self, ctx: PipelineContext) -> None:
                raise RuntimeError("extraction failed")

        pipeline = ComparePipeline(stages=[FailingStage()])
        with pytest.raises(RuntimeError, match="extraction failed"):
            pipeline.run(ctx)
