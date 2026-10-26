"""Root-cause controls: source retention and ambiguity must survive repairs."""
from collections import Counter

import fitz
import pytest

from app.models import BBox, Page
from app.services.signing_region.block_detector import SigningBlockDetector
from app.services.signing_region.value_ocr import SigningFieldValueOCR
from app.services.signing_region.visual_recall import WholePageSigningVisualRecall as Recall
from app.services.signing_region.models import SigningVisualObjectType as Kind
from app.services.table_compare.repair import TableRepairContext, TableRepairService
from app.services.table_compare.types import LogicalCell, LogicalRow
from tests.test_signing_visual_recall import _block, _object, _document
from tests.test_signing_region_value_ocr import _field, _region, _pdf, _FakeOCRExtractor


def _box(x,y,w=60,h=18):
    return BBox(x0=x,y0=y,x1=x+w,y1=y+h)


@pytest.mark.parametrize('text', ['2026.05.30','2026年5月30日','2026-05-03'])
def test_separate_date_value_is_recalled_from_same_line(text):
    label = _block('label','时间：',_box(320,650,34))
    date = _block('value',text,_box(360,651))
    page = Page(page_no=1,width=600,height=800,blocks=[label,date])
    assert date in SigningBlockDetector()._candidate_blocks(page,'body')


@pytest.mark.parametrize('position', [(100,650),(360,700),(490,650)])
def test_other_column_or_line_is_not_borrowed_as_date(position):
    label = _block('label','时间：',_box(320,650,34))
    date = _block('value','2026.05.30',_box(*position))
    page = Page(page_no=1,width=600,height=800,blocks=[label,date])
    assert date not in SigningBlockDetector()._candidate_blocks(page,'body')


def test_two_adjacent_date_candidates_remain_ambiguous():
    label = _block('label','时间：',_box(320,650,34))
    a = _block('a','2026.05.30',_box(360,651))
    b = _block('b','2026.05.03',_box(362,651))
    page = Page(page_no=1,width=600,height=800,blocks=[label,a,b])
    selected = SigningBlockDetector()._candidate_blocks(page,'body')
    assert a not in selected and b not in selected


def test_incomplete_date_reread_invalidates_prior_confirmation(tmp_path):
    field = _field(value='2026年5月3日')
    field.raw_ref.update(field_key='date',content_recognition_status='CONFIRMED')
    ocr = SigningFieldValueOCR(extractor=_FakeOCRExtractor(['2026年5月']))
    with fitz.open(_pdf(tmp_path)) as pdf:
        result = ocr._recover_occluded_printed_field(pdf,field)
    assert result['status'] == 'INCOMPLETE_LOCAL_DATE'
    assert field.raw_ref['content_recognition_status'] == 'NEEDS_REVIEW'
    assert field.text == '2026年5月3日'


def test_same_model_repetition_cannot_confirm_disputed_day(tmp_path):
    field = _field(value='2026年5月3日')
    peer = _field(value='2026年5月30日')
    for f in (field,peer):
        f.raw_ref.update(field_key='date',content_recognition_status='CONFIRMED')
    ocr = SigningFieldValueOCR(extractor=_FakeOCRExtractor(['2026年5月3日']*2))
    result = ocr.enrich_disputed_dates(_pdf(tmp_path),_region(field),_region(peer))
    assert result[0]['status'] == 'RECOVERED'
    assert field.text == '2026年5月3日'  # Never copy the other document's value.
    assert field.raw_ref['content_recognition_status'] == 'NEEDS_REVIEW'


def test_round_seal_fragment_can_match_but_neighbour_cannot(tmp_path,monkeypatch):
    document = _document(tmp_path/'absent.pdf',[])
    target = _object(Kind.COMPANY_ROUND_SEAL,bbox=_box(100,100,100,100))
    fragment = _object(Kind.OTHER_STAMP,bbox=_box(160,160,25,25))
    other = _object(Kind.OTHER_STAMP,bbox=_box(205,160,25,25))
    assert Recall._best_counterpart(target,document,[fragment],document,page_alignment=None,used=set()) is None
    monkeypatch.setattr('app.services.seal_visual_equivalence.corresponding_red_graphics',lambda *args:{'equivalent':True})
    assert Recall._best_counterpart(target,document,[fragment],document,page_alignment=None,used=set()) == 0
    assert Recall._best_counterpart(target,document,[other],document,page_alignment=None,used=set()) is None
    assert Recall._best_counterpart(target,document,[fragment],document,page_alignment=None,used={0}) is None


