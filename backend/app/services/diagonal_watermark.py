"""Own repeated pale diagonal overlays separately from contractual prose."""
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
import re

from app.models import DiffItem, EvidenceBox
from app.utils.id_utils import generate_diff_id


def compact(text):
    return re.sub(r'\s+','',text or '')


def _same_family(a,b):
    if min(len(a),len(b))<2:
        return False
    if a in b or b in a:
        return True
    # Digits are contractual identifiers, never fuzzy-normalize them.
    return not re.search(r'\d',a+b) and min(len(a),len(b))>=6 and SequenceMatcher(None,a,b).ratio()>=.65


def classify_diagonal_watermarks(document):
    import fitz
    import cv2
    import numpy as np
    if not Path(document.path).is_file():
        return []
    eligible=[]
    seeds=[]
    with fitz.open(document.path) as pdf:
        for page in document.pages:
            if not 1<=page.page_no<=len(pdf):
                continue
            native=pdf[page.page_no-1]
            sx,sy=native.rect.width/page.width,native.rect.height/page.height
            for block in page.blocks:
                b=block.bbox
                text=compact(block.text)
                w,h=b.x1-b.x0,b.y1-b.y0
                if (block.block_type not in {'text','ocr_line'} or not 2<=len(text)<=40
                        or '\n' in block.text or h<35 or w<=0 or not .3<h/w<1.5):
                    continue
                eligible.append(block)
                clip=fitz.Rect(b.x0*sx,b.y0*sy,b.x1*sx,b.y1*sy)&native.rect
                if clip.is_empty:
                    continue
                pix=native.get_pixmap(matrix=fitz.Matrix(2,2),clip=clip,colorspace=fitz.csGRAY,alpha=False)
                gray=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width)
                pale=(gray>175)&(gray<245)
                y,x=np.nonzero(pale)
                if len(x)<50 or (gray<150).sum()/len(x)>.4:
                    continue
                values,vectors=np.linalg.eigh(np.cov(np.array([x,y])))
                angle=abs(float(np.degrees(np.arctan2(vectors[1,1],vectors[0,1]))))%180
                if values[0]<=0 or values[1]/values[0]<3 or not (25<angle<65 or 115<angle<155):
                    continue
                seeds.append(block)
    counts=Counter(compact(b.text) for b in seeds)
    families=[]
    for text in sorted(counts,key=lambda t:(-counts[t],-len(t),t)):
        if any(_same_family(text,f) for f in families):
            continue
        occurrences=[b for b in seeds if _same_family(text,compact(b.text))]
        if len(occurrences)>=2:
            families.append(max((compact(b.text) for b in occurrences),key=lambda t:(counts[t],len(t))))
    decisions=[]
    for block in eligible:
        family=next((f for f in families if _same_family(f,compact(block.text))),None)
        if family is None:
            continue
        # Preserve source text and coordinates. The metadata channel owns this
        # overlay; clause and cover-extra channels must not duplicate it.
        block.block_type='watermark'
        block.block_role='watermark'
        block.enter_clause_compare=False
        block.flow_role='non_text'
        block.semantic_reasons=[*block.semantic_reasons,'PALE_DIAGONAL_REPEATED_OVERLAY']
        decisions.append({'block_id':block.block_id,'page_no':block.page_no,'text':block.text,'family':family})
    return decisions


def build_watermark_diffs(original,compare,start_index=1):
    def groups(document):
        blocks=[b for p in document.pages for b in p.blocks if b.block_type=='watermark'
                and 'PALE_DIAGONAL_REPEATED_OVERLAY' in b.semantic_reasons]
        counts=Counter(compact(b.text) for b in blocks)
        result={}
        for text in sorted(counts,key=lambda t:(-counts[t],-len(t),t)):
            if any(_same_family(text,key) for key in result):
                continue
            result[text]=[b for b in blocks if _same_family(text,compact(b.text))]
        return result
    left,right=groups(original),groups(compare)
    # Across documents require exact canonical text. OCR family grouping does
    # not authorize calling a changed watermark equivalent.
    removed={k:v for k,v in left.items() if k not in right}
    added={k:v for k,v in right.items() if k not in left}
    if not removed and not added:
        return []
    def evidence(group):
        return [EvidenceBox(page_no=b.page_no,bbox=b.bbox,method='diagonal_watermark_region',text=b.text)
                for blocks in group.values() for b in blocks]
    old,new='\n'.join(sorted(removed)),'\n'.join(sorted(added))
    kind='MODIFY' if old and new else 'ADD' if new else 'DELETE'
    return [DiffItem(diff_id=generate_diff_id(start_index),diff_type=kind,source_type='metadata',title='水印',
                     original_text=old,compare_text=new,original_evidence=evidence(removed),compare_evidence=evidence(added),
                     readable_change=f'水印变化：{old or "无"} → {new or "无"}',match_method='diagonal_overlay_ownership',
                     quality_status='NEEDS_REVIEW',content_recognition_status='NEEDS_REVIEW',
                     match_score_details={'field_key':'watermark','change_decision':'NEEDS_REVIEW',
                                          'reason':'PALE_DIAGONAL_REPEATED_OVERLAY','original_families':list(left),'compare_families':list(right)})]
