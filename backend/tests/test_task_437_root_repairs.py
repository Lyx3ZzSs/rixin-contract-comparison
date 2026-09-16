from app.models import BBox, Document, Page, TextBlock
from app.services.native_heading_repair import NativeHeadingRepairService, NativeSectionTitleCandidate
from app.services.cover.extractor import _cover_blocks
from app.services.signing_region.visual_recall import WholePageSigningVisualRecall
from app.services.signing_region.models import SigningVisualObjectType


def block(text, x=80, y=100, kind='text', key='b'):
    return TextBlock(block_id=key,page_no=1,text=text,bbox=BBox(x0=x,y0=y,x1=x+240,y1=y+20),block_type=kind)


def test_native_table_line_confusables_do_not_create_body_copy():
    page=Page(page_no=1,width=600,height=800,blocks=[block('处理器\n芯片组：1ntel 7500 芯片组\n内存',kind='table')])
    line=NativeSectionTitleCandidate(1,'芯片组:lntel 7500 芯片组',BBox(x0=90,y0=101,x1=290,y1=119),())
    assert NativeHeadingRepairService._candidate_overlaps_table(page,line)
    changed=NativeSectionTitleCandidate(1,'芯片组:lntel 8500 芯片组',line.bbox,())
    assert not NativeHeadingRepairService._candidate_overlaps_table(page,changed)
    outside=NativeSectionTitleCandidate(1,line.text,BBox(x0=90,y0=200,x1=290,y1=220),())
    assert not NativeHeadingRepairService._candidate_overlaps_table(page,outside)


def test_cover_collection_stops_before_catalog_not_first_catalog_chapter():
    blocks=[block('光功率预测设备技术协议',kind='doc_title'),block('目 录',y=130,key='toc'),block('技术协议通用部分',y=160,key='entry'),block('第一章 总则',y=190,key='chapter')]
    doc=Document(filename='x.pdf',path='x.pdf',page_count=1,pages=[Page(page_no=1,width=600,height=800,blocks=blocks)])
    assert [b.block_id for b in _cover_blocks(doc)]==['b']


def test_three_party_vertical_seal_uses_explicit_designer():
    page=Page(page_no=1,width=600,height=800,blocks=[block('设计方：某设计有限公司',y=120,key='design'),block('买方：某买方有限公司',y=310,key='buyer'),block('卖方：某卖方有限公司',y=480,key='seller')])
    role,reason,confidence=WholePageSigningVisualRecall._attribute_party(page,BBox(x0=100,y0=130,x1=220,y1=240),SigningVisualObjectType.COMPANY_ROUND_SEAL)
    assert role=='设计方'
    assert confidence>.5


def test_seal_without_role_anchor_keeps_unknown():
    page=Page(page_no=1,width=600,height=800,blocks=[])
    role,_,_=WholePageSigningVisualRecall._attribute_party(page,BBox(x0=100,y0=130,x1=220,y1=240),SigningVisualObjectType.COMPANY_ROUND_SEAL)
    assert role=='unknown'


def test_company_recovery_selects_company_line_not_neighbor_signature():
    from app.services.signing_region.value_ocr import SigningFieldValueOCR
    lines=[block('中誉设计有限公司'),block('签字）：李某',y=135,key='signature'),block('73405299',y=170,key='phone')]
    assert [b.text for b in SigningFieldValueOCR._party_value_lines(lines)]==['中誉设计有限公司']
    ambiguous=[block('甲有限公司'),block('乙有限公司',y=130,key='other')]
    assert len(SigningFieldValueOCR._party_value_lines(ambiguous))==2


def test_native_slash_recovers_thin_ocr_box_but_keeps_real_seven(tmp_path):
    import fitz
    from app.models import CharBox
    from app.services.table_slash_recovery import repair_table_slashes
    pdf=fitz.open();p=pdf.new_page(width=600,height=800);p.insert_text((100,100),'/');p.insert_text((200,100),'7')
    path=tmp_path/'slash.pdf';pdf.save(path);pdf.close()
    blocks=[]
    for i,x in enumerate([100,200]):
        b=block('7',x=x,y=90,kind='table',key=str(i));b.raw_html='<table><tr><td>7</td></tr></table>'
        b.char_boxes=[CharBox(char='7',page_no=1,text_index=0,bbox=BBox(x0=x-2,y0=94,x1=x+8,y1=95))];blocks.append(b)
    doc=Document(filename=path.name,path=str(path),page_count=1,pages=[Page(page_no=1,width=600,height=800,blocks=blocks)])
    repair_table_slashes(doc)
    assert [b.text for b in blocks]==['/','7']


