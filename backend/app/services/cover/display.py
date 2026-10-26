"""Value-owned presentation geometry, independent of changed-character evidence."""
from app.models import FieldDisplayEvidence, TextRange
from .evidence import field_evidence
from .patterns import find_text_index


def _value_boxes(field):
    if not field.value_parts:
        return None
    cursor=0
    for part in field.value_parts:
        if part.start!=cursor or part.end<=part.start or field.value[part.start:part.end]!=part.text:
            return None
        offset=part.block_start if part.block_start is not None else find_text_index(part.block.text,part.text)
        if offset is None or part.block.text[offset:offset+len(part.text)]!=part.text:
            return None
        expected={offset+i for i,c in enumerate(part.text) if not c.isspace()}
        chars=[c for c in part.block.char_boxes if c.text_index in expected]
        if len(chars)!=len(expected) or {c.text_index for c in chars}!=expected:
            return None
        if any(c.char!=part.block.text[c.text_index] for c in chars):
            return None
        cursor=part.end
    if cursor!=len(field.value):
        return None
    return field_evidence(field,[TextRange(start=0,end=len(field.value),highlight_type='MODIFY')])


def location_display(key,left,right):
    # Location is one field value. Do not change presentation policies for
    # dates, identifiers, parties or unstructured extra cover text here.
    if key!='sign_place' or left is None or right is None:
        return None
    original,compare=_value_boxes(left),_value_boxes(right)
    if not original or not compare:
        return None
    return FieldDisplayEvidence(field_key=key,original=original,compare=compare)
