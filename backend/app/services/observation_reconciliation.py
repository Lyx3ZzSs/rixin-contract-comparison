"""Adjudicate existing footer observations after final business-object ownership.

This does not detect new ink or promote uncertain handwriting to a change.
Every consumed observation keeps its input evidence and disposition in the audit.
"""
import cv2
import numpy as np

from app.models import BBox
from app.services.visual_impressions import ImpressionVerifier


def _area(b):
    return max(0, b.x1-b.x0)*max(0, b.y1-b.y0)


def _coverage(a, b):
    return (max(0, min(a.x1,b.x1)-max(a.x0,b.x0)) *
            max(0, min(a.y1,b.y1)-max(a.y0,b.y0)) / max(.01, _area(a)))


def _pixels(image, box, *, threshold=210):
    x0,y0=max(0,int(box.x0*1.5)),max(0,int(box.y0*1.5))
    x1,y1=min(image.shape[1],int(box.x1*1.5)+1),min(image.shape[0],int(box.y1*1.5)+1)
    crop=image[y0:y1,x0:x1]
    if not crop.size:
        return None
    gray=cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY)
    # Match the footer detector's neutral-ink policy; blue scan backgrounds
    # and red stamps do not constitute handwritten footer observations.
    neutral=cv2.cvtColor(crop,cv2.COLOR_RGB2HSV)[:,:,1] < 80
    ys,xs=np.nonzero((gray < threshold) & neutral)
    if not len(xs):
        return None
    return (xs+x0)/1.5,(ys+y0)/1.5,gray[ys,xs]


def _scan_corner(image, b):
    if b.y1 < image.shape[0]/1.5-15 or b.x1-b.x0 > 12:
        return False
    x0=max(0,int((b.x0-40)*1.5));x1=min(image.shape[1],int((b.x1+40)*1.5))
    y0=max(0,int((b.y0-10)*1.5))
    crop=image[y0:,x0:x1]
    mask=(cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY)<110).astype(np.uint8)
    _,labels,stats,_=cv2.connectedComponentsWithStats(mask)
    bx0,bx1=max(0,int(b.x0*1.5)-x0),min(crop.shape[1],int(b.x1*1.5)-x0+1)
    by0,by1=max(0,int(b.y0*1.5)-y0),min(crop.shape[0],int(b.y1*1.5)-y0+1)
    region=labels[by0:by1,bx0:bx1];count=np.count_nonzero(region)
    for i,(x,y,w,h,_) in enumerate(stats[1:],1):
        # A narrow corner crop must belong to a broad bottom-edge component.
        if (w>=12 and y+h>=crop.shape[0]-2
                and np.count_nonzero(labels[-2:]==i)>=16
                and np.count_nonzero(region==i)/max(1,count)>=.9):
            return True
    return False


