from __future__ import annotations

from pathlib import Path

import fitz

from app.models import BBox, Document, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter
from app.services.document_preparation import DocumentPreparer
from app.services.native_heading_repair import NativeHeadingRepairService, load_native_heading_index


def _write_pdf(path: Path, lines: list[tuple[float, str]]) -> None:
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    for y, text in lines:
        page.insert_text((72, y), text, fontname="china-s", fontsize=12)
    pdf.save(path)
    pdf.close()


def _write_split_line_pdf(path: Path, lines: list[tuple[float, float, str]]) -> None:
    _write_styled_line_pdf(
        path,
        [
            (x, baseline, text, 12, "helv" if text.isascii() else "china-s")
            for x, baseline, text in lines
        ],
    )


def _write_styled_line_pdf(
    path: Path,
    lines: list[tuple[float, float, str, float, str]],
) -> None:
    _write_styled_pages_pdf(path, [lines])


def _write_styled_pages_pdf(
    path: Path,
    pages: list[list[tuple[float, float, str, float, str]]],
) -> None:
    pdf = fitz.open()
    for lines in pages:
        page = pdf.new_page(width=595, height=842)
        for x, baseline, text, font_size, fontname in lines:
            page.insert_textbox(
                fitz.Rect(x, baseline - font_size - 2, x + 400, baseline + font_size + 4),
                text,
                fontname=fontname,
                fontsize=font_size,
            )
    pdf.save(path)
    pdf.close()


def _ocr_document(
    path: Path,
    heading: str = "8.",
    *,
    title_type: str = "paragraph_title",
    context_text: str = "8.1 甲方拥有工作成果。",
) -> Document:
    return Document(
        filename=path.name,
        path=str(path),
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="heading",
                        page_no=1,
                        text=heading,
                        bbox=BBox(x0=70, y0=82, x1=92, y1=102),
                        block_type=title_type,
                        source="ppocrv5",
                    ),
                    TextBlock(
                        block_id="child",
                        page_no=1,
                        text=context_text,
                        bbox=BBox(x0=72, y0=116, x1=360, y1=138),
                    ),
                ],
            )
        ],
    )


def test_repairs_bare_number_from_exact_native_heading(tmp_path: Path) -> None:
    path = tmp_path / "native.pdf"
    _write_pdf(path, [(96, "8. 知识产权"), (130, "8.1 甲方拥有工作成果。")])
    document = _ocr_document(path)

    result = NativeHeadingRepairService().repair(document)

    heading = document.pages[0].blocks[0]
    assert heading.text == "8. 知识产权"
    assert heading.bbox.x1 > 92
    assert heading.char_boxes
    assert "native_heading_repair" in heading.source
    assert "native_heading_repair:8" in heading.semantic_reasons
    assert result.repaired_count == 1
    assert result.decisions[0]["action"] == "repaired"


def test_inserts_missing_native_appendix_titles_before_clause_splitting(tmp_path: Path) -> None:
    path = tmp_path / "appendix-title-missed-by-ocr.pdf"
    _write_styled_line_pdf(
        path,
        [
            (72, 80, "附件一", 15, "china-s"),
            (220, 108, "安全生产管理协议（模板）", 16, "china-s"),
            (72, 148, "项目名称：新能源场站功率预测系统服务", 12, "china-s"),
            (72, 180, "为贯彻安全第一、预防为主的方针，双方订立本协议。", 12, "china-s"),
        ],
    )
    document = Document(
        filename=path.name,
        path=str(path),
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="ocr-body-1",
                        page_no=1,
                        text="项目名称：新能源场站功率预测系统服务",
                        bbox=BBox(x0=72, y0=130, x1=330, y1=150),
                        source="ppocrv5",
                    ),
                    TextBlock(
                        block_id="ocr-body-2",
                        page_no=1,
                        text="为贯彻安全第一、预防为主的方针，双方订立本协议。",
                        bbox=BBox(x0=72, y0=162, x1=420, y1=182),
                        source="ppocrv5",
                    ),
                ],
            )
        ],
    )

    result = NativeHeadingRepairService().repair(document)

    assert [block.text for block in document.pages[0].blocks[:2]] == [
        "附件一",
        "安全生产管理协议(模板)",
    ]
    assert all(block.block_type == "paragraph_title" for block in document.pages[0].blocks[:2])
    assert all("native_section_title_repair" in block.source for block in document.pages[0].blocks[:2])
    assert result.repaired_count == 2

    DocumentPreparer().prepare(document, "original")
    clauses = ClauseSplitter().split(document, "O")

    assert all(clause.section_type == "appendix" for clause in clauses)


