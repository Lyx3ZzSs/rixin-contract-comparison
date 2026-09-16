"""Positive and counterfactual controls for signing/body ownership repairs."""
from copy import deepcopy
from types import SimpleNamespace

import fitz
import pytest

from app.models import BBox, DiffItem, Document, EvidenceBox, Page, TextBlock
from app.services.normalizer import TextNormalizer
from app.services.seal_comparator import SealEntry
from app.services.seal_visual_equivalence import corresponding_red_graphics
from app.services.signing_region.printed_ownership import reconcile_party_table_diffs, exclude_equal_signing_prose
from app.services.signing_region.clause_document import SigningClauseDocumentResult

BOX = BBox(x0=80, y0=100, x1=300, y1=125)


def doc(text):
    block = TextBlock(block_id='row', page_no=1, block_type='table', text=text, bbox=BOX)
    return Document(filename='x.pdf', path='missing.pdf', page_count=1,
                    pages=[Page(page_no=1, width=600, height=800, blocks=[block])])


def test_primary_party_agreement_reconciles_cell_noise_but_not_real_change():
    before = doc('甲方：北京甲方公司\n乙方：上海乙方公司')
    after = doc('甲方：北京甲方公司\n乙方：上海乙方公司')
    e = EvidenceBox(page_no=1, bbox=BOX, text='甲', method='table_cell')
    diff = DiffItem(diff_id='candidate', source_type='table', diff_type='MODIFY',
                    original_text='甲方：北京甲方公司 (盖章） 头 出',
                    compare_text='甲方：北京乙方公司 号 方', original_evidence=[e], compare_evidence=[e])
    assert not reconcile_party_table_diffs([diff], before, after)[0]
    after.pages[0].blocks[0].text = '甲方：北京乙方公司\n乙方：上海乙方公司'
    assert reconcile_party_table_diffs([diff], before, after)[0] == [diff]
    after.pages[0].blocks[0].text = '甲方：北京甲方公司\n甲方：北京乙方公司'
    assert reconcile_party_table_diffs([diff], before, after)[0] == [diff]


def test_signing_prose_consumption_requires_party_bound_values_and_retains_prose():
    def region(date):
        elems = [SimpleNamespace(element_type='party_field', text=f'{role}：{name}',
                                raw_ref={'party_role': role})
                 for role, name in [('甲方', '北京甲方公司'), ('乙方', '上海乙方公司')]]
        elems.append(SimpleNamespace(element_type='field', text=date,
                    raw_ref={'party_role':'乙方','field_key':'date'}))
        return SimpleNamespace(elements=elems, page_no=1, signing_block_id='sb')
    def result():
        d = doc('甲方：北京甲方公司')
        d.pages[0].blocks += [TextBlock(block_id='prose', page_no=1, text='甲方承担全部费用。', bbox=BOX)]
        return SigningClauseDocumentResult(d)
    owner = SimpleNamespace(block_id='sb', exclude_from_clause_diff=False, source_block_ids=['row','prose'])
    a,b = result(),result()
    exclude_equal_signing_prose(a,b,[(region('2026.05.30'),region('2026.05.30'),1)],[owner],[owner])
    assert [x.block_id for x in a.document.pages[0].blocks] == ['prose']
    a,b = result(),result()
    exclude_equal_signing_prose(a,b,[(region('2026.05.30'),region('2026.05.03'),1)],[owner],[owner])
    assert len(a.document.pages[0].blocks) == 2


def test_chinese_year_zero_normalization_does_not_fold_changed_year_or_numbers():
    n = TextNormalizer().normalize_for_diff
    assert n('二0二六年五月') == n('二〇二六年五月')
    assert n('二〇二六年五月') != n('二〇二七年五月')
    assert n('账户10030') != n('账户1030')


def seal_pdf(tmp_path, name, changed=False):
    path = tmp_path / name
    pdf = fitz.open(); page = pdf.new_page(width=400, height=400)
    red = (0.8, 0.05, 0.05)
    page.draw_circle((200,200),65,color=red,width=2)
    for i in range(5):
        page.insert_text((153,165+i*18),'STAMP 123456' if not(changed and i==2) else 'STAMP 123956',
                         fontsize=10,color=red)
    pdf.save(path); pdf.close()
    d = Document(filename=name,path=str(path),page_count=1,
                 pages=[Page(page_no=1,width=400,height=400,blocks=[])])
    return d, SealEntry(1,'OCR',BBox(x0=130,y0=130,x1=270,y1=270))


def test_red_ink_comparison_rejects_changed_inscription_with_same_ring(tmp_path):
    a, x = seal_pdf(tmp_path,'a.pdf')
    b, y = seal_pdf(tmp_path,'b.pdf')
    c, z = seal_pdf(tmp_path,'c.pdf',True)
    assert corresponding_red_graphics(a,x,b,y,verify_ink=True)['equivalent']
    assert not corresponding_red_graphics(a,x,c,z,verify_ink=True)['equivalent']


def test_scan_edge_slivers_do_not_become_seal_additions():
    from app.services.signing_region.visual_recall import WholePageSigningVisualRecall, SigningVisualRecallResult
    from app.services.signing_region.models import SigningVisualObject, SigningVisualObjectType
    thin = SigningVisualObject(object_id='slivers',object_type=SigningVisualObjectType.EDGE_SEAM_SEAL,
        page_no=1,bbox=BBox(x0=598,y0=100,x1=600,y1=120), supporting_pages=[1,2,3],
        fragment_bboxes=[BBox(x0=598,y0=100,x1=600,y1=120)]*3,
        visual_detection_confidence=.9)
    result = SigningVisualRecallResult(additions=[thin])
    assert WholePageSigningVisualRecall().reconcile_diffs(result,[],[],start_index=1) == ([],[])
    assert thin.raw_ref['decision_reason']=='SCAN_EDGE_SLIVERS_WITHOUT_SEAL_STRUCTURE'
    wide = thin.model_copy(deep=True)
    wide.fragment_bboxes = [BBox(x0=580,y0=100,x1=600,y1=160)]*3
    result = SigningVisualRecallResult(additions=[wide])
    assert len(WholePageSigningVisualRecall().reconcile_diffs(result,[],[],start_index=1)[1])==1


def test_company_row_reconciliation_retains_added_obligation():
    before, after = doc('甲方：北京甲方公司'), doc('甲方：北京甲方公司')
    e = EvidenceBox(page_no=1,bbox=BOX,method='table_cell')
    diff = DiffItem(diff_id='d',source_type='table',diff_type='MODIFY',
        original_text='甲方：北京甲方公司 (盖章)',compare_text='甲方：北京甲方公司 承担连带责任',
        original_evidence=[e],compare_evidence=[e])
    assert reconcile_party_table_diffs([diff],before,after)[0] == [diff]


def test_page_promotion_does_not_duplicate_cross_page_clause():
    from app.services.page_diff import PageDiffConsolidator
    from tests.test_page_diff import _document, _added_page, _base_page
    original = _document([_base_page()])
    compare = _document([_base_page(), _added_page(2,'设备报价'), _added_page(3,'软件报价')])
    evidence = [EvidenceBox(page_no=p.page_no,bbox=BBox(x0=50,y0=80,x1=550,y1=600),
                text='设备条款',highlight_type='ADD',method='char_exact') for p in compare.pages[1:]]
    diff = DiffItem(diff_id='cross',source_type='clause',diff_type='ADD',compare_text='设备条款',compare_evidence=evidence)
    result = PageDiffConsolidator().consolidate(original,compare,[diff])
    assert result == [diff]
