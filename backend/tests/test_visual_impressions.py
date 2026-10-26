import pytest
import numpy as np
from app.models import BBox, EvidenceBox
from app.services.visual_impressions import ImpressionVerifier


def verifier(image):
    obj = ImpressionVerifier("/not/a/file")
    obj.render = lambda n: image if n == 1 else None
    return obj


def test_real_dark_ink_survives_with_adjacent_faint_impression_separated():
    image = np.full((200, 400, 3), 255, np.uint8)
    image[50:100, 40:70] = 185
    image[50:100, 200:230] = 20
    e = EvidenceBox(page_no=1, bbox=BBox(x0=20, y0=20, x1=170, y1=80))
    definite, faint, proof = verifier(image).inspect(e)
    assert definite.x0 >= 130 and faint.x1 < 60 and proof is None


def test_faint_real_ink_is_retained_for_review_without_mirror_proof():
    image = np.full((200, 200, 3), 255, np.uint8)
    image[40:90, 30:70] = 185
    e = EvidenceBox(page_no=1, bbox=BBox(x0=10, y0=10, x1=90, y1=90))
    definite, faint, proof = verifier(image).inspect(e)
    assert definite is None and faint is not None and proof is None


def test_strong_stamp_is_not_removed_as_a_pale_impression():
    image = np.full((200, 200, 3), 255, np.uint8)
    image[40:90, 30:70] = [220, 30, 30]
    e = EvidenceBox(page_no=1, bbox=BBox(x0=10, y0=10, x1=90, y1=90))
    assert verifier(image).inspect(e, seal=True) is None


def test_mirror_requires_shape_agreement_with_adjacent_page():
    import cv2

    rng = np.random.default_rng(41)
    shape = np.zeros((60, 70), np.uint8)
    for _ in range(9):
        x, y = rng.integers(5, 45, 2)
        cv2.line(shape, (int(x), int(y)), (int(x + 17), int(y + 9)), 255, 3)
    first = np.full((200, 500, 3), 255, np.uint8)
    second = first.copy()
    first[60:120, 60:130][shape > 0] = 180
    second[60:120, 370:440][cv2.flip(shape, 1) > 0] = 20
    obj = verifier(first)
    obj.render = lambda n: {1: first, 2: second}.get(n)
    evidence = EvidenceBox(page_no=1, bbox=BBox(x0=40, y0=40, x1=87, y1=80))
    result = obj.inspect(evidence)
    assert result[2]["reason"] == "MIRRORED_ADJACENT_PAGE_IMPRESSION"
    assert result[2]["counterpart"]["page"] == 2


def test_faint_detected_stamp_without_adjacent_counterpart_is_preserved():
    image = np.full((200, 200, 3), 255, np.uint8)
    image[40:90, 30:70] = [230, 200, 200]
    e = EvidenceBox(page_no=1, bbox=BBox(x0=10, y0=10, x1=90, y1=90))
    assert verifier(image).inspect(e, seal=True) is None


def test_below_verification_threshold_is_unresolved_not_confirmed_ink():
    image = np.full((200, 200, 3), 255, np.uint8)
    image[40:90, 30:70] = 230
    e = EvidenceBox(page_no=1, bbox=BBox(x0=10, y0=10, x1=90, y1=90))
    definite, faint, proof = verifier(image).inspect(e)
    assert definite is None and faint == e.bbox and proof is None