def test_inserts_missing_multiline_native_document_title_without_title_keywords(tmp_path: Path) -> None:
    path = tmp_path / "multiline-title-missed-by-ocr.pdf"
    _write_styled_line_pdf(
        path,
        [
            (80, 110, "附件五：技术协议", 16, "china-s"),
            (118, 210, "某公司 2026 年新能源项目", 22, "china-s"),
            (124, 262, "系统授权服务单一来源项目", 22, "china-s"),
            (278, 314, "实施文件", 22, "china-s"),
            (163, 560, "甲方：某公司", 14, "china-s"),
            (163, 596, "乙方：服务商", 14, "china-s"),
            (163, 638, "签订地点：某市", 14, "china-s"),
        ],
    )
    document = Document(
        filename=path.name,
        path=str(path),
        page_count=1,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="ocr-appendix",
                        page_no=1,
                        text="附件五：技术协议",
                        bbox=BBox(x0=80, y0=92, x1=210, y1=114),
                        source="ppocrv5",
                    ),
                    TextBlock(
                        block_id="ocr-year-fragment",
                        page_no=1,
                        text="2026",
                        bbox=BBox(x0=278, y0=190, x1=330, y1=212),
                        source="ppocrv5",
                    ),
                    TextBlock(
                        block_id="ocr-party-a",
                        page_no=1,
                        text="甲方：某公司",
                        bbox=BBox(x0=163, y0=540, x1=300, y1=562),
                        source="ppocrv5",
                    ),
                    TextBlock(
                        block_id="ocr-party-b",
                        page_no=1,
                        text="乙方：服务商",
                        bbox=BBox(x0=163, y0=576, x1=300, y1=598),
                        source="ppocrv5",
                    ),
                ],
            )
        ],
    )

    result = NativeHeadingRepairService().repair(document)

    inserted_titles = [
        block.text
        for block in document.pages[0].blocks
        if block.source == "native_section_title_repair"
    ]
    assert inserted_titles == [
        "某公司 2026 年新能源项目",
        "系统授权服务单一来源项目",
        "实施文件",
    ]
    assert all(
        block.block_type == "text"
        for block in document.pages[0].blocks
        if block.source == "native_section_title_repair"
    )
    assert result.repaired_count == 3


