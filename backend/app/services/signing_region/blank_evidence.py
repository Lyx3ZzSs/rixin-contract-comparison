"""A missing OCR value is blank only when its localized image is clear."""
from pathlib import Path

from app.models import BBox, Document
from app.services.field_observation import ObservationState


def _printed_label_boxes(page):
    import re
    labels = re.compile(r'(?:(?:法定代表人|负责人|授权代表|代表签字|签字|签名|日期|时间|甲方|乙方|盖章)|[（）():：/或\s])+')
    chars = {(c.char,c.bbox.x0,c.bbox.y0,c.bbox.x1,c.bbox.y1):c
             for b in page.blocks for c in b.char_boxes if c.char.strip()
             and 0<c.bbox.y1-c.bbox.y0<40}
    lines=[]
    for c in sorted(chars.values(),key=lambda c:((c.bbox.y0+c.bbox.y1)/2,c.bbox.x0)):
        cy=(c.bbox.y0+c.bbox.y1)/2
        if not lines or abs(cy-lines[-1][0])>4:
            lines.append((cy,[]))
        lines[-1][1].append(c)
    return [c.bbox for _,line in lines
            if labels.fullmatch(''.join(c.char for c in sorted(line,key=lambda c:c.bbox.x0))) for c in line]


def verify_blank_field_rois(document: Document, regions) -> list[dict]:
    import fitz
    import cv2
    import numpy as np

    candidates = [(region,e) for region in regions for e in region.elements
                  if e.raw_ref.get('field_key') and not e.text.strip() and e.raw_ref.get('value_roi')]
    if not candidates:
        return []
    diagnostics = []
    pdf = None
    try:
        if Path(document.path).is_file():
            pdf = fitz.open(document.path)
        pages = {p.page_no:p for p in document.pages}
        label_boxes = {number:_printed_label_boxes(page) for number,page in pages.items()}
        from app.services.signing_region.comparator import SigningRegionComparator
        for region,element in candidates:
            # Pixel absence does not repair a rejected field attribution,
            # an occluded ROI or a failed extraction. It can only validate
            # (or downgrade) an otherwise geometrically observable blank.
            prior = SigningRegionComparator._field_observation(element,region)
            if prior.state != ObservationState.OBSERVED_BLANK:
                diagnostics.append({'element_id':element.element_id,'method':'LOCAL_VALUE_ROI_INK',
                                    'state':prior.state.value,'reason':prior.reason,'status':'PRIOR_UNCERTAINTY_PRESERVED'})
                continue
            state, reason = ObservationState.UNOBSERVED, 'BLANK_PIXELS_UNAVAILABLE'
            item = {'element_id':element.element_id,'method':'LOCAL_VALUE_ROI_INK'}
            roi = BBox.model_validate(element.raw_ref['value_roi'])
            model_page = pages.get(element.page_no)
            if pdf is not None and model_page and 1 <= element.page_no <= len(pdf):
                page = pdf[element.page_no-1]
                sx,sy = page.rect.width/model_page.width,page.rect.height/model_page.height
                requested = fitz.Rect(roi.x0*sx,roi.y0*sy,roi.x1*sx,roi.y1*sy)
                clip = requested & page.rect
                if not clip.is_empty and clip.get_area() >= requested.get_area()*.98 and clip.width >= 8 and clip.height >= 6:
                    pix = page.get_pixmap(matrix=fitz.Matrix(2,2),clip=clip,colorspace=fitz.csGRAY,alpha=False)
                    gray = np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width)
                    # Rule lines are not filled content. Remove only long,
                    # thin straight lines; handwriting and red ink stay.
                    ink = (gray < 210).astype('uint8')
                    rule_ink = ink.copy()
                    masked = 0
                    for b in label_boxes.get(element.page_no,[]):
                        # The broad ROI may include the preceding printed
                        # label row or the current label's final punctuation.
                        # Never mask arbitrary OCR values in the writing area.
                        if not (b.y1 <= element.bbox.y0+2 or b.x1 <= roi.x0+8):
                            continue
                        x0=max(0,int(((b.x0-2)*sx-clip.x0)*2))
                        y0=max(0,int(((b.y0-2)*sy-clip.y0)*2))
                        x1=min(pix.width,int(((b.x1+2)*sx-clip.x0)*2)+1)
                        y1=min(pix.height,int(((b.y1+2)*sy-clip.y0)*2)+1)
                        if x1>x0 and y1>y0:
                            ink[y0:y1,x0:x1]=0
                            masked+=1
                    item['masked_template_characters']=masked
                    rules = cv2.morphologyEx(rule_ink,cv2.MORPH_OPEN,np.ones((1,max(30,int(pix.width*.8))),np.uint8))
                    vertical = cv2.morphologyEx(rule_ink,cv2.MORPH_OPEN,np.ones((max(12,int(pix.height*.8)),1),np.uint8))
                    # Accept vertical borders only at the ROI edge, so a
                    # written digit "1" inside the field cannot disappear.
                    allowed=np.zeros(pix.width,dtype=bool)
                    allowed[:6]=True
                    allowed[-6:]=True
                    for block in model_page.blocks:
                        if block.block_type!='table' or block.bbox.y1<roi.y0 or block.bbox.y0>roi.y1:
                            continue
                        boundaries=[block.bbox.x0,block.bbox.x1,
                                    *(x for cell in block.table_cell_bboxes for x in (cell[0],cell[2]))]
                        for x in boundaries:
                            center=int((x*sx-clip.x0)*2)
                            start,end=max(0,center-6),min(pix.width,center+7)
                            if start<end:
                                allowed[start:end]=True
                    vertical[:,~allowed]=0
                    rules=cv2.bitwise_or(rules,vertical)
                    remaining = ink & (1-rules)
                    count,_,stats,_ = cv2.connectedComponentsWithStats(remaining,8)
                    components = [int(s[cv2.CC_STAT_AREA]) for s in stats[1:]
                                  if s[cv2.CC_STAT_WIDTH]>=2 and s[cv2.CC_STAT_HEIGHT]>=2]
                    item.update(ink_pixels=int(remaining.sum()),largest_component=max(components,default=0))
                    if max(components,default=0) <= 3 and remaining.sum() <= max(8,remaining.size*.001):
                        state,reason = ObservationState.OBSERVED_BLANK,'VISUALLY_VERIFIED_BLANK_VALUE_ROI'
                    else:
                        reason = 'UNRECOGNIZED_INK_IN_VALUE_ROI'
            element.observation_state = state
            element.observation_reason = reason
            element.raw_ref.update(observation_state=state.value,observation_reason=reason,blank_visual_evidence=item)
            diagnostics.append({**item,'state':state.value,'reason':reason})
    finally:
        if pdf is not None:
            pdf.close()
    return diagnostics


