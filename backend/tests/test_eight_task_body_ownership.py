"""Content preservation across cover boundaries and handwritten page footers."""
from dataclasses import replace

import pytest

from app.models import Page
from app.services.clause_splitter import ClauseSplitter
from app.services.document_preparation import DocumentPreparer
from app.services.header_footer_compare import HeaderFooterComparator
from tests.test_document_identity_regressions import block, document


def preamble(extra_cover=False, changed=False):
    d = document([block('服务协议',kind='doc_title')])
    if extra_cover:
        d.pages[0].blocks.append(block('AB-2026',y=180))
    d.pages.append(Page(page_no=2,width=600,height=800,blocks=[
        block('风功率预测技术服务协议',kind='paragraph_title',page=2,y=70),
        block('甲方：新能源有限公司',page=2,y=140),
        block('乙方：科技股份有限公司',page=2,y=175),
        block('鉴于甲方系依法注册的企业法人，需要以下技术服务：',page=2,y=235),
        block('每16分钟上报数据。' if changed else '每15分钟上报数据。',page=2,y=265),
        block('第一条 服务要求',page=2,y=310),
        block('乙方应提供系统维护服务。',page=2,y=340),
    ]))
    d.page_count=2
    return d


@pytest.mark.parametrize('changed',[False,True])
def test_cover_state_does_not_consume_preamble_and_real_change_survives(changed):
    a,b=preamble(),preamble(True,changed)
    DocumentPreparer().prepare_pair(a,b)
    texts=[''.join(c.normalized_text for c in ClauseSplitter().split(d,s)) for s,d in [('O',a),('C',b)]]
    assert all('鉴于甲方' in t for t in texts)
    assert '每15分钟' in texts[0]
    assert ('每16分钟' if changed else '每15分钟') in texts[1]


def test_mixed_multiline_ocr_block_cannot_be_discarded_as_cover_noise():
    d=document([block('甲方：某公司\n鉴于双方同意每15分钟上报数据。')])
    s=ClauseSplitter();u=s._collect_units(d)[0]
    assert not s._is_pre_body_noise(u,None)
    assert s._is_pre_body_noise(replace(u,text='甲方：某公司\n乙方：另一公司'),None)


def footer_document(with_annotations=True,changed=False):
    d=document([]);d.pages=[]
    for n in range(1,6):
        bs=[block('第一条 服务要求',page=n,y=140),
            block('联系人郭先生负责数据上报，每16分钟执行。' if changed else '联系人郭先生负责数据上报，每15分钟执行。',page=n,y=170),
            block('不得停止服务。',page=n,y=705),block(str(n),kind='number',page=n,y=720,x=280)]
        if with_annotations:
            b=block('郭',page=n,y=738 if n<5 else 710,x=320)
            b.bbox.x1=356;b.bbox.y1=b.bbox.y0+45
            bs.append(b)
        d.pages.append(Page(page_no=n,width=600,height=800,blocks=bs))
    d.page_count=5;return d


def test_repeated_footer_has_one_channel_and_keeps_equal_body_characters():
    a,b=footer_document(False),footer_document(True,True)
    DocumentPreparer().prepare_pair(a,b)
    cs=ClauseSplitter().split(b,'C')
    assert any('联系人郭先生' in c.text and '16分钟' in c.text for c in cs)
    assert any('不得停止服务' in c.text for c in cs)
    assert not any(c.text.strip()=='郭' or '\n郭' in c.text for c in cs)
    diffs=HeaderFooterComparator().build_diffs(a,b)
    assert any(d.compare_text=='郭' and len(d.compare_evidence)==5 for d in diffs)


def test_one_off_lower_body_does_not_become_footer():
    d=footer_document(False)
    d.pages[-1].blocks.append(block('郭',page=5,x=320,y=700))
    DocumentPreparer().prepare(d,'O')
    assert d.pages[-1].blocks[-1].enter_clause_compare is not False


def test_cover_extras_do_not_repeat_footer_evidence():
    from app.services.cover.extractor import _cover_blocks
    d=document([block('技术服务协议',kind='doc_title'),block('郭梁',y=735)])
    d.pages[0].blocks[-1].block_role='page_footer'
    assert all(b.text!='郭梁' for b in _cover_blocks(d))


