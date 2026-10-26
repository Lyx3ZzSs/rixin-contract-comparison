from types import SimpleNamespace

import fitz
import pytest

from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.formula_variable_ocr import reconcile_formula_variables


def box(x0=10, y0=100, x1=220, y1=120):
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def document(tmp_path, name, token, *, formula=True):
    path = tmp_path / f"{name}.pdf"
    with fitz.open() as pdf:
        pdf.new_page(width=600, height=800)
        pdf.save(path)
    text = f"其中:限电时刻{token}为i时刻的可用功率"
    block = TextBlock(block_id=name, page_no=1, text=text, bbox=box(), source="PP-OCRv6",
                      char_boxes=[CharBox(char=c, text_index=i, page_no=1,
                                          bbox=box(10+i*7, 100, 17+i*7, 120)) for i, c in enumerate(text)])
    blocks = [block]
    if formula:
        blocks.insert(0, TextBlock(block_id=name+"-formula", page_no=1, text="x=1", block_type="formula",
                                  bbox=box(10, 50, 220, 90)))
    return Document(filename=path.name, page_count=1, path=str(path), pages=[Page(page_no=1, width=600, height=800, blocks=blocks)])


class Reader:
    def __init__(self, tokens, confidence=.97):
        self.tokens = iter(tokens)
        self.calls = 0
        self.confidence = confidence

    def predict(self, path, file_type):
        self.calls += 1
        return next(self.tokens)

    def payload_to_document(self, token, path):
        # The actual crop spans one preceding and three following characters.
        text = f"刻{token}为i时"
        block = TextBlock(block_id="roi", page_no=1, text=text, bbox=box(0, 0, 200, 50),
                          confidence=self.confidence,
                          char_boxes=[CharBox(char=c, text_index=i, page_no=1,
                                              bbox=box(i*10, 0, (i+1)*10, 50)) for i,c in enumerate(text)])
        return SimpleNamespace(pages=[SimpleNamespace(width=200, height=50, blocks=[block])])


def test_two_own_pixel_readings_repair_missing_subscript_and_keep_geometry(tmp_path):
    original, compare = document(tmp_path, "o", "PMi"), document(tmp_path, "c", "PM")
    reader = Reader(["PMi", "PMi"])
    changes = reconcile_formula_variables(original, compare, extractor=reader)
    assert [(side, b.block_id) for side,b in changes] == [("compare", "c")]
    block = compare.pages[0].blocks[-1]
    assert block.text == original.pages[0].blocks[-1].text
    assert len(block.char_boxes) == len(block.text)
    assert all(block.text[c.text_index] == c.char and c.page_no == 1 for c in block.char_boxes)
    assert len([s for s in block.source_evidence if s.source == "formula_variable_roi_ppocr"]) == 2
    assert reader.calls == 2


@pytest.mark.parametrize("readings", [("PM", "PM"), ("PMi", "PM"), ("PQi", "PQi")])
def test_peer_never_overwrites_missing_or_conflicting_own_evidence(tmp_path, readings):
    original, compare = document(tmp_path, "o", "PMi"), document(tmp_path, "c", "PM")
    before = compare.model_dump()
    assert reconcile_formula_variables(original, compare, extractor=Reader(readings)) == []
    assert compare.model_dump() == before


def test_not_a_formula_or_real_variable_substitution_does_not_request_ocr(tmp_path):
    for token, formula in [("PM", False), ("PQi", True)]:
        original, compare = document(tmp_path, "o", "PMi", formula=formula), document(tmp_path, "c", token, formula=formula)
        reader = Reader([])
        assert reconcile_formula_variables(original, compare, extractor=reader) == []
        assert reader.calls == 0


def test_low_confidence_cannot_repair(tmp_path):
    original, compare = document(tmp_path, "o", "PMi"), document(tmp_path, "c", "PM")
    assert reconcile_formula_variables(original, compare, extractor=Reader(["PMi", "PMi"], .7)) == []


def test_real_case_change_is_kept_when_own_pixels_confirm_it(tmp_path):
    original, compare = document(tmp_path, "o", "PPi"), document(tmp_path, "c", "Ppi")
    reader = Reader(["PPi", "PPi", "Ppi", "Ppi"])
    assert reconcile_formula_variables(original, compare, extractor=reader) == []
    assert "PPi" in original.pages[0].blocks[-1].text
    assert "Ppi" in compare.pages[0].blocks[-1].text
    assert reader.calls == 4


def test_unavailable_ocr_leaves_original_text_and_characters_intact(tmp_path):
    original, compare = document(tmp_path, "o", "PMi"), document(tmp_path, "c", "PM")
    before = compare.model_dump()
    # A missing response must never authorize copying the peer's variable.
    assert reconcile_formula_variables(original, compare, extractor=Reader([])) == []
    assert compare.model_dump() == before
