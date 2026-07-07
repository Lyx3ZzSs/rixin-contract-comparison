from app.models import BBox, Document, Page, TextBlock
from app.services.signing_region.clause_document import SigningClauseDocumentBuilder
from app.services.signing_region.models import (
    SigningBlock,
    SigningBlockConfidenceLevel,
    SigningBlockRole,
)


def _bbox(x0: float, y0: float, x1: float, y1: float) -> BBox:
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _signing_block(
    block_id: str,
    page_no: int,
    bbox: BBox,
    *,
    source_block_ids: list[str] | None = None,
    text: str = "甲方：A 乙方：B\n(盖章)\n(签字)\n日期：",
    exclude_from_clause_diff: bool = True,
) -> SigningBlock:
    return SigningBlock(
        block_id=block_id,
        page_no=page_no,
        bbox=bbox,
        block_role=SigningBlockRole.BOTH_PARTIES,
        confidence=0.9,
        confidence_level=SigningBlockConfidenceLevel.HIGH,
        confidence_reasons=["test"],
        source_block_ids=source_block_ids or [],
        text=text,
        exclude_from_clause_diff=exclude_from_clause_diff,
    )


def test_builder_removes_high_confidence_signing_block_blocks_only() -> None:
    body = TextBlock(
        block_id="body",
        page_no=10,
        text="14.2 正文条款",
        bbox=_bbox(80, 550, 520, 590),
    )
    signing = TextBlock(
        block_id="signing",
        page_no=10,
        text="甲方：A 乙方：B\n(盖章)\n(签字)\n日期：",
        bbox=_bbox(65, 620, 485, 730),
    )
    page = Page(page_no=10, width=595, height=842, blocks=[body, signing])
    doc = Document(filename="x.pdf", path="x.pdf", page_count=1, pages=[page])
    block = _signing_block(
        "SB-10-1",
        10,
        _bbox(60, 610, 500, 740),
        source_block_ids=["signing"],
        text=signing.text,
    )

    result = SigningClauseDocumentBuilder().build(doc, [block])

    assert [b.block_id for b in result.document.pages[0].blocks] == ["body"]
    assert [b.block_id for b in doc.pages[0].blocks] == ["body", "signing"]
    assert result.document is not doc
    assert result.document.pages[0] is not page
    assert result.excluded_block_ids == ["signing"]
    assert result.entries[0]["reason"] == "high_confidence_signing_block"


def test_builder_keeps_blocks_when_signing_block_is_not_excludable() -> None:
    body = TextBlock(
        block_id="body",
        page_no=10,
        text="14.2 正文条款",
        bbox=_bbox(80, 550, 520, 590),
    )
    signing = TextBlock(
        block_id="signing",
        page_no=10,
        text="甲方：A 乙方：B\n(盖章)",
        bbox=_bbox(65, 620, 485, 700),
    )
    page = Page(page_no=10, width=595, height=842, blocks=[body, signing])
    doc = Document(filename="x.pdf", path="x.pdf", page_count=1, pages=[page])
    block = _signing_block(
        "SB-10-1",
        10,
        _bbox(60, 610, 500, 740),
        source_block_ids=["signing"],
        exclude_from_clause_diff=False,
    )

    result = SigningClauseDocumentBuilder().build(doc, [block])

    assert [b.block_id for b in result.document.pages[0].blocks] == ["body", "signing"]
    assert result.excluded_block_ids == []
    assert result.entries == []


def test_builder_matches_by_bbox_overlap_when_source_block_id_is_missing() -> None:
    body = TextBlock(
        block_id="body",
        page_no=10,
        text="14.2 正文条款",
        bbox=_bbox(80, 550, 520, 590),
    )
    signing = TextBlock(
        block_id="signing",
        page_no=10,
        text="甲方：A 乙方：B\n(盖章)",
        bbox=_bbox(70, 620, 470, 720),
    )
    page = Page(page_no=10, width=595, height=842, blocks=[body, signing])
    doc = Document(filename="x.pdf", path="x.pdf", page_count=1, pages=[page])
    block = _signing_block("SB-10-1", 10, _bbox(65, 615, 475, 725))

    result = SigningClauseDocumentBuilder().build(doc, [block])

    assert [b.block_id for b in result.document.pages[0].blocks] == ["body"]
    assert result.excluded_block_ids == ["signing"]
    assert result.entries == [
        {
            "page_no": 10,
            "block_id": "signing",
            "signing_block_id": "SB-10-1",
            "reason": "high_confidence_signing_block",
        }
    ]