def test_dark_fragments_of_translucent_mirror_require_darker_peer_and_coverage():
    import cv2

    rng = np.random.default_rng(41)
    shape = np.zeros((60, 70), np.uint8)
    for _ in range(9):
        x, y = rng.integers(5, 45, 2)
        cv2.line(shape, (int(x), int(y)), (int(x + 17), int(y + 9)), 255, 3)
    first = np.full((200, 500, 3), 255, np.uint8)
    second = first.copy()
    first[60:120, 60:130][shape > 0] = 145
    first[60:120, 60:130][(shape > 0) & (np.indices(shape.shape)[1] % 2 == 0)] = 80
    second[60:120, 370:440][cv2.flip(shape, 1) > 0] = 20
    obj = verifier(first)
    obj.render = lambda n: {1: first, 2: second}.get(n)
    evidence = EvidenceBox(page_no=1, bbox=BBox(x0=40, y0=40, x1=87, y1=80))
    result = obj.inspect(evidence)
    assert result[0] is None
    assert result[2]['explained_strong_ink_ratio'] >= .9
    # A real dark mark is retained, even when a mirrored shape exists nearby.
    first[60:120, 60:130][shape > 0] = 20
    assert obj.inspect(evidence) is None


@pytest.mark.parametrize('side,direction', [('compare','ADD'),('original','DELETE')])
def test_mirrored_seal_is_a_page_change_with_origin_evidence(monkeypatch, side, direction):
    from app.models import DiffItem, CompareTask
    from app.services.visual_impressions import reconcile_impressions
    from app.services.diff_events import event_response
    box=BBox(x0=100,y0=140,x1=210,y1=260)
    proof={'reason':'MIRRORED_ADJACENT_PAGE_IMPRESSION','mirror_score':.615,'direct_score':.432,
           'counterpart':{'page':13,'bbox':[350,140,460,260],'rotation_degrees':0}}
    monkeypatch.setattr(ImpressionVerifier,'inspect',lambda self,evidence,**kwargs:(None,box,proof))
    raw=DiffItem(diff_id='seal',diff_type=direction,source_type='seal',
                 **{side+'_evidence':[EvidenceBox(page_no=12,bbox=box)]})
    diffs,observations,decisions=reconcile_impressions([raw],'/missing','/missing')
    assert len(diffs)==1 and not observations
    diff=diffs[0]
    assert diff.diff_type==direction and getattr(diff,side+'_evidence')==getattr(raw,side+'_evidence')
    assert '疑似透印' in diff.title and 'PDF 第 13 页' in diff.readable_change
    assert diff.match_score_details['seal_origin_decision']=='SUSPECTED_MIRROR'
    assert diff.match_score_details['impression_evidence'][0]['counterpart']['page']==13
    assert decisions[0]['retained_bbox']==box.model_dump()
    response=event_response(CompareTask(task_id='x',diffs=diffs,comparison_observations=observations))
    assert response['event_count']==1 and response['comparison_observation_count']==0



def test_unresolved_seal_keeps_its_identity_and_footer_mirror_stays_suppressed(monkeypatch):
    from app.models import DiffItem
    from app.services.visual_impressions import reconcile_impressions
    box=BBox(x0=100,y0=780,x1=200,y1=820)
    def inspect(self,evidence,**kwargs):
        reason='FAINT_INK_OR_SCAN_ARTIFACT_UNRESOLVED' if kwargs.get('seal') else 'MIRRORED_ADJACENT_PAGE_IMPRESSION'
        return None,box,{'reason':reason,'mirror_score':.458,'direct_score':.419}
    monkeypatch.setattr(ImpressionVerifier,'inspect',inspect)
    diffs=[DiffItem(diff_id=kind,diff_type='ADD',source_type=kind,compare_evidence=[EvidenceBox(
        page_no=12,bbox=box,method='footer_visual_registered' if kind=='header_footer' else 'seal_region')])
        for kind in ['seal','header_footer']]
    kept,observations,_=reconcile_impressions(diffs,'/missing','/missing')
    assert len(kept)==1 and not observations
    assert kept[0].source_type=='seal' and kept[0].diff_type=='ADD'
    assert kept[0].quality_status=='NEEDS_REVIEW'
    assert kept[0].match_score_details['seal_change_decision']=='CANDIDATE'
    assert kept[0].match_score_details['impression_evidence'][0]['mirror_score']==.458
