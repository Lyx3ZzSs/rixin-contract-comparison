from io import BytesIO

import fitz
from PIL import Image

from app.models import BBox, EvidenceBox
from app.services.cover.extra_text import merge_extra_fragments
from app.services.cover.types import CoverExtraFragment
from app.services.extractors.contract_v3.artifact import merge_native
from app.services.extractors.contract_v3.native import NativeReader
from app.services.extractors.contract_v3.ocr import coverage_plan, SelectiveOCRRouter
from app.config import Settings


def test_hidden_search_layer_is_replaced_by_own_raster_ocr(tmp_path):
    source = fitz.open()
    page = source.new_page(width=200, height=100)
    page.insert_text((20, 45), 'Actual words', fontsize=14)
    raster = page.get_pixmap().tobytes('png')
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    page.insert_image(page.rect, stream=raster)
    page.insert_text((20, 45), 'Wrong words', fontsize=14, render_mode=3)
    sample, raw, image, png = NativeReader().read_page(page, source='scan.pdf', digest='abc', page_no=1)
    artifact = merge_native(sample, raw, [])
    artifact['coverage'] = coverage_plan(artifact, sample)
    assert artifact['coverage']['route'] == 'SCANNED'
    assert not NativeReader.has_native_chars(raw)
    assert artifact['native_chars'] and not any(c['usable'] for c in artifact['native_chars'])

    class OCR(SelectiveOCRRouter):
        def _request(self, png):
            # Hidden glyphs must not white-out the real pixels before inference.
            assert Image.open(BytesIO(png)).tobytes() == image.tobytes()
            return {'result': {'ocrResults': [{'prunedResult': {
                'rec_texts': ['Actual words'], 'rec_scores': [.99],
                'rec_polys': [[[40, 60], [190, 60], [190, 95], [40, 95]]],
            }}]}}
    OCR(Settings(_env_file=None)).recognize(artifact, sample, image)
    assert [b['text'] for b in artifact['blocks']] == ['Actual words']
    assert artifact['blocks'][0]['source'] == 'PP-OCRv6'
    assert not artifact['errors']


def test_visible_glyphs_survive_alongside_hidden_search_layer():
    doc = fitz.open(); page = doc.new_page()
    page.insert_text((30, 40), 'Visible')
    page.insert_text((30, 80), 'Hidden', render_mode=3)
    sample, raw, _, _ = NativeReader().read_page(page, source='mixed.pdf', digest='abc', page_no=1)
    artifact = merge_native(sample, raw, [])
    assert NativeReader.has_native_chars(raw)
    assert ''.join(b['text'] for b in artifact['blocks']) == 'Visible'


def test_zero_opacity_native_does_not_mask_raster():
    doc=fitz.open();page=doc.new_page();page.insert_text((30,40),'Invisible',fill_opacity=0)
    sample,raw,_,_=NativeReader().read_page(page,source='transparent.pdf',digest='abc',page_no=1)
    artifact=merge_native(sample,raw,[])
    assert not any(c['usable'] for c in artifact['native_chars'])


def fragment(text, box, id, layout='same-layout'):
    return CoverExtraFragment(value=text,evidence=EvidenceBox(page_no=1,bbox=BBox(x0=box[0],y0=box[1],x1=box[2],y1=box[3]),text=text),block_id=id,layout_block_id=layout)


def test_tall_search_layer_boxes_do_not_reverse_adjacent_whole_lines():
    a=fragment('依照有关法律，遵循平等、自愿、',[99.349,268.2515,516.35,303.5995],'first')
    b=fragment('公平和诚实信用的原则',[75.099,293.7947,521.85,325.8725],'second')
    assert [e.value for e in merge_extra_fragments([a,b])] == [a.value.replace('，', ','),b.value]


def test_true_horizontal_field_fragments_still_join():
    a=fragment('地址：',[30,100,60,114],'label')
    b=fragment('北京市海淀区',[64,102,160,116],'value')
    assert [e.value for e in merge_extra_fragments([a,b])] == ['地址:北京市海淀区']


def test_small_overlapping_noise_does_not_regroup_neighboring_cover_fields():
    fragments = [fragment('Cakilur70260513',[344,5.5,493,58],'a',''),
                 fragment('N2o',[493.5,7.5,539.5,33.5],'b',''),
                 fragment('正',[450,13.5,546,78.5],'c','')]
    assert [e.value for e in merge_extra_fragments(fragments)] == ['Cakilur70260513','正','N2o']
