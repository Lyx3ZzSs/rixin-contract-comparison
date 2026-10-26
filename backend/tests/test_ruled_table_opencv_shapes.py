"""Exercise both OpenCV HoughLinesP layouts at the real recovery call site."""
import cv2
import numpy as np
import pytest

from app.services.ruled_table_recovery import recover_ruled_tables
from tests.test_representation_root_repairs import _ruled_document


@pytest.mark.parametrize("shape", [(4, 4), (4, 1, 4)])
def test_hough_layout_preserves_geometry_and_character_evidence(tmp_path, monkeypatch, shape):
    document = _ruled_document(tmp_path, "table.pdf", ["Price", "Tax", "100", "6", "Total", "106"])
    original = document.model_copy(deep=True)
    segments = np.array([[80, 120, 720, 120], [80, 280, 720, 280],
                         [80, 440, 720, 440], [80, 600, 720, 600]], dtype=np.int32)
    expected = segments.copy()
    monkeypatch.setattr(cv2, "HoughLinesP", lambda *args, **kwargs: segments.reshape(shape))
    seen = []
    arctan2 = np.arctan2

    def record(dy, dx):
        seen.append((dy, dx))
        return arctan2(dy, dx)

    monkeypatch.setattr(np, "arctan2", record)
    assert len(recover_ruled_tables(document)) == 1
    assert seen == [(row[3] - row[1], row[2] - row[0]) for row in expected]
    assert all(dy.dtype == dx.dtype == expected.dtype for dy, dx in seen)
    np.testing.assert_array_equal(segments, expected)
    assert segments.dtype == expected.dtype
    block = document.pages[0].blocks[0]
    assert block.char_boxes == original.pages[0].blocks[0].char_boxes
    assert block.text == original.pages[0].blocks[0].text
    # The legacy layout is the semantic reference, including recovered cell boxes.
    monkeypatch.setattr(cv2, "HoughLinesP", lambda *args, **kwargs: expected.reshape(4, 1, 4))
    assert len(recover_ruled_tables(original)) == 1
    assert document.model_dump(mode="json") == original.model_dump(mode="json")
