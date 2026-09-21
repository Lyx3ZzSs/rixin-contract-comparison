"""Compare unchanged Golden failures and complete captured semantic payloads."""
from collections import Counter
import json
from pathlib import Path
import re

from compare_results import differences

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'output/analysis/perf-phase2-20260921'

def reports(variant):
    return {p.name.removesuffix('-report.json'):json.loads(p.read_text())
            for p in (OUT/('golden-'+variant)/'pytest').rglob('*-report.json')}

def captures(variant):
    return {re.sub(r'-[0-9a-f]{8}$','',p.stem).removeprefix('golden-').upper():p
            for p in (OUT/('golden-'+variant)).glob('golden-*.json')}

all_reports={variant:reports(variant) for variant in ['phase0','reference','alignment','scoring']}
result={}
for variant,current in all_reports.items():
    failures=[f for r in current.values() for f in r['failures']]
    row=dict(contracts=len(current),failure_count=len(failures),failure_types=dict(Counter(f['failure_type'] for f in failures)),
             failed_assertions=sorted((f['contract'],f['case_id'],f['failure_type']) for f in failures))
    if variant in ['alignment','scoring']:
        reference=all_reports['reference']
        row['reports_exact_difference_paths']=differences(reference,current)
        before,after=captures('reference'),captures(variant)
        row['capture_contracts_equal']=set(before)==set(after)
        row['semantic_differences']={}
        for key in sorted(before.keys() & after.keys()):
            diff=differences(json.loads(before[key].read_text()),json.loads(after[key].read_text()))
            row['semantic_differences'][key]=diff
    result[variant]=row
(OUT/'golden-comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print({key:{k:v for k,v in value.items() if k in ['contracts','failure_count','failure_types','capture_contracts_equal']} for key,value in result.items()})
