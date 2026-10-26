import cv2
import numpy as np
from app.services.matrix_graphics import MatrixGraphicComparator


def image(value="contract matrix verification " * 5):
    q = cv2.QRCodeEncoder_create().encode(value)
    q = cv2.resize(q, None, fx=5, fy=5, interpolation=cv2.INTER_NEAREST)
    a = np.full((600, 600), 255, np.uint8)
    a[200 : 200 + q.shape[0], 200 : 200 + q.shape[1]] = q
    return a


def test_locates_code_without_ocr_and_proves_blank_counterpart():
    a = image()
    boxes = MatrixGraphicComparator.candidates(a)
    assert len(boxes) == 1
    result = MatrixGraphicComparator.counterpart(a, boxes[0], np.full_like(a, 255))
    assert result["decision"] == "ABSENT"


def test_contrast_change_is_not_insertion_or_content_change():
    a = image()
    box = MatrixGraphicComparator.candidates(a)[0]
    for factor in [0.7, 0.2, 0.03]:
        faded = np.uint8(255 - (255 - a.astype(float)) * factor)
        assert MatrixGraphicComparator.counterpart(a, box, faded)["decision"] == "SAME"


def test_real_payload_replacement_remains_a_change():
    a = image()
    b = image("different matrix contents!! " * 5)
    result = MatrixGraphicComparator.counterpart(a, MatrixGraphicComparator.candidates(a)[0], b)
    assert result["decision"] == "CHANGED", result


def test_text_and_table_rectangles_are_not_matrix_graphics():
    a = np.full((600, 600), 255, np.uint8)
    for y in range(50, 500, 35):
        cv2.putText(a, "0088 Contract 2026", (50, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 0, 1)
    for x in [40, 200, 400, 560]:
        cv2.line(a, (x, 10), (x, 580), 0, 1)
    assert MatrixGraphicComparator.candidates(a) == []


def test_residual_ink_is_not_treated_as_blank():
    a = image()
    box = MatrixGraphicComparator.candidates(a)[0]
    other = np.full_like(a, 255)
    other[230:330:3, 230:330:3] = 240
    assert MatrixGraphicComparator.counterpart(a, box, other)["decision"] == "UNOBSERVED"


def test_pdf_add_delete_replace_and_cropped_counterpart(tmp_path):
    import fitz
    from app.services.page_alignment import PageAlignmentEntry, PageAlignmentResult, PageAlignmentStatus

    def pdf(name, pixels):
        path = tmp_path / name
        with fitz.open() as doc:
            page = doc.new_page(width=200, height=200)
            page.insert_image(page.rect, stream=cv2.imencode(".png", pixels)[1].tobytes())
            doc.save(path)
        return path

    code = pdf("code.pdf", image())
    blank = pdf("blank.pdf", np.full((600, 600), 255, np.uint8))
    other = pdf("other.pdf", image("different matrix contents!! " * 5))
    alignment = PageAlignmentResult((PageAlignmentEntry(PageAlignmentStatus.MATCHED, (1,), (1,), 1.0, (), "test"),))
    detector = MatrixGraphicComparator()
    for a, b, expected in [(blank, code, "ADD"), (code, blank, "DELETE"), (code, other, "MODIFY")]:
        diffs, observations = detector.compare(a, b, alignment)
        serial_diffs, serial_observations = MatrixGraphicComparator(max_inflight=1).compare(a, b, alignment)
        assert [d.model_dump() for d in diffs] == [d.model_dump() for d in serial_diffs]
        assert observations == serial_observations
        assert len(diffs) == 1, (expected, observations)
        assert diffs[0].diff_type == expected
        assert diffs[0].source_type == "graphic"
    cropped = image()
    cropped[:, 310:] = 255
    diffs, observations = detector.compare(code, pdf("cropped.pdf", cropped), alignment)
    assert diffs == []
    assert any(o["decision"] == "UNOBSERVED" for o in observations)
