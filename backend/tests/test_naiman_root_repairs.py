"""Regressions for localization drift, repeated scalar cells and range evidence."""
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from app.models import Clause, ClausePair
from app.services.diff.builder import build_diffs
from app.services.extractors.contract_v3.table_raster_evidence import repair_clear_merged_rows, validate_internal_dividers
from app.services.table_compare import TableComparator


@pytest.mark.parametrize('scale', [1, 2, 3])
@pytest.mark.parametrize('divider', ['solid', 'absent', 'partial'])
def test_divider_search_accounts_for_both_predicted_edges(scale, divider):
    image = Image.new('RGB', (986*scale, 1078*scale), 'white')
    draw = ImageDraw.Draw(image)
    def line(coords):
        draw.line([round(v*scale) for v in coords], fill='black', width=scale)
    for y in (114, 158):
        line([682, y, 877, y])
    for x in (682, 877):
        line([x, 114, x, 158])
    if divider != 'absent':
        line([784, 114, 784, 158 if divider == 'solid' else 140])
    cells = [dict(cell_id=str(i), row=0, column=i, rowspan=1, colspan=1,
                  bbox=[v*scale for v in box])
             for i, box in enumerate(([682,114,777,157], [780,114,877,158]))]
    result = validate_internal_dividers({'cells': cells}, image)
    repaired, changes = repair_clear_merged_rows(cells, image)
    if divider == 'solid':
        assert result.usable
        assert repaired == cells and not changes
    elif divider == 'absent':
        assert len(repaired) == 1 and repaired[0]['colspan'] == 2
    else:
        assert repaired == cells and not changes


def test_narrow_cell_keeps_existing_localization_tolerance():
    image=Image.new('RGB',(1020,1300),'white')
    ImageDraw.Draw(image).line([993,10,993,100],fill='black',width=1)
    cells=[dict(cell_id=str(i),row=0,column=i,rowspan=1,colspan=1,bbox=box)
           for i,box in enumerate(([908,10,987,100],[990,10,1007,100]))]
    assert validate_internal_dividers({'cells':cells},image).usable


def test_duplicate_scalar_prefers_same_column_before_cross_column(monkeypatch):
    builder = TableComparator()._diff_builder
    left, right = object(), object()
    cells = {id(left): [{'col':0,'norm':'1'}, {'col':1,'norm':'设备'}, {'col':4,'norm':'1'}],
             id(right): [{'col':0,'norm':'1'}, {'col':1,'norm':'设备'}, {'col':3,'norm':'套1'}]}
    monkeypatch.setattr(builder._matcher, 'row_match_cells', lambda t, r: cells[id(t)])
    monkeypatch.setattr(builder._matcher, 'row_source_text', lambda *a: '')
    monkeypatch.setattr(builder._matcher, 'row_cell_match_score', lambda a,b,*args: float(a==b))
    monkeypatch.setattr(builder, '_is_possible_ocr_shift_row', lambda *a: True)
    result = builder._layout_shift_matches(left,right,0,0,None,None)
    assert result['orig_to_comp'] == {0:0,1:1}


def test_real_column_shift_and_real_quantity_change_remain_detectable():
    from tests.test_table_compare_structured import _structured_table
    builder=TableComparator()._diff_builder
    a=_structured_table([['1','预测主机','型号A','套','1']])
    b=_structured_table([['1','预测主机','型号A','套','2']])
    diffs=builder.diff_cells(a,b)
    assert any(d.original_text=='1' and d.compare_text=='2' for d in diffs)
    # A genuine extraction shift still has a distinct text anchor.
    shifted=_structured_table([['1','预测主机','型号A','','套']])
    matches=builder._layout_shift_matches(a,shifted,0,0,None,None)
    assert matches and matches['orig_to_comp'][3]==4


def test_amount_guard_preserves_swapped_amounts():
    diffs=clause_diffs('甲方支付1000元，乙方支付2000元。','甲方支付2000元，乙方支付1000元。')
    assert diffs and 'CRITICAL_FIELD_AMOUNT_CHANGE' in diffs[0].review_flags


def clause_diffs(a, b):
    return build_diffs([ClausePair(original=Clause(clause_id='o',text=a,normalized_text=a),
        compare=Clause(clause_id='c',text=b,normalized_text=b),score=100)])


def test_amount_guard_does_not_invent_amount_change_from_unit_spacing():
    a='缴纳违约金300—1000元，负责人缴纳违约金500—2000 元。'
    b='缴纳违约金300一1000元，负责人缴纳违约金500一2000元。'
    diffs=clause_diffs(a,b)
    assert diffs  # Without pixel evidence the different symbol remains visible.
    assert 'CRITICAL_FIELD_AMOUNT_CHANGE' not in diffs[0].review_flags
    real=clause_diffs(a,b.replace('2000','2500'))
    assert 'CRITICAL_FIELD_AMOUNT_CHANGE' in real[0].review_flags


