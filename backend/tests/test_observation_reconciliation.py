import fitz
from app.models import BBox, CompareTask, ComparisonObservation, DiffItem, EvidenceBox
from app.services.observation_reconciliation import reconcile_observations


def observation(name, box, source='header_footer', page=1):
    return ComparisonObservation(observation_id=name, reason='FAINT_INK_OR_SCAN_ARTIFACT_UNRESOLVED',
        source_type=source, compare_evidence=[EvidenceBox(page_no=page,bbox=BBox(x0=box[0],y0=box[1],x1=box[2],y1=box[3]))])


def pdf(tmp_path, shapes):
    path=tmp_path/'scan.pdf'
    doc=fitz.open();p=doc.new_page(width=200,height=300)
    for box,color in shapes:p.draw_rect(fitz.Rect(box),color=None,fill=color)
    doc.save(path);doc.close()
    return path


def test_noise_and_scan_corner_are_not_independent_review_objects(tmp_path):
    path=pdf(tmp_path,[((30,275,31,278),(.7,)*3),((165,290,195,300),(0,)*3),((60,265,80,285),(.7,)*3)])
    task=CompareTask(task_id='x',comparison_observations=[observation('speck',(30,275,31,278)),
        observation('corner',(190,290,195,299)),observation('real-faint',(60,265,80,285))])
    reconcile_observations(task,path,path)
    assert [o.observation_id for o in task.comparison_observations]==['real-faint']
    assert len(task.metrics['observation_reconciliation'])==2


def test_tight_ink_ownership_consumes_blank_padded_box_without_confirming_text(tmp_path):
    path=pdf(tmp_path,[((60,270,80,285),(.1,)*3)])
    main=DiffItem(diff_id='main',diff_type='ADD',source_type='header_footer',content_recognition_status='NEEDS_REVIEW',
        compare_evidence=[EvidenceBox(page_no=1,bbox=BBox(x0=59,y0=269,x1=81,y1=287))])
    task=CompareTask(task_id='x',diffs=[main],comparison_observations=[observation('duplicate',(58,255,82,288))])
    reconcile_observations(task,path,path)
    assert not task.comparison_observations
    assert task.diffs[0].content_recognition_status=='NEEDS_REVIEW'
    assert task.diffs[0].match_score_details['observation_evidence'][0]['observation_id']=='duplicate'


def test_nested_observations_merge_but_disjoint_and_other_kinds_survive(tmp_path):
    path=pdf(tmp_path,[((60,265,80,285),(.7,)*3),((100,265,120,285),(.7,)*3)])
    task=CompareTask(task_id='x',comparison_observations=[observation('outer',(60,265,80,285)),
        observation('inner',(62,267,70,275)),observation('other',(100,265,120,285)),
        observation('seal',(10,280,11,282),'seal'),observation('table',(20,280,21,282),'table')])
    reconcile_observations(task,path,path)
    assert {o.observation_id for o in task.comparison_observations}=={'outer','other','seal','table'}


def test_missing_pdf_or_manually_reviewed_observation_is_never_silently_removed(tmp_path):
    o=observation('reviewed',(30,275,31,278));o.review_status='CONFIRMED'
    path=pdf(tmp_path,[((30,275,31,278),(.7,)*3)])
    task=CompareTask(task_id='x',comparison_observations=[o])
    reconcile_observations(task,path,path)
    assert task.comparison_observations==[o]
    task.comparison_observations=[observation('unobserved',(30,275,31,278))]
    reconcile_observations(task,'/missing','/missing')
    assert len(task.comparison_observations)==1


