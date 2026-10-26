import pytest

from app.models import BBox, DiffItem, EvidenceBox
from app.services.evidence_locator import EvidenceLocator


def evidence(text, bbox, method):
    return EvidenceBox(page_no=27,bbox=BBox(x0=bbox[0],y0=bbox[1],x1=bbox[2],y1=bbox[3]),
                       method=method,text=text,highlight_type="ADD",confidence=.9)


def items():
    seal=DiffItem(diff_id="seal",diff_type="ADD",source_type="seal",title="甲方 · 公司圆章",
                  compare_text="公司圆章（章文识别待复核）",match_method="whole_page_visual_recall",
                  match_score_details={"object_type":"COMPANY_ROUND_SEAL"},
                  compare_evidence=[evidence("公司圆章（章文识别待复核）",[142,594,257,705],"whole_page_visual_object")])
    text=DiffItem(diff_id="text",diff_type="ADD",source_type="clause",compare_text="章",
                  compare_evidence=[evidence("章",[202.7,657.2,232.8,685.8],"char_exact")])
    return seal,text


@pytest.mark.parametrize("reverse",[False,True])
def test_overlapping_text_character_cannot_erase_round_seal_location(reverse):
    seal,text=items();diffs=[seal,text][::(-1 if reverse else 1)]
    EvidenceLocator().locate(diffs)
    assert len(seal.compare_evidence)==1
    assert len(text.compare_evidence)==1
    assert seal.compare_evidence[0].bbox==BBox(x0=142,y0=594,x1=257,y1=705)


def test_same_text_evidence_still_deduplicates():
    _,a=items();b=a.model_copy(deep=True,update={"diff_id":"second"})
    EvidenceLocator().locate([a,b])
    assert len(a.compare_evidence)+len(b.compare_evidence)==1


def test_independent_embedded_text_deletion_retains_original_seal_geometry():
    seal,text=items()
    for d in (seal,text):
        d.diff_type="DELETE"
        d.original_evidence=[e.model_copy(update={"highlight_type":"DELETE"}) for e in d.compare_evidence]
        d.compare_evidence=[]
    EvidenceLocator().locate([seal,text])
    assert len(seal.original_evidence)==len(text.original_evidence)==1