@pytest.mark.parametrize('texts,expected', [
    (['300—1000','300—1000'], '300—1000'),
    (['300一1000','300一1000'], '300一1000'),
    (['300—1000','300一1000'], '300一1000'),
    (['350—1000','350—1000'], '300一1000'),
])
def test_range_requires_two_own_readings_with_unchanged_endpoints(tmp_path,texts,expected):
    from tests.test_payment_symbol_ocr import document, Reader
    from app.services.numeric_range_ocr import reconcile_numeric_ranges
    a=document(tmp_path,'o','违约金300—1000元',source='native_pdf')
    b=document(tmp_path,'c','违约金300一1000元')
    reconcile_numeric_ranges(a,b,Reader(texts))
    block=b.pages[0].blocks[0]
    assert block.text == '违约金'+expected+'元'
    assert all(block.text[c.text_index]==c.char for c in block.char_boxes)


def test_range_reading_never_hides_changed_endpoint(tmp_path):
    from tests.test_payment_symbol_ocr import document, Reader
    from app.services.numeric_range_ocr import reconcile_numeric_ranges
    a=document(tmp_path,'o','违约金300—1000元',source='native_pdf')
    b=document(tmp_path,'c','违约金350一1000元')
    reconcile_numeric_ranges(a,b,Reader(['350—1000']*2))
    assert clause_diffs(a.pages[0].blocks[0].text,b.pages[0].blocks[0].text)


def test_chinese_numeral_is_not_globally_replaced(tmp_path):
    from tests.test_payment_symbol_ocr import document, Reader
    from app.services.numeric_range_ocr import reconcile_numeric_ranges
    a=document(tmp_path,'o','第一条一万元，数量1至2',source='native_pdf')
    b=document(tmp_path,'c','第一条一万元，数量1至2')
    reader=Reader([])
    assert reconcile_numeric_ranges(a,b,reader)==[] and reader.calls==0


@pytest.mark.parametrize('own_name', ['深能奈曼光伏电站光', '另一光伏电站光'])
def test_occluded_field_uses_own_value_and_consumes_both_fragments(tmp_path,own_name):
    import fitz
    import numpy as np
    from app.models import Document,Page,TextBlock,BBox
    from app.services.printed_text_recall import recover_occluded_field_lines
    path=tmp_path/'sealed.pdf'
    pixels=np.full((400,400,3),255,np.uint8)
    pixels[100:120,120:200]=[230,60,60]
    with fitz.open() as pdf:
        p=pdf.new_page(width=400,height=400)
        p.insert_image(p.rect,pixmap=fitz.Pixmap(fitz.csRGB,400,400,pixels.tobytes(),False))
        pdf.save(path)
    def block(ident,text,x0,x1):
        return TextBlock(block_id=ident,page_no=1,text=text,source='PP-OCRv6',
                         bbox=BBox(x0=x0,y0=105,x1=x1,y1=119))
    def doc(blocks):
        return Document(filename=path.name,path=str(path),page_count=1,
            pages=[Page(page_no=1,width=400,height=400,blocks=blocks)])
    a=doc([block('reference','项目名称：深能奈曼光伏电站光功率预测设备采购',60,340)])
    a.pages[0].blocks[0].block_type='paragraph_title'
    b=doc([block('prefix','项目名称：',60,120),block('suffix','功率预测设备采购',240,340)])
    class Reader:
        value='项目名称：'+own_name+'功率预测设备采购'
        confidence=.99
        def predict(self,path,**kwargs):
            with Image.open(path) as im:
                return im.size
        def payload_to_document(self,size,path):
            w,h=size
            reading=block('own',self.value,12/304*w,292/304*w)
            reading.bbox.y0=18/50*h;reading.bbox.y1=32/50*h
            reading.confidence=self.confidence
            return SimpleNamespace(pages=[SimpleNamespace(width=w,height=h,blocks=[reading])])
    reader=Reader()
    changed=recover_occluded_field_lines(a,b,reader)
    assert len(changed)==1 and len(b.pages[0].blocks)==1
    assert b.pages[0].blocks[0].text==reader.value
    assert len([e for e in changed[0].source_evidence if e.source=='occluded_field_roi_ppocr'])==2
    # Recovery is idempotent; a different real name remains different.
    assert recover_occluded_field_lines(a,b,reader)==[]
    if own_name!='深能奈曼光伏电站光':
        assert clause_diffs(a.pages[0].blocks[0].text,b.pages[0].blocks[0].text)
    reader.confidence=.6
    c=doc([block('prefix','项目名称：',60,120),block('suffix','功率预测设备采购',240,340)])
    before=c.model_dump()
    assert recover_occluded_field_lines(a,c,reader)==[] and c.model_dump()==before
