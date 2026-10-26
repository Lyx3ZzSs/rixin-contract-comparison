"""Evidence-based repairs shared by all comparison channels."""
from __future__ import annotations

import re
import unicodedata

from app.models import Document, Page, TextBlock


def compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def revision_continuations(page: Page, is_revision) -> set[str]:
    """Keep a wrapped comment in its margin column, never consume body text."""
    ordered = sorted(page.blocks, key=lambda b: (b.bbox.y0, b.bbox.x0))
    comments = [b for b in ordered if is_revision(b, page)
                and b.bbox.x0 >= page.width * 0.65]
    result: set[str] = set()
    # Native PDF runs often split a balloon horizontally at the font change:
    # "设置格式[author]:" and "字体:..." are separate blocks on one row.
    # Keep the complete balloon extent so a wrapped payload can return to its
    # original left edge. A format keyword without a revision anchor is not noise.
    format_groups = [b.bbox.model_copy() for b in comments
                     if compact(b.text).startswith("设置格式[")]
    for block in ordered:
        if block in comments or block.raw_html or block.bbox.x0 < page.width * .65:
            continue
        text = compact(block.text)
        format_payload = bool(re.match(
            r"^(?:字体|字号|字形|字体颜色|色|颜色|突出显示|Hyperlink\.\d+)[,:：]?", text))
        for box in format_groups:
            row_height = max(1.0, block.bbox.y1 - block.bbox.y0)
            same_row = (min(box.y1, block.bbox.y1) - max(box.y0, block.bbox.y0) >= row_height * .5
                        and 0 <= block.bbox.x0 - box.x1 <= row_height)
            next_row = (0 <= block.bbox.y0 - box.y1 <= row_height * 1.1
                        and abs(block.bbox.x0 - box.x0) <= row_height
                        and block.bbox.x1 <= box.x1 + row_height)
            if len(text) <= 80 and ((same_row and format_payload) or (next_row and (
                    format_payload or re.match(r"^(?:宋体|黑体|楷体|仿宋|英语|中文|[（(]默认)", text)))):
                result.add(block.block_id)
                box.x0 = min(box.x0, block.bbox.x0)
                box.x1 = max(box.x1, block.bbox.x1)
                box.y1 = max(box.y1, block.bbox.y1)
                break
    for block in ordered:
        if block in comments or block.block_type in {"table", "number"}:
            continue
        for previous in comments:
            height = max(previous.bbox.y1 - previous.bbox.y0, 1)
            if (0 <= block.bbox.y0 - previous.bbox.y1 <= height * 1.1
                    and abs(block.bbox.x0 - previous.bbox.x0) <= height
                    and block.bbox.x1 <= previous.bbox.x1 + height
                    and len(compact(block.text)) <= 40
                    and not re.match(r"^(?:第.+[章节条]|\d+[.、])", block.text)):
                result.add(block.block_id)
                comments.append(block)
                break
    return result


def repeated_title_ids(document: Document) -> set[str]:
    if not document.pages:
        return set()
    titles = {compact(b.text) for b in document.pages[0].blocks
              if b.block_type in {"doc_title", "title"}
              and 8 <= len(compact(b.text)) <= 80
              and re.search(r"合同|协议", b.text)}
    return {b.block_id for page in document.pages[1:] for b in page.blocks
            if b.block_type in {"header", "page_header", "doc_title", "title"}
            and compact(b.text) in titles}


def reconcile_contact_placeholders(original: Document, compare: Document) -> list[tuple[str, TextBlock]]:
    from app.services.contact_placeholder_pixels import reconcile
    return reconcile(original, compare)


