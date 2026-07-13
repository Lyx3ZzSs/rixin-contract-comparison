from __future__ import annotations

from pathlib import Path

import fitz

from app.models import BBox, Document, Page, TextBlock
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
