"""Party-qualified field ownership shared by recognition and presentation."""
import re
import unicodedata

PARTY = re.compile(r'^(委托人|委托方|受托人|受托方|承包人|承包方|甲方|乙方|丙方|买方|卖方)(?:[（(][甲乙丙]方[）)])?\s*[:：]\s*(.+)$')
FIELD = re.compile(r'^(统一社会信用代码|住所地|注册地址|联系地址|法定代表人|联系人|联系电话|传真号码|邮箱地址)\s*[:：]\s*')
ROLES = {'委托方':'委托人','受托方':'承包人','受托人':'承包人','承包方':'承包人'}


def compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def party(text):
    m = PARTY.fullmatch(compact(text))
    return (ROLES.get(m[1], m[1]), m[2]) if m else None


def contact_blocks(page):
    """Only unambiguous vertical lists; never assign side-by-side parties by y."""
    anchors = [b for b in page.blocks if party(b.text)]
    if any(abs(a.bbox.y0-b.bbox.y0) < max(a.bbox.y1-a.bbox.y0, b.bbox.y1-b.bbox.y0)
           for i,a in enumerate(anchors) for b in anchors[i+1:]):
        return {}
    found = {}; owner = None
    for block in sorted(page.blocks, key=lambda b:(b.bbox.y0,b.bbox.x0)):
        anchor = party(block.text)
        if anchor:
            owner = anchor
        match = re.fullmatch(r'(传真号码|邮箱地址)[:：]\s*(.*)', compact(block.text))
        if owner and match:
            key = (*owner, match[1])
            found.setdefault(key, []).append(block)
    return {key:values[0] for key,values in found.items() if len(values)==1}


def text_fields(text):
    """Return unique value spans under explicit party anchors, retaining offsets."""
    result = {}; owner = None; offset = 0
    for line in text.splitlines(keepends=True):
        value = line.rstrip('\r\n')
        anchor = party(value)
        if anchor:
            owner = anchor
        else:
            m = FIELD.match(value)
            if owner and m:
                key = (*owner,m[1])
                result.setdefault(key, []).append((offset+m.end(), offset+len(value)))
            elif value.strip():
                # Preamble prose or a new section terminates field ownership.
                owner = None
        offset += len(line)
    return {key:spans[0] for key,spans in result.items() if len(spans)==1}