def test_native_continuation_row_recovers_own_grid_not_page_number(tmp_path):
    import fitz
    from app.services.table_slash_recovery import recover_native_continuation_rows
    pdf=fitz.open();p=pdf.new_page(width=600,height=800)
    for x in [80,120,200,280]:p.draw_line((x,70),(x,95))
    p.draw_line((80,95),(280,95));p.insert_text((90,88),'4')
    path=tmp_path/'row.pdf';pdf.save(path);pdf.close()
    b=block('4',x=90,y=75,kind='ocr_line');b.bbox=BBox(x0=90,y0=75,x1=98,y1=88)
    footer=block('4',x=90,y=760,key='footer')
    doc=Document(filename=path.name,path=str(path),page_count=1,pages=[Page(page_no=1,width=600,height=800,blocks=[b,footer])])
    assert recover_native_continuation_rows(doc)
    assert b.block_type=='table' and b.raw_html.count('<td>')==3
    assert footer.block_type=='text'


def test_company_label_missing_colon_keeps_field_identity_and_body_prose():
    from app.services.cover.patterns import parse_labeled_line
    from app.services.clause_splitter import ClauseSplitter
    assert parse_labeled_line('买方某新能源有限公司')==('buyer','某新能源有限公司')
    assert parse_labeled_line('买方应向某新能源有限公司付款。') is None
    assert ClauseSplitter()._is_cover_noise_text('买方某新能源有限公司')


def test_contact_stamp_character_is_rejected_but_black_name_kept(tmp_path):
    import fitz
    from app.services.signing_region.blank_evidence import reject_stamp_text_as_person
    from app.services.signing_region.models import SigningElement,SigningElementType,SigningRegion,SigningRegionRole
    pdf=fitz.open();p=pdf.new_page(width=600,height=800)
    p.insert_text((100,120),'中',fontname='china-s',fontsize=24,color=(1,0,0));p.insert_text((200,120),'中',fontname='china-s',fontsize=24,color=(0,0,0))
    path=tmp_path/'stamp.pdf';pdf.save(path);pdf.close()
    elements=[SigningElement(element_id=str(i),element_type=SigningElementType.FIELD,page_no=1,text='中',bbox=BBox(x0=x,y0=95,x1=x+24,y1=124),raw_ref={'field_key':'contact'}) for i,x in enumerate([100,200])]
    # The label/line ROI contains black ink; only the localized value is red.
    elements[0].raw_ref['value_bbox']=elements[0].bbox.model_dump()
    elements[0].bbox=BBox(x0=100,y0=95,x1=224,y1=124)
    region=SigningRegion(region_id='r',page_no=1,role=SigningRegionRole.UNKNOWN,bbox=BBox(x0=50,y0=80,x1=400,y1=150),elements=elements)
    doc=Document(filename=path.name,path=str(path),page_count=1,pages=[Page(page_no=1,width=600,height=800,blocks=[])])
    reject_stamp_text_as_person(doc,[region])
    assert elements[0].text=='' and elements[0].raw_ref['content_recognition_status']=='NEEDS_REVIEW'
    assert elements[1].text=='中'


