from __future__ import annotations

from app.models import BBox, Document, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter
from app.services.document_preparation import DocumentPreparer


def test_appendix_section_flow_uses_visual_order_and_keeps_numbered_headings() -> None:
    document = Document(
        filename="sample.pdf",
        path="sample.pdf",
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="appendix-chapter",
                        page_no=1,
                        text="一、系统开放性与可配置性要求",
                        bbox=BBox(x0=84, y0=117, x1=230, y1=126),
                        block_type="paragraph_title",
                        block_role="paragraph_title",
                        flow_role="heading",
                        reading_order=1,
                        layout_order=2,
                    ),
                    TextBlock(
                        block_id="requirement-title",
                        page_no=1,
                        text="1. 预测文件上报接口开放",
                        bbox=BBox(x0=103, y0=139, x1=230, y1=149),
                        block_type="paragraph_title",
                        block_role="paragraph_title",
                        flow_role="heading",
                        reading_order=2,
                        layout_order=3,
                    ),
                    TextBlock(
                        block_id="appendix-title",
                        page_no=1,
                        text="附件一技术服务条款",
                        bbox=BBox(x0=230, y0=69, x1=401, y1=86),
                        block_type="paragraph_title",
                        block_role="paragraph_title",
                        flow_role="heading",
                        reading_order=3,
                        layout_order=1,
                    ),
                    TextBlock(
                        block_id="requirement-body",
                        page_no=1,
                        text="乙方须提供功率预测系统完整的文件上报接口。",
                        bbox=BBox(x0=84, y0=162, x1=535, y1=172),
                        reading_order=4,
                        layout_order=4,
                    ),
                ],
            )
        ],
    )

    DocumentPreparer().prepare(document, "original")
    clauses = ClauseSplitter().split(document, "O")

    assert [clause.section_type for clause in clauses] == ["appendix", "appendix", "appendix"]
    assert [clause.source_block_ids for clause in clauses] == [
        ["appendix-title"],
        ["appendix-chapter"],
        ["requirement-title", "requirement-body"],
    ]
    assert clauses[1].title == "系统开放性与可配置性要求"
    assert clauses[2].clause_no == "1"
    assert all("SECTION_APPENDIX" in clause.split_flags for clause in clauses)
