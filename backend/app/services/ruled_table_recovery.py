"""Recover cell ownership from visible rules, retaining the OCR text evidence.

Only closed rectangular cells are accepted. No text recognition or value
substitution is performed; every non-space OCR character must have one owner.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from html import escape
from pathlib import Path
import re
import unicodedata

from app.models import Document
from app.services.ordered_analysis import ordered_analysis
from app.services.pipeline_metrics import PerformanceRecorder


def _clusters(values, tolerance=4):
    groups = []
    for value in sorted(values):
        if groups and value - groups[-1][-1] <= tolerance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def recover_ruled_tables(document: Document, *, max_inflight: int = 1,
                         performance: PerformanceRecorder | None = None) -> list:
    import cv2
    import fitz
    import numpy as np

    if not Path(document.path).is_file():
        return []
    recovered = []
    performance = performance or PerformanceRecorder()

    def analyze(item):
        page, blocks, gray = item
        height, width = gray.shape
        with performance.measure("ruled_table_geometry"):
            binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15)
            # One-pixel morphology loses the rules of a slightly rotated
            # scan. Estimate page skew from several long horizontal rules,
            # then map recovered cells back into the source coordinates.
            inverse_rotation = None
            rotation = None
            segments = cv2.HoughLinesP(binary, 1, np.pi / 1800, 80,
                                       minLineLength=width * .25, maxLineGap=12)
            angles = []
            if segments is not None:
                for x0, y0, x1, y1 in segments[:, 0]:
                    angle = float(np.degrees(np.arctan2(y1-y0, x1-x0)))
                    if abs(angle) <= 4:
                        angles.append(angle)
            if len(angles) >= 4:
                cluster = max(([a for a in angles if abs(a-center) <= .25] for center in angles), key=len)
                angle = float(np.median(cluster))
                if len(cluster) >= max(4,len(angles)*.4) and .15 <= abs(angle) <= 4:
                    rotation = cv2.getRotationMatrix2D((width/2, height/2), angle, 1)
                    binary = cv2.warpAffine(binary, rotation, (width, height), flags=cv2.INTER_NEAREST)
                    inverse_rotation = cv2.invertAffineTransform(rotation)
            horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((1, max(30, width // 25)), np.uint8))
            vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((max(30, height // 25), 1), np.uint8))
            rules = cv2.bitwise_or(horizontal, vertical)
            rules = cv2.morphologyEx(rules, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
            contours, hierarchy = cv2.findContours(rules, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            sx, sy = page.width / width, page.height / height
            if hierarchy is None:
                return page, blocks, [], sx, sy, inverse_rotation, rotation
            rectangles = []
            for contour, node in zip(contours, hierarchy[0]):
                if node[3] < 0:  # bounded white cells, not the enclosing table
                    continue
                x, y, w, h = cv2.boundingRect(contour)
                if w * sx < 12 or h * sy < 8 or cv2.contourArea(contour) / (w * h) < .85:
                    continue
                rectangles.append((x*sx, y*sy, (x+w)*sx, (y+h)*sy))
        return page, blocks, rectangles, sx, sy, inverse_rotation, rotation

    with fitz.open(document.path) as pdf:
        def inputs():
            for page in document.pages:
                blocks = [b for b in page.blocks if b.block_type == 'table' and b.char_boxes]
                if not blocks or not 1 <= page.page_no <= len(pdf):
                    continue
                # PyMuPDF handles are used exclusively by the caller thread.
                with performance.measure("ruled_table_render"):
                    pix = pdf[page.page_no - 1].get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csGRAY)
                    gray = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
                performance.increment("ruled_table_pages")
                yield page, blocks, gray

        with closing(ordered_analysis(analyze, inputs(), max_inflight)) as analyzed:
            for page, blocks, rectangles, sx, sy, inverse_rotation, rotation in analyzed:
                def rotated_point(x, y):
                    if inverse_rotation is None:
                        return x, y
                    point = rotation @ np.array([x/sx, y/sy, 1])
                    return point[0]*sx, point[1]*sy

                def mapped_rect(rect, matrix):
                    x0,y0,x1,y1 = rect
                    corners = np.array([[x0/sx,y0/sy,1],[x1/sx,y0/sy,1],
                                        [x1/sx,y1/sy,1],[x0/sx,y1/sy,1]]) @ matrix.T
                    return (float(corners[:,0].min()*sx), float(corners[:,1].min()*sy),
                            float(corners[:,0].max()*sx), float(corners[:,1].max()*sy))
                for block in blocks:
                    compact = re.sub(r"\s+", "", block.text)
                    # A signing frame encloses several labelled fields; its rules
                    # do not describe the semantic field rows. Keep that schema
                    # (including independently recognized date values) intact.
                    labelled_signing_form = (
                        re.search(r"法定代表人|法人代表|授权代表|委托代理人", block.text)
                        and re.search(r"甲方|乙方|单位名称|单位地址", block.text)
                    ) or (
                        page.page_no == 1 and re.search(r"签订日期|签署日期", block.text)
                        and re.search(r"甲方|乙方|买方|卖方", block.text)
                    ) or (
                        "开户银行" in compact and re.search(r"[帐账账]号|账号", compact)
                        and re.search(r"税号|信用代码|纳税人", compact)
                    )
                    if labelled_signing_form:
                        continue
                    if Counter(c for c in block.text if not c.isspace()) != Counter(
                        c for char in block.char_boxes for c in char.char if not c.isspace()
                    ):
                        continue
                    box = block.bbox
                    bounds = (box.x0,box.y0,box.x1,box.y1)
                    if inverse_rotation is not None:
                        bounds = mapped_rect(bounds,rotation)
                    cells = [r for r in rectangles if r[0] >= bounds[0]-8 and r[1] >= bounds[1]-8 and r[2] <= bounds[2]+8 and r[3] <= bounds[3]+8]
                    if len(cells) < 2:
                        continue
                    # Form labels above a closed frame belong to the same OCR
                    # object. Retain them in a separate leading band, rather than
                    # discarding them or inventing interior grid lines.
                    top = min(r[1] for r in cells)
                    above = [c for c in block.char_boxes if c.char.strip()
                             and rotated_point((c.bbox.x0+c.bbox.x1)/2,(c.bbox.y0+c.bbox.y1)/2)[1] < top-1]
                    if above and top - min(rotated_point(c.bbox.x0,c.bbox.y0)[1] for c in above) <= 65:
                        cells.append((min(r[0] for r in cells), min(rotated_point(c.bbox.x0,c.bbox.y0)[1] for c in above)-1,
                                      max(r[2] for r in cells), top))
                    xs = _clusters([x for r in cells for x in (r[0], r[2])])
                    ys = _clusters([y for r in cells for y in (r[1], r[3])])
                    if len(xs) < 2 or len(ys) < 3:
                        continue
                    slots = []
                    occupied = set()
                    valid = True
                    for rect in cells:
                        x0, y0, x1, y1 = rect
                        a, b = [min(range(len(xs)), key=lambda i: abs(xs[i]-x)) for x in (x0, x1)]
                        c, d = [min(range(len(ys)), key=lambda i: abs(ys[i]-y)) for y in (y0, y1)]
                        area = {(r, col) for r in range(c, d) for col in range(a, b)}
                        if not area or area & occupied:
                            valid = False
                            break
                        occupied |= area
                        slots.append((c, a, d-c, b-a, rect, []))
                    # Missing borders must never be interpreted as blank cells.
                    if not valid or len(occupied) != (len(xs)-1)*(len(ys)-1):
                        continue
                    for char in block.char_boxes:
                        if not char.char.strip():
                            continue
                        cx, cy = (char.bbox.x0+char.bbox.x1)/2, (char.bbox.y0+char.bbox.y1)/2
                        cx, cy = rotated_point(cx,cy)
                        # Use shared grid boundaries, not independently expanded
                        # contour boxes which give border-adjacent glyphs two owners.
                        owners = [slot for slot in slots
                                  if xs[slot[1]] <= cx < xs[slot[1]+slot[3]]
                                  and ys[slot[0]] <= cy < ys[slot[0]+slot[2]]]
                        if len(owners) != 1:
                            valid = False
                            break
                        owners[0][5].append(char)
                    if not valid:
                        continue
                    rows = [[] for _ in range(len(ys)-1)]
                    boxes = []
                    texts = []
                    for row, col, rowspan, colspan, rect, chars in sorted(slots):
                        lines = []
                        for char in sorted(chars, key=lambda c: ((c.bbox.y0+c.bbox.y1)/2, c.bbox.x0)):
                            cy = (char.bbox.y0+char.bbox.y1)/2
                            if not lines or abs(cy-lines[-1][0]) > 4:
                                lines.append((cy, []))
                            lines[-1][1].append(char)
                        text = '\n'.join(''.join(c.char for c in sorted(line, key=lambda c: c.bbox.x0)) for _, line in lines)
                        texts.append(text)
                        rows[row].append(f'<td rowspan="{rowspan}" colspan="{colspan}">{escape(text)}</td>')
                        boxes.append(list(mapped_rect(rect,inverse_rotation) if inverse_rotation is not None else rect))
                    if Counter(''.join(c.char for c in block.char_boxes if c.char.strip())) != Counter(''.join(texts).replace('\n','')):
                        continue
                    # Geometry repair must not replace correctly read technical
                    # units with a truncated line-OCR spelling (e.g. 24AH -> 24H).
                    # Character conservation against line OCR alone is insufficient:
                    # the independent HTML reading is also an input to preserve.
                    from xml.etree import ElementTree
                    try:
                        old_text = " ".join(ElementTree.fromstring(block.raw_html).itertext())
                    except (ElementTree.ParseError, TypeError):
                        old_text = ""
                    def units(value):
                        value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
                        value = re.sub(r"(?<=\d)O(?=V|A|W|Hz)", "0", value)
                        return Counter(re.findall(r"\d+(?:\.\d+)?[A-Za-z]{1,5}", value))
                    if units(old_text) - units(" ".join(texts)):
                        continue
                    block.raw_html = '<table>' + ''.join('<tr>'+''.join(row)+'</tr>' for row in rows) + '</table>'
                    block.table_cell_bboxes = boxes
                    if "visible_ruled_grid" not in block.source:
                        block.source = (block.source or "ocr") + "+visible_ruled_grid"
                    recovered.append(block)
    return recovered
