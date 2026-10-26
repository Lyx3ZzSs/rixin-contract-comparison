from pathlib import Path

import fitz
import pytest

from app.models import BBox, CharBox, DiffItem, Document, EvidenceBox, Page, TextBlock
from app.services.cover.facade import CoverMetadataComparator
from app.services.diff_quality import DiffQualityProcessor
from app.services.extractors.ppocrv5 import PPOCRV5Extractor


def cover(kind, text="中国华能", x=430, y=70, page_no=1):
    blocks = [
        TextBlock(
            block_id="logo",
            page_no=page_no,
            text=text,
            block_type=kind,
            bbox=BBox(x0=x, y0=y, x1=x + 100, y1=y + 24),
            confidence=0.999,
        ),
        TextBlock(
            block_id="title",
            page_no=page_no,
            text="技术服务项目合同",
            block_type="doc_title",
            bbox=BBox(x0=100, y0=270, x1=480, y1=300),
        ),
    ]
    return Document(
        filename="cover.pdf", path="", page_count=1, pages=[Page(page_no=page_no, width=598, height=822, blocks=blocks)]
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_located_cover_text_matches_across_layout_roles(reverse):
    a, b = cover("header"), cover("text", x=433, y=65)
    if reverse:
        a, b = b, a
    diffs = CoverMetadataComparator().build_diffs(a, b)
    assert not any("中国华能" in d.original_text + d.compare_text for d in diffs)


@pytest.mark.parametrize("kwargs", [{"text": "中国华电"}, {"x": 100}, {"y": 450}, {"page_no": 2}])
def test_cover_same_word_elsewhere_or_changed_logo_is_not_presence_evidence(kwargs):
    diffs = CoverMetadataComparator().build_diffs(cover("header", **kwargs), cover("text"))
    assert any(d.diff_type == "ADD" and d.compare_text == "中国华能" for d in diffs)


def test_body_rate_number_survives_fragmented_right_column_evidence():
    diff = DiffItem(
        diff_id="D1",
        diff_type="MODIFY",
        source_type="clause",
        original_text="合同总价款3‰",
        compare_text="合同总价款3%。",
        original_snippet="3‰",
        compare_snippet="3%。",
        compare_evidence=[
            EvidenceBox(page_no=9, text=t, bbox=BBox(x0=x, y0=246, x1=x + 8, y1=260))
            for t, x in [("3", 492), ("%。", 500)]
        ],
    )
    result = DiffQualityProcessor().process([diff])
    assert len(result.diffs) == 1
    assert result.diffs[0].compare_text == diff.compare_text
    assert result.diffs[0].compare_snippet == diff.compare_snippet


def glyph(path: Path, loops=3):
    pdf = fitz.open()
    p = pdf.new_page(width=180, height=90)
    for x, y in [(74, 34), (84, 48)] + ([(74, 48)] if loops == 3 else []):
        p.draw_circle((x, y), 4, color=(0, 0, 0), width=1.4)
    if loops:
        p.draw_line((77, 51), (82, 31), color=(0, 0, 0), width=1.4)
    pdf.save(path)
    pdf.close()
    return path


def rate_block(text="3%。"):
    return TextBlock(
        block_id="rate",
        page_no=1,
        text=text,
        source="PP-OCRv6",
        bbox=BBox(x0=60, y0=26, x1=90, y1=56),
        char_boxes=[
            CharBox(char="3", text_index=0, page_no=1, bbox=BBox(x0=60, y0=28, x1=67, y1=54)),
            *[
                CharBox(char=c, text_index=i, page_no=1, bbox=BBox(x0=70, y0=28, x1=89, y1=54))
                for i, c in enumerate(text[1:], 1)
            ],
        ],
    )


def test_line_end_shared_box_permille_is_repaired_with_adjacent_context(tmp_path):
    path = glyph(tmp_path / "rate.pdf")
    b = rate_block()
    context = TextBlock(block_id="next", page_no=1, text="的违约金。", bbox=BBox(x0=60, y0=62, x1=130, y1=76))
    page = Page(page_no=1, width=180, height=90, blocks=[b, context])
    assert PPOCRV5Extractor()._correct_verified_per_mille_ocr([page], path) == 1
    assert b.text == "3‰"
    assert [(c.char, c.text_index) for c in b.char_boxes] == [("3", 0), ("‰", 1)]


@pytest.mark.parametrize("text", ["3‰", "3%。"])
def test_visual_unit_handles_line_end_and_shared_suffix(tmp_path, text):
    with fitz.open(glyph(tmp_path / "rate.pdf")) as pdf:
        assert PPOCRV5Extractor()._visual_percent_unit(pdf, rate_block(text), 1) == "‰"


def test_visual_unit_failure_is_unknown_not_percent(tmp_path):
    pdf = fitz.open()
    pdf.new_page(width=180, height=90)
    pdf.save(tmp_path / "blank.pdf")
    pdf.close()
    with fitz.open(tmp_path / "blank.pdf") as pdf:
        assert PPOCRV5Extractor()._visual_percent_unit(pdf, rate_block(), 1) is None


def test_actual_percent_with_stop_is_not_rewritten(tmp_path):
    path = glyph(tmp_path / "rate.pdf", loops=2)
    b = rate_block()
    context = TextBlock(block_id="next", page_no=1, text="的违约金。", bbox=BBox(x0=60, y0=62, x1=130, y1=76))
    page = Page(page_no=1, width=180, height=90, blocks=[b, context])
    assert PPOCRV5Extractor()._correct_verified_per_mille_ocr([page], path) == 0
    assert b.text == "3%。"


def test_v3_repair_preserves_raw_sources_for_many_to_one_glyph(tmp_path):
    from app.services.extractors.contract_v3.text_contract_repair import apply_text_contract_repairs
    from app.services.extractors.contract_v3.artifact import rectangle, to_page

    path = glyph(tmp_path / "rate.pdf")
    rate = rate_block()

    def artifact_block(b, order):
        def box(x):
            return [x.x0, x.y0, x.x1, x.y1]

        return dict(
            block_id=b.block_id,
            text=b.text,
            bbox=box(b.bbox),
            source="PP-OCRv6",
            raw_layout_type="text",
            resolved_block_type="text",
            reading_order=order,
            reason="test",
            ocr_char_provenance=[
                dict(char=c.char, text_index=c.text_index, bbox=box(c.bbox), polygon=rectangle(box(c.bbox)))
                for c in b.char_boxes
            ],
        )

    following = TextBlock(block_id="next", page_no=1, text="的违约金。", bbox=BBox(x0=60, y0=62, x1=130, y1=76))
    a = dict(
        pdf=str(path),
        page_no=1,
        width=180,
        height=90,
        regions=[],
        native_chars=[],
        errors=[],
        blocks=[artifact_block(rate, 1), artifact_block(following, 2)],
    )
    repairs = apply_text_contract_repairs([a])
    b = a["blocks"][0]
    c = b["ocr_char_provenance"][1]
    assert b["raw_ocr_text"] == "3%。" and b["text"] == "3‰"
    assert c["source_range"] == [1, 3]
    assert [s["char"] for s in c["source_refs"]] == ["%", "。"]
    assert c["provenance_kind"] == "NORMALIZED_MANY_TO_ONE"
    assert all(b["source_to_canonical"][s["source_char_id"]] == [1] for s in c["source_refs"])
    assert to_page(a).blocks[0].text == "3‰"
    assert repairs[0]["type"] == "verified_per_mille"


@pytest.mark.parametrize("extra", ["，36小时解决", "，费率另为8%"])
def test_same_visual_unit_cannot_hide_other_business_changes(tmp_path, extra):
    from tests.test_text_evidence import _percent_document, _percent_diff
    from app.services.text_evidence import EvidenceConsensusEvaluator

    a = _percent_document(tmp_path / "a.pdf", text="5%作为违约金", loop_count=3)
    b = _percent_document(tmp_path / "b.pdf", text="5‰作为违约金" + extra, loop_count=3)
    d = _percent_diff()
    d.compare_text += extra
    assert EvidenceConsensusEvaluator().assess(d, a, b).verdict != "EQUIVALENT"
    assert DiffQualityProcessor().process([d], original_document=a, compare_document=b).diffs


def test_changed_rate_number_not_suppressed(tmp_path):
    from tests.test_text_evidence import _percent_document, _percent_diff

    a = _percent_document(tmp_path / "a.pdf", text="5%作为违约金", loop_count=3)
    b = _percent_document(tmp_path / "b.pdf", text="6‰作为违约金", loop_count=3)
    d = _percent_diff()
    d.compare_text = d.compare_text.replace("5", "6")
    d.compare_snippet = "6‰"
    assert DiffQualityProcessor().process([d], original_document=a, compare_document=b).diffs


def test_unclear_visual_is_reviewed_not_confirmed(tmp_path):
    from tests.test_text_evidence import _percent_document, _percent_diff

    a = _percent_document(tmp_path / "a.pdf", text="5%作为违约金", loop_count=3)
    b = _percent_document(tmp_path / "b.pdf", text="5‰作为违约金", loop_count=3)
    pdf = fitz.open()
    pdf.new_page(width=180, height=90)
    pdf.save(tmp_path / "blank.pdf")
    pdf.close()
    b.path = str(tmp_path / "blank.pdf")
    result = DiffQualityProcessor().process([_percent_diff()], original_document=a, compare_document=b)
    assert len(result.diffs) == 1 and result.diffs[0].quality_status == "NEEDS_REVIEW"


def test_body_rate_number_is_protected_even_with_margin_warning():
    d = DiffItem(
        diff_id="D2",
        diff_type="MODIFY",
        source_type="clause",
        original_text="3‰",
        compare_text="3\n%。",
        original_snippet="3‰",
        compare_snippet="3%。",
        review_flags=["READING_ORDER_RISK"],
        compare_evidence=[EvidenceBox(page_no=1, text="3", bbox=BBox(x0=570, y0=780, x1=580, y1=793))],
    )
    assert not DiffQualityProcessor()._trim_edge_annotation_from_mixed_diff(d, cover("header"))
    assert d.compare_text == "3\n%。" and d.compare_snippet == "3%。"


def test_multiple_shared_suffixes_keep_text_and_char_indices(tmp_path):
    path = glyph(tmp_path / "rates.pdf")
    with fitz.open(path) as pdf:
        p = pdf[0]
        for x, y in [(106, 34), (116, 48), (106, 48)]:
            p.draw_circle((x, y), 4, color=(0, 0, 0), width=1.4)
        p.draw_line((109, 51), (114, 31), color=(0, 0, 0), width=1.4)
        pdf.saveIncr()
    b = rate_block()
    b.text = "3%。5%。"
    b.bbox.x1 = 122
    b.char_boxes += [
        CharBox(char=c, text_index=i, page_no=1, bbox=BBox(x0=x0, y0=28, x1=x1, y1=54))
        for c, i, x0, x1 in [("5", 3, 94, 99), ("%", 4, 102, 121), ("。", 5, 102, 121)]
    ]
    context = TextBlock(block_id="next", page_no=1, text="的违约金。", bbox=BBox(x0=60, y0=62, x1=130, y1=76))
    assert (
        PPOCRV5Extractor()._correct_verified_per_mille_ocr(
            [Page(page_no=1, width=180, height=90, blocks=[b, context])], path
        )
        == 2
    )
    assert b.text == "3‰5‰"
    assert [(c.char, c.text_index) for c in b.char_boxes] == list(zip(b.text, range(4)))