def repair_corroborated_ocr(page: Page) -> list[TextBlock]:
    """Correct a duplicated overlap or amount glyph only with local corroboration."""
    repaired = []
    blocks = sorted(page.blocks, key=lambda b: (b.bbox.y0, b.bbox.x0))
    for right in blocks:
        if right.block_type != 'text' or not right.char_boxes:
            continue
        sources = [compact(s.raw_text) for s in right.source_evidence
                   if s.source == 'ppstructure' and s.page_no == page.page_no and s.bbox is not None]
        if not sources:
            continue
        for left in blocks:
            if left is right or left.block_type != 'text' or not left.char_boxes:
                continue
            if not (left.bbox.x0 < right.bbox.x0 < left.bbox.x1
                    and min(left.bbox.y1, right.bbox.y1)-max(left.bbox.y0, right.bbox.y0) > 0):
                continue
            for n in range(min(8, len(left.text), len(right.text)), 0, -1):
                if left.text[-n:] != right.text[:n]:
                    continue
                a = [c for c in left.char_boxes if c.text_index is not None and c.text_index >= len(left.text)-n]
                b = [c for c in right.char_boxes if c.text_index is not None and c.text_index < n]
                if len(a) != n or len(b) != n:
                    continue
                if not all(min(x.bbox.x1,y.bbox.x1)+1 > max(x.bbox.x0,y.bbox.x0)
                           and min(x.bbox.y1,y.bbox.y1)>max(x.bbox.y0,y.bbox.y0) for x,y in zip(a,b)):
                    continue
                joined, duplicate = compact(left.text+right.text[n:]), compact(left.text+right.text)
                if len(joined) < 6 or not any(joined in s and duplicate not in s for s in sources):
                    continue
                right.text = right.text[n:]
                right.char_boxes = [c.model_copy(update={'text_index':c.text_index-n}) for c in right.char_boxes
                                    if c.text_index is not None and c.text_index >= n]
                repaired.append(right)
                break
        # Never normalize arbitrary prose or change a numeral. The second OCR
        # must contain the complete financial token, not just the proposed glyph.
        for match in list(re.finditer(r'[零壹贰叁肆伍陆柒捌玖拾佰仟任万亿元角分整]{8,}', right.text)):
            old = match.group()
            new = old.replace('任', '仟')
            if old == new or not any(new in s and old not in s and re.search(r'\d+\.\d+元', s) for s in sources):
                continue
            right.text = right.text[:match.start()]+new+right.text[match.end():]
            for c in right.char_boxes:
                if c.text_index is not None and match.start() <= c.text_index < match.end():
                    c.char = right.text[c.text_index]
            repaired.append(right)
    return repaired


def reconcile_body_roles(original: Document, compare: Document) -> list[tuple[str, TextBlock]]:
    """Recover a local body fragment when the paired page confirms its role.

    Exact text, corresponding geometry, and an independent neighbouring body
    anchor are all required. A layout detector's unmatched status is not proof
    that recognized text is noise.
    """
    changes = []
    for left, right in zip(original.pages, compare.pages):
        if left.page_no != right.page_no or left.page_no == 1:
            continue
        shared = {compact(b.text) for b in left.blocks if len(compact(b.text)) >= 8}
        shared &= {compact(b.text) for b in right.blocks if len(compact(b.text)) >= 8}
        if not shared:
            continue
        for side, page, other in [('original', left, right), ('compare', right, left)]:
            for block in page.blocks:
                text = compact(block.text)
                if len(text) < 3 or not re.search(r'[\u4e00-\u9fff]', text):
                    continue
                if block.block_type in {'table', 'image', 'figure', 'seal'} or block.bbox.y0 > page.height*.85:
                    continue
                peers = [b for b in other.blocks if compact(b.text) == text
                         and abs(b.bbox.y0/other.height-block.bbox.y0/page.height) < .06
                         and abs(b.bbox.x0/other.width-block.bbox.x0/page.width) < .08
                         and b.block_type in {'text','paragraph_title','doc_title'}
                         and b.flow_role not in {'noise','non_text','margin'}]
                if len(peers) != 1:
                    continue
                peer = peers[0]
                if (peer.block_type in {'paragraph_title','doc_title'} and text.endswith(('表','单','书'))
                        and len(text) <= 24
                        and (block.bbox.y0 < page.height*.3 or any(
                            b.block_type == 'table' and 0 <= b.bbox.y0-block.bbox.y1 <= 60
                            for b in page.blocks))):
                    block.block_type = peer.block_type = 'paragraph_title'
                    block.flow_role = peer.flow_role = 'heading'
                    block.enter_clause_compare = peer.enter_clause_compare = True
                    changes.append((side, block))
                elif block.flow_role in {'noise','margin'} or block.block_type in {'footnote','vision_footnote'}:
                    if not any(-12 <= block.bbox.y0-anchor.bbox.y0 <= 65 and compact(anchor.text) in shared for anchor in page.blocks):
                        continue
                    block.block_type = 'text'
                    block.flow_role = 'body'
                    block.enter_clause_compare = True
                    changes.append((side, block))
    return changes