def test_inserts_missing_cover_document_title_as_cover_metadata(tmp_path: Path) -> None:
    path = tmp_path / "cover-title-missed-by-ocr.pdf"
    _write_styled_pages_pdf(
        path,
        [
            [
                (72, 86, "合同编号：CYSZ-001", 12, "china-s"),
                (120, 220, "某公司 2026 年新能源场站", 22, "china-s"),
                (124, 272, "功率预测系统授权服务", 22, "china-s"),
                (238, 324, "合同", 22, "china-s"),
                (72, 500, "合同主要内容以双方约定为准。", 12, "china-s"),
            ],
            [
                (72, 96, "1. 定义", 14, "china-s"),
                (72, 132, "本合同术语含义如下。", 12, "china-s"),
            ],
        ],
    )
    document = Document(
        filename=path.name,
        path=str(path),
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="ocr-contract-no",
                        page_no=1,
                        text="合同编号：CYSZ-001",
                        bbox=BBox(x0=72, y0=64, x1=240, y1=88),
                        source="ppocrv5",
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="ocr-heading",
                        page_no=2,
                        text="1. 定义",
                        bbox=BBox(x0=72, y0=76, x1=160, y1=100),
                        block_type="paragraph_title",
                        source="ppocrv5",
                    ),
                    TextBlock(
                        block_id="ocr-body",
                        page_no=2,
                        text="本合同术语含义如下。",
                        bbox=BBox(x0=72, y0=112, x1=250, y1=136),
                        source="ppocrv5",
                    ),
                ],
            ),
        ],
    )

    NativeHeadingRepairService().repair(document)

    inserted_titles = [
        block
        for block in document.pages[0].blocks
        if block.source == "native_section_title_repair"
    ]
    assert [block.text for block in inserted_titles] == [
        "某公司 2026 年新能源场站",
        "功率预测系统授权服务",
        "合同",
    ]
    assert all(block.block_type == "doc_title" for block in inserted_titles)

    clauses = ClauseSplitter().split(document, "O")

    assert [clause.title for clause in clauses] == ["定义"]


def test_inserts_native_attachment_catalog_and_keeps_following_title_in_one_clause(tmp_path: Path) -> None:
    path = tmp_path / "attachment-catalog-missed-by-ocr.pdf"
    _write_styled_pages_pdf(
        path,
        [
            [
                (72, 86, "签署页", 14, "china-s"),
                (72, 700, "附件一：安全生产管理协议", 12, "china-s"),
                (72, 726, "附件二：廉洁协议书", 12, "china-s"),
            ],
            [
                (72, 86, "附件一", 15, "china-s"),
                (180, 116, "安全生产管理协议（模板）", 16, "china-s"),
                (72, 164, "项目名称：新能源场站功率预测系统服务", 12, "china-s"),
                (72, 196, "双方应当遵守安全生产管理要求。", 12, "china-s"),
            ],
        ],
    )
    document = Document(
        filename=path.name,
        path=str(path),
        page_count=2,
        pages=[
            Page(
                page_no=1,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="ocr-signing",
                        page_no=1,
                        text="签署页",
                        bbox=BBox(x0=72, y0=68, x1=130, y1=90),
                        source="ppocrv5",
                    )
                ],
            ),
            Page(
                page_no=2,
                width=595,
                height=842,
                blocks=[
                    TextBlock(
                        block_id="ocr-project",
                        page_no=2,
                        text="项目名称：新能源场站功率预测系统服务",
                        bbox=BBox(x0=72, y0=146, x1=350, y1=168),
                        source="ppocrv5",
                    ),
                    TextBlock(
                        block_id="ocr-body",
                        page_no=2,
                        text="双方应当遵守安全生产管理要求。",
                        bbox=BBox(x0=72, y0=178, x1=330, y1=200),
                        source="ppocrv5",
                    ),
                ],
            ),
        ],
    )

    NativeHeadingRepairService().repair(document)

    assert [block.text for block in document.pages[0].blocks] == [
        "签署页",
        "附件一:安全生产管理协议",
        "附件二:廉洁协议书",
    ]
    inserted = [
        block
        for page in document.pages
        for block in page.blocks
        if block.source == "native_section_title_repair"
    ]
    assert all(block.block_type == "text" for block in inserted)

    DocumentPreparer().prepare(document, "original")
    clauses = ClauseSplitter().split(document, "O")
    appendix_clauses = [clause for clause in clauses if clause.section_type == "appendix"]

    assert len(appendix_clauses) == 1
    assert "附件一:安全生产管理协议" in appendix_clauses[0].text
    assert "附件一\n安全生产管理协议(模板)" in appendix_clauses[0].text


