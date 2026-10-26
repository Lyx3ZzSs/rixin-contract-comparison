import json
from pathlib import Path
from app.models import Clause, CompareTask, DiffItem
from app.services.clause_rewrite_highlights import prepare_clause_highlights
from app.services.diff_events import build_snapshot, event_response


def fixture():
    v=json.loads((Path(__file__).parent/'fixtures/payment_clause_rewrite.json').read_text())
    return DiffItem.model_validate(v['diff']),{s:Clause.model_validate(c) for s,c in v['clauses'].items()}


def test_payment_is_one_modify_with_full_clause_geometry_and_lossless_raw_edits():
    d,c=fixture();before=d.model_dump()
    prepare_clause_highlights([d],[c['original']],[c['compare']])
    t=CompareTask(task_id='test',diffs=[d]);t.event_snapshot=build_snapshot(t)
    es=event_response(t)['events'];assert len(es)==1 and es[0]['direction']=='MODIFY'
    display=es[0]['diff']['clause_display_evidence'];assert display['unit']=='clause'
    for side in ('original','compare'):
        assert len(display[side])==len(c[side].bboxes)
        assert ''.join(e['text'] for e in display[side])==''.join(b.text for b in c[side].bboxes)
        for e,box in zip(display[side],c[side].bboxes):
            assert e['highlight_type']=='MODIFY' and e['page_no']==box.page_no
            assert e['bbox']==box.bbox.model_dump()
        # The midpoint between rows must stay unpainted.
        gap=(c[side].bboxes[0].bbox.y1+c[side].bboxes[1].bbox.y0)/2
        assert not any(e['bbox']['y0']<gap<e['bbox']['y1'] for e in display[side])
        assert getattr(d,side+'_evidence')==getattr(DiffItem.model_validate(before),side+'_evidence')
        assert getattr(d,side+'_change_ranges')==getattr(DiffItem.model_validate(before),side+'_change_ranges')


def test_missing_or_mismatched_source_never_invents_full_clause_box():
    d,c=fixture();c['compare'].text+='额外文字'
    prepare_clause_highlights([d],[c['original']],[c['compare']]);assert d.clause_display_evidence is None


def test_single_word_change_and_fields_keep_precise_evidence():
    for mode in ('single','cover','party'):
        d,c=fixture()
        if mode=='single':d.compare_text=d.original_text.replace('30','60')
        elif mode=='cover':d.source_type='cover'
        else:d.original_text='委托人:甲公司\n法定代表人:许峰\n'+d.original_text
        prepare_clause_highlights([d],[c['original']],[c['compare']]);assert d.clause_display_evidence is None


def test_overlap_with_other_clause_rejects_union_box():
    d,c=fixture();other=c['original'].model_copy(deep=True);other.clause_id='other'
    prepare_clause_highlights([d],[c['original'],other],[c['compare']]);assert d.clause_display_evidence is None


def test_cross_page_boxes_never_join_pages():
    d,c=fixture();c['original'].bboxes[-1].page_no=4;c['original'].page_numbers=[3,4]
    prepare_clause_highlights([d],[c['original']],[c['compare']])
    assert [e.page_no for e in d.clause_display_evidence.original]==[3,3,3,4]


def test_rolled_back_legacy_display_metadata_is_not_reactivated():
    d,c=fixture();payload=d.model_dump()
    for unit in ('field','clause'):
        payload['display_evidence']={'unit':unit,'original':[c['original'].bboxes[0].model_dump()],'compare':[]}
        loaded=DiffItem.model_validate(payload)
        assert loaded.clause_display_evidence is None
        assert 'display_evidence' not in loaded.model_dump()


def test_short_numbered_clause_uses_one_modify_with_full_line_contours():
    v=json.loads((Path(__file__).parent/'fixtures/short_clause_rewrite.json').read_text())
    d=DiffItem.model_validate(v['diff']);cs={s:Clause.model_validate(c) for s,c in v['clauses'].items()}
    before=d.model_dump()
    prepare_clause_highlights([d],[cs['original']],[cs['compare']])
    assert d.clause_display_evidence is not None
    t=CompareTask(task_id='short',diffs=[d]);t.event_snapshot=build_snapshot(t)
    es=event_response(t)['events'];assert len(es)==1 and es[0]['direction']=='MODIFY'
    for s in cs:
        boxes=getattr(d.clause_display_evidence,s)
        assert len(boxes)==1 and boxes[0].bbox==cs[s].bboxes[0].bbox
        assert boxes[0].text==getattr(d,s+'_text') and boxes[0].highlight_type=='MODIFY'
        assert getattr(d,s+'_change_ranges')==getattr(DiffItem.model_validate(before),s+'_change_ranges')


def test_numeric_edits_and_separate_single_glyph_errors_stay_precise():
    from app.services.diff_engine import DiffEngine
    from app.models import BBox,EvidenceBox
    examples=[('5.2 甲方支付100元，乙方于30天内完成。','5.2 甲方支付200元，乙方于60天内完成。'),
              ('5.2 乙方提供含税发票，向有管辖权的法院起诉。','5.2 乙方提供合税发票，向有管轴权的法院起诉。')]
    for a,b in examples:
        _,_,ra,rb=DiffEngine()._changed_snippets(a,b)
        d=DiffItem(diff_id='d',diff_type='MODIFY',clause_no='5.2',original_clause_id='a',compare_clause_id='b',
                   original_text=a,compare_text=b,original_change_ranges=ra,compare_change_ranges=rb)
        def clause(cid,text):
            return Clause(clause_id=cid,clause_no='5.2',text=text,normalized_text=text,
                          bboxes=[EvidenceBox(page_no=1,bbox=BBox(x0=10,y0=20,x1=300,y1=30),text=text)])
        prepare_clause_highlights([d],[clause('a',a)],[clause('b',b)])
        assert d.clause_display_evidence is None
