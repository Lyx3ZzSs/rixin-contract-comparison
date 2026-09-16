"""Regressions for printed dates, prose signatures and missing cover OCR cells."""

from pathlib import Path

import cv2
import fitz
import numpy as np
import pytest

from app.models import BBox, CharBox, DiffItem, Document, EvidenceBox, Page, TextBlock
from app.services.document_preparation import DocumentPreparer
from app.services.pipeline_stages import _reconcile_visual_signing_date_diffs
from app.services.signing_region.visual_recall import WholePageSigningVisualRecall as Recall


def block(text, y=100, x=50, width=300, key="b"):
    return TextBlock(block_id=key, page_no=1, text=text, bbox=BBox(x0=x, y0=y, x1=x + width, y1=y + 20))


def page(blocks):
    return Page(page_no=1, width=600, height=800, blocks=blocks)


@pytest.mark.parametrize(
    "text",
    [
        "有效期限：2024年7月1日-2025年6月30日",
        "服务时间：2026年7月1日",
        "交付日期：2026年7月1日",
        "服务期限：2026年7月1日至2029年6月30日",
        "本合同自2026年7月1日至2029年6月30日履行",
    ],
)
def test_printed_period_is_not_handwritten_even_with_signing_context(text):
    p = page([block("甲方签订日期：", y=70), block(text)])
    image = np.zeros((800, 600, 3), np.uint8)
    assert Recall()._date_objects(p, image, cv2, np) == []


def test_table_date_label_must_be_on_same_row():
    b = block("签订日期2026年7月1日", width=400)
    b.char_boxes = [
        CharBox(
            char=c,
            page_no=1,
            text_index=i,
            bbox=BBox(x0=50 + i * 10, x1=60 + i * 10, y0=70 if i < 4 else 100, y1=90 if i < 4 else 120),
        )
        for i, c in enumerate(b.text)
    ]
    assert Recall()._date_objects(page([b]), np.zeros((800, 600, 3), np.uint8), cv2, np) == []


@pytest.mark.parametrize(
    "text", ["以用户签字确认的验收单原件作为付款条件。", "双方签字确认并保存。", "应提供买方指定人员签字确认的签收单"]
)
def test_signature_prose_cannot_seed_body_ink_detection(text):
    b = block(text)
    assert not Recall._has_local_signature_cue(page([b]), b)


@pytest.mark.parametrize("value", ["2026.4.2", "2026.4.21"])
def test_partial_date_completion_has_one_localized_highlight_despite_ocr_disagreement(value):
    outer = BBox(x0=250, y0=640, x1=380, y1=665)
    inner = BBox(x0=337, y0=643, x1=365, y1=665)
    v = DiffItem(
        diff_id="WHOLE-PAGE-date",
        diff_type="ADD",
        source_type="signing_region",
        compare_text=value,
        match_score_details={"object_type": "HANDWRITTEN_DATE"},
        compare_evidence=[EvidenceBox(page_no=1, bbox=outer)],
    )
    d = DiffItem(
        diff_id="field",
        diff_type="MODIFY",
        source_type="metadata",
        title="封面字段：签订日期",
        original_text="2026年4月 日",
        compare_text="2026年4月21日",
        compare_evidence=[EvidenceBox(page_no=1, bbox=inner, text="21")],
    )
    result, remap = _reconcile_visual_signing_date_diffs([v, d])
    assert len(result) == 1 and remap[v.diff_id] == d.diff_id
    assert result[0].compare_evidence[0].bbox == inner
    assert result[0].compare_text == "2026年4月21日"


def test_different_date_field_is_not_merged_by_page_alone():
    v = DiffItem(
        diff_id="WHOLE-PAGE-date",
        diff_type="ADD",
        source_type="signing_region",
        compare_text="2026.4.2",
        match_score_details={"object_type": "HANDWRITTEN_DATE"},
        compare_evidence=[EvidenceBox(page_no=1, bbox=block("").bbox)],
    )
    d = DiffItem(
        diff_id="field",
        diff_type="MODIFY",
        source_type="metadata",
        title="封面字段：签订日期",
        compare_text="2026年4月21日",
        compare_evidence=[EvidenceBox(page_no=1, bbox=block("", y=400).bbox)],
    )
    assert len(_reconcile_visual_signing_date_diffs([v, d])[0]) == 2


@pytest.mark.parametrize("conflict", [False, True])
def test_sparse_native_grid_recovers_same_pdf_only_and_preserves_conflict(tmp_path: Path, conflict):
    path = tmp_path / "table.pdf"
    with fitz.open() as pdf:
        p = pdf.new_page(width=600, height=800)
        for y in [100, 140, 180, 220, 260]:
            p.draw_line((50, y), (450, y))
        for x in [50, 170, 450]:
            p.draw_line((x, 100), (x, 260))
        for n, (label, value) in enumerate(
            [
                ("Buyer", "Alpha electricity company"),
                ("Seller", "Beta service company"),
                ("Location", "Beijing"),
                ("Date", "2026"),
            ]
        ):
            p.insert_text((60, 125 + 40 * n), label)
            p.insert_text((180, 125 + 40 * n), value)
        pdf.save(path)
    value = "2027" if conflict else "2026"
    b = block(value)
    b.bbox = BBox(x0=50, y0=100, x1=450, y1=260)
    b.raw_html = "<table>" + "<tr><td></td><td></td></tr>" * 3 + f"<tr><td></td><td>{value}</td></tr></table>"
    doc = Document(filename=path.name, path=str(path), page_count=1, pages=[page([b])])
    result = DocumentPreparer().prepare(doc, "original")
    recovered = any(d.reason == "sparse_table_native_text_recovered" for d in result.decisions)
    assert recovered is not conflict
    if not conflict:
        assert "Alpha electricity company" in b.raw_html and "Beijing" in b.text
        assert b.char_boxes
    else:
        assert "Alpha" not in b.raw_html


def test_broken_round_seal_is_one_object_not_letter_sized_name_seals():
    im = np.full((800, 600, 3), 255, np.uint8)
    cv2.circle(im, (290, 400), 65, (180, 50, 50), 3)
    cv2.putText(im, "ABC", (240, 395), cv2.FONT_HERSHEY_SIMPLEX, 1, (180, 50, 50), 3)
    # Dark printed lines sever the red outline.
    for y in [360, 400, 430]:
        cv2.line(im, (210, y), (370, y), (0, 0, 0), 5)
    objects = Recall()._red_objects(page([block("甲方盖章", y=480)]), im, cv2, np)
    central = [o for o in objects if 200 < o.bbox.x0 < 360]
    assert len(central) == 1
    assert central[0].object_type.value == "COMPANY_ROUND_SEAL"


def test_red_islands_in_blue_background_do_not_form_a_round_seal():
    im = np.full((800, 600, 3), (100, 180, 230), np.uint8)
    cv2.circle(im, (290, 400), 65, (180, 50, 50), 3)
    for y in [360, 400, 430]:
        cv2.line(im, (210, y), (370, y), (0, 0, 0), 5)
    objects = Recall()._red_objects(page([block("甲方盖章", y=480)]), im, cv2, np)
    assert not any(o.raw_ref.get("reason") == "FRAGMENTED_RED_SEAL_REASSEMBLED" for o in objects)
