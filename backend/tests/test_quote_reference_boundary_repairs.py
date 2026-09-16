import pytest

from app.config_models import DocumentUnderstandingSettings
from app.models import BBox, Clause, Document, Page, TextBlock
from app.services.clause_splitter import ClauseSplitter
from app.services.diff_engine import DiffEngine
from app.services.document_preparation import DocumentPreparer
from app.services.document_understanding import DocumentUnderstandingService
from app.services.matcher import ClauseMatcher
from app.services.normalizer import TextNormalizer


@pytest.mark.parametrize('text',[
    '本合同正本一式肆份，甲乙双方各执贰份。合同附页、报价单、',
    '乙方应提交报价表', '付款条件详见报价清单', '服务范围包括报价单',
])
def test_body_quote_reference_is_not_a_quote_title(text):
    assert not DocumentUnderstandingService.is_quote_title_text(text)
    block=TextBlock(block_id='body',page_no=2,text=text,bbox=BBox(x0=50,y0=100,x1=500,y1=130))
    assert DocumentPreparer()._section_role(block,text) is None


@pytest.mark.parametrize('text',['报价单','附件一：分项报价表','功率预测服务报价清单','报价表格式','报价明细（单位：元）','技术需求报价表'])
def test_actual_quote_title_still_establishes_section(text):
    assert DocumentUnderstandingService.is_quote_title_text(text)


def test_quote_reference_stays_in_main_body_across_page_break():
    pages=[]
    for page_no,text in [(2,'本合同正本一式肆份，甲乙双方各执贰份。合同附页、报价单、'),(3,'订货单、收货验收单与合同正本具同等法律效力。')]:
        pages.append(Page(page_no=page_no,width=600,height=850,blocks=[TextBlock(block_id=f'b{page_no}',page_no=page_no,text=text,bbox=BBox(x0=50,y0=100,x1=500,y1=130))]))
    doc=Document(filename='test.pdf',path='test.pdf',page_count=2,pages=pages)
    DocumentUnderstandingService(DocumentUnderstandingSettings()).understand(doc,'original')
    DocumentPreparer().prepare(doc,'original')
    clauses=ClauseSplitter().split(doc,'O')
    assert clauses and all(c.section_type=='main_contract' for c in clauses)
    assert '报价单' in ''.join(c.text for c in clauses)


def clause(id,text,no='',title=''):
    normalizer=TextNormalizer()
    return Clause(clause_id=id,clause_no=no,title=title,text=text,normalized_text=normalizer.normalize_for_diff(text),match_text=normalizer.normalize_for_match(text),page_numbers=[6])


BODY='本合同正本一式肆份,甲乙双方各执贰份。合同附页、报价单、订货单、收货验收单与合同正本具同等法律效力。'


def test_short_heading_surplus_exact_containment_preserves_renumbering():
    original=[clause('O1','十一、其它','十一','其它'),clause('O2',BODY)]
    compare=[clause('N1','十二、其它\n'+BODY,'十二','其它')]
    pairs=ClauseMatcher().match(original,compare)
    body=next(pair for pair in pairs if pair.original and pair.original.clause_id=='O2')
    assert body.compare is not None and body.compare.text==BODY
    diffs=DiffEngine().build_diffs(pairs)
    assert len(diffs)==1
    assert diffs[0].diff_type=='MODIFY'
    assert diffs[0].original_text=='十一、其它'
    assert diffs[0].compare_text=='十二、其它'


def test_real_body_change_is_not_consumed_as_exact_containment():
    original=[clause('O1','十一、其它','十一','其它'),clause('O2',BODY)]
    changed=BODY.replace('一式肆份','一式陆份')
    compare=[clause('N1','十二、其它\n'+changed,'十二','其它')]
    diffs=DiffEngine().build_diffs(ClauseMatcher().match(original,compare))
    assert any('陆份' in diff.compare_text for diff in diffs)
    assert any('肆份' in diff.original_text for diff in diffs)


