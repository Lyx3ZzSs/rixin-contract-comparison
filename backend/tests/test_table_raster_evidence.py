"""A false empty column must fail without rejecting a real empty column."""

import copy
import json
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from app.services.extractors.contract_v3.table_raster_evidence import validate_raster_evidence
from app.services.extractors.contract_v3.tables import _publish, _tokens


ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / 'output/analysis/mineru-unet-validation-20261005'
RID = 'PO202604090100-compare-p1:v3:13'


def real_false_column():
    manifest = json.loads((FROZEN/'holdout/input-manifest.json').read_text())
    region = next(r for r in manifest['regions'] if r['region_id']==RID)
    artifact = json.loads((FROZEN/'holdout/selected'/f'{RID}.json').read_text())
    return artifact, region, Image.open(ROOT/region['crop_path']).convert('RGB')


@pytest.mark.skipif(not (FROZEN/'holdout/input-manifest.json').exists(), reason='frozen experiment missing')
def test_real_thirteenth_column_has_no_independent_boundary_evidence():
    artifact,region,image=real_false_column()
    assert artifact['cols']==13  # Original crop has 12; regression source is unchanged.
    result=validate_raster_evidence(artifact,image,region['ocr'])
    assert not result.usable
    assert 'UNSUPPORTED_BLANK_EDGE_CELL' in result.reasons
    # Only the load-bearing empty exterior cell is sufficient to reproduce it.
    exterior=next(c for c in artifact['cells'] if c['col']==12)
    assert not validate_raster_evidence(dict(cols=13,cells=[exterior]),image,[]).usable


@pytest.mark.skipif(not (FROZEN/'holdout/input-manifest.json').exists(), reason='frozen experiment missing')
def test_pixel_rejection_reaches_real_publication_and_clears_structured_fields():
    candidate,region,image=real_false_column()
    page=json.loads((ROOT/region['page_artifact']).read_text())
    table_region=next(r for r in page['regions'] if r['region_id']==RID)
    page['blocks']=[b for b in page['blocks'] if b['region_id']==RID]
    cells=[dict(cell_id=c['cell_id'],row=c['row'],column=c['col'],rowspan=c['rowspan'],colspan=c['colspan'],bbox=c['pdf_page_bbox']) for c in candidate['cells']]
    # Reproduce the previous false accept at the actual publication boundary.
    old=_publish(page,table_region,copy.deepcopy(cells),_tokens(page,table_region),'MINERU4_UNET_EXPERIMENTAL')
    assert old['status']=='CONSISTENT' and old['html']
    before=[b['text'] for b in page['blocks']]
    evidence=validate_raster_evidence(candidate,image,region['ocr'])
    new=_publish(page,table_region,copy.deepcopy(cells),_tokens(page,table_region),'MINERU4_UNET_EXPERIMENTAL',evidence.reasons)
    assert new['status']=='TABLE_STRUCTURE_UNRESOLVED'
    assert new['html'] is None and not new['cells']
    assert all(not b.get('raw_html') and not b.get('table_cell_bboxes') for b in page['blocks'])
    assert before==[b['text'] for b in page['blocks']]


@pytest.mark.skipif(not (FROZEN/'holdout/input-manifest.json').exists(), reason='frozen experiment missing')
def test_real_blank_name_column_with_inset_cell_box_is_preserved():
    name='HUABEI_FIVE_STATIONS-compare-p25:v3:2'
    manifest=json.loads((FROZEN/'holdout/input-manifest.json').read_text())
    region=next(r for r in manifest['regions'] if r['region_id']==name)
    artifact=json.loads((FROZEN/'holdout/selected'/f'{name}.json').read_text())
    image=Image.open(ROOT/region['crop_path']).convert('RGB')
    assert artifact['cols']==4
    assert validate_raster_evidence(artifact,image,region['ocr']).usable


@pytest.mark.parametrize('scale',[1,2])
def test_real_empty_outer_column_and_merged_note_are_preserved(scale):
    image=Image.new('RGB',(260*scale,200*scale),'white')
    draw=ImageDraw.Draw(image)
    def line(coords):draw.line([int(v*scale) for v in coords],fill='black',width=2*scale)
    for y in [10,70,130,190]:line([10,y,250,y])
    for x in [10,90,180,250]:line([x,10,x,130])
    line([10,130,10,190]);line([250,130,250,190])
    cells=[]
    for row,(y0,y1) in enumerate([(10,70),(70,130)]):
        for col,(x0,x1) in enumerate([(10,90),(90,180),(180,250)]):
            cells.append(dict(cell_id=str(len(cells)),row=row,col=col,rowspan=1,colspan=1,bbox=[v*scale for v in [x0+2,y0+2,x1-2,y1-2]]))
    cells.append(dict(cell_id='note',row=2,col=0,rowspan=1,colspan=3,bbox=[v*scale for v in [12,132,248,188]]))
    artifact=dict(rows=3,cols=3,cells=cells)
    # Real empty cells need no text; independent rules are their evidence.
    assert validate_raster_evidence(artifact,image,[]).usable