def reject_stamp_text_as_person(document,regions):
    """A localized stamp inscription is not a recognized representative name."""
    import re
    from app.services.red_seal_visual import RedSealVisualInspector
    inspector=RedSealVisualInspector()
    diagnostics=[]
    for region in regions:
        for e in region.elements:
            if e.raw_ref.get('field_key') not in {'legal_representative','authorized_representative','signature','contact'}:
                continue
            # "用章" can also be part of a real person's name. Require an
            # explicit inscription term or a separately read stamp fragment.
            inscription = '专用章' in re.sub(r'\s+','',e.text) or re.search(r'(?:^|\s)用章(?:\s|$)',e.text)
            red_only = (e.raw_ref.get('field_key') == 'contact' and 0 < len(e.text.strip()) <= 4
                        and _is_red_only_value(document, e))
            if not (inscription or red_only):
                continue
            metrics=inspector.inspect(document,e.page_no,e.bbox)
            if metrics.red_pixels<30:
                continue
            item={'element_id':e.element_id,'rejected_text':e.text,'reason':'STAMP_INSCRIPTION_IS_NOT_PERSON_NAME',
                  'red_pixels':metrics.red_pixels}
            diagnostics.append(item)
            e.raw_ref['rejected_stamp_value']={**item,'value_evidence':e.raw_ref.get('value_evidence',[])}
            e.raw_ref['value_evidence']=[]
            e.raw_ref['content_recognition_status']='NEEDS_REVIEW'
            e.raw_ref['content_recognition_reason']=item['reason']
            e.text=''
            e.observation_state=ObservationState.UNOBSERVED
            e.observation_reason=item['reason']
            e.raw_ref.update(observation_state=e.observation_state.value,observation_reason=e.observation_reason)
    return diagnostics


