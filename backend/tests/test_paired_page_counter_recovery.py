"""Page-counter identity must survive scan transforms without swallowing body text."""
import pytest

from app.models import BBox, Document, Page, TextBlock, TextSourceEvidence
from app.services.document_preparation import DocumentPreparer
from app.services.cover import CoverMetadataComparator
from app.services.page_number_evidence import page_counters, match_page_counters


def block(id,text,x,y,width=140,kind='text',role='text',confidence=1):
    return TextBlock(block_id=id,page_no=1,text=text,block_type=kind,block_role=role,
        confidence=confidence,bbox=BBox(x0=x,y0=y,x1=x+width,y1=y+14),source='native_pdf')


def pair(ratio=.87):
    anchors=[block('title','功率预测服务合同',80,100,200,'doc_title'),
             block('party-a','委托人：测试甲方有限公司',340,300),
             block('party-b','承包人：测试乙方有限公司',80,480),
             block('place','签订地点：广东省广州市',330,650)]
    originals=[b.model_copy(deep=True) for b in anchors]
    originals.extend([block('counter','- 1 ',289,925,10,'number'),block('dash','-',299,925,4)])
    factor=ratio/.925
    comparisons=[b.model_copy(deep=True) for b in anchors]
    comparisons.append(block('counter','-1-',289,925,14))
    for b in comparisons:
        b.bbox=b.bbox.model_copy(update={'y0':b.bbox.y0*factor,'y1':b.bbox.y1*factor})
    def doc(name,blocks):
        return Document(path=f'/nonexistent/{name}.pdf',filename=f'{name}.pdf',page_count=1,
                        pages=[Page(page_no=1,width=600,height=1000,blocks=blocks)])
    return doc('original',originals),doc('compare',comparisons)


def false_adds(original,compare):
    return [d for d in CoverMetadataComparator().build_diffs(original,compare)
            if d.title=='封面额外文本' and d.compare_text=='-1-']


def test_preparation_recovers_shifted_counter_before_cover_comparison():
    original,compare=pair()
    DocumentPreparer().prepare_pair(original,compare)
    assert not false_adds(original,compare)
    assert compare.pages[0].blocks[-1].block_role=='page_footer'
    assert compare.pages[0].blocks[-1].enter_clause_compare is False
    assert len(match_page_counters(original.pages[0],compare.pages[0]))==1


@pytest.mark.parametrize('ratio',[.76,.80,.84,.87,.89,.94])
def test_scan_vertical_scale_does_not_change_counter_identity(ratio):
    original,compare=pair(ratio)
    DocumentPreparer().prepare_pair(original,compare)
    assert not false_adds(original,compare)
    assert len(match_page_counters(original.pages[0],compare.pages[0]))==1


def test_split_target_counter_marks_only_its_adjacent_fragments():
    original,compare=pair()
    p=compare.pages[0];counter=p.blocks.pop();y=counter.bbox.y0
    p.blocks.extend([block('left','-',289,y,4),block('digit','1',293,y,6),block('right','-',299,y,4),
                     block('other-dash','-',500,y,4)])
    DocumentPreparer().prepare_pair(original,compare)
    assert all(b.block_role=='page_footer' for b in p.blocks if b.block_id in {'left','digit','right'})
    assert next(b for b in p.blocks if b.block_id=='other-dash').block_role!='page_footer'
    assert len(match_page_counters(original.pages[0],p))==1


def test_recovery_works_on_original_side_and_does_not_change_text():
    from app.services.page_number_evidence import paired_counter_role_recoveries
    native,scan=pair()
    before=scan.model_dump()
    proposals=paired_counter_role_recoveries(scan,native)
    assert [(side,b.block_id) for side,b,ref in proposals]==[('original','counter')]
    assert scan.model_dump()==before  # Discover first, apply only in preparation.


@pytest.mark.parametrize('variant',['negative','bare-number','table-cell','inside-table','same-row-body',
                                    'body-below','wrong-slot','missing-anchors','low-confidence',
                                    'conflicting-reading','changed-counter','duplicate','both-untyped','reference-table'])
def test_unproven_candidates_are_not_promoted(variant):
    from app.services.page_number_evidence import paired_counter_role_recoveries
    original,compare=pair();page=compare.pages[0];target=page.blocks[-1]
    if variant=='negative':target.text='-1'
    elif variant=='bare-number':target.text='1'
    elif variant=='table-cell':target.block_type='table_cell'
    elif variant=='inside-table':page.blocks.append(block('table','表格',250,860,100,'table'))
    elif variant=='same-row-body':page.blocks.append(block('label','温度测量值',200,target.bbox.y0,60))
    elif variant=='body-below':page.blocks.append(block('body','其他合同约定',80,900))
    elif variant=='wrong-slot':target.bbox=target.bbox.model_copy(update={'x0':480,'x1':494})
    elif variant=='missing-anchors':page.blocks=page.blocks[1:]
    elif variant=='low-confidence':target.confidence=.7
    elif variant=='conflicting-reading':target.source_evidence=[TextSourceEvidence(source='other',raw_text='-2-',page_no=1,bbox=target.bbox)]
    elif variant=='changed-counter':target.text='-2-'
    elif variant=='duplicate':page.blocks.append(target.model_copy(update={'block_id':'duplicate'}))
    elif variant=='reference-table':original.pages[0].blocks[-2].block_type='table_cell'
    elif variant=='both-untyped':
        original.pages[0].blocks[-2].block_type='text'
        for b in original.pages[0].blocks[-2:]:b.bbox=b.bbox.model_copy(update={'y0':870,'y1':884})
    before=compare.model_dump()
    assert paired_counter_role_recoveries(original,compare)==[]
    assert compare.model_dump()==before


def test_changed_counter_is_not_filtered_as_equal_cover_text():
    original,compare=pair();compare.pages[0].blocks[-1].text='-2-'
    DocumentPreparer().prepare_pair(original,compare)
    assert match_page_counters(original.pages[0],compare.pages[0])==[]
    assert any(d.compare_text=='-2-' for d in CoverMetadataComparator().build_diffs(original,compare))