def _context_mirror(verifier, evidence, tones, enclosing_boxes=()):
    if float(np.median(tones)) < 110:
        return None
    b=evidence.bbox
    contexts=[BBox(x0=max(0,box.x0-pad),y0=max(0,box.y0-4),x1=box.x1+pad,y1=box.y1+4)
              for box in (b,*enclosing_boxes) for pad in (12,24)]
    for context_box in contexts:
        expanded=evidence.model_copy(update={'bbox':context_box})
        image=verifier.render(evidence.page_no)
        cb=expanded.bbox
        x0,y0=max(0,int(cb.x0*1.5)),max(0,int(cb.y0*1.5))
        crop=image[y0:min(image.shape[0],int(cb.y1*1.5)+1),x0:min(image.shape[1],int(cb.x1*1.5)+1)]
        proof=verifier.mirrored_peer(evidence.page_no,cb,crop)
        if not proof or proof['mirror_score']<.5 or proof['mirror_score']-proof['direct_score']<.12:
            continue
        peer=proof['counterpart']
        px0,py0,px1,py1=[round(v*1.5) for v in peer['bbox']]
        peer_crop=verifier.render(peer['page'])[py0:py1,px0:px1]
        peer_gray=cv2.cvtColor(peer_crop,cv2.COLOR_RGB2GRAY)
        peer_ink=verifier.ink(peer_crop)>0
        if not np.any(peer_ink):continue
        peer_tone=float(np.median(peer_gray[peer_ink]))
        if float(np.median(tones))-peer_tone<40:continue
        stamp=verifier.ink(peer_crop,strong=True)
        sh,sw=stamp.shape
        stamp=cv2.warpAffine(stamp,cv2.getRotationMatrix2D((sw/2,sh/2),-peer['rotation_degrees'],1),(sw,sh))
        mask=verifier.ink(crop)>0
        ys,xs=np.nonzero(mask)
        if not len(xs):continue
        ty0,ty1,tx0,tx1=ys.min(),ys.max()+1,xs.min(),xs.max()+1
        explained=cv2.resize(cv2.flip(stamp,1),(tx1-tx0,ty1-ty0))
        explained=cv2.dilate(explained,np.ones((3,3),np.uint8))>0
        yy,xx=np.indices(explained.shape)
        # Verify only the original candidate's strokes. Context may contain a
        # neighbouring genuine signature; its pixels are never consumed here.
        candidate=((xx+tx0+x0>=b.x0*1.5)&(xx+tx0+x0<=b.x1*1.5)
                   &(yy+ty0+y0>=b.y0*1.5)&(yy+ty0+y0<=b.y1*1.5))
        target=mask[ty0:ty1,tx0:tx1]&candidate
        strong=(verifier.ink(crop,strong=True)[ty0:ty1,tx0:tx1]>0)&candidate
        coverage=float(np.count_nonzero(target&explained)/max(1,np.count_nonzero(target)))
        strong_coverage=float(np.count_nonzero(strong&explained)/max(1,np.count_nonzero(strong))) if np.any(strong) else 1.
        if coverage>=.7 and strong_coverage>=.9:
            return dict(proof,context_bbox=expanded.bbox.model_dump(),explained_ink_ratio=round(coverage,3),
                        explained_strong_ink_ratio=round(strong_coverage,3),source_ink_tone=float(np.median(tones)),counterpart_ink_tone=peer_tone)
    return None


