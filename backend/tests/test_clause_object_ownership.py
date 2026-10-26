"""Contract boundaries and seal ownership must precede clause comparison."""

import fitz
import pytest

from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter
from app.services.document_preparation import DocumentPreparer, DocumentPreparationResult
from app.services.signing_region.clause_document import SigningClauseDocumentResult
from app.services.signing_region.models import SigningVisualObject, SigningVisualObjectType


def block(id, text, page=1, y=100, kind="text"):
    return TextBlock(block_id=id, page_no=page, text=text, bbox=BBox(x0=60, y0=y, x1=360, y1=y + 16), block_type=kind)


def prepared_clauses(first, second):
    document = Document(
        filename="test.pdf",
        path="test.pdf",
        page_count=2,
        pages=[
            Page(page_no=1, width=600, height=800, blocks=first),
            Page(page_no=2, width=600, height=800, blocks=second),
        ],
    )
    preparer = DocumentPreparer()
    active = None
    for page in document.pages:
        active = preparer._classify_page_structure(page, "compare", DocumentPreparationResult(), active)
    return ClauseSplitter().split(document, "N")


def test_quotation_company_heading_starts_new_document_not_previous_clause():
    clauses = prepared_clauses(
        [block("clause", "22.5本合同一式两份，双方各持一份，传真件有效。")],
        [
            block("company", "示例科技股份有限公司", 2, 70, "doc_title"),
            block("quote", "自动控制系统报价明细", 2, 90, "paragraph_title"),
            block("table", "序号 名称 数量 单价 合计", 2, 160, "table"),
        ],
    )
    previous = next(c for c in clauses if c.clause_no == "22.5")
    assert previous.page_numbers == [1]
    assert "示例" not in previous.text
    assert any("示例" in c.text and c.section_type == "quote" for c in clauses)


def test_real_cross_page_clause_and_midpage_quote_are_not_cut_at_page_start():
    clauses = prepared_clauses(
        [block("clause", "22.5交付内容包括：")],
        [
            block("continuation", "系统设备及配套服务。", 2, 70),
            block("quote", "设备报价明细", 2, 350, "paragraph_title"),
            block("table", "数量 单价 合计", 2, 420, "table"),
        ],
    )
    previous = next(c for c in clauses if c.clause_no == "22.5")
    assert "系统设备及配套服务" in previous.text
    assert previous.page_numbers == [1, 2]


@pytest.mark.parametrize(
    "ink,layout,expected",
    [((0.8, 0.02, 0.02), "", True), ((0, 0, 0), "", False), ((0.8, 0.02, 0.02), "body-region", False)],
)
def test_visual_seal_ownership_requires_ink_and_preserves_printed_body(tmp_path, ink, layout, expected):
    from app.services.signing_region.text_ownership import reconcile_visual_seal_text

    path = tmp_path / "seal.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=300)
    page.insert_text((100, 100), "STAMP", fontsize=14, color=ink)
    spans = page.get_text("rawdict")["blocks"][0]["lines"][0]["spans"][0]
    chars = [
        CharBox(
            char=c["c"],
            page_no=1,
            bbox=BBox(x0=c["bbox"][0], y0=c["bbox"][1], x1=c["bbox"][2], y1=c["bbox"][3]),
            text_index=i,
        )
        for i, c in enumerate(spans["chars"])
    ]
    box = BBox(x0=95, y0=80, x1=160, y1=105)
    pdf.save(path)
    pdf.close()
    text = TextBlock(
        block_id="ocr", page_no=1, text="STAMP", bbox=box, char_boxes=chars, source="PP-OCRv6", layout_block_id=layout
    )
    document = Document(
        filename=path.name, path=str(path), page_count=1, pages=[Page(page_no=1, width=300, height=300, blocks=[text])]
    )
    result = SigningClauseDocumentResult(document)
    obj = SigningVisualObject(
        object_id="seal",
        object_type=SigningVisualObjectType.OTHER_STAMP,
        page_no=1,
        bbox=box,
        visual_detection_confidence=0.9,
    )
    reconcile_visual_seal_text(result, [obj], path)
    assert ("ocr" in result.excluded_block_ids) == expected
    assert bool(result.document.pages[0].blocks) is not expected


def test_two_quotation_entries_remain_distinct_without_numbered_body():
    def quotation(page, name):
        return [
            block(f"company{page}", name, page, 70, "doc_title"),
            block(f"quote{page}", "自动控制系统报价明细", page, 90, "paragraph_title"),
            block(f"table{page}", "序号 名称 数量 单价 合计", page, 160, "table"),
        ]

    clauses = prepared_clauses(quotation(1, "第一家公司"), quotation(2, "第二家公司"))
    assert not any("第一家公司" in c.text and "第二家公司" in c.text for c in clauses)
    assert any("第一家公司" in c.text for c in clauses)
    assert any("第二家公司" in c.text for c in clauses)


def test_new_document_boundary_wins_over_bare_number_paragraph_continuation():
    from app.services.clause_splitter import ClauseUnit
    from app.services.clause_paragraphs import ParagraphBuilder
    from app.models import EvidenceBox

    box = BBox(x0=50, y0=50, x1=250, y1=70)
    a = ClauseUnit(
        text="1",
        char_boxes=[],
        page_no=1,
        block_id="a",
        block_type="text",
        bbox=box,
        evidence=EvidenceBox(page_no=1, bbox=box),
    )
    b = ClauseUnit(
        text="报价明细",
        char_boxes=[],
        page_no=1,
        block_id="b",
        block_type="doc_title",
        bbox=box,
        evidence=EvidenceBox(page_no=1, bbox=box),
        split_flags=("DOCUMENT_ENTRY_BOUNDARY",),
    )
    assert len(ParagraphBuilder().build([a, b], parse_marker=lambda text: ("1", "") if text == "1" else None)) == 2


def test_black_body_crossed_by_red_stamp_is_preserved(tmp_path):
    from app.services.signing_region.text_ownership import reconcile_visual_seal_text

    path = tmp_path / "overlap.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=300)
    page.insert_text((80, 100), "10000", fontsize=14, color=(0, 0, 0))
    spans = page.get_text("rawdict")["blocks"][0]["lines"][0]["spans"][0]
    chars = [
        CharBox(
            char=c["c"],
            page_no=1,
            bbox=BBox(x0=c["bbox"][0], y0=c["bbox"][1], x1=c["bbox"][2], y1=c["bbox"][3]),
            text_index=i,
        )
        for i, c in enumerate(spans["chars"])
    ]
    page.draw_line((75, 94), (125, 94), color=(0.8, 0, 0), width=2)
    pdf.save(path)
    pdf.close()
    box = BBox(x0=75, y0=80, x1=125, y1=105)
    text = TextBlock(block_id="amount", page_no=1, text="10000", bbox=box, char_boxes=chars, source="PP-OCRv6")
    doc = Document(
        filename=path.name, path=str(path), page_count=1, pages=[Page(page_no=1, width=300, height=300, blocks=[text])]
    )
    result = SigningClauseDocumentResult(doc)
    obj = SigningVisualObject(
        object_id="seal",
        object_type=SigningVisualObjectType.OTHER_STAMP,
        page_no=1,
        bbox=box,
        visual_detection_confidence=0.9,
    )
    reconcile_visual_seal_text(result, [obj], path)
    assert result.document.pages[0].blocks[0].text == "10000"
    assert not result.excluded_block_ids
