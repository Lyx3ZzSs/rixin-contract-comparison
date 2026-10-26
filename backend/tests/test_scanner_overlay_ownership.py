from app.services.repeated_overlay_filter import RepeatedOverlayFilter
from app.services.cover_metadata import CoverMetadataComparator
from tests.test_cover_metadata import _block, _multi_page_document


def test_confirmed_scanner_overlay_is_not_a_cover_change_but_handwriting_is():
    originals = []
    compares = []
    for _ in range(5):
        originals.append([_block('title', '技术服务合同', 120, 200, 450, 230, 'doc_title')])
        compares.append([
            _block('title', '技术服务合同', 120, 200, 450, 230, 'doc_title'),
            _block('scanner', '扫描全能王 创建', 565, 20, 578, 117, source='native_pdf'),
            _block('signature', '黄科', 460, 740, 495, 770, source='PP-OCRv6'),
        ])
    original, compare = _multi_page_document(originals), _multi_page_document(compares)
    RepeatedOverlayFilter().apply(compare)
    diffs = CoverMetadataComparator().build_diffs(original, compare)
    assert not any('扫描全能王' in d.compare_text for d in diffs)
    assert any('黄科' in d.compare_text for d in diffs)
