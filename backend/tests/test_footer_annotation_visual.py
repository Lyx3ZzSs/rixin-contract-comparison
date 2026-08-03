from __future__ import annotations

import numpy as np

from app.models import BBox, DiffItem, Document, EvidenceBox, Page
from app.services.diff_quality import DiffQualityProcessor
from app.services.footer_annotation_visual import (
    FooterAnnotationVisualComparator,
    PageRegistration,
    RenderedPage,
)


class _Renderer:
    def __init__(self, images: dict[tuple[str, int], np.ndarray]) -> None:
        self.images = images

    def render(self, pdf_path: str, page_no: int) -> RenderedPage | None:
        image = self.images.get((pdf_path, page_no))
        if image is None:
            return None
        return RenderedPage(image=image, width_points=200, height_points=200)


class _IdentityRegistrar:
    def register(self, _original: np.ndarray, _compare: np.ndarray) -> PageRegistration:
        return PageRegistration(homography=np.eye(3, dtype=np.float32), match_count=100, inlier_count=90)


def _document(path: str, page_count: int = 1) -> Document:
    return Document(
        filename=path,
        path=path,
        page_count=page_count,
        pages=[Page(page_no=page_no, width=200, height=200, blocks=[]) for page_no in range(1, page_count + 1)],
    )


def _blank() -> np.ndarray:
    return np.full((200, 200, 3), 255, dtype=np.uint8)


def _comparator(images: dict[tuple[str, int], np.ndarray]) -> FooterAnnotationVisualComparator:
    return FooterAnnotationVisualComparator(renderer=_Renderer(images), registrar=_IdentityRegistrar())


def test_detects_added_footer_handwriting_despite_unrelated_original_footer_text() -> None:
    original = _blank()
    original[182:194, 80:125] = 0
    compare = _blank()
    compare[176:196, 22:38] = 0

    diffs = _comparator({("original.pdf", 1): original, ("compare.pdf", 1): compare}).build_diffs(
        _document("original.pdf"),
        _document("compare.pdf"),
    )

    assert len(diffs) == 1
    diff = diffs[0]
    assert diff.diff_type == "ADD"
    assert diff.title == "第1页左下角手写签注"
    assert diff.compare_evidence[0].method == "footer_visual_registered"
    assert diff.compare_evidence[0].bbox.x0 < 30
    assert diff.compare_evidence[0].bbox.x1 < 50


def test_ignores_scan_border_touching_the_page_edge() -> None:
    original = _blank()
    compare = _blank()
    compare[170:200, 0:5] = 0

    diffs = _comparator({("original.pdf", 1): original, ("compare.pdf", 1): compare}).build_diffs(
        _document("original.pdf"),
        _document("compare.pdf"),
    )

    assert diffs == []


def test_ignores_aligned_printed_footer_content() -> None:
    original = _blank()
    compare = _blank()
    original[176:196, 22:38] = 0
    compare[176:196, 22:38] = 0

    diffs = _comparator({("original.pdf", 1): original, ("compare.pdf", 1): compare}).build_diffs(
        _document("original.pdf"),
        _document("compare.pdf"),
    )

    assert diffs == []


def test_visual_footer_diffs_remain_page_specific_during_quality_processing() -> None:
    images: dict[tuple[str, int], np.ndarray] = {}
    for page_no in (1, 2):
        original = _blank()
        compare = _blank()
        compare[176:196, 22:38] = 0
        images[("original.pdf", page_no)] = original
        images[("compare.pdf", page_no)] = compare

    diffs = _comparator(images).build_diffs(
        _document("original.pdf", page_count=2),
        _document("compare.pdf", page_count=2),
    )
    result = DiffQualityProcessor().process(diffs)

    assert len(result.diffs) == 2
    assert [diff.title for diff in result.diffs] == ["第1页左下角手写签注", "第2页左下角手写签注"]
    assert not any(decision.action == "cross_source_merged" for decision in result.decisions)


