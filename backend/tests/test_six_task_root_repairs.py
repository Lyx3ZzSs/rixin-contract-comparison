"""Root invariants: ownership, physical evidence, and distinct audit identities."""
from types import SimpleNamespace

import fitz
import pytest

from app.models import BBox, CharBox, Document, Page, TextBlock, TextSourceEvidence
from app.models_table import StructuredTable, TableCell, TableRow
from app.services.clause_splitter import ClauseSplitter
from app.services.document_role_repair import (
    normalize_signing_lines, reconcile_local_source_disagreement, recover_native_technical_tokens,
)
from app.services.signing_region.block_detector import SigningBlockDetector
from app.services.signing_region.clause_document import SigningClauseDocumentBuilder
from app.services.table_compare import TableComparator
from app.utils.id_utils import next_diff_index


def block(key, text, y=100, x=60, width=None, kind='text'):
    b = TextBlock(block_id=key, text=text, page_no=1, block_type=kind,
                  bbox=BBox(x0=x, y0=y, x1=x+(width or len(text)*8), y1=y+14))
    b.char_boxes = [CharBox(char=c, page_no=1, text_index=i,
                    bbox=BBox(x0=x+i*8, y0=y, x1=x+(i+1)*8, y1=y+14)) for i,c in enumerate(text)]
    return b


def doc(blocks, path='missing.pdf'):
    return Document(filename='test.pdf', path=str(path), page_count=1,
                    pages=[Page(page_no=1, width=600, height=820, blocks=blocks)])


@pytest.mark.parametrize('intro', ['根据有关规定，双方签订本协议，并共同遵守以下条款。',
                                   '为遵守国家职业健康法律法规，双方约定本协议。'])
def test_shared_layout_bbox_cannot_pull_prose_into_signing(intro):
    blocks = [block('a','甲方：（盖章）',100), block('b','乙方：（盖章）',100,x=330),
              block('rep','代表：',130),block('rep-b','代表：',130,x=330),
              block('date','日期：',160),block('date-b','日期：',160,x=330),
              block('intro',intro,200),block('terms','第一条 乙方应在30日内完成全部工作并承担违约责任。',240)]
    # Layout groups the paragraph starting above the first physical OCR line.
    for b in blocks[6:]:
        b.layout_bbox = BBox(x0=50,y0=195,x1=570,y1=280)
    original = doc(blocks)
    detected = SigningBlockDetector().detect(original)
    assert detected.blocks and max(b.bbox.y1 for b in detected.blocks) <= 200
    for b in detected.blocks:
        assert not {'intro','terms'} & set(b.source_block_ids)
    content = SigningClauseDocumentBuilder().build(original,detected.blocks,detected.pages).document
    compare = content.model_copy(deep=True)
    compare.pages[0].blocks[-1].text = compare.pages[0].blocks[-1].text.replace('30','45')
    left, right = ClauseSplitter().split(content,'O'), ClauseSplitter().split(compare,'C')
    assert any('30日' in c.text for c in left)
    assert any('45日' in c.text for c in right)


def test_service_acceptance_form_is_compared_as_content():
    original = doc([block('title','服务验收单'),block('body','验收内容 完成情况 服务评价',200),
                    block('sign','客户验收签字/公章',600),block('date','签字日期',650)])
    result = SigningBlockDetector().detect(original)
    assert not result.blocks
    assert SigningClauseDocumentBuilder().build(original,result.blocks).document == original


def test_explicit_signing_page_keeps_middle_fields_and_both_parties():
    title=block('title','（本页无正文，为《服务合同》签署页）',60)
    physical = [block('a','委托人：甲公司',130),block('b','承包人：乙公司',130,x=320),
                block('rep-a','法定代表人或其委托代理人：',330),
                block('rep-b','法定代表人或其委托代理人：',330,x=320),
                block('sign-a','（签字/盖章）',360),block('sign-b','（签字/盖章）',360,x=320),
                block('acc-a','账号：1234567890',620),block('acc-b','账号：0987654321',620,x=320),
                block('date-a','签字日期：2026年7月10日',700),block('date-b','签字日期：年月日',700,x=320)]
    merged=block('merged','',120,kind='table')
    for b in physical:
        offset=len(merged.text)
        merged.char_boxes.extend(c.model_copy(update={'text_index':c.text_index+offset}) for c in b.char_boxes)
        merged.text+=b.text+'\n'
    merged.bbox=BBox(x0=60,y0=120,x1=580,y1=720)
    d=doc([title,merged]);normalize_signing_lines(d)
    text='\n'.join(b.text for b in d.pages[0].blocks)
    assert all(b.text in text for b in physical)
    assert all(not ('1234567890' in b.text and '0987654321' in b.text) for b in d.pages[0].blocks)
    result=SigningBlockDetector().detect(d)
    assert result.blocks and max(b.bbox.y1 for b in result.blocks)>=714


@pytest.mark.parametrize('change,own_agrees,repair', [('鹂',True,True),('鹂',False,False),('鹏',True,False)])
def test_address_glyph_requires_own_and_peer_corroboration(change,own_agrees,repair):
    text='通讯地址：江苏省南京市八卦洲街道鹏岛路254号'
    a,b=block('a',text),block('b',text.replace('鹏',change))
    for value,raw in [(a,text),(b,text if own_agrees else b.text)]:
        value.source_evidence=[TextSourceEvidence(source='ppstructure',raw_text=raw,page_no=1,bbox=value.bbox)]
    changed=reconcile_local_source_disagreement(doc([a]),doc([b]))
    assert bool(changed)==repair
    assert b.text==(text if repair else text.replace('鹏',change))


