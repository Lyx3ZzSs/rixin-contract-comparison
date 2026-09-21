"""One-variable live B pipeline pairs, plus bounded pathological observations."""
import json
import os
from pathlib import Path
import subprocess
import time

from compare_results import differences

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'output/analysis/perf-phase2-20260921/cpu-pipelines'
OUT.mkdir(parents=True,exist_ok=True)
report={}
for experiment,flag in [('alignment','PHASE2_PAGE_BOUND'),('scoring','PHASE2_EXACT_CUTOFF')]:
    report[experiment]=[]
    for repeat in range(1,4):
        pair={}
        for variant in ['baseline','candidate']:
            path=OUT/experiment/f'B-{repeat}-{variant}';path.mkdir(parents=True,exist_ok=False)
            env=dict(os.environ)
            if variant=='candidate':env[flag]='1'
            with (path/'run.log').open('w') as log:
                subprocess.run([str(ROOT/'backend/.venv/bin/python'),str(ROOT/'experiments/performance_phase2/run_pipeline.py'),'B',str(path)],env=env,stdout=log,stderr=log,check=True,timeout=600)
            pair[variant]=json.loads((path/'result.json').read_text())
        keys=['diffs','pairs','original_sections','compare_sections','events']
        a,b=pair['baseline'],pair['candidate']
        delta=differences({k:a[k] for k in keys},{k:b[k] for k in keys})
        delta+=differences(a['page_alignment']['entries'],b['page_alignment']['entries'],'/page_mapping')
        row=dict(repeat=repeat,baseline_seconds=a['pipeline_seconds'],candidate_seconds=b['pipeline_seconds'],semantic_difference_paths=delta)
        report[experiment].append(row)
        (OUT/'report.json').write_text(json.dumps(report,indent=2))
        print(experiment,row,flush=True)
        if delta:break

# First isolate the actual longest pair, retaining raw (unrounded) score fields.
report['longest-pair']=[]
for variant in ['baseline','candidate']:
    path=OUT/f'longest-pair-{variant}';path.mkdir(exist_ok=False)
    start=time.monotonic()
    with (path/'run.log').open('w') as log:
        try:
            subprocess.run([str(ROOT/'backend/.venv/bin/python'),str(ROOT/'experiments/performance_phase2/pathological_pair.py'),variant,str(path)],stdout=log,stderr=log,check=True,timeout=300)
            record=json.loads((path/'result.json').read_text())
            record['status']='completed'
        except subprocess.TimeoutExpired:
            record=dict(variant=variant,status='timeout_300_seconds',observation_seconds=time.monotonic()-start)
    report['longest-pair'].append(record)
    (OUT/'report.json').write_text(json.dumps(report,indent=2))
    print(record,flush=True)
if all(r['status']=='completed' for r in report['longest-pair']):
    assert report['longest-pair'][0]['score']==report['longest-pair'][1]['score'], 'REVERT: pathological raw scores changed'

# Single deterministic stress observation per variant; a timeout is a censored
# lower bound and cannot establish ordering equality or a percentage speedup.
report['C-pathological']=[]
for variant in ['baseline','candidate']:
    path=OUT/f'C-pathological-{variant}';path.mkdir(exist_ok=False)
    env=dict(os.environ)
    if variant=='candidate':env['PHASE2_EXACT_CUTOFF']='1'
    start=time.monotonic()
    with (path/'run.log').open('w') as log:
        try:
            subprocess.run([str(ROOT/'backend/.venv/bin/python'),str(ROOT/'experiments/performance_phase2/run_pipeline.py'),'C-pathological',str(path)],env=env,stdout=log,stderr=log,check=True,timeout=300)
            status='completed'
        except subprocess.TimeoutExpired:
            status='timeout_300_seconds'
    report['C-pathological'].append(dict(variant=variant,status=status,observation_seconds=time.monotonic()-start))
    (OUT/'report.json').write_text(json.dumps(report,indent=2))
    print(report['C-pathological'][-1],flush=True)
(OUT/'complete.json').write_text(json.dumps({'done':True}))
