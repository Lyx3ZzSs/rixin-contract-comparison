from app.models import BBox,CharBox,TextBlock,CompareTask
from app.services.cover.types import CoverField,CoverValuePart
from app.services.cover.comparator import _build_diff
from app.services.diff_events import build_snapshot,event_response


def field(value,key='sign_place'):
    prefix='签订地点：';text=prefix+value
    chars=[CharBox(char=c,page_no=1,bbox=BBox(x0=10+i*10,y0=20,x1=20+i*10,y1=30),text_index=i) for i,c in enumerate(text)]
    block=TextBlock(block_id=value,page_no=1,text=text,bbox=BBox(x0=10,y0=20,x1=10+len(text)*10,y1=30),char_boxes=chars)
    return CoverField(key=key,label='签订地点',value=value,
                      value_parts=[CoverValuePart(block=block,text=value,start=0,end=len(value),block_start=len(prefix))])


def test_whole_location_value_includes_shared_suffix_but_excludes_label():
    d=_build_diff('sign_place',field('广东省广州市'),field('浙江省金华市'),1)
    assert [b.text for b in d.original_evidence]==['广东','广州']
    t=CompareTask(task_id='place',diffs=[d]);t.event_snapshot=build_snapshot(t)
    es=event_response(t)['events'];assert len(es)==1 and es[0]['direction']=='MODIFY'
    display=es[0]['diff']['field_display_evidence']
    assert display['unit']=='field' and display['field_key']=='sign_place'
    for side,value in [('original','广东省广州市'),('compare','浙江省金华市')]:
        assert len(display[side])==1
        box=display[side][0];assert box['text']==value and box['highlight_type']=='MODIFY'
        assert 59<box['bbox']['x0']<61 and 119<box['bbox']['x1']<121


def test_missing_character_geometry_does_not_expand_to_label_or_whole_block():
    a,b=field('广东省广州市'),field('浙江省金华市')
    a.value_parts[0].block.char_boxes.pop()
    d=_build_diff('sign_place',a,b,1)
    assert d.field_display_evidence is None


def test_other_cover_fields_and_one_sided_changes_keep_current_behavior():
    assert _build_diff('buyer',field('甲公司','buyer'),field('乙公司','buyer'),1).field_display_evidence is None
    assert _build_diff('sign_place',None,field('浙江省金华市'),1).field_display_evidence is None