def reconcile_observations(task, original_path, compare_path):
    verifiers={side:ImpressionVerifier(path) for side,path in [('original',original_path),('compare',compare_path)]}
    kept=[];decisions=[]
    try:
        for raw in task.comparison_observations:
            o=raw.model_copy(deep=True)
            if (o.source_type!='header_footer' or o.review_status!='UNREVIEWED'
                    or len(o.original_evidence)+len(o.compare_evidence)!=1):
                kept.append(o);continue
            side='compare' if o.compare_evidence else 'original'
            evidence=getattr(o,side+'_evidence')[0];b=evidence.bbox
            verifier=verifiers[side];image=verifier.render(evidence.page_no)
            if image is None or b.y0<image.shape[0]/1.5*.85:
                kept.append(o);continue
            pixels=_pixels(image,b)
            reason=None;owner=None;proof=None;shared_owners=[]
            if _scan_corner(image,b):
                reason='SCAN_BOTTOM_EDGE_COMPONENT'
            elif pixels is not None:
                xs,ys,tones=pixels
                tight=BBox(x0=float(xs.min()),y0=float(ys.min()),x1=float(xs.max()+1/1.5),y1=float(ys.max()+1/1.5))
                owners=[];claimed=np.zeros(len(xs),dtype=bool);touching=[]
                for d in task.diffs:
                    if d.source_type!='header_footer' or d.diff_type!=('ADD' if side=='compare' else 'DELETE'):
                        continue
                    for e in getattr(d,side+'_evidence'):
                        if e.page_no!=evidence.page_no:continue
                        eb=e.bbox
                        covered=(xs>=eb.x0-2)&(xs<=eb.x1+2)&(ys>=eb.y0-2)&(ys<=eb.y1+2)
                        coverage=np.mean(covered)
                        if np.any(covered):
                            claimed|=covered
                            if all(item is not d for item in touching):touching.append(d)
                        same_partial_object=(d.content_recognition_status=='NEEDS_REVIEW'
                            and o.reason=='VISUAL_FOOTER_CONTENT_UNVERIFIED'
                            and _coverage(tight,eb)>=.65 and _coverage(eb,tight)>=.65
                            and max(tight.x1-tight.x0,eb.x1-eb.x0)/max(.1,min(tight.x1-tight.x0,eb.x1-eb.x0))<=1.5)
                        if coverage>=.9 or same_partial_object:
                            owners.append(d);break
                if len(owners)==1:
                    owner=owners[0];reason='OWNED_BY_FINAL_FOOTER_OBJECT'
                elif len(touching)>1 and np.mean(claimed)>=.9:
                    shared_owners=touching;reason='OWNED_BY_FINAL_FOOTER_OBJECTS'
                else:
                    w,h=tight.x1-tight.x0,tight.y1-tight.y0
                    if min(w,h)<=3 and max(w,h)<=12 and _area(tight)<=40:
                        reason='ISOLATED_SUB_OBJECT_SPECK'
                    elif (w<=4 and h/max(.1,w)>=8
                          and (tight.x0<image.shape[1]/1.5*.05 or tight.x1>image.shape[1]/1.5*.95)):
                        reason='NARROW_SCAN_EDGE_FRAGMENT'
                    else:
                        enclosing=[]
                        for other in task.comparison_observations:
                            boxes=getattr(other,side+'_evidence')
                            if (other is not raw and other.source_type=='header_footer'
                                    and other.review_status=='UNREVIEWED' and len(boxes)==1
                                    and boxes[0].page_no==evidence.page_no
                                    and _area(boxes[0].bbox)>_area(b) and _coverage(b,boxes[0].bbox)>=.95):
                                enclosing.append(boxes[0].bbox)
                        proof=_context_mirror(verifier,evidence,tones,enclosing)
                        if proof:reason='MIRRORED_ADJACENT_PAGE_IMPRESSION'
                # Keep original bounds in the audit; tighten displayed support
                # only when removing blank padding, not when no ink was found.
                evidence.bbox=tight
            if reason:
                record=dict(observation=raw.model_dump(mode='json'),reason=reason,
                            owner_diff_id=owner.diff_id if owner else None,proof=proof)
                if shared_owners:
                    record['owner_diff_ids']=[d.diff_id for d in shared_owners]
                    for d in shared_owners:
                        d.match_score_details.setdefault('observation_evidence',[]).append(raw.model_dump(mode='json'))
                decisions.append(record)
                if owner:
                    owner.match_score_details.setdefault('observation_evidence',[]).append(raw.model_dump(mode='json'))
                continue
            kept.append(o)
        # Containment is resolved after noise/mirror adjudication so a discarded
        # large candidate cannot accidentally consume a valid smaller object.
        removed=set()
        for i,o in sorted(enumerate(kept),key=lambda p:-sum(_area(e.bbox) for e in p[1].original_evidence+p[1].compare_evidence)):
            if i in removed or o.source_type!='header_footer' or o.review_status!='UNREVIEWED':continue
            for j,other in enumerate(kept):
                if i==j or j in removed or other.source_type!=o.source_type or other.review_status!='UNREVIEWED':continue
                side='compare' if o.compare_evidence else 'original'
                a=getattr(o,side+'_evidence');b=getattr(other,side+'_evidence')
                if len(a)!=1 or len(b)!=1 or other.original_evidence and other.compare_evidence:continue
                if a[0].page_no==b[0].page_no and _coverage(b[0].bbox,a[0].bbox)>=.95:
                    removed.add(j)
                    o.source_diff_ids=list(dict.fromkeys(o.source_diff_ids+other.source_diff_ids))
                    decisions.append(dict(observation=other.model_dump(mode='json'),reason='OWNED_BY_OBSERVATION',owner_observation_id=o.observation_id))
        task.comparison_observations=[o for i,o in enumerate(kept) if i not in removed]
        task.metrics['observation_reconciliation']=decisions
    finally:
        for verifier in verifiers.values():verifier.close()
