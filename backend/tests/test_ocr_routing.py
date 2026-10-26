"""OCR routing decisions and page-level routing behaviour (the 9-case matrix)."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from app.services.ocr_routing import (
    REASON_CORRUPT_ENCODING,
    REASON_EMPTY_PAGE,
    REASON_FIELD_FORM,
    REASON_FORM_LAYOUT,
    REASON_LARGE_IMAGE_LOW_TEXT,
    REASON_NATIVE_OK,
    REASON_SCANNED_PAGE,
    REASON_SPARSE_TEXT,
    REASON_TABLE_PAGE,
    NativeDocumentMetrics,
    NativePageMetricsCollector,
    OcrRoutingThresholds,
    PageNativeMetrics,
    PageTextQualityEvaluator,
)


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------


def _text_page_metrics(page_no: int = 1, chars: int = 600, **overrides) -> PageNativeMetrics:
    defaults = dict(
        width=595.0,
        height=842.0,
        native_char_count=chars,
        valid_char_count=chars,
        suspicious_char_count=0,
        char_box_count=chars,
        page_area=595.0 * 842.0,
        image_coverage=0.0,
        largest_image_ratio=0.0,
        image_count=0,
        vector_table_count=0,
        text_table_like=False,
        line_count=12,
        median_line_chars=40.0,
        field_line_count=0,
        multi_column=False,
        red_vector=False,
        signing_text=False,
    )
    defaults.update(overrides)
    return PageNativeMetrics(page_no=page_no, **defaults)


CJK_FONT = "china-s"


def _body_text(lines: int = 12) -> str:
    return "\n".join(
        f"第{index}条 甲方应于验收合格后支付合同款{index * 1000}元，乙方应提供相应发票。" for index in range(1, lines + 1)
    )


def _insert_body(page: fitz.Page) -> None:
    page.insert_text((50, 80), _body_text(), fontsize=9, fontname=CJK_FONT)


def _scan_image(path: Path) -> bytes:
    with fitz.open() as source:
        page = source.new_page(width=595, height=842)
        page.insert_text((60, 120), "SCANNED CONTRACT PAGE", fontsize=18)
        return page.get_pixmap(dpi=100).tobytes("png")


def write_text_pdf(path: Path, *, pages: int = 1) -> Path:
    with fitz.open() as doc:
        for _ in range(pages):
            _insert_body(doc.new_page(width=595, height=842))
        doc.save(path)
    return path


def write_scanned_pdf(path: Path, *, pages: int = 1) -> Path:
    image = _scan_image(path)
    with fitz.open() as doc:
        for _ in range(pages):
            page = doc.new_page(width=595, height=842)
            page.insert_image(page.rect, stream=image)
        doc.save(path)
    return path


def write_mixed_pdf(path: Path) -> Path:
    """page 1 text, page 2 scanned, page 3 text, page 4 scanned, page 5 text."""
    image = _scan_image(path)
    with fitz.open() as doc:
        for index in range(5):
            page = doc.new_page(width=595, height=842)
            if index % 2 == 0:
                _insert_body(page)
            else:
                page.insert_image(page.rect, stream=image)
        doc.save(path)
    return path


def write_blank_pdf(path: Path, *, pages: int = 1) -> Path:
    with fitz.open() as doc:
        for _ in range(pages):
            doc.new_page(width=595, height=842)
        doc.save(path)
    return path


def write_logo_pdf(path: Path) -> Path:
    """Full text layer plus a small logo raster - must NOT trigger OCR."""
    image = _scan_image(path)
    with fitz.open() as doc:
        page = doc.new_page(width=595, height=842)
        _insert_body(page)
        page.insert_image(fitz.Rect(470, 40, 545, 90), stream=image)
        doc.save(path)
    return path


def write_ocr_layer_pdf(path: Path) -> Path:
    """A scan that already carries a good, complete OCR text layer."""
    image = _scan_image(path)
    with fitz.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=image)
        _insert_body(page)
        doc.save(path)
    return path


def write_corrupt_pdf(path: Path) -> Path:
    """Text objects present, but the encoding is broken (GBK bytes read as Latin-1).

    This is what a wrong-charset text layer looks like in the wild: the page has
    hundreds of characters, very few of which are printable contract characters.
    """
    mojibake = "甲方应于合同签订之日起支付合同款".encode("gbk").decode("latin-1") * 6
    with fitz.open() as doc:
        doc.new_page(width=595, height=842).insert_text((50, 80), mojibake, fontsize=10)
        doc.save(path)
    return path


def write_ruled_table_pdf(path: Path) -> Path:
    """Digital page carrying a ruled table (detected natively by find_tables)."""
    with fitz.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 60), _body_text(6), fontsize=9, fontname=CJK_FONT)
        for row in range(6):
            y = 300 + row * 22
            page.draw_line(fitz.Point(50, y), fitz.Point(545, y))
        for column in range(5):
            x = 50 + column * 124
            page.draw_line(fitz.Point(x, 300), fitz.Point(x, 410))
        page.insert_text((60, 318), "序号  产品名称  数量  单价  金额", fontsize=9, fontname=CJK_FONT)
        doc.save(path)
    return path


def write_scanned_table_pdf(path: Path) -> Path:
    image = _scan_image(path)
    with fitz.open() as doc:
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=image)
        doc.save(path)
    return path


# --------------------------------------------------------------------------
# decision unit tests (pure)
# --------------------------------------------------------------------------


def test_good_native_text_skips_ocr():
    decision = PageTextQualityEvaluator().evaluate(_text_page_metrics())
    assert decision.needs_ocr is False
    assert decision.reason == REASON_NATIVE_OK
    assert decision.native_char_count == 600
    assert decision.printable_ratio == 1.0
    assert decision.quality_score == 1.0


def test_scan_without_text_layer_needs_ocr():
    metrics = _text_page_metrics(chars=0, valid_char_count=0, char_box_count=0, image_coverage=1.0, image_count=1)
    decision = PageTextQualityEvaluator().evaluate(metrics)
    assert decision.needs_ocr is True
    assert decision.reason == REASON_SCANNED_PAGE


def test_truly_blank_page_is_not_ocr_ed():
    metrics = _text_page_metrics(chars=0, valid_char_count=0, char_box_count=0)
    decision = PageTextQualityEvaluator().evaluate(metrics)
    assert decision.needs_ocr is False
    assert decision.reason == REASON_EMPTY_PAGE
    assert decision.quality_score is None


def test_large_image_with_a_few_characters_needs_ocr():
    metrics = _text_page_metrics(chars=4, valid_char_count=4, char_box_count=4, image_coverage=0.97, image_count=1)
    decision = PageTextQualityEvaluator().evaluate(metrics)
    assert decision.needs_ocr is True
    assert decision.reason == REASON_LARGE_IMAGE_LOW_TEXT


def test_low_text_without_images_is_kept_native():
    metrics = _text_page_metrics(chars=40, valid_char_count=40, char_box_count=40, image_coverage=0.0)
    decision = PageTextQualityEvaluator().evaluate(metrics)
    assert decision.needs_ocr is False
    assert decision.reason == REASON_SPARSE_TEXT


def test_corrupt_encoding_needs_ocr():
    metrics = _text_page_metrics(chars=600, valid_char_count=300, char_box_count=600)
    decision = PageTextQualityEvaluator().evaluate(metrics)
    assert decision.needs_ocr is True
    assert decision.reason == REASON_CORRUPT_ENCODING


def test_replacement_characters_need_ocr():
    metrics = _text_page_metrics(chars=300, valid_char_count=300, suspicious_char_count=60, char_box_count=300)
    decision = PageTextQualityEvaluator().evaluate(metrics)
    assert decision.needs_ocr is True
    assert decision.reason == REASON_CORRUPT_ENCODING


def test_a_couple_of_suspicious_characters_do_not_trigger_ocr():
    """Real text layers contain a few PUA bullets; that must not force OCR."""
    metrics = _text_page_metrics(chars=600, valid_char_count=600, suspicious_char_count=3, char_box_count=600)
    decision = PageTextQualityEvaluator().evaluate(metrics)
    assert decision.needs_ocr is False
    assert decision.reason == REASON_NATIVE_OK


def test_missing_char_boxes_need_ocr():
    metrics = _text_page_metrics(chars=600, valid_char_count=600, char_box_count=120)
    decision = PageTextQualityEvaluator().evaluate(metrics)
    assert decision.needs_ocr is True
    assert decision.reason == "missing_char_boxes"


@pytest.mark.parametrize(
    "overrides,expected_reason",
    [
        ({"vector_table_count": 2}, REASON_TABLE_PAGE),
        ({"text_table_like": True}, REASON_TABLE_PAGE),
        ({"line_count": 30, "median_line_chars": 4.0}, REASON_FORM_LAYOUT),
        ({"line_count": 20, "median_line_chars": 15.0, "field_line_count": 12}, REASON_FIELD_FORM),
        ({"multi_column": True}, "multi_column_layout"),
        ({"signing_text": True}, "signing_region_text"),
        ({"red_vector": True}, "red_vector_graphics"),
    ],
)
def test_structure_protection_rules_route_to_ocr(overrides, expected_reason):
    decision = PageTextQualityEvaluator().evaluate(_text_page_metrics(**overrides))
    assert decision.needs_ocr is True
    assert decision.reason == expected_reason


def test_a_party_field_page_is_routed_to_ocr():
    """Party/field pages carry headings that the native path does not classify."""
    metrics = _text_page_metrics(line_count=23, median_line_chars=15.0, field_line_count=18)
    decision = PageTextQualityEvaluator().evaluate(metrics)
    assert decision.needs_ocr is True
    assert decision.reason == REASON_FIELD_FORM


def test_a_few_colons_do_not_make_a_field_page():
    metrics = _text_page_metrics(line_count=30, median_line_chars=30.0, field_line_count=3)
    assert PageTextQualityEvaluator().evaluate(metrics).reason == REASON_NATIVE_OK


def test_prose_lines_are_not_mistaken_for_a_form():
    metrics = _text_page_metrics(line_count=60, median_line_chars=32.0)
    assert PageTextQualityEvaluator().evaluate(metrics).reason == REASON_NATIVE_OK


def test_a_sparse_page_carrying_a_table_is_still_ocr_ed():
    """Table protection must win over the 'too little text' branch."""
    metrics = _text_page_metrics(chars=54, valid_char_count=54, char_box_count=54, vector_table_count=1)
    decision = PageTextQualityEvaluator().evaluate(metrics)
    assert decision.needs_ocr is True
    assert decision.reason == REASON_TABLE_PAGE


def test_structure_protection_can_be_disabled_per_rule():
    thresholds = OcrRoutingThresholds(
        protect_tables=False,
        protect_multi_column=False,
        protect_signing_regions=False,
        protect_red_vectors=False,
    )
    metrics = _text_page_metrics(vector_table_count=3, multi_column=True, signing_text=True, red_vector=True)
    decision = PageTextQualityEvaluator(thresholds).evaluate(metrics)
    assert decision.needs_ocr is False
    assert decision.reason == REASON_NATIVE_OK


def test_thresholds_are_configurable():
    thresholds = OcrRoutingThresholds(min_native_chars=1000)
    decision = PageTextQualityEvaluator(thresholds).evaluate(_text_page_metrics(chars=600))
    assert decision.needs_ocr is False
    assert decision.reason == REASON_SPARSE_TEXT


def test_log_fields_are_log_friendly():
    decision = PageTextQualityEvaluator().evaluate(
        _text_page_metrics(page_no=6, chars=4, valid_char_count=4, char_box_count=4, image_coverage=0.97)
    )
    fields = decision.as_log_fields(6)
    assert fields == {
        "page": 6,
        "route": "ocr",
        "reason": REASON_LARGE_IMAGE_LOW_TEXT,
        "native_chars": 4,
        "image_coverage": 0.97,
    }


# --------------------------------------------------------------------------
# segment helpers
# --------------------------------------------------------------------------
