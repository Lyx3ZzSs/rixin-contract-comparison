"""Cover ink belongs to its physical ROI even when its characters are unreadable."""


import fitz
import pytest
from PIL import Image, ImageDraw

from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.cover.residual_ocr import CoverResidualOCR
from app.services.signing_region.visual_recall import WholePageSigningVisualRecall


def doc(path, *, scanned=False, extra=True):
    chars = [
        CharBox(char=c, text_index=i, page_no=1, bbox=BBox(x0=100 + i * 20, y0=200, x1=120 + i * 20, y1=225))
        for i, c in enumerate("技术服务合同")
    ]
    b = TextBlock(
        block_id="title",
        page_no=1,
        text="技术服务合同",
        source="PP-OCRv6" if scanned else "native_pdf",
        block_type="doc_title",
        bbox=BBox(x0=98, y0=195, x1=275 if extra else 222, y1=232),
        char_boxes=chars,
        confidence=0.999,
    )
    return Document(
        filename=path.name, path=str(path), page_count=1, pages=[Page(page_no=1, width=600, height=800, blocks=[b])]
    )


def pdf(path, *, ink=False):
    d = fitz.open()
    p = d.new_page(width=600, height=800)
    if ink:
        p.draw_polyline([(238, 205), (245, 225), (250, 200), (260, 219)], color=(0, 0, 0), width=2)
    d.save(path)
    d.close()
    return path


class OCR:
    def __init__(self):
        self.calls = 0

    def predict(self, path, file_type=1):
        self.calls += 1
        w, h = Image.open(path).size
        return [
            {
                "dt_polys": [[[8, 8], [w - 8, 8], [w - 8, h - 8], [8, h - 8]]],
                "rec_texts": ["族"],
                "rec_scores": [0.074],
                "rec_polys": [[[8, 8], [w - 8, 8], [w - 8, h - 8], [8, h - 8]]],
            }
        ]


@pytest.mark.parametrize("reverse", [False, True])
def test_unreadable_ink_is_change_not_confident_character(tmp_path, reverse):
    a = doc(pdf(tmp_path / "a.pdf"))
    b = doc(pdf(tmp_path / "b.pdf", ink=True), scanned=True)
    if reverse:
        a, b = b, a
    ocr = OCR()
    diffs = CoverResidualOCR(ocr).build_diffs(a, b)
    assert len(diffs) == 1
    d = diffs[0]
    assert d.diff_type == ("DELETE" if reverse else "ADD")
    assert "族" not in d.original_text + d.compare_text
    assert d.content_recognition_status == "NEEDS_REVIEW"
    ev = (d.original_evidence if reverse else d.compare_evidence)[0]
    assert ev.bbox.x0 > 220 and ev.bbox.x1 < 280
    assert d.match_score_details["recognition_scope"] == "ink_presence"


@pytest.mark.parametrize("mode", ["both_present", "blank", "already_owned", "fully_explained", "different_title"])
def test_residual_must_be_unowned_and_counterpart_blank(tmp_path, mode):
    a = doc(pdf(tmp_path / "a.pdf", ink=mode == "both_present"))
    b = doc(pdf(tmp_path / "b.pdf", ink=mode != "blank"), scanned=True, extra=mode != "fully_explained")
    if mode == "already_owned":
        b.pages[0].blocks.append(
            TextBlock(block_id="ink", page_no=1, text="签", bbox=BBox(x0=230, y0=195, x1=275, y1=232))
        )
    if mode == "different_title":
        a.pages[0].blocks[0].text = "完全不同的合同"
    ocr = OCR()
    assert CoverResidualOCR(ocr).build_diffs(a, b) == []
    assert ocr.calls == 0


def test_scan_image_edge_recalls_two_seals_without_edge_specks(tmp_path):
    image = Image.new("RGB", (540, 800), "white")
    draw = ImageDraw.Draw(image)
    for y in [230, 420]:
        draw.ellipse((518, y, 600, y + 90), outline="red", width=3)
        draw.line((531, y + 28, 539, y + 28), fill="red", width=3)
    draw.line((539, 4, 539, 35), fill="red", width=2)
    draw.line((520, 330, 522, 350), fill="red", width=2)
    image_path = tmp_path / "scan.png"
    image.save(image_path)
    d = fitz.open()
    p = d.new_page(width=600, height=800)
    p.insert_image(fitz.Rect(0, 0, 540, 800), filename=str(image_path))
    path = tmp_path / "scan.pdf"
    d.save(path)
    d.close()
    v = WholePageSigningVisualRecall()
    found = v.detect(doc(path, scanned=True))
    edges = [o for o in found if o.object_type.value == "EDGE_SEAM_SEAL"]
    assert len(edges) == 2
    assert 230 <= edges[0].bbox.y0 < 245
    assert 420 <= edges[1].bbox.y0 < 435


def test_native_counterpart_text_is_not_blank_even_if_font_does_not_render(tmp_path):
    a = doc(pdf(tmp_path / "a.pdf"))
    b = doc(pdf(tmp_path / "b.pdf", ink=True), scanned=True)
    a.pages[0].blocks.append(
        TextBlock(
            block_id="native-extra",
            page_no=1,
            text="批注",
            source="native_pdf",
            bbox=BBox(x0=230, y0=198, x1=270, y1=230),
        )
    )
    ocr = OCR()
    assert CoverResidualOCR(ocr).build_diffs(a, b) == []
    assert ocr.calls == 0