def _row(index,values,source=''):
    return LogicalRow(index,[LogicalCell(index,c,t,None,1,'table',index,c) for c,t in enumerate(values)],1,'table',index,source_text=source)


def test_phantom_repair_cannot_consume_product_or_price(monkeypatch):
    rows = [_row(0,['2','气象服务 数值天气预报服务','80000']),
            _row(1,['3','光功率预测系统维保服务','90000'])]
    broken = [_row(0,['2','气象服务','80000']),_row(1,['3','数值天气预报服务',''])]
    service = TableRepairService()
    monkeypatch.setattr(service,'_try_split_phantom_merged_name_pair',lambda *args:broken)
    repaired = service.repair_phantom_merged_name_rows(TableRepairContext(),rows,6)
    tokens = lambda rs:Counter(c for r in rs for cell in r.cells for c in cell.text if not c.isspace())
    assert tokens(repaired) == tokens(rows)
    assert any('90000' in c.text for r in repaired for c in r.cells)


@pytest.mark.parametrize('written', [False,True])
def test_blank_requires_pixels_and_preserves_unread_ink(tmp_path,written):
    from app.services.signing_region.blank_evidence import verify_blank_field_rois
    from app.services.field_observation import ObservationState
    field=_field(value='')
    path=tmp_path/'blank.pdf'
    with fitz.open() as pdf:
        page=pdf.new_page(width=600,height=800)
        if written:
            page.insert_text((195,320),'2026.05.30')
        pdf.save(path)
    doc=_document(path,[])
    verify_blank_field_rois(doc,[_region(field)])
    assert field.observation_state == (ObservationState.UNOBSERVED if written else ObservationState.OBSERVED_BLANK)
    assert field.raw_ref['blank_visual_evidence']['method']=='LOCAL_VALUE_ROI_INK'


@pytest.mark.parametrize('right_text,expected',[('2026.05.30',True),('2026.05.3',False),('2026.05.80',False),('2026.05.31',False)])
def test_date_ink_check_retains_real_digit_changes(tmp_path,right_text,expected):
    from app.services.signing_region.date_visual_equivalence import date_ink_equivalence
    def make(name,text):
        path=tmp_path/name
        with fitz.open() as pdf:
            p=pdf.new_page(width=600,height=800)
            p.insert_text((100,320),text,fontsize=20)
            pdf.save(path)
        field=_field(value=text)
        field.element_id=name
        field.bbox=BBox(x0=98,y0=299,x1=230,y1=326)
        field.raw_ref['value_roi']=field.bbox.model_dump()
        return _document(path,[]),field
    a,left=make('a.pdf','2026.05.30')
    b,right=make('b.pdf',right_text)
    proof=date_ink_equivalence(a,left,b,right)
    assert proof['equivalent'] is expected,proof


@pytest.mark.parametrize('angle',[-1.6,1.6])
def test_slanted_table_recovers_cell_ownership_without_losing_amount(tmp_path,angle):
    from app.models import Document,TextBlock,CharBox
    from app.services.ruled_table_recovery import recover_ruled_tables
    from app.services.table_compare.parser import LogicalTableParser
    path=tmp_path/'skew.pdf'
    with fitz.open() as pdf:
        page=pdf.new_page(width=600,height=800)
        morph=(fitz.Point(300,220),fitz.Matrix(angle))
        shape=page.new_shape()
        for x in [50,100,300,380,450,550]:
            shape.draw_line((x,200),(x,250))
        for y in [200,222,250]:
            shape.draw_line((50,y),(550,y))
        shape.finish(color=(0,0,0),width=1,morph=morph)
        shape.commit()
        for x,text in zip([55,105,305,385,455],['No','Project','Buyer','Qty','Price']):
            page.insert_text((x,215),text,fontsize=10,morph=morph)
        for x,text in zip([55,105,305,385,455],['1','Maintenance','ABC','3','240000']):
            page.insert_text((x,240),text,fontsize=10,morph=morph)
        chars=[]
        for block in page.get_text('rawdict')['blocks']:
            for line in block.get('lines',[]):
                for span in line['spans']:
                    for c in span['chars']:
                        x0,y0,x1,y1=c['bbox']
                        chars.append(CharBox(char=c['c'],text_index=len(chars),page_no=1,bbox=BBox(x0=x0,y0=y0,x1=x1,y1=y1)))
        pdf.save(path)
    b=TextBlock(block_id='table',page_no=1,block_type='table',text=''.join(c.char for c in chars),
                bbox=BBox(x0=40,y0=185,x1=560,y1=265),char_boxes=chars,
                raw_html='<table><tr><td>incorrect</td></tr></table>')
    doc=_document(path,[b])
    assert recover_ruled_tables(doc)==[b]
    parser=LogicalTableParser()
    table=parser.parse_tables(parser.table_blocks(doc))[0]
    assert table.col_count==5
    assert table.get_cell(1,4).text=='240000'


