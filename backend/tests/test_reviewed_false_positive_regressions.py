"""Synthetic regressions for the reviewed comparison failures (no contracts)."""

from __future__ import annotations

import pytest

from app.models import BBox, CharBox, Clause, DiffItem, Document, EvidenceBox, Page, TextBlock, TextRange
from app.services.audit_summary import build_audit_items, build_audit_stats
from app.services.diff.overlap_dedup import deduplicate_overlaps
from app.services.diff_quality import DiffQualityProcessor
from app.services.document_preparation import DocumentPreparer
from app.services.matcher import ClauseMatcher
from app.services.signing_region.models import SigningVisualObject, SigningVisualObjectType
from app.services.signing_region.visual_recall import WholePageSigningVisualRecall
from app.services.table_compare.diff_builder import TableDiffBuilder
from app.services.table_compare import TableComparator
from app.services.table_compare.types import CellDiff, CellDiffGroup, LogicalCell, LogicalRow, LogicalTable


def evidence(text: str, kind: str, page: int = 1) -> EvidenceBox:
    return EvidenceBox(page_no=page, bbox=BBox(x0=50, y0=100, x1=500, y1=150), text=text, highlight_type=kind)


def test_containment_cannot_consume_whole_original_from_one_shared_sentence() -> None:
    shared = "甲方按照合同约定支付服务费用。"
    original = Clause(clause_id="O", normalized_text="", text=shared + "\n乙方应提供一年维护服务，逾期承担违约责任。")
    other = Clause(clause_id="N", normalized_text="", text="其他条款。\n" + shared)
    matcher = ClauseMatcher()
    assert matcher._best_compare_containment(original, [other], {}) is None
    complete = Clause(clause_id="N2", normalized_text="", text="第一条双方应当严格遵守合同其他约定。\n" + original.text)
    assert matcher._best_compare_containment(original, [complete], {}) is not None


@pytest.mark.parametrize("kind,side", [("ADD", "compare"), ("DELETE", "original")])
def test_short_display_snippet_never_erases_complete_obligation(kind: str, side: str) -> None:
    text = "2.3乙方应于开工通知之日起30日内完成工作并通过最终验收。"
    diff = DiffItem(
        diff_id="new", diff_type=kind, source_type="clause", **{f"{side}_text": text, f"{side}_snippet": "2.3"}
    )
    result = DiffQualityProcessor().process([diff])
    assert len(result.diffs) == 1
    assert "30日" in getattr(result.diffs[0], f"{side}_text")


def test_overlap_keeps_new_first_line_old_obligation_and_changed_amount() -> None:
    common = "本合同服务费用已经包含交通、材料、人工以及完成项目所需的其他全部费用。"
    old = "乙方应提供驻场服务，每周提交工作进度报告。" + common + "合同总价10000元。"
    new = "2.3工期为30日，全部工作须通过最终验收。\n" + common + "合同总价20000元。"
    mod = DiffItem(
        diff_id="M",
        diff_type="MODIFY",
        source_type="clause",
        original_text=old,
        original_evidence=[evidence(old, "DELETE")],
        original_change_ranges=[TextRange(start=0, end=len(old), highlight_type="DELETE")],
    )
    add = DiffItem(
        diff_id="A",
        diff_type="ADD",
        source_type="clause",
        compare_text=new,
        compare_evidence=[evidence(new.splitlines()[0], "ADD"), evidence(common, "ADD")],
        compare_change_ranges=[TextRange(start=0, end=len(new), highlight_type="ADD")],
    )
    by_id = {item.diff_id: item for item in deduplicate_overlaps([mod, add])}
    assert "驻场服务" in by_id["M"].original_snippet
    assert "30日" in by_id["A"].compare_snippet
    assert "最终验收" in by_id["A"].compare_snippet
    # The differing digit must remain highlighted on both sides.
    assert any("10000" in old[r.start : r.end] for r in by_id["M"].original_change_ranges)
    assert any("20000" in new[r.start : r.end] for r in by_id["A"].compare_change_ranges)
    assert by_id["A"].compare_evidence[0].page_no == 1


def test_repeated_text_on_another_page_is_not_overlap_proof() -> None:
    common = "本合同服务费用已经包含交通、材料、人工以及完成项目所需的其他全部费用。"
    mod = DiffItem(
        diff_id="M",
        diff_type="MODIFY",
        source_type="clause",
        original_text=common,
        original_evidence=[evidence(common, "DELETE", 1)],
    )
    add = DiffItem(
        diff_id="A",
        diff_type="ADD",
        source_type="clause",
        compare_text="2.3新增义务\n" + common,
        compare_evidence=[evidence(common, "ADD", 8)],
    )
    result = deduplicate_overlaps([mod, add])
    assert result == [mod, add]


@pytest.mark.parametrize(
    "text", ["删除[Jy]: 5.5按验收条件支付费用。", "设置格式[Jy]: 字体：11 磅", "设置格式[Editor A]: 行距:1.5 倍行距"]
)
def test_revision_sidebar_is_excluded_with_latin_author(text: str) -> None:
    block = TextBlock(block_id="revision", page_no=1, text=text, bbox=BBox(x0=535, y0=100, x1=760, y1=120))
    doc = Document(
        filename="test.pdf",
        path="test.pdf",
        page_count=1,
        pages=[Page(page_no=1, width=772, height=842, blocks=[block])],
    )
    DocumentPreparer().prepare(doc, "original")
    assert block.block_role == "revision_artifact"
    assert block.enter_clause_compare is False


