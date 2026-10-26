"""Representation repairs must conserve content and retain real changes."""
from collections import Counter

import fitz
import pytest

from app.models import BBox, CharBox, Document, Page, TextBlock, TextSourceEvidence
from app.services.document_preparation import DocumentPreparer
from app.services.document_role_repair import repair_corroborated_ocr, reconcile_body_roles
from app.services.ruled_table_recovery import recover_ruled_tables
from app.services.table_compare import TableComparator
from app.services.table_compare.parser import LogicalTableParser
from app.services.table_compare.repair import TableRepairService
from app.services.table_compare.types import LogicalCell, LogicalRow
from app.services.signing_region.extractor import FIELD_RE, SigningRegionExtractor
from app.services.signing_region.block_detector import SigningBlockDetector
from app.services.clause_splitter import ClauseSplitter
from app.services.page_alignment import PageSequenceAligner
from tests.test_document_identity_regressions import block, document
from tests.test_page_alignment import _page


@pytest.mark.parametrize('values', [
    ['合计', '6', '480000'], ['合计', '80000', '75471.70'],
    ['5.乙方应提供服务并保留数据。', '合计', '160000'],
    ['合计人民币金额200000.00元（含税价）：贰拾万圆整'],
])
def test_summary_cannot_discard_quantity_price_column_or_prose(values):
    row = LogicalRow(0, [LogicalCell(0,i,t,None,1,'b',0,i) for i,t in enumerate(values)],1,'b',0)
    assert TableRepairService()._split_merged_summary_row(row,9) is None


