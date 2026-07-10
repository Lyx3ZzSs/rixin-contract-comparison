from __future__ import annotations

from pathlib import Path

import fitz

from app.models import BBox, ClausePair, Document, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter
from app.services.diff.builder import build_diffs
from app.services.native_heading_repair import NativeHeadingRepairService
from app.services.repeated_overlay_filter import RepeatedOverlayFilter


HEADINGS = {
    1: [("2", "服务内容")],
    2: [("8", "知识产权"), ("9", "保密")],
    3: [("11", "合同变更、终止"), ("12", "不可抗力"), ("13", "索赔"), ("14", "违约责任")],
    4: [("17", "合同生效"), ("18", "份数")],
}
TARGET_NUMBERS = {number for headings in HEADINGS.values() for number, _ in headings}


def _write_native_pdf(path: Path) -> None:
    pdf = fitz.open()
    for page_no in range(1, 6):
        page = pdf.new_page(width=595, height=842)
        for index, (number, title) in enumerate(HEADINGS.get(page_no, [])):
            y = 96 + index * 140
            page.insert_textbox(
                fitz.Rect(72, y - 17, 104, y + 19),
                f"{number}.",
                fontname="helv",
                fontsize=15,
            )
            page.insert_textbox(
                fitz.Rect(106, y - 17, 260, y + 19),
                title,
                fontname="china-s",
                fontsize=15,
            )
            body_text = (
                "本合同一式伍份。"
                if number == "18"
                else f"{number}.1 双方按本条约定履行义务。"
            )
            page.insert_textbox(
                fitz.Rect(92, y + 22, 520, y + 52),
                body_text,
                fontname="china-s",
                fontsize=12,
            )
    pdf.save(path)
    pdf.close()


def _ocr_document(path: Path, *, complete_titles: bool, overlay: bool) -> Document:
    pages: list[Page] = []
    for page_no in range(1, 6):
        blocks: list[TextBlock] = []
        for index, (number, title) in enumerate(HEADINGS.get(page_no, [])):
            y0 = 82 + index * 140
            heading_text = f"{number}. {title}" if complete_titles else f"{number}."
            blocks.append(
                TextBlock(
                    block_id=f"p{page_no}-h{number}",
                    page_no=page_no,
                    text=heading_text,
                    bbox=BBox(x0=70, y0=y0, x1=190 if complete_titles else 92, y1=y0 + 22),
                    block_type="text" if number == "18" and not complete_titles else "paragraph_title",
                    source="ppocrv5",
                )
            )
            body_text = "本合同一式伍份。" if number == "18" else f"{number}.1 双方按本条约定履行义务。"
            blocks.append(
                TextBlock(
                    block_id=f"p{page_no}-b{number}",
                    page_no=page_no,
                    text=body_text,
                    bbox=BBox(x0=92, y0=y0 + 34, x1=500, y1=y0 + 58),
                )
            )
        if page_no == 4:
            blocks.append(
                TextBlock(
                    block_id="p4-h19",
                    page_no=4,
                    text="19. 特别约定",
                    bbox=BBox(x0=70, y0=380, x1=190, y1=404),
                    block_type="paragraph_title",
                )
            )
        if overlay:
            blocks.append(
                TextBlock(
                    block_id=f"p{page_no}-overlay",
                    page_no=page_no,
                    text="黄科",
                    bbox=BBox(x0=500, y0=780, x1=540, y1=800),
                    source="ppocrv5",
                )
            )
        pages.append(Page(page_no=page_no, width=595, height=842, blocks=blocks))
    return Document(filename=path.name, path=str(path), page_count=5, pages=pages)


def test_generated_heading_evidence_repair_removes_all_title_only_false_diffs(
    tmp_path: Path,
) -> None:
    original_path = tmp_path / "original.pdf"
    compare_path = tmp_path / "compare.pdf"
    _write_native_pdf(original_path)
    _write_native_pdf(compare_path)
    original = _ocr_document(original_path, complete_titles=False, overlay=False)
    compare = _ocr_document(compare_path, complete_titles=True, overlay=True)

    native_pdf = fitz.open(original_path)
    try:
        for page_no, headings in HEADINGS.items():
            raw = native_pdf[page_no - 1].get_text("rawdict")
            line_spans = [
                (
                    "".join(
                        str(char.get("c") or "")
                        for span in line.get("spans", [])
                        for char in span.get("chars", [])
                    ).strip(),
                    line.get("spans", []),
                )
                for block in raw.get("blocks", [])
                if block.get("type") == 0
                for line in block.get("lines", [])
            ]
            compact_lines = {text.replace(" ", "") for text, _ in line_spans}
            for number, title in headings:
                body_text = (
                    "本合同一式伍份。"
                    if number == "18"
                    else f"{number}.1 双方按本条约定履行义务。"
                )
                assert f"{number}." in compact_lines
                assert title in compact_lines
                assert f"{number}.{title}" not in compact_lines
                assert {
                    round(float(span.get("size") or 0), 1)
                    for text, spans in line_spans
                    if text.replace(" ", "") in {f"{number}.", title}
                    for span in spans
                } == {15.0}
                assert {
                    round(float(span.get("size") or 0), 1)
                    for text, spans in line_spans
                    if text.replace(" ", "") == body_text.replace(" ", "")
                    for span in spans
                } == {12.0}
    finally:
        native_pdf.close()

    heading_result = NativeHeadingRepairService().repair(original)
    overlay_result = RepeatedOverlayFilter().apply(compare)
    original_clauses = ClauseSplitter().split(original, "O")
    compare_clauses = ClauseSplitter().split(compare, "N")

    assert heading_result.repaired_count == 9
    assert overlay_result.filtered_block_count == 5
    assert next(
        block
        for page in original.pages
        for block in page.blocks
        if block.block_id == "p4-h18"
    ).block_type == "text"
    original_by_number = {
        item.clause_no: item for item in original_clauses if item.clause_no in TARGET_NUMBERS
    }
    compare_by_number = {
        item.clause_no: item for item in compare_clauses if item.clause_no in TARGET_NUMBERS
    }
    assert set(original_by_number) == TARGET_NUMBERS
    assert set(compare_by_number) == TARGET_NUMBERS
    assert original_by_number["13"].title == compare_by_number["13"].title == "索赔"
    assert original_by_number["18"].title == compare_by_number["18"].title == "份数"

    pairs = [
        ClausePair(
            original=original_by_number[number],
            compare=compare_by_number[number],
            score=100.0,
            match_method="same_clause_no_weighted",
            score_details={"alignment": {"body_similarity": 1.0}},
        )
        for number in sorted(TARGET_NUMBERS, key=int)
    ]

    assert build_diffs(pairs) == []
    assert all("黄科" not in clause.text for clause in compare_clauses)
    assert all(
        block.enter_clause_compare is False
        for page in compare.pages
        for block in page.blocks
        if block.text == "黄科"
    )