def test_partial_unreadable_object_has_one_owner_but_adjacent_ink_remains(tmp_path):
    path=pdf(tmp_path,[((40,270,50,295),(.1,)*3),((55,270,70,290),(.7,)*3)])
    main=DiffItem(diff_id='main',diff_type='ADD',source_type='header_footer',content_recognition_status='NEEDS_REVIEW',
        compare_evidence=[EvidenceBox(page_no=1,bbox=BBox(x0=39,y0=266,x1=51,y1=290))])
    o=observation('partial',(40,270,50,295));o.reason='VISUAL_FOOTER_CONTENT_UNVERIFIED'
    task=CompareTask(task_id='x',diffs=[main],comparison_observations=[o,observation('neighbour',(55,270,70,290))])
    reconcile_observations(task,path,path)
    assert [o.observation_id for o in task.comparison_observations]==['neighbour']
    assert task.diffs[0].content_recognition_status=='NEEDS_REVIEW'


def test_context_mirror_explains_candidate_but_never_a_real_dark_mark():
    import cv2
    import numpy as np
    from app.services.visual_impressions import ImpressionVerifier
    from app.services.observation_reconciliation import _context_mirror, _pixels
    rng=np.random.default_rng(41);shape=np.zeros((60,70),np.uint8)
    for _ in range(9):
        x,y=rng.integers(5,45,2);cv2.line(shape,(int(x),int(y)),(int(x+17),int(y+9)),255,3)
    first=np.full((200,500,3),255,np.uint8);second=first.copy()
    first[60:120,60:130][shape>0]=180
    second[60:120,370:440][cv2.flip(shape,1)>0]=20
    obj=ImpressionVerifier('/missing');obj.render=lambda n:{1:first,2:second}.get(n)
    e=EvidenceBox(page_no=1,bbox=BBox(x0=40,y0=40,x1=87,y1=80))
    assert _context_mirror(obj,e,_pixels(first,e.bbox)[2])['explained_ink_ratio']>=.7
    first[60:120,60:130][shape>0]=20
    assert _context_mirror(obj,e,_pixels(first,e.bbox)[2]) is None


def test_small_nested_faint_fragment_can_use_full_objects_context():
    import cv2
    import numpy as np
    from app.services.visual_impressions import ImpressionVerifier
    from app.services.observation_reconciliation import _context_mirror, _pixels
    shape=np.zeros((60,100),np.uint8)
    cv2.putText(shape,'R7',(3,45),cv2.FONT_HERSHEY_SIMPLEX,1.5,255,3)
    first=np.full((200,500,3),255,np.uint8);second=first.copy()
    first[60:120,60:160][shape>0]=180
    second[60:120,340:440][cv2.flip(shape,1)>0]=20
    obj=ImpressionVerifier('/missing');obj.render=lambda n:{1:first,2:second}.get(n)
    outer=BBox(x0=40,y0=40,x1=107,y1=80)
    ys,xs=np.nonzero(shape);x,y=int(xs[len(xs)//2]),int(ys[len(ys)//2])
    e=EvidenceBox(page_no=1,bbox=BBox(x0=(60+x)/1.5,y0=(60+y)/1.5,x1=(65+x)/1.5,y1=(65+y)/1.5))
    proof=_context_mirror(obj,e,_pixels(first,e.bbox)[2],[outer])
    assert proof and proof['explained_ink_ratio']>=.7


def test_observation_spanning_two_counted_objects_is_not_a_third_overlay(tmp_path):
    path=pdf(tmp_path,[((40,270,55,290),(.7,)*3),((70,270,85,290),(.7,)*3)])
    diffs=[DiffItem(diff_id=f'ink-{x}',diff_type='ADD',source_type='header_footer',
        compare_evidence=[EvidenceBox(page_no=1,bbox=BBox(x0=x,y0=269,x1=x+16,y1=291))]) for x in [39,69]]
    task=CompareTask(task_id='x',diffs=diffs,comparison_observations=[observation('combined',(39,269,86,291))])
    reconcile_observations(task,path,path)
    assert len(task.diffs)==2 and not task.comparison_observations
    assert task.metrics['observation_reconciliation'][0]['owner_diff_ids']==['ink-39','ink-69']
