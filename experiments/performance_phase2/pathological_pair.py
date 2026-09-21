"""One actual longest-clause pair. Parent process enforces a 300 s timeout."""
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
from app.services.clause_splitter import ClauseSplitter
from app.services.matching.candidate_scoring import CandidateScoringMixin
from score_cutoff import ExactCutoffScoring

source=ROOT/'output/analysis/e2e-20260921/runtime-C/tasks/42e90acf-021e-429d-a03f-e29ad79fa2ff/diagnostics/ocr'
out=Path(sys.argv[2]);out.mkdir(parents=True,exist_ok=True)
docs=[]
for side in ['original','compare']:
    doc=Document.model_validate(json.loads((source/f'C-{side}_ppstructure_ocr_hybrid_raw.json').read_text())['document'])
    NativeHeadingRepairService().repair(doc);RepeatedOverlayFilter().apply(doc);docs.append(doc)
DocumentUnderstandingService(settings.document_understanding).understand_pair(*docs)
DocumentPreparer(image_analysis_max_inflight=settings.compare_image_analysis_max_inflight).prepare_pair(*docs)
clauses=[ClauseSplitter().split(doc,side) for doc,side in zip(docs,['O','N'])]
longest=[max(side,key=lambda c:len(c.text)) for side in clauses]
texts=[c.match_text or c.normalized_text for c in longest]
dimensions=dict(variant=sys.argv[1],clause_counts=[len(c) for c in clauses],text_lengths=[len(c.text) for c in longest],match_lengths=[len(t) for t in texts])
(out/'dimensions.json').write_text(json.dumps(dimensions))
scorer=CandidateScoringMixin() if sys.argv[1]=='baseline' else ExactCutoffScoring()
start,cpu=time.perf_counter(),time.process_time()
result=scorer._body_score_details(*texts)
(out/'result.json').write_text(json.dumps(dict(dimensions,seconds=time.perf_counter()-start,cpu_seconds=time.process_time()-cpu,score=result,counters=dict(getattr(scorer,'cutoff_counts',{}))),indent=2))
