"""Revision balloons must not become signing fields or distort party columns."""
import pytest
from app.models import BBox, Document, Page, TextBlock
from app.services.document_preparation import DocumentPreparer, DocumentPreparationResult
from app.services.signing_region.block_detector import SigningBlockDetector
from app.services.signing_region.extractor import SigningRegionExtractor
from app.services.signing_region.models import SigningBlock
from app.services.cover.comparator import _build_extra_text_diffs
from app.services.cover.types import CoverExtraction, CoverField


def block(text, x, y, w, key, role='text'):
    return TextBlock(block_id=key,page_no=1,text=text,bbox=BBox(x0=x,y0=y,x1=x+w,y1=y+12),
                     block_type='text',block_role=role,flow_role='body',source='native_pdf')


def document(blocks,width=772):
    return Document(filename='fixture.pdf',path='',page_count=1,pages=[Page(page_no=1,width=width,height=842,blocks=blocks)])


def signing_blocks():
    return [block('本页无正文，为合同签署页',90,60,300,'heading'),
            block('委托人：示例甲公司',90,125,180,'a'),block('承包人：示例乙公司',302,125,180,'b'),
            block('法定代表人：',90,270,120,'la'),block('法定代表人：',302,270,120,'lb'),
            block('地址：南宁市大坡',90,390,180,'aa'),block('地址：北京市建材城中路',302,390,202,'ba'),
            block('360号生活楼',90,414,180,'ac'),block('27号1幢2层227室',302,414,170,'bc'),
            block('账号：11111',90,600,140,'an'),block('账号：22222',302,600,140,'bn'),
            block('设置格式[Jy]:',540,390,52,'comment'),block('字体:（默认）宋体,字体颜',597,390,170,'payload'),
            block('色:黑色,英语(美国)',540,406,167,'wrap')]


def signing_region(blocks):
    return SigningBlock(block_id='SB',page_no=1,bbox=BBox(x0=70,y0=50,x1=772,y1=770),
                        confidence_reasons=['two_column_layout'],source_block_ids=[b.block_id for b in blocks])


def test_revision_same_row_and_wrap_are_owned_before_signing_detection():
    blocks=signing_blocks();d=document(blocks)
    DocumentPreparer()._classify_page_structure(d.pages[0],'original',DocumentPreparationResult())
    assert all(b.block_role=='revision_artifact' for b in blocks if b.block_id in {'comment','payload','wrap'})
    detected=SigningBlockDetector().detect(d)
    assert detected.blocks
    assert not {'comment','payload','wrap'}.intersection(i for sb in detected.blocks for i in sb.source_block_ids)
    regions=SigningRegionExtractor().extract_from_blocks(detected.blocks,d)
    fields=[e for r in regions for e in r.elements if e.element_type.value=='field']
    assert not any(e.raw_ref['field_key'].startswith('custom:字体') for e in fields)
    addr=next(e for e in fields if e.raw_ref['field_key']=='address' and e.raw_ref['party_role']=='乙方')
    assert addr.text=='北京市建材城中路27号1幢2层227室'


def test_revision_marked_block_is_rejected_even_with_stale_signing_sources():
    bs=signing_blocks()
    for b in bs:
        if b.block_id in {'comment','payload','wrap'}:b.block_role='revision_artifact'
    fields=SigningRegionExtractor()._field_elements(signing_region(bs),{b.block_id:b for b in bs})
    assert not any(e.raw_ref['field_key'] in {'custom:字体','custom:色'} for e in fields)


@pytest.mark.parametrize('roles',[('委托人','承包人'),('甲方','乙方')])
def test_party_heading_anchors_take_precedence_over_third_column(roles):
    bs=signing_blocks();bs[1].text=roles[0]+'：示例甲公司';bs[2].text=roles[1]+'：示例乙公司'
    fields=SigningRegionExtractor()._field_elements(signing_region(bs),{b.block_id:b for b in bs})
    account=next(e for e in fields if e.raw_ref['field_key']=='account' and e.text=='22222')
    assert account.raw_ref['party_role']=='乙方'


def test_address_cannot_take_a_far_left_continuation_even_without_columns():
    bs=signing_blocks();source=next(b for b in bs if b.block_id=='ba')
    tails=SigningRegionExtractor._field_continuation_blocks(source,source.bbox,None,bs,use_columns=False)
    assert [b.block_id for b in tails]==['bc']


def test_address_preserves_a_short_hanging_continuation():
    source = block('地址：平阳景苑B区第5幢720', 126, 169, 374, 'address')
    source.bbox.y1 = 183.5
    tail = block('号', 97.5, 194, 18.5, 'tail')
    tails = SigningRegionExtractor._field_continuation_blocks(
        source, source.bbox, None, [source, tail], use_columns=False)
    assert tails == [tail]


def test_authorized_representative_label_is_not_a_party_heading():
    bs = [block('甲方：示例甲公司', 60, 120, 180, 'a'),
          block('乙方：示例乙公司', 303, 120, 180, 'b')]
    region = signing_region(bs)
    expected = SigningRegionExtractor._two_column_midpoint(region, bs)
    bs.extend([block('法人代表或授权委托人：', 60, 260, 210, 'la'),
               block('法人代表或授权委托人：', 303, 260, 210, 'lb')])
    assert SigningRegionExtractor._two_column_midpoint(region, bs) == expected


def test_real_format_clause_and_unanchored_margin_field_are_not_revision():
    bs=[block('字体：宋体五号',90,390,200,'body'),block('字体：宋体五号',597,390,170,'unanchored')]
    d=document(bs);DocumentPreparer()._classify_page_structure(d.pages[0],'original',DocumentPreparationResult())
    assert all(b.block_role!='revision_artifact' for b in bs)


def cover_pair():
    anchors=[block('完整合同标题',110,240,370,'title'),block('承包人合同编号：',360,100,80,'id'),
             block('委托人：示例甲公司',98,600,400,'a'),block('承包人：示例乙公司',98,632,400,'b'),
             block('签订地点',98,663,110,'place')]
    logo=block('SPIC',130,96,56,'logo');logo.block_type='header'
    left=document([logo,*anchors]);right=left.model_copy(deep=True);right.pages[0].width=598
    for b in right.pages[0].blocks:
        b.bbox=BBox(x0=.86*b.bbox.x0+50,x1=.86*b.bbox.x1+50,y0=.86*b.bbox.y0+25,y1=.86*b.bbox.y1+25)
        b.block_type='text'
    return left,right


def extras(a,b):
    extraction=CoverExtraction(fields={'project_title':CoverField('project_title','标题','合同')})
    return _build_extra_text_diffs(a,b,extraction,extraction,1)


def test_equal_logo_is_aligned_by_other_cover_content_despite_page_margin():
    assert not extras(*cover_pair())


@pytest.mark.parametrize('change',['replace','new_position','no_context'])
def test_changed_or_unlocated_logo_is_still_reported(change):
    a,b=cover_pair()
    if change=='replace':b.pages[0].blocks[0].text='OTHER'
    if change=='new_position':b.pages[0].blocks[0].bbox.x0+=200;b.pages[0].blocks[0].bbox.x1+=200
    if change=='no_context':a.pages[0].blocks=a.pages[0].blocks[:1];b.pages[0].blocks=b.pages[0].blocks[:1]
    assert extras(a,b)
