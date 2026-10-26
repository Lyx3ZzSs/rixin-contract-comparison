"""Retain registered page ink independently from its origin and OCR reading.

Only existing one-sided, registered detections can establish page presence.
Mirror evidence explains origin; it cannot cancel that page-local observation.
"""
from hashlib import sha256

import cv2
import numpy as np

from app.models import BBox, DiffItem, EvidenceBox

OBJECT = 'FOOTER_INK_CHANGE'


def _coverage(a, b):
    return (max(0, min(a.x1,b.x1)-max(a.x0,b.x0)) *
            max(0, min(a.y1,b.y1)-max(a.y0,b.y0)) /
            max(.01,(a.x1-a.x0)*(a.y1-a.y0)))


def retain_registered_ink(diffs, decisions, verifiers):
    # Import at call time: the observation verifier uses ImpressionVerifier too.
    from app.services.observation_reconciliation import _pixels, _scan_corner
    registered=[]
    for d in diffs:
        if d.source_type!='header_footer' or d.diff_type not in {'ADD','DELETE'}:
            continue
        side='compare' if d.diff_type=='ADD' else 'original'
        proofs=[e for e in getattr(d,side+'_evidence') if e.method=='footer_visual_registered']
        proofs.extend(EvidenceBox.model_validate(p['evidence'])
                      for p in d.match_score_details.get('footer_visual_evidence',[])
                      if p.get('side')==side and p.get('diff_type')==d.diff_type)
        registered.extend((side,d.diff_id,e) for e in proofs)
    footer_ids={d.diff_id for d in diffs if d.source_type=='header_footer'}
    changes=[]
    for decision in decisions:
        if decision['diff_id'] not in footer_ids:continue
        b=BBox.model_validate(decision['faint_bbox']);side=decision['side'];page=decision['page']
        supports=[(ident,e) for s,ident,e in registered if s==side and e.page_no==page
                  and max(_coverage(b,e.bbox),_coverage(e.bbox,b))>=.6]
        if not supports:
            continue
        image=verifiers[side].render(page)
        if (image is None or b.x1>image.shape[1]/1.5*.34 or b.y0<image.shape[0]/1.5*.85
                or _scan_corner(image,b)):
            continue
        # A faint component box may have lost pixels at the verifier's stricter
        # gray threshold. For an entirely faint candidate, retain its registered
        # extent; never expand across a separately retained dark signature.
        if decision.get('retained_bbox') is None:
            local=[e.bbox for _,e in supports if _coverage(b,e.bbox)>=.9
                   and (e.bbox.x1-e.bbox.x0)<=2*(b.x1-b.x0)
                   and (e.bbox.y1-e.bbox.y0)<=3*(b.y1-b.y0)]
            if local:b=min(local,key=lambda box:(box.x1-box.x0)*(box.y1-box.y0))
        crop=image[max(0,int(b.y0*1.5)):int(b.y1*1.5)+1,
                   max(0,int(b.x0*1.5)):int(b.x1*1.5)+1]
        gray=cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY)
        neutral=cv2.cvtColor(crop,cv2.COLOR_RGB2HSV)[:,:,1]<80
        seeds=((gray<230)&neutral).astype(np.uint8)
        _,_,stats,_=cv2.connectedComponentsWithStats(seeds)
        # A few weak pixels spread across a wide box are scan texture, not a
        # supported stroke. Long one-pixel edge lines also lack local shape.
        if not any(area>=15 and w>=3 and h>=3 and max(w/h,h/w)<=6
                   for _,_,w,h,area in stats[1:]):continue
        pixels=_pixels(image,b,threshold=240)
        if pixels is None:
            continue
        xs,ys,_=pixels
        tight=BBox(x0=float(xs.min()),y0=float(ys.min()),x1=float(xs.max()+2/3),y1=float(ys.max()+2/3))
        # Blank OCR padding must not defeat corroboration, but mere contact
        # with a tiny registered fragment must not confirm a larger crop.
        supports=[(ident,e) for ident,e in supports if _coverage(tight,e.bbox)>=.6]
        if not supports:continue
        w,h=tight.x1-tight.x0,tight.y1-tight.y0
        # Exclude specks, narrow border fragments and solid scan/skin shadows.
        # Contrast is deliberately NOT evidence against page presence.
        if len(xs)<30 or w<8 or h<6 or max(w/h,h/w)>8 or len(xs)/(w*h*2.25)>.55:
            continue
        evidence=supports[0][1].model_copy(update={'bbox':tight,'char_boxes':[],
            'method':'footer_ink_change','text':'检测到左下角印迹','text_confidence':None})
        existing=next((d for d in changes if d.diff_type==('ADD' if side=='compare' else 'DELETE')
                       and getattr(d,side+'_evidence')[0].page_no==page
                       and max(_coverage(tight,getattr(d,side+'_evidence')[0].bbox),
                               _coverage(getattr(d,side+'_evidence')[0].bbox,tight))>=.9),None)
        proof=decision.get('proof') or {}
        if existing:
            box=getattr(existing,side+'_evidence')[0].bbox
            evidence.bbox=BBox(x0=min(box.x0,tight.x0),y0=min(box.y0,tight.y0),
                              x1=max(box.x1,tight.x1),y1=max(box.y1,tight.y1))
            setattr(existing,side+'_evidence',[evidence])
            change=existing
        else:
            digest=sha256(f'{side}:{page}:{tight.model_dump_json()}'.encode()).hexdigest()[:12]
            change=DiffItem(diff_id=f'footer-ink-{side}-{page:04}-{digest}',
                diff_type='ADD' if side=='compare' else 'DELETE',source_type='header_footer',
                title=f'第{page}页左下角印迹',
                **{side+'_text':evidence.text,side+'_snippet':evidence.text,side+'_evidence':[evidence]},
                quality_status='NEEDS_REVIEW',content_recognition_status='CONFIRMED',
                content_recognition_reason='INK_PRESENCE_CONFIRMED_TEXT_UNREAD',
                review_flags=['FOOTER_INK_ORIGIN_REVIEW'],
                match_score_details={'object_type':OBJECT,'recognition_scope':'ink_presence',
                    'handwriting_text_status':'NEEDS_REVIEW','footer_origin_decision':'UNRESOLVED',
                    'registered_evidence':[],'impression_evidence':[]})
            changes.append(change)
        detail=change.match_score_details
        for ident,e in supports:
            support={'diff_id':ident,'evidence':e.model_dump(mode='json')}
            if support not in detail['registered_evidence']:detail['registered_evidence'].append(support)
        if proof and proof not in detail['impression_evidence']:detail['impression_evidence'].append(proof)
        if proof.get('reason')=='MIRRORED_ADJACENT_PAGE_IMPRESSION':
            detail['footer_origin_decision']='SUSPECTED_MIRROR'
        mirror=detail['footer_origin_decision']=='SUSPECTED_MIRROR'
        change.title=f'第{page}页左下角印迹（'+('疑似透印' if mirror else '性质待复核')+'）'
        change.readable_change=('本页相对另一侧存在印迹变化，已计入差异；'
            +('疑似透印，可能来自邻页，不代表已确认一次独立签字。' if mirror else '具体字迹及是否为独立手写签注需复核。'))
        decision['page_change_diff_id']=change.diff_id
    return changes


