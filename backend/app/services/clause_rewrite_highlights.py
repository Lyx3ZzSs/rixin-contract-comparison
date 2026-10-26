"""Full-clause presentation for substantial prose rewrites; raw edits stay intact."""
from collections import Counter
import re

from app.models import ClauseDisplayEvidence
from app.services.party_field_scope import party, FIELD
from app.services.financial_amount_highlights import FIELD_KEY, amount_display, amount_spans


def _compact(text):
    return ''.join(text.split())


def _rewrite(diff):
    if diff.source_type != 'clause' or diff.diff_type != 'MODIFY' or not diff.clause_no:
        return False
    # Scope follows a complete prose clause with multiple wording edits, not
    # its character count. A phrase on each side distinguishes rewrites from
    # isolated OCR glyphs or changes only to numbers and punctuation.
    for side in ('original','compare'):
        text=getattr(diff,side+'_text')
        ranges=getattr(diff,side+'_change_ranges')
        if not text.rstrip().endswith(('。','；',';','.')) or len(ranges)<2:
            return False
        if not all(0<=r.start<r.end<=len(text) for r in ranges):
            return False
        if not any(re.search(r'[\u4e00-\u9fff]{2,}',text[r.start:r.end]) for r in ranges):
            return False
    return not any(party(line) or FIELD.match(line.strip())
                   for text in (diff.original_text,diff.compare_text) for line in text.splitlines())


def _boxes(clause, text, all_clauses):
    if clause.text!=text or not clause.bboxes:
        return None
    # Only verified clause-owned text blocks may extend the changed glyphs.
    if _compact(''.join(b.text for b in clause.bboxes))!=_compact(text):
        return None
    result=[]
    for source in clause.bboxes:
        box=source.bbox
        # Retain line contours, never fill the whitespace between lines. A
        # multiline source block needs finer geometry before it can be painted.
        if len(source.text.splitlines())!=1 or not source.text.strip():
            return None
        if box.x1<=box.x0 or box.y1<=box.y0:
            return None
        for other in all_clauses:
            if other.clause_id==clause.clause_id:
                continue
            if any(b.page_no==source.page_no and min(box.x1,b.bbox.x1)>max(box.x0,b.bbox.x0)
                   and min(box.y1,b.bbox.y1)>max(box.y0,b.bbox.y0) for b in other.bboxes):
                return None
        result.append(source.model_copy(deep=True,update={
            'method':'clause_rewrite_display','highlight_type':'MODIFY'}))

    return result


def prepare_clause_highlights(diffs, original_clauses, compare_clauses):
    documents={'original':original_clauses,'compare':compare_clauses}
    maps={s:{c.clause_id:c for c in cs} for s,cs in documents.items()}
    counts={s:Counter(getattr(d,s+'_clause_id') for d in diffs) for s in documents}
    for diff in diffs:
        diff.clause_display_evidence=None
        if diff.field_display_evidence and diff.field_display_evidence.field_key == FIELD_KEY:
            diff.field_display_evidence = None
        spans = amount_spans(diff)
        if spans is not None:
            clauses = {s: maps[s].get(getattr(diff, s + '_clause_id')) for s in documents}
            if all(counts[s][getattr(diff, s + '_clause_id')] == 1 for s in documents):
                diff.field_display_evidence = amount_display(diff, clauses, spans)
            # Missing field geometry falls back to raw changed glyphs, never prose scope.
            continue
        if not _rewrite(diff):
            continue
        evidence={}
        for side in documents:
            cid=getattr(diff,side+'_clause_id');clause=maps[side].get(cid)
            if clause is None or counts[side][cid]!=1:
                break
            boxes=_boxes(clause,getattr(diff,side+'_text'),documents[side])
            if not boxes:
                break
            evidence[side]=boxes
        if len(evidence)==2:
            diff.clause_display_evidence=ClauseDisplayEvidence(unit='clause',**evidence)
