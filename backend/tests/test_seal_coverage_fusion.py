"""Coverage and visual fusion must agree on a surviving physical seal owner."""
import pytest

from app.models import BBox, DiffItem, Document, EvidenceBox, Page
from app.services.extractors.base import ExtractionResult
from app.services.pipeline_stages import SigningRegionStage, SummaryStage
from app.services.signing_region.models import SigningVisualObject, SigningVisualObjectType
from app.services.signing_region.visual_recall import SigningVisualRecallResult
from app.services.diff_events import event_response
from tests.test_signing_region_pipeline import _ctx, _TestArtifactStore, _NoCandidateDetector


@pytest.mark.parametrize('page_no', [20,30])
@pytest.mark.parametrize('legacy_confidence', [.9,.99])
def test_fused_personal_seal_survives_real_stage_summary_and_event_projection(monkeypatch,tmp_path,page_no,legacy_confidence):
    ctx=_ctx(tmp_path)
    doc=Document(filename='test.pdf',path='test.pdf',page_count=page_no,
                 pages=[Page(page_no=page_no,width=598,height=820,blocks=[])])
    ctx.original_extraction=ExtractionResult(document=doc.model_copy(deep=True),extractor_used='test')
    ctx.compare_extraction=ExtractionResult(document=doc.model_copy(deep=True),extractor_used='test')
    box=BBox(x0=235.3,y0=144,x1=287.3,y1=194)
    legacy=DiffItem(diff_id='legacy',diff_type='ADD',source_type='seal',compare_text='印章文字',
        compare_evidence=[EvidenceBox(page_no=page_no,bbox=box,method='seal_region',confidence=legacy_confidence)])
    signing=DiffItem(diff_id='signing',diff_type='ADD',source_type='signing_region',section_type='signature:seal',
        compare_text='seal',review_flags=['SIGNING_SEAL_CHANGE'],
        compare_evidence=[EvidenceBox(page_no=page_no,bbox=box,method='signing_region_element',confidence=.95)])
    ctx.seal_diffs=[legacy]
    visual=SigningVisualObject(object_id='own-name-seal',object_type=SigningVisualObjectType.PERSONAL_NAME_SEAL,
        page_no=page_no,bbox=box,visual_detection_confidence=.9,party_role='甲方',party_attribution_confidence=.8)
    store=_TestArtifactStore(tmp_path/'artifacts')
    stage=SigningRegionStage(artifact_store=store,visual_enabled=True)
    stage.block_detector=_NoCandidateDetector()
    monkeypatch.setattr(stage.diff_builder,'build_diffs',lambda *a,**kw:[signing])
    monkeypatch.setattr(stage.whole_page_visual_recall,'compare',
                        lambda *a,**kw:SigningVisualRecallResult(additions=[visual]))
    stage.execute(ctx)
    ctx.diffs=[*ctx.seal_diffs,*ctx.signing_region_diffs]
    SummaryStage(artifact_store=store).execute(ctx)
    assert len(ctx.task.diffs)==1
    diff=ctx.task.diffs[0]
    assert diff.match_score_details['object_type']=='PERSONAL_NAME_SEAL'
    assert diff.compare_evidence[0].page_no==page_no
    assert diff.compare_evidence[0].bbox==box
    response=event_response(ctx.task)
    assert len(response['events'])==1
    assert response['events'][0]['diff']['compare_evidence'][0]['page_no']==page_no