def recover_visible_native_punctuation(document: Document) -> list[TextBlock]:
    """Disambiguate isolated OCR glyphs using visible native glyphs at the ROI."""
    from pathlib import Path
    import fitz
    from app.models import TextSourceEvidence
    if not Path(document.path).is_file():
        return []
    changes = []
    with fitz.open(document.path) as pdf:
        for page in document.pages:
            candidates = [b for b in page.blocks if re.fullmatch('[oO0Il]', b.text.strip()) and b.block_type == 'text']
            if not candidates or page.page_no > len(pdf):
                continue
            native = pdf[page.page_no-1]
            if any(s['type'] == 3 or s['opacity'] < .9 for s in native.get_texttrace()):
                continue
            images = [fitz.Rect(i['bbox']) for i in native.get_image_info()]
            chars = [c for b in native.get_text('rawdict')['blocks'] for line in b.get('lines',[])
                     for span in line['spans'] for c in span['chars']]
            for block in candidates:
                point = fitz.Point((block.bbox.x0+block.bbox.x1)/2, (block.bbox.y0+block.bbox.y1)/2)
                if any(point in box for box in images):
                    continue
                hits = [c for c in chars if point in fitz.Rect(c['bbox']) and c['c'].strip()]
                if len(hits) != 1 or hits[0]['c'] not in '。.,，;；:：':
                    continue
                block.text = hits[0]['c']
                if len(block.char_boxes) == 1:
                    block.char_boxes[0].char = block.text
                block.source_evidence.append(TextSourceEvidence(source='visible_native_glyph',raw_text=block.text,
                                                               bbox=block.bbox,page_no=page.page_no,confidence=1))
                changes.append(block)
    return changes


def recover_native_technical_tokens(document: Document) -> list[TextBlock]:
    """Restore omitted filename separators only from co-located visible text."""
    from pathlib import Path
    import fitz
    from app.models import CharBox, BBox, TextSourceEvidence
    if not Path(document.path).is_file():
        return []
    changed = []
    norm = lambda s: re.sub(r'[\s_]+', '', unicodedata.normalize('NFKC', s))
    with fitz.open(document.path) as pdf:
        for page in document.pages:
            native = pdf[page.page_no-1]
            if any(s['type'] == 3 or s['opacity'] < .9 for s in native.get_texttrace()):
                continue
            images = [fitz.Rect(i['bbox']) for i in native.get_image_info()]
            lines = [l for b in native.get_text('rawdict')['blocks'] for l in b.get('lines', [])]
            for block in page.blocks:
                if block.block_type != 'text' or '.txt' not in block.text.lower():
                    continue
                roi = fitz.Rect(block.bbox.x0,block.bbox.y0,block.bbox.x1,block.bbox.y1)
                if any((roi & box).get_area() > roi.get_area()*.2 for box in images):
                    continue
                matches = []
                for line in lines:
                    chars = [c for s in line['spans'] for c in s['chars']]
                    text = ''.join(c['c'] for c in chars)
                    rect = fitz.Rect(line['bbox'])
                    horizontal_overlap = max(0.0, min(rect.x1, roi.x1) - max(rect.x0, roi.x0))
                    if ('_' in text and text.count('_') > block.text.count('_') and norm(text)==norm(block.text)
                            and abs((rect.y0+rect.y1-roi.y0-roi.y1)/2)<12
                            and horizontal_overlap >= min(rect.width, roi.width)*.7):
                        matches.append((text, chars))
                if len(matches) != 1:
                    continue
                text, chars = matches[0]
                block.text = text
                block.char_boxes = [CharBox(char=c['c'],page_no=page.page_no,text_index=i,
                    bbox=BBox(x0=c['bbox'][0],y0=c['bbox'][1],x1=c['bbox'][2],y1=c['bbox'][3])) for i,c in enumerate(chars)]
                block.source_evidence.append(TextSourceEvidence(source='visible_native_filename',raw_text=text,page_no=page.page_no,bbox=block.bbox,confidence=1))
                changed.append(block)
    return changed


