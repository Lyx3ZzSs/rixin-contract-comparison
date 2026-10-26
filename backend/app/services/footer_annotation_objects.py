"""Page-local annotation ownership, independent of speculative OCR characters.

Only registered novel ink in a recurring lower-left annotation column is
eligible. Contrast, geometry and impression verification corroborate the object;
none of them establishes what the handwriting says. Preserve readings as
candidates and explicitly scope confirmation to annotation presence.
"""
from collections import defaultdict

import cv2
import numpy as np

from app.models import BBox, DiffItem, EvidenceBox
from app.services.page_number_evidence import page_counters
from app.services.visual_impressions import ImpressionVerifier


OBJECT = 'FOOTER_HANDWRITTEN_ANNOTATION'


def _overlap(a, b):
    intersection = max(0, min(a.x1,b.x1)-max(a.x0,b.x0))*max(0,min(a.y1,b.y1)-max(a.y0,b.y0))
    return intersection/max(1,min((a.x1-a.x0)*(a.y1-a.y0),(b.x1-b.x0)*(b.y1-b.y0)))


def _eligible(e, page):
    b = e.bbox
    return (page is not None and page.width > 0 and page.height > 0
            and b.x1 <= page.width*.34 and b.y0 >= page.height*.90
            and 4 <= b.x1-b.x0 <= page.width*.20 and 12 <= b.y1-b.y0 <= page.height*.08
            and not any(_overlap(b,c.bbox) >= .3 for c in page_counters(page)))


def _strong_object(e, verifier):
    result = verifier.inspect(e)
    if result is not None:
        if result[0] is None:
            return None
        e = e.model_copy(update={'bbox':result[0], 'char_boxes':[]})
    image = verifier.render(e.page_no)
    if image is None:
        return None
    b=e.bbox
    crop=image[max(0,int(b.y0*1.5)):int(b.y1*1.5)+1,max(0,int(b.x0*1.5)):int(b.x1*1.5)+1]
    if not crop.size:
        return None
    gray=cv2.cvtColor(crop,cv2.COLOR_RGB2GRAY)
    neutral=cv2.cvtColor(crop,cv2.COLOR_RGB2HSV)[:,:,1] < 80
    strong=(gray<110)&neutral
    ink=(gray<210)&neutral
    if np.count_nonzero(strong)<15 or np.count_nonzero(strong)/max(1,np.count_nonzero(ink))<.25:
        return None
    # Saturated skin / solid scanner shadows are not pen strokes.
    if np.count_nonzero(ink)/gray.size > .55:
        return None
    return e


