"""Synthetic regressions from the ten-contract holdout investigation."""
from __future__ import annotations

import numpy as np
import cv2
import pytest

from app.models import BBox, DiffItem, Document, EvidenceBox, Page, TextBlock, TextSourceEvidence
from app.services.diff_quality import DiffQualityProcessor
from app.services.document_preparation import DocumentPreparer
from app.services.signing_region.visual_recall import WholePageSigningVisualRecall
from tests.golden_contract.semantic_matcher import SemanticGoldenMatcher


def box(x=50, y=100, width=200, height=20):
    return BBox(x0=x, y0=y, x1=x + width, y1=y + height)


def document(blocks):
    return Document(filename="synthetic.pdf", path="", page_count=1,
                    pages=[Page(page_no=1, width=600, height=800, blocks=blocks)])


@pytest.mark.parametrize("value", ["AB/N-20260522-00041", "合同编号:ABC-20261234"])
def test_header_location_does_not_erase_identifier(value):
    diff = DiffItem(diff_id="id", diff_type="ADD", source_type="header_footer", title="页眉",
                    compare_text=value, compare_snippet=value,
                    compare_evidence=[EvidenceBox(page_no=1, bbox=box(), text=value)])
    result = DiffQualityProcessor().process([diff])
    assert len(result.diffs) == 1
    assert result.diffs[0].quality_status == "NEEDS_REVIEW"


def test_choice_numeral_without_two_sided_visual_proof_is_not_noise():
    diff = DiffItem(diff_id="choice", diff_type="MODIFY", source_type="clause", match_score=100,
                    original_text="争议按以下第一种方式处理", compare_text="争议按以下第二种方式处理",
                    original_snippet="一", compare_snippet="二")
    assert len(DiffQualityProcessor().process([diff]).diffs) == 1


@pytest.mark.parametrize("already_present,vertical_gap,expected", [(False, 20, True), (True, 20, False), (False, 2, False)])
def test_missing_layout_prefix_requires_uncovered_text_and_geometry(already_present, vertical_gap, expected):
    prefix = "乙方应承担全部经济赔偿责任，"
    body = "甲方的直接损失和间接损失均按照本合同相关规定计算。"
    source = TextSourceEvidence(source="ppstructure", raw_text=prefix + body, page_no=1,
                               bbox=box(y=100 - vertical_gap, height=80), confidence=0.99)
    block = TextBlock(block_id="b", page_no=1, text=body, bbox=box(), block_type="text",
                      layout_block_id="layout", source_evidence=[source])
    doc = document([block])
    if already_present:
        doc.pages[0].blocks.append(TextBlock(block_id="earlier", page_no=1, text=prefix, bbox=box(y=80)))
    result = DocumentPreparer().prepare(doc, side="compare")
    recovered = [b for b in doc.pages[0].blocks if b.source == "ppstructure_layout_prefix_recovery"]
    assert bool(recovered) == expected
    if expected:
        assert recovered[0].text == prefix.replace("，", ",")
        assert not recovered[0].char_boxes
        assert any(d.reason == "same_layout_missing_prefix_recovered" for d in result.decisions)


@pytest.mark.parametrize("choice,expected", [("一", True), ("二", False)])
def test_split_form_line_coverage_preserves_changed_choice(choice, expected):
    prefix = "4.2.1. 双方同意采用以下第"
    doc = document([
        TextBlock(block_id="p", page_no=1, text=prefix, bbox=box(width=180)),
        TextBlock(block_id="value", page_no=1, text=f"({choice})、(三)种方式付款", bbox=box(x=240)),
    ])
    text = prefix + "\n(一)、"
    diff = DiffItem(diff_id="split", diff_type="ADD", source_type="clause", clause_no="4.2.1",
                    compare_text=text, compare_snippet=text,
                    compare_evidence=[EvidenceBox(page_no=1, bbox=box(), text=text)])
    assert DiffQualityProcessor()._same_page_clause_prefix_covered(diff, doc) is expected


def test_participant_anchor_detects_both_ink_clusters_but_not_blank():
    page = document([TextBlock(block_id="participants", page_no=1, text="参与人员：",
                               bbox=box(x=50, y=400, width=65))]).pages[0]
    detector = WholePageSigningVisualRecall()
    blank = np.full((800, 600, 3), 255, dtype=np.uint8)
    assert detector._signature_objects(page, blank, [], [], cv2, np) == []
    ink = blank.copy()
    for start in [125, 200]:
        cv2.polylines(ink, [np.array([[start, 405], [start + 25, 430], [start, 438],
                                      [start + 35, 410], [start + 20, 440]], dtype=np.int32)], False, (0, 0, 0), 3)
    objects = detector._signature_objects(page, ink, [], [], cv2, np)
    assert len(objects) == 1
    assert objects[0].bbox.x1 > 225
    assert objects[0].raw_ref["participant_signature"] is True


def actual_visual(pages):
    return {"diff_type": "ADD", "source_type": "seal", "title": "骑缝章", "quality_status": "NEEDS_REVIEW",
            "match_score_details": {"object_type": "EDGE_SEAM_SEAL"},
            "compare_evidence": [{"page_no": p, "bbox": box().model_dump()} for p in pages]}


