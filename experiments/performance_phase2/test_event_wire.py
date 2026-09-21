import pytest
from event_wire import pack,unpack


def test_round_trip_preserves_order_duplicates_coordinates_ids_and_empty_sides():
    evidence={'page_no':3,'bbox':{'x0':1.25,'y0':2.,'x1':30.,'y1':4.},'char_boxes':[{'char':'甲','bbox':[1,2,3,4]}]}
    diff={'diff_id':'raw1','original_evidence':[evidence,evidence],'compare_evidence':[],'review_status':'CONFIRMED'}
    source={'events':[{'event_id':'ev1','audit_item_ids':['audit1'],'diff':dict(diff,diff_id='ev1'),'children':[diff]}],'revision':4,'page_alignment':[{'original_pages':[3],'compare_pages':[4]}]}
    wire=pack(source)
    assert len(wire['evidence_by_id'])==1
    restored=unpack(wire)
    assert restored==source
    restored['events'][0]['diff']['original_evidence'][0]['bbox']['x0']=999
    assert restored['events'][0]['children'][0]['original_evidence'][0]['bbox']['x0']==1.25
    wire['events'][0]['diff']['original_evidence_refs'][0]='missing'
    with pytest.raises(KeyError):unpack(wire)
