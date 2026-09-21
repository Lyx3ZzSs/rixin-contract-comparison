"""A-E frozen-extraction stage check; no remote calls and no E2E claim.

Preparation must first reproduce the saved baseline mapping exactly. Each
candidate then compares every mapping field, not only matched page numbers.
"""
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import settings
from app.models import Document
from app.services.document_understanding import DocumentUnderstandingService
from app.services.document_preparation import DocumentPreparer
from app.services.native_heading_repair import NativeHeadingRepairService
from app.services.repeated_overlay_filter import RepeatedOverlayFilter
from app.services.page_alignment import PageSequenceAligner
from anchor_bound import AnchorBoundAligner

OUT = ROOT / 'output/analysis/perf-phase2-20260921/alignment-matrix'
OUT.mkdir(parents=True, exist_ok=True)
manifest = json.loads((ROOT / 'output/analysis/e2e-20260921/cases.json').read_text())
rows = []
for case in 'ABCDE':
    fixed = manifest[case]
    for path, info in zip(fixed['paths'], fixed['files']):
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == info['sha256']
    if case == 'A':
        source = ROOT / 'output/analysis/perf-phase2-20260921/cache/A-1-baseline'
        docs = [Document.model_validate(json.loads((source / f'{side}-extraction.json').read_text()) |
                dict(path=path, filename=Path(path).name))
                for side, path in zip(('original', 'compare'), fixed['paths'])]
        expected_path = source / 'runtime/tasks/phase2-A/diagnostics/debug/page_alignment.json'
    else:
        if case == 'B':
            source = ROOT / 'output/analysis/e2e-20260921/profile-B/runtime/tasks/profile-B/diagnostics'
        else:
            base = ROOT / f'output/analysis/perf-phase2-20260921/animation/baseline/runtime-{case}/tasks'
            source = sorted(base.glob('*/diagnostics'))[0]
        docs = []
        for side, info in zip(('original', 'compare'), fixed['files']):
            files = list((source / 'ocr').glob(f'*{side}_ppstructure_ocr_hybrid_raw.json'))
            assert len(files) == 1, (case, side, files)
            doc = Document.model_validate(json.loads(files[0].read_text())['document'])
            assert hashlib.sha256(Path(doc.path).read_bytes()).hexdigest() == info['sha256']
            NativeHeadingRepairService().repair(doc)
            RepeatedOverlayFilter().apply(doc)
            docs.append(doc)
        expected_path = source / 'debug/page_alignment.json'
    DocumentUnderstandingService(settings.document_understanding).understand_pair(*docs)
    DocumentPreparer(image_analysis_max_inflight=settings.compare_image_analysis_max_inflight).prepare_pair(*docs)
    expected = json.loads(expected_path.read_text())
    for repeat in range(1, 4):
        baseline = None
        for variant, cls in [('baseline', PageSequenceAligner), ('candidate', AnchorBoundAligner)]:
            start, cpu = time.perf_counter(), time.process_time()
            result = cls().align(*docs)
            row = dict(case=case, repeat=repeat, variant=variant, seconds=time.perf_counter()-start,
                       cpu_seconds=time.process_time()-cpu, source=str(source),
                       performance=result.performance, mapping=result.to_debug_payload())
            if variant == 'baseline':
                baseline = result.entries
                row['reproduces_saved_mapping'] = result.to_debug_payload() == expected
                assert row['reproduces_saved_mapping'], f'{case}: preparation is not equivalent to saved baseline'
            else:
                row['exact_mapping_equal'] = result.entries == baseline
                assert row['exact_mapping_equal'], f'REVERT {case}: mapping changed'
            rows.append(row)
            (OUT / 'report.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            print(case, variant, repeat, round(row['seconds'], 4), flush=True)
(OUT / 'complete.json').write_text(json.dumps({'completed': True, 'rows': len(rows)}))