def test_exact_containment_allows_short_heading_but_fuzzy_does_not():
    matcher=ClauseMatcher()
    assert matcher._find_contained_span(BODY,'十二、其它\n'+BODY) is not None
    assert matcher._find_contained_span(BODY,'十二、其它\n'+BODY.replace('肆份','陆份')) is None


def test_persisted_contract_keeps_real_number_change_without_body_add_delete():
    import json
    from pathlib import Path
    from app.services.signing_region.block_detector import SigningBlockDetector
    from app.services.signing_region.clause_document import SigningClauseDocumentBuilder
    root=Path(__file__).resolve().parents[2]/'storage/tasks'
    paths=list(root.glob('396e58a1*/diagnostics/ocr/original*hybrid*.json'))
    if not paths:
        pytest.skip('persisted OCR audit corpus is not installed')
    docs=[]
    for side in ['original','compare']:
        path=next(paths[0].parent.glob(side+'*hybrid*.json'))
        docs.append(Document.model_validate(json.loads(path.read_text())['document']))
    # The numbering differs in both independent OCR sources on each side.
    for doc,number in zip(docs,['十一','十二']):
        heading=next(b for b in doc.pages[5].blocks if '其它' in b.text)
        assert len(heading.source_evidence)>=2
        assert all(number in item.raw_text for item in heading.source_evidence)
    DocumentPreparer().prepare_pair(*docs)
    DocumentUnderstandingService(DocumentUnderstandingSettings()).understand_pair(*docs)
    clauses=[]
    for side,doc in zip(['O','N'],docs):
        result=SigningBlockDetector().detect(doc)
        reduced=SigningClauseDocumentBuilder().build(doc,result.blocks,result.pages).document
        clauses.append(ClauseSplitter().split(reduced,side))
    diffs=DiffEngine().build_diffs(ClauseMatcher().match(*clauses))
    relevant=[d for d in diffs if '本合同正本' in d.original_text+d.compare_text]
    assert len(relevant)==1 and relevant[0].diff_type=='MODIFY'
    diff=relevant[0]
    original_changed=''.join(diff.original_text[r.start:r.end] for r in diff.original_change_ranges)
    compare_changed=''.join(diff.compare_text[r.start:r.end] for r in diff.compare_change_ranges)
    assert original_changed=='一' and compare_changed=='二'


def test_line_reflow_does_not_hide_real_word_order_or_negation_changes():
    from app.services.diff.range_refiner import changed_snippets
    left='十一、其它\n甲方不得拒绝支付货款，乙方应按期供货。'
    for right in ['十二、其它\n甲方可以拒绝支付货款，乙方应按期供货。',
                  '十二、其它\n乙方应按期供货，甲方不得拒绝支付货款。']:
        _,_,left_ranges,right_ranges=changed_snippets(left,right)
        assert any(r.end>len('十一、其它') for r in left_ranges+right_ranges)


def test_boundary_coverage_cannot_erase_changed_heading_number_found_elsewhere():
    from app.models import DiffItem
    from app.services.diff.boundary_coverage import ClauseBoundaryCoverageFilter, BoundaryCoverageContext
    original=clause('O','十一、其它\n'+BODY,'十一','其它')
    compare=clause('N','十二、其它\n'+BODY,'十二','其它')
    diff=DiffEngine().build_diffs(ClauseMatcher().match([original],[compare]))[0]
    diff.review_flags.append('LOW_CONFIDENCE_MATCH')
    def doc(text):
        return Document(filename='test.pdf',path='',page_count=1,pages=[Page(page_no=6,width=600,height=850,blocks=[TextBlock(block_id='b',page_no=6,text=text+'\n一 二',bbox=BBox(x0=50,y0=100,x1=500,y1=130))])])
    ctx=BoundaryCoverageContext(original_clauses=[original],compare_clauses=[compare],original_document=doc(original.text),compare_document=doc(compare.text))
    retained,decisions=ClauseBoundaryCoverageFilter().filter([diff],ctx)
    assert retained==[diff]
    assert not any(d.action.startswith('suppressed') for d in decisions)
