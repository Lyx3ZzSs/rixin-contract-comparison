from types import SimpleNamespace

import fitz
import pytest

from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.payment_symbol_ocr import reconcile_payment_symbols


def document(tmp_path, name, text, *, slash_ink=True, rule=True, source="PP-OCRv6"):
    path = tmp_path / f"{name}.pdf"
    chars = []
    x = 30
    for index, c in enumerate(text):
        if c == "个" and "/" not in text[:index]:
            x += 20
        chars.append(CharBox(char=c, text_index=index, page_no=1,
                            bbox=BBox(x0=x, y0=100, x1=x+10, y1=120)))
        x += 10
    with fitz.open() as pdf:
        p = pdf.new_page(width=600, height=800)
        if "后个" in text:
            left = next(c.bbox.x1 for c in chars if c.char == "后")
            if rule:
                p.draw_line((left, 119), (left+20, 119), width=1)
            if slash_ink:
                p.draw_line((left+8, 118), (left+12, 102), width=1)
        pdf.save(path)
    block = TextBlock(block_id=name, page_no=1, text=text, source=source,
                      bbox=BBox(x0=30,y0=100,x1=x,y1=120),char_boxes=chars)
    return Document(filename=path.name,path=str(path),page_count=1,
                    pages=[Page(page_no=1,width=600,height=800,blocks=[block])])


class Reader:
    def __init__(self, texts, confidence=.97):
        self.texts = iter(texts)
        self.confidence = confidence
        self.calls = 0

    def predict(self, path, file_type):
        self.calls += 1
        return next(self.texts)

    def payload_to_document(self, text, path):
        block = TextBlock(block_id="roi",page_no=1,text=text,confidence=self.confidence,
                          bbox=BBox(x0=0,y0=0,x1=len(text)*10,y1=20),
                          char_boxes=[CharBox(char=c,text_index=i,page_no=1,
                                      bbox=BBox(x0=i*10,y0=0,x1=(i+1)*10,y1=20)) for i,c in enumerate(text)])
        return SimpleNamespace(pages=[SimpleNamespace(width=len(text)*10,height=20,blocks=[block])])


def pair(tmp_path, kind, **kwargs):
    a, b = (("进度款：¥/元（合同总金额的/%）", "进度款：Y/元（合同总金额的/%）") if kind=="currency"
            else ("方开具的等额增值税专用发票后/个工作日内支付", "方开具的等额增值税专用发票后个工作日内支付"))
    return document(tmp_path,"o",a,source="native_pdf"),document(tmp_path,"c",b,**kwargs)


@pytest.mark.parametrize("kind,text", [("currency","款：￥/元"),("slash","票后/个工作")])
def test_own_two_scale_readings_recover_symbols_with_character_geometry(tmp_path, kind, text):
    a,b=pair(tmp_path,kind);reader=Reader([text,text])
    changes=reconcile_payment_symbols(a,b,reader)
    assert [(s,q.block_id) for s,q in changes]==[("compare","c")]
    q=b.pages[0].blocks[0]
    assert q.text==a.pages[0].blocks[0].text
    assert len(q.char_boxes)==len(q.text)
    assert all(q.text[c.text_index]==c.char and c.page_no==1 for c in q.char_boxes)
    assert sum(e.source=="payment_symbol_roi_ppocr" for e in q.source_evidence)==2
    assert reader.calls==2


@pytest.mark.parametrize("texts", [
    ["款：Y/元","款：Y/元"], # A real Y must remain a difference.
    ["款：￥/元","款：Y/元"],
    ["款：￥1元","款：￥1元"], # Cannot overwrite surrounding digits.
])
def test_currency_requires_consensus_and_exact_unchanged_context(tmp_path,texts):
    a,b=pair(tmp_path,"currency");before=b.model_dump()
    assert reconcile_payment_symbols(a,b,Reader(texts))==[]
    assert b.model_dump()==before


@pytest.mark.parametrize("texts", [
    ["票后个工作","票后个工作"],
    ["票后1个工作","票后1个工作"],
    ["票后7个工作","票后7个工作"],
    ["票后/个工作","票后个工作"],
    ["票后\\个工作","票后\\个工作"],
])
def test_peer_slash_cannot_overwrite_absent_or_different_own_reading(tmp_path,texts):
    a,b=pair(tmp_path,"slash");before=b.model_dump()
    assert reconcile_payment_symbols(a,b,Reader(texts))==[]
    assert b.model_dump()==before


@pytest.mark.parametrize("kwargs", [{"slash_ink":False},{"rule":False}])
def test_no_insertion_into_blank_underline_or_unqualified_gap(tmp_path,kwargs):
    a,b=pair(tmp_path,"slash",**kwargs);reader=Reader(["票后/个工作"]*2)
    assert reconcile_payment_symbols(a,b,reader)==[]
    assert reader.calls==0


@pytest.mark.parametrize("confidence,texts", [(.6,["款：￥/元"]*2),(.97,[])])
def test_low_confidence_and_unavailable_ocr_leave_source_unchanged(tmp_path,confidence,texts):
    a,b=pair(tmp_path,"currency");before=b.model_dump()
    assert reconcile_payment_symbols(a,b,Reader(texts,confidence))==[]
    assert b.model_dump()==before


def test_real_numeric_difference_and_ambiguous_peer_do_not_trigger_recovery(tmp_path):
    a,b=pair(tmp_path,"currency");a.pages[0].blocks[0].text=a.pages[0].blocks[0].text.replace("/%","30%")
    reader=Reader([])
    assert reconcile_payment_symbols(a,b,reader)==[]
    a,b=pair(tmp_path,"currency");a.pages[0].blocks.append(a.pages[0].blocks[0].model_copy(deep=True))
    assert reconcile_payment_symbols(a,b,reader)==[]
    assert reader.calls==0
