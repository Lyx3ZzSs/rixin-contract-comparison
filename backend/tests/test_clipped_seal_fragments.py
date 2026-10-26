import json
from pathlib import Path

from app.models import BBox, DiffItem, EvidenceBox
from app.services.signing_region.models import SigningVisualObject as Object
from app.services.signing_region.models import SigningVisualObjectType as Kind
from app.services.signing_region.visual_recall import WholePageSigningVisualRecall as Recall, SigningVisualRecallResult


def fragments():
    return [Object.model_validate(o) for o in json.loads(
        (Path(__file__).parent / 'fixtures/clipped_seal_fragments.json').read_text())]


def test_clipped_stamp_fragments_have_one_owner_across_pages():
    objects = Recall._dedupe_objects(fragments())
    for page in (1, 3):
        same_page = [o for o in objects if o.page_no == page]
        assert len(same_page) == 1
        assert same_page[0].object_type == Kind.EDGE_SEAM_SEAL
        assert same_page[0].party_role == 'unknown'
        assert same_page[0].bbox.y0 < 210 and same_page[0].bbox.y1 > 320
    additions = Recall._aggregate_edge_fragments(objects)
    assert len(additions) == 1
    assert set(additions[0].supporting_pages) == {1, 3, 5}
    existing = [DiffItem(diff_id=f'D{p}', diff_type='ADD', source_type='seal',
                        compare_evidence=[EvidenceBox(page_no=p, bbox=BBox(x0=552,y0=199,x1=597,y1=325))])
                for p in (1,3,5)]
    seals, signing = Recall().reconcile_diffs(SigningVisualRecallResult(additions=additions),existing,[],start_index=9)
    assert len(seals) == 1 and not signing
    assert {e.page_no for e in seals[0].compare_evidence} == {1,3,5}


def test_separate_edge_stamps_and_complete_circle_stay_separate():
    objects = [o for o in fragments() if o.page_no == 1]
    shifted = [o.model_copy(deep=True) for o in objects]
    for o in shifted:
        o.object_id += '-lower'
        o.bbox = BBox(x0=o.bbox.x0,y0=o.bbox.y0+210,x1=o.bbox.x1,y1=o.bbox.y1+210)
    circle = objects[0].model_copy(deep=True)
    circle.object_id = 'complete-circle'
    circle.bbox = BBox(x0=510,y0=100,x1=590,y1=180)
    result = Recall._dedupe_objects([*objects,*shifted,circle])
    assert len(result) == 3
    assert next(o for o in result if o.object_id=='complete-circle').object_type == Kind.COMPANY_ROUND_SEAL


def test_inset_round_object_is_not_retyped_from_shape_alone():
    o = fragments()[0]
    o.bbox = BBox(x0=510,y0=210,x1=550,y1=310)
    result = Recall._dedupe_objects([o])
    assert result[0].object_type == Kind.COMPANY_ROUND_SEAL


def test_two_small_complete_circles_touching_edge_are_not_one_fragment():
    circles = []
    for index, y in enumerate((100, 145)):
        o = fragments()[0]
        o.object_id = f'complete-{index}'
        o.bbox = BBox(x0=557,y0=y,x1=597,y1=y+40)
        circles.append(o)
    result = Recall._dedupe_objects(circles)
    assert len(result) == 2
    assert all(o.object_type == Kind.COMPANY_ROUND_SEAL for o in result)