def test_address_number_change_is_never_rewritten_from_other_sources():
    a=block('a','通讯地址：江苏省南京市八卦洲街道鹏岛路254号')
    b=block('b',a.text.replace('254','255'))
    for value in [a,b]:value.source_evidence=[TextSourceEvidence(source='ppstructure',raw_text=a.text,page_no=1,bbox=value.bbox)]
    assert not reconcile_local_source_disagreement(doc([a]),doc([b]))
    assert '255' in b.text


@pytest.mark.parametrize('ocr,hidden,expected', [('0700 NWP.txt',False,'0700_NWP.txt'),
    ('0800 NWP.txt',False,'0800 NWP.txt'),('0700 NWP.txt',True,'0700 NWP.txt')])
def test_native_filename_repair_requires_visible_unchanged_content(tmp_path,ocr,hidden,expected):
    path=tmp_path/'token.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page(width=600,height=820)
        p.insert_text((60,120),'0700_NWP.txt',fontsize=12,render_mode=3 if hidden else 0)
        pdf.save(path)
    b=block('token',ocr,107)
    recover_native_technical_tokens(doc([b],path))
    assert b.text==expected


@pytest.mark.parametrize('amount,covered', [('4000',True),('5000',False)])
def test_missing_grid_row_needs_exact_physical_values(amount,covered):
    def source(key,price):
        cols=['1','国产操作系统','凝思','套','3',price,'12000']
        b=block(key,'',300,kind='table')
        for i,text in enumerate(cols):
            part=block(str(i),text,300,x=40+i*75)
            offset=len(b.text)
            b.char_boxes.extend(c.model_copy(update={'text_index':c.text_index+offset}) for c in part.char_boxes)
            b.text+=text+'\n'
        return b
    a,b=source('a','4000'),source('b',amount)
    # Broken HTML merges the description, brand and unit into a single cell.
    table=StructuredTable(page_no=1,source_block_id='a',col_count=2,rows=[TableRow(row_index=0,cells=[
        TableCell(row_index=0,col_index=0,text='1国产操作系统凝思套3'),TableCell(row_index=0,col_index=1,text='400012000')])])
    empty=StructuredTable(page_no=1,source_block_id='b',col_count=2,rows=[])
    builder=TableComparator()._diff_builder
    builder.source_blocks={'original':{'a':a},'compare':{'b':b}}
    diffs=builder.diff_cells(table,empty,a,b)
    assert (not diffs)==covered


def test_filtered_diff_ids_do_not_overwrite_signature_with_clause():
    existing=[SimpleNamespace(diff_id=value) for value in ['D001','D014','WHOLE-PAGE-VIS-6-S1']]
    assert next_diff_index(existing)==15
    assert next_diff_index([])==1


def test_native_filename_from_another_column_cannot_rewrite_field(tmp_path):
    path=tmp_path/'columns.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page(width=600,height=820)
        p.insert_text((350,120),'0700_NWP.txt',fontsize=12)
        pdf.save(path)
    b=block('token','0700 NWP.txt',107,x=60)
    assert not recover_native_technical_tokens(doc([b],path))
    assert b.text=='0700 NWP.txt'


def test_closing_marker_role_does_not_depend_on_page_merge():
    from app.services.clause_paragraphs import ParagraphBuilder
    from app.services.clause_splitter import ClauseUnit
    from app.models import EvidenceBox
    def unit(text,page,y):
        bbox=BBox(x0=60,y0=y,x1=450,y1=y+14)
        return ClauseUnit(text=text,char_boxes=[],page_no=page,block_id=text,
            block_type='text',bbox=bbox,evidence=EvidenceBox(page_no=page,bbox=bbox))
    builder=ParagraphBuilder()
    for previous in [unit('本特别约定以双方约定为准。',1,720),unit('本特别约定以双方约定为准。',2,70)]:
        marker=unit('（以下无正文）',2,120)
        result=builder.build([previous,marker],parse_marker=lambda text:None)
        assert result[-1].section_type=='signature'
        assert result[-1].text==marker.text


def test_missing_party_colon_does_not_leave_signature_fields_in_body():
    d=doc([block('a','甲方',100),block('b','乙方：（盖章）',100,x=360),
           block('ra','代表：',130),block('rb','代表：',130,x=360),
           block('da','日期：',160),block('db','日期：',160,x=360),
           block('body','根据有关规定，双方应当履行以下合同义务并承担责任。',200)])
    found=SigningBlockDetector().detect(d)
    assert found.blocks and all(b.exclude_from_clause_diff for b in found.blocks)
    content=SigningClauseDocumentBuilder().build(d,found.blocks,found.pages).document
    assert [b.block_id for b in content.pages[0].blocks]==['body']


def test_bare_party_labels_alone_are_not_a_signing_region():
    d=doc([block('a','甲方',100),block('b','乙方',100,x=330)])
    assert not SigningBlockDetector().detect(d).blocks
