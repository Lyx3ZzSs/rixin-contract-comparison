from app.models import BBox, CharBox, Clause, DiffItem, EvidenceBox, TextRange
from app.services.evidence_locator import EvidenceLocator


def test_exact_changed_name_keeps_location_until_cover_body_ownership_is_resolved():
    text='法定代表人：【柳文斌】'
    boxes=[CharBox(char=c,page_no=2,text_index=i,bbox=BBox(x0=100+i*10,y0=216,x1=110+i*10,y1=233)) for i,c in enumerate(text)]
    clause=Clause(clause_id='N',text=text,normalized_text=text,char_boxes=boxes)
    change=DiffItem(diff_id='body',source_type='clause',diff_type='MODIFY',compare_clause_id='N',compare_text=text,compare_snippet='柳文斌',compare_change_ranges=[TextRange(start=7,end=10,highlight_type='ADD')])
    metadata=DiffItem(diff_id='cover',source_type='metadata',diff_type='ADD',compare_text=text,compare_evidence=[EvidenceBox(page_no=2,bbox=BBox(x0=100,y0=216,x1=210,y1=233),method='cover_extra',text=text,highlight_type='ADD')])
    EvidenceLocator().locate([metadata,change],compare_clauses=[clause])
    assert [e.text for e in change.compare_evidence]==['柳文斌']
    assert change.compare_evidence[0].method=='char_exact'
    assert metadata.compare_evidence


def test_real_disconnected_name_address_changes_keep_all_same_side_boxes():
    import json
    from pathlib import Path
    import pytest
    from app.models import Document
    from app.config_models import DocumentUnderstandingSettings
    from app.services.document_preparation import DocumentPreparer
    from app.services.document_understanding import DocumentUnderstandingService
    from app.services.clause_splitter import ClauseSplitter
    root=Path(__file__).resolve().parents[2]
    paths=list((root/'storage/tasks').glob('50eaf632*/diagnostics/ocr/compare*hybrid*.json'))
    stages=list((root/'output/analysis/eight-task-fix/verified').glob('50eaf632*/ClauseDiffStage.json'))
    if not paths or not stages:
        pytest.skip('persisted OCR audit corpus not installed')
    document=Document.model_validate(json.loads(paths[0].read_text())['document'])
    DocumentPreparer().prepare(document,'compare')
    DocumentUnderstandingService(DocumentUnderstandingSettings()).understand(document,'compare')
    clauses=ClauseSplitter().split(document,'N')
    diffs=[DiffItem.model_validate(d) for d in json.loads(stages[0].read_text())]
    body=next(d for d in diffs if d.source_type=='clause' and '柳文斌' in d.compare_text)
    EvidenceLocator().locate(diffs,compare_clauses=clauses)
    assert len(body.compare_evidence)==4
    assert {e.page_no for e in body.compare_evidence}=={2}
    assert ''.join(e.text for e in body.compare_evidence)=='柳文斌地址:【山西省太原市小店区平阳路65号平阳景苑B区第5幢720号】'
    assert all(e.method=='char_exact' for e in body.compare_evidence)
    name=body.compare_evidence[0]
    assert name.bbox.x0>190 and name.bbox.x1<245 and 210<name.bbox.y0<235