def test_cover_recovery_uses_own_reading_and_retains_conflicting_reads(tmp_path):
    import fitz
    from app.services.cover.party_ocr import CoverPartyOCR
    pdf=fitz.open();pdf.new_page(width=600,height=800)
    path=tmp_path/'own.pdf';pdf.save(path);pdf.close()
    def document(designer):
        blocks=[block('买方：某买方有限公司',y=400,key='buyer'),block('卖方：某卖方有限公司',y=500,key='seller')]
        if designer: blocks.append(block('设计方：原设计有限公司',y=300,key='designer'))
        return Document(filename=path.name,path=str(path),page_count=1,pages=[Page(page_no=1,width=600,height=800,blocks=blocks)])
    class OwnOCR:
        def __init__(self, inconsistent=False):self.calls=0;self.inconsistent=inconsistent
        def predict(self,path,file_type=1):
            self.calls+=1
            return '设计方：新设计有限公司' if self.calls==1 or not self.inconsistent else '设计方：另一设计有限公司'
        def payload_to_document(self,payload,path):
            b=block(payload);b.confidence=.99
            return Document(filename='crop.png',path=path,page_count=1,pages=[Page(page_no=1,width=600,height=800,blocks=[b])])
    own,peer=document(False),document(True)
    result=CoverPartyOCR(OwnOCR()).enrich_pair(peer,own)
    assert result[0]['status']=='RECOVERED'
    assert any(b.text=='设计方：新设计有限公司' for b in own.pages[0].blocks)
    assert not any(b.text=='设计方：原设计有限公司' for b in own.pages[0].blocks)
    own=document(False)
    result=CoverPartyOCR(OwnOCR(True)).enrich_pair(peer,own)
    assert result[0]['status']=='NEEDS_REVIEW'
    assert not any('设计方' in b.text for b in own.pages[0].blocks)


def test_cover_title_role_repair_cannot_hide_changed_device_title():
    from app.services.cover.party_ocr import reconcile_cover_title_roles
    def document(title,kind):
        blocks=[block(title,y=200,kind=kind),block('买方：某买方有限公司',y=400,key='buyer'),block('卖方：某卖方有限公司',y=500,key='seller')]
        return Document(filename='x',path='x',page_count=1,pages=[Page(page_no=1,width=600,height=800,blocks=blocks)])
    original=document('光功率预测系统设备','doc_title');same=document('光功率预测系统设备','text')
    changed=document('风功率预测系统设备','text')
    assert reconcile_cover_title_roles(original,same)
    assert same.pages[0].blocks[0].block_type=='doc_title'
    assert not reconcile_cover_title_roles(original,changed)
    assert changed.pages[0].blocks[0].block_type=='text'


def test_occluded_company_correction_requires_own_matching_party_support(tmp_path):
    import fitz
    from app.services.signing_region.value_ocr import SigningFieldValueOCR
    from app.services.signing_region.models import SigningElement,SigningElementType
    pdf=fitz.open();pdf.new_page(width=600,height=800)
    path=tmp_path/'company.pdf';pdf.save(path);pdf.close()
    class OwnOCR:
        def predict(self,*args,**kwargs):return {}
        def payload_to_document(self,payload,path):
            b=block('中誉设计有限公司');b.confidence=.99
            return Document(filename='crop.png',path=path,page_count=1,pages=[Page(page_no=1,width=600,height=800,blocks=[b])])
    def field(support):
        box=BBox(x0=80,y0=100,x1=300,y1=130)
        return SigningElement(element_id='company',element_type=SigningElementType.PARTY_FIELD,page_no=1,bbox=box,text='设计方：中设限有限公司',
            raw_ref={'field_key':'party_name','party_role':'设计方','source_block_type':'seal','value_roi':box.model_dump(),'same_document_party_support':support})
    service=SigningFieldValueOCR(OwnOCR())
    with fitz.open(path) as pdf:
        missing=field([])
        assert service._recover_occluded_printed_field(pdf,missing)['status']=='LOCAL_READING_CONFLICTS_WITH_EXISTING_VALUE'
        supported=field([{'text':'中誉设计有限公司','page_no':2,'block_id':'own-cover'}])
        assert service._recover_occluded_printed_field(pdf,supported)['status']=='RECOVERED'
        assert supported.text=='设计方：中誉设计有限公司'
        conflicting=field([{'text':'中誉设计有限公司'},{'text':'另一设计有限公司'}])
        assert service._recover_occluded_printed_field(pdf,conflicting)['status']=='LOCAL_READING_CONFLICTS_WITH_EXISTING_VALUE'