def test_parallel_page_processing_preserves_serial_diff_order_and_ids() -> None:
    images: dict[tuple[str, int], np.ndarray] = {}
    for page_no in (1, 2, 3):
        original = _blank()
        compare = _blank()
        compare[176:196, 20 + page_no * 4 : 34 + page_no * 4] = 0
        images[("original.pdf", page_no)] = original
        images[("compare.pdf", page_no)] = compare
    original_document = _document("original.pdf", page_count=3)
    compare_document = _document("compare.pdf", page_count=3)

    serial = FooterAnnotationVisualComparator(
        renderer=_Renderer(images),
        registrar=_IdentityRegistrar(),
        max_inflight=1,
    ).build_diffs(original_document, compare_document, start_index=7)
    parallel = FooterAnnotationVisualComparator(
        renderer=_Renderer(images),
        registrar=_IdentityRegistrar(),
        max_inflight=3,
    ).build_diffs(original_document, compare_document, start_index=7)

    assert [diff.model_dump(mode="json") for diff in parallel] == [
        diff.model_dump(mode="json") for diff in serial
    ]


def test_registered_visual_evidence_replaces_overlapping_footer_ocr_noise() -> None:
    original = _blank()
    compare = _blank()
    compare[176:196, 22:38] = 0
    comparator = _comparator({("original.pdf", 1): original, ("compare.pdf", 1): compare})
    visual_diffs = comparator.build_diffs(_document("original.pdf"), _document("compare.pdf"))
    ocr_diff = DiffItem(
        diff_id="D900",
        diff_type="ADD",
        title="页脚",
        compare_text="怀意",
        compare_snippet="怀意",
        source_type="header_footer",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=21, y0=182, x1=39, y1=197),
                method="header_footer",
                text="怀意",
                highlight_type="ADD",
            )
        ],
    )

    kept = comparator.remove_overlapping_ocr_diffs([ocr_diff], visual_diffs)

    assert kept == []


def test_keeps_footer_ocr_evidence_when_visual_box_only_covers_a_small_fragment() -> None:
    comparator = _comparator({})
    visual_diff = DiffItem(
        diff_id="D901",
        diff_type="ADD",
        source_type="header_footer",
        review_flags=[comparator.visual_flag],
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=22, y0=176, x1=38, y1=196),
                method="footer_visual_registered",
                text="检测到左下角手写签注",
                highlight_type="ADD",
            )
        ],
    )
    ocr_diff = DiffItem(
        diff_id="D902",
        diff_type="ADD",
        title="页脚",
        compare_text="完整手写签名",
        compare_snippet="完整手写签名",
        source_type="header_footer",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=10, y0=170, x1=100, y1=199),
                method="header_footer",
                text="完整手写签名",
                highlight_type="ADD",
            )
        ],
    )

    kept = comparator.remove_overlapping_ocr_diffs([ocr_diff], [visual_diff])

    assert len(kept) == 1
    assert kept[0].compare_evidence == ocr_diff.compare_evidence


def test_removes_footer_ocr_evidence_when_visual_box_covers_most_axes() -> None:
    comparator = _comparator({})
    visual_diff = DiffItem(
        diff_id="D903",
        diff_type="ADD",
        source_type="header_footer",
        review_flags=[comparator.visual_flag],
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=10, y0=15, x1=95, y1=45),
                method="footer_visual_registered",
                text="检测到左下角手写签注",
                highlight_type="ADD",
            )
        ],
    )
    ocr_diff = DiffItem(
        diff_id="D904",
        diff_type="ADD",
        source_type="header_footer",
        compare_text="完整手写签名",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=0, y0=0, x1=100, y1=60),
                method="header_footer",
                text="完整手写签名",
                highlight_type="ADD",
            )
        ],
    )

    kept = comparator.remove_overlapping_ocr_diffs([ocr_diff], [visual_diff])

    assert kept == []


