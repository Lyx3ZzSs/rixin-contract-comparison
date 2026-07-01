from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from app.config import Settings, settings
from app.errors import PipelineContractError
from app.infrastructure.artifact_store import LocalArtifactStore
from app.infrastructure.task_repository import LocalJsonTaskRepository
from app.models import (
    BBox,
    Clause,
    ClausePair,
    CompareTask,
    CompareOptions,
    DiffItem,
    Document,
    EvidenceBox,
    LayoutQualityReport,
    OcrRemediationAction,
    Page,
    PageLayoutQualityReport,
    PageOcrQualityProfile,
    TaskOcrQualitySummary,
    TaskOcrRemediationSummary,
    TextBlock,
)
from app.services.extractors.base import ExtractionResult
from app.services.compare_service import CompareService
from app.services.diff_quality import DiffQualityResult
from app.services.pipeline import ComparePipeline, PipelineContext, _copy_processing_result
from app.services.pipeline_stages import (
    ClauseDiffStage,
    DiffQualityStage,
    MatchStage,
    ModelRoutingStage,
    OcrQualityStage,
    OcrRemediationStage,
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


def _write_text_pdf(path: Path, text: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 96), text, fontname="china-s")
    doc.save(path)
    doc.close()


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

    def test_deduplicates_final_diffs(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        duplicate = DiffItem(
            diff_id="D001",
            diff_type="DELETE",
            title="表格字段：单位名称",
            original_text="国能日新科技股份有限公司",
            source_type="table",
        )
        ctx.diffs = [
            duplicate,
            duplicate.model_copy(deep=True),
            DiffItem(
                diff_id="D002",
                diff_type="DELETE",
                title="表格字段：单位名称",
                original_text="国能日新科技股份有限公司",
                source_type="table",
            ),
        ]

        SummaryStage().execute(ctx)

        assert [diff.diff_id for diff in ctx.task.diffs] == ["D001"]
        assert ctx.task.diff_count == 1

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
        progress_values = {
            type(stage).__name__: (stage.start_progress, stage.progress)
            for stage in ComparePipeline().stages
        }
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


def test_pipeline_completion_preserves_ocr_quality_summary(tmp_path: Path) -> None:
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

    ComparePipeline(stages=[OcrQualitySummaryStage()], repository=repository).run(ctx)

    persisted = repository.load_compare_task(ctx.task.task_id)
    assert persisted.ocr_quality_summary is not None
    assert persisted.ocr_quality_summary.status == "LOW_TEXT_CONFIDENCE"
    assert persisted.ocr_quality_summary.risk_page_count == 1
    assert persisted.ocr_quality_summary.affected_diff_count == 1


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