def reconcile_ink_ownership(diffs, original_path, compare_path):
    """One physical change has one owner, even if OCR and visual routes overlap."""
    from app.services.visual_impressions import ImpressionVerifier
    from app.services.observation_reconciliation import _pixels
    verifiers={s:ImpressionVerifier(p) for s,p in [('original',original_path),('compare',compare_path)]}
    kept=[]
    try:
        for d in diffs:
            if d.match_score_details.get('object_type')!=OBJECT:
                kept.append(d);continue
            side='compare' if d.diff_type=='ADD' else 'original';e=getattr(d,side+'_evidence')[0]
            image=verifiers[side].render(e.page_no)
            pixels=_pixels(image,e.bbox,threshold=240) if image is not None else None
            owners=[]
            if pixels is not None:
                xs,ys,_=pixels
                claimed=np.zeros(len(xs),dtype=bool)
                for other in diffs:
                    if other is d or other.source_type!='header_footer' or other.diff_type!=d.diff_type:
                        continue
                    if other.match_score_details.get('object_type')==OBJECT:continue
                    if other.content_recognition_reason=='VISUAL_FOOTER_CONTENT_UNVERIFIED':continue
                    owned=np.zeros(len(xs),dtype=bool)
                    for box in getattr(other,side+'_evidence'):
                        if box.page_no!=e.page_no:continue
                        owned|=((xs>=box.bbox.x0-1)&(xs<=box.bbox.x1+1)&
                                (ys>=box.bbox.y0-1)&(ys<=box.bbox.y1+1))
                    if np.any(owned):
                        claimed|=owned
                    if np.mean(owned)>=.9:owners.append(other)
                # A wide detector rectangle can cover an adjacent signature.
                # Project only the still-unowned ink, not that entire rectangle.
                if np.any(claimed) and not owners:
                    remaining=~claimed
                    # A detached scanner-edge pixel must not stretch the new
                    # box back across the signature whose ink we just removed.
                    px=np.rint((xs-xs.min())*1.5).astype(int)
                    py=np.rint((ys-ys.min())*1.5).astype(int)
                    mask=np.zeros((py.max()+1,px.max()+1),np.uint8)
                    mask[py[remaining],px[remaining]]=1
                    _,labels,stats,_=cv2.connectedComponentsWithStats(mask)
                    valid=[i for i,(_,_,w,h,area) in enumerate(stats[1:],1)
                           if area>=8 and not (min(w,h)<=2 and max(w,h)/min(w,h)>=8)
                           and not (area/(w*h)>.65 and max(w/h,h/w)>=4)]
                    remaining &= np.isin(labels[py,px],valid)
                    if remaining.sum()>=30:
                        rx,ry=xs[remaining],ys[remaining]
                        tight=BBox(x0=float(rx.min()),y0=float(ry.min()),
                                   x1=float(rx.max()+2/3),y1=float(ry.max()+2/3))
                        if tight.x1-tight.x0>=8 and tight.y1-tight.y0>=6:
                            d.match_score_details['ink_bbox_before_ownership']=e.bbox.model_dump()
                            e.bbox=tight
            if len(owners)==1:
                owners[0].match_score_details.setdefault('owned_ink_changes',[]).append(d.model_dump(mode='json'))
            else:kept.append(d)
        return kept
    finally:
        for v in verifiers.values():v.close()
