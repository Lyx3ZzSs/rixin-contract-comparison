import fitz
import pytest

from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.document_role_repair import reconcile_contact_placeholders


def documents(tmp_path, glyph='slash', recognized='L'):
    path = tmp_path/'scan.pdf'
    pdf = fitz.open(); page = pdf.new_page(width=300,height=300)
    blocks=[]
    for i,label in enumerate(['传真号码','邮箱地址']):
        y=40+i*25
        if glyph == 'slash':
            page.draw_line((78,y+1),(73,y+11),width=.8)
            page.draw_line((73,y+11),(78,y+11),width=.8)
        elif glyph == 'L':
            page.draw_line((73,y+1),(73,y+11),width=.8)
            page.draw_line((73,y+11),(78,y+11),width=.8)
        elif glyph == 'backslash':
            page.draw_line((73,y+1),(78,y+11),width=.8)
        elif glyph == '7':
            page.draw_line((72,y+1),(79,y+1),width=.8)
            page.draw_line((79,y+1),(73,y+11),width=.8)
        elif glyph == 'underline':
            page.draw_line((73,y+11),(83,y+11),width=.8)
        text=label+'：'+recognized
        chars=[CharBox(char=c,page_no=1,text_index=j,bbox=BBox(x0=20+j*10,y0=y,x1=30+j*10,y1=y+14)) for j,c in enumerate(text)]
        chars[4].bbox=BBox(x0=65,y0=y,x1=68,y1=y+14)
        blocks.append(TextBlock(block_id=str(i),page_no=1,text=text,bbox=BBox(x0=20,y0=y,x1=85,y1=y+14),source='PP-OCRv6',char_boxes=chars))
    pdf.save(path);pdf.close()
    owner=TextBlock(block_id='owner',page_no=1,text='委托人：示例公司',bbox=BBox(x0=20,y0=10,x1=130,y1=24))
    target=Document(filename='scan.pdf',path=str(path),page_count=1,pages=[Page(page_no=1,width=300,height=300,blocks=[owner,*blocks])])
    original=target.model_copy(deep=True)
    for b in original.pages[0].blocks[1:]:
        b.text=b.text[:5]+'/';b.source='native_pdf'
    return original,target


@pytest.mark.parametrize('recognized',['L',''])
def test_restore_only_visually_verified_placeholder(tmp_path,recognized):
    a,b=documents(tmp_path,recognized=recognized)
    result=reconcile_contact_placeholders(a,b)
    assert [x.text for x in b.pages[0].blocks[1:]]==['传真号码：/','邮箱地址：/']
    assert len(result)==2
    assert all(x.source_evidence for x in b.pages[0].blocks[1:])


@pytest.mark.parametrize('glyph',['blank','L','backslash','7','underline'])
def test_do_not_invent_slash_or_delete_real_glyph(tmp_path,glyph):
    a,b=documents(tmp_path,glyph=glyph)
    before=b.model_dump()
    assert reconcile_contact_placeholders(a,b)==[]
    assert b.model_dump()==before


def test_no_repair_without_matching_party_or_pdf(tmp_path):
    a,b=documents(tmp_path)
    a.pages[0].blocks[0].text='委托人：另一公司'
    assert reconcile_contact_placeholders(a,b)==[]
    a.pages[0].blocks[0].text=b.pages[0].blocks[0].text
    b.path=str(tmp_path/'missing.pdf')
    assert reconcile_contact_placeholders(a,b)==[]


def test_valid_contact_values_and_unrelated_letters_are_untouched(tmp_path):
    a,b=documents(tmp_path)
    b.pages[0].blocks[1].text='传真号码：123456'
    b.pages[0].blocks[2].text='邮箱地址：L@example.com'
    before=b.model_dump()
    assert reconcile_contact_placeholders(a,b)==[]
    assert b.model_dump()==before


def test_repair_is_idempotent_and_keeps_source_coordinates(tmp_path):
    a,b=documents(tmp_path)
    label=b.pages[0].blocks[1].char_boxes[:5]
    assert len(reconcile_contact_placeholders(a,b))==2
    assert b.pages[0].blocks[1].char_boxes[:5]==label
    assert b.pages[0].blocks[1].char_boxes[-1].char=='/'
    assert b.pages[0].blocks[1].source_evidence[0].raw_text=='传真号码：L'
    assert reconcile_contact_placeholders(a,b)==[]
