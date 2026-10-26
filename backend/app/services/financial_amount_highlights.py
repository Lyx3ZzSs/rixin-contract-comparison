"""Amount-owned display geometry; never correct or suppress OCR observations."""
import re

from app.models import BBox, EvidenceBox, FieldDisplayEvidence


# 任 is accepted only as an observed glyph inside an amount, not normalized to 仟.
_DIGITS = r'[零壹贰叁肆伍陆柒捌玖拾佰仟任万亿]+'
_AMOUNT = re.compile(rf'{_DIGITS}元(?:整|{_DIGITS}角(?:{_DIGITS}分)?|{_DIGITS}分)?')
FIELD_KEY = 'financial_uppercase_amount'


def amount_spans(diff):
    """Return field spans only when all edits belong to uppercase amounts."""
    if diff.source_type != 'clause' or diff.diff_type != 'MODIFY':
        return None
    result = {}
    for side in ('original', 'compare'):
        text = getattr(diff, side + '_text')
        changes = getattr(diff, side + '_change_ranges')
        matches = list(_AMOUNT.finditer(text))
        selected = set()
        if not changes:
            return None
        for change in changes:
            owner = next((m for m in matches if m.start() <= change.start < change.end <= m.end()), None)
            if owner is None:
                return None
            selected.add(owner.span())
        result[side] = sorted(selected)
    return result


def _boxes(clause, text, spans):
    if clause is None or clause.text != text or len(clause.char_boxes) != len(text):
        return None
    boxes = []
    for start, end in spans:
        chars = clause.char_boxes[start:end]
        if any(c is None or c.char != text[start + i]
               or c.bbox.x1 <= c.bbox.x0 or c.bbox.y1 <= c.bbox.y0 for i, c in enumerate(chars)):
            return None
        # Preserve source order and line/page boundaries, even for a wrapped value.
        lines = []
        for char in chars:
            if not lines or char.page_no != lines[-1][-1].page_no or abs(
                char.bbox.y0 + char.bbox.y1 - lines[-1][-1].bbox.y0 - lines[-1][-1].bbox.y1
            ) > 8:
                lines.append([])
            lines[-1].append(char)
        for line in lines:
            boxes.append(EvidenceBox(
                page_no=line[0].page_no,
                bbox=BBox(x0=min(c.bbox.x0 for c in line), y0=min(c.bbox.y0 for c in line),
                          x1=max(c.bbox.x1 for c in line), y1=max(c.bbox.y1 for c in line)),
                text=''.join(c.char for c in line), method='financial_amount_display',
                highlight_type='MODIFY', char_boxes=line,
            ))
    return boxes


def amount_display(diff, clauses, spans):
    evidence = {side: _boxes(clauses.get(side), getattr(diff, side + '_text'), spans[side])
                for side in ('original', 'compare')}
    if not all(evidence.values()):
        return None
    return FieldDisplayEvidence(field_key=FIELD_KEY, **evidence)