def _ruled_document(tmp_path, name, values):
    pdf = fitz.open()
    page = pdf.new_page(width=400,height=400)
    for x in [40,200,360]:
        page.draw_line((x,60),(x,300))
    for y in [60,140,220,300]:
        page.draw_line((40,y),(360,y))
    for index, text in enumerate(values):
        page.insert_text((50+160*(index%2),100+80*(index//2)),text,fontsize=12)
    path = tmp_path / name
    pdf.save(path)
    chars = []
    text = ''
    for b in page.get_text('rawdict')['blocks']:
        for line in b.get('lines',[]):
            for span in line['spans']:
                for c in span['chars']:
                    x0,y0,x1,y1 = c['bbox']
                    chars.append(CharBox(char=c['c'],page_no=1,text_index=len(text),bbox=BBox(x0=x0,y0=y0,x1=x1,y1=y1)))
                    text += c['c']
            text += '\n'
    pdf.close()
    b = TextBlock(block_id=name,text=text,block_type='table',page_no=1,
                  bbox=BBox(x0=40,y0=60,x1=360,y1=300),char_boxes=chars,
                  raw_html='<table><tr><td>incorrect grid</td></tr></table>')
    return Document(filename=name,path=str(path),page_count=1,pages=[Page(page_no=1,width=400,height=400,blocks=[b])])


@pytest.mark.parametrize('changed', ['100','101','-100','shall not pay'])
@pytest.mark.parametrize('workers', [1, 2])
def test_visible_grid_owns_characters_and_preserves_real_changes(tmp_path, changed, workers):
    left = _ruled_document(tmp_path,'left.pdf',['Price','Tax','100','6','Total','106'])
    right = _ruled_document(tmp_path,'right.pdf',['Price','Tax',changed,'6','Total','106'])
    for doc in [left,right]:
        before = Counter(c for c in doc.pages[0].blocks[0].text if not c.isspace())
        assert len(recover_ruled_tables(doc, max_inflight=workers)) == 1
        tables = LogicalTableParser().parse_tables(LogicalTableParser().table_blocks(doc))
        assert tables[0].col_count == 2
        assert Counter(c for row in tables[0].rows for cell in row.cells for c in cell.text if not c.isspace()) == before
    diffs,_ = TableComparator().build_diffs(left,right)
    assert bool(diffs) == (changed != '100')
    if changed != '100':
        assert any(changed.replace(' ','') in d.compare_text.replace(' ','') for d in diffs)


def test_incomplete_character_evidence_cannot_replace_grid(tmp_path):
    doc = _ruled_document(tmp_path,'partial.pdf',['Price','Tax','100','6','Total','106'])
    b = doc.pages[0].blocks[0]
    b.char_boxes.pop()
    old = b.raw_html
    assert not recover_ruled_tables(doc)
    assert b.raw_html == old


def test_service_paragraph_is_not_a_table_section_title():
    cell = LogicalCell(0,2,'乙方应提供软件升级服务，并在功率预测系统发生故障时处理。',None,2,'b',0,2)
    assert LogicalTableParser()._section_title([cell],9) == ''


def test_blank_form_labels_are_not_date_values():
    text = '计划开工日期： 计划竣工日期： 本项目现场负责人姓名： 承包人： 20**年**月**日'
    matches = [m.groupdict() for m in FIELD_RE.finditer(text)]
    assert matches[:3] == [dict(label=k,value='') for k in ['计划开工日期','计划竣工日期','本项目现场负责人姓名']]
    assert SigningRegionExtractor._standalone_field_label('签字；') == '签字'
    filled = list(FIELD_RE.finditer('签订日期：2026年9月7日 盖章：'))
    assert filled[0].group('value').strip() == '2026年9月7日'


def test_missing_party_heading_does_not_truncate_signing_fields():
    detector = SigningBlockDetector()
    assert detector._should_extend_signing_continuation([
        block('法定代表人/授权代表（签字）'),block('签订日期：',y=135)])
    assert not detector._should_extend_signing_continuation([block('双方应按签订日期履行义务。')])


def test_section_role_survives_page_break_and_resets_at_main_heading():
    doc = document([block('附件一：保廉合同',kind='paragraph_title')])
    doc.pages += [Page(page_no=2,width=600,height=800,blocks=[block('格等处理措施。',page=2)]),
                  Page(page_no=3,width=600,height=800,blocks=[block('第一条 合同价款',page=3)])]
    doc.page_count = 3
    DocumentPreparer().prepare(doc,'original')
    assert doc.pages[1].blocks[0].block_role == 'appendix_section'
    assert doc.pages[2].blocks[0].block_role != 'appendix_section'


def test_body_recovery_needs_text_and_geometry():
    anchor = '根据合同第三条第3款约定：'
    left = document([]);right = document([])
    left.pages = [_page(2,anchor)]
    right.pages = [_page(2,anchor)]
    lost = block('。现我方',x=420,y=81,page=2);lost.flow_role='noise'
    peer = block('。现我方',x=420,y=81,page=2)
    left.pages[0].blocks.append(lost);right.pages[0].blocks.append(peer)
    assert reconcile_body_roles(left,right)
    assert lost.flow_role == 'body'
    lost.flow_role='noise';peer.bbox.y0 += 150;peer.bbox.y1 += 150
    assert not reconcile_body_roles(left,right)


def test_nested_article_reference_is_continuation():
    splitter = ClauseSplitter()
    assert splitter._is_article_reference_continuation('第三条第3款约定：',('第三条','第3款约定：'))
    assert not splitter._is_article_reference_continuation('第三条 付款条件',('第三条','付款条件'))


@pytest.mark.parametrize('corroborated', [True,False])
def test_uppercase_money_glyph_requires_same_region_corroboration(corroborated):
    b = block('大写（壹万贰任叁佰肆拾元伍角整）')
    b.char_boxes = [CharBox(char=c,page_no=1,text_index=i,bbox=b.bbox) for i,c in enumerate(b.text)]
    if corroborated:
        b.source_evidence = [TextSourceEvidence(source='ppstructure',raw_text='12340.50元，大写（壹万贰仟叁佰肆拾元伍角整）',bbox=b.bbox,page_no=1,confidence=1)]
    repair_corroborated_ocr(document([b]).pages[0])
    assert ('仟' in b.text) == corroborated


@pytest.mark.parametrize('before,after', [
    ('乙方应按照合同约定及时提供技术服务并承担维护责任。','乙方不应按照合同约定及时提供技术服务并承担维护责任。'),
    ('乙方每15分钟上报一次预测数据，并保证全年正常运行。','乙方每16分钟上报一次预测数据，并保证全年正常运行。'),
    ('乙方应在合同签订后10日内完成全部设备安装调试。','乙方应在合同签订后100日内完成全部设备安装调试。'),
    ('amount:100.00','amount:-100.00'),
])
def test_similarity_does_not_prove_numeric_or_obligation_equivalence(before,after):
    from app.services.table_compare.matcher import TableMatcher
    assert not TableMatcher().is_similar_ocr_noise(before,after)