def test_removes_footer_ocr_evidence_covered_by_grouped_visual_boxes() -> None:
    comparator = _comparator({})
    visual_diff = DiffItem(
        diff_id="D905",
        diff_type="ADD",
        source_type="header_footer",
        review_flags=[comparator.visual_flag],
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=5, y0=10, x1=50, y1=35),
                method="footer_visual_registered",
                text="检测到左下角手写签注",
                highlight_type="ADD",
            ),
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=55, y0=25, x1=95, y1=55),
                method="footer_visual_registered",
                text="检测到左下角手写签注",
                highlight_type="ADD",
            ),
        ],
    )
    ocr_diff = DiffItem(
        diff_id="D906",
        diff_type="ADD",
        source_type="header_footer",
        compare_text="完整手写签名",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=0, y0=0, x1=100, y1=60),
                method="header_footer",
                text="完整手写签名",
                highlight_type="ADD",
            )
        ],
    )

    kept = comparator.remove_overlapping_ocr_diffs([ocr_diff], [visual_diff])

    assert kept == []


def test_removes_padded_footer_ocr_box_containing_same_tight_visual_box() -> None:
    comparator = _comparator({})
    visual_diff = DiffItem(
        diff_id="D905A",
        diff_type="ADD",
        source_type="header_footer",
        review_flags=[comparator.visual_flag],
        compare_evidence=[
            EvidenceBox(
                page_no=2,
                bbox=BBox(x0=49.97, y0=788.0, x1=65.30, y1=814.0),
                method="footer_visual_registered",
                text="检测到左下角手写签注",
                highlight_type="ADD",
            )
        ],
    )
    padded_ocr_diff = DiffItem(
        diff_id="D905B",
        diff_type="ADD",
        source_type="header_footer",
        compare_text="蛋",
        compare_evidence=[
            EvidenceBox(
                page_no=2,
                bbox=BBox(x0=42.5, y0=783.0, x1=73.0, y1=819.5),
                method="header_footer",
                text="蛋",
                highlight_type="ADD",
            )
        ],
    )

    kept = comparator.remove_overlapping_ocr_diffs([padded_ocr_diff], [visual_diff])

    assert kept == []


def test_does_not_group_visual_boxes_from_different_diffs_for_ocr_deduplication() -> None:
    comparator = _comparator({})
    visual_diffs = [
        DiffItem(
            diff_id="D907",
            diff_type="ADD",
            source_type="header_footer",
            review_flags=[comparator.visual_flag],
            compare_evidence=[
                EvidenceBox(
                    page_no=1,
                    bbox=BBox(x0=5, y0=10, x1=50, y1=35),
                    method="footer_visual_registered",
                    text="检测到左下角手写签注",
                    highlight_type="ADD",
                )
            ],
        ),
        DiffItem(
            diff_id="D908",
            diff_type="ADD",
            source_type="header_footer",
            review_flags=[comparator.visual_flag],
            compare_evidence=[
                EvidenceBox(
                    page_no=1,
                    bbox=BBox(x0=55, y0=25, x1=95, y1=55),
                    method="footer_visual_registered",
                    text="检测到左下角手写签注",
                    highlight_type="ADD",
                )
            ],
        ),
    ]
    ocr_diff = DiffItem(
        diff_id="D909",
        diff_type="ADD",
        source_type="header_footer",
        compare_text="完整手写签名",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=0, y0=0, x1=100, y1=60),
                method="header_footer",
                text="完整手写签名",
                highlight_type="ADD",
            )
        ],
    )

    kept = comparator.remove_overlapping_ocr_diffs([ocr_diff], visual_diffs)

    assert len(kept) == 1
    assert kept[0].compare_evidence == ocr_diff.compare_evidence


