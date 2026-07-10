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


def _ocr_document(path: Path, heading: str = "8.", *, title_type: str = "paragraph_title") -> Document:
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
                        text="8.1 甲方拥有工作成果。",
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


def test_does_not_replace_conflicting_ocr_title(tmp_path: Path) -> None:
    path = tmp_path / "conflict.pdf"
    _write_pdf(path, [(96, "8. 知识产权"), (130, "8.1 甲方拥有工作成果。")])
    document = _ocr_document(path, "8. 保密")

    result = NativeHeadingRepairService().repair(document)

    assert document.pages[0].blocks[0].text == "8. 保密"
    assert result.repaired_count == 0


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


def test_native_heading_index_requires_exact_number_and_title(tmp_path: Path) -> None:
    path = tmp_path / "index.pdf"
    _write_pdf(path, [(96, "17. 合同生效"), (160, "18. 份数")])

    index = load_native_heading_index(path)

    assert index.contains_exact("17", "合同生效", {1})
    assert index.contains_exact("18", "份数", {1})
    assert not index.contains_exact("17", "份数", {1})
