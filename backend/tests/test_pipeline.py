from __future__ import annotations

import json
import itertools
import threading
from pathlib import Path

import fitz
import pytest

from app.config import Settings, settings
from app.errors import PipelineContractError, TaskCancelled
from app.infrastructure.artifact_store import LocalArtifactStore
from app.infrastructure.execution_state import (
    CancellationToken,
    ExecutionStateCoordinator,
    TaskExecutionContext,
)
from app.infrastructure.task_repository import LocalJsonTaskRepository
from app.infrastructure.task_runner import LocalJsonTaskJobRepository, TaskJob
from app.models import (
    AuditItemReview,
    BBox,
    Clause,
    ClausePair,
    CompareTask,
    CompareOptions,
    DiffItem,
    Document,
    EvidenceBox,
    LayoutQualityReport,
    NormalizedBBox,
    OcrRemediationAction,
    Page,
    PageLayoutQualityReport,
    PageOcrQualityProfile,
    TaskOcrQualitySummary,
    TaskOcrRemediationSummary,
    TextBlock,
)
from app.services.extractors.base import DocumentExtractor, ExtractionResult
from app.services.compare_service import CompareService
from app.services.diff_quality import DiffQualityResult
from app.services.document_profiler import DocumentProfiler
from app.services.native_heading_repair import NativeHeadingRepairResult
from app.services.pipeline import ComparePipeline, PipelineContext, _copy_processing_result
from app.services.pipeline_stages import (
    ClauseDiffStage,
    DiffQualityStage,
    ExtractionStage,
    MatchStage,
    ModelRoutingStage,
    OcrQualityStage,
    OcrRemediationStage,
    PreClauseDiffStage,
    SplitStage,
    SummaryStage,
)
from app.services.progress_bus import ProgressBus
from app.services.repeated_overlay_filter import RepeatedOverlayFilterResult
from app.services.review_service import CompareReviewService


def test_document_extraction_result_and_compare_pipeline_remain_importable() -> None:
    assert ExtractionResult is not None
    assert DocumentExtractor is not None
    assert ComparePipeline is not None


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


def _write_text_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 96), text, fontname="china-s")
    doc.save(path)
    doc.close()


class _SequentialExtractor:
    name = "ppstructure_ocr_hybrid"

    def __init__(self, results: list[ExtractionResult]) -> None:
        self.results = results

    def extract(self, path: str | Path, task_id: str | None = None) -> ExtractionResult:
        result = self.results.pop(0)
        result.document.path = str(path)
        result.document.filename = Path(path).name
        return result


def _attach_profile(document: Document, extractor_used: str = "ppstructure_ocr_hybrid") -> Document:
    profile = DocumentProfiler().profile(document, extractor_used)
    document.profile = profile
    return document


class _StubNativeHeadingRepairService:
    def __init__(self, results: list[NativeHeadingRepairResult]) -> None:
        self.results = results

    def repair(self, document: Document) -> NativeHeadingRepairResult:
        return self.results.pop(0)


class _StubRepeatedOverlayFilter:
    def __init__(self, results: list[RepeatedOverlayFilterResult] | None = None) -> None:
        self.results = results or [RepeatedOverlayFilterResult(), RepeatedOverlayFilterResult()]

    def apply(self, document: Document) -> RepeatedOverlayFilterResult:
        return self.results.pop(0)


class _StubFooterVisualComparator:
    def __init__(self, diffs: list[DiffItem]) -> None:
        self.diffs = diffs

    def build_diffs(self, _original: Document, _compare: Document, *, start_index: int) -> list[DiffItem]:
        assert start_index == 1
        return self.diffs

    def remove_overlapping_ocr_diffs(
        self,
        ocr_diffs: list[DiffItem],
        _visual_diffs: list[DiffItem],
    ) -> list[DiffItem]:
        return ocr_diffs


def test_extraction_stage_repairs_native_heading_refreshes_preattached_profiles_and_writes_normalizer_debug(
    tmp_path: Path,
) -> None:
    ctx = make_ctx(tmp_path)
    _write_text_pdf(ctx.original_pdf, "8. 知识产权\n8.1 甲方拥有工作成果。")
    _write_text_pdf(ctx.compare_pdf, "9. 违约责任\n9.1 乙方承担违约责任。")
    original = Document(
        filename="original.pdf",
        path=str(ctx.original_pdf),
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="o8",
                        page_no=1,
                        text="8.",
                        bbox=BBox(x0=70, y0=82, x1=92, y1=102),
                        block_type="paragraph_title",
                    ),
                    TextBlock(
                        block_id="o81",
                        page_no=1,
                        text="8.1 甲方拥有工作成果。",
                        bbox=BBox(x0=72, y0=116, x1=360, y1=138),
                    ),
                ],
            )
        ],
    )
    stale_original_chars = sum(len(block.text.strip()) for block in original.pages[0].blocks)
    compare = Document(
        filename="compare.pdf",
        path=str(ctx.compare_pdf),
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="n8",
                        page_no=1,
                        text="9.",
                        bbox=BBox(x0=70, y0=82, x1=92, y1=102),
                        block_type="paragraph_title",
                    ),
                    TextBlock(
                        block_id="n81",
                        page_no=1,
                        text="9.1 乙方承担违约责任。",
                        bbox=BBox(x0=72, y0=116, x1=360, y1=138),
                    ),
                ],
            )
        ],
    )
    stale_compare_chars = sum(len(block.text.strip()) for block in compare.pages[0].blocks)
    original = _attach_profile(original)
    compare = _attach_profile(compare)
    extractor = _SequentialExtractor(
        [
            ExtractionResult(
                document=original,
                extractor_used="ppstructure_ocr_hybrid",
                profile=original.profile,
            ),
            ExtractionResult(
                document=compare,
                extractor_used="ppstructure_ocr_hybrid",
                profile=compare.profile,
            ),
        ]
    )

    ExtractionStage(extractor=extractor, artifact_store=LocalArtifactStore(settings)).execute(ctx)

    assert ctx.original_extraction is not None
    assert ctx.compare_extraction is not None
    assert ctx.original_extraction.document.pages[0].blocks[0].text == "8. 知识产权"
    assert ctx.compare_extraction.document.pages[0].blocks[0].text == "9. 违约责任"
    assert ctx.original_extraction.profile is not None
    assert ctx.compare_extraction.profile is not None
    repaired_original_chars = sum(len(block.text.strip()) for block in ctx.original_extraction.document.pages[0].blocks)
    repaired_compare_chars = sum(len(block.text.strip()) for block in ctx.compare_extraction.document.pages[0].blocks)
    assert repaired_original_chars > stale_original_chars
    assert repaired_compare_chars > stale_compare_chars
    assert ctx.original_extraction.profile.total_text_chars == repaired_original_chars
    assert ctx.compare_extraction.profile.total_text_chars == repaired_compare_chars
    assert ctx.original_extraction.document.profile is not None
    assert ctx.compare_extraction.document.profile is not None
    assert ctx.original_extraction.document.profile.total_text_chars == repaired_original_chars
    assert ctx.compare_extraction.document.profile.total_text_chars == repaired_compare_chars
    assert Path(ctx.task.debug_artifact_paths["native_heading_repair"]).exists()
    assert Path(ctx.task.debug_artifact_paths["repeated_overlay_filter"]).exists()
    assert ctx.task.metrics["native_heading_repair"]["original"] == 1
    assert ctx.task.metrics["native_heading_repair"]["compare"] == 1
    assert ctx.task.metrics["repeated_overlay_filter"]["compare"] == 0