def normalize_signing_lines(document: Document) -> list[TextBlock]:
    """Use OCR character geometry for explicit signing pages with marginal revisions.

    Both a merged table and joined two-column OCR lines become physical lines.
    Keep visual objects and every source character except separately located
    revision comments; never use HTML cell text to invent field characters.
    """
    from app.models import BBox
    repaired = []
    for page in document.pages:
        if not any('本页无正文' in b.text and '签署页' in b.text and len(b.text)<100 for b in page.blocks):
            continue
        starts=[]
        for b in page.blocks:
            for match in re.finditer('法定代表人', b.text):
                starts.extend(c.bbox.x0 for c in b.char_boxes if c.text_index==match.start())
        starts=sorted(starts)
        split=max(zip(starts,starts[1:]),key=lambda pair:pair[1]-pair[0],default=None)
        column_start=split[1]-1 if split and split[1]-split[0]>100 else None
        blocks = []
        for block in page.blocks:
            if not block.char_boxes or block.block_type in {'seal','stamp','image','figure','footer','number'}:
                blocks.append(block)
                continue
            chars = [c for c in block.char_boxes if c.char.strip()]
            if block.block_type == 'table':
                comment_x = []
                for match in re.finditer(r'(?:设置格式|删除|插入)\[', block.text):
                    hits = [c for c in chars if c.text_index == match.start()]
                    comment_x.extend(c.bbox.x0 for c in hits if c.bbox.x0>block.bbox.x0+(block.bbox.x1-block.bbox.x0)*.55)
                if comment_x:
                    cutoff=min(comment_x)-3
                    chars=[c for c in chars if c.bbox.x1<cutoff]
            lines=[]
            for char in sorted(chars,key=lambda c: ((c.bbox.y0+c.bbox.y1)/2,c.bbox.x0)):
                cy=(char.bbox.y0+char.bbox.y1)/2
                if not lines or abs(cy-lines[-1][0])>4:
                    lines.append((cy,[]))
                lines[-1][1].append(char)
            parts=[]
            for _,line in lines:
                ordered=sorted(line,key=lambda c:c.bbox.x0)
                if column_start is not None and '本页无正文' not in block.text:
                    column_groups=[[c for c in ordered if (c.bbox.x0+c.bbox.x1)/2<column_start],
                                   [c for c in ordered if (c.bbox.x0+c.bbox.x1)/2>=column_start]]
                else:
                    column_groups=[ordered]
                groups=[group for group in column_groups if group]
                for group in groups:
                    group.sort(key=lambda c:c.text_index if c.text_index is not None else c.bbox.x0)
                    text=''.join(c.char for c in group)
                    bbox=BBox(x0=min(c.bbox.x0 for c in group),y0=min(c.bbox.y0 for c in group),x1=max(c.bbox.x1 for c in group),y1=max(c.bbox.y1 for c in group))
                    parts.append(block.model_copy(update={'block_id':f'{block.block_id}_physical_{len(parts)}','text':text,'bbox':bbox,
                        'block_type':'text','flow_role':'body','raw_html':'','table_cell_bboxes':[],
                        'layout_bbox':None,'source':block.source+'+signing_physical_line',
                        'char_boxes':[c.model_copy(update={'text_index':i}) for i,c in enumerate(group)],'source_evidence':[]}))
            if parts:
                blocks.extend(parts);repaired.append(block)
            else:
                blocks.append(block)
        page.blocks=blocks
    return repaired