def _is_red_only_value(document, element):
    """Test the value's own ink; a nearby stamp alone cannot reject a name."""
    import fitz
    import numpy as np
    if not Path(document.path).is_file():
        return False
    model_page = next((p for p in document.pages if p.page_no == element.page_no), None)
    if model_page is None:
        return False
    with fitz.open(document.path) as pdf:
        page = pdf[element.page_no - 1]
        sx, sy = page.rect.width / model_page.width, page.rect.height / model_page.height
        b = BBox.model_validate(element.raw_ref['value_bbox']) if element.raw_ref.get('value_bbox') else element.bbox
        clip = fitz.Rect(b.x0*sx, b.y0*sy, b.x1*sx, b.y1*sy) & page.rect
        if clip.is_empty:
            return False
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=clip, colorspace=fitz.csRGB, alpha=False)
        rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).astype(float)
        red = (rgb[:,:,0] > rgb[:,:,1]*1.35) & (rgb[:,:,0] > rgb[:,:,2]*1.35) & (rgb[:,:,1] < 200)
        black = (rgb.max(axis=2) < 160) & ~red
        return red.sum() >= 30 and black.sum() <= max(5, red.sum()*.08)


def remove_owned_stamp_table_diffs(diffs,original_regions,compare_regions):
    """Keep a rejected stamp inscription from resurfacing as a table name edit."""
    import re
    import unicodedata
    def normalize(text):
        return re.sub(r'[\s:：]','',unicodedata.normalize('NFKC',text))
    def candidates(regions):
        return [e for r in regions for e in r.elements
                if e.raw_ref.get('rejected_stamp_value',{}).get('reason')=='STAMP_INSCRIPTION_IS_NOT_PERSON_NAME']
    left,right=candidates(original_regions),candidates(compare_regions)
    kept,decisions=[],[]
    def owned(evidence,element):
        return all(e.page_no==element.page_no and e.bbox.x0>=element.bbox.x0-2
                   and e.bbox.x1<=element.bbox.x1+2 and e.bbox.y0>=element.bbox.y0-2
                   and e.bbox.y1<=element.bbox.y1+2 for e in evidence)
    def field_text(e):
        return normalize(str(e.raw_ref.get('field_label',''))+e.raw_ref['rejected_stamp_value']['rejected_text'])
    for diff in diffs:
        pair=None
        if diff.source_type=='table' and diff.diff_type=='MODIFY' and (diff.original_evidence or diff.compare_evidence):
            pair=next(((a,b) for a in left for b in right
                       if a.raw_ref.get('party_role')==b.raw_ref.get('party_role')
                       and a.raw_ref.get('field_key')==b.raw_ref.get('field_key')
                       and normalize(diff.original_text)==field_text(a) and normalize(diff.compare_text)==field_text(b)
                       and owned(diff.original_evidence,a) and owned(diff.compare_evidence,b)),None)
        if pair is None:
            kept.append(diff)
        else:
            decisions.append({'diff_id':diff.diff_id,'reason':'STAMP_TEXT_OWNED_BY_SIGNING_FIELD',
                              'original_element_id':pair[0].element_id,'compare_element_id':pair[1].element_id,
                              'original_text':diff.original_text,'compare_text':diff.compare_text})
    return kept,decisions