def test_watermark_family_never_fuzzes_changed_identifiers():
    from app.services.diagonal_watermark import _same_family
    assert _same_family('大唐电子商务平台','大居电工商务平')
    assert not _same_family('CDTHT20260060282','CDTHT20260060283')


def test_watermark_changes_have_one_owner_and_body_names_survive(tmp_path):
    from app.services.diagonal_watermark import classify_diagonal_watermarks,build_watermark_diffs
    from app.services.cover.patterns import skip_extra_block
    path=tmp_path/'watermark.pdf'
    blocks=[]
    with fitz.open() as pdf:
        page=pdf.new_page(width=600,height=800)
        for i,y in enumerate([180,380]):
            origin=fitz.Point(120,y)
            page.insert_text(origin,'WATERMARK',fontsize=24,color=(.85,.85,.85),morph=(origin,fitz.Matrix(45)))
        pdf.save(path)
        for i,b in enumerate(page.get_text('blocks')):
            blocks.append(_block(str(i),'WATERMARK',BBox(x0=b[0],y0=b[1],x1=b[2],y1=b[3]),block_type='ocr_line'))
    body=_block('body','WATERMARK',_box(80,600,150,20))
    doc=_document(path,[*blocks,body])
    decisions=classify_diagonal_watermarks(doc)
    assert len(decisions)==2
    assert all(skip_extra_block(b) and b.enter_clause_compare is False for b in blocks)
    assert body.enter_clause_compare is not False and body.block_type=='text'
    assert not build_watermark_diffs(doc,doc)
    diffs=build_watermark_diffs(_document(path,[]),doc)
    assert len(diffs)==1 and diffs[0].compare_text=='WATERMARK'


def test_blank_pixels_cannot_override_unreliable_field_identity(tmp_path):
    from app.services.signing_region.blank_evidence import verify_blank_field_rois
    from app.services.field_observation import ObservationState
    field=_field(value='')
    field.observation_state=ObservationState.UNOBSERVED
    field.observation_reason='CROSS_COLUMN_EVIDENCE_REJECTED'
    doc=_document(_pdf(tmp_path),[])
    diagnostic=verify_blank_field_rois(doc,[_region(field)])
    assert field.observation_state==ObservationState.UNOBSERVED
    assert field.observation_reason=='CROSS_COLUMN_EVIDENCE_REJECTED'
    assert diagnostic[0]['status']=='PRIOR_UNCERTAINTY_PRESERVED'


def test_verified_date_equality_cannot_hide_non_date_table_change():
    from app.models import DiffItem,EvidenceBox
    from app.services.signing_region.date_visual_equivalence import remove_owned_date_table_diffs
    a,b=_field(value='2026年5月30日'),_field(value='2026年5月3日')
    proof={'equivalent':True,'original_element_id':a.element_id,'compare_element_id':b.element_id}
    a.raw_ref.update(field_key='date',paired_date_ink=proof)
    b.raw_ref.update(field_key='date',paired_date_ink=proof)
    diff=DiffItem(diff_id='date',source_type='table',diff_type='MODIFY',original_text='日期：2026年5月30日',
                  compare_text='日期：2026年5月3日',original_evidence=[EvidenceBox(page_no=1,bbox=a.bbox)])
    assert not remove_owned_date_table_diffs([diff],[_region(a)],[_region(b)])
    diff.original_text+=' | 90000'
    diff.compare_text+=' | 80000'
    assert remove_owned_date_table_diffs([diff],[_region(a)],[_region(b)])==[diff]


def test_empty_date_template_is_not_a_disputed_reading(tmp_path):
    a,b=_field(value='年月日'),_field(value='2026年5月8日')
    for f in (a,b):f.raw_ref.update(field_key='date',content_recognition_status='CONFIRMED')
    service=SigningFieldValueOCR(extractor=_FakeOCRExtractor([]))
    path=_pdf(tmp_path)
    assert not service.enrich_disputed_dates(path,_region(a),_region(b))
    assert not service.enrich_disputed_dates(path,_region(b),_region(a))
    assert a.raw_ref['content_recognition_status']==b.raw_ref['content_recognition_status']=='CONFIRMED'