def reconcile_footer_objects(diffs, original, compare):
    docs={'original':original,'compare':compare}
    pages={side:{p.page_no:p for p in doc.pages} for side,doc in docs.items()}
    verifiers={side:ImpressionVerifier(doc.path) for side,doc in docs.items()}
    candidates=defaultdict(list)
    try:
        for d in diffs:
            if d.source_type!='header_footer' or d.diff_type not in {'ADD','DELETE'}:
                continue
            if d.match_score_details.get('object_type')==OBJECT:
                continue
            side='compare' if d.diff_type=='ADD' else 'original'
            proofs=[e for e in getattr(d,side+'_evidence') if e.method=='footer_visual_registered']
            # Earlier evidence fusion may have retained registered ink as an
            # auxiliary source of an OCR event. Recover that proof, not its text.
            proofs.extend(EvidenceBox.model_validate(p['evidence'])
                          for p in d.match_score_details.get('footer_visual_evidence',[])
                          if p.get('side')==side and p.get('diff_type')==d.diff_type)
            for e in proofs:
                page=pages[side].get(e.page_no)
                if not _eligible(e,page):
                    continue
                e=_strong_object(e,verifiers[side])
                if e is not None and _eligible(e,page):
                    candidates[side,e.page_no].append((d,e))
        # A one-off unknown footer image remains unclassified. Recurrence here
        # is spatial (page-local marks), never agreement of guessed characters.
        supported={side for side in docs if len({n for s,n in candidates if s==side})>=3}
        objects=[]
        consumed=defaultdict(set)
        for (side,number),items in sorted(candidates.items()):
            if side not in supported:
                continue
            clusters=[]
            for item in items:
                related=[g for g in clusters if any(_overlap(item[1].bbox,e.bbox)>=.6 for _,e in g)]
                if not related:
                    clusters.append([item])
                else:
                    merged=[item]+[pair for g in related for pair in g]
                    clusters=[g for g in clusters if g not in related]+[merged]
            for group in clusters:
                b=BBox(x0=min(e.bbox.x0 for _,e in group),y0=min(e.bbox.y0 for _,e in group),
                       x1=max(e.bbox.x1 for _,e in group),y1=max(e.bbox.y1 for _,e in group))
                readings=[]; sources=[]; extents=[b]
                for d in diffs:
                    if d.source_type!='header_footer' or d.diff_type!=group[0][0].diff_type:
                        continue
                    for index,e in enumerate(getattr(d,side+'_evidence')):
                        if e.page_no!=number or _overlap(e.bbox,b)<.6:
                            continue
                        if e.method not in {'header_footer','footer_visual_registered'}:
                            continue
                        if (e.method=='header_footer' and len(''.join(e.text.split()))>12
                                and d.content_recognition_reason!='FOOTER_CROP_READING_UNVERIFIED'):
                            continue
                        if e.method == 'header_footer' and _eligible(e, pages[side].get(number)):
                            localized = _strong_object(e, verifiers[side])
                            if localized is not None:
                                extents.append(localized.bbox)
                        consumed[id(d)].add(index)
                        sources.append({'diff_id':d.diff_id,'evidence':e.model_dump(mode='json')})
                        if e.method=='header_footer':
                            readings.extend(c for c in d.content_candidates if c.get('source')=='footer_ocr_before_crop')
                            readings.append({'text':e.text,'source':'footer_ocr','side':side,
                                             'confidence':e.text_confidence if e.text_confidence is not None else d.text_confidence})
                # Registered detection can stop at the scan margin. A verified
                # overlapping OCR crop supplies the rest of the same pen stroke,
                # without supplying the OCR reading as business content.
                b=BBox(x0=min(x.x0 for x in extents),y0=min(x.y0 for x in extents),
                       x1=max(x.x1 for x in extents),y1=max(x.y1 for x in extents))
                text='检测到左下角手写签注'
                evidence=group[0][1].model_copy(update={'bbox':b,'text':text,'char_boxes':[],
                                                    'method':'footer_annotation_object','text_confidence':None})
                direction=group[0][0].diff_type
                objects.append(DiffItem(diff_id=f'footer-{side}-{number:04}-{len(objects)+1:04}',diff_type=direction,
                    source_type='header_footer',title=f'第{number}页左下角手写签注',
                    **{side+'_text':text,side+'_snippet':text,side+'_evidence':[evidence]},
                    readable_change=f"第{number}页左下角{'新增' if direction=='ADD' else '删除'}手写签注；具体字迹未确认，请核对原图。",
                    quality_status='NEEDS_REVIEW',content_recognition_status='CONFIRMED',
                    content_recognition_reason='ANNOTATION_PRESENCE_CONFIRMED_TEXT_UNREAD',
                    review_flags=['HANDWRITTEN_ANNOTATION_REVIEW'],content_candidates=readings,
                    match_score_details={'object_type':OBJECT,'recognition_scope':'annotation_presence',
                                         'handwriting_text_status':'NEEDS_REVIEW','source_evidence':sources,
                                         'registered_evidence':[e.model_dump(mode='json') for _,e in group]}))
        result=[]
        for d in diffs:
            if id(d) not in consumed:
                result.append(d)
                continue
            side='compare' if d.diff_type=='ADD' else 'original'
            remaining=[e for index,e in enumerate(getattr(d,side+'_evidence')) if index not in consumed[id(d)]]
            if remaining:
                result.append(d.model_copy(deep=True,update={side+'_evidence':remaining}))
        return result+objects
    finally:
        for verifier in verifiers.values():
            verifier.close()
