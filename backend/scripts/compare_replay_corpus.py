"""Compare two cached replays against the unchanged contract golden oracle."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BACKEND))
from tests.golden_contract.semantic_matcher import SemanticGoldenMatcher


def compare(before,after,output):
    fixture = BACKEND/'tests/fixtures/contract_golden'
    contracts = json.loads((fixture/'manifest.json').read_text())['contracts']
    report = {'contracts':[], 'expected_contracts':len(contracts)}
    for contract in contracts:
        reports=[]; counts=[]
        task = contract['source_run_id']
        assert json.loads((before/task/'input-hashes.json').read_text()) == json.loads((after/task/'input-hashes.json').read_text())
        for root in (before,after):
            diffs=json.loads((root/task/'result.json').read_text())['diffs']
            oracle=json.loads((fixture/contract['oracle']).read_text())['assertions']
            reports.append(SemanticGoldenMatcher().match(contract['contract_key'],oracle,diffs))
            counts.append(len(diffs))
        old,new=[{(f.case_id,f.failure_type):f for f in r.failures} for r in reports]
        report['contracts'].append({
            'contract':contract['contract_key'],'source_task_id':task,
            'before_diff_count':counts[0],'after_diff_count':counts[1],
            'before_failure_count':len(reports[0].failures),'after_failure_count':len(reports[1].failures),
            'new_failures':[asdict(new[key]) for key in sorted(new.keys()-old.keys())],
            'resolved_failures':[asdict(old[key]) for key in sorted(old.keys()-new.keys())],
            'remaining_failures':[asdict(f) for f in reports[1].failures],
            'input_hashes_equal':True,
        })
    report['executed_contracts']=len(report['contracts'])
    report['before_failures']=sum(c['before_failure_count'] for c in report['contracts'])
    report['after_failures']=sum(c['after_failure_count'] for c in report['contracts'])
    report['new_failures']=sum(len(c['new_failures']) for c in report['contracts'])
    report['passed_no_new_failures']=report['new_failures']==0
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='contracts'},indent=2))
    if not report['passed_no_new_failures']:raise SystemExit(1)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--before',type=Path,required=True)
    p.add_argument('--after',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();compare(args.before,args.after,args.output)