@pytest.mark.parametrize('written',[False,True])
def test_template_labels_and_table_border_do_not_hide_written_digit(tmp_path,written):
    from app.models import CharBox
    from app.services.field_observation import ObservationState
    from app.services.signing_region.blank_evidence import verify_blank_field_rois
    path=tmp_path/'template.pdf'
    chars=[]
    with fitz.open() as pdf:
        p=pdf.new_page(width=600,height=800)
        p.insert_text((180,290),'法定代表人（负责人）或',fontname='china-s',fontsize=10)
        for block in p.get_text('rawdict')['blocks']:
            for line in block.get('lines',[]):
                for span in line['spans']:
                    for c in span['chars']:
                        x0,y0,x1,y1=c['bbox']
                        chars.append(CharBox(char=c['c'],page_no=1,text_index=len(chars),bbox=BBox(x0=x0,y0=y0,x1=x1,y1=y1)))
        p.draw_line((265,260),(265,355),width=1)
        if written:p.insert_text((215,320),'1',fontsize=18)
        pdf.save(path)
    table=_block('table',''.join(c.char for c in chars),BBox(x0=80,y0=260,x1=265,y1=355),block_type='table')
    table.char_boxes=chars
    field=_field(value='')
    field.raw_ref['value_roi']=BBox(x0=180,y0=275,x1=280,y1=332).model_dump()
    diag=verify_blank_field_rois(_document(path,[table]),[_region(field)])
    assert diag[0]['masked_template_characters']>0
    assert field.observation_state==(ObservationState.UNOBSERVED if written else ObservationState.OBSERVED_BLANK)


@pytest.mark.parametrize('color,recognized,expected',[( (.92,.92,.92),'','image'),((0,0,0),'','seal'),((1,.92,.92),'','seal'),((.92,.92,.92),'合同专用章','seal')])
def test_layout_image_requires_seal_evidence_but_black_and_pale_red_survive(tmp_path,color,recognized,expected):
    from app.services.seal_image_validation import reclassify_unverified_seal_images
    path=tmp_path/'image.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page(width=600,height=800)
        p.draw_rect(fitz.Rect(110,110,145,145),color=color,fill=color)
        pdf.save(path)
    b=_block('image',recognized or '<img src="image.png"/>',_box(100,100,60,60),block_type='seal')
    b.raw_html='<img src="image.png"/>'
    document=_document(path,[b])
    reclassify_unverified_seal_images(document)
    assert b.block_type==expected
    assert b.text==(recognized or '<img src="image.png"/>')


@pytest.mark.parametrize('suffix',['',' | 90000'])
def test_rejected_stamp_inscription_has_one_owner_but_other_table_values_survive(suffix):
    from app.models import DiffItem,EvidenceBox
    from app.services.signing_region.blank_evidence import remove_owned_stamp_table_diffs
    a,b=_field(value=''),_field(value='')
    for f,raw in [(a,'出 用章'),(b,'合同专用章')]:
        f.raw_ref.update(field_label='法定代表人',rejected_stamp_value={'reason':'STAMP_INSCRIPTION_IS_NOT_PERSON_NAME','rejected_text':raw})
    diff=DiffItem(diff_id='noise',source_type='table',diff_type='MODIFY',
        original_text='法定代表人：出 用章'+suffix,compare_text='法定代表人：合同专用章'+suffix,
        original_evidence=[EvidenceBox(page_no=1,bbox=a.bbox)],compare_evidence=[EvidenceBox(page_no=1,bbox=b.bbox)])
    kept,decisions=remove_owned_stamp_table_diffs([diff],[_region(a)],[_region(b)])
    assert bool(kept)==bool(suffix)
    assert bool(decisions)==(not suffix)
    diff.compare_text='法定代表人：张三'
    assert remove_owned_stamp_table_diffs([diff],[_region(a)],[_region(b)])[0]==[diff]


@pytest.mark.parametrize('name,retained',[('张用章',True),('出 用章',False)])
def test_person_name_containing_stamp_syllables_is_not_rejected(tmp_path,name,retained):
    from app.services.signing_region.blank_evidence import reject_stamp_text_as_person
    field=_field(value=name)
    path=tmp_path/'name.pdf'
    with fitz.open() as pdf:
        p=pdf.new_page(width=600,height=800)
        p.draw_circle(fitz.Point(210,315),10,color=(1,0,0),width=2)
        pdf.save(path)
    reject_stamp_text_as_person(_document(path,[]),[_region(field)])
    assert (field.text==name) is retained
