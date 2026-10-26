"""Validate all Golden contracts against a hash-bound offline pipeline replay.

This does not replace the live-OCR blocking suite. It verifies saved full-pipeline
results, immutable PDF inputs, and the exact business code used by that replay.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from tests.golden_contract.golden_loader import load_corpus, load_manifest, oracle_statistics, validate_execution
from tests.golden_contract.semantic_matcher import SemanticGoldenMatcher


def verify(results_root: Path, data_root: Path, report_path: Path) -> int:
    corpus = load_corpus(data_root)
    stats = oracle_statistics(corpus)
    recorded = json.loads((results_root / 'code-hashes.json').read_text())
    current = {'app/' + str(p.relative_to(BACKEND / 'app')): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted((BACKEND / 'app').rglob('*.py'))}
    if current != recorded:
        raise AssertionError('Replay business code does not match the current workspace; replay again')
    contracts = []
    for contract in corpus:
        folder = results_root / contract.source_run_id
        inputs = json.loads((folder / 'input-hashes.json').read_text())
        for side, pdf in [('original', contract.original_pdf), ('compare', contract.compare_pdf)]:
            digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
            candidates = [value for path, value in inputs.items()
                          if f'/input/{side}/' in path and path.endswith('.pdf')]
            if candidates != [digest]:
                raise AssertionError(f'{contract.contract_key}: {side} replay PDF hash mismatch')
        for stage in ['SummaryStage.json', 'VisualizationStage.json']:
            if not (folder / stage).is_file():
                raise AssertionError(f'{contract.contract_key}: incomplete replay: {stage}')
        result = json.loads((folder / 'result.json').read_text())
        report = SemanticGoldenMatcher().match(contract.contract_key, contract.oracle['assertions'], result['diffs'])
        contracts.append(asdict(report))
        print(f'{contract.contract_key}: failures={len(report.failures)} positives={report.matched_positive} negatives={report.matched_negative}')
    keys = [c.contract_key for c in corpus]
    validate_execution([c['contract_key'] for c in load_manifest()['contracts']], keys, [c['contract'] for c in contracts])
    for report in contracts:
        report['consumed_actual'] = sorted(report['consumed_actual'])
    payload = {'baseline_version': load_manifest()['baseline_version'], 'mode': 'verified_offline_pipeline_results',
               'statistics': stats, 'contracts': contracts,
               'failure_count': sum(len(c['failures']) for c in contracts)}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"GOLDEN OFFLINE: contracts={len(corpus)} failures={payload['failure_count']}")
    return int(payload['failure_count'] > 0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results-root', type=Path, required=True)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(verify(args.results_root, args.data_root, args.report))
