"""Structure invariants: moving a form cannot create field additions/deletions."""
import json
from pathlib import Path

import pytest

from app.models import BBox, Document, Page, TextBlock
from app.services.signing_region.block_detector import SigningBlockDetector
from app.services.signing_region.clause_document import SigningClauseDocumentBuilder
from app.services.signing_region.extractor import SigningRegionExtractor
from app.services.signing_region.matcher import SigningRegionMatcher
from app.services.signing_region.models import SigningElement, SigningElementType, SigningRegion


def block(id, text, x, y, page=1):
    return TextBlock(block_id=id, text=text, page_no=page, bbox=BBox(x0=x, y0=y, x1=x+160, y1=y+18))


def document(pages):
    return Document(filename='test.pdf', path='test.pdf', page_count=len(pages), pages=pages)


@pytest.mark.parametrize('offset', [0, 110, 200])
def test_local_form_moves_without_consuming_prose(offset):
    page = Page(page_no=1, width=600, height=850, blocks=[
        block('before', '双方应当履行廉洁义务，违约责任按本条处理。', 70, 50),
        block('a', '甲方：', 70, 210+offset), block('b', '乙方：', 340, 210+offset),
        block('ar', '法定代表人（签字或盖章）：', 70, 250+offset),
        block('br', '法定代表人（签字或盖章）：', 340, 250+offset),
        block('ad', '年月日', 70, 290+offset), block('bd', '年月日', 340, 290+offset),
        block('after', '第一条甲方应当支付合同价款人民币壹万元。', 70, 340+offset),
    ])
    doc = document([page])
    detected = SigningBlockDetector().detect(doc)
    assert len(detected.blocks) == 1
    assert {'a','b','ar','br','ad','bd'} == set(detected.blocks[0].source_block_ids)
    remaining = SigningClauseDocumentBuilder().build(doc, detected.blocks, detected.pages).document
    assert {b.block_id for b in remaining.pages[0].blocks} == {'before','after'}


def test_execution_sentence_is_not_a_signature_slot():
    page = Page(page_no=1, width=600, height=850, blocks=[
        block('prose', '本合同一式肆份，由甲方：项目公司与乙方：供货单位经法定代表人签字盖章后生效。',70,400),
    ])
    assert SigningBlockDetector().detect(document([page])).blocks == []


def continuation_document(boundary=False):
    page1 = Page(page_no=1, width=600, height=850, blocks=[
        block('a','甲方：',70,640), block('b','乙方：供货有限公司',340,640),
        block('ar','法定代表人（签字盖章）：',70,685),block('br','法定代表人（签字盖章）：',340,685),
        block('ap','电话：',70,735),block('bp','电话：01012345678',340,735),
    ])
    blocks = [block('new','附件一银行信息说明',70,40,2)] if boundary else []
    blocks += [block('bank-a','开户银行：',70,80,2),block('bank-b','开户银行：招商银行支行',340,80,2),
               block('acct-a','账号：',70,120,2),block('acct-b','账号：12345678',340,120,2),
               block('tax-a','税号：',70,160,2),block('tax-b','税号：9876543210',340,160,2)]
    return document([page1,Page(page_no=2,width=600,height=850,blocks=blocks)])


def test_business_continuation_keeps_source_pages_and_values():
    doc = continuation_document()
    result = SigningBlockDetector().detect(doc)
    assert len(result.blocks) == 2
    assert result.blocks[0].group_id == result.blocks[1].group_id != ''
    regions = SigningRegionExtractor().extract_from_blocks(result.blocks,doc)
    joined = SigningRegionMatcher._join_continuation_groups(regions)
    assert len(joined) == 1
    accounts = [e for e in joined[0].elements if e.raw_ref.get('field_key') == 'account']
    assert any(e.text == '12345678' and e.page_no == 2 for e in accounts)
    assert any(e.text == '' and e.raw_ref.get('party_role') == '甲方' for e in accounts)
    # Joining the structure never normalizes or copies a changed account value.
    changed = regions[-1].model_copy(deep=True)
    next(e for e in changed.elements if e.text == '12345678').text = '12345679'
    joined_changed = SigningRegionMatcher._join_continuation_groups([regions[0],changed])
    assert any(e.text == '12345679' for e in joined_changed[0].elements)