def test_repairs_ordinary_text_block_from_split_native_heading_lines(tmp_path: Path) -> None:
    path = tmp_path / "split-native.pdf"
    _write_styled_line_pdf(
        path,
        [
            (72, 96, "18.", 15, "helv"),
            (106, 96, "份数", 15, "china-s"),
            (92, 132, "双方按本条约定履行义务。", 11.99, "china-s"),
            (92, 160, "双方继续履行其他约定义务。", 12.01, "china-s"),
        ],
    )
    document = _ocr_document(
        path,
        "18.",
        title_type="text",
        context_text="19. 特别约定",
    )

    result = NativeHeadingRepairService().repair(document)

    heading = document.pages[0].blocks[0]
    assert heading.text == "18. 份数"
    assert heading.block_type == "text"
    assert heading.semantic_reasons == ["native_heading_repair:18"]
    assert [(box.char, box.text_index) for box in heading.char_boxes] == [
        ("1", 0),
        ("8", 1),
        (".", 2),
        ("份", 4),
        ("数", 5),
    ]
    assert result.repaired_count == 1
    assert result.decisions == [
        {
            "action": "repaired",
            "page_no": 1,
            "number": "18",
            "title": "份数",
            "block_id": "heading",
            "reason": "exact_native_heading",
        }
    ]

    repeated = NativeHeadingRepairService().repair(document)

    assert repeated.repaired_count == 0
    assert heading.semantic_reasons == ["native_heading_repair:18"]


