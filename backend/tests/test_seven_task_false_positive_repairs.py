"""Physical-object and source-ownership regressions, including negative controls."""
import json
from pathlib import Path

import pytest

from app.models import BBox, DiffItem, Document, Page, TextBlock
from app.services.signing_region.models import SigningVisualObject, SigningVisualObjectType as Kind
from app.services.signing_region.visual_recall import WholePageSigningVisualRecall as Recall
from app.services.diff_quality import DiffQualityProcessor
from app.services.pipeline_stages import SigningRegionStage
from app.services.signing_region.block_detector import SigningBlockDetector
from tests.test_signing_visual_recall import _object, _block, _document, _write_pdf

ROOT = Path(__file__).resolve().parents[2]


def saved(prefix):
    path = ROOT / f'output/analysis/seven-task-root-cause-20260908/{prefix}-saved-result.json'
    if not path.exists():
        pytest.skip('local audit corpus unavailable')
    return json.loads(path.read_text())


@pytest.mark.parametrize('partial', [(225.5,397.5,274.5,425.5), (253.5,143.9,287.4,193.6)])
def test_name_seal_truncated_row_or_column_is_one_object(partial):
    full = (223.875,375.847,277.845,427.159) if partial[1] > 300 else (235.333,144,287.333,194)
    obj = _object(Kind.PERSONAL_NAME_SEAL, bbox=BBox(x0=full[0],y0=full[1],x1=full[2],y1=full[3]))
    from app.models import EvidenceBox
    diff = DiffItem(diff_id='partial',diff_type='ADD',source_type='seal',compare_evidence=[EvidenceBox(page_no=1,bbox=BBox(x0=partial[0],y0=partial[1],x1=partial[2],y1=partial[3]))])
    assert Recall._same_physical_object(obj,diff)
    diff.compare_evidence[0].bbox = BBox(x0=180,y0=100,x1=320,y1=450)
    assert not Recall._same_physical_object(obj,diff)
    diff.compare_evidence[0].bbox = BBox(x0=full[0]-10,y0=full[1]-10,x1=full[2]+10,y1=full[3]+10)
    assert not Recall._same_physical_object(obj,diff)


def test_nested_edge_contours_merge_but_separate_seals_survive():
    outer = _object(Kind.COMPANY_ROUND_SEAL,bbox=BBox(x0=554,y0=111,x1=600,y1=223))
    inner = _object(Kind.EDGE_SEAM_SEAL,bbox=BBox(x0=556,y0=152,x1=571,y1=173))
    outer.raw_ref['reason']='PAGE_EDGE_ROUND_SEAL'
    inner.raw_ref['reason']='PAGE_EDGE_RED_FRAGMENT'
    independent = _object(Kind.PERSONAL_NAME_SEAL,bbox=inner.bbox)
    assert Recall._dedupe_objects([inner,outer,independent]) == [outer,independent]


def test_printed_next_field_cannot_be_a_signature(tmp_path):
    def draw(page):
        page.insert_text((190,325),'0551-87388200',fontsize=24)
        page.insert_text((190,355),'BANK ACCOUNT',fontsize=24)
    path = _write_pdf(tmp_path,'printed.pdf',draw)
    doc = _document(path,[_block('rep','甲方：法人代表或授权委托人：',BBox(x0=80,y0=225,x1=240,y1=245)),
        _block('sign','(签字)',BBox(x0=193,y0=257,x1=245,y1=279)),
        _block('fax','传真：',BBox(x0=80,y0=288,x1=124,y1=311)),
        _block('phone','电话：0551-87388200',BBox(x0=84,y0=321,x1=330,y1=339))])
    assert not [o for o in Recall().detect(doc) if o.object_type == Kind.HANDWRITTEN_SIGNATURE]


def test_body_heading_and_execution_clause_cannot_be_absorbed():
    anchor = BBox(x0=50,y0=100,x1=550,y1=600)
    for text in ['七、其它','本合同一式肆份，甲乙双方各执贰份，经双方法定代表人签字']:
        b = _block('body',text,BBox(x0=80,y0=130,x1=500,y1=150))
        assert not SigningRegionStage._absorbable_signing_fragment(anchor,b)


def test_blank_cover_field_merges_with_actual_fill_and_keeps_change():
    diffs = [DiffItem.model_validate(d) for d in saved('8ed27b94')['diffs'] if d['diff_id'] in {'D001','D020'}]
    decisions=[]
    kept=DiffQualityProcessor()._dedupe_cover_fields_owned_by_body(diffs,decisions)
    assert [d.diff_id for d in kept] == ['D020']
    assert '柳文斌' in kept[0].compare_text
    diffs[1].compare_change_ranges=[]
    assert len(DiffQualityProcessor()._dedupe_cover_fields_owned_by_body(diffs,[])) == 2