def test_deduplicates_misclassified_clause_evidence_after_visual_footer_detection() -> None:
    comparator = _comparator({})
    visual_diff = DiffItem(
        diff_id="D910",
        diff_type="ADD",
        source_type="header_footer",
        review_flags=[comparator.visual_flag],
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=10, y0=15, x1=95, y1=55),
                method="footer_visual_registered",
                text="检测到左下角手写签注",
                highlight_type="ADD",
            )
        ],
    )
    misclassified_clause = DiffItem(
        diff_id="D911",
        diff_type="ADD",
        source_type="clause",
        compare_text="意",
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=20, y0=10, x1=100, y1=60),
                method="char_exact",
                text="意",
                highlight_type="ADD",
            )
        ],
    )

    kept = comparator.remove_overlapping_diffs([misclassified_clause, visual_diff])

    assert [diff.diff_id for diff in kept] == [visual_diff.diff_id]


def test_post_evidence_deduplication_keeps_diffs_that_never_had_evidence() -> None:
    comparator = _comparator({})
    visual_diff = DiffItem(
        diff_id="D912",
        diff_type="ADD",
        source_type="header_footer",
        review_flags=[comparator.visual_flag],
        compare_evidence=[
            EvidenceBox(
                page_no=1,
                bbox=BBox(x0=10, y0=15, x1=95, y1=55),
                method="footer_visual_registered",
                text="检测到左下角手写签注",
                highlight_type="ADD",
            )
        ],
    )
    evidence_less_diff = DiffItem(
        diff_id="D913",
        diff_type="ADD",
        source_type="clause",
        compare_text="需要人工复核的新增内容",
    )

    kept = comparator.remove_overlapping_diffs([evidence_less_diff, visual_diff])

    assert [diff.diff_id for diff in kept] == [evidence_less_diff.diff_id, visual_diff.diff_id]


def test_prefers_substantive_annotation_over_isolated_speck() -> None:
    original = _blank()
    compare = _blank()
    compare[176:196, 10:26] = 0
    original[176:196, 10:11] = 0
    compare[185:190, 42:45] = 0

    diffs = _comparator({("original.pdf", 1): original, ("compare.pdf", 1): compare}).build_diffs(
        _document("original.pdf"),
        _document("compare.pdf"),
    )

    assert len(diffs) == 1
    assert len(diffs[0].compare_evidence) == 1
    assert diffs[0].compare_evidence[0].bbox.x0 < 30


def test_keeps_multiple_substantive_annotation_regions_on_same_page() -> None:
    original = np.full((400, 400, 3), 255, dtype=np.uint8)
    compare = original.copy()
    compare[374:394, 15:35] = 0
    compare[366:378, 65:69] = 0
    compare[366:378, 86:89] = 0

    diffs = _comparator({("original.pdf", 1): original, ("compare.pdf", 1): compare}).build_diffs(
        _document("original.pdf"),
        _document("compare.pdf"),
    )

    assert len(diffs) == 1
    assert len(diffs[0].compare_evidence) == 2
    assert diffs[0].compare_evidence[0].bbox.x0 < 30
    assert diffs[0].compare_evidence[1].bbox.x0 == 32.5
    assert diffs[0].compare_evidence[1].bbox.x1 == 44.5


def test_ignores_thin_secondary_scan_fragment() -> None:
    original = np.full((400, 400, 3), 255, dtype=np.uint8)
    compare = original.copy()
    compare[374:394, 15:35] = 0
    compare[360:380, 80:83] = 0

    diffs = _comparator({("original.pdf", 1): original, ("compare.pdf", 1): compare}).build_diffs(
        _document("original.pdf"),
        _document("compare.pdf"),
    )

    assert len(diffs) == 1
    assert len(diffs[0].compare_evidence) == 1
    assert diffs[0].compare_evidence[0].bbox.x1 < 30


