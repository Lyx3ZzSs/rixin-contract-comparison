"""Recover a slash only from a bounded contact slot's own raster evidence."""
import logging
from pathlib import Path
import re

import cv2
import fitz
import numpy as np

from app.models import BBox, CharBox, TextSourceEvidence
from app.services.party_field_scope import compact, contact_blocks

logger = logging.getLogger(__name__)
SOURCE = 'own_contact_placeholder_pixels'


def slash_component(mask, scale, line_height):
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    significant = [s for s in stats[1:] if s[4] >= scale*2]
    if len(significant) != 1:
        return None
    x,y,w,h,area = map(int,significant[0])
    if not (.45*line_height*scale <= h <= 1.3*line_height*scale and .25 <= w/h <= .85):
        return None
    ink = mask[y:y+h,x:x+w]
    xs = []; ys = []
    # Permit a short underline joined at the foot, not a top bar (7) or a
    # vertical stroke (L/1). The diagonal must occupy almost the entire height.
    for row in range(max(1,round(h*.03)),round(h*.8)):
        columns = np.flatnonzero(ink[row])
        if not len(columns) or columns[-1]-columns[0]+1 > max(2*scale,h*.18):
            return None
        ys.append(row);xs.append(float(np.median(columns)))
    if len(xs) < 5:
        return None
    slope,intercept = np.polyfit(ys,xs,1)
    residual = np.mean((np.array(xs)-(np.array(ys)*slope+intercept))**2)
    if not (-.8 <= slope <= -.3 and residual <= (.3*scale)**2):
        return None
    return x,y,w,h


def verify(document, page, block):
    colon_index = max(block.text.rfind(':'),block.text.rfind('：'))
    colons = [c for c in block.char_boxes if c.text_index==colon_index and c.char in ':：']
    if len(colons)!=1 or not Path(document.path).is_file():
        return []
    h=block.bbox.y1-block.bbox.y0
    if h<=0:
        return []
    rect=fitz.Rect(colons[0].bbox.x1+1,block.bbox.y0-2,colons[0].bbox.x1+h*1.8,block.bbox.y1+2)
    # Another text field may not enter the proposed blank slot.
    if any(b is not block and b.block_role != 'revision_artifact'
           and (rect & fitz.Rect(b.bbox.x0,b.bbox.y0,b.bbox.x1,b.bbox.y1)).get_area()>1
           for b in page.blocks):
        return []
    readings=[]
    with fitz.open(document.path) as pdf:
        p=pdf[page.page_no-1]
        if abs(p.rect.width-page.width)>1 or abs(p.rect.height-page.height)>1:
            return []
        rect &= p.rect
        for scale in (3,5):
            pix=p.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=rect,colorspace=fitz.csGRAY,alpha=False)
            pixels=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width)
            box=slash_component(pixels<150,scale,h)
            if box is None:
                return []
            x,y,w,height=box
            readings.append(BBox(x0=(pix.x+x)/scale,y0=(pix.y+y)/scale,
                                 x1=(pix.x+x+w)/scale,y1=(pix.y+y+height)/scale))
    if any(abs(getattr(readings[0],k)-getattr(readings[1],k))>1 for k in ('x0','y0','x1','y1')):
        return []
    return readings


def reconcile(original,compare):
    changes=[]
    for side,own,other in (('original',original,compare),('compare',compare,original)):
        peer_pages={p.page_no:p for p in other.pages}
        for page in own.pages:
            peer=peer_pages.get(page.page_no)
            if peer is None:
                continue
            refs=contact_blocks(peer)
            for key,block in contact_blocks(page).items():
                reference=refs.get(key)
                if (block.source!='PP-OCRv6' or reference is None
                        or not re.fullmatch(r'(传真号码|邮箱地址)[:：]/',compact(reference.text))
                        or not re.fullmatch(r'(传真号码|邮箱地址)[:：]L?',compact(block.text))):
                    continue
                try:
                    readings=verify(own,page,block)
                except Exception as exc:
                    logger.warning('Contact placeholder verification unavailable: %s',type(exc).__name__)
                    continue
                if len(readings)!=2:
                    continue
                old=block.text;start=max(old.rfind(':'),old.rfind('：'))+1
                block.source_evidence.append(TextSourceEvidence(source='contact_placeholder_before',raw_text=old,
                    page_no=page.page_no,bbox=block.bbox,confidence=block.confidence))
                for scale,box in zip((3,5),readings):
                    block.source_evidence.append(TextSourceEvidence(source=f'{SOURCE}_{scale}x',raw_text='/',page_no=page.page_no,bbox=box))
                block.text=old[:start]+'/'
                block.char_boxes=[c for c in block.char_boxes if c.text_index is not None and c.text_index<start]
                block.char_boxes.append(CharBox(char='/',page_no=page.page_no,text_index=start,bbox=readings[-1]))
                block.semantic_reasons.append(SOURCE)
                changes.append((side,block))
    return changes
