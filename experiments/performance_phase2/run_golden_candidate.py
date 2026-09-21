"""Run the unchanged blocking Golden against ONE isolated experimental variable."""
import os
import dataclasses
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
variant=sys.argv[1]
out=ROOT/'output/analysis/perf-phase2-20260921'/('golden-'+variant)
out.mkdir(parents=True,exist_ok=True)
os.environ['GOLDEN_CONTRACT_DATA_ROOT']=str(ROOT/'storage/golden-intake/contract-golden-v3')
sys.path.insert(0,str(ROOT/'backend'))
import app.services.pipeline_stages as stages
if variant=='reference':
    pass
elif variant=='alignment':
    from anchor_bound import AnchorBoundAligner
    stages.PageSequenceAligner=AnchorBoundAligner
elif variant=='scoring':
    from app.services.matcher import ClauseMatcher
    from score_cutoff import ExactCutoffScoring
    class CutoffMatcher(ExactCutoffScoring,ClauseMatcher):
        pass
    stages.ClauseMatcher=CutoffMatcher
else:
    raise ValueError(variant)
from app.services.pipeline import ComparePipeline
from app.services.diff_events import event_response
original_run=ComparePipeline.run
def capture(self, ctx):
    result=original_run(self, ctx)
    def dump(value):
        if hasattr(value,'model_dump'):return value.model_dump(mode='json')
        if dataclasses.is_dataclass(value):return dataclasses.asdict(value)
        if isinstance(value,list):return [dump(x) for x in value]
        return value
    payload=dict(diffs=dump(ctx.task.diffs),pairs=dump(ctx.pairs),
                 original_sections=dump(ctx.original_clauses),compare_sections=dump(ctx.compare_clauses),
                 page_mapping=dump(ctx.page_alignment.entries))
    target=out/(ctx.task.task_id+'.json')
    target.write_text(json.dumps(payload,ensure_ascii=False,default=dump));target.chmod(0o600)
    return result
ComparePipeline.run=capture
import pytest
raise SystemExit(pytest.main([str(ROOT/'backend/tests/golden_contract/test_contract_golden_blocking.py'),'-q','-s','--basetemp',str(out/'pytest')]))
