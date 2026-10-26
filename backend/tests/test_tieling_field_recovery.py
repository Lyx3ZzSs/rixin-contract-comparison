import json
from pathlib import Path

import fitz
import pytest
from app.models import BBox, Document, Page, TextBlock
from app.services.signing_region.models import SigningRegion
from app.services.signing_region.value_ocr import SigningFieldValueOCR
from app.services.signing_region.comparator import SigningRegionComparator

NAME = '陕西派能中和电力科技有限公司'
CODE = '91610103MABOHNNLOU'
MISREAD = '91610103MAB0HNNLOU'


def case(tmp_path):
    d = json.loads((Path(__file__).parent/'fixtures/tieling_signing_fields.json').read_text())
    a,b=SigningRegion.model_validate(d['original']),SigningRegion.model_validate(d['compare'])
    path=tmp_path/'document.pdf'
    with fitz.open() as pdf:
        for _ in range(3):pdf.new_page(width=603,height=824)
        pdf.save(path)
    document=Document(filename=path.name,path=str(path),page_count=3,pages=[Page.model_validate(d['compare_page'])])
    return a,b,document


def field(region,key):
    return next(e for e in region.elements if e.raw_ref.get('party_role')=='甲方' and e.raw_ref.get('field_key')==key)


class OCR:
    def __init__(self, readings, confidence=.99):
        self.readings=iter(readings);self.calls=0;self.confidence=confidence
    def predict(self,path,file_type=1):
        self.calls+=1
        return next(self.readings)
    def payload_to_document(self,texts,path):
        return Document(filename='roi.png',path=str(path),page_count=1,pages=[Page(page_no=1,width=1000,height=250,
            blocks=[TextBlock(block_id=str(i),page_no=1,text=t,bbox=BBox(x0=20,y0=20+90*i,x1=900 if i==0 else 60,y1=80+90*i),confidence=self.confidence) for i,t in enumerate(texts)])])


@pytest.mark.parametrize('name',[NAME,'陕西派能中和新能源有限公司'])
def test_truncated_party_recovers_from_own_full_column_and_preserves_true_change(tmp_path,name):
    a,b,doc=case(tmp_path);ocr=OCR([[f'甲方：{name[:-1]}','司']]*2)
    service=SigningFieldValueOCR(extractor=ocr)
    result=service.enrich_occluded_fields(doc.path,[b],source_document=doc)
    assert result[0]['status']=='RECOVERED'
    party=field(b,'party_name')
    assert name in party.text
    roi=party.raw_ref['value_roi']
    assert roi['x0']<=62 and 290<=roi['x1']<297 and roi['y1']>=239
    changes=SigningRegionComparator().compare(a,b).party_changes
    assert bool(changes)==(name!=NAME)


def test_neighbor_party_label_cannot_become_the_recovered_company(tmp_path):
    a,b,doc=case(tmp_path);before=field(b,'party_name').text
    service=SigningFieldValueOCR(extractor=OCR([['乙方：陕西派能中和电力科技有限公司']]*2))
    service.enrich_occluded_fields(doc.path,[b],source_document=doc)
    assert field(b,'party_name').text==before


def test_equal_short_readings_are_incomplete_not_disagreement(tmp_path):
    a,b,doc=case(tmp_path);party=field(b,'party_name')
    with fitz.open(doc.path) as pdf:
        result=SigningFieldValueOCR(extractor=OCR([['陕西派']]*2))._recover_occluded_printed_field(pdf,party)
    assert result['status']=='INCOMPLETE_PARTY_NAME'


@pytest.mark.parametrize('reading,expected',[(CODE,CODE),(MISREAD,MISREAD)])
def test_identifier_uses_own_readings_not_peer_value(tmp_path,reading,expected):
    a,b,doc=case(tmp_path);service=SigningFieldValueOCR(extractor=OCR([[reading]]*2))
    result=service.enrich_disputed_identifiers(doc.path,b,a)
    assert result[0]['status']=='RECOVERED'
    assert field(b,'credit_code').text==expected
    changes=SigningRegionComparator().compare(a,b).field_changes
    assert any(c.get('field_key')=='credit_code' for c in changes)==(expected!=CODE)
    assert len(field(b,'credit_code').raw_ref['value_evidence'])==2


@pytest.mark.parametrize('readings,confidence', [([[CODE],[MISREAD]],.99),([[CODE]]*2,.6),([['91610103MAB8HNNLOU']]*2,.99)])
def test_uncertain_or_unrelated_identifier_readings_do_not_overwrite(tmp_path,readings,confidence):
    a,b,doc=case(tmp_path);service=SigningFieldValueOCR(extractor=OCR(readings,confidence))
    service.enrich_disputed_identifiers(doc.path,b,a)
    assert field(b,'credit_code').text==MISREAD


def test_native_identifier_is_not_overwritten_by_ocr(tmp_path):
    a,b,doc=case(tmp_path);ocr=OCR([])
    assert SigningFieldValueOCR(extractor=ocr).enrich_disputed_identifiers(doc.path,a,b)==[]
    assert ocr.calls==0 and field(a,'credit_code').text==CODE


def test_real_nonambiguous_identifier_change_does_not_trigger_reread(tmp_path):
    a,b,doc=case(tmp_path);field(b,'credit_code').text='91610103MAB8HNNLOU';ocr=OCR([])
    assert SigningFieldValueOCR(extractor=ocr).enrich_disputed_identifiers(doc.path,b,a)==[]
    assert ocr.calls==0
