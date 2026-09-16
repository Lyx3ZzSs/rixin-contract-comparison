import numpy as np

from app.models import BBox, Document, Page, TextBlock
from app.services.extractors.ppstructure_ocr_hybrid import PPStructureOCRHybridExtractor
from app.services.footer_annotation_visual import FooterAnnotationVisualComparator, PageRegistration, RenderedPage
from app.services.header_footer_compare import HeaderFooterComparator


def block(key, text, kind, box):
    return TextBlock(
        block_id=key, page_no=1, text=text, block_type=kind, bbox=BBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3])
    )


def test_unmatched_footer_region_survives_as_unverified_geometry_not_confirmed_name():
    number = block("number", "26", "number", (300, 705, 314, 718))
    layout = block("layout-footer", "童", "footnote", (305, 730, 510, 790))
    body_note = block("body-note", "注：报价不含税", "footnote", (100, 600, 400, 620))
    page = Page(page_no=1, width=600, height=820, blocks=[layout, body_note])
    merged = PPStructureOCRHybridExtractor()._merge_page_blocks([number], page, 600, 820)
    retained = [b for b in merged if b.layout_block_id == "layout-footer"]
    assert len(retained) == 1
    assert retained[0].bbox == layout.bbox and retained[0].text == "童"
    assert "UNVERIFIED_LAYOUT_FOOTER" in retained[0].semantic_reasons
    assert not any(b.layout_block_id == "body-note" for b in merged)
    doc = Document(filename="x", path="x", page_count=1, pages=[Page(page_no=1, width=600, height=820, blocks=merged)])
    assert not any(e.text == "童" for e in HeaderFooterComparator()._entries_by_slot(doc).get("footer", []))


class Registrar:
    def register(self, *args):
        return PageRegistration(np.eye(3, dtype=np.float32), 100, 90)


def test_page_number_anchored_footer_detects_unlabelled_center_and_right_ink_completely():
    import cv2

    original = np.full((820, 600, 3), 255, np.uint8)
    compare = original.copy()
    # Printed page number is present in both images; changed body text above it
    # must not be swept into the handwriting search.
    cv2.putText(original, "26", (300, 716), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1)
    cv2.putText(compare, "26", (300, 716), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1)
    cv2.putText(compare, "BODY CHANGE", (300, 690), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    shapes = []
    for x in [310, 410, 500]:
        points = np.array([[x + 12, 728], [x + 8, 748], [x + 20, 758], [x, 771], [x + 17, 786]], np.int32)
        cv2.polylines(compare, [points], False, (0, 0, 0), 2)
        shapes.append(BBox(x0=x - 1, y0=727, x1=x + 22, y1=788))
    number = block("n", "26", "number", (300, 705, 314, 719))
    model = Page(page_no=1, width=600, height=820, blocks=[number])
    service = FooterAnnotationVisualComparator(registrar=Registrar())
    diffs = service._page_diffs(
        RenderedPage(original, 600, 820),
        RenderedPage(compare, 600, 820),
        page_no=1,
        start_index=1,
        original_model=model,
        compare_model=model,
    )
    assert len(diffs) == 1 and diffs[0].diff_type == "ADD"
    assert "左下角" not in diffs[0].title
    assert diffs[0].content_recognition_status == "NEEDS_REVIEW"
    boxes = [e.bbox for e in diffs[0].compare_evidence]
    for expected in shapes:
        assert any(
            b.x0 <= expected.x0 + 2 and b.y0 <= expected.y0 + 2 and b.x1 >= expected.x1 - 2 and b.y1 >= expected.y1 - 2
            for b in boxes
        )
    assert all(b.y0 > 719 for b in boxes)


def test_identical_unlabelled_footer_ink_produces_no_difference():
    import cv2

    image = np.full((820, 600, 3), 255, np.uint8)
    cv2.putText(image, "SIGN", (420, 770), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    model = Page(page_no=1, width=600, height=820, blocks=[block("n", "26", "number", (300, 705, 314, 719))])
    service = FooterAnnotationVisualComparator(registrar=Registrar())
    assert (
        service._page_diffs(
            RenderedPage(image, 600, 820),
            RenderedPage(image.copy(), 600, 820),
            page_no=1,
            start_index=1,
            original_model=model,
            compare_model=model,
        )
        == []
    )


def test_page_number_anchored_footer_excludes_scan_shadow_connected_to_page_edge():
    import cv2

    original = np.full((820, 600, 3), 255, np.uint8)
    compare = original.copy()
    cv2.fillPoly(
        compare,
        [np.array([[15, 820], [16, 800], [27, 806], [35, 808], [49, 813], [50, 820]], np.int32)],
        (190, 190, 190),
    )
    model = Page(page_no=1, width=600, height=820, blocks=[block("n", "26", "number", (300, 705, 314, 719))])
    service = FooterAnnotationVisualComparator(registrar=Registrar())
    assert (
        service._page_diffs(
            RenderedPage(original, 600, 820),
            RenderedPage(compare, 600, 820),
            page_no=1,
            start_index=1,
            original_model=model,
            compare_model=model,
        )
        == []
    )
