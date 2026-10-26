"""Recognize a lone straight slash from its own pixels, not the peer's text."""
from pathlib import Path

from app.models import BBox, TextSourceEvidence


def repair_table_slashes(document):
    import cv2
    import fitz
    import numpy as np

    if not Path(document.path).is_file():
        return []
    repaired = []
    with fitz.open(document.path) as pdf:
        for page in document.pages:
            visible_slashes = []
            if 1 <= page.page_no <= len(pdf):
                for span in pdf[page.page_no-1].get_texttrace():
                    if span.get("type") != 0 or span.get("opacity", 1) < 0.9:
                        continue
                    visible_slashes.extend(fitz.Rect(char[3]) for char in span["chars"] if char[0] == ord("/"))
            for block in page.blocks:
                if not block.raw_html or not 1 <= page.page_no <= len(pdf):
                    continue
                for char in block.char_boxes:
                    index = char.text_index
                    if char.char not in {"1", "7"} or index is None or index >= len(block.text) or block.text[index] != char.char:
                        continue
                    start, end = block.text.rfind("\n", 0, index) + 1, block.text.find("\n", index)
                    if block.text[start:end if end >= 0 else None].strip() != char.char:
                        continue
                    b = char.bbox
                    if b.x1-b.x0 > 20 or b.y1-b.y0 > 25:
                        continue
                    own = fitz.Rect(b.x0-2, b.y0-2, b.x1+2, b.y1+2)
                    native_slash = next((rect for rect in visible_slashes
                        if (rect & own).get_area() >= rect.get_area() * 0.7
                        or (b.y1-b.y0 < 3 and rect.y0 <= (b.y0+b.y1)/2 <= rect.y1
                            and b.x0-1 <= (rect.x0+rect.x1)/2 <= b.x1+1
                            and abs((rect.x0+rect.x1-b.x0-b.x1)/2) < 6)), None)
                    if native_slash is not None:
                        original = char.char
                        char.char = "/"
                        char.bbox = BBox(x0=native_slash.x0,y0=native_slash.y0,x1=native_slash.x1,y1=native_slash.y1)
                        block.text = block.text[:index] + "/" + block.text[index+1:]
                        block.source_evidence.append(TextSourceEvidence(source="visible_native_table_slash", raw_text="/",
                            page_no=page.page_no, bbox=b, confidence=1.0))
                        repaired.append((block, original, b))
                        continue
                    # A recognizer calling a slash "1" often supplies a very
                    # narrow vertical box. Include the diagonal's side strokes.
                    clip = fitz.Rect(b.x0-6, b.y0-2, b.x1+6, b.y1+2) & pdf[page.page_no-1].rect
                    pix = pdf[page.page_no-1].get_pixmap(matrix=fitz.Matrix(4, 4), clip=clip, colorspace=fitz.csGRAY, annots=False)
                    mask = (np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width) < 170).astype("uint8")
                    _, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
                    components = [(i, s) for i, s in enumerate(stats[1:], 1)
                                  if s[4] >= 3 and s[0] > 0 and s[1] > 0 and s[0]+s[2] < pix.width and s[1]+s[3] < pix.height]
                    if not components or len(components) > 3:
                        continue
                    yy, xx = np.where(np.isin(labels, [i for i, _ in components]))
                    if len(xx) < 12 or not 8 <= int(yy.max()-yy.min()+1) <= 64:
                        continue
                    points = np.column_stack((xx, yy))
                    eigenvalues, vectors = np.linalg.eigh(np.cov(points, rowvar=False))
                    direction = vectors[:, -1]
                    slope = direction[0] / direction[1] if abs(direction[1]) > 0.01 else 0
                    residual = np.quantile(abs((points-points.mean(axis=0)) @ vectors[:, 0]), 0.9) / 4
                    if not (-0.9 <= slope <= -0.25 and residual < 0.4 and eigenvalues[0]/max(eigenvalues[1], 1) < 0.035):
                        continue
                    original = char.char
                    char.char = "/"
                    block.text = block.text[:index] + "/" + block.text[index+1:]
                    block.source_evidence.append(TextSourceEvidence(source="visible_table_slash", raw_text="/", page_no=page.page_no,
                        bbox=b, confidence=0.98))
                    repaired.append((block, original, b))
    return repaired


def recover_native_continuation_rows(document):
    """Recover an open-top continuation row from this PDF's visible vector grid.

    Require a small top-of-page integer, at least three aligned vertical rules,
    and one continuous bottom rule. No peer values or inferred text are used.
    """
    import fitz
    import re
    from html import escape
    if not Path(document.path).is_file():
        return []
    changed = []
    with fitz.open(document.path) as pdf:
        for page in document.pages:
            candidates = [b for b in page.blocks if b.block_type in {'ocr_line','text','number'}
                and re.fullmatch(r'\d{1,3}',b.text.strip()) and b.bbox.y1 < page.height*.18]
            if not candidates or not 1 <= page.page_no <= len(pdf):
                continue
            lines = [item for drawing in pdf[page.page_no-1].get_drawings()
                     if (drawing.get('stroke_opacity') or 0) >= .9 and drawing.get('color') is not None
                     for item in drawing['items'] if item[0]=='l']
            for block in candidates:
                cy=(block.bbox.y0+block.bbox.y1)/2
                vertical = [(a.x,min(a.y,b.y),max(a.y,b.y)) for _,a,b in lines
                            if abs(a.x-b.x)<.5 and min(a.y,b.y)<=cy<=max(a.y,b.y)
                            and 8<=abs(a.y-b.y)<=40]
                if len(vertical)<3:
                    continue
                baseline=min(vertical,key=lambda v:abs(v[0]-block.bbox.x0))
                grid=sorted(set(v[0] for v in vertical if abs(v[1]-baseline[1])<1 and abs(v[2]-baseline[2])<1))
                if len(grid)<3 or not grid[0] <= block.bbox.x0 < block.bbox.x1 <= grid[-1]:
                    continue
                if not any(abs(a.y-b.y)<.5 and abs(a.y-baseline[2])<1
                           and min(a.x,b.x)<=grid[0]+1 and max(a.x,b.x)>=grid[-1]-1 for _,a,b in lines):
                    continue
                col=next((i for i in range(len(grid)-1) if grid[i]<=block.bbox.x0 and block.bbox.x1<=grid[i+1]),None)
                if col is None:
                    continue
                block.raw_html='<table><tr>'+''.join('<td>'+ (escape(block.text.strip()) if i==col else '')+'</td>' for i in range(len(grid)-1))+'</tr></table>'
                block.table_cell_bboxes=[[a,baseline[1],b,baseline[2]] for a,b in zip(grid,grid[1:])]
                block.bbox=BBox(x0=grid[0],y0=baseline[1],x1=grid[-1],y1=baseline[2])
                block.block_type='table'
                block.flow_role='table'
                block.source += '+visible_native_continuation_grid'
                changed.append(block)
    return changed
