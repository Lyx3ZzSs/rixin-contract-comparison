import fitz
import pytest

from app.models import BBox, CharBox, Clause, DiffItem, EvidenceBox, TextRange
from app.services.cover.comparator import _build_preamble_field_diff
from app.services.cover.types import CoverField
from app.services.evidence_locator import EvidenceLocator
from app.services.evidence_relocator import EvidenceRelocator
from app.services.text_coordinate_locator import TextCoordinateLocator


def pair(page=2, y=100):
    texts = ['甲方：国能长源随州发电有限公司随县分公司', '甲方：国能长源随州发电有限公司']
    fields, clauses = [], []
    for i, text in enumerate(texts):
        box = BBox(x0=40, y0=y, x1=40+len(text)*10, y1=y+12)
        evidence = EvidenceBox(page_no=page, bbox=box, text=text, method='cover_metadata')
        fields.append(CoverField(key='buyer', label='甲方', value=text[3:], evidences=[evidence]))
        clauses.append(Clause(clause_id=str(i), text=text, normalized_text=text, page_numbers=[page],
            char_boxes=[CharBox(char=c, page_no=page, text_index=k,
                bbox=BBox(x0=40+k*10, y0=y, x1=50+k*10, y1=y+12)) for k,c in enumerate(text)]))
    field = _build_preamble_field_diff(page, 'buyer', *fields, 1)
    clause = DiffItem(diff_id='D002', diff_type='MODIFY', source_type='clause',
        original_clause_id='0', compare_clause_id='1', original_text=texts[0], compare_text=texts[1],
        original_change_ranges=[TextRange(start=15, end=20, highlight_type='DELETE')])
    return field, clause, clauses


def test_preamble_change_has_one_owner_and_retains_real_deleted_suffix():
    field, clause, clauses = pair()
    result = EvidenceLocator().locate([field, clause], [clauses[0]], [clauses[1]])
    assert [d.diff_id for d in result] == ['D001']
    assert field.original_evidence[0].text == '随县分公司'
    assert field.original_evidence[0].page_no == 2


@pytest.mark.parametrize('mismatch', ['page', 'region', 'partial_change', 'missing_chars'])
def test_independent_or_incomplete_clause_cannot_consume_field(mismatch):
    field, clause, clauses = pair()
    if mismatch == 'page':
        for c in clauses:
            for char in c.char_boxes: char.page_no = 3
    elif mismatch == 'region':
        for c in clauses:
            for char in c.char_boxes:
                char.bbox.y0 += 300
                char.bbox.y1 += 300
    elif mismatch == 'partial_change':
        clause.original_change_ranges[0].end = 16
    else:
        clauses[1].char_boxes = []
    result = EvidenceLocator().locate([field, clause], [clauses[0]], [clauses[1]])
    assert field in result
    assert field.original_evidence and field.original_evidence[0].page_no == 2


def test_unanchored_metadata_must_not_search_cover_for_same_text(tmp_path):
    path = tmp_path/'repeat.pdf'
    pdf = fitz.open()
    pdf.new_page().insert_text((40, 100), 'CompanyBranch')
    pdf.new_page().insert_text((40, 100), 'CompanyBranch')
    pdf.save(path)
    pdf.close()
    diff = DiffItem(diff_id='D1', diff_type='MODIFY', source_type='metadata', original_snippet='CompanyBranch')
    TextCoordinateLocator().refine(path, path, [diff])
    assert diff.original_evidence == []


def test_standalone_field_only_highlights_changed_suffix():
    field, _, _ = pair()
    assert [(r.start, r.end) for r in field.original_change_ranges] == [(15,20)]
    assert field.compare_change_ranges == []


def test_mixed_clause_keeps_unrelated_title_change():
    field, diff, clauses = pair()
    # Add an independently located title change after the party line.
    start = len(clauses[0].text)
    clauses[0].text += '旧标题'
    clauses[0].char_boxes.extend(CharBox(char=c, page_no=2, text_index=start+i,
        bbox=BBox(x0=40+i*10, y0=50, x1=50+i*10, y1=62)) for i,c in enumerate('旧标题'))
    diff.original_text = clauses[0].text
    diff.original_change_ranges.append(TextRange(start=start, end=start+3, highlight_type='DELETE'))
    result = EvidenceLocator().locate([field, diff], [clauses[0]], [clauses[1]])
    assert len(result) == 2
    assert diff.original_snippet == '旧标题'
    assert [e.text for e in diff.original_evidence] == ['旧标题']
    assert [e.text for e in field.original_evidence] == ['随县分公司']


def test_remediation_cannot_reintroduce_cover_match_or_unchanged_side(tmp_path):
    field, _, _ = pair()
    result = EvidenceRelocator().relocate(field, side='compare', page_no=2,
        original_pdf=tmp_path/'missing.pdf', compare_pdf=tmp_path/'missing.pdf')
    assert result.status == 'SKIPPED'
    field.original_evidence = []
    result = EvidenceRelocator().relocate(field, side='original', page_no=None,
        original_pdf=tmp_path/'missing.pdf', compare_pdf=tmp_path/'missing.pdf')
    assert result.status == 'SKIPPED'
