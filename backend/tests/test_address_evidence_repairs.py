"""Address identity, overlapping print ownership and incomplete observations."""
from app.models import BBox, CharBox, Document, Page, TextBlock
from app.services.cover import CoverMetadataComparator
from app.services.signing_region.comparator import SigningRegionComparator
from app.services.signing_region.models import SigningElement, SigningElementType

ADDRESS = '江苏省南京市栖霞区八卦洲街道鹏岛路254号悦福大厦12-0044'


def line(text, index, y, kind='text', x=60):
    return TextBlock(block_id=str(index),page_no=2,text=text,source='PP-OCRv6',confidence=.99,
        block_type=kind,flow_role='non_text' if kind=='seal' else 'body',
        bbox=BBox(x0=x,y0=y,x1=x+len(text)*10,y1=y+16),
        char_boxes=[CharBox(char=c,text_index=i,page_no=2,bbox=BBox(x0=x+i*10,y0=y,x1=x+(i+1)*10,y1=y+16)) for i,c in enumerate(text)])


def doc(address=ADDRESS, seller='北京市海淀区西三旗建材城中路27号'):
    texts=['甲方：南京新能源有限公司','通讯地址：'+address,'乙方：北京科技有限公司','通讯地址：'+seller]
    return Document(filename='sample',path='',page_count=2,pages=[Page(page_no=1,width=600,height=800,
        blocks=[TextBlock(block_id='title',page_no=1,text='功率预测系统采购合同',block_type='doc_title',bbox=BBox(x0=60,y0=100,x1=500,y1=120))]),
        Page(page_no=2,width=600,height=800,blocks=[line(t,i,180+i*30) for i,t in enumerate(texts)])])


def test_same_party_address_edit_is_one_modify_with_localized_character():
    diffs=CoverMetadataComparator().build_diffs(doc(),doc(ADDRESS.replace('鹏','鹂')))
    diffs=[d for d in diffs if '254' in d.original_text+d.compare_text]
    assert len(diffs)==1
    assert diffs[0].diff_type=='MODIFY'
    assert diffs[0].match_score_details['field_key']=='buyer_address'
    assert [e.text for e in diffs[0].original_evidence]==['鹏']
    assert [e.text for e in diffs[0].compare_evidence]==['鹂']


def test_buyer_seller_addresses_do_not_cancel_when_swapped():
    a=ADDRESS;b='北京市海淀区西三旗建材城中路27号'
    diffs=CoverMetadataComparator().build_diffs(doc(a,b),doc(b,a))
    address=[d for d in diffs if 'address' in d.match_score_details.get('field_key','')]
    assert len(address)==2
    assert all(d.diff_type=='MODIFY' for d in address)


def test_rejected_candidate_does_not_veto_accepted_owned_value():
    field=SigningElement(element_id='a',element_type=SigningElementType.FIELD,page_no=8,
        text=ADDRESS,bbox=BBox(x0=60,y0=160,x1=290,y1=210),raw_ref={
            'field_key':'address','party_role':'甲方',
            'value_evidence':[{'text':ADDRESS,'normalized_text':ADDRESS,'localized':True,'provenance_valid':True,
                               'bbox':{'x0':60,'y0':160,'x1':290,'y1':210}}],
            'rejected_contaminated_evidence':[{'rejection_reason':'AMBIGUOUS_MULTI_LABEL_BLOCK'}]})
    assert not SigningRegionComparator._has_rejected_cross_column_evidence(field)
    field.raw_ref['value_evidence']=[]
    assert SigningRegionComparator._has_rejected_cross_column_evidence(field)


def test_seal_address_tail_requires_own_image_reads_and_keeps_true_number_edit(tmp_path, monkeypatch):
    import fitz
    from app.services import address_evidence as ae
    original=doc();compare=doc()
    for name,d in [('o',original),('c',compare)]:
        path=tmp_path/(name+'.pdf')
        with fitz.open() as pdf:
            pdf.new_page(width=600,height=800);pdf.new_page(width=600,height=800);pdf.save(path)
        d.path=str(path)
        d.pages[1].blocks=[line('地址：江苏省南京市栖霞区八卦洲街',0,160),
            line('道鹏岛路254号悦福大厦12-0044',1,190,'seal')]
    tail=compare.pages[1].blocks[1]
    def read(extractor,page,block,scale):
        return block.model_copy(deep=True)
    monkeypatch.setattr(ae,'read_address_line',read)
    changes=ae.reconcile_addresses(original,compare,extractor=object())
    assert tail.block_type=='text' and tail.flow_role=='body'
    assert tail.text.endswith('12-0044')
    assert len(changes)==2
    # Same geometry is not permission to copy a peer's house number.
    tail.block_type='seal';tail.flow_role='non_text';tail.source_evidence=[]
    tail.semantic_reasons=[];tail.text=tail.text.replace('254','255')
    ae.reconcile_addresses(original,compare,extractor=object())
    assert '255' in tail.text and tail.block_type=='text'