def test_extraction_stage_propagates_native_heading_warning_to_task_parse_warnings(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    _write_text_pdf(ctx.original_pdf, "第一条 付款")
    _write_text_pdf(ctx.compare_pdf, "第一条 付款")
    original = Document(
        filename="original.pdf",
        path=str(ctx.original_pdf),
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="o1",
                        page_no=1,
                        text="第一条 付款",
                        bbox=BBox(x0=70, y0=82, x1=180, y1=102),
                    )
                ],
            )
        ],
    )
    compare = Document(
        filename="compare.pdf",
        path=str(ctx.compare_pdf),
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="n1",
                        page_no=1,
                        text="第一条 付款",
                        bbox=BBox(x0=70, y0=82, x1=180, y1=102),
                    )
                ],
            )
        ],
    )
    extractor = _SequentialExtractor(
        [
            ExtractionResult(document=original, extractor_used="ppstructure_ocr_hybrid"),
            ExtractionResult(document=compare, extractor_used="ppstructure_ocr_hybrid"),
        ]
    )
    stage = ExtractionStage(
        extractor=extractor,
        artifact_store=LocalArtifactStore(settings),
        native_heading_repair=_StubNativeHeadingRepairService(
            [
                NativeHeadingRepairResult(warnings=["native heading repair warning"]),
                NativeHeadingRepairResult(),
            ]
        ),
        repeated_overlay_filter=_StubRepeatedOverlayFilter(),
    )

    stage.execute(ctx)

    assert ctx.original_extraction is not None
    assert "native heading repair warning" in ctx.original_extraction.warnings
    assert "native heading repair warning" in ctx.task.parse_warnings
    assert any(
        item.code == "PARSE_WARNING"
        and item.message == "native heading repair warning"
        and item.source == "original_extractor"
        for item in ctx.task.parse_warning_details
    )


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

    def test_suppresses_layout_reflow_punctuation_equivalent_diff(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.pairs = [
            ClausePair(
                original=make_clause(
                    "O001",
                    "4",
                    "4我方提供6%增值税专用发票,需方支付全部\n款项.",
                ),
                compare=make_clause(
                    "N001",
                    "4",
                    "4我方提供6%增值税专用发票,需方支\n付全部款项。",
                ),
                score=100.0,
                match_method="same_clause_no_weighted",
            )
        ]

        ClauseDiffStage().execute(ctx)

        assert ctx.clause_diffs == []
        assert ctx.diffs == []

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
            "<table><tr><td>甲方</td><td>江苏东大</td></tr><tr><td>签订日期</td><td>2026年4月 日</td></tr></table>"
        )
        compare_html = (
            "<table><tr><td>甲方</td><td>江苏东大</td></tr><tr><td>签订日期</td><td>2026年4月21日</td></tr></table>"
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

    def test_includes_registered_visual_footer_diffs(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.original_extraction = ExtractionResult(document=make_document("正文一致。"), extractor_used="test")
        ctx.compare_extraction = ExtractionResult(document=make_document("正文一致。"), extractor_used="test")
        visual_diff = DiffItem(
            diff_id="D001",
            diff_type="ADD",
            title="第1页左下角手写签注",
            compare_text="检测到左下角手写签注",
            compare_snippet="检测到左下角手写签注",
            source_type="header_footer",
            review_flags=["VISUAL_FOOTER_ANNOTATION"],
        )
        stage = PreClauseDiffStage()
        stage.footer_visual = _StubFooterVisualComparator([visual_diff])

        stage.execute(ctx)

        assert ctx.header_footer_diffs == [visual_diff]

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

    def test_filters_header_footer_diffs_when_option_enabled(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.task.compare_options = CompareOptions(ignore_headers_footers=True)
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

        assert ctx.header_footer_diffs == []

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

    def test_filters_stamp_diffs_when_option_enabled(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.task.compare_options = CompareOptions(ignore_stamps=True)
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

        assert ctx.seal_diffs == []

    def test_writes_table_repair_debug_artifact(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        original = make_table_document(
            "<table><tr><td>序号</td><td>产品名称</td><td>金额</td></tr>"
            "<tr><td>1</td><td>服务器</td><td>100</td></tr></table>"
        )
        compare = make_table_document(
            "<table><tr><td>序号</td><td>产品名称</td><td>金额</td></tr>"
            "<tr><td>1</td><td>服务器</td><td>200</td></tr></table>"
        )
        ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
        ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")

        PreClauseDiffStage().execute(ctx)

        assert "table_repair" in ctx.task.debug_artifact_paths
        path = Path(ctx.task.debug_artifact_paths["table_repair"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["tier"] == "cell_level"
        assert payload["quality"]["min"] >= 0.75
        assert payload["quality"]["details"]["original"]["grid_score"] > 0
        assert payload["quality"]["details"]["original"]["bbox_score"] > 0
        assert payload["quality"]["details"]["original"]["text_score"] > 0
        assert payload["quality"]["details"]["original"]["continuation_score"] > 0
        assert payload["quality"]["details"]["original"]["business_score"] > 0
        assert payload["table_block_counts"] == {"original": 1, "compare": 1}
        assert payload["parsed_table_counts"] == {"original": 1, "compare": 1}
        assert payload["logical_table_counts"] == {"original": 1, "compare": 1}
        assert payload["quality_metrics"]["original"]["table_count"] == 1
        assert payload["quality_metrics"]["compare"]["row_count"] == 1
        assert "source_text_token_coverage" in payload["quality_metrics"]["compare"]
        assert payload["shape_changes"]["compare"]["row_count_delta"] == -1
        assert payload["shape_changes"]["compare_vs_original_logical"]["row_count_delta"] == 0
        assert payload["suppressed_diffs"]["suppressed_diff_count"] == 0
        assert payload["suppressed_diffs"]["suppressed_diff_counts_by_reason"] == {}
        assert payload["repair"]["original"]["decision_count"] == 0
        assert payload["repair"]["compare"]["decision_count"] == 0
        assert payload["tables"]["compare"][0]["bbox_coverage"] == 0.0

    def test_records_table_repair_decision_debug_artifact(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        original = make_table_document(
            "<table>"
            "<tr><td>序号</td><td>产品名称</td><td>详细配置</td><td>品牌</td>"
            "<td>单位</td><td>数量</td><td>单价</td><td>金额</td><td>备注</td></tr>"
            "<tr><td>2</td><td>中期模型</td><td>配置A</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>100</td><td>100</td><td></td></tr>"
            "<tr><td>3</td><td>短期模型</td><td>配置B</td><td>国能日新</td>"
            "<td>套</td><td>1</td><td>200</td><td>200</td><td></td></tr>"
            "</table>"
        )
        compare = make_table_document(
            "<table>"
            "<tr><td>序号</td><td>产品名称</td><td>详细配置</td><td>品牌</td>"
            "<td>单位</td><td>数量</td><td>单价</td><td>金额</td><td>备注</td></tr>"
            "<tr><td>2 3</td><td>中期模型 短期模型</td><td>配置A 配置B</td>"
            "<td>国能日新 国能日新</td><td>套 套</td><td>1 1</td>"
            "<td>100 200</td><td>100 200</td><td></td></tr>"
            "</table>"
        )
        ctx.original_extraction = ExtractionResult(document=original, extractor_used="test")
        ctx.compare_extraction = ExtractionResult(document=compare, extractor_used="test")

        PreClauseDiffStage().execute(ctx)

        payload = json.loads(Path(ctx.task.debug_artifact_paths["table_repair"]).read_text(encoding="utf-8"))
        decisions = payload["repair"]["compare"]["decisions"]
        decision = next(item for item in decisions if item["repair_type"] == "merged_sequence_split")
        assert decision["source_block_id"] == "p1_t1"
        assert decision["row_index"] == 0
        assert decision["before_metrics"]["row_count"] == 1
        assert decision["after_metrics"]["row_count"] == 2
        assert decision["signals"]["sequences"] == ["2", "3"]
        assert decision["confidence"] > 0
        assert decision["reason"]


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

    def test_filters_header_footer_diffs_when_option_enabled(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.task.compare_options = CompareOptions(ignore_headers_footers=True)
        header_diff = DiffItem(
            diff_id="D001",
            diff_type="MODIFY",
            title="页眉",
            source_type="header_footer",
            original_text="合同编号：A-001",
            compare_text="合同编号：B-002",
        )
        clause_diff = DiffItem(
            diff_id="D002",
            diff_type="MODIFY",
            title="正文",
            source_type="clause",
            original_text="30日",
            compare_text="45日",
        )
        ctx.diffs = [header_diff, clause_diff]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D002"]
        assert ctx.task.diff_count == 1

    def test_ignore_stamps_hides_signing_region_diffs_in_summary(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.task.compare_options = CompareOptions(ignore_stamps=True)
        signing_region_diff = DiffItem(
            diff_id="D001",
            diff_type="MODIFY",
            source_type="signing_region",
        )
        seal_diff = DiffItem(
            diff_id="D002",
            diff_type="MODIFY",
            source_type="seal",
        )
        clause_diff = DiffItem(
            diff_id="D003",
            diff_type="MODIFY",
            source_type="clause",
        )
        ctx.diffs = [signing_region_diff, seal_diff, clause_diff]

        SummaryStage().execute(ctx)

        assert ctx.task.diffs == [clause_diff]
        assert ctx.task.diff_count == 1

    def test_same_id_final_dedupe_merges_evidence_flags_sources_and_review_projection(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        first = DiffItem(
            diff_id="D001",
            diff_type="DELETE",
            title="表格字段：单位名称",
            original_text="国能日新科技股份有限公司",
            source_type="table",
            original_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=0, y0=0, x1=100, y1=20), text="单位名称")],
            structural_flags=["TABLE_STRUCTURE"],
            review_flags=["FIRST_FLAG"],
            merged_sources=["metadata"],
        )
        reviewed = first.model_copy(
            deep=True,
            update={
                "source_type": "metadata",
                "original_evidence": [EvidenceBox(page_no=2, bbox=BBox(x0=0, y0=0, x1=100, y1=20), text="公司名称")],
                "structural_flags": ["OCR_STRUCTURE"],
                "review_flags": ["SECOND_FLAG"],
                "merged_sources": ["header_footer"],
                "review_status": "CONFIRMED",
                "review_comment": "已核验",
                "reviewed_by": "reviewer",
                "reviewed_at": "2026-07-17T00:00:00+00:00",
            },
        )
        ctx.diffs = [
            first,
            reviewed,
            DiffItem(
                diff_id="D002",
                diff_type="DELETE",
                title="表格字段：单位名称",
                original_text="国能日新科技股份有限公司",
                source_type="table",
            ),
        ]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D001", "D002"]
        survivor = ctx.task.diffs[0]
        assert [item.page_no for item in survivor.original_evidence] == [1, 2]
        assert survivor.structural_flags == ["OCR_STRUCTURE", "TABLE_STRUCTURE"]
        assert survivor.review_flags == ["FIRST_FLAG", "SECOND_FLAG"]
        assert survivor.merged_sources == ["header_footer", "metadata", "table"]
        assert survivor.review_status == "CONFIRMED"
        assert survivor.review_comment == "已核验"
        assert survivor.reviewed_by == "reviewer"
        assert survivor.reviewed_at == "2026-07-17T00:00:00+00:00"
        assert ctx.task.diff_count == 2

    @pytest.mark.parametrize(
        ("second_update", "case_name"),
        [
            ({"source_type": "metadata"}, "source"),
            ({"section_type": "appendix"}, "section_type"),
            ({"diff_type": "DELETE"}, "diff_type"),
            ({"original_text": "付款20日"}, "original_text"),
            ({"compare_text": "付款60日"}, "compare_text"),
        ],
    )
    def test_different_id_final_dedupe_requires_all_semantic_fields_to_match(
        self,
        tmp_path: Path,
        second_update: dict[str, str],
        case_name: str,
    ) -> None:
        ctx = make_ctx(tmp_path)
        first = self._located_diff("D010")
        second = self._located_diff("D020").model_copy(update=second_update)
        ctx.diffs = [first, second]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D010", "D020"], case_name

    @pytest.mark.parametrize(
        "second_update",
        [
            {"original_clause_id": "O002", "compare_clause_id": "C002", "section_path": ["其他", "条款"]},
            {"original_clause_id": None, "compare_clause_id": None, "section_path": ["其他", "期限"]},
        ],
    )
    def test_different_id_final_dedupe_keeps_cross_clause_or_stable_path_diffs(
        self,
        tmp_path: Path,
        second_update: dict[str, object],
    ) -> None:
        ctx = make_ctx(tmp_path)
        first = self._located_diff("D010")
        if second_update["original_clause_id"] is None:
            first = first.model_copy(
                update={"original_clause_id": None, "compare_clause_id": None, "section_path": ["付款", "方式"]}
            )
        ctx.diffs = [first, self._located_diff("D020").model_copy(update=second_update)]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D010", "D020"]

    def test_different_id_final_dedupe_keeps_same_text_on_different_pages(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.diffs = [self._located_diff("D010", page_no=1), self._located_diff("D020", page_no=2)]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D010", "D020"]

    def test_different_id_final_dedupe_never_text_merges_unlocated_diffs(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.diffs = [
            self._located_diff("D010").model_copy(update={"original_evidence": [], "compare_evidence": []}),
            self._located_diff("D020").model_copy(update={"original_evidence": [], "compare_evidence": []}),
        ]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D010", "D020"]

    @pytest.mark.parametrize(("shift", "expected_ids"), [(21, ["D010", "D020"]), (20, ["D010"])])
    def test_final_dedupe_uses_inclusive_evidence_coverage_threshold(
        self,
        tmp_path: Path,
        shift: int,
        expected_ids: list[str],
    ) -> None:
        ctx = make_ctx(tmp_path)
        first = self._located_diff("D010", compare_evidence=False)
        second = self._located_diff("D020", x0=shift, compare_evidence=False)
        ctx.diffs = [first, second]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == expected_ids

    def test_final_dedupe_requires_both_located_sides_to_meet_coverage_threshold(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        first = self._located_diff("D010")
        second = self._located_diff("D020", original_x0=20, compare_x0=21)
        ctx.diffs = [first, second]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D010", "D020"]

    def test_final_dedupe_allows_one_located_side_when_other_side_is_unlocated_for_both(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.diffs = [
            self._located_diff("D010", compare_evidence=False),
            self._located_diff("D020", original_x0=20, compare_evidence=False),
        ]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D010"]

    def test_final_dedupe_rejects_a_side_located_for_only_one_candidate(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.diffs = [
            self._located_diff("D010", compare_evidence=False),
            self._located_diff("D020", original_x0=20),
        ]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D010", "D020"]

    def test_final_dedupe_does_not_transitively_bridge_non_overlapping_members(self, tmp_path: Path) -> None:
        def run(order: list[DiffItem], suffix: str) -> list[dict[str, object]]:
            ctx = make_ctx(tmp_path / suffix)
            ctx.diffs = [item.model_copy(deep=True) for item in order]
            SummaryStage().execute(ctx)
            return [item.model_dump(mode="json") for item in ctx.task.diffs]

        left = self._located_diff("D010", original_x0=0, compare_evidence=False)
        bridge = self._located_diff("D020", original_x0=20, compare_evidence=False)
        right = self._located_diff("D030", original_x0=40, compare_evidence=False)

        forward = run([left, bridge, right], "bridge-forward")
        reverse = run([right, bridge, left], "bridge-reverse")

        assert forward == reverse
        assert [item["diff_id"] for item in forward] == ["D010", "D030"]
        assert len(forward[0]["original_evidence"]) == 2
        assert len(forward[1]["original_evidence"]) == 1

    def test_final_dedupe_bridge_partition_is_independent_of_id_labels_and_input_order(self, tmp_path: Path) -> None:
        def partition(order: tuple[DiffItem, ...], suffix: str) -> list[tuple[float, ...]]:
            ctx = make_ctx(tmp_path / suffix)
            ctx.diffs = [item.model_copy(deep=True) for item in order]
            SummaryStage().execute(ctx)
            return sorted(
                tuple(sorted(evidence.bbox.x0 for evidence in diff.original_evidence)) for diff in ctx.task.diffs
            )

        spatially_labeled = [
            self._located_diff("D100", original_x0=0, compare_evidence=False),
            self._located_diff("D200", original_x0=20, compare_evidence=False),
            self._located_diff("D300", original_x0=40, compare_evidence=False),
        ]
        reverse_labeled = [
            self._located_diff("D300", original_x0=0, compare_evidence=False),
            self._located_diff("D200", original_x0=20, compare_evidence=False),
            self._located_diff("D100", original_x0=40, compare_evidence=False),
        ]

        expected = [(0, 20), (40,)]
        for index, order in enumerate(itertools.permutations(spatially_labeled)):
            assert partition(order, f"spatial-{index}") == expected
        for index, order in enumerate(itertools.permutations(reverse_labeled)):
            assert partition(order, f"reverse-{index}") == expected

    def test_final_dedupe_assigns_distinct_stable_ids_to_unrelated_empty_id_diffs(self, tmp_path: Path) -> None:
        def run(order: list[DiffItem], suffix: str) -> list[dict[str, object]]:
            ctx = make_ctx(tmp_path / suffix)
            ctx.diffs = [item.model_copy(deep=True) for item in order]
            SummaryStage().execute(ctx)
            return [item.model_dump(mode="json") for item in ctx.task.diffs]

        first = self._located_diff("").model_copy(update={"original_text": "付款30日", "compare_text": "付款45日"})
        second = self._located_diff("").model_copy(update={"original_text": "交付10日", "compare_text": "交付20日"})

        forward = run([first, second], "empty-distinct-forward")
        reverse = run([second, first], "empty-distinct-reverse")

        assert forward == reverse
        assert len(forward) == 2
        assert len({item["diff_id"] for item in forward}) == 2
        assert all(str(item["diff_id"]).startswith("AUTO-") for item in forward)

    def test_final_dedupe_avoids_collision_between_generated_and_original_auto_ids(self, tmp_path: Path) -> None:
        def run(order: list[DiffItem], suffix: str) -> list[dict[str, object]]:
            ctx = make_ctx(tmp_path / suffix)
            ctx.diffs = [item.model_copy(deep=True) for item in order]
            SummaryStage().execute(ctx)
            return [item.model_dump(mode="json") for item in ctx.task.diffs]

        empty = self._located_diff("").model_copy(update={"original_evidence": [], "compare_evidence": []})
        probe = run([empty], "auto-collision-probe")
        default_auto_id = str(probe[0]["diff_id"])
        legitimate = self._located_diff(default_auto_id).model_copy(
            update={
                "original_text": "完全不同的原文",
                "compare_text": "完全不同的修订文本",
                "section_path": ["其他条款"],
                "original_clause_id": "O999",
                "compare_clause_id": "C999",
                "original_evidence": [],
                "compare_evidence": [],
            }
        )

        forward = run([empty, empty.model_copy(deep=True), legitimate], "auto-collision-forward")
        reverse = run([legitimate, empty.model_copy(deep=True), empty], "auto-collision-reverse")

        assert forward == reverse
        assert len(forward) == 3
        assert len({str(item["diff_id"]) for item in forward}) == 3
        assert default_auto_id in {str(item["diff_id"]) for item in forward}
        legitimate_result = next(item for item in forward if item["diff_id"] == default_auto_id)
        assert legitimate_result["original_text"] == "完全不同的原文"

    def test_final_dedupe_merges_matching_empty_id_into_existing_nonempty_id(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.diffs = [
            self._located_diff("", original_x0=20, compare_x0=20),
            self._located_diff("D010"),
        ]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D010"]
        assert len(ctx.task.diffs[0].original_evidence) == 2

    def test_final_dedupe_empty_id_candidates_partition_stably(self, tmp_path: Path) -> None:
        def run(order: list[DiffItem], suffix: str) -> list[tuple[str, tuple[float, ...]]]:
            ctx = make_ctx(tmp_path / suffix)
            ctx.diffs = [item.model_copy(deep=True) for item in order]
            SummaryStage().execute(ctx)
            return sorted(
                (diff.diff_id, tuple(sorted(item.bbox.x0 for item in diff.original_evidence)))
                for diff in ctx.task.diffs
            )

        candidates = [
            self._located_diff("", original_x0=0, compare_evidence=False),
            self._located_diff("", original_x0=20, compare_evidence=False),
            self._located_diff("", original_x0=40, compare_evidence=False),
        ]

        forward = run(candidates, "empty-candidates-forward")
        reverse = run(list(reversed(candidates)), "empty-candidates-reverse")

        assert forward == reverse
        assert sorted(positions for _, positions in forward) == [(0, 20), (40,)]
        assert all(diff_id.startswith("AUTO-") for diff_id, _ in forward)

    def test_final_dedupe_keeps_identical_unlocated_empty_id_occurrences_distinct(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        empty = self._located_diff("").model_copy(update={"original_evidence": [], "compare_evidence": []})
        ctx.diffs = [empty, empty.model_copy(deep=True)]

        SummaryStage().execute(ctx)

        assert len(ctx.task.diffs) == 2
        assert len({diff.diff_id for diff in ctx.task.diffs}) == 2
        assert all(diff.diff_id.startswith("AUTO-") for diff in ctx.task.diffs)

    def test_final_dedupe_auto_id_is_stable_across_review_projection_changes(self, tmp_path: Path) -> None:
        def generated_id(diff: DiffItem, suffix: str) -> str:
            ctx = make_ctx(tmp_path / suffix)
            ctx.diffs = [diff]
            SummaryStage().execute(ctx)
            return ctx.task.diffs[0].diff_id

        empty = self._located_diff("")
        reviewed = empty.model_copy(
            deep=True,
            update={
                "review_status": "CONFIRMED",
                "review_comment": "已核验",
                "reviewed_by": "reviewer",
                "reviewed_at": "2026-07-17T00:00:00+00:00",
            },
        )

        assert generated_id(empty, "auto-unreviewed") == generated_id(reviewed, "auto-reviewed")

    @pytest.mark.parametrize("duplicate_id", ["D010", "D020"])
    def test_final_dedupe_preserves_rich_fields_independent_of_canonical_id_and_input_order(
        self,
        tmp_path: Path,
        duplicate_id: str,
    ) -> None:
        def run(order: list[DiffItem], suffix: str) -> dict[str, object]:
            ctx = make_ctx(tmp_path / suffix)
            ctx.diffs = [item.model_copy(deep=True) for item in order]
            SummaryStage().execute(ctx)
            assert len(ctx.task.diffs) == 1
            return ctx.task.diffs[0].model_dump(mode="json")

        sparse = self._located_diff("D010").model_copy(
            update={"title": "", "original_snippet": "", "compare_snippet": ""}
        )
        rich = self._located_diff(duplicate_id, original_x0=20, compare_x0=20).model_copy(
            update={
                "clause_no": "8.1",
                "title": "付款期限",
                "original_snippet": "原付款期限为30日",
                "compare_snippet": "现付款期限为45日",
                "readable_change": "付款期限由30日延长为45日",
                "match_score": 96.5,
                "match_method": "stable_path",
                "match_score_details": {"semantic": 0.96, "layout": {"page": 1}},
                "match_candidates": [{"clause_id": "C001", "score": 96.5}],
                "match_confidence": "HIGH",
                "text_confidence": 0.98,
            }
        )

        forward = run([sparse, rich], f"rich-{duplicate_id}-forward")
        reverse = run([rich, sparse], f"rich-{duplicate_id}-reverse")

        assert forward == reverse
        assert forward["title"] == "付款期限"
        assert forward["clause_no"] == "8.1"
        assert forward["original_snippet"] == "原付款期限为30日"
        assert forward["compare_snippet"] == "现付款期限为45日"
        assert forward["readable_change"] == "付款期限由30日延长为45日"
        assert forward["match_score"] == 96.5
        assert forward["match_method"] == "stable_path"
        assert forward["match_score_details"] == {"layout": {"page": 1}, "semantic": 0.96}
        assert forward["match_candidates"] == [{"clause_id": "C001", "score": 96.5}]
        assert forward["match_confidence"] == "HIGH"
        assert forward["text_confidence"] == 0.98

    def test_final_dedupe_marks_conflicting_rich_fields_for_review_deterministically(self, tmp_path: Path) -> None:
        def run(order: list[DiffItem], suffix: str) -> DiffItem:
            ctx = make_ctx(tmp_path / suffix)
            ctx.diffs = [item.model_copy(deep=True) for item in order]
            SummaryStage().execute(ctx)
            return ctx.task.diffs[0]

        first = self._located_diff("D010").model_copy(update={"title": "付款期限", "match_method": "method-a"})
        second = self._located_diff("D020", original_x0=20, compare_x0=20).model_copy(
            update={"title": "合同付款期限", "match_method": "method-b"}
        )

        forward = run([first, second], "conflict-forward")
        reverse = run([second, first], "conflict-reverse")

        assert forward == reverse
        assert forward.title == "合同付款期限"
        assert "FINAL_DEDUPE_FIELD_CONFLICT" in forward.review_flags
        assert forward.quality_status == "NEEDS_REVIEW"

    def test_final_dedupe_preserves_none_for_missing_optional_clause_ids(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        first = self._located_diff("D010").model_copy(update={"original_clause_id": None, "compare_clause_id": None})
        second = self._located_diff("D020", original_x0=20, compare_x0=20).model_copy(
            update={"original_clause_id": None, "compare_clause_id": None}
        )
        ctx.diffs = [first, second]

        SummaryStage().execute(ctx)

        assert len(ctx.task.diffs) == 1
        assert ctx.task.diffs[0].original_clause_id is None
        assert ctx.task.diffs[0].compare_clause_id is None

    def test_final_dedupe_treats_page_zero_as_unlocated(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.diffs = [
            self._located_diff("D010", page_no=0, compare_evidence=False),
            self._located_diff("D020", page_no=0, original_x0=20, compare_evidence=False),
        ]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D010", "D020"]

    def test_final_dedupe_falls_back_to_raw_bbox_when_normalized_bbox_is_invalid(self, tmp_path: Path) -> None:
        invalid_normalized = NormalizedBBox(x0=0, y0=0, x1=0, y1=0)
        ctx = make_ctx(tmp_path)
        first = self._located_diff("D010", compare_evidence=False)
        second = self._located_diff("D020", original_x0=20, compare_evidence=False)
        first.original_evidence[0].bbox.normalized = invalid_normalized
        second.original_evidence[0].bbox.normalized = invalid_normalized
        ctx.diffs = [first, second]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D010"]

    def test_final_dedupe_prefers_normalized_bbox_when_both_are_valid(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        first = self._located_diff("D010", compare_evidence=False)
        second = self._located_diff("D020", original_x0=30, compare_evidence=False)
        first.original_evidence[0].bbox.normalized = NormalizedBBox(x0=0, y0=0, x1=1, y1=1)
        second.original_evidence[0].bbox.normalized = NormalizedBBox(x0=0.2, y0=0, x1=1.2, y1=1)
        ctx.diffs = [first, second]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D010"]

    def test_final_dedupe_does_not_locate_zero_area_raw_or_normalized_bbox(self, tmp_path: Path) -> None:
        zero_bbox = BBox(
            x0=10,
            y0=10,
            x1=10,
            y1=10,
            normalized=NormalizedBBox(x0=20, y0=20, x1=20, y1=20),
        )
        ctx = make_ctx(tmp_path)
        first = self._located_diff("D010", compare_evidence=False)
        second = self._located_diff("D020", compare_evidence=False)
        first.original_evidence[0].bbox = zero_bbox
        second.original_evidence[0].bbox = zero_bbox.model_copy(deep=True)
        ctx.diffs = [first, second]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D010", "D020"]

    @pytest.mark.parametrize("invalid_coordinate", [float("nan"), float("inf")])
    @pytest.mark.parametrize("coordinate_space", ["raw", "normalized"])
    def test_final_dedupe_never_merges_nonfinite_evidence_coordinates(
        self,
        tmp_path: Path,
        invalid_coordinate: float,
        coordinate_space: str,
    ) -> None:
        ctx = make_ctx(tmp_path)
        first = self._located_diff("D010", compare_evidence=False)
        second = self._located_diff("D020", original_x0=20, compare_evidence=False)
        if coordinate_space == "raw":
            first.original_evidence[0].bbox.x1 = invalid_coordinate
            second.original_evidence[0].bbox.x1 = invalid_coordinate
            first.original_evidence[0].bbox.normalized = NormalizedBBox(x0=0, y0=0, x1=1, y1=1)
            second.original_evidence[0].bbox.normalized = NormalizedBBox(x0=0.2, y0=0, x1=1.2, y1=1)
        else:
            first.original_evidence[0].bbox.x1 = 0
            second.original_evidence[0].bbox.x1 = 20
            first.original_evidence[0].bbox.normalized = NormalizedBBox(
                x0=0,
                y0=0,
                x1=invalid_coordinate,
                y1=1,
            )
            second.original_evidence[0].bbox.normalized = NormalizedBBox(
                x0=0.2,
                y0=0,
                x1=invalid_coordinate,
                y1=1,
            )
        ctx.diffs = [first, second]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D010", "D020"]

    def test_final_dedupe_merges_colliding_remediation_actions_conservatively(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.task.ocr_remediation_summary = TaskOcrRemediationSummary(
            status="ACTIONS_PLANNED",
            attempted_action_count=2,
            successful_action_count=1,
            unresolved_action_count=1,
            risk_reduced_page_count=1,
            actions=[
                OcrRemediationAction(
                    action_id="original:1:D010:RELOCATE_EVIDENCE",
                    action_type="RELOCATE_EVIDENCE",
                    reason="LOW_TEXT_CONFIDENCE",
                    status="SUCCEEDED",
                    side="original",
                    page_no=1,
                    diff_id="D010",
                    before_quality={"score": 0.3},
                    after_quality={"score": 0.9, "source": "relocated"},
                    changed_evidence=True,
                    review_flags_added=["OCR_REMEDIATION_EVIDENCE_RELOCATED"],
                    notes=["relocated"],
                ),
                OcrRemediationAction(
                    action_id="original:1:D020:RELOCATE_EVIDENCE",
                    action_type="RELOCATE_EVIDENCE",
                    reason="EVIDENCE_UNRELIABLE",
                    status="FAILED",
                    side="original",
                    page_no=1,
                    diff_id="D020",
                    before_quality={"method": "block_fallback"},
                    after_quality={"error": "not-found"},
                    review_flags_added=["OCR_REMEDIATION_UNRESOLVED"],
                    notes=["not-found"],
                ),
            ],
        )
        ctx.diffs = [self._located_diff("D010"), self._located_diff("D020", original_x0=20, compare_x0=20)]

        SummaryStage().execute(ctx)

        summary = ctx.task.ocr_remediation_summary
        assert summary is not None
        assert len(summary.actions) == 1
        action = summary.actions[0]
        assert action.action_id == "original:1:D010:RELOCATE_EVIDENCE"
        assert action.diff_id == "D010"
        assert action.status == "FAILED"
        assert action.changed_evidence is False
        assert action.before_quality == {"method": "block_fallback", "score": 0.3}
        assert action.after_quality == {"error": "not-found", "score": 0.9, "source": "relocated"}
        assert action.review_flags_added == ["OCR_REMEDIATION_EVIDENCE_RELOCATED", "OCR_REMEDIATION_UNRESOLVED"]
        assert action.notes == ["not-found", "relocated"]
        assert summary.attempted_action_count == 1
        assert summary.successful_action_count == 0
        assert summary.unresolved_action_count == 1
        assert summary.risk_reduced_page_count == 0

    def test_ocr_remediation_summary_counts_unique_successful_positive_pages(self) -> None:
        summary = TaskOcrRemediationSummary(
            actions=[
                OcrRemediationAction(
                    action_id="original:1:D010:RELOCATE_EVIDENCE",
                    action_type="RELOCATE_EVIDENCE",
                    reason="done",
                    status="SUCCEEDED",
                    side="original",
                    page_no=1,
                    diff_id="D010",
                ),
                OcrRemediationAction(
                    action_id="original:1:D020:RELOCATE_EVIDENCE",
                    action_type="RELOCATE_EVIDENCE",
                    reason="done",
                    status="SUCCEEDED",
                    side="original",
                    page_no=1,
                    diff_id="D020",
                ),
                OcrRemediationAction(
                    action_id="compare:1:D030:RELOCATE_EVIDENCE",
                    action_type="RELOCATE_EVIDENCE",
                    reason="done",
                    status="SUCCEEDED",
                    side="compare",
                    page_no=1,
                    diff_id="D030",
                ),
                OcrRemediationAction(
                    action_id="original:0:D040:RELOCATE_EVIDENCE",
                    action_type="RELOCATE_EVIDENCE",
                    reason="invalid-page",
                    status="SUCCEEDED",
                    side="original",
                    page_no=0,
                    diff_id="D040",
                ),
            ]
        )

        OcrRemediationStage._refresh_summary_counts(summary)

        assert summary.risk_reduced_page_count == 2

    def test_final_dedupe_uses_canonical_id_and_merged_content_independent_of_input_order(self, tmp_path: Path) -> None:
        def run(order: list[DiffItem], suffix: str) -> tuple[list[dict[str, object]], CompareTask]:
            ctx = make_ctx(tmp_path / suffix)
            ctx.task.audit_item_reviews = {
                "D020:MODIFY": AuditItemReview(
                    review_status="CONFIRMED",
                    review_comment="已核验",
                    reviewed_by="reviewer",
                    reviewed_at="2026-07-17T00:00:00+00:00",
                )
            }
            ctx.diffs = [item.model_copy(deep=True) for item in order]
            SummaryStage().execute(ctx)
            return [item.model_dump(mode="json") for item in ctx.task.diffs], ctx.task

        higher = self._located_diff("D020", original_x0=20, compare_x0=20).model_copy(
            update={
                "review_flags": ["HIGHER_ID"],
                "review_status": "CONFIRMED",
                "review_comment": "已核验",
                "reviewed_by": "reviewer",
                "reviewed_at": "2026-07-17T00:00:00+00:00",
            }
        )
        lower = self._located_diff("D010").model_copy(update={"review_flags": ["LOWER_ID"]})

        forward_diffs, forward_task = run([higher, lower], "forward")
        reverse_diffs, reverse_task = run([lower, higher], "reverse")

        assert forward_diffs == reverse_diffs
        assert [item["diff_id"] for item in forward_diffs] == ["D010"]
        assert forward_task.audit_item_reviews == reverse_task.audit_item_reviews
        assert set(forward_task.audit_item_reviews) == {"D010:MODIFY"}

    def test_final_dedupe_projects_conflicting_audit_item_reviews_conservatively(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.task.audit_item_reviews = {
            "D010:MODIFY": AuditItemReview(
                review_status="CONFIRMED",
                review_comment="已确认",
                reviewed_at="2026-07-17T02:00:00+00:00",
            ),
            "D020:MODIFY": AuditItemReview(
                review_status="FALSE_POSITIVE",
                review_comment="误报",
                reviewed_at="2026-07-17T01:00:00+00:00",
            ),
        }
        ctx.diffs = [self._located_diff("D010"), self._located_diff("D020", original_x0=20, compare_x0=20)]

        SummaryStage().execute(ctx)

        assert set(ctx.task.audit_item_reviews) == {"D010:MODIFY"}
        review = ctx.task.audit_item_reviews["D010:MODIFY"]
        assert review.review_status == "NEEDS_REVIEW"
        assert review.review_comment == "已确认"

    def test_final_dedupe_remaps_colon_diff_id_audit_reviews_by_known_type_suffix(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.task.audit_item_reviews = {
            "A:2:DELETE": AuditItemReview(
                review_status="CONFIRMED",
                review_comment="canonical",
                reviewed_at="2026-07-17T02:00:00+00:00",
            ),
            "B:1:DELETE": AuditItemReview(
                review_status="FALSE_POSITIVE",
                review_comment="duplicate",
                reviewed_at="2026-07-17T01:00:00+00:00",
            ),
        }
        ctx.diffs = [
            self._located_diff("A:2"),
            self._located_diff("B:1", original_x0=20, compare_x0=20),
        ]

        SummaryStage().execute(ctx)

        assert set(ctx.task.audit_item_reviews) == {"A:2:DELETE"}
        review = ctx.task.audit_item_reviews["A:2:DELETE"]
        assert review.review_status == "NEEDS_REVIEW"
        assert review.review_comment == "canonical"

    def test_final_dedupe_drops_ambiguous_blank_audit_review_before_stats(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.task.audit_item_reviews = {
            ":MODIFY": AuditItemReview(review_status="CONFIRMED"),
            "D900:MODIFY": AuditItemReview(review_status="FALSE_POSITIVE"),
        }
        first_empty = self._located_diff("").model_copy(
            update={"original_text": "付款30日", "compare_text": "付款45日"}
        )
        second_empty = self._located_diff("").model_copy(
            update={"original_text": "交付10日", "compare_text": "交付20日"}
        )
        ctx.diffs = [first_empty, second_empty, self._located_diff("D900")]

        SummaryStage().execute(ctx)
        CompareReviewService().refresh_review_stats(ctx.task)

        assert set(ctx.task.audit_item_reviews) == {"D900:MODIFY"}
        assert ctx.task.reviewed_count == 1
        assert ctx.task.confirmed_count == 0
        assert ctx.task.false_positive_count == 1

    def test_final_dedupe_remaps_unique_empty_id_references_to_generated_id(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.task.ocr_quality_summary = TaskOcrQualitySummary(
            status="LOW_TEXT_CONFIDENCE",
            profiles=[
                PageOcrQualityProfile(
                    side="original",
                    page_no=1,
                    status="LOW_TEXT_CONFIDENCE",
                    affected_diff_ids=[""],
                )
            ],
        )
        ctx.task.ocr_remediation_summary = TaskOcrRemediationSummary(
            status="ACTIONS_PLANNED",
            actions=[
                OcrRemediationAction(
                    action_id="original:1::RELOCATE_EVIDENCE",
                    action_type="RELOCATE_EVIDENCE",
                    reason="LOW_TEXT_CONFIDENCE",
                    side="original",
                    page_no=1,
                    diff_id="",
                )
            ],
        )
        ctx.task.audit_item_reviews = {":MODIFY": AuditItemReview(review_status="CONFIRMED")}
        ctx.diffs = [self._located_diff("")]

        SummaryStage().execute(ctx)

        generated_id = ctx.task.diffs[0].diff_id
        assert generated_id.startswith("AUTO-")
        assert ctx.task.ocr_quality_summary is not None
        assert ctx.task.ocr_quality_summary.profiles[0].affected_diff_ids == [generated_id]
        assert ctx.task.ocr_remediation_summary is not None
        assert ctx.task.ocr_remediation_summary.actions[0].diff_id == generated_id
        assert generated_id in ctx.task.ocr_remediation_summary.actions[0].action_id
        assert set(ctx.task.audit_item_reviews) == {f"{generated_id}:MODIFY"}

    @staticmethod
    def _located_diff(
        diff_id: str,
        *,
        page_no: int = 1,
        x0: float = 0,
        original_x0: float | None = None,
        compare_x0: float | None = None,
        compare_evidence: bool = True,
    ) -> DiffItem:
        original_left = x0 if original_x0 is None else original_x0
        compare_left = x0 if compare_x0 is None else compare_x0
        return DiffItem(
            diff_id=diff_id,
            diff_type="MODIFY",
            source_type="clause",
            section_type="main_contract",
            section_path=["付款", "期限"],
            original_clause_id="O001",
            compare_clause_id="C001",
            title="付款期限",
            original_text="付款30日",
            compare_text="付款45日",
            original_evidence=[
                EvidenceBox(
                    page_no=page_no,
                    bbox=BBox(x0=original_left, y0=0, x1=original_left + 100, y1=100),
                    highlight_type="MODIFY",
                )
            ],
            compare_evidence=(
                [
                    EvidenceBox(
                        page_no=page_no,
                        bbox=BBox(x0=compare_left, y0=200, x1=compare_left + 100, y1=300),
                        highlight_type="MODIFY",
                    )
                ]
                if compare_evidence
                else []
            ),
        )

    def test_final_dedupe_remaps_ocr_summaries(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.task.ocr_quality_summary = TaskOcrQualitySummary(
            status="LOW_TEXT_CONFIDENCE",
            requires_review=True,
            risk_page_count=1,
            affected_diff_count=1,
            profiles=[
                PageOcrQualityProfile(
                    side="original",
                    page_no=1,
                    status="LOW_TEXT_CONFIDENCE",
                    affected_diff_ids=["D002"],
                )
            ],
        )
        ctx.task.ocr_remediation_summary = TaskOcrRemediationSummary(
            status="ACTIONS_PLANNED",
            attempted_action_count=1,
            unresolved_action_count=1,
            actions=[
                OcrRemediationAction(
                    action_id="original:1:D002:RELOCATE_EVIDENCE",
                    action_type="RELOCATE_EVIDENCE",
                    reason="LOW_TEXT_CONFIDENCE",
                    side="original",
                    page_no=1,
                    diff_id="D002",
                    review_flags_added=["OCR_REMEDIATION_PLANNED"],
                )
            ],
        )
        ctx.diffs = [
            DiffItem(
                diff_id="D001",
                diff_type="MODIFY",
                source_type="table",
                title="付款",
                section_type="main_contract",
                section_path=["付款"],
                original_text="付款30日",
                compare_text="付款45日",
                original_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=0, y0=0, x1=100, y1=100))],
            ),
            DiffItem(
                diff_id="D002",
                diff_type="MODIFY",
                source_type="table",
                title="付款",
                section_type="main_contract",
                section_path=["付款"],
                original_text="付款30日",
                compare_text="付款45日",
                original_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=20, y0=0, x1=120, y1=100))],
                review_flags=["OCR_REMEDIATION_PLANNED"],
                quality_status="NEEDS_REVIEW",
            ),
        ]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D001"]
        survivor = ctx.task.diffs[0]
        assert survivor.review_flags == ["OCR_REMEDIATION_PLANNED"]
        assert survivor.quality_status == "NEEDS_REVIEW"
        assert ctx.task.ocr_remediation_summary is not None
        action = ctx.task.ocr_remediation_summary.actions[0]
        assert action.diff_id == "D001"
        assert "D001" in action.action_id
        assert "D002" not in action.action_id
        assert ctx.task.ocr_quality_summary is not None
        assert ctx.task.ocr_quality_summary.profiles[0].affected_diff_ids == ["D001"]


class TestComparePipeline:
    def test_default_stages_include_ocr_quality_before_diff_quality(self) -> None:
        stage_names = [type(stage).__name__ for stage in ComparePipeline().stages]

        assert stage_names[stage_names.index("EvidenceStage") : stage_names.index("DiffQualityStage") + 1] == [
            "EvidenceStage",
            "OcrQualityStage",
            "OcrRemediationStage",
            "ModelRoutingStage",
            "DiffQualityStage",
        ]
        assert stage_names[stage_names.index("PreClauseDiffStage") : stage_names.index("SplitStage") + 1] == [
            "PreClauseDiffStage",
            "SigningRegionStage",
            "SplitStage",
        ]
        progress_values = {
            type(stage).__name__: (stage.start_progress, stage.progress) for stage in ComparePipeline().stages
        }
        assert progress_values["SigningRegionStage"] == (40, 42)
        assert progress_values["OcrQualityStage"] == (83, 84)
        assert progress_values["OcrRemediationStage"] == (84, 85)
        assert progress_values["ModelRoutingStage"] == (85, 85)
        assert progress_values["DiffQualityStage"] == (85, 86)
        assert progress_values["VisualizationStage"] == (86, 87)

    def test_compare_service_pipeline_includes_model_routing_before_diff_quality(self) -> None:
        stage_names = [type(stage).__name__ for stage in CompareService()._build_pipeline().stages]

        assert stage_names[stage_names.index("OcrQualityStage") : stage_names.index("DiffQualityStage") + 1] == [
            "OcrQualityStage",
            "OcrRemediationStage",
            "ModelRoutingStage",
            "DiffQualityStage",
        ]
        assert stage_names[stage_names.index("PreClauseDiffStage") : stage_names.index("SplitStage") + 1] == [
            "PreClauseDiffStage",
            "SigningRegionStage",
            "SplitStage",
        ]

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

        pipeline = ComparePipeline(
            stages=[
                FakeStage("stage_a", 30),
                FakeStage("stage_b", 60),
                FakeStage("stage_c", 90),
            ]
        )
        result = pipeline.run(ctx)

        assert result.status == "PROCESSING"
        assert result.progress_percent == 90
        assert execution_log == ["stage_a", "stage_b", "stage_c"]

    def test_pipeline_returns_computation_without_terminal_persistence_or_publication(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ctx = make_ctx(tmp_path)
        repository = LocalJsonTaskRepository(settings)
        repository.save_compare_task(ctx.task)
        events: list[object] = []
        monkeypatch.setattr(ProgressBus.get_instance(), "publish", events.append)

        class Stage:
            name = "compute"
            start_progress = 20
            progress = 90

            def execute(self, stage_ctx: PipelineContext) -> None:
                stage_ctx.task.metrics["result"] = "ready"

        result = ComparePipeline(stages=[Stage()], repository=repository).run(ctx)
        stored = repository.load_compare_task(ctx.task.task_id)

        assert result.metrics["result"] == "ready"
        assert result.status == "PROCESSING"
        assert stored.status == "PROCESSING"
        assert not any(getattr(event, "status", None) in {"COMPLETED", "FAILED"} for event in events)

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

        pipeline = ComparePipeline(
            stages=[
                TrackingStage("a", 30),
                TrackingStage("b", 60),
                TrackingStage("c", 90),
            ]
        )
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

    def test_pipeline_records_peak_memory_from_stage_start_and_end(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        ctx = make_ctx(tmp_path)

        class NoOpStage:
            name = "noop"
            start_progress = 50
            progress = 50

            def execute(self, ctx: PipelineContext) -> None:
                return None

        samples = iter([64.0, 48.0])
        monkeypatch.setattr("app.services.pipeline.get_process_memory_mb", lambda: next(samples))

        result = ComparePipeline(stages=[NoOpStage()]).run(ctx)

        assert result.metrics["peak_memory_mb"] == 64.0
        assert result.metrics["stages"][0]["memory_mb_start"] == 64.0
        assert result.metrics["stages"][0]["memory_mb_end"] == 48.0

    def test_pipeline_preserves_stage_metrics_recorded_before_completion(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        ctx.task.metrics["custom_stage"] = {"value": True}

        class NoOpStage:
            name = "noop"
            start_progress = 50
            progress = 50

            def execute(self, ctx: PipelineContext) -> None:
                return None

        result = ComparePipeline(stages=[NoOpStage()]).run(ctx)

        assert result.metrics["custom_stage"] == {"value": True}
        assert "stages" in result.metrics


def test_pipeline_result_preserves_ocr_quality_summary_without_terminal_persistence(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    repository = LocalJsonTaskRepository(settings)
    repository.save_compare_task(CompareTask(task_id=ctx.task.task_id, status="PROCESSING"))

    class OcrQualitySummaryStage:
        name = "ocr_quality"
        start_progress = 50
        progress = 70

        def execute(self, ctx: PipelineContext) -> None:
            ctx.task.ocr_quality_summary = TaskOcrQualitySummary(
                status="LOW_TEXT_CONFIDENCE",
                requires_review=True,
                page_count_by_status={"LOW_TEXT_CONFIDENCE": 1},
                risk_page_count=1,
                affected_diff_count=1,
                profiles=[
                    PageOcrQualityProfile(
                        side="original",
                        page_no=1,
                        status="LOW_TEXT_CONFIDENCE",
                        score=0.6,
                        reasons=["LOW_AVG_CONFIDENCE"],
                        affected_diff_ids=["D001"],
                    )
                ],
            )

    result = ComparePipeline(stages=[OcrQualitySummaryStage()], repository=repository).run(ctx)

    persisted = repository.load_compare_task(ctx.task.task_id)
    assert result.ocr_quality_summary is not None
    assert result.ocr_quality_summary.status == "LOW_TEXT_CONFIDENCE"
    assert result.ocr_quality_summary.risk_page_count == 1
    assert result.ocr_quality_summary.affected_diff_count == 1
    assert persisted.ocr_quality_summary is None


def test_copy_processing_result_persists_ocr_remediation_summary() -> None:
    target = CompareTask(task_id="task-copy")
    source = CompareTask(task_id="task-copy")
    source.ocr_remediation_summary = TaskOcrRemediationSummary(
        status="ACTIONS_PLANNED",
        attempted_action_count=1,
        unresolved_action_count=1,
    )

    _copy_processing_result(target, source)

    assert target.ocr_remediation_summary is not None
    assert target.ocr_remediation_summary.attempted_action_count == 1


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

    def test_pipeline_failure_records_end_memory_as_peak(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        ctx = make_ctx(tmp_path)

        class FailingStage:
            name = "failing"
            start_progress = 50
            progress = 50

            def execute(self, ctx: PipelineContext) -> None:
                raise RuntimeError("extraction failed")

        samples = iter([32.0, 96.0])
        monkeypatch.setattr("app.services.pipeline.get_process_memory_mb", lambda: next(samples))

        with pytest.raises(RuntimeError, match="extraction failed"):
            ComparePipeline(stages=[FailingStage()]).run(ctx)

        assert ctx.task.metrics["peak_memory_mb"] == 96.0
        assert ctx.task.metrics["stages"][0]["memory_mb_end"] == 96.0


def test_ocr_quality_stage_writes_artifact_and_flags_diff(tmp_path: Path) -> None:
    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    original_doc = Document(
        filename="original.pdf",
        path="original.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=600,
                height=800,
                blocks=[
                    TextBlock(
                        block_id="O1",
                        page_no=1,
                        text="付款30日",
                        bbox=BBox(x0=10, y0=10, x1=100, y1=40),
                        confidence=0.6,
                    )
                ],
            )
        ],
    )
    compare_doc = Document(
        filename="compare.pdf",
        path="compare.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=600,
                height=800,
                blocks=[
                    TextBlock(
                        block_id="C1",
                        page_no=1,
                        text="付款45日",
                        bbox=BBox(x0=10, y0=10, x1=100, y1=40),
                        confidence=0.95,
                    )
                ],
            )
        ],
    )
    task = CompareTask(task_id="TOCRQUALITY")
    ctx = PipelineContext(
        task=task,
        original_pdf=tmp_path / "original.pdf",
        compare_pdf=tmp_path / "compare.pdf",
    )
    ctx.set_extractions(
        ExtractionResult(
            document=original_doc,
            extractor_used="ppstructure_ocr_hybrid",
            layout_quality=LayoutQualityReport(
                page_count=1,
                page_quality=[
                    PageLayoutQualityReport(
                        page_no=1,
                        ocr_block_count=2,
                        matched_ocr_block_count=1,
                        meaningful_unmatched_count=1,
                    )
                ],
            ),
        ),
        ExtractionResult(document=compare_doc, extractor_used="ppstructure_ocr_hybrid"),
    )
    ctx.diffs = [
        DiffItem(
            diff_id="D001",
            diff_type="MODIFY",
            title="付款",
            original_text="付款30日",
            compare_text="付款45日",
            original_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=10, y0=10, x1=100, y1=40))],
        )
    ]

    OcrQualityStage(artifact_store=artifact_store).execute(ctx)

    assert task.ocr_quality_summary is not None
    assert task.ocr_quality_summary.risk_page_count == 1
    assert task.ocr_quality_summary.affected_diff_count == 1
    assert "ocr_quality" in task.debug_artifact_paths
    assert Path(task.debug_artifact_paths["ocr_quality"]).exists()
    assert ctx.diffs[0].quality_status == "NEEDS_REVIEW"
    assert "OCR_LOW_CONFIDENCE" in ctx.diffs[0].review_flags


def test_ocr_remediation_stage_plans_actions_and_marks_diffs(tmp_path: Path) -> None:
    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    task = CompareTask(task_id="task-remediation")
    task.ocr_quality_summary = TaskOcrQualitySummary(
        status="LAYOUT_MISMATCH",
        requires_review=True,
        risk_page_count=1,
        affected_diff_count=1,
        profiles=[
            PageOcrQualityProfile(
                side="original",
                page_no=1,
                status="LAYOUT_MISMATCH",
                reasons=["LOW_LAYOUT_MATCH_RATE"],
                affected_diff_ids=["diff-1"],
            )
        ],
    )
    diff = DiffItem(
        diff_id="diff-1",
        diff_type="MODIFY",
        source_type="clause",
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )
    ctx = PipelineContext(task=task, original_pdf=tmp_path / "o.pdf", compare_pdf=tmp_path / "c.pdf")
    ctx.diffs = [diff]

    OcrRemediationStage(artifact_store=artifact_store).execute(ctx)

    assert task.ocr_remediation_summary is not None
    assert task.ocr_remediation_summary.attempted_action_count == 1
    artifact_path = Path(task.debug_artifact_paths["ocr_remediation"])
    assert artifact_path == tmp_path / "storage" / "tasks" / "task-remediation" / "debug" / "ocr_remediation.json"
    assert artifact_path.exists()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["attempted_action_count"] == 1
    assert payload["actions"][0]["diff_id"] == "diff-1"
    assert "OCR_REMEDIATION_PLANNED" in ctx.diffs[0].review_flags


def test_ocr_remediation_removes_false_delete_after_crop_text_recovery(tmp_path: Path) -> None:
    class RecoveringText:
        def recover(self, **_kwargs) -> str:
            return "担全部法律责任（包括行政处罚、刑事责任）和经济赔偿责任（包括"

    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    task = CompareTask(
        task_id="task-crop-recovery",
        ocr_quality_summary=TaskOcrQualitySummary(
            status="UNRELIABLE",
            requires_review=True,
            profiles=[
                PageOcrQualityProfile(
                    side="compare",
                    page_no=25,
                    status="UNRELIABLE",
                    reasons=["MEANINGFUL_UNMATCHED_OCR"],
                    affected_diff_ids=["D051"],
                )
            ],
        ),
    )
    diff = DiffItem(
        diff_id="D051",
        diff_type="MODIFY",
        source_type="clause",
        original_snippet="担全部法律责任(包括行政处罚、刑事责任)和经济赔偿责任(包括",
        compare_snippet="",
        original_evidence=[EvidenceBox(page_no=25, bbox=BBox(x0=65, y0=75, x1=542, y1=92))],
        review_flags=["PAGE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )
    ctx = PipelineContext(task=task, original_pdf=tmp_path / "o.pdf", compare_pdf=tmp_path / "c.pdf")
    ctx.diffs = [diff]

    OcrRemediationStage(artifact_store=artifact_store, text_recovery=RecoveringText()).execute(ctx)

    assert ctx.diffs == []
    action = task.ocr_remediation_summary.actions[0]
    assert action.action_type == "RETRY_OCR_PAGE"
    assert action.status == "SUCCEEDED"
    assert action.changed_diff_text is True


def test_model_routing_stage_writes_debug_artifact_without_mutating_diffs(tmp_path: Path) -> None:
    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    task = CompareTask(
        task_id="task-model-routing",
        ocr_quality_summary=TaskOcrQualitySummary(
            status="LOW_TEXT_CONFIDENCE",
            requires_review=True,
            profiles=[
                PageOcrQualityProfile(
                    side="original",
                    page_no=1,
                    status="LOW_TEXT_CONFIDENCE",
                    reasons=["LOW_AVG_CONFIDENCE"],
                    affected_diff_ids=["D001"],
                )
            ],
        ),
    )
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="clause",
        original_text="付款金额为100元",
        compare_text="付款金额为120元",
        review_flags=["OCR_LOW_CONFIDENCE"],
        quality_status="NEEDS_REVIEW",
    )
    ctx = PipelineContext(task=task, original_pdf=tmp_path / "o.pdf", compare_pdf=tmp_path / "c.pdf")
    ctx.diffs = [diff]
    before_diff_payload = [item.model_dump(mode="json") for item in ctx.diffs]

    ModelRoutingStage(artifact_store=artifact_store).execute(ctx)

    assert [item.model_dump(mode="json") for item in ctx.diffs] == before_diff_payload
    artifact_path = Path(task.debug_artifact_paths["ocr_model_routing"])
    assert artifact_path == tmp_path / "storage" / "tasks" / "task-model-routing" / "debug" / "ocr_model_routing.json"
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["status"] == "RETRY_RECOMMENDED"
    assert payload["routes"][0]["recommended_route"] == "HIGH_DPI_PAGE_RETRY"
    assert payload["routes"][0]["should_execute"] is False


def test_model_routing_stage_isolates_analyzer_failure(tmp_path: Path) -> None:
    class FailingAnalyzer:
        def analyze(self, *_args, **_kwargs):
            raise RuntimeError("routing analyzer failed")

    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    task = CompareTask(task_id="task-model-routing-failure")
    ctx = PipelineContext(task=task, original_pdf=tmp_path / "o.pdf", compare_pdf=tmp_path / "c.pdf")
    stage = ModelRoutingStage(artifact_store=artifact_store)
    stage.analyzer = FailingAnalyzer()

    stage.execute(ctx)

    assert "ocr_model_routing" not in task.debug_artifact_paths


def test_ocr_remediation_stage_executes_evidence_relocation(tmp_path: Path) -> None:
    original_pdf = tmp_path / "original.pdf"
    compare_pdf = tmp_path / "compare.pdf"
    _write_text_pdf(original_pdf, "付款金额为100元")
    _write_text_pdf(compare_pdf, "付款金额为120元")
    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    task = CompareTask(
        task_id="task-relocate",
        ocr_quality_summary=TaskOcrQualitySummary(
            status="LAYOUT_MISMATCH",
            requires_review=True,
            risk_page_count=1,
            affected_diff_count=1,
            profiles=[
                PageOcrQualityProfile(
                    side="original",
                    page_no=1,
                    status="LAYOUT_MISMATCH",
                    affected_diff_ids=["D001"],
                )
            ],
        ),
    )
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="clause",
        original_snippet="付款金额为100元",
        compare_snippet="付款金额为120元",
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=10, y0=10, x1=80, y1=30),
                method="block_fallback",
                text="付款金额为100元",
                highlight_type="MODIFY",
                confidence=0.46,
                evidence_quality="LOW",
            )
        ],
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )
    ctx = PipelineContext(task=task, original_pdf=original_pdf, compare_pdf=compare_pdf)
    ctx.diffs = [diff]

    OcrRemediationStage(artifact_store=artifact_store).execute(ctx)

    action = task.ocr_remediation_summary.actions[0]
    assert action.status == "SUCCEEDED"
    assert action.changed_evidence is True
    assert action.changed_diff_text is False
    assert action.before_quality["max_confidence"] == 0.46
    assert action.after_quality["max_confidence"] > 0.46
    assert ctx.diffs[0].original_text == ""
    assert ctx.diffs[0].original_evidence[0].method == "text_exact"
    assert "OCR_REMEDIATION_EVIDENCE_RELOCATED" in ctx.diffs[0].review_flags
    assert task.ocr_remediation_summary.successful_action_count == 1
    assert task.ocr_remediation_summary.unresolved_action_count == 0
    assert task.ocr_remediation_summary.risk_reduced_diff_count == 1


def test_ocr_remediation_stage_keeps_unresolved_state_when_relocation_fails(tmp_path: Path) -> None:
    original_pdf = tmp_path / "original.pdf"
    compare_pdf = tmp_path / "compare.pdf"
    _write_text_pdf(original_pdf, "其他内容")
    _write_text_pdf(compare_pdf, "付款金额为120元")
    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    task = CompareTask(
        task_id="task-relocate-failed",
        ocr_quality_summary=TaskOcrQualitySummary(
            status="LAYOUT_MISMATCH",
            requires_review=True,
            risk_page_count=1,
            affected_diff_count=1,
            profiles=[
                PageOcrQualityProfile(
                    side="original",
                    page_no=1,
                    status="LAYOUT_MISMATCH",
                    affected_diff_ids=["D001"],
                )
            ],
        ),
    )
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="clause",
        original_snippet="付款金额为100元",
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=10, y0=10, x1=80, y1=30),
                method="block_fallback",
                text="付款金额为100元",
                highlight_type="MODIFY",
                confidence=0.46,
                evidence_quality="LOW",
            )
        ],
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )
    ctx = PipelineContext(task=task, original_pdf=original_pdf, compare_pdf=compare_pdf)
    ctx.diffs = [diff]

    OcrRemediationStage(artifact_store=artifact_store).execute(ctx)

    action = task.ocr_remediation_summary.actions[0]
    assert action.status == "FAILED"
    assert action.changed_evidence is False
    assert action.changed_diff_text is False
    assert ctx.diffs[0].original_evidence[0].method == "block_fallback"
    assert "OCR_REMEDIATION_UNRESOLVED" in ctx.diffs[0].review_flags
    assert ctx.diffs[0].quality_status == "NEEDS_REVIEW"
    assert task.ocr_remediation_summary.successful_action_count == 0
    assert task.ocr_remediation_summary.unresolved_action_count == 1


def test_ocr_remediation_stage_reports_terminal_status_when_relocation_skipped(tmp_path: Path) -> None:
    original_pdf = tmp_path / "original.pdf"
    compare_pdf = tmp_path / "compare.pdf"
    _write_text_pdf(original_pdf, "付款金额为100元")
    _write_text_pdf(compare_pdf, "付款金额为120元")
    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    task = CompareTask(
        task_id="task-relocate-skipped",
        ocr_quality_summary=TaskOcrQualitySummary(
            status="LAYOUT_MISMATCH",
            requires_review=True,
            risk_page_count=1,
            affected_diff_count=1,
            profiles=[
                PageOcrQualityProfile(
                    side="original",
                    page_no=1,
                    status="LAYOUT_MISMATCH",
                    affected_diff_ids=["D001"],
                )
            ],
        ),
    )
    diff = DiffItem(
        diff_id="D001",
        diff_type="MODIFY",
        source_type="clause",
        original_snippet="付款金额为100元",
        original_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=10, y0=10, x1=80, y1=30),
                method="text_exact",
                text="付款金额为100元",
                highlight_type="MODIFY",
                confidence=0.95,
                evidence_quality="HIGH",
            )
        ],
        review_flags=["EVIDENCE_UNRELIABLE"],
        quality_status="NEEDS_REVIEW",
    )
    ctx = PipelineContext(task=task, original_pdf=original_pdf, compare_pdf=compare_pdf)
    ctx.diffs = [diff]

    OcrRemediationStage(artifact_store=artifact_store).execute(ctx)

    action = task.ocr_remediation_summary.actions[0]
    assert action.status == "SKIPPED"
    assert task.ocr_remediation_summary.unresolved_action_count == 0
    assert task.ocr_remediation_summary.successful_action_count == 0
    assert task.ocr_remediation_summary.status == "OK"


def test_ocr_remediation_actions_survive_diff_quality_cross_source_merge(tmp_path: Path) -> None:
    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    task = CompareTask(
        task_id="TOCRREMEDIATIONMERGE",
        ocr_remediation_summary=TaskOcrRemediationSummary(
            status="ACTIONS_PLANNED",
            attempted_action_count=1,
            unresolved_action_count=1,
            actions=[
                OcrRemediationAction(
                    action_id="original:1:D002:RELOCATE_EVIDENCE",
                    action_type="RELOCATE_EVIDENCE",
                    reason="EVIDENCE_UNRELIABLE",
                    side="original",
                    page_no=1,
                    diff_id="D002",
                    review_flags_added=["OCR_REMEDIATION_PLANNED"],
                )
            ],
        ),
    )
    ctx = PipelineContext(
        task=task,
        original_pdf=tmp_path / "original.pdf",
        compare_pdf=tmp_path / "compare.pdf",
    )
    ctx.set_extractions(
        ExtractionResult(document=make_document("付款30日"), extractor_used="test"),
        ExtractionResult(document=make_document("付款45日"), extractor_used="test"),
    )
    ctx.diffs = [
        DiffItem(
            diff_id="D001",
            diff_type="MODIFY",
            source_type="metadata",
            title="付款",
            original_text="付款30日",
            compare_text="付款45日",
        ),
        DiffItem(
            diff_id="D002",
            diff_type="MODIFY",
            source_type="table",
            title="付款",
            original_text="付款30日",
            compare_text="付款45日",
            review_flags=["OCR_REMEDIATION_PLANNED"],
            quality_status="NEEDS_REVIEW",
        ),
    ]

    DiffQualityStage(artifact_store=artifact_store).execute(ctx)

    assert [diff.diff_id for diff in ctx.diffs] == ["D001"]
    assert task.ocr_remediation_summary is not None
    action = task.ocr_remediation_summary.actions[0]
    assert action.diff_id == "D001"
    assert action.action_id == "original:1:D001:RELOCATE_EVIDENCE"


def test_diff_quality_stage_passes_boundary_context_to_processor(tmp_path: Path) -> None:
    class SpyDiffQualityProcessor:
        def __init__(self) -> None:
            self.kwargs = None

        def process(self, diffs, **kwargs):
            self.kwargs = kwargs
            return DiffQualityResult(diffs=diffs, decisions=[])

    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    ctx = make_ctx(tmp_path)
    original_document = make_document("第一条 原合同文本。")
    compare_document = make_document("第一条 新合同文本。")
    ctx.set_extractions(
        ExtractionResult(document=original_document, extractor_used="test"),
        ExtractionResult(document=compare_document, extractor_used="test"),
    )
    ctx.original_clauses = [make_clause("O001", "1", "第一条 原合同文本。")]
    ctx.compare_clauses = [make_clause("N001", "1", "第一条 新合同文本。")]
    ctx.diffs = [DiffItem(diff_id="D001", diff_type="MODIFY", source_type="clause")]
    stage = DiffQualityStage(artifact_store=artifact_store)
    spy = SpyDiffQualityProcessor()
    stage.processor = spy

    stage.execute(ctx)

    assert spy.kwargs is not None
    assert spy.kwargs["original_clauses"] is ctx.original_clauses
    assert spy.kwargs["compare_clauses"] is ctx.compare_clauses
    assert spy.kwargs["original_document"] is original_document
    assert spy.kwargs["compare_document"] is compare_document


def test_diff_quality_stage_allows_diffs_without_extractions(tmp_path: Path) -> None:
    class SpyDiffQualityProcessor:
        def __init__(self) -> None:
            self.called = False
            self.kwargs = None

        def process(self, diffs, **kwargs):
            self.called = True
            self.kwargs = kwargs
            return DiffQualityResult(diffs=diffs, decisions=[])

    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    ctx = make_ctx(tmp_path)
    ctx.original_clauses = [make_clause("O001", "1", "第一条 原合同文本。")]
    ctx.compare_clauses = [make_clause("N001", "1", "第一条 新合同文本。")]
    ctx.diffs = [DiffItem(diff_id="D001", diff_type="MODIFY", source_type="clause")]
    stage = DiffQualityStage(artifact_store=artifact_store)
    spy = SpyDiffQualityProcessor()
    stage.processor = spy

    stage.execute(ctx)

    assert spy.called
    assert spy.kwargs is not None
    assert spy.kwargs["original_clauses"] is ctx.original_clauses
    assert spy.kwargs["compare_clauses"] is ctx.compare_clauses
    assert spy.kwargs["original_document"] is None
    assert spy.kwargs["compare_document"] is None


def test_diff_quality_stage_requires_diffs_before_optional_extractions(tmp_path: Path) -> None:
    class MissingDiffsContext(PipelineContext):
        def require_diffs(self) -> list[DiffItem]:
            raise PipelineContractError("Pipeline stage requires diffs.")

        def require_extractions(self):
            raise AssertionError("DiffQualityStage should not require extractions before diffs")

    ctx = MissingDiffsContext(
        task=CompareTask(task_id="TMISSINGDIFFS"),
        original_pdf=tmp_path / "original.pdf",
        compare_pdf=tmp_path / "compare.pdf",
    )

    with pytest.raises(PipelineContractError, match="requires diffs"):
        DiffQualityStage().execute(ctx)


def test_ocr_quality_survives_diff_quality_cross_source_merge(tmp_path: Path) -> None:
    artifact_store = LocalArtifactStore(Settings(storage_dir=tmp_path / "storage"))
    original_doc = Document(
        filename="original.pdf",
        path="original.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=600,
                height=800,
                blocks=[
                    TextBlock(
                        block_id="O1",
                        page_no=1,
                        text="付款30日",
                        bbox=BBox(x0=10, y0=10, x1=100, y1=40),
                        confidence=0.6,
                    )
                ],
            ),
            Page(
                page_no=2,
                width=600,
                height=800,
                blocks=[
                    TextBlock(
                        block_id="O2",
                        page_no=2,
                        text="付款30日",
                        bbox=BBox(x0=10, y0=10, x1=100, y1=40),
                        confidence=0.95,
                    )
                ],
            ),
        ],
    )
    compare_doc = Document(
        filename="compare.pdf",
        path="compare.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=600,
                height=800,
                blocks=[
                    TextBlock(
                        block_id="C1",
                        page_no=1,
                        text="付款45日",
                        bbox=BBox(x0=10, y0=10, x1=100, y1=40),
                        confidence=0.95,
                    )
                ],
            ),
            Page(
                page_no=2,
                width=600,
                height=800,
                blocks=[
                    TextBlock(
                        block_id="C2",
                        page_no=2,
                        text="付款45日",
                        bbox=BBox(x0=10, y0=10, x1=100, y1=40),
                        confidence=0.95,
                    )
                ],
            ),
        ],
    )
    task = CompareTask(task_id="TOCRMERGE")
    ctx = PipelineContext(
        task=task,
        original_pdf=tmp_path / "original.pdf",
        compare_pdf=tmp_path / "compare.pdf",
    )
    ctx.set_extractions(
        ExtractionResult(document=original_doc, extractor_used="ppstructure_ocr_hybrid"),
        ExtractionResult(document=compare_doc, extractor_used="ppstructure_ocr_hybrid"),
    )
    ctx.diffs = [
        DiffItem(
            diff_id="D001",
            diff_type="MODIFY",
            source_type="metadata",
            title="付款",
            original_text="付款30日",
            compare_text="付款45日",
            original_evidence=[EvidenceBox(page_no=2, bbox=BBox(x0=10, y0=10, x1=100, y1=40))],
        ),
        DiffItem(
            diff_id="D002",
            diff_type="MODIFY",
            source_type="table",
            title="付款",
            original_text="付款30日",
            compare_text="付款45日",
            original_evidence=[EvidenceBox(page_no=1, bbox=BBox(x0=10, y0=10, x1=100, y1=40))],
        ),
    ]

    OcrQualityStage(artifact_store=artifact_store).execute(ctx)
    assert task.ocr_quality_summary is not None
    assert task.ocr_quality_summary.profiles[0].affected_diff_ids == ["D002"]

    DiffQualityStage(artifact_store=artifact_store).execute(ctx)

    assert [diff.diff_id for diff in ctx.diffs] == ["D001"]
    winner = ctx.diffs[0]
    assert winner.quality_status == "NEEDS_REVIEW"
    assert "OCR_LOW_CONFIDENCE" in winner.review_flags
    assert task.ocr_quality_summary.profiles[0].affected_diff_ids == ["D001"]
    assert task.ocr_quality_summary.affected_diff_count == 1


def _attach_running_execution(
    ctx: PipelineContext,
    tmp_path: Path,
) -> tuple[ExecutionStateCoordinator, TaskJob]:
    repository = LocalJsonTaskJobRepository(Settings(storage_dir=tmp_path / "execution-state"))
    coordinator = ExecutionStateCoordinator(repository)
    job = coordinator.enqueue(
        TaskJob(
            job_id=f"compare:{ctx.task.task_id}:1",
            task_id=ctx.task.task_id,
            task_type="compare",
        )
    )
    claimed = coordinator.claim_next(worker_id="pipeline-worker", lease_seconds=30)
    assert claimed is not None
    ctx.execution_context = TaskExecutionContext(
        job_id=job.job_id,
        task_id=job.task_id,
        worker_id="pipeline-worker",
        cancellation_token=CancellationToken(
            job_id=job.job_id,
            worker_id="pipeline-worker",
            coordinator=coordinator,
        ),
    )
    return coordinator, job


def test_pipeline_checks_cancellation_immediately_before_stage(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    coordinator, job = _attach_running_execution(ctx, tmp_path)
    coordinator.request_cancel(job.task_id, task_type="compare")
    executed: list[str] = []

    class Stage:
        name = "must-not-run"
        start_progress = 20
        progress = 30

        def execute(self, _ctx: PipelineContext) -> None:
            executed.append(self.name)

    with pytest.raises(TaskCancelled):
        ComparePipeline(stages=[Stage()]).run(ctx)

    assert executed == []
    assert ctx.task.status == "PROCESSING"
    assert coordinator.mark_cancelled(job.job_id, worker_id="pipeline-worker").status == "CANCELLED"


def test_pipeline_rechecks_cancellation_after_progress_write_before_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = make_ctx(tmp_path)
    coordinator, job = _attach_running_execution(ctx, tmp_path)
    repository = LocalJsonTaskRepository(Settings(storage_dir=tmp_path / "pipeline-tasks"))
    entered_progress = threading.Event()
    release_progress = threading.Event()
    executed: list[str] = []
    errors: list[BaseException] = []
    update = repository.update_compare_task

    def blocking_update(task_id: str, mutate: object) -> CompareTask:
        entered_progress.set()
        assert release_progress.wait(1)
        return update(task_id, mutate)

    monkeypatch.setattr(repository, "update_compare_task", blocking_update)

    class Stage:
        name = "must-not-start"
        start_progress = 20
        progress = 30

        def execute(self, _ctx: PipelineContext) -> None:
            executed.append(self.name)

    def run() -> None:
        try:
            ComparePipeline(stages=[Stage()], repository=repository).run(ctx)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert entered_progress.wait(1)
    coordinator.request_cancel(job.task_id, task_type="compare")
    release_progress.set()
    thread.join()

    assert len(errors) == 1
    assert isinstance(errors[0], TaskCancelled)
    assert executed == []


def test_pipeline_rechecks_cancellation_after_stage_end_progress_persistence_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = make_ctx(tmp_path)
    coordinator, job = _attach_running_execution(ctx, tmp_path)
    repository = LocalJsonTaskRepository(Settings(storage_dir=tmp_path / "pipeline-tasks"))
    entered_stage_end_progress = threading.Event()
    release_stage_end_progress = threading.Event()
    errors: list[BaseException] = []
    events: list[object] = []
    update_calls = 0
    update = repository.update_compare_task
    monkeypatch.setattr(ProgressBus.get_instance(), "publish", events.append)

    def block_stage_end_update(task_id: str, mutate: object) -> CompareTask:
        nonlocal update_calls
        update_calls += 1
        if update_calls == 2:
            entered_stage_end_progress.set()
            assert release_stage_end_progress.wait(1)
        return update(task_id, mutate)

    monkeypatch.setattr(repository, "update_compare_task", block_stage_end_update)

    class Stage:
        name = "stage-end-race"
        start_progress = 20
        progress = 80

        def execute(self, _ctx: PipelineContext) -> None:
            return None

    def run() -> None:
        try:
            ComparePipeline(stages=[Stage()], repository=repository).run(ctx)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert entered_stage_end_progress.wait(1)
    coordinator.request_cancel(job.task_id, task_type="compare")
    release_stage_end_progress.set()
    thread.join()

    assert len(errors) == 1
    assert isinstance(errors[0], TaskCancelled)
    assert repository.load_compare_task(ctx.task.task_id).progress_percent == 80
    assert not any(
        getattr(event, "stage", None) == "stage-end-race" and getattr(event, "progress_percent", None) == 80
        for event in events
    )


def test_pipeline_checks_cancellation_after_blocked_stage_before_downstream_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = make_ctx(tmp_path)
    coordinator, job = _attach_running_execution(ctx, tmp_path)
    entered = threading.Event()
    release = threading.Event()
    downstream: list[str] = []
    errors: list[BaseException] = []
    events: list[object] = []
    monkeypatch.setattr(ProgressBus.get_instance(), "publish", events.append)

    class BlockedStage:
        name = "blocked"
        start_progress = 20
        progress = 40

        def execute(self, _ctx: PipelineContext) -> None:
            entered.set()
            assert release.wait(1)

    class DownstreamStage:
        name = "downstream"
        start_progress = 50
        progress = 80

        def execute(self, _ctx: PipelineContext) -> None:
            downstream.append(self.name)

    def run() -> None:
        try:
            ComparePipeline(stages=[BlockedStage(), DownstreamStage()]).run(ctx)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert entered.wait(1)
    coordinator.request_cancel(job.task_id, task_type="compare")
    release.set()
    thread.join()

    assert len(errors) == 1
    assert isinstance(errors[0], TaskCancelled)
    assert downstream == []
    assert not any(getattr(event, "status", None) == "COMPLETED" for event in events)
    assert coordinator.mark_cancelled(job.job_id, worker_id="pipeline-worker").status == "CANCELLED"


def test_pipeline_checks_cancellation_immediately_after_stage_returns(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    coordinator, job = _attach_running_execution(ctx, tmp_path)
    downstream: list[str] = []

    class CancellingStage:
        name = "remote-ocr"
        start_progress = 20
        progress = 50

        def execute(self, _ctx: PipelineContext) -> None:
            coordinator.request_cancel(job.task_id, task_type="compare")

    class DownstreamStage:
        name = "downstream"
        start_progress = 60
        progress = 80

        def execute(self, _ctx: PipelineContext) -> None:
            downstream.append(self.name)

    with pytest.raises(TaskCancelled):
        ComparePipeline(stages=[CancellingStage(), DownstreamStage()]).run(ctx)

    assert downstream == []
    assert coordinator.mark_cancelled(job.job_id, worker_id="pipeline-worker").status == "CANCELLED"


def test_pipeline_checks_cancellation_immediately_before_terminal_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = make_ctx(tmp_path)
    coordinator, job = _attach_running_execution(ctx, tmp_path)
    token = ctx.execution_context.cancellation_token
    checks = 0
    events: list[object] = []
    monkeypatch.setattr(ProgressBus.get_instance(), "publish", events.append)

    class CancelBeforeTerminalToken:
        def raise_if_cancelled(self) -> None:
            nonlocal checks
            checks += 1
            if checks == 6:
                coordinator.request_cancel(job.task_id, task_type="compare")
            token.raise_if_cancelled()

    ctx.execution_context = TaskExecutionContext(
        job_id=job.job_id,
        task_id=job.task_id,
        worker_id="pipeline-worker",
        cancellation_token=CancelBeforeTerminalToken(),
    )

    class Stage:
        name = "last-stage"
        start_progress = 20
        progress = 90

        def execute(self, _ctx: PipelineContext) -> None:
            return None

    with pytest.raises(TaskCancelled):
        ComparePipeline(stages=[Stage()]).run(ctx)

    assert checks == 6
    assert ctx.task.status == "PROCESSING"
    assert not any(getattr(event, "status", None) == "COMPLETED" for event in events)
    assert coordinator.mark_cancelled(job.job_id, worker_id="pipeline-worker").status == "CANCELLED"


def test_compare_service_passes_execution_context_into_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_repository = LocalJsonTaskRepository(Settings(storage_dir=tmp_path / "task-store"))
    service = CompareService(repository=task_repository)
    seed_ctx = PipelineContext(
        task=CompareTask(task_id="TSERVICE_CONTEXT"),
        original_pdf=tmp_path / "original.pdf",
        compare_pdf=tmp_path / "compare.pdf",
    )
    _coordinator, _job = _attach_running_execution(seed_ctx, tmp_path)
    execution_context = seed_ctx.execution_context
    seen: list[TaskExecutionContext | None] = []

    class CapturingPipeline:
        def run(self, ctx: PipelineContext) -> CompareTask:
            seen.append(ctx.execution_context)
            return ctx.task

    monkeypatch.setattr(service, "_build_pipeline", lambda: CapturingPipeline())

    service.compare(
        tmp_path / "original.pdf",
        tmp_path / "compare.pdf",
        task_id="TSERVICE_CONTEXT",
        execution_context=execution_context,
    )

    assert seen == [execution_context]


def test_compare_service_progress_callback_checks_cancellation_before_writing(tmp_path: Path) -> None:
    task_repository = LocalJsonTaskRepository(Settings(storage_dir=tmp_path / "task-store"))
    task_repository.save_compare_task(CompareTask(task_id="TPROGRESS_CANCEL", stage="before", progress_percent=10))
    service = CompareService(repository=task_repository)
    seed_ctx = PipelineContext(
        task=CompareTask(task_id="TPROGRESS_CANCEL"),
        original_pdf=tmp_path / "original.pdf",
        compare_pdf=tmp_path / "compare.pdf",
    )
    coordinator, job = _attach_running_execution(seed_ctx, tmp_path)
    coordinator.request_cancel(job.task_id, task_type="compare")
    callback = service._make_progress_callback("TPROGRESS_CANCEL", seed_ctx.execution_context)

    with pytest.raises(TaskCancelled):
        callback(50, "after")

    stored = task_repository.load_compare_task("TPROGRESS_CANCEL")
    assert (stored.stage, stored.progress_percent) == ("before", 10)