def test_contract_sentence_about_deletion_is_retained() -> None:
    block = TextBlock(
        block_id="body",
        page_no=1,
        text="3.2甲方要求删除错误数据时，乙方应在两日内处理。",
        bbox=BBox(x0=70, y0=100, x1=510, y1=120),
    )
    doc = Document(
        filename="test.pdf",
        path="test.pdf",
        page_count=1,
        pages=[Page(page_no=1, width=600, height=800, blocks=[block])],
    )
    DocumentPreparer().prepare(doc, "original")
    assert block.block_role != "revision_artifact"


def source_row(values: list[str]) -> TextBlock:
    text, boxes = "", []
    for column, value in enumerate(values):
        for index, char in enumerate(value):
            boxes.append(
                CharBox(
                    char=char,
                    text_index=len(text),
                    page_no=1,
                    bbox=BBox(x0=column * 150 + index * 10, y0=100, x1=column * 150 + index * 10 + 9, y1=110),
                )
            )
            text += char
        text += "\n"
    return TextBlock(block_id="table", page_no=1, text=text, char_boxes=boxes, bbox=BBox(x0=0, y0=90, x1=700, y1=130))


def test_source_row_recovery_preserves_quantity_and_column_changes() -> None:
    signature = TableDiffBuilder._source_row_columns
    original = source_row(["预测模型", "技术参数", "1", "2"])
    assert signature(original, "预测模型") == signature(source_row(["预测模型", "技术参数", "1", "2"]), "预测模型")
    assert signature(original, "预测模型") != signature(source_row(["预测模型", "技术参数", "2", "1"]), "预测模型")
    assert signature(original, "预测模型") != signature(source_row(["预测模型", "技术参数", "3", "2"]), "预测模型")


def test_candidate_is_not_counted_as_confirmed_even_with_legacy_normal_quality() -> None:
    diff = DiffItem(
        diff_id="candidate",
        diff_type="ADD",
        quality_status="NORMAL",
        compare_text="签名",
        compare_evidence=[evidence("签名", "ADD")],
        match_score_details={"change_decision": "CANDIDATE"},
    )
    items = build_audit_items([diff])
    assert all(item.change_decision == "CANDIDATE" for item in items)
    assert build_audit_stats([diff]).add == 0
    assert build_audit_stats([diff]).needs_review == len(items)


def test_existing_edge_mark_prevents_confirmation_after_aggregation() -> None:
    objects = [
        SigningVisualObject(
            object_id=str(page),
            object_type=SigningVisualObjectType.EDGE_SEAM_SEAL,
            page_no=page,
            bbox=BBox(x0=580, y0=100, x1=600, y1=200),
            visual_detection_confidence=0.99,
            supporting_pages=[page],
            raw_ref={"counterpart_edge_present": True},
        )
        for page in range(1, 6)
    ]
    aggregate = WholePageSigningVisualRecall._aggregate_edge_fragments(objects)[0]
    assert not WholePageSigningVisualRecall._is_confirmed_object(aggregate)


def test_unsupported_consensus_does_not_downgrade_observed_duration_change() -> None:
    original = evidence("30天", "MODIFY")
    compare = evidence("在服务期限内", "MODIFY")
    for item in (original, compare):
        item.method = "char_exact"
        item.confidence = 0.98
    diff = DiffItem(diff_id="duration", diff_type="MODIFY", original_text="30天", compare_text="在服务期限内",
                    original_evidence=[original], compare_evidence=[compare])
    result = DiffQualityProcessor().process([diff]).diffs[0]
    assert result.match_score_details["change_decision"] == "CONFIRMED"
    diff.compare_evidence = []
    result = DiffQualityProcessor().process([diff]).diffs[0]
    assert result.match_score_details["change_decision"] == "NEEDS_REVIEW"


def test_stitched_table_evidence_uses_continuation_page_source() -> None:
    text = "新增服务内容"
    first = TextBlock(block_id="first", page_no=1, text=text, bbox=BBox(x0=40, y0=100, x1=400, y1=300))
    second = TextBlock(block_id="second", page_no=2, text=text, bbox=BBox(x0=40, y0=100, x1=400, y1=300),
                       char_boxes=[CharBox(char=char, text_index=i, page_no=2,
                                           bbox=BBox(x0=200+i*10, y0=180, x1=210+i*10, y1=192))
                                   for i,char in enumerate(text)])
    cell = LogicalCell(0, 0, text, second.bbox, 2, "second", 0, 0)
    table = LogicalTable([LogicalRow(0, [cell], 2, "second", 0)], 1, 1, source_block_id="first")
    group = CellDiffGroup(0, [CellDiff(0, 0, "", text, "ADD", compare_row=0)])
    builder = TableComparator()._diff_builder
    builder.source_blocks["compare"] = {"first": first, "second": second}
    evidence_boxes = builder._char_level_evidence(table, group, first, "compare")
    assert len(evidence_boxes) == 1
    assert evidence_boxes[0].page_no == 2
    assert evidence_boxes[0].bbox.x0 == 200
    assert evidence_boxes[0].bbox.y0 == 180


def test_replay_summary_artifact_stays_in_injected_storage(tmp_path) -> None:
    from app.config import Settings
    from app.infrastructure.artifact_store import ArtifactStore
    from app.infrastructure.task_repository import SQLiteTaskRepository
    from app.models import CompareTask
    from app.services.compare_service import CompareService
    from app.services.pipeline import PipelineContext

    runtime = Settings(storage_dir=tmp_path)
    service = CompareService(repository=SQLiteTaskRepository(runtime), artifact_store=ArtifactStore(runtime))
    summary = service._build_pipeline().stages[-1]
    task = CompareTask(task_id="isolated-summary")
    ctx = PipelineContext(task=task, original_pdf=tmp_path/"original.pdf", compare_pdf=tmp_path/"compare.pdf")
    summary.execute(ctx)
    assert (tmp_path/"tasks/isolated-summary/diagnostics/debug/diff_decisions.json").is_file()
