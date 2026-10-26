"""Rotated source PDFs must keep text, visual evidence and business geometry aligned."""

import hashlib

import fitz
import numpy as np
import pytest

from app.services.pdf_geometry import prepare_display_pdf


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_display_pdf_preserves_pixels_and_source_and_exposes_horizontal_text(tmp_path, rotation):
    source = tmp_path / "input.pdf"
    with fitz.open() as pdf:
        p = pdf.new_page(width=500, height=600)
        p.insert_text((250, 300), "1.7 Currency unit", rotate=rotation)
        p.set_rotation(rotation)
        pdf.save(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with fitz.open(source) as pdf:
        before = pdf[0].get_pixmap(alpha=False)
        size = tuple(pdf[0].rect)
    dest, audit = prepare_display_pdf(source, tmp_path / "derived.pdf")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    with fitz.open(dest) as pdf:
        p = pdf[0]
        assert p.rotation == 0 and tuple(p.rect) == size
        after = p.get_pixmap(alpha=False)
        delta = np.abs(
            np.frombuffer(before.samples, dtype=np.uint8).astype(int)
            - np.frombuffer(after.samples, dtype=np.uint8).astype(int)
        )
        assert delta.mean() < 0.01
        block = p.get_text("blocks")[0]
        assert "Currency unit" in block[4]
        assert block[2] - block[0] > block[3] - block[1]
    assert audit["coordinate_space"] == "PDF_DISPLAY"
    assert (dest == source) == (rotation == 0)
    if rotation:
        assert audit["pages"][0]["source_rotation"] == rotation
        assert len(audit["pages"][0]["source_to_display"]) == 6


def test_signing_match_cannot_jump_over_reliable_page_alignment():
    from tests.test_signing_region_comparison import _region
    from app.services.page_alignment import PageAlignmentEntry, PageAlignmentResult, PageAlignmentStatus
    from app.services.signing_region.matcher import SigningRegionMatcher

    alignment = PageAlignmentResult(
        tuple(PageAlignmentEntry(PageAlignmentStatus.MATCHED, (n,), (n,), 0.95, (), "test") for n in (13, 53))
    )
    a = _region("a", "甲方 盖章 签字 日期 电话 地址", page_no=13)
    b = _region("b", "甲方 盖章 签字 日期 电话 地址", page_no=53)
    pairs = SigningRegionMatcher().match([a], [b], page_alignment=alignment)
    assert not any(left is not None and right is not None for left, right, _ in pairs)


def test_cover_title_uses_local_line_geometry_not_inverted_model_order():
    from app.models import Document, Page, TextBlock, BBox
    from app.services.cover.extractor import extract
    from app.services.normalizer import TextNormalizer

    blocks = [
        TextBlock(block_id=str(i), page_no=1, text=text, bbox=BBox(x0=80, y0=y, x1=500, y1=y + 25))
        for i, (y, text) in enumerate([(277, "权服务合同"), (239, "司2026 年新能源场站功率预测系统授")])
    ]
    d = Document(
        filename="a.pdf", path="a.pdf", page_count=1, pages=[Page(page_no=1, width=595, height=842, blocks=blocks)]
    )
    assert extract(TextNormalizer(), d)["project_title"].value.startswith("司2026")


@pytest.mark.parametrize("ruled", [True, False])
def test_rowspan_repair_requires_observed_horizontal_rules(ruled):
    from PIL import Image, ImageDraw
    from app.services.extractors.contract_v3.table_row_spans import split_observed_row_spans

    image = Image.new("RGB", (250, 220), "white")
    draw = ImageDraw.Draw(image)
    cells = [dict(row=0, column=0, rowspan=4, colspan=1, bbox=[10, 10, 80, 210], raw_geometry_index=0)]
    for row in range(4):
        cells.append(
            dict(
                row=row,
                column=1,
                rowspan=1,
                colspan=1,
                bbox=[80, 10 + row * 50, 240, 60 + row * 50],
                raw_geometry_index=row + 1,
            )
        )
        draw.line((80, 10 + row * 50, 240, 10 + row * 50), fill="black", width=2)
        if ruled and row:
            draw.line((10, 10 + row * 50, 80, 10 + row * 50), fill="black", width=2)
    result, audit = split_observed_row_spans(cells, image)
    assert len(result) == (8 if ruled else 5)
    assert bool(audit) == ruled
    assert len({c["raw_geometry_index"] for c in result}) == len(result)


def test_stamp_name_is_not_reassigned_but_overlapping_printed_prose_is():
    from app.services.extractors.contract_v3.printed_ownership import printed_text_owner

    stamp = dict(
        region_id="seal",
        raw_layout_type="seal",
        bbox=[200, 200, 320, 330],
        polygon=[[200, 200], [320, 200], [320, 330], [200, 330]],
    )
    assert (
        printed_text_owner(
            [50, 240, 480, 260],
            "7.1 本协议履行过程中，如遇国家法律法规、地方政府规章或行业标准修订",
            0.99,
            [stamp],
            stamp,
        )
        is None
    )
    assert printed_text_owner([205, 240, 315, 260], "国能长源随州发电有限公司随县分公司", 0.99, [stamp], stamp) is stamp


@pytest.mark.parametrize("neighbor_count", [1, 2])
def test_table_header_recovery_preserves_observed_characters_and_needs_corroboration(neighbor_count):
    from app.services.extractors.contract_v3.printed_ownership import recover_table_headers

    table = dict(
        region_id="t", raw_layout_type="table", bbox=[0, 0, 400, 400], polygon=[[0, 0], [400, 0], [400, 400], [0, 400]]
    )
    headers = [
        dict(block_id=f"h{i}", region_id="t", text=t, raw_ocr_score=0.99, bbox=[i * 100, 10, i * 100 + 50, 25])
        for i, t in enumerate(["序号", "考核标准"][:neighbor_count])
    ]
    text = "同 考核项目"
    stamp = dict(
        block_id="s",
        region_id="seal",
        raw_layout_type="seal",
        source="PP-OCRv6",
        raw_ocr_score=0.84,
        text=text,
        bbox=[100, 10, 160, 25],
        ocr_char_provenance=[
            dict(text_index=i, char=c, bbox=[100 + i * 10, 10, 110 + i * 10, 25]) for i, c in enumerate(text)
        ],
    )
    artifact = dict(regions=[table], blocks=headers + [stamp])
    recover_table_headers(artifact)
    if neighbor_count == 1:
        assert stamp["text"] == text
        assert len(artifact["blocks"]) == 2
    else:
        recovered = artifact["blocks"][-1]
        assert stamp["text"] == "同 "
        assert recovered["text"] == "考核项目" and recovered["region_id"] == "t"
        assert stamp["text"] + recovered["text"] == text
        assert [c["source_text_index"] for c in recovered["ocr_char_provenance"]] == [2, 3, 4, 5]
        assert [c["text_index"] for c in recovered["ocr_char_provenance"]] == [0, 1, 2, 3]


def test_curved_scan_row_rules_recover_separate_serial_cells():
    from PIL import Image, ImageDraw
    from app.services.extractors.contract_v3.table_row_spans import split_observed_row_spans

    image = Image.new("RGB", (250, 220), "white")
    draw = ImageDraw.Draw(image)
    cells = [dict(row=0, column=0, rowspan=4, colspan=1, bbox=[10, 10, 80, 210], raw_geometry_index=0)]
    for row in range(4):
        y = 10 + row * 50
        cells.append(
            dict(row=row, column=1, rowspan=1, colspan=1, bbox=[80, y, 240, y + 50], raw_geometry_index=row + 1)
        )
        if row:
            draw.line((10, y + 16, 80, y), fill="black", width=2)
    result, audit = split_observed_row_spans(cells, image)
    assert len(result) == 8 and len(audit[0]["boundaries"]) == 3
    assert all(c["rowspan"] == 1 for c in result)


def test_seal_fusion_keeps_form_party_when_layout_seal_has_no_party():
    from app.models import DiffItem, EvidenceBox, BBox
    from app.services.signing_region.models import SigningVisualObject, SigningVisualObjectType
    from app.services.signing_region.visual_recall import WholePageSigningVisualRecall

    box = BBox(x0=350, y0=140, x1=460, y1=260)
    obj = SigningVisualObject(
        object_id="s",
        object_type=SigningVisualObjectType.COMPANY_ROUND_SEAL,
        page_no=13,
        bbox=box,
        party_role="甲方",
        visual_detection_confidence=0.9,
    )
    generic = DiffItem(diff_id="layout", diff_type="ADD", source_type="seal", title="印章区域")
    form = DiffItem(
        diff_id="form",
        diff_type="ADD",
        source_type="signing_region",
        title="乙方 · 印章",
        section_type="signature:seal",
        compare_evidence=[EvidenceBox(page_no=13, bbox=box)],
    )
    recall = WholePageSigningVisualRecall()
    recall._preserve_fused_party(generic, obj, [generic, form])
    recall._enrich_diff(generic, obj)
    assert generic.match_score_details["party_role"] == "乙方"
    assert generic.match_score_details["party_binding_source_diff_ids"] == ["form"]