def reconcile_local_source_disagreement(original: Document, compare: Document) -> list[tuple[str, TextBlock]]:
    """Resolve one CJK OCR glyph only when own layout and peer sources agree."""
    changed=[]
    pages={p.page_no:p for p in compare.pages}
    for left in original.pages:
        right=pages.get(left.page_no)
        if right is None:
            continue
        for side,page,peer in [('original',left,right),('compare',right,left)]:
            for block in page.blocks:
                if block.block_type!='text' or not re.search(r'地址\s*[:：]', block.text):
                    continue
                text=compact(block.text)
                for evidence in block.source_evidence:
                    candidate=compact(evidence.raw_text)
                    if evidence.source!='ppstructure' or evidence.bbox is None or len(candidate)!=len(text) or len(text)<16:
                        continue
                    differences=[i for i,(a,b) in enumerate(zip(text,candidate)) if a!=b]
                    if len(differences)!=1 or not all('\u4e00'<=s[differences[0]]<='\u9fff' for s in [text,candidate]):
                        continue
                    peers=[b for b in peer.blocks if compact(b.text)==candidate
                        and abs(b.bbox.y0/peer.height-block.bbox.y0/page.height)<.035
                        and abs(b.bbox.x0/peer.width-block.bbox.x0/page.width)<.06]
                    if len(peers)!=1 or not any(s.source=='ppstructure' and compact(s.raw_text)==candidate for s in peers[0].source_evidence):
                        continue
                    positions=[i for i,c in enumerate(block.text) if not c.isspace()]
                    if len(positions)!=len(text):
                        continue
                    index=positions[differences[0]];replacement=candidate[differences[0]]
                    block.text=block.text[:index]+replacement+block.text[index+1:]
                    for char in block.char_boxes:
                        if char.text_index==index:char.char=replacement
                    changed.append((side,block));break
    return changed


def reconcile_interior_document_titles(original: Document, compare: Document):
    """Compare interior title prose on both sides, even when layout roles differ.

    Require a paired contract opening (both party labels below a compact title
    band). Give preamble metadata the whole title on both sides; later openings
    outside its scope must remain in clause comparison.
    """
    changes = []
    for left, right in zip(original.pages[1:], compare.pages[1:]):
        if left.page_no != right.page_no:
            continue
        if not all(any(b.text.strip().startswith('鉴于') for b in p.blocks) for p in (left, right)):
            continue
        groups = []
        for page in (left, right):
            parties = [b for b in page.blocks if re.match(r'^(甲方|乙方)[:：]', compact(b.text))
                       and b.bbox.y0 < page.height*.28]
            if {compact(b.text)[:2] for b in parties} != {'甲方','乙方'}:
                break
            top = min(b.bbox.y0 for b in parties)
            titles = [b for b in page.blocks if 6<=len(compact(b.text))<=65
                      and b.block_type in {'text','paragraph_title','doc_title'}
                      and b.bbox.y1 < top and b.bbox.y0 > page.height*.04
                      and abs((b.bbox.x0+b.bbox.x1)/2-page.width/2)<page.width*.15]
            if not 1<=len(titles)<=3 or not any('合同' in b.text or '协议' in b.text for b in titles):
                break
            groups.append(titles)
        if len(groups)!=2 or not any(b.block_type=='doc_title' for g in groups for b in g):
            continue
        from app.services.cover.extractor import _extract_cover
        from app.services.normalizer import TextNormalizer

        for group in groups:
            for block in group:
                block.block_type = 'doc_title'
        # Do not suppress clause text unless the metadata extractor actually
        # owns every title line on BOTH sides, including unchanged titles.
        fields = [_extract_cover(TextNormalizer(), doc).preamble_titles.get(left.page_no)
                  for doc in (original, compare)]
        metadata_owned = all(
            field is not None
            and {b.block_id for b in group} == {part.block.block_id for part in field.value_parts}
            for field, group in zip(fields, groups)
        )
        for side, group in zip(('original','compare'), groups):
            for block in group:
                block.block_type = 'doc_title' if metadata_owned else 'paragraph_title'
                block.enter_clause_compare = not metadata_owned
                block.flow_role='heading'
                block.semantic_reasons.append('paired_interior_document_title')
                changes.append((side,block))
    return changes