def test_line_geometry_ignores_oversized_parent():
    b=_block('line','七、其它',BBox(x0=50,y0=120,x1=120,y1=140))
    b.layout_bbox=BBox(x0=50,y0=120,x1=550,y1=600)
    assert SigningBlockDetector._effective_bbox(b)==b.bbox


def test_total_label_ocr_conflict_cannot_be_confirmed_as_business_change():
    from app.services.table_compare.parser import LogicalTableParser
    from app.services.table_compare.repair import TableRepairService
    from app.services.table_compare.diff_builder import TableDiffBuilder
    parser=LogicalTableParser()
    b=_block('table','合计\n国\n有限公司\n240000',BBox(x0=80,y0=100,x1=500,y1=300),block_type='table')
    html='<table><tr><td colspan="4">合计 国 可</td><td>240000</td></tr></table>'
    b.raw_html=html
    tables=parser.parse_tables([(b,html)])
    cell=tables[0].get_cell(0,0)
    assert cell.observation_reason=='TABLE_LABEL_OCR_CONFLICT'
    assert TableDiffBuilder._cell_observation(tables[0],0,0,cell.text).state.value=='UNOBSERVED'
    assert TableDiffBuilder._cell_observation(tables[0],0,4,'240001').state.value=='OBSERVED_VALUE'
    logical=parser._logical_cells(tables[0],0,0)
    assert logical[0].observation_reason==cell.observation_reason
    from app.services.table_compare import TableComparator
    original=_document(Path('original.pdf'),[b])
    compare=original.model_copy(deep=True)
    compare.pages[0].blocks[0].raw_html=html.replace('国 可','国 四').replace('240000','240001')
    compare.pages[0].blocks[0].text=b.text.replace('240000','240001')
    diffs,_=TableComparator().build_diffs(original,compare)
    assert any('240001' in d.compare_text for d in diffs)
    assert all(d.match_score_details['change_decision']=='NEEDS_REVIEW'
               for d in diffs if '合计 国' in d.original_text)


def test_same_short_table_values_on_different_pages_do_not_merge():
    from app.models import EvidenceBox
    diffs=[DiffItem(diff_id=str(p),source_type='table',diff_type='ADD',compare_text='1 | 1',compare_snippet='1 | 1',
                   compare_evidence=[EvidenceBox(page_no=p,bbox=BBox(x0=100,y0=100,x1=140,y1=120))]) for p in [21,27,28]]
    assert len(DiffQualityProcessor()._dedupe_cross_source(diffs,[]))==3
    diffs[1].compare_evidence[0].page_no=21
    assert len(DiffQualityProcessor()._dedupe_cross_source(diffs,[]))==2


@pytest.mark.parametrize('title_type',['text','paragraph_title','doc_title','title'])
def test_attachment_catalog_preserves_date_across_title_representations(title_type):
    from app.models import EvidenceBox
    from app.services.clause_splitter import ClauseSplitter, ClauseUnit
    def unit(text,pn,kind):
        box=BBox(x0=80,y0=100,x1=500,y1=125)
        return ClauseUnit(text=text,char_boxes=[],page_no=pn,block_id=text,block_type=kind,bbox=box,
                          evidence=EvidenceBox(page_no=pn,bbox=box),section_type='appendix')
    units=[unit('十五、争议解决',35,'paragraph_title'),unit('双方应协商解决合同争议。',35,'text'),
           unit('附件五：技术协议',36,title_type),unit('新能源功率预测系统',36,'doc_title'),
           unit('技术协议',36,'doc_title'),unit('签订日期：2026年5月6日',36,'text')]
    clauses,_=ClauseSplitter()._detect_clause_items(units)
    assert len(clauses)==1
    assert '签订日期：2026年5月6日' in clauses[0].texts


def test_missing_attachment_heading_does_not_append_body_to_cover():
    from app.models import EvidenceBox
    from app.services.clause_splitter import ClauseSplitter, ClauseUnit
    def unit(text,pn,kind):
        box=BBox(x0=80,y0=100,x1=500,y1=125)
        return ClauseUnit(text=text,char_boxes=[],page_no=pn,block_id=text,block_type=kind,bbox=box,
                          evidence=EvidenceBox(page_no=pn,bbox=box),section_type='appendix')
    units=[unit('九、争议解决',11,'paragraph_title'),unit('技术协议（专用部分）',17,'doc_title'),unit('甲方：采购公司\n乙方：供货公司',17,'text'),
           unit('(2) GB/T 20046-2006',18,'text'),unit('《光伏系统电网接口特性》;',18,'text')]
    clauses,_=ClauseSplitter()._detect_clause_items(units)
    assert len(clauses)==3
    assert not any('GB/T' in text for text in clauses[1].texts)
    assert '(2) GB/T 20046-2006' in clauses[2].texts
    units[3]=unit('(2) GB/T 20046-2006',17,'text')
    assert len(ClauseSplitter()._detect_clause_items(units[:4])[0])==2