def test_new_attachment_prevents_cross_page_grouping():
    result = SigningBlockDetector().detect(continuation_document(boundary=True))
    assert all(not b.group_id for b in result.blocks)
    assert not any('cross_page_business_continuation' in b.confidence_reasons for b in result.blocks)


def test_reordered_three_party_signature_page_matches_changed_company():
    bbox = BBox(x0=80,y0=80,x1=500,y1=600)
    def region(id,page,text):
        return SigningRegion(region_id=id,page_no=page,bbox=bbox,confidence=0.6,elements=[
            SigningElement(element_id=id,element_type=SigningElementType.SIGNING_TABLE,page_no=page,bbox=bbox,text=text)])
    original = region('o',55,'技术协议签字页\n买方：原买方公司\n卖方：\n设计方：\n电话：\n地址：')
    compare = region('c',54,'签字页\n设计方：设计公司\n买方：新买方公司\n卖方：供货公司\n电话：')
    pairs = SigningRegionMatcher().match([original],[compare])
    assert len(pairs)==1 and pairs[0][0] is original and pairs[0][1] is compare
    assert '原买方公司' in pairs[0][0].elements[0].text
    assert '新买方公司' in pairs[0][1].elements[0].text


def persisted_document(prefix, side):
    root = Path(__file__).resolve().parents[2] / 'storage/tasks'
    paths = list(root.glob(prefix+'*/diagnostics/ocr/'+side+'*hybrid*'))
    if not paths:
        pytest.skip('persisted OCR audit corpus is not installed')
    return Document.model_validate(json.loads(paths[0].read_text())['document'])


@pytest.mark.parametrize('side',['original','compare'])
def test_persisted_middle_signing_form_preserves_clean_prose(side):
    doc=persisted_document('1c341117',side)
    result=SigningBlockDetector().detect(doc)
    signing=[b for b in result.blocks if b.page_no==19]
    assert len(signing)==1 and signing[0].exclude_from_clause_diff
    assert 'p19_ppocrv5_b11' in signing[0].source_block_ids
    assert not {'p19_ppocrv5_b8','p19_ppocrv5_b9','p19_ppocrv5_b10'}.intersection(signing[0].source_block_ids)


@pytest.mark.parametrize('title', ['（本页无正文，为《技术服务合同》签署页）', '(本页无正文，为《技术服务合同》签署页)', '技术协议签字页'])
def test_signature_title_parentheses_do_not_disable_form_continuation(title):
    page = Page(page_no=1, width=600, height=850, blocks=[block('title',title,70,70)])
    assert SigningBlockDetector._explicit_signing_page(page)


def test_historical_parenthesized_signature_page_keeps_business_fields_owned():
    doc = persisted_document('d84c2f1b','compare')
    result = SigningBlockDetector().detect(doc)
    blocks = [b for b in result.blocks if b.page_no==6]
    assert len(blocks)==1 and blocks[0].exclude_from_clause_diff
    assert {'p6_ppocrv5_b1','p6_ppocrv5_b9','p6_ppocrv5_b14'} <= set(blocks[0].source_block_ids)


@pytest.mark.parametrize('prefix,side,pages', [
    ('50eaf632','original',{29}), ('50eaf632','compare',{29}), ('636c3588','original',{38,40}),
])
def test_bare_date_labels_do_not_promote_old_business_forms(prefix,side,pages):
    result = SigningBlockDetector().detect(persisted_document(prefix,side))
    assert not any(b.page_no in pages for b in result.blocks)


def test_local_mixed_date_value_and_next_column_label_are_owned():
    doc=persisted_document('1c341117','compare')
    result=SigningBlockDetector().detect(doc)
    blocks=[b for b in result.blocks if b.page_no==20]
    assert any('p20_ppocrv5_b10' in b.source_block_ids for b in blocks)
    builder=SigningClauseDocumentBuilder().build(doc,result.blocks,result.pages)
    assert 'p20_ppocrv5_b10' in builder.excluded_block_ids


def test_mixed_date_line_does_not_become_global_signing_anchor():
    b=block('date','2026.6.11日期：',70,300)
    page=Page(page_no=1,width=600,height=850,blocks=[b])
    assert SigningBlockDetector().detect(document([page])).blocks==[]
