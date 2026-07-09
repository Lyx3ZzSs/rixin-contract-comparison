from __future__ import annotations

from app.config_models import DocumentUnderstandingSettings
from app.models import BBox, Document, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter
from app.services.document_preparation import DocumentPreparer
from app.services.document_understanding import DocumentUnderstandingService


def _block(
    block_id: str,
    text: str,
    y0: float,
    y1: float,
    *,
    block_type: str = "text",
    flow_role: str = "",
    confidence: float | None = 0.95,
) -> TextBlock:
    return TextBlock(
        block_id=block_id,
        page_no=1,
        text=text,
        bbox=BBox(x0=50, y0=y0, x1=500, y1=y1),
        block_type=block_type,
        flow_role=flow_role,
        confidence=confidence,
    )


def _document(blocks: list[TextBlock]) -> Document:
    return Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[Page(page_no=1, width=595, height=842, blocks=blocks)],
    )


def test_rule_understanding_excludes_toc_from_clause_split() -> None:
    document = _document([
        _block("toc-title", "目录", 80, 110, block_type="paragraph_title", flow_role="heading"),
        _block("toc-1", "一、服务内容与要求......3", 130, 160),
        _block("toc-2", "二、合同金额......5", 170, 200),
        _block("toc-3", "三、付款方式......8", 210, 240),
        _block("toc-4", "四、违约责任......10", 250, 280),
        _block("toc-5", "五、争议解决......12", 290, 320),
    ])

    result = DocumentUnderstandingService(DocumentUnderstandingSettings()).understand(document, "compare")
    clauses = ClauseSplitter().split(document, "N")

    assert result.semantic_decisions
    assert document.pages[0].semantic_role == "toc"
    assert all(block.enter_clause_compare is False for block in document.pages[0].blocks)
    assert clauses == []


def test_rule_understanding_routes_table_blocks_out_of_clause_compare() -> None:
    document = _document([
        _block("title", "1. 服务内容", 80, 110),
        _block("table", "序号 服务内容 数量 单价 金额", 130, 360, block_type="table", flow_role="table"),
    ])

    DocumentUnderstandingService(DocumentUnderstandingSettings()).understand(document, "original")
    clauses = ClauseSplitter().split(document, "O")

    assert document.pages[0].blocks[1].semantic_role == "table_body"
    assert document.pages[0].blocks[1].enter_clause_compare is False
    assert len(clauses) == 1
    assert clauses[0].source_block_ids == ["title"]


def test_rule_understanding_keeps_quote_page_for_section_clause_split() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="quote-title",
                        page_no=2,
                        text="报价表格式",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=110),
                    ),
                    TextBlock(
                        block_id="quote-body",
                        page_no=2,
                        text="项目名称：风功率预测服务",
                        bbox=BBox(x0=50, y0=130, x1=500, y1=160),
                    ),
                ],
            )
        ],
    )

    DocumentUnderstandingService(DocumentUnderstandingSettings()).understand(document, "compare")
    DocumentPreparer().prepare(document, "compare")
    clauses = ClauseSplitter().split(document, "N")

    assert document.pages[0].semantic_role == "quote"
    assert [clause.section_type for clause in clauses] == ["quote"]
    assert clauses[0].source_block_ids == ["quote-title", "quote-body"]
    assert "SECTION_QUOTE" in clauses[0].split_flags


def test_rule_understanding_keeps_appendix_page_for_section_clause_split() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=2,
        pages=[
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="appendix-title",
                        page_no=2,
                        text="附件一：技术规范",
                        bbox=BBox(x0=50, y0=80, x1=500, y1=110),
                    ),
                    TextBlock(
                        block_id="appendix-body",
                        page_no=2,
                        text="设备应满足风功率预测要求。",
                        bbox=BBox(x0=50, y0=130, x1=500, y1=160),
                    ),
                ],
            )
        ],
    )

    DocumentUnderstandingService(DocumentUnderstandingSettings()).understand(document, "compare")
    DocumentPreparer().prepare(document, "compare")
    clauses = ClauseSplitter().split(document, "N")

    assert document.pages[0].semantic_role == "appendix"
    assert [clause.section_type for clause in clauses] == ["appendix"]
    assert clauses[0].source_block_ids == ["appendix-title", "appendix-body"]
    assert "SECTION_APPENDIX" in clauses[0].split_flags
