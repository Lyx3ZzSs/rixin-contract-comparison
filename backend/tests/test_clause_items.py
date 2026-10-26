from __future__ import annotations

from app.models import BBox, EvidenceBox
from app.services.clause_items import ClauseItem


def evidence(page_no: int, y0: float) -> EvidenceBox:
    return EvidenceBox(page_no=page_no, bbox=BBox(x0=0, y0=y0, x1=10, y1=y0 + 5), text=f"p{page_no}")


def item(text: str, *, page_no: int = 1, y0: float = 10, flags: list[str] | None = None) -> ClauseItem:
    return ClauseItem.from_part(
        clause_no="1",
        title="付款",
        section_type="main_contract",
        section_path=["第一条 付款"],
        text=text,
        char_boxes=[],
        page_numbers=[page_no],
        bboxes=[evidence(page_no, y0)],
        source_block_ids=[f"b{page_no}"],
        segmentation_reason="marker:1",
        segmentation_confidence=0.9,
        split_flags=flags or [],
    )


def test_clause_item_merge_preserves_parallel_metadata_and_dedupes_flags() -> None:
    target = item("1. 付款", flags=["PARAGRAPH_MERGED"])
    source = item("付款方式", page_no=2, y0=5, flags=["PARAGRAPH_MERGED", "CROSS_PAGE_CONTINUATION_MERGED"])

    target.merge_from(source, "continuation_boundary_repair")

    assert target.texts == ["1. 付款", "付款方式"]
    assert target.page_numbers == [1, 2]
    assert [box.page_no for box in target.bboxes] == [1, 2]
    assert target.source_block_ids == ["b1", "b2"]
    assert target.split_flags == ["PARAGRAPH_MERGED", "CROSS_PAGE_CONTINUATION_MERGED"]
    assert target.segmentation_reason == "marker:1|order:continuation_boundary_repair"


def test_clause_item_pop_and_insert_fragment_keep_lists_aligned() -> None:
    clause = item("1. 付款")
    clause.append_part(
        text="补充说明",
        char_boxes=[],
        page_numbers=[1],
        bboxes=[evidence(1, 1)],
        source_block_ids=["b-extra"],
        split_flags=["READING_ORDER_REPAIRED"],
    )

    fragment = clause.pop_fragment(1)
    clause.insert_fragment(0, fragment)

    assert clause.texts == ["补充说明", "1. 付款"]
    assert clause.page_numbers == [1, 1]
    assert [box.bbox.y0 for box in clause.bboxes] == [1, 10]
    assert clause.source_block_ids == ["b-extra", "b1"]


def test_clause_item_pop_fragment_preserves_multi_evidence_fragment_metadata() -> None:
    clause = ClauseItem.from_part(
        clause_no="1",
        title="付款",
        section_type="main_contract",
        section_path=["第一条 付款"],
        text="跨页合并正文",
        char_boxes=[],
        page_numbers=[1, 2],
        bboxes=[evidence(1, 100), evidence(2, 10)],
        source_block_ids=["b1", "b2"],
        segmentation_reason="marker:1",
        segmentation_confidence=0.9,
        split_flags=[],
    )
    clause.append_part(
        text="上移片段",
        char_boxes=[],
        page_numbers=[1],
        bboxes=[evidence(1, 50)],
        source_block_ids=["b3"],
    )

    fragment = clause.pop_fragment(1)

    assert fragment.text == "上移片段"
    assert fragment.page_numbers == [1]
    assert [box.bbox.y0 for box in fragment.bboxes] == [50]
    assert fragment.source_block_ids == ["b3"]
    assert clause.texts == ["跨页合并正文"]
    assert clause.page_numbers == [1, 2]
    assert [box.bbox.y0 for box in clause.bboxes] == [100, 10]
    assert clause.source_block_ids == ["b1", "b2"]
