import pytest
from app.models import BBox, TextBlock, DiffItem, EvidenceBox, TextRange
from app.services.signing_region.extractor import SigningRegionExtractor as Extractor
from app.services.signing_region.models import SigningBlock, SigningElement, SigningRegion
from app.services.signing_region.comparator import SigningRegionComparator
from app.services.signing_region.diff_builder import SigningRegionDiffBuilder
from app.services.diff_events import _projection
from tests.test_signing_party_precision import region


@pytest.mark.parametrize('signature', ['伟', ''])
@pytest.mark.parametrize('label', ['法人代表或授权委托人', '授权 委托人', '受委托人'])
def test_representative_does_not_steal_company_stamp_label(signature,label):
    blocks = [TextBlock(block_id='company',page_no=1,text='甲方：测试有限公司',bbox=BBox(x0=60,y0=100,x1=280,y1=120)),
              TextBlock(block_id='representative',page_no=1,text=label+'：'+signature,bbox=BBox(x0=60,y0=220,x1=280,y1=240))]
    sb=SigningBlock(block_id='SB',page_no=1,bbox=BBox(x0=50,y0=90,x1=300,y1=300),source_block_ids=[b.block_id for b in blocks])
    fields=Extractor()._party_field_elements(sb,{b.block_id:b for b in blocks})
    assert len(fields)==1
    suffix=SigningElement(element_id='suffix',element_type='label',page_no=1,text='(盖章)',bbox=BBox(x0=80,y0=140,x1=130,y1=160),raw_ref={'party_role':'甲方'})
    Extractor._merge_party_residuals(fields,[suffix])
    assert fields[0].text=='甲方：测试有限公司(盖章)'


def test_real_principal_label_remains_supported():
    block=TextBlock(block_id='party',page_no=1,text='委托人：测试有限公司',bbox=BBox(x0=60,y0=100,x1=280,y1=120))
    sb=SigningBlock(block_id='SB',page_no=1,bbox=block.bbox,source_block_ids=['party'])
    assert Extractor()._party_field_elements(sb,{'party':block})[0].text=='甲方：测试有限公司'


def test_suffix_ownership_is_spatial_not_last_role_in_list():
    upper=region(suffix='').elements[0]
    lower=upper.model_copy(deep=True);lower.element_id='second-party';lower.bbox=BBox(x0=80,y0=250,x1=290,y1=270)
    suffix=SigningElement(element_id='suffix',element_type='label',page_no=1,text='(盖章)',bbox=BBox(x0=80,y0=130,x1=125,y1=150),raw_ref={'party_role':'甲方'})
    for fields in ([upper.model_copy(deep=True),lower.model_copy(deep=True)], [lower.model_copy(deep=True),upper.model_copy(deep=True)]):
        Extractor._merge_party_residuals(fields,[suffix])
        assert next(e for e in fields if e.element_id==upper.element_id).text.endswith('(盖章)')
        assert not next(e for e in fields if e.element_id==lower.element_id).text.endswith('(盖章)')


@pytest.mark.parametrize('direction', ['ADD','DELETE'])
def test_real_stamp_label_change_has_consistent_segment_and_event_color(direction):
    a,b=region(),region(suffix='')
    if direction=='ADD':a,b=b,a
    comparison=SigningRegionComparator().compare(a,b)
    diff=next(d for d in SigningRegionDiffBuilder().build_diffs([comparison]) if d.section_type=='signature:label')
    evidence=diff.compare_evidence if direction=='ADD' else diff.original_evidence
    assert {e.highlight_type for e in evidence}=={direction}
    event=_projection(diff)
    assert event.diff_type==direction


@pytest.mark.parametrize('direction', ['ADD','DELETE'])
def test_projection_repairs_legacy_modify_box_without_mutating_raw(direction):
    side='compare' if direction=='ADD' else 'original';other='original' if direction=='ADD' else 'compare'
    diff=DiffItem(diff_id='legacy',diff_type='MODIFY',**{f'{side}_text':'公司（盖章）',f'{other}_text':'公司',
        f'{side}_change_ranges':[TextRange(start=2,end=6,highlight_type=direction)],
        f'{side}_evidence':[EvidenceBox(page_no=1,bbox=BBox(x0=1,y0=1,x1=20,y1=20),highlight_type='MODIFY')]})
    event=_projection(diff)
    assert event.diff_type==direction
    assert getattr(event,f'{side}_evidence')[0].highlight_type==direction
    assert getattr(diff,f'{side}_evidence')[0].highlight_type=='MODIFY'
