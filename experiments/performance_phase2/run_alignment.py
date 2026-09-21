"""Offline B stage differential, using frozen real OCR + unchanged preparation."""
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from app.config import settings
from app.models import Document
from app.services.document_understanding import DocumentUnderstandingService
from app.services.document_preparation import DocumentPreparer
from app.services.native_heading_repair import NativeHeadingRepairService
from app.services.repeated_overlay_filter import RepeatedOverlayFilter
from app.services.page_alignment import PageSequenceAligner
from anchor_bound import AnchorBoundAligner

OUT=ROOT/'output/analysis/perf-phase2-20260921/alignment'
OUT.mkdir(parents=True,exist_ok=True)
source=ROOT/'output/analysis/e2e-20260921/profile-B/runtime/tasks/profile-B/diagnostics'
docs=[]
for side in ('original','compare'):
    raw=json.loads((source/f'ocr/B-{side}_ppstructure_ocr_hybrid_raw.json').read_text())
    document=Document.model_validate(raw['document'])
    NativeHeadingRepairService().repair(document)
    RepeatedOverlayFilter().apply(document)
    docs.append(document)
DocumentUnderstandingService(settings.document_understanding).understand_pair(*docs)
DocumentPreparer(image_analysis_max_inflight=settings.compare_image_analysis_max_inflight).prepare_pair(*docs)
expected=json.loads((source/'debug/page_alignment.json').read_text())
rows=[]
for repeat in range(1,4):
    baseline=None
    for label,cls in [('baseline',PageSequenceAligner),('candidate',AnchorBoundAligner)]:
        start=time.perf_counter();cpu=time.process_time()
        result=cls().align(*docs)
        row={'variant':label,'repeat':repeat,'seconds':time.perf_counter()-start,'cpu_seconds':time.process_time()-cpu,'performance':result.performance,'mapping':result.to_debug_payload()}
        if label=='baseline':
            baseline=result.to_debug_payload()
            row['matches_first_stage_mapping']=baseline==expected
        else:
            row['exact_mapping_equal']=result.to_debug_payload()==baseline
            assert row['exact_mapping_equal'], 'Stop experiment: page mapping changed'
        rows.append(row)
        (OUT/'B-stage.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False))
        print(label,repeat,round(row['seconds'],3),result.performance['counters'],flush=True)