@pytest.mark.parametrize('text,offset,expected',[
    ('1.1 使用IS0-9000系列认证书',0,True),
    ('1.1 使用IS0-9000系列认证书',100,False),
    ('1.1 使用ISO-9001系列认证书',0,False),
])
def test_native_title_recall_does_not_duplicate_one_physical_line(text,offset,expected):
    from app.services.native_heading_repair import NativeHeadingRepairService,NativeSectionTitleCandidate
    b=block(text,y=140)
    candidate=NativeSectionTitleCandidate(page_no=1,text='1.1 使用ISO-9000系列认证书',
                                         bbox=b.bbox.model_copy(update={'y0':140+offset,'y1':152+offset}),
                                         char_boxes=(),block_type='paragraph_title')
    assert NativeHeadingRepairService._has_same_physical_text(document([b]).pages[0],candidate)==expected


@pytest.mark.parametrize('project',['新能源项目','新能源改造项目'])
def test_inline_project_prefix_recovery_uses_only_same_side_source(project):
    from app.models import BBox,TextSourceEvidence
    suffix='光功率预测设备采购'
    b=block(suffix,x=240,y=110);b.layout_block_id='layout-row'
    full='项目名称：'+project+suffix
    b.source_evidence=[TextSourceEvidence(source='ppstructure',raw_text=full,page_no=1,
                      bbox=BBox(x0=70,y0=110,x1=b.bbox.x1,y1=122),confidence=1)]
    d=document([b]);DocumentPreparer().prepare(d,'C')
    assert b.text==full
    assert 'same_side_inline_layout_prefix' in b.source


def test_inline_layout_prefix_rejects_unrelated_geometry():
    from app.models import BBox,TextSourceEvidence
    b=block('光功率预测设备采购',x=240,y=110);b.layout_block_id='layout-row'
    b.source_evidence=[TextSourceEvidence(source='ppstructure',raw_text='项目名称：其他光功率预测设备采购',page_no=1,
                      bbox=BBox(x0=70,y0=20,x1=b.bbox.x1,y1=40),confidence=1)]
    DocumentPreparer().prepare(document([b]),'C')
    assert b.text=='光功率预测设备采购'


@pytest.mark.parametrize('same_page,changed,expected',[(True,True,1),(False,True,2),(True,False,2)])
def test_cover_field_dedup_requires_same_physical_changed_value(same_page,changed,expected):
    from app.models import BBox, DiffItem, EvidenceBox, TextRange
    from app.services.diff_quality import DiffQualityProcessor
    name='法定代表人:【张三】'
    extra=DiffItem(diff_id='M',diff_type='ADD',source_type='metadata',title='封面额外文本',
                   compare_text=name,compare_evidence=[EvidenceBox(page_no=2,bbox=BBox(x0=10,y0=10,x1=100,y1=20),text=name)])
    body=DiffItem(diff_id='C',diff_type='MODIFY',compare_text=name+'\n地址:新地址',
                 compare_change_ranges=[TextRange(start=7 if changed else 12,end=9 if changed else 16,highlight_type='ADD')],
                 compare_evidence=[EvidenceBox(page_no=2 if same_page else 3,bbox=BBox(x0=60,y0=10,x1=80,y1=20),text='张三')])
    decisions=[]
    result=DiffQualityProcessor()._dedupe_cover_fields_owned_by_body([extra,body],decisions)
    assert len(result)==expected
    assert body in result and '新地址' in body.compare_text
    if expected==1:
        assert decisions[0].detail['merged_diff_id']=='M'


def test_rightmost_address_characters_are_not_trimmed_as_edge_annotation():
    from app.models import BBox, DiffItem, EvidenceBox
    from app.services.diff_quality import DiffQualityProcessor
    value='地址:【山西省太原市小店区平阳路65号平阳景苑B区第5幢720\n号】'
    d=DiffItem(diff_id='C',diff_type='MODIFY',compare_text=value,
               compare_snippet=value.replace('\n',''),compare_evidence=[
                   EvidenceBox(page_no=2,bbox=BBox(x0=460,y0=240,x1=520,y1=257),
                               method='char_exact',text='幢720')])
    assert not DiffQualityProcessor()._trim_edge_annotation_from_mixed_diff(d)
    assert '幢720' in d.compare_snippet
