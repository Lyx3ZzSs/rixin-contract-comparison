from copy import deepcopy

import pytest

from app.models import BBox, DiffItem, EvidenceBox
from app.services.footer_annotation_visual import FooterAnnotationVisualComparator


def ocr(identity="ocr", page=1, box=(445, 785.5, 511, 829), direction="ADD"):
    side = "compare" if direction == "ADD" else "original"
    return DiffItem(
        diff_id=identity,
        diff_type=direction,
        source_type="header_footer",
        content_recognition_status="NEEDS_REVIEW",
        content_recognition_reason="LOW_OCR_CONFIDENCE",
        **{
            side + "_text": "黄科",
            side + "_evidence": [
                EvidenceBox(
                    page_no=page,
                    bbox=BBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
                    method="header_footer",
                    text="黄科",
                    highlight_type=direction,
                )
            ],
        },
    )


def visual(page=1, direction="ADD"):
    side = "compare" if direction == "ADD" else "original"
    return DiffItem(
        diff_id="visual",
        diff_type=direction,
        source_type="header_footer",
        review_flags=["VISUAL_FOOTER_ANNOTATION"],
        quality_status="NEEDS_REVIEW",
        content_recognition_status="NEEDS_REVIEW",
        content_recognition_reason="VISUAL_FOOTER_CONTENT_UNVERIFIED",
        **{
            side + "_text": "检测到页脚视觉变化（性质待确认）",
            side + "_evidence": [
                EvidenceBox(
                    page_no=page,
                    bbox=BBox(x0=431.09, y0=769.33, x1=524.37, y1=829.33),
                    method="footer_visual_registered",
                    highlight_type=direction,
                )
            ],
        },
    )


@pytest.mark.parametrize("direction", ["ADD", "DELETE"])
def test_one_ink_object_has_one_main_box_with_both_sources_and_unchanged_uncertainty(direction):
    items = [ocr(direction=direction), visual(direction=direction)]
    before = deepcopy(items)
    result = FooterAnnotationVisualComparator().remove_overlapping_diffs(items)
    assert len(result) == 1
    assert result[0].diff_id == "ocr"
    assert result[0].content_recognition_status == "NEEDS_REVIEW"
    assert result[0].content_recognition_reason == "LOW_OCR_CONFIDENCE"
    side = "compare" if direction == "ADD" else "original"
    assert len(getattr(result[0], side + "_evidence")) == 1
    proof = result[0].match_score_details["footer_visual_evidence"][0]
    assert proof["side"] == side and proof["evidence"]["page_no"] == 1
    assert proof["content_recognition_reason"] == "VISUAL_FOOTER_CONTENT_UNVERIFIED"
    assert items == before
    assert FooterAnnotationVisualComparator().remove_overlapping_diffs(result) == result


def test_fusion_is_page_local_and_keeps_other_independent_ink():
    text = ocr()
    text.compare_evidence.append(ocr(page=2).compare_evidence[0])
    ink = visual()
    ink.compare_evidence.append(
        EvidenceBox(page_no=1, bbox=BBox(x0=40, y0=770, x1=70, y1=810), method="footer_visual_registered")
    )
    result = FooterAnnotationVisualComparator().remove_overlapping_diffs([text, ink])
    assert len(result) == 2
    assert len(result[0].compare_evidence) == 2
    assert len(result[1].compare_evidence) == 1 and result[1].compare_evidence[0].bbox.x0 == 40


@pytest.mark.parametrize("other", [ocr(page=2), ocr(direction="DELETE"), ocr(box=(200, 785, 240, 820))])
def test_unrelated_ocr_does_not_consume_visual_evidence(other):
    assert len(FooterAnnotationVisualComparator().remove_overlapping_diffs([other, visual()])) == 2


def test_ambiguous_two_text_owners_keep_visual_evidence():
    assert (
        len(FooterAnnotationVisualComparator().remove_overlapping_diffs([ocr(), ocr(identity="other"), visual()])) == 3
    )


def test_visual_box_containing_extra_separate_ink_is_not_consumed():
    ink = visual()
    ink.compare_evidence[0].bbox = BBox(x0=200, y0=740, x1=580, y1=839)
    assert len(FooterAnnotationVisualComparator().remove_overlapping_diffs([ocr(), ink])) == 2


def test_small_ink_component_inside_ocr_is_support_not_another_change():
    ink = visual()
    ink.compare_evidence[0].bbox = BBox(x0=450, y0=801, x1=461, y1=806)
    result = FooterAnnotationVisualComparator().remove_overlapping_diffs([ocr(), ink])
    assert len(result) == 1


def test_partially_overlapping_visual_region_with_extra_right_ink_is_retained():
    text = ocr(box=(425.5, 791.5, 504.5, 828))
    ink = visual()
    ink.compare_evidence[0].bbox = BBox(x0=442.42, y0=796.67, x1=535.70, y1=828.67)
    result = FooterAnnotationVisualComparator().remove_overlapping_diffs([text, ink])
    assert len(result) == 2
    assert result[1].compare_evidence == ink.compare_evidence


def test_localized_added_footer_characters_within_clause_modify_can_own_ink():
    text = ocr()
    text.source_type = "clause"
    text.diff_type = "MODIFY"
    text.compare_text = "实际仍有其他正文差异，必须保留。" * 5
    text.compare_evidence[0].method = "char_exact"
    result = FooterAnnotationVisualComparator().remove_overlapping_diffs([text, visual()])
    assert len(result) == 1 and result[0].compare_text == text.compare_text
    assert result[0].diff_type == "MODIFY"