def expected_visual(page):
    return {"case_id": f"edge-{page}", "assertion": "MUST_REVIEW", "page": page,
            "object_type": "EDGE_SEAM_SEAL", "bbox_expectation": {"side": "compare", "target_bbox": box().model_dump(), "min_iou": 0.35}}


def test_aggregated_seal_can_satisfy_distinct_page_evidence_but_not_missing_page():
    matcher = SemanticGoldenMatcher()
    assert not matcher.match("test", [expected_visual(1), expected_visual(2)], [actual_visual([1, 2])]).failures
    assert matcher.match("test", [expected_visual(2)], [actual_visual([1])]).failures
    assert matcher.match("test", [expected_visual(1), expected_visual(1)], [actual_visual([1])]).failures


def test_bbox_never_borrows_another_page_and_prefers_spatial_candidate():
    matcher = SemanticGoldenMatcher()
    expected = expected_visual(2)
    wrong = actual_visual([1, 2])
    wrong["compare_evidence"][1]["bbox"] = box(x=350).model_dump()
    assert matcher.match("test", [expected], [wrong]).failures[0].failure_type == "BBOX_REGRESSION"
    assert not matcher.match("test", [expected], [wrong, actual_visual([2])]).failures


def test_decision_failure_is_not_counted_again_as_unexpected_confirmed():
    actual = actual_visual([1])
    actual["quality_status"] = "NORMAL"
    expected = expected_visual(1)
    expected["quality_status"] = "NEEDS_REVIEW"
    failures = SemanticGoldenMatcher().match("test", [expected], [actual]).failures
    assert [f.failure_type for f in failures] == ["DECISION_REGRESSION"]


def test_layout_line_support_does_not_require_missing_primary_ocr_token():
    prefix = "4.2.1. 双方同意采用以下第"
    source = TextSourceEvidence(source="ppstructure", raw_text=prefix + "(一)、(三)种方式付款", page_no=1,
                               bbox=box(height=20), confidence=0.99)
    doc = document([TextBlock(block_id="p", page_no=1, text=prefix, bbox=box(), source_evidence=[source])])
    text = prefix + "(一)、"
    diff = DiffItem(diff_id="split", diff_type="ADD", source_type="clause", clause_no="4.2.1",
                    compare_text=text, compare_snippet=text,
                    compare_evidence=[EvidenceBox(page_no=1, bbox=box(), text=text)])
    assert DiffQualityProcessor()._same_page_clause_prefix_covered(diff, doc)
    source.bbox.y1 = 300
    assert not DiffQualityProcessor()._same_page_clause_prefix_covered(diff, doc)


def test_visual_enrichment_preserves_existing_party_region_binding():
    from app.services.signing_region.models import SigningVisualObject, SigningVisualObjectType
    diff = DiffItem(diff_id="seal", diff_type="ADD", source_type="signing_region", title="乙方 · 印章",
                    compare_text="seal", compare_evidence=[EvidenceBox(page_no=1, bbox=box())],
                    match_score_details={"change_decision": "CANDIDATE"})
    visual = SigningVisualObject(object_id="v", object_type=SigningVisualObjectType.OTHER_STAMP,
                                 page_no=1, bbox=box(), party_role="甲方", visual_detection_confidence=0.9,
                                 party_attribution_confidence=0.7)
    WholePageSigningVisualRecall._enrich_diff(diff, visual)
    assert diff.title.startswith("乙方 ·")
    assert diff.match_score_details["party_attribution_conflict"]
    assert diff.match_score_details["visual_party_candidate"] == "甲方"
    assert diff.match_score_details["change_decision"] == "CANDIDATE"
    assert diff.compare_text != "seal"


def test_edge_aggregation_keeps_full_pale_seal_roi_on_each_source_page():
    from app.services.signing_region.models import SigningVisualObject, SigningVisualObjectType
    full = box(x=530, y=280, width=65, height=130)
    fragment = box(x=580, y=320, width=15, height=30)
    obj = SigningVisualObject(object_id="edge", object_type=SigningVisualObjectType.EDGE_SEAM_SEAL,
                              page_no=1, bbox=fragment, supporting_pages=[1, 2],
                              fragment_bboxes=[fragment, box(x=1, y=500, width=10, height=50)])
    d = DiffItem(diff_id="edge", diff_type="ADD", source_type="seal", title="印章区域",
                 compare_evidence=[EvidenceBox(page_no=1, bbox=full, method="seal_region")])
    WholePageSigningVisualRecall._enrich_diff(d, obj)
    assert any(e.page_no == 1 and e.bbox == full for e in d.compare_evidence)
    assert any(e.page_no == 2 and e.bbox.x0 == 1 for e in d.compare_evidence)
    wrong_page = DiffItem(diff_id="wrong", diff_type="ADD", source_type="seal",
                          compare_evidence=[EvidenceBox(page_no=2, bbox=full, method="seal_region")])
    assert not WholePageSigningVisualRecall._overlapping_diffs(obj, [wrong_page])
