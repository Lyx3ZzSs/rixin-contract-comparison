import pytest

from app.models import BBox, CompareTask, DiffItem, Document, EvidenceBox
from app.services.diff_events import event_response
from app.services.evidence_locator import EvidenceLocator
from app.services.seal_comparator import build_seal_diffs
from app.services.signing_region.models import SigningVisualObject, SigningVisualObjectType
from app.services.signing_region.visual_recall import SigningVisualRecallResult, WholePageSigningVisualRecall
from tests.test_contract_extraction_v3 import Layout, OCR, _box, _pdf
from app.services.extractors.contract_v3.extractor import ContractExtractionV3


def test_unread_seals_survive_full_extraction_and_reach_comparison(tmp_path):
    pdf = _pdf(tmp_path / "scan.pdf", raster=True)
    # OCR returns ordinary text outside both seal regions.
    extractor = ContractExtractionV3(
        layout=Layout(
            [
                _box("seal", [200, 150, 360, 310], 20),
                _box("seal", [450, 150, 610, 310], 20, 2),
            ]
        ),
        ocr=OCR(),
    )
    doc = extractor.extract(pdf).document
    seals = [b for b in doc.pages[0].blocks if b.block_type == "seal"]
    assert len(seals) == 2
    assert all(not b.text and not b.char_boxes and not b.enter_clause_compare for b in seals)
    assert all(b.source == "PP-DocLayoutV3" and b.confidence == 0.9 for b in seals)
    assert any(b.text == "Missing text" for b in doc.pages[0].blocks)
    diffs = build_seal_diffs(Document(filename="empty", path="", pages=[], page_count=0), doc)
    assert len(diffs) == 2
    assert all(d.source_type == "seal" for d in diffs)


def test_existing_seal_text_does_not_create_duplicate_visual_object(tmp_path):
    pdf = _pdf(tmp_path / "scan.pdf", raster=True)
    doc = ContractExtractionV3(layout=Layout([_box("seal", [0, 0, 160, 80], 20)]), ocr=OCR()).extract(pdf).document
    assert len([b for b in doc.pages[0].blocks if b.block_type == "seal"]) == 1


def seal(page=1, y=100, kind=SigningVisualObjectType.COMPANY_ROUND_SEAL):
    return SigningVisualObject(
        object_id=f"s-{page}-{y}",
        object_type=kind,
        page_no=page,
        bbox=BBox(x0=570, y0=y, x1=600, y1=y + 80),
        visual_detection_confidence=0.78,
        supporting_pages=[page],
        raw_ref={"page_width": 600, "page_height": 800},
    )


@pytest.mark.parametrize(
    "kind",
    [
        SigningVisualObjectType.COMPANY_ROUND_SEAL,
        SigningVisualObjectType.PERSONAL_NAME_SEAL,
        SigningVisualObjectType.EDGE_SEAM_SEAL,
        SigningVisualObjectType.OTHER_STAMP,
    ],
)
def test_recalled_seals_use_seal_channel_and_preserve_detector_evidence(kind):
    seals, signing = WholePageSigningVisualRecall().reconcile_diffs(
        SigningVisualRecallResult(additions=[seal(kind=kind)]), [], [], start_index=1
    )
    assert len(seals) == 1 and signing == []
    assert seals[0].source_type == "seal"
    EvidenceLocator().assign_evidence_confidence(seals)
    assert seals[0].compare_evidence[0].confidence == 0.78
    assert seals[0].compare_evidence[0].evidence_quality == "MEDIUM"


def test_historical_event_projection_normalizes_seal_without_mutating_task():
    d = DiffItem(
        diff_id="old",
        diff_type="ADD",
        source_type="signing_region",
        section_type="signature:company_round_seal",
        match_score_details={"object_type": "COMPANY_ROUND_SEAL"},
        compare_evidence=[EvidenceBox(page_no=12, bbox=seal().bbox, method="whole_page_visual_object")],
    )
    task = CompareTask(task_id="old-task", diffs=[d])
    before = task.model_dump()
    event = event_response(task)["events"][0]
    assert event["diff"]["source_type"] == "seal"
    assert event["children"][0]["source_type"] == "seal"
    assert task.model_dump() == before


def test_edge_groups_do_not_merge_distinct_vertical_bands_or_page_sides():
    objects = [seal(p, y, SigningVisualObjectType.EDGE_SEAM_SEAL) for p in [1, 2] for y in [80, 580]]
    left = seal(3, 80, SigningVisualObjectType.EDGE_SEAM_SEAL)
    left.bbox = BBox(x0=0, y0=80, x1=30, y1=160)
    objects.append(left)
    groups = WholePageSigningVisualRecall._aggregate_edge_fragments(objects)
    assert len(groups) == 3
    assert sorted(len(set(o.supporting_pages)) for o in groups) == [1, 2, 2]
    assert len({o.object_id for o in groups}) == 3


def test_geometric_edge_group_alone_never_confirms_same_seal():
    objects = [seal(p, 200, SigningVisualObjectType.EDGE_SEAM_SEAL) for p in range(1, 5)]
    for o in objects:
        o.visual_detection_confidence = 0.9
        o.raw_ref["document_page_count"] = 20
    group = WholePageSigningVisualRecall._aggregate_edge_fragments(objects)[0]
    assert not WholePageSigningVisualRecall._is_confirmed_object(group)


def test_edge_grouping_does_not_absorb_complete_round_seal():
    round_seal = seal(3, 200)
    round_seal.raw_ref["reason"] = "PAGE_EDGE_ROUND_SEAL"
    groups = WholePageSigningVisualRecall._aggregate_edge_fragments(
        [
            seal(1, 200, SigningVisualObjectType.EDGE_SEAM_SEAL),
            seal(2, 200, SigningVisualObjectType.EDGE_SEAM_SEAL),
            round_seal,
        ]
    )
    assert len(groups) == 2
    assert round_seal in groups


def test_isolated_tiny_edge_speck_is_diagnostic_only():
    speck = seal(kind=SigningVisualObjectType.EDGE_SEAM_SEAL)
    speck.bbox = BBox(x0=594, y0=302, x1=600, y1=309)
    result = SigningVisualRecallResult(additions=[speck])
    assert WholePageSigningVisualRecall().reconcile_diffs(result, [], [], start_index=1) == ([], [])
    assert speck.raw_ref["decision_reason"] == "EDGE_SPECKS_WITHOUT_SEAL_STRUCTURE"


def test_edge_groups_cannot_reuse_and_overwrite_the_same_existing_diff():
    objects = [seal(p, y, SigningVisualObjectType.EDGE_SEAM_SEAL) for p in [1, 2] for y in [80, 580]]
    groups = WholePageSigningVisualRecall._aggregate_edge_fragments(objects)
    existing = DiffItem(
        diff_id="legacy",
        diff_type="ADD",
        source_type="seal",
        compare_evidence=[EvidenceBox(page_no=1, bbox=objects[0].bbox, method="seal_region")],
    )
    seals, signing = WholePageSigningVisualRecall().reconcile_diffs(
        SigningVisualRecallResult(additions=groups), [existing], [], start_index=2
    )
    assert len(seals) == 2 and not signing
    assert len({d.match_score_details["edge_group_id"] for d in seals}) == 2
    assert all(d.quality_status == "NEEDS_REVIEW" for d in seals)