def test_unreadable_seal_tail_stays_unresolved(tmp_path,monkeypatch):
    import fitz
    from app.services import address_evidence as ae
    d=doc();path=tmp_path/'a.pdf'
    with fitz.open() as pdf:
        pdf.new_page();pdf.new_page();pdf.save(path)
    d.path=str(path);d.pages[1].blocks=[line('地址：江苏省南京市栖霞区八卦洲街',0,160),line('道鹏岛路254号悦福大厦12-0044',1,190,'seal')]
    monkeypatch.setattr(ae,'read_address_line',lambda *a:None)
    assert ae.reconcile_addresses(d,doc(),extractor=object())==[]
    assert d.pages[1].blocks[1].block_type=='seal'


def test_address_reread_requires_every_located_line_and_rejects_overlapping_noise():
    import fitz
    from types import SimpleNamespace
    from app.services.signing_region.value_ocr import SigningFieldValueOCR
    first=line('地址：江苏省南京市栖霞区八卦洲街',0,10)
    tail=line('道鹏岛路254号悦福大厦12-0044',1,50)
    required=[(b.text,b.bbox) for b in (first,tail)]
    clip=fitz.Rect(0,0,600,100);page=SimpleNamespace(width=600,height=100)
    select=SigningFieldValueOCR._address_value_lines
    assert select([first],required,clip,page) is None
    noise=line('20E00',2,30,x=180);noise.confidence=.6
    assert select([first,noise,tail],required,clip,page)==[first,tail]
    noise.bbox.y0=50;noise.bbox.y1=60
    assert select([first,noise,tail],required,clip,page) is None


def test_partial_confirmed_address_is_still_unobserved_when_tail_is_unresolved():
    from app.services.signing_region.models import SigningRegion
    field=SigningElement(element_id='partial',element_type=SigningElementType.FIELD,page_no=8,
        text='江苏省南京市栖霞区八卦洲街',bbox=BBox(x0=60,y0=160,x1=290,y1=180),
        observation_state='OBSERVED_VALUE',raw_ref={'field_key':'address',
            'unresolved_address_lines':[{'text':'道鹏岛路254号悦福大厦12-0044'}]})
    region=SigningRegion(region_id='r',page_no=8,bbox=field.bbox,elements=[field])
    obs=SigningRegionComparator._field_observation(field,region)
    assert obs.state.value=='UNOBSERVED'
    assert obs.reason=='INCOMPLETE_ADDRESS_LINES'


def test_confirmed_roi_cannot_erase_disputed_address_content(tmp_path):
    import fitz
    from app.services.signing_region.value_ocr import SigningFieldValueOCR
    from tests.test_signing_region_value_ocr import _occluded_address, _FakeOCRExtractor, _pdf
    field,region=_occluded_address('北京市昌平区商业街32号')
    field.raw_ref.update(address_recognition_disputed=True,content_recognition_status='NEEDS_REVIEW',
        content_recognition_reason='ADDRESS_LINE_READINGS_DISAGREE')
    service=SigningFieldValueOCR(extractor=_FakeOCRExtractor([field.text,field.text]))
    result=service.enrich_occluded_fields(_pdf(tmp_path),[region])
    assert result[0]['status']=='RECOVERED'
    assert field.raw_ref['content_recognition_status']=='NEEDS_REVIEW'
    assert field.raw_ref['content_recognition_reason']=='ADDRESS_LINE_READINGS_DISAGREE'


def test_true_address_change_has_one_owner_with_clause_comparison():
    from app.models import Clause,DiffItem,TextRange
    from app.services.evidence_locator import EvidenceLocator
    a=doc();b=doc(ADDRESS.replace('254','255'))
    field=next(d for d in CoverMetadataComparator().build_diffs(a,b) if d.match_score_details.get('field_key')=='buyer_address')
    blocks=[d.pages[1].blocks[1] for d in [a,b]]
    clauses=[Clause(clause_id=str(i),text=v.text,normalized_text=v.text,page_numbers=[2],char_boxes=v.char_boxes) for i,v in enumerate(blocks)]
    i=blocks[0].text.index('254')
    clause=DiffItem(diff_id='clause',diff_type='MODIFY',source_type='clause',original_clause_id='0',compare_clause_id='1',
        original_text=blocks[0].text,compare_text=blocks[1].text,
        original_change_ranges=[TextRange(start=i,end=i+3,highlight_type='MODIFY')],
        compare_change_ranges=[TextRange(start=i,end=i+3,highlight_type='MODIFY')])
    results=EvidenceLocator().locate([field,clause],[clauses[0]],[clauses[1]])
    assert [r.diff_id for r in results]==[field.diff_id]
    assert [e.text for e in field.original_evidence]==['254']
    assert [e.text for e in field.compare_evidence]==['255']
