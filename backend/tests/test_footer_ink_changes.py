"""Registered page changes survive origin uncertainty without confirming handwriting."""
import fitz
import pytest
from app.models import BBox, CompareTask, DiffItem, EvidenceBox
from app.services.visual_impressions import reconcile_impressions
from app.services.comparison_observations import separate_observations
from app.services.observation_reconciliation import reconcile_observations
from app.services.diff_events import event_response


def documents(tmp_path, kind='ink'):
    paths=[]
    for side in ['original','compare']:
        doc=fitz.open();p=doc.new_page(width=600,height=840)
        if side=='compare':
            if kind=='ink':
                for a,b in [((40,775),(59,794)),((42,793),(68,779)),((54,773),(54,798))]:
                    p.draw_line(a,b,color=(.7,)*3,width=1.5)
            elif kind=='solid':p.draw_rect(fitz.Rect(40,775,68,798),fill=(.7,)*3,color=None)
            elif kind=='edge':p.draw_line((40,770),(40,830),color=(.7,)*3,width=1)
            elif kind=='scan_lines':
                for x in (42,52,62):p.draw_line((x,775),(x,798),color=(.88,)*3,width=.5)
            elif kind=='texture':
                for x in range(40,69,4):
                    for y in range(775,799,4):p.draw_rect(fitz.Rect(x,y,x+.6,y+.6),color=None,fill=(.93,)*3)
            elif kind=='skin':p.draw_rect(fitz.Rect(40,775,68,798),fill=(.8,.4,.2),color=None)
        path=tmp_path/f'{side}.pdf';doc.save(path);doc.close();paths.append(path)
    return paths


def candidate(method='footer_visual_registered'):
    return DiffItem(diff_id='visual',diff_type='ADD',source_type='header_footer',
        content_recognition_reason='VISUAL_FOOTER_CONTENT_UNVERIFIED',
        compare_evidence=[EvidenceBox(page_no=1,bbox=BBox(x0=38,y0=770,x1=71,y1=802),method=method)])


@pytest.mark.parametrize('mirrored',[False,True])
def test_registered_faint_page_change_is_counted_visible_and_not_called_a_signature(tmp_path,monkeypatch,mirrored):
    paths=documents(tmp_path)
    if mirrored:
        from app.services.visual_impressions import ImpressionVerifier
        monkeypatch.setattr(ImpressionVerifier,'mirrored_peer',lambda *a,**k:{
            'reason':'MIRRORED_ADJACENT_PAGE_IMPRESSION','mirror_score':.52,'direct_score':.3,
            'counterpart':{'page':2,'bbox':[400,770,450,810],'rotation_degrees':0}})
    diffs,observations,decisions=reconcile_impressions([candidate()],*paths)
    task=CompareTask(task_id='test',diffs=diffs)
    separate_observations(task);task.comparison_observations.extend(observations)
    reconcile_observations(task,*paths)
    response=event_response(task)
    assert response['event_count']==1
    assert not response['comparison_observations']
    change=task.diffs[0]
    assert change.diff_type=='ADD' and change.content_recognition_status=='CONFIRMED'
    assert change.match_score_details['recognition_scope']=='ink_presence'
    assert change.match_score_details['handwriting_text_status']=='NEEDS_REVIEW'
    assert '手写签注' not in change.title and not change.compare_change_ranges
    assert ('疑似透印' in change.readable_change)==mirrored
    assert change.compare_evidence[0].bbox.y1>=797


@pytest.mark.parametrize('kind',['solid','edge','skin','texture','scan_lines'])
def test_artifacts_are_not_promoted_to_page_ink_changes(tmp_path,kind):
    paths=documents(tmp_path,kind)
    diffs,_,_=reconcile_impressions([candidate()],*paths)
    assert not any(d.match_score_details.get('object_type')=='FOOTER_INK_CHANGE' for d in diffs)


def test_ocr_guess_without_registered_change_cannot_promote_faint_ink(tmp_path):
    paths=documents(tmp_path)
    diffs,_,_=reconcile_impressions([candidate('header_footer')],*paths)
    assert not any(d.match_score_details.get('object_type')=='FOOTER_INK_CHANGE' for d in diffs)


def test_duplicate_registered_candidates_count_once(tmp_path):
    paths=documents(tmp_path);other=candidate();other.diff_id='other'
    diffs,_,_=reconcile_impressions([candidate(),other],*paths)
    assert len([d for d in diffs if d.match_score_details.get('object_type')=='FOOTER_INK_CHANGE'])==1


def test_ink_already_owned_by_formal_signature_does_not_count_twice(tmp_path):
    from app.services.footer_ink_changes import reconcile_ink_ownership
    paths=documents(tmp_path)
    changes,_,_=reconcile_impressions([candidate()],*paths)
    ink=next(d for d in changes if d.match_score_details.get('object_type')=='FOOTER_INK_CHANGE')
    owner=DiffItem(diff_id='signature',diff_type='ADD',source_type='header_footer',
        content_recognition_status='CONFIRMED',compare_evidence=ink.compare_evidence)
    result=reconcile_ink_ownership([owner,ink],*paths)
    assert len(result)==1 and result[0].diff_id=='signature'
    assert result[0].match_score_details['owned_ink_changes'][0]['diff_id']==ink.diff_id


def test_recovered_crop_excludes_an_adjacent_already_counted_signature(tmp_path):
    from app.services.footer_ink_changes import reconcile_ink_ownership
    paths=documents(tmp_path)
    doc=fitz.open(paths[1]);p=doc[0]
    p.draw_line((15,770),(15,802),color=(.7,)*3,width=5)
    for a,b in [((90,775),(109,794)),((92,793),(118,779)),((104,773),(104,798))]:
        p.draw_line(a,b,color=(.7,)*3,width=1.5)
    doc.saveIncr();doc.close()
    evidence=EvidenceBox(page_no=1,bbox=BBox(x0=10,y0=770,x1=121,y1=802),method='footer_ink_change')
    ink=DiffItem(diff_id='wide',diff_type='ADD',source_type='header_footer',compare_evidence=[evidence],
        match_score_details={'object_type':'FOOTER_INK_CHANGE'})
    owner=DiffItem(diff_id='old',diff_type='ADD',source_type='header_footer',
        compare_evidence=[evidence.model_copy(update={'bbox':BBox(x0=38,y0=770,x1=71,y1=802)})])
    result=reconcile_ink_ownership([owner,ink],*paths)
    assert len(result)==2 and result[1].compare_evidence[0].bbox.x0>85
    assert result[0].compare_evidence[0].bbox.x1==71
