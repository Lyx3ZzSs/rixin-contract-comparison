"""A faint image-only layout label does not establish a business seal object."""
import html
from pathlib import Path
import re


def reclassify_unverified_seal_images(document):
    import fitz
    import numpy as np

    candidates=[(page,b) for page in document.pages for b in page.blocks
                if b.block_type in {'seal','stamp'} and '<img' in (b.text+' '+b.raw_html).lower()
                and not html.unescape(re.sub(r'<[^>]+>','',b.text)).strip()]
    if not candidates or not Path(document.path).is_file():
        return []
    decisions=[]
    with fitz.open(document.path) as pdf:
        for page,block in candidates:
            if not 1<=page.page_no<=len(pdf):
                continue
            native=pdf[page.page_no-1]
            b=block.bbox
            sx,sy=native.rect.width/page.width,native.rect.height/page.height
            clip=fitz.Rect(b.x0*sx,b.y0*sy,b.x1*sx,b.y1*sy)&native.rect
            if clip.is_empty:
                continue
            pix=native.get_pixmap(matrix=fitz.Matrix(2,2),clip=clip,colorspace=fitz.csRGB,alpha=False)
            rgb=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3).astype('int16')
            dark=int((rgb.mean(axis=2)<210).sum())
            colored=int(((rgb.max(axis=2)-rgb.min(axis=2))>12).sum())
            if pix.width*pix.height<100 or dark>3 or colored>3:
                continue
            # Keep the source image and provenance. Only the unsupported seal
            # class is removed; no claim of QR identity or graphic equality.
            block.block_type='image'
            block.block_role='unclassified_graphic'
            block.flow_role='non_text'
            block.enter_clause_compare=False
            block.semantic_reasons=[*block.semantic_reasons,'UNVERIFIED_IMAGE_ONLY_SEAL_LABEL']
            decisions.append({'page_no':page.page_no,'block_id':block.block_id,'dark_pixels':dark,
                              'colored_pixels':colored,'reason':'UNVERIFIED_IMAGE_ONLY_SEAL_LABEL'})
    return decisions