def test_removes_internal_vertical_scan_edge_before_locating_annotation() -> None:
    original = _blank()
    compare = _blank()
    compare[160:198, 7:9] = 0
    compare[176:196, 15:31] = 0

    diffs = _comparator({("original.pdf", 1): original, ("compare.pdf", 1): compare}).build_diffs(
        _document("original.pdf"),
        _document("compare.pdf"),
    )

    assert len(diffs) == 1
    bbox = diffs[0].compare_evidence[0].bbox
    assert bbox.x0 >= 15
    assert bbox.y0 >= 176


def test_ignores_colored_scan_capture_artifact() -> None:
    original = _blank()
    compare = _blank()
    compare[176:198, 15:35] = np.array([180, 95, 45], dtype=np.uint8)

    diffs = _comparator({("original.pdf", 1): original, ("compare.pdf", 1): compare}).build_diffs(
        _document("original.pdf"),
        _document("compare.pdf"),
    )

    assert diffs == []


def test_detects_low_contrast_neutral_footer_handwriting() -> None:
    original = _blank()
    compare = _blank()
    ink_color = np.array([220, 230, 228], dtype=np.uint8)
    compare[183:197, 22:30] = ink_color
    compare[183:197, 34:42] = ink_color

    diffs = _comparator({("original.pdf", 1): original, ("compare.pdf", 1): compare}).build_diffs(
        _document("original.pdf"),
        _document("compare.pdf"),
    )

    assert len(diffs) == 1
    bbox = diffs[0].compare_evidence[0].bbox
    assert bbox.x0 <= 22
    assert bbox.x1 >= 42


def test_ignores_very_dark_saturated_scan_capture_artifact() -> None:
    original = _blank()
    compare = _blank()
    artifact_color = np.array([55, 20, 10], dtype=np.uint8)
    compare[183:197, 22:30] = artifact_color
    compare[183:197, 35:43] = artifact_color

    diffs = _comparator({("original.pdf", 1): original, ("compare.pdf", 1): compare}).build_diffs(
        _document("original.pdf"),
        _document("compare.pdf"),
    )

    assert diffs == []


def test_removes_dense_vertical_capture_band_without_losing_adjacent_handwriting() -> None:
    original = np.full((400, 400, 3), 255, dtype=np.uint8)
    compare = original.copy()
    compare[350:398, 15:35] = 80
    compare[350:398:6, 15:35] = 255
    compare[374:394, 38:70] = 0

    diffs = _comparator({("original.pdf", 1): original, ("compare.pdf", 1): compare}).build_diffs(
        _document("original.pdf"),
        _document("compare.pdf"),
    )

    assert len(diffs) == 1
    assert len(diffs[0].compare_evidence) == 1
    assert diffs[0].compare_evidence[0].bbox.x0 >= 19


def test_keeps_footer_handwriting_that_extends_past_the_old_left_roi_boundary() -> None:
    original = np.full((400, 400, 3), 255, dtype=np.uint8)
    compare = original.copy()
    compare[374:394, 90:100] = 0
    compare[374:394, 105:115] = 0
    compare[374:394, 120:130] = 0

    diffs = _comparator({("original.pdf", 1): original, ("compare.pdf", 1): compare}).build_diffs(
        _document("original.pdf"),
        _document("compare.pdf"),
    )

    assert len(diffs) == 1
    assert diffs[0].compare_evidence[0].bbox.x1 >= 65


def test_groups_baseline_aligned_signature_fragments_across_a_wide_character_gap() -> None:
    original = np.full((400, 400, 3), 255, dtype=np.uint8)
    compare = original.copy()
    compare[374:394, 70:80] = 0
    compare[374:394, 114:124] = 0

    diffs = _comparator({("original.pdf", 1): original, ("compare.pdf", 1): compare}).build_diffs(
        _document("original.pdf"),
        _document("compare.pdf"),
    )

    assert len(diffs) == 1
    assert len(diffs[0].compare_evidence) == 1
    assert diffs[0].compare_evidence[0].bbox.x0 == 35
    assert diffs[0].compare_evidence[0].bbox.x1 == 62
