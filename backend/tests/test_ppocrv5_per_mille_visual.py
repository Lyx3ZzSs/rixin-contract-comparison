from __future__ import annotations

from pathlib import Path

import fitz

from app.models import BBox, CharBox, TextBlock
from app.services.extractors.ppocrv5 import PPOCRV5Extractor


def test_per_mille_native_repair_accepts_letter_o_ocr_suffix() -> None:
    extractor = PPOCRV5Extractor()

    corrected = extractor._correct_line_per_mille_text(
        "物价款的3%o作为违约金",
        "物价款的3‰作为违约金",
    )

    assert corrected == "物价款的3‰作为违约金"


def test_visual_per_mille_detection_counts_third_closed_loop(tmp_path: Path) -> None:
    extractor = PPOCRV5Extractor()
    per_mille_pdf = _glyph_pdf(tmp_path / "per-mille.pdf", loop_count=3)
    percent_pdf = _glyph_pdf(tmp_path / "percent.pdf", loop_count=2)
    block = _visual_test_block()
    percent_index = block.text.index("%")

    with fitz.open(per_mille_pdf) as pdf:
        assert extractor._visual_glyph_is_per_mille(pdf, block, percent_index)
    with fitz.open(percent_pdf) as pdf:
        assert not extractor._visual_glyph_is_per_mille(pdf, block, percent_index)


def _glyph_pdf(path: Path, *, loop_count: int) -> Path:
    document = fitz.open()
    page = document.new_page(width=180, height=90)
    page.draw_circle((74, 34), 4, color=(0, 0, 0), width=1.4)
    page.draw_circle((84, 48), 4, color=(0, 0, 0), width=1.4)
    if loop_count == 3:
        page.draw_circle((74, 48), 4, color=(0, 0, 0), width=1.4)
    page.draw_line((77, 51), (82, 31), color=(0, 0, 0), width=1.4)
    document.save(path)
    document.close()
    return path


def _visual_test_block() -> TextBlock:
    text = "3%作为违约金"
    return TextBlock(
        block_id="p1_b1",
        page_no=1,
        text=text,
        bbox=BBox(x0=60, y0=26, x1=150, y1=56),
        char_boxes=[
            CharBox(char="3", page_no=1, bbox=BBox(x0=60, y0=28, x1=67, y1=54), text_index=0),
            CharBox(char="%", page_no=1, bbox=BBox(x0=70, y0=28, x1=87, y1=54), text_index=1),
            CharBox(char="作", page_no=1, bbox=BBox(x0=91, y0=28, x1=104, y1=54), text_index=2),
        ],
    )