def test_repairs_page_bottom_split_heading_with_adjacent_page_body_style(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cross-page-body-style.pdf"
    _write_styled_pages_pdf(
        path,
        [
            [
                (72, 790, "2.", 15, "helv"),
                (106, 790, "服务内容", 15, "china-s"),
            ],
            [
                (92, 80, "乙方应按合同约定提供技术服务。", 12, "china-s"),
                (92, 108, "双方应及时确认具体服务成果。", 12, "china-s"),
            ],
        ],
    )
    document = _ocr_document(path, "2.", context_text="2.1 乙方提供技术服务。")
    document.pages[0].blocks[0].bbox = BBox(x0=70, y0=772, x1=92, y1=794)

    result = NativeHeadingRepairService().repair(document)

    assert load_native_heading_index(path).contains_exact("2", "服务内容", {1})
    assert document.pages[0].blocks[0].text == "2. 服务内容"
    assert result.repaired_count == 1


def test_repairs_page_bottom_heading_with_unpunctuated_wrapped_body_style(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cross-page-unpunctuated-body-style.pdf"
    _write_styled_pages_pdf(
        path,
        [
            [
                (72, 790, "2.", 15, "helv"),
                (106, 790, "服务内容", 15, "china-s"),
            ],
            [
                (92, 80, "乙方应按合同约定提供技术服务", 12, "china-s"),
                (92, 108, "双方应及时确认具体服务成果", 12, "china-s"),
            ],
        ],
    )
    document = _ocr_document(path, "2.", context_text="2.1 乙方提供技术服务。")
    document.pages[0].blocks[0].bbox = BBox(x0=70, y0=772, x1=92, y1=794)

    result = NativeHeadingRepairService().repair(document)

    assert load_native_heading_index(path).contains_exact("2", "服务内容", {1})
    assert document.pages[0].blocks[0].text == "2. 服务内容"
    assert result.repaired_count == 1


def test_rejects_page_bottom_split_heading_without_next_page_body_style(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-next-page-body-style.pdf"
    _write_styled_pages_pdf(
        path,
        [
            [
                (72, 790, "2.", 15, "helv"),
                (106, 790, "服务内容", 15, "china-s"),
            ],
            [],
        ],
    )

    assert not load_native_heading_index(path).contains_exact("2", "服务内容", {1})


def test_rejects_non_adjacent_page_body_style_for_bottom_split_heading(
    tmp_path: Path,
) -> None:
    path = tmp_path / "non-adjacent-body-style.pdf"
    _write_styled_pages_pdf(
        path,
        [
            [
                (72, 790, "2.", 15, "helv"),
                (106, 790, "服务内容", 15, "china-s"),
            ],
            [],
            [
                (92, 80, "乙方应按合同约定提供技术服务。", 12, "china-s"),
                (92, 108, "双方应及时确认具体服务成果。", 12, "china-s"),
            ],
        ],
    )

    assert not load_native_heading_index(path).contains_exact("2", "服务内容", {1})


def test_rejects_next_page_body_style_after_new_heading_boundary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "next-page-new-heading-boundary.pdf"
    _write_styled_pages_pdf(
        path,
        [
            [
                (72, 790, "2.", 15, "helv"),
                (106, 790, "服务内容", 15, "china-s"),
            ],
            [
                (72, 80, "3. 费用结算", 15, "china-s"),
                (92, 112, "结算项目数量单价金额以清单为准。", 12, "china-s"),
                (92, 140, "双方应及时核对结算项目明细。", 12, "china-s"),
            ],
        ],
    )

    assert not load_native_heading_index(path).contains_exact("2", "服务内容", {1})


def test_does_not_stitch_same_baseline_short_body_as_native_title(tmp_path: Path) -> None:
    path = tmp_path / "same-baseline-body.pdf"
    _write_styled_line_pdf(
        path,
        [
            (72, 96, "18.", 12, "helv"),
            (106, 96, "双方应按合同约定履行", 12, "china-s"),
            (92, 132, "注：本页说明。", 10, "china-s"),
            (92, 160, "本合同其他条款继续有效。", 12, "china-s"),
            (92, 188, "双方应依约履行各自义务。", 12, "china-s"),
        ],
    )
    document = _ocr_document(path, "18.", context_text="19. 特别约定")

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "18."
    assert result.repaired_count == 0
    assert not load_native_heading_index(path).contains_exact(
        "18",
        "双方应按合同约定履行",
        {1},
    )


def test_does_not_stitch_when_smaller_table_text_outweighs_main_body(
    tmp_path: Path,
) -> None:
    path = tmp_path / "table-style-pollution.pdf"
    _write_styled_line_pdf(
        path,
        [
            (72, 96, "18.", 12, "helv"),
            (106, 96, "双方应按合同约定履行", 12, "china-s"),
            (92, 132, "表格第一行数据说明及金额数量以附件为准。", 10, "china-s"),
            (92, 156, "表格第二行数据说明及金额数量以附件为准。", 10, "china-s"),
            (92, 180, "表格第三行数据说明及金额数量以附件为准。", 10, "china-s"),
            (92, 204, "本合同其他条款继续有效。", 12, "china-s"),
            (92, 228, "双方应依约履行各自义务。", 12, "china-s"),
        ],
    )
    document = _ocr_document(path, "18.", context_text="19. 特别约定")

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "18."
    assert result.repaired_count == 0
    assert not load_native_heading_index(path).contains_exact(
        "18",
        "双方应按合同约定履行",
        {1},
    )


def test_does_not_stitch_vertically_separated_native_lines(tmp_path: Path) -> None:
    path = tmp_path / "vertically-separated.pdf"
    _write_split_line_pdf(path, [(72, 96, "18."), (106, 128, "份数")])
    document = _ocr_document(path, "18.", context_text="19. 特别约定")

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "18."
    assert result.repaired_count == 0
    assert not load_native_heading_index(path).contains_exact("18", "份数", {1})


def test_does_not_stitch_ambiguous_same_baseline_native_titles(tmp_path: Path) -> None:
    path = tmp_path / "ambiguous-split.pdf"
    _write_split_line_pdf(
        path,
        [(72, 96, "18."), (106, 96, "份数"), (112, 96, "合同文本")],
    )
    document = _ocr_document(path, "18.", context_text="19. 特别约定")

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "18."
    assert result.repaired_count == 0
    assert not load_native_heading_index(path).contains_exact("18", "份数", {1})


def test_repairs_parent_heading_when_ocr_contains_decimal_children(tmp_path: Path) -> None:
    path = tmp_path / "decimal-children.pdf"
    _write_pdf(path, [(96, "8. 知识产权"), (130, "8.1 甲方拥有工作成果。")])
    document = _ocr_document(path, context_text="8.1 甲方拥有工作成果。")
    document.pages[0].blocks.append(
        TextBlock(
            block_id="child-2",
            page_no=1,
            text="8.2 乙方保证交付成果不存在权利瑕疵。",
            bbox=BBox(x0=72, y0=146, x1=420, y1=168),
        )
    )

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "8. 知识产权"
    assert result.repaired_count == 1
    assert result.decisions[0]["action"] == "repaired"


def test_repairs_parent_heading_when_same_marker_line_is_ordinary_body(
    tmp_path: Path,
) -> None:
    path = tmp_path / "same-marker-body.pdf"
    _write_pdf(path, [(96, "8. 知识产权"), (130, "8.1 甲方拥有工作成果。")])
    document = _ocr_document(path)
    document.pages[0].blocks.append(
        TextBlock(
            block_id="same-marker-body",
            page_no=1,
            text="8. 双方应按约履行。",
            bbox=BBox(x0=72, y0=146, x1=260, y1=168),
            block_type="text",
        )
    )

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "8. 知识产权"
    assert result.repaired_count == 1


def test_does_not_replace_conflicting_ocr_title(tmp_path: Path) -> None:
    path = tmp_path / "conflict.pdf"
    _write_pdf(path, [(96, "8. 知识产权"), (130, "8.1 甲方拥有工作成果。")])
    document = _ocr_document(path)
    document.pages[0].blocks.append(
        TextBlock(
            block_id="conflicting-title",
            page_no=1,
            text="8. 保密",
            bbox=BBox(x0=72, y0=106, x1=180, y1=128),
            block_type="paragraph_title",
        )
    )

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "8."
    assert result.repaired_count == 0
    assert result.decisions[0]["reason"] == "conflicting_ocr_title"


def test_keeps_explicit_same_level_title_with_value_and_colon(tmp_path: Path) -> None:
    path = tmp_path / "explicit-value-title-conflict.pdf"
    _write_pdf(path, [(96, "8. 知识产权"), (130, "8.1 甲方拥有工作成果。")])
    document = _ocr_document(path)
    document.pages[0].blocks.append(
        TextBlock(
            block_id="explicit-value-title",
            page_no=1,
            text="8. 服务期限12个月：",
            bbox=BBox(x0=72, y0=106, x1=280, y1=128),
            block_type="paragraph_title",
        )
    )

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "8."
    assert result.repaired_count == 0
    assert result.decisions[0]["reason"] == "conflicting_ocr_title"


def test_ignores_same_level_text_ending_in_continuation_comma(tmp_path: Path) -> None:
    path = tmp_path / "comma-body.pdf"
    _write_pdf(path, [(96, "8. 知识产权"), (130, "8.1 甲方拥有工作成果。")])
    document = _ocr_document(path)
    document.pages[0].blocks.append(
        TextBlock(
            block_id="comma-body",
            page_no=1,
            text="8. 双方应按约履行，",
            bbox=BBox(x0=72, y0=146, x1=260, y1=168),
            block_type="text",
        )
    )

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "8. 知识产权"
    assert result.repaired_count == 1


def test_ignores_same_level_text_with_quoted_sentence_terminal(tmp_path: Path) -> None:
    path = tmp_path / "quoted-body.pdf"
    _write_pdf(path, [(96, "8. 知识产权"), (130, "8.1 甲方拥有工作成果。")])
    document = _ocr_document(path)
    document.pages[0].blocks.append(
        TextBlock(
            block_id="quoted-body",
            page_no=1,
            text="8. 双方应按约履行。”",
            bbox=BBox(x0=72, y0=146, x1=260, y1=168),
            block_type="text",
        )
    )

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "8. 知识产权"
    assert result.repaired_count == 1


def test_keeps_long_explicit_same_level_ocr_title(tmp_path: Path) -> None:
    path = tmp_path / "long-explicit-conflict.pdf"
    _write_pdf(path, [(96, "8. 知识产权"), (130, "8.1 甲方拥有工作成果。")])
    document = _ocr_document(path)
    document.pages[0].blocks.append(
        TextBlock(
            block_id="long-conflicting-title",
            page_no=1,
            text="8. 技术服务成果交付验收付款安排以及双方其他权利义务特别约定",
            bbox=BBox(x0=72, y0=106, x1=520, y1=128),
            block_type="paragraph_title",
        )
    )

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "8."
    assert result.repaired_count == 0
    assert result.decisions[0]["reason"] == "conflicting_ocr_title"


def test_keeps_long_unpunctuated_same_level_text_conflict(tmp_path: Path) -> None:
    path = tmp_path / "long-text-conflict.pdf"
    _write_pdf(path, [(96, "8. 知识产权"), (130, "8.1 甲方拥有工作成果。")])
    document = _ocr_document(path)
    document.pages[0].blocks.append(
        TextBlock(
            block_id="long-text-conflict",
            page_no=1,
            text="8. 技术服务成果交付验收付款安排以及双方其他权利义务特别约定",
            bbox=BBox(x0=72, y0=106, x1=520, y1=128),
            block_type="text",
        )
    )

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "8."
    assert result.repaired_count == 0
    assert result.decisions[0]["reason"] == "conflicting_ocr_title"


def test_rejects_wrong_number_and_amount_like_native_lines(tmp_path: Path) -> None:
    path = tmp_path / "wrong-or-value.pdf"
    _write_pdf(path, [(96, "9. 知识产权"), (160, "8. 100万元")])
    document = _ocr_document(path)

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "8."
    assert result.repaired_count == 0


def test_rejects_ambiguous_native_heading_and_fails_open(tmp_path: Path) -> None:
    ambiguous = tmp_path / "ambiguous.pdf"
    _write_pdf(ambiguous, [(96, "8. 知识产权"), (160, "8. 保密")])
    document = _ocr_document(ambiguous)

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "8."
    assert result.repaired_count == 0
    assert any(item["reason"] == "ambiguous_native_heading" for item in result.decisions)

    missing = _ocr_document(tmp_path / "missing.pdf")
    missing_result = NativeHeadingRepairService().repair(missing)
    assert missing.pages[0].blocks[0].text == "8."
    assert missing_result.warnings

    blank_path = tmp_path / "blank.pdf"
    blank_pdf = fitz.open()
    blank_pdf.new_page(width=595, height=842)
    blank_pdf.save(blank_path)
    blank_pdf.close()
    blank = _ocr_document(blank_path)
    blank_result = NativeHeadingRepairService().repair(blank)
    assert blank.pages[0].blocks[0].text == "8."
    assert blank_result.repaired_count == 0


def test_fails_open_when_native_page_extraction_raises(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "native-page-failure.pdf"
    _write_pdf(path, [(96, "8. 知识产权")])
    document = _ocr_document(path)
    original = document.model_dump()

    class FailingPage:
        def get_text(self, option: str) -> dict:
            raise RuntimeError(f"native page extraction failed for {option}")

    class FailingPdf:
        def __iter__(self):
            return iter([FailingPage()])

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.services.native_heading_repair.fitz.open", lambda _: FailingPdf())

    result = NativeHeadingRepairService().repair(document)

    assert document.model_dump() == original
    assert result.repaired_count == 0
    assert result.warnings
    assert "native PDF extraction failed" in result.warnings[0]


def test_native_heading_index_requires_exact_number_and_title(tmp_path: Path) -> None:
    path = tmp_path / "index.pdf"
    _write_pdf(path, [(96, "17. 合同生效"), (160, "18. 份数")])

    index = load_native_heading_index(path)

    assert index.contains_exact("17", "合同生效", {1})
    assert index.contains_exact("18", "份数", {1})
    assert not index.contains_exact("17", "份数", {1})
